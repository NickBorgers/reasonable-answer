"""The LangGraph loop: intake → generate ⇄ critique → triage → orchestrate → control.

LangGraph state is *shared* across nodes, so isolation here is deliberate rather
than automatic (docs/isolation.md). Two things make it structural:

* every model call is built from an explicit, minimal argument list — nodes never
  hand a model the state object;
* the orchestrator node is invoked through :func:`_orchestrate` which accepts an
  ``OrchestratorView`` and nothing else, so artifact-bearing state has no path in.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from . import prompts, report as report_mod, roles, triage
from .config import Config, ConfigError, validate_roster_health
from .controller import acceptance_state, decide, detect_cycle
from .llm import LLMClient, MalformedOutputError, ModelCallError
from .schemas import (
    CleanRecord,
    ControllerInput,
    CritiqueOutput,
    Decision,
    Defect,
    LensResult,
    OrchestratorRecommendation,
    OrchestratorView,
    SeverityCounts,
)
from .store import RunStore
from .taxonomy import LENSES, Lens

log = logging.getLogger(__name__)


class State(TypedDict, total=False):
    run_id: str
    question: str
    seed: str | None

    report: str
    artifact_hash: str
    author_alias: str
    author_identity: str
    writer_rotation: int

    round: int
    hash_history: list[str]

    pending_lenses: list[str]
    lens_results: dict[str, Any]
    used_critics: dict[str, list[str]]
    clean_records: list[dict]
    defects: list[dict]

    view: dict
    decision: dict
    polish_next: bool
    polish_used: int
    critique_attempts_remaining: int
    confirmation_attempts_remaining: int

    prev_material: int
    prev_signature: list
    stagnation_count: int

    fatal: bool
    fatal_reason: str | None
    terminal_status: str | None
    scoreboard: list[dict]
    warnings: list[str]


@dataclass
class Runtime:
    """Everything the nodes need that is not run state."""

    config: Config
    client: LLMClient
    identities: dict[str, str]
    store: RunStore
    warnings: list[str] = field(default_factory=list)


def build_runtime(
    config: Config, run_id: str | None = None, client: LLMClient | None = None
) -> Runtime:
    """Startup validation, fail closed before a single token is spent (RA-015)."""
    client = client or LLMClient(config)
    identities = client.resolve_identities(config.roster.all_aliases)
    warnings = validate_roster_health(config, identities)

    for alias in config.roster.all_aliases:
        mode = client.probe_structured_output(alias)
        log.info("structured-output mode for %s (%s): %s", alias, identities[alias], mode)

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    store = RunStore(config.runs_dir, run_id)
    store.event(
        "startup",
        identities=identities,
        modes={a: client.mode_for(a) for a in config.roster.all_aliases},
        warnings=warnings,
        budgets=config.budgets.model_dump(),
    )
    for warning in warnings:
        log.warning("roster: %s", warning)
    return Runtime(config=config, client=client, identities=identities, store=store,
                   warnings=warnings)


# --------------------------------------------------------------------- intake


def _intake(state: State, rt: Runtime) -> dict:
    question = (state.get("question") or "").strip()
    seed = (state.get("seed") or "").strip() or None
    cfg = rt.config

    # RA-018: v1 requires an explicit question on every path, including seed-only.
    if not question:
        raise ConfigError(
            "intake rejected: a question is required (question inference from a bare "
            "seed is deferred behind an opt-in flag)"
        )
    if len(question) > cfg.max_question_chars:
        raise ConfigError(f"intake rejected: question exceeds {cfg.max_question_chars} chars")
    if seed and len(seed) > cfg.max_report_chars:
        raise ConfigError(f"intake rejected: seed exceeds {cfg.max_report_chars} chars")

    base: dict = {
        "question": question,
        "seed": seed,
        "round": 0,
        "hash_history": [],
        "writer_rotation": 0,
        "lens_results": {},
        "used_critics": {},
        "clean_records": [],
        "defects": [],
        "polish_used": 0,
        "polish_next": False,
        "critique_attempts_remaining": cfg.budgets.critique_attempts,
        "confirmation_attempts_remaining": cfg.budgets.confirmation_attempts,
        "prev_material": -1,
        "prev_signature": [],
        "stagnation_count": 0,
        "fatal": False,
        "fatal_reason": None,
        "terminal_status": None,
        "scoreboard": [],
        "pending_lenses": [lens.value for lens in LENSES],
        "warnings": rt.warnings,
    }

    if seed:
        # The seed *is* R1. It has no model author, so every critic is eligible.
        h = report_mod.artifact_hash(seed)
        base |= {
            "report": seed,
            "artifact_hash": h,
            "author_alias": "seed",
            "author_identity": "external/seed",
            "hash_history": [h],
            # the seed is R1, so it occupies the first tick — and min_ticks > 1
            # guarantees it is never accepted on its first critique
            "round": 1,
        }
        rt.store.report(1, h, seed, "seed")
        rt.store.event("intake", path="seed", artifact_hash=h)
    else:
        rt.store.event("intake", path="question")
    return base


def _route_intake(state: State) -> str:
    return "critique" if state.get("report") else "generate"


# ------------------------------------------------------------------- generate


def _generate(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    last_author = state.get("author_identity")
    if last_author == "external/seed":
        last_author = None  # a human seed excludes nobody from writing

    rotation = state.get("writer_rotation", 0)
    try:
        alias = roles.next_writer(cfg.roster, rt.identities, last_author, rotation)
    except roles.RosterExhausted as exc:
        return {"fatal": True, "fatal_reason": str(exc)}
    identity = rt.identities[alias]

    polish = state.get("polish_next", False)
    defects = [Defect.model_validate(d) for d in state.get("defects", [])]

    if state.get("report"):
        user = prompts.writer_revision(state["question"], state["report"], defects, polish)
    else:
        user = prompts.writer_first_draft(state["question"])

    try:
        completion = rt.client.complete(
            alias,
            system=prompts.WRITER_SYSTEM,
            user=user,
            max_tokens=32000,
        )
    except ModelCallError as exc:
        return {"fatal": True, "fatal_reason": f"generator {alias} failed: {exc}"}

    text = completion.text.strip()
    if not text:
        return {"fatal": True, "fatal_reason": f"generator {alias} returned an empty report"}
    if len(text) > cfg.max_report_chars:
        text = text[: cfg.max_report_chars]

    h = report_mod.artifact_hash(text)
    history = [*state.get("hash_history", []), h]
    # A tick is one *draft*, counted here rather than at triage: a writer that
    # returns byte-identical output must still advance the clock, or the loop
    # would stall below min_ticks forever. Re-critiques (rules 2 and 8) never
    # pass through this node, so they correctly leave `round` alone (RG-001).
    round_no = state.get("round", 0) + 1
    rt.store.report(round_no, h, text, identity)
    rt.store.event(
        "generate",
        author=identity,
        artifact_hash=h,
        polish=polish,
        defects_applied=len(defects),
        tokens=completion.completion_tokens,
    )

    return {
        "report": text,
        "artifact_hash": h,
        "author_alias": alias,
        "author_identity": identity,
        "writer_rotation": rotation + 1,
        "hash_history": history,
        "round": round_no,
        # Every generation resets every per-artifact accumulator — unconditionally,
        # even when the writer happened to reproduce byte-identical text. Keying the
        # reset on "the hash changed" would let a clean record earned under a
        # *different* author satisfy acceptance for this one (RC-002).
        "clean_records": [],
        "lens_results": {},
        "used_critics": {},
        "defects": [],
        "polish_next": False,
        "pending_lenses": [lens.value for lens in LENSES],
    }


# ------------------------------------------------------------------- critique


def _critique_one(
    rt: Runtime,
    lens: Lens,
    question: str,
    report_text: str,
    artifact_hash: str,
    author_identity: str,
    used: set[str],
    attempt: int,
) -> LensResult:
    """One lens, one fresh context. Failure is recorded as a *failed lens*, never as
    'no issues found' — a failed review can never manufacture a clean record."""
    try:
        alias = roles.pick_critic(rt.config.roster, rt.identities, lens, author_identity, used)
        identity = rt.identities[alias]
        roles.assert_author_exclusion(identity, author_identity, lens)
    except roles.RosterExhausted as exc:
        # A lens with no eligible non-author is fatal, but it must reach that verdict
        # through the controller (rule 1/3), not by crashing out of a worker thread.
        return LensResult(
            lens=lens,
            artifact_hash=artifact_hash,
            critic_alias="(none)",
            critic_identity="(none)",
            artifact_author_identity=author_identity,
            failed=True,
            failure_reason=str(exc)[:400],
            attempt=attempt,
        )

    base = LensResult(
        lens=lens,
        artifact_hash=artifact_hash,
        critic_alias=alias,
        critic_identity=identity,
        artifact_author_identity=author_identity,
        attempt=attempt,
    )

    rendered = report_mod.render_with_loci(report_text)
    structure = report_mod.parse(report_text)

    try:
        output = rt.client.structured(
            alias,
            system=prompts.CRITIC_SYSTEM,
            user=prompts.critic_user(lens, question, rendered),
            schema=CritiqueOutput,
            max_tokens=16000,
        )
    except (ModelCallError, MalformedOutputError, ValidationError) as exc:
        return base.model_copy(update={"failed": True, "failure_reason": str(exc)[:400]})

    try:
        for issue in output.issues:
            triage.validate_issue(
                lens, issue, structure, rt.config.require_verbatim_spans
            )
    except triage.LensValidationError as exc:
        # Fail-closed: one bad field fails the whole lens; nothing is silently dropped.
        return base.model_copy(update={"failed": True, "failure_reason": str(exc)[:400]})

    return base.model_copy(update={"issues": output.issues})


def _critique(state: State, rt: Runtime) -> dict:
    pending = [Lens(v) for v in state.get("pending_lenses") or [l.value for l in LENSES]]
    question = state["question"]
    report_text = state["report"]
    artifact_hash = state["artifact_hash"]
    author_identity = state["author_identity"]

    used_raw: dict[str, list[str]] = dict(state.get("used_critics", {}))
    used = {k: set(v) for k, v in used_raw.items()}
    results = dict(state.get("lens_results", {}))

    def work(lens: Lens) -> LensResult:
        attempt = 1 + len(used.get(lens.value, set()))
        return _critique_one(
            rt,
            lens,
            question,
            report_text,
            artifact_hash,
            author_identity,
            used.get(lens.value, set()),
            attempt,
        )

    with ThreadPoolExecutor(max_workers=rt.config.budgets.max_concurrency) as pool:
        for result in pool.map(work, pending):
            results[result.lens.value] = result.model_dump(mode="json")
            used.setdefault(result.lens.value, set()).add(result.critic_identity)
            rt.store.critique(artifact_hash, result.lens.value, result.attempt, result)
            rt.store.event(
                "critique",
                lens=result.lens.value,
                critic=result.critic_identity,
                artifact_hash=artifact_hash,
                failed=result.failed,
                failure_reason=result.failure_reason,
                issues=len(result.issues),
            )

    return {
        "lens_results": results,
        "used_critics": {k: sorted(v) for k, v in used.items()},
    }


# --------------------------------------------------------------------- triage


def _triage(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    results = [LensResult.model_validate(r) for r in state["lens_results"].values()]
    artifact_hash = state["artifact_hash"]

    lenses_failed = sum(1 for r in results if r.failed) + (len(LENSES) - len(results))
    per_category, totals = triage.tally(results)
    material = triage.material_count(totals)
    round_no = state.get("round", 0)

    prev_material = state.get("prev_material", -1)
    delta = 0 if prev_material < 0 else material - prev_material

    signature = list(triage.signal_signature(per_category))
    prev_signature = [tuple(x) for x in state.get("prev_signature", [])]
    stagnation = (
        state.get("stagnation_count", 0) + 1
        if signature and [tuple(s) for s in signature] == prev_signature
        else 0
    )

    # Clean records accumulate across re-critiques of the SAME hash; a new hash
    # resets them in `_generate`.
    existing = [CleanRecord.model_validate(r) for r in state.get("clean_records", [])]
    fresh = [r for r in triage.clean_records(results) if r not in existing]
    records = existing + fresh

    status = roles.lens_statuses(
        cfg.roster,
        rt.identities,
        state["author_identity"],
        artifact_hash,
        records,
        {k: set(v) for k, v in state.get("used_critics", {}).items()},
    )
    acceptance = acceptance_state(status, material)

    view = triage.build_view(
        per_category=per_category,
        totals=totals,
        delta_material_vs_prev=delta,
        lenses_failed=lenses_failed,
        round_no=round_no,
        min_ticks=cfg.budgets.min_ticks,
        hard_cap=cfg.budgets.hard_cap,
        roster_size=len(set(rt.identities.values())),
        lens_cleared={s.lens: s.cleared_count for s in status},
        acceptance=acceptance,
        polish_used=state.get("polish_used", 0),
        polish_cap=cfg.budgets.polish_cap,
        stagnation_count=stagnation,
        cycle_detected=detect_cycle(state.get("hash_history", []), cfg.budgets.cycle_period),
    )

    defects = [d.model_dump(mode="json") for d in triage.to_defects(results)]
    rt.store.view(round_no, view)
    rt.store.event(
        "triage",
        artifact_hash=artifact_hash,
        material=material,
        lenses_failed=lenses_failed,
        cleared={s.lens.value: s.cleared_count for s in status},
        acceptance=acceptance,
    )

    scoreboard = [
        *state.get("scoreboard", []),
        {
            "round": round_no,
            "artifact_hash": artifact_hash,
            "blocking": totals.blocking,
            "major": totals.major,
            "minor": totals.minor,
            "report": state["report"],
        },
    ]

    return {
        "round": round_no,
        "view": view.model_dump(mode="json"),
        "defects": defects,
        "clean_records": [r.model_dump(mode="json") for r in records],
        "prev_material": material,
        "prev_signature": signature,
        "stagnation_count": stagnation,
        "scoreboard": scoreboard,
    }


# --------------------------------------------------------------- orchestrate


def _orchestrate_call(client: LLMClient, alias: str, view: OrchestratorView) -> bool:
    """The blind LLM's entire interface. It takes an OrchestratorView and returns a
    boolean. There is deliberately no parameter through which content could arrive."""
    try:
        rec = client.structured(
            alias,
            system=prompts.ORCHESTRATOR_SYSTEM,
            user=prompts.orchestrator_user(view.model_dump_json(indent=2)),
            schema=OrchestratorRecommendation,
            max_tokens=4000,
        )
    except (ModelCallError, MalformedOutputError):
        return False  # no recommendation ⇒ no polish; the LLM can only *enable* rule 9
    return rec.polish_recommended


def _orchestrate(state: State, rt: Runtime) -> dict:
    view = OrchestratorView.model_validate(state["view"])
    alias = rt.config.roster.writers[0]
    recommended = _orchestrate_call(rt.client, alias, view)
    rt.store.event("orchestrate", polish_recommended=recommended)
    return {"polish_next": recommended}


# ----------------------------------------------------------------- controller


def _empty_view(cfg: Config, round_no: int) -> OrchestratorView:
    """A zeroed view for the one case where the controller must decide before any
    critique has run: a generator that died on the first draft."""
    return OrchestratorView(
        counts={},
        totals=SeverityCounts(),
        delta_material_vs_prev=0,
        lenses_failed=0,
        round=round_no,
        min_ticks=cfg.budgets.min_ticks,
        hard_cap=cfg.budgets.hard_cap,
        roster_size=0,
        lens_cleared={lens.value: 0 for lens in LENSES},
        acceptance="none",
        polish_used=0,
        polish_cap=cfg.budgets.polish_cap,
        stagnation_count=0,
        cycle_detected=False,
    )


def _control(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    # The controller owns *every* termination, including a dead generator — so the
    # fatal path routes through here rather than jumping to finalize (RA-020).
    view = (
        OrchestratorView.model_validate(state["view"])
        if state.get("view")
        else _empty_view(cfg, state.get("round", 0))
    )
    results = {k: LensResult.model_validate(v) for k, v in state["lens_results"].items()}
    records = [CleanRecord.model_validate(r) for r in state.get("clean_records", [])]

    # These defaults only matter on the fatal-before-any-draft path; every other
    # path has a real artifact by the time control runs.
    author_identity = state.get("author_identity", "(none)")
    artifact_hash = state.get("artifact_hash", "")

    status = roles.lens_statuses(
        cfg.roster,
        rt.identities,
        author_identity,
        artifact_hash,
        records,
        {k: set(v) for k, v in state.get("used_critics", {}).items()},
    )

    ci = ControllerInput(
        view=view,
        fatal=state.get("fatal", False),
        fatal_reason=state.get("fatal_reason"),
        run_id=state["run_id"],
        artifact_hash=artifact_hash,
        artifact_hash_history=state.get("hash_history", []),
        author_identity=author_identity,
        lens_status=status,
        critique_attempts_remaining=state.get("critique_attempts_remaining", 0),
        confirmation_attempts_remaining=state.get("confirmation_attempts_remaining", 0),
        polish_recommended=state.get("polish_next", False),
        stagnation_limit=cfg.budgets.stagnation_limit,
        cycle_period=cfg.budgets.cycle_period,
    )

    decision = decide(ci)
    if decision.rule == 2:
        # the concrete failed lenses are operational detail the table abstracts over
        decision = decision.model_copy(
            update={
                "recritique_lenses": [
                    Lens(name)
                    for name, r in results.items()
                    if r.failed
                ]
                or list(LENSES)
            }
        )

    rt.store.decision(view.round, decision)
    rt.store.event(
        "control",
        rule=decision.rule,
        action=decision.action,
        terminal=decision.terminal_status,
        note=decision.note,
    )

    out: dict = {"decision": decision.model_dump(mode="json")}
    if decision.action == "recritique":
        out["pending_lenses"] = [l.value for l in decision.recritique_lenses]
        if decision.rule == 2:
            out["critique_attempts_remaining"] = state["critique_attempts_remaining"] - 1
        else:
            out["confirmation_attempts_remaining"] = (
                state["confirmation_attempts_remaining"] - 1
            )
    elif decision.action == "generate":
        out["polish_next"] = decision.polish
        if decision.polish:
            out["polish_used"] = state.get("polish_used", 0) + 1
    else:
        out["terminal_status"] = decision.terminal_status
    return out


def _route_control(state: State) -> str:
    decision = Decision.model_validate(state["decision"])
    return {"generate": "generate", "recritique": "critique", "terminal": "finalize"}[
        decision.action
    ]


# -------------------------------------------------------------------- finalize


def _finalize(state: State, rt: Runtime) -> dict:
    status = state.get("terminal_status") or "aborted"
    board = state.get("scoreboard", [])

    if status in ("accepted", "converged_unconfirmed"):
        text = state.get("report", "")
        chosen_round = state.get("round", 0)
    elif board:
        # Never ship the last draft just because it is last — ship the best-scoring one.
        from .controller import best_scoring_index

        idx = best_scoring_index([(b["blocking"], b["major"], b["minor"]) for b in board])
        text = board[idx]["report"]
        chosen_round = board[idx]["round"]
    else:
        text = state.get("report", "")
        chosen_round = state.get("round", 0)

    view = state.get("view", {})
    summary = {
        "run_id": state["run_id"],
        "terminal_status": status,
        "rounds": state.get("round", 0),
        "chosen_round": chosen_round,
        "artifact_hash": report_mod.artifact_hash(text) if text else None,
        "final_view": view,
        "clean_records": state.get("clean_records", []),
        "outstanding_defects": state.get("defects", []),
        "warnings": state.get("warnings", []),
        "note": Decision.model_validate(state["decision"]).note if state.get("decision") else "",
        "label": (
            "consensus-reviewed with in-artifact sourcing (no external retrieval in v1)"
        ),
    }
    rt.store.final(text, summary)
    rt.store.event("finalize", **{k: v for k, v in summary.items() if k != "final_view"})
    return {"terminal_status": status, "report": text}


# ----------------------------------------------------------------------- graph


def build_graph(rt: Runtime):
    graph = StateGraph(State)
    graph.add_node("intake", lambda s: _intake(s, rt))
    graph.add_node("generate", lambda s: _generate(s, rt))
    graph.add_node("critique", lambda s: _critique(s, rt))
    graph.add_node("triage", lambda s: _triage(s, rt))
    graph.add_node("orchestrate", lambda s: _orchestrate(s, rt))
    graph.add_node("control", lambda s: _control(s, rt))
    graph.add_node("finalize", lambda s: _finalize(s, rt))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", _route_intake, {"generate": "generate", "critique": "critique"}
    )
    # A dead generator still terminates *through* the controller, so the run gets a
    # recorded rule-1 decision and a normal audit trail rather than a silent exit.
    graph.add_conditional_edges(
        "generate",
        lambda s: "control" if s.get("fatal") else "critique",
        {"critique": "critique", "control": "control"},
    )
    graph.add_edge("critique", "triage")
    graph.add_edge("triage", "orchestrate")
    graph.add_edge("orchestrate", "control")
    graph.add_conditional_edges(
        "control",
        _route_control,
        {"generate": "generate", "critique": "critique", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph


def _checkpointer(rt: Runtime):
    """A per-run SQLite checkpoint next to the audit trail. Best-effort: an
    unavailable checkpointer costs resumability, never correctness."""
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:  # pragma: no cover - optional dependency
        log.warning("langgraph-checkpoint-sqlite is unavailable; this run is not resumable")
        return None
    conn = sqlite3.connect(rt.store.dir / "state.sqlite", check_same_thread=False)
    return SqliteSaver(conn)


def run(
    config: Config,
    question: str,
    seed: str | None = None,
    run_id: str | None = None,
    checkpointer: Any | None = None,
    client: LLMClient | None = None,
) -> dict:
    rt = build_runtime(config, run_id, client)
    checkpointer = checkpointer if checkpointer is not None else _checkpointer(rt)
    compiled = build_graph(rt).compile(checkpointer=checkpointer)
    invoke_config = {
        "recursion_limit": max(100, config.budgets.hard_cap * 12),
        "configurable": {"thread_id": rt.store.run_id},
    }

    # Resumability is the point of the checkpointer: this is a slow local-model
    # system, and losing an hour of critique to a dropped connection is the failure
    # mode that matters. An unfinished thread continues from its last completed node;
    # a fresh one starts at intake.
    initial: State | None = {"run_id": rt.store.run_id, "question": question, "seed": seed}
    if checkpointer is not None:
        snapshot = compiled.get_state(invoke_config)
        if snapshot.next:
            log.info("resuming run %s at %s", rt.store.run_id, snapshot.next)
            rt.store.event("resume", resumed_at=list(snapshot.next))
            initial = None
        elif snapshot.values.get("terminal_status"):
            log.info("run %s already terminated", rt.store.run_id)
            return {**snapshot.values, "run_dir": str(rt.store.dir), "run_id": rt.store.run_id}

    final = compiled.invoke(initial, config=invoke_config)
    final["run_dir"] = str(rt.store.dir)
    final["run_id"] = rt.store.run_id
    return final
