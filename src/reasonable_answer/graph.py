"""The LangGraph loop: intake → generate ⇄ critique → triage → orchestrate → control.

LangGraph state is *shared* across nodes, so isolation here is deliberate rather
than automatic (docs/isolation.md). Two things make it structural:

* every model call is built from an explicit, minimal argument list — nodes never
  hand a model the state object;
* the orchestrator node is invoked through :func:`_orchestrate` which accepts an
  ``OrchestratorView`` and nothing else, so artifact-bearing state has no path in.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import audition as audition_mod
from . import critique as critique_mod
from . import dispute as dispute_mod
from . import fetch, prompts, resolve, roles, search, triage
from . import report as report_mod
from .config import Config, ConfigError, validate_roster_health
from .controller import acceptance_state, decide, detect_cycle
from .llm import LLMClient, MalformedOutputError, ModelCallError
from .schemas import (
    AdjudicationRecord,
    CleanRecord,
    ControllerInput,
    Decision,
    Defect,
    Dispute,
    LensResult,
    OrchestratorRecommendation,
    OrchestratorView,
    SeverityCounts,
    WriterDisputes,
)
from .store import RunStore
from .taxonomy import LENSES, Lens

log = logging.getLogger(__name__)


class State(TypedDict, total=False):
    run_id: str
    question: str
    seed: str | None
    #: What ingest made of the seed at the edge — the format it came from, where it
    #: came from, and anything it wants the user told. Provenance only: no node routes
    #: on these, and they are outside the resume fingerprint (see `_run_fingerprint`).
    seed_format: str | None
    seed_source: str | None
    seed_warnings: list[str]
    fingerprint: str
    #: Captured once at intake so every prompt in the run carries the same date
    #: (RB-010: confirmation critiques stay byte-identical across midnight).
    run_date: str

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
    #: Per-lens count of critique attempts made on THIS artifact. `used_critics` is a
    #: set of distinct identities and so stops growing once the eligible pool is
    #: exhausted; this counter keeps climbing, so `pick_critic`'s rotation advances on
    #: every rule-2 retry instead of freezing on one fallback model (mirrors the
    #: `writer_rotation` idiom).
    critique_rounds: dict[str, int]
    clean_records: list[dict]
    defects: list[dict]

    # Dispute channel (D25). The registry lives here — checkpointed state — so it
    # survives resume, and a content purge cannot break a live run.
    pending_disputes: list[dict]
    adjudications: list[dict]
    dispute_budget_remaining: int
    defect_provenance: dict[str, list[str]]

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
    #: None when search is disabled; writers then run exactly as they did before.
    searcher: Any | None = None
    #: None when source verification is off; the evidence lens then judges citations
    #: on their face, exactly as it did before.
    fetcher: Any | None = None

    @property
    def search_enabled(self) -> bool:
        return self.searcher is not None

    @property
    def verify_sources(self) -> bool:
        return self.fetcher is not None

    @property
    def disputes_enabled(self) -> bool:
        return self.config.disputes.enabled


def build_runtime(
    config: Config, run_id: str | None = None, client: LLMClient | None = None
) -> Runtime:
    """Startup validation, fail closed before a single token is spent (RA-015)."""
    client = client or LLMClient(config)
    identities = client.resolve_identities(config.roster.all_aliases)
    warnings = validate_roster_health(config, identities)
    # Structural eligibility says a lens *has* a reviewer; this says the reviewer can
    # actually find a defect (D20). Cache-read only, so it costs nothing and stays put
    # ahead of the probes below — a roster with an unfit critic should not get as far
    # as spending tokens on structured-output detection.
    audition_mod.enforce_fitness(config.audition, config.roster, identities)

    for alias in config.roster.all_aliases:
        mode = client.probe_structured_output(alias)
        log.info("structured-output mode for %s (%s): %s", alias, identities[alias], mode)

    searcher = _build_searcher(config, client)
    read_pdfs = _pdf_reading_enabled(config)
    resolver = _build_resolver(config, warnings)
    fetcher = (
        fetch.SourceFetcher(
            timeout=config.search.fetch_timeout_seconds,
            max_bytes=config.search.fetch_max_bytes,
            max_chars=config.search.fetch_max_chars,
            read_pdfs=read_pdfs,
            pdf_max_bytes=config.sources.pdf.max_bytes,
            pdf_max_pages=config.sources.pdf.max_pages,
            resolver=resolver,
        )
        if config.search.verify_sources
        else None
    )

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    store = RunStore(config.runs_dir, run_id)
    store.event(
        "startup",
        identities=identities,
        modes={a: client.mode_for(a) for a in config.roster.all_aliases},
        warnings=warnings,
        budgets=config.budgets.model_dump(),
        search_enabled=searcher is not None,
        search_query_budget=config.search.query_budget if searcher else 0,
        verify_sources=fetcher is not None,
        read_pdfs=read_pdfs,
        resolve_tiers=sorted(_enabled_tiers(config)),
        audition_enforced=config.audition.enforce,
    )
    for warning in warnings:
        log.warning("roster: %s", warning)
    return Runtime(config=config, client=client, identities=identities, store=store,
                   warnings=warnings, searcher=searcher, fetcher=fetcher)


def _pdf_reading_enabled(config: Config) -> bool:
    """Both switches on, and `pypdf` actually importable. Fatal if not.

    Checked here rather than at the first cited PDF, for the same reason
    `_build_searcher` checks a credential at startup: a dependency failure that
    surfaces mid-run costs a run's worth of tokens to discover, and arrives disguised
    as a per-source `unreadable` that looks like the site's fault rather than ours.
    """
    if not (config.sources.enabled and config.sources.pdf.enabled):
        return False
    try:
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "sources.pdf.enabled is on but pypdf is not installed. Install the extra "
            "(uv sync --extra ingest) or set sources.pdf.enabled: false."
        ) from exc
    return True


def _enabled_tiers(config: Config) -> set[str]:
    """The resolver tiers actually switched on, master switch included."""
    if not config.sources.enabled:
        return set()
    tiers = set()
    if config.sources.identifiers.enabled:
        tiers.add(fetch.ResolutionTier.IDENTIFIER.value)
    if config.sources.open_access.enabled:
        tiers.add(fetch.ResolutionTier.OPEN_ACCESS.value)
    if config.sources.extraction.enabled:
        tiers.add(fetch.ResolutionTier.EXTRACTION.value)
    return tiers


def _extraction_call_ceiling(config: Config) -> int:
    """The configured cap, or the structural one when none is set (D40).

    `max_sources * hard_cap` is the most distinct URLs a run could ever cite — every
    citation replaced in every round. Derived rather than written down so that raising
    `budgets.hard_cap` cannot silently start starving the tier at the old number.

    This bounds a *bug*, not a bill. `SourceFetcher` caches per URL for the whole run, so
    three critics re-verifying one '## Sources' list across eight rounds cost one call per
    URL, not twenty-four; what this catches is a fetch loop that ignores that cache.
    """
    configured = config.sources.extraction.max_calls_per_run
    if configured is not None:
        return configured
    return max(1, config.search.max_sources * config.budgets.hard_cap)


def _build_resolver(config: Config, warnings: list[str]):
    """Construct the resolver ladder (D39), or return None when no tier is on.

    A sibling of `_build_searcher` and `_pdf_reading_enabled` for the same reason both of
    those live here: network clients are assembled at startup and injected, so the graph
    itself performs no I/O and the test suite stays offline (D24).

    Two failure modes, deliberately graded differently. An unrecognised provider name is
    **fatal**: it silently disables a tier the operator believes they enabled, which is
    the same class of failure `_build_searcher` refuses to start with. A missing contact
    email is a **warning**: the polite pool is a courtesy, and demotion to the anonymous
    pool is degraded service rather than a broken configuration.
    """
    tiers = _enabled_tiers(config)
    if not tiers:
        return None
    if not config.search.verify_sources:
        warnings.append(
            "sources: resolver tiers are enabled but search.verify_sources is off, so "
            "nothing fetches and no tier will ever run"
        )

    sources = config.sources
    contact_email = sources.contact_email
    if not contact_email:
        warnings.append(
            f"sources: ${sources.contact_email_env} is unset, so registry requests go to "
            f"the anonymous rate-limit pool instead of Crossref/Unpaywall's polite pool "
            f"— expect slower answers and more throttling under load"
        )

    identifiers_on = fetch.ResolutionTier.IDENTIFIER.value in tiers
    open_access_on = fetch.ResolutionTier.OPEN_ACCESS.value in tiers
    extraction_on = fetch.ResolutionTier.EXTRACTION.value in tiers

    # Credentials resolved here, before a single call: the same fail-closed posture
    # `_build_searcher` applies to Brave. A tier that starts without its key spends its
    # whole budget on 401s and reports them as coverage.
    extraction_key = ""
    if extraction_on:
        if not sources.extraction.provider:
            raise ConfigError(
                "fail closed: sources.extraction.enabled is on but no provider is named. "
                "Defaulting to whichever provider happens to be first would send a paid "
                "call to a vendor nobody chose."
            )
        extraction_key = search.resolve_token(
            sources.extraction.api_key_env, sources.extraction.token_file
        )
    core_key = ""
    if open_access_on and fetch.Provider.CORE.value in sources.open_access.providers:
        core_key = search.resolve_token(
            sources.open_access.core_api_key_env, sources.open_access.core_token_file
        )
    if open_access_on and not config.sources.pdf.enabled:
        # Most free copies are PDFs (arXiv's only form is one), and without PDF reading
        # each of those mirrors fetches, comes back as an unreadable content type, and
        # falls through to metadata. The tier still helps — it just helps far less than
        # its call budget suggests.
        warnings.append(
            "sources: the open-access tier is on but sources.pdf.enabled is off, so most "
            "free copies (arXiv has no other form) will fetch and then be unreadable"
        )
    try:
        resolver, resolver_warnings = resolve.build(
            # A disabled tier is handed no provider names, so nothing is constructed for
            # it at all — enabling one tier can never turn the other on.
            identifier_providers=sources.identifiers.providers if identifiers_on else [],
            identifier_timeout=sources.identifiers.timeout_seconds,
            identifier_budget=sources.identifiers.max_calls_per_run,
            open_access_providers=sources.open_access.providers if open_access_on else [],
            open_access_timeout=sources.open_access.timeout_seconds,
            open_access_budget=sources.open_access.max_calls_per_run,
            contact_email=contact_email,
            core_api_key=core_key,
            extraction_provider=sources.extraction.provider if extraction_on else "",
            extraction_api_key=extraction_key,
            extraction_timeout=sources.extraction.timeout_seconds,
            extraction_budget=_extraction_call_ceiling(config),
            max_chars=config.search.fetch_max_chars,
        )
    except resolve.UnknownProvider as exc:
        raise ConfigError(f"fail closed: {exc}") from exc

    warnings.extend(resolver_warnings)
    log.info("source resolver enabled: tiers %s", sorted(tiers))
    return resolver


def _build_searcher(config: Config, client: LLMClient) -> search.BraveSearch | None:
    """Construct the search client, or fail closed. Returns None when search is off.

    Both checks here are fatal by design. A missing credential is obvious. The
    tool-calling probe is the subtle one: several small models accept a `tools`
    parameter and never emit a call. That writer would still be told to produce a
    '## Sources' section, and would fill it from memory — citations that look
    identical to retrieved ones but were never looked up. Refusing to start is the
    only honest response, since nothing downstream can tell the two apart.
    """
    if not config.search.enabled:
        return None

    token = search.resolve_token(config.search.api_key_env, config.search.token_file)

    incapable = [a for a in config.roster.writers if not client.probe_tool_calling(a)]
    if incapable:
        raise ConfigError(
            f"fail closed: web search is enabled but these writers cannot emit tool "
            f"calls: {incapable}. They would produce unsourced reports that still "
            f"claim citations. Remove them from the writer pool or disable search."
        )

    log.info(
        "web search enabled: %d queries for this run, %d results per query",
        config.search.query_budget,
        config.search.max_results,
    )
    return search.BraveSearch(
        token,
        budget=search.QueryBudget(config.search.query_budget),
        max_results=config.search.max_results,
        timeout=config.budgets.timeout_seconds,
        min_interval=config.search.min_interval_seconds,
    )


# --------------------------------------------------------------------- intake


def _today() -> str:
    """Monkeypatch point for tests; UTC so a run's date is host-timezone-independent."""
    from datetime import datetime

    return datetime.now(UTC).date().isoformat()


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
        "run_date": _today(),
        "round": 0,
        "hash_history": [],
        "writer_rotation": 0,
        "lens_results": {},
        "used_critics": {},
        "critique_rounds": {},
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
        # Ingest runs at the edge, so anything it noticed about the seed — a format
        # that carried no headings, a truncated page — arrives here as text and joins
        # the run's own warnings rather than getting its own channel.
        "warnings": [*rt.warnings, *(state.get("seed_warnings") or [])],
        "pending_disputes": [],
        "adjudications": [],
        "dispute_budget_remaining": cfg.disputes.budget if cfg.disputes.enabled else 0,
        "defect_provenance": {},
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
        # Written here, not only by the web worker, so every entry path leaves the
        # exact hashed bytes on disk and any seeded run is resumable.
        rt.store.seed(seed)
        rt.store.report(1, h, seed, "seed")
        rt.store.event(
            "intake",
            path="seed",
            artifact_hash=h,
            run_date=base["run_date"],
            # Provenance belongs in the audit trail, not in the run state: it answers
            # "where did R1 come from?" without changing what any node reads.
            seed_format=state.get("seed_format"),
            seed_source=state.get("seed_source"),
        )
    else:
        rt.store.event("intake", path="question", run_date=base["run_date"])
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
        pool = roles.writer_pool(cfg.roster, rt.identities, last_author)
    except roles.RosterExhausted as exc:
        return {"fatal": True, "fatal_reason": str(exc)}

    polish = state.get("polish_next", False)
    defects = [Defect.model_validate(d) for d in state.get("defects", [])]

    # .get(): a checkpoint from before run_date existed resumes dateless, which is
    # exactly the prior behavior.
    run_date = state.get("run_date")
    if state.get("report"):
        user = prompts.writer_revision(
            state["question"],
            state["report"],
            defects,
            polish,
            rt.disputes_enabled,
            current_date=run_date,
        )
    else:
        user = prompts.writer_first_draft(state["question"], current_date=run_date)

    search_kwargs: dict[str, Any] = {}
    if rt.search_enabled:
        searcher = rt.searcher
        search_kwargs = {
            "tools": [search.SEARCH_TOOL],
            "tool_handler": search.make_tool_handler(searcher),
            "max_tool_rounds": cfg.search.max_tool_rounds,
            # Withdraw the tool the moment its budget is gone. Otherwise the handler
            # keeps answering "budget exhausted" and a determined writer spends every
            # remaining round asking again instead of writing (D42).
            "should_offer_tools": lambda: not searcher.budget.exhausted,
        }

    # One flaky response must not cost the run. Attempts rotate through the eligible
    # pool and wrap, so a model that is down, rate-limited, or answering with nothing
    # is routed around when there is somewhere to route to — and simply given another,
    # spaced, chance when there is not. On a revision round a two-writer roster leaves
    # exactly one eligible model (author exclusion already removed the other), so
    # bounding these attempts by the pool size made the whole budget 1 and one empty
    # completion aborted the run (D42). Re-asking a pool member never re-asks the
    # previous author: `writer_pool` excluded them before this ran.
    attempts = cfg.budgets.writer_attempts
    alias = ""
    completion = None
    last_failure = ""
    for offset in range(attempts):
        if offset > 0:
            rt.client.backoff_between_writer_attempts(offset)
        alias = pool[(rotation + offset) % len(pool)]
        try:
            reply = rt.client.complete(
                alias,
                system=prompts.writer_system(rt.search_enabled),
                user=user,
                max_tokens=32000,
                **search_kwargs,
            )
        except ModelCallError as exc:
            last_failure = f"generator {alias} failed: {exc}"
        else:
            if reply.text.strip():
                completion = reply
                rotation += offset
                break
            last_failure = f"generator {alias} returned an empty report"
        # Recorded per attempt: a run that silently changed authors mid-draft is
        # unauditable, and the roster's weak models are only visible from here.
        rt.store.event("generate_failed", author=rt.identities[alias], reason=last_failure)
        log.warning("writer attempt %d/%d: %s", offset + 1, attempts, last_failure)

    if completion is None:
        return {"fatal": True, "fatal_reason": f"every eligible writer failed; last: {last_failure}"}

    identity = rt.identities[alias]
    text = completion.text.strip()
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
        # Auditable after the fact: did this draft's citations come from a lookup?
        searches=completion.tool_calls,
    )

    pending_disputes = _elicit_disputes(state, rt, alias, text, defects, polish)

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
        "critique_rounds": {},
        "defects": [],
        "polish_next": False,
        "pending_lenses": [lens.value for lens in LENSES],
        "pending_disputes": pending_disputes,
    }


def _elicit_disputes(
    state: State, rt: Runtime, alias: str, revised: str, defects: list[Defect], polish: bool
) -> list[dict]:
    """The dispute-elicitation pass (D25): one separate structured call to the
    writer that just revised. Self-contained entries (full Defect + Dispute) so a
    resume between generate and adjudicate loses nothing when `defects` resets.

    Never fatal: any failure degrades to 'no disputes' and the run proceeds
    exactly as it would with the channel off."""
    if (
        not rt.disputes_enabled
        or polish
        or not defects
        or state.get("dispute_budget_remaining", 0) <= 0
    ):
        return []
    try:
        raw = rt.client.structured(
            alias,
            system=prompts.writer_system(False),
            user=prompts.writer_dispute(state["question"], revised, defects),
            schema=WriterDisputes,
            max_tokens=8000,
        )
    except (ModelCallError, MalformedOutputError) as exc:
        # Record only the exception TYPE — never `str(exc)`. A MalformedOutputError's
        # message is built from schema-validation text that echoes the REJECTED INPUT
        # (the writer's dispute grounds and evidence quotes), which is report-derived
        # (private) content; a ModelCallError message can likewise carry model I/O.
        # events.jsonl is RETAINED by `ra purge --content-only` (D25), so any
        # exception-derived string here would leak artifact text past a content purge.
        rt.store.event("dispute_pass_failed", error_type=type(exc).__name__)
        return []
    accepted = dispute_mod.validate_disputes(raw, defects, rt.config.disputes.max_per_pass)
    return [
        {"defect": d.model_dump(mode="json"), "dispute": dis.model_dump(mode="json")}
        for dis, d in accepted
    ]


# ----------------------------------------------------------------- adjudicate


def _paragraph_containing(structure, span: str) -> str:
    """The defect's locus indexes the *previous* draft, but the revised draft is
    what the run now holds — so the arbiter's context paragraph is found by span
    (the writer was told to leave disputed text intact). Falls back to the span
    itself when the text moved."""
    needle = triage._normalize(span)
    if needle:
        for p in structure.paragraphs:
            if needle in triage._normalize(p.text):
                return p.text
    return span


def _adjudicate(state: State, rt: Runtime) -> dict:
    """Rule on the writer's disputes (D25). A passthrough when there are none.

    Every path that is not an explicit `upheld` leaves the finding standing; only
    upheld records ever suppress anything downstream. This node re-runs cleanly on
    resume: `pending_disputes` is self-contained and the once-per-key registry
    makes replayed entries free duplicates."""
    pending = state.get("pending_disputes") or []
    if not pending:
        return {}

    records = [AdjudicationRecord.model_validate(r) for r in state.get("adjudications", [])]
    ruled_keys = {dispute_mod.registry_key(r.category, r.claim_span) for r in records}
    budget = state.get("dispute_budget_remaining", 0)
    provenance = state.get("defect_provenance", {})
    report_text = state["report"]
    structure = report_mod.parse(report_text)
    round_no = state.get("round", 0)
    disputer = state.get("author_identity", "(none)")

    for seq, entry in enumerate(pending, 1):
        defect = Defect.model_validate(entry["defect"])
        challenge = Dispute.model_validate(entry["dispute"])
        key = dispute_mod.registry_key(defect.category, defect.claim_span)
        # Content record first (purgeable dir): the full grounds are auditable
        # while the run is retained, and droppable with the other content.
        rt.store.dispute(
            round_no, seq, {"defect": entry["defect"], "dispute": entry["dispute"]}
        )

        if key in ruled_keys:
            verdict, method = "dismissed", "duplicate"
        elif budget <= 0:
            verdict, method = "dismissed", "budget_exhausted"
        else:
            budget -= 1
            mechanical = dispute_mod.adjudicate_mechanical(
                challenge, defect, report_text, rt.fetcher
            )
            if mechanical is True:
                verdict, method = "upheld", "mechanical"
            else:
                raisers = set(provenance.get(f"{key[0]}|{key[1]}", []))
                arbiters = dispute_mod.eligible_arbiters(
                    rt.config.roster, rt.identities, disputer, raisers
                )
                if not arbiters:
                    verdict, method = "dismissed", "no_eligible_arbiter"
                else:
                    page = None
                    if (
                        rt.fetcher is not None
                        and challenge.evidence_url
                        and challenge.evidence_url in fetch.extract_source_urls(report_text)
                    ):
                        page = rt.fetcher.fetch(challenge.evidence_url)
                    try:
                        ruling = dispute_mod.adjudicate_one(
                            rt.client,
                            arbiters[0],
                            defect,
                            challenge,
                            _paragraph_containing(structure, defect.claim_span),
                            state["question"],
                            evidence_page=page,
                            max_tokens=rt.config.disputes.arbiter_max_tokens,
                        )
                    except (ModelCallError, MalformedOutputError):
                        verdict, method = "dismissed", "arbiter_failed"
                    else:
                        verdict = "upheld" if ruling.dispute_upheld else "overruled"
                        method = "arbiter"

        ruled_keys.add(key)
        records.append(
            AdjudicationRecord(
                category=defect.category,
                claim_span=defect.claim_span,
                verdict=verdict,  # type: ignore[arg-type]
                method=method,  # type: ignore[arg-type]
                round=round_no,
            )
        )
        # Signal-only: no spans, no grounds — events.jsonl outlives a content purge.
        rt.store.event(
            "adjudication",
            category=defect.category.value,
            verdict=verdict,
            method=method,
            round=round_no,
        )

    return {
        "adjudications": [r.model_dump(mode="json") for r in records],
        "dispute_budget_remaining": budget,
        "pending_disputes": [],
    }


# ------------------------------------------------------------------- critique

#: Outcomes whose HTTP status tells an operator something the outcome alone does not.
_STATUS_BEARING_OUTCOMES = frozenset(
    {fetch.SourceOutcome.BLOCKED, fetch.SourceOutcome.NOT_FOUND}
)


def _failure_reasons(failed: list) -> dict[str, int]:
    """Tally why fetches failed, from the closed outcome vocabulary.

    This used to slice `FetchedSource.error` — free text that can be
    `"ConnectionResetError: <detail>"` or `"unreadable content type (application/pdf)"`,
    whose tail may carry a URL or page detail that does not belong in the audit trail
    (RA-016). `SourceOutcome` removes the hazard rather than filtering it: every key
    here is now a member of a fixed enum, optionally suffixed with the HTTP status,
    which is a number and not private run material.

    The status suffix is kept only where it changes what an operator would do —
    `blocked:403` (add a provider) reads differently from `blocked:429` (slow down).
    """
    counts: dict[str, int] = {}
    for source in failed:
        reason = source.outcome.value
        if source.status and source.outcome in _STATUS_BEARING_OUTCOMES:
            reason = f"{reason}:{source.status}"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _resolution_tiers(sources: list) -> dict[str, int]:
    """Tally which rung of the ladder produced each source — **all** of them, not just
    the failures (D39).

    A tier that never fires costs calls and buys nothing, and the failure tally cannot
    show that: a source the open-access tier rescued is a *success* and leaves no trace
    there. `{"direct": 5, "open_access": 2, "identifier": 4}` is what tells an operator
    whether a tier is earning its keep, and is closed-vocabulary throughout (RA-016).
    """
    counts: dict[str, int] = {}
    for source in sources:
        tier = source.tier.value
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _critique_one(
    rt: Runtime,
    lens: Lens,
    question: str,
    report_text: str,
    artifact_hash: str,
    author_identity: str,
    used: set[str],
    attempt: int,
    run_date: str | None = None,
) -> LensResult:
    """One lens, one fresh context. Failure is recorded as a *failed lens*, never as
    'no issues found' — a failed review can never manufacture a clean record."""
    try:
        alias = roles.pick_critic(
            rt.config.roster,
            rt.identities,
            lens,
            author_identity,
            used,
            # `attempt` counts this lens's tries on this artifact, so once the pool is
            # exhausted each further attempt lands on a different model.
            rotation=attempt - 1,
        )
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

    # Only the evidence lens. Handing fetched pages to logic or completeness would
    # widen what those lenses can see without widening what they may raise, and the
    # extra context is a channel for smuggling material into a scope that has no use
    # for it (docs/isolation.md).
    sources = None
    if lens is Lens.EVIDENCE and rt.verify_sources:
        urls = fetch.extract_source_urls(report_text, limit=rt.config.search.max_sources)
        sources = rt.fetcher.fetch_all(urls) if urls else None
        if sources:
            failed = [s for s in sources if not s.ok]
            # Counts alone said "10 of 12 failed" for a whole production run without
            # saying why, and a failure is cached for the run's lifetime, so the evidence
            # lens works from whatever survived. `_failure_reasons` keeps only the reason
            # *class* — a status code or an exception type — never a URL or page text
            # (RA-016).
            reasons = _failure_reasons(failed)
            rt.store.event(
                "fetch_sources",
                artifact_hash=artifact_hash,
                fetched=len(sources),
                failed=len(failed),
                failure_reasons=reasons,
                tiers=_resolution_tiers(sources),
            )
            if failed:
                log.warning(
                    "source fetch: %d of %d failed (%s)",
                    len(failed),
                    len(sources),
                    ", ".join(f"{reason}x{count}" for reason, count in sorted(reasons.items())),
                )

    result = critique_mod.critique_once(
        rt.client,
        alias,
        identity,
        lens,
        question,
        report_text,
        artifact_hash,
        author_identity,
        sources=sources,
        require_verbatim_spans=rt.config.require_verbatim_spans,
        attempt=attempt,
        current_date=run_date,
    )

    # A cited URL that a definitive not-found (404/410) does not resolve is a
    # `fabricated_citation` as a matter of fact, not a critic's judgement — so raise it
    # mechanically here, in the fetch path, rather than trusting a critic model to elect
    # to make the finding. This closes the launder that let a wholly-404 bibliography
    # clear the evidence lens (issue #92, D38). Attached only to a *completed* review: a
    # failed lens is discarded and re-critiqued (rule 2), and because the fetch is cached
    # the finding is simply re-derived on the next attempt, so nothing is lost.
    if sources and not result.failed:
        mechanical = triage.mechanical_citation_issues(sources, report_mod.parse(report_text))
        if mechanical:
            result = result.model_copy(update={"issues": [*mechanical, *result.issues]})
    return result


def _critique(state: State, rt: Runtime) -> dict:
    pending = [Lens(v) for v in state.get("pending_lenses") or [lens.value for lens in LENSES]]
    question = state["question"]
    report_text = state["report"]
    artifact_hash = state["artifact_hash"]
    author_identity = state["author_identity"]

    used_raw: dict[str, list[str]] = dict(state.get("used_critics", {}))
    used = {k: set(v) for k, v in used_raw.items()}
    # Count of tries per lens on this artifact. Unlike `used`, this keeps climbing after
    # the eligible pool is exhausted, so rule-2 retries keep rotating (docs/convergence.md)
    # rather than freezing `attempt` — and so the rotation index passed to `pick_critic`.
    rounds: dict[str, int] = dict(state.get("critique_rounds", {}))
    results = dict(state.get("lens_results", {}))

    run_date = state.get("run_date")

    def work(lens: Lens) -> LensResult:
        attempt = rounds.get(lens.value, 0) + 1
        return _critique_one(
            rt,
            lens,
            question,
            report_text,
            artifact_hash,
            author_identity,
            used.get(lens.value, set()),
            attempt,
            run_date=run_date,
        )

    with ThreadPoolExecutor(max_workers=rt.config.budgets.max_concurrency) as pool:
        for result in pool.map(work, pending):
            results[result.lens.value] = result.model_dump(mode="json")
            used.setdefault(result.lens.value, set()).add(result.critic_identity)
            rounds[result.lens.value] = rounds.get(result.lens.value, 0) + 1
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
        "critique_rounds": rounds,
    }


# --------------------------------------------------------------------- triage


def _triage(state: State, rt: Runtime) -> dict:
    cfg = rt.config
    results = [LensResult.model_validate(r) for r in state["lens_results"].values()]
    artifact_hash = state["artifact_hash"]

    # Suppression (D25) is applied ONCE, here, before anything is counted — so
    # tally, clean records, defects and the stagnation signature all see the same
    # filtered stream. Only `upheld` adjudications suppress.
    adjudications = [
        AdjudicationRecord.model_validate(r) for r in state.get("adjudications", [])
    ]
    upheld = dispute_mod.suppression_keys(adjudications)
    results, suppressed = triage.suppress(results, upheld)
    for entry in suppressed:
        rt.store.event("suppression", artifact_hash=artifact_hash, **entry)

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

    overruled = dispute_mod.overruled_keys(adjudications)
    defects = [d.model_dump(mode="json") for d in triage.to_defects(results, overruled)]
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
            # The defect list belongs to *this* artifact. Carried on the row so that
            # when finalize ships an earlier draft than the terminal one, it can hand
            # the export that draft's own defects rather than the terminal round's —
            # the same per-artifact keying the clean records already have (issue #93).
            "defects": defects,
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
        # Audit-side raiser identities per surviving material issue: consumed only
        # by arbiter *eligibility* (deterministic code), never by any prompt (D25).
        "defect_provenance": triage.defect_provenance(results),
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
    alias = rt.config.roster.orchestrator_alias
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
    # The audit trail is per-run and stays on disk; these lines are the only account of
    # a run's shape that reaches container stdout, and therefore log aggregation. A run
    # that aborts after exhausting its critique budget used to be indistinguishable, from
    # outside, from one that shipped an answer.
    if decision.action == "terminal" and decision.terminal_status != "accepted":
        log.warning(
            "run %s terminal=%s at round %d (rule %d): %s",
            rt.store.run_id,
            decision.terminal_status,
            view.round,
            decision.rule,
            decision.note,
        )
    else:
        log.info(
            "run %s round %d: rule %d -> %s (%s)",
            rt.store.run_id,
            view.round,
            decision.rule,
            decision.action,
            decision.note,
        )

    out: dict = {"decision": decision.model_dump(mode="json")}
    if decision.action == "recritique":
        out["pending_lenses"] = [lens.value for lens in decision.recritique_lenses]
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
        defects = state.get("defects", [])
    elif board:
        # Never ship the last draft just because it is last — ship the best-scoring one,
        # scored on each artifact's most-critiqued triage rather than its first.
        from .controller import best_scoring_index, latest_scores_per_artifact

        rows = latest_scores_per_artifact(board)
        idx = best_scoring_index([(b["blocking"], b["major"], b["minor"]) for b in rows])
        text = rows[idx]["report"]
        chosen_round = rows[idx]["round"]
        # The defects that annotate the shipped report must be the ones raised against
        # *it*, not against whatever draft the loop stopped on — on a non-accepted
        # terminal those differ (issue #93). The chosen row carries its own defect set;
        # a row written before this field existed has none, and the export keys on hash
        # and says so rather than substituting the terminal round's.
        defects = rows[idx].get("defects", [])
    else:
        text = state.get("report", "")
        chosen_round = state.get("round", 0)
        defects = state.get("defects", [])

    artifact_hash = report_mod.artifact_hash(text) if text else None
    # Stamp each outstanding defect with the artifact it belongs to, so an export can
    # filter to the shipped draft exactly as `_reviewers` filters the clean records —
    # one keying discipline for both annotation surfaces (issue #93).
    outstanding = [{**d, "artifact_hash": artifact_hash} for d in defects]

    view = state.get("view", {})
    summary = {
        "run_id": state["run_id"],
        "terminal_status": status,
        "rounds": state.get("round", 0),
        "chosen_round": chosen_round,
        "artifact_hash": artifact_hash,
        "final_view": view,
        "clean_records": state.get("clean_records", []),
        "outstanding_defects": outstanding,
        "warnings": state.get("warnings", []),
        "note": Decision.model_validate(state["decision"]).note if state.get("decision") else "",
        "label": (
            "consensus-reviewed with verified sourcing"
            if rt.verify_sources
            else "consensus-reviewed with retrieved sourcing"
            if rt.search_enabled
            else "consensus-reviewed with in-artifact sourcing (no external retrieval)"
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
    graph.add_node("adjudicate", lambda s: _adjudicate(s, rt))
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
    # The adjudicate node sits on the one-way generate→critique edge (D25): it
    # introduces no new cycle, and a shutdown between the two nodes resumes here
    # with `pending_disputes` intact.
    graph.add_conditional_edges(
        "generate",
        lambda s: "control" if s.get("fatal") else "adjudicate",
        {"adjudicate": "adjudicate", "control": "control"},
    )
    graph.add_edge("adjudicate", "critique")
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


def _recursion_limit(config: Config) -> int:
    """Size the graph's step budget from the *loop's* own bounded transitions.

    A flat limit can bite before the controller's budgets do: rule 2 and rule 8 each
    re-enter critique → triage → orchestrate → control without generating, so a
    small `hard_cap` with large retry budgets could hit LangGraph's ceiling and raise
    instead of terminating at rule 3 or 11.
    """
    b = config.budgets
    laps = b.hard_cap + b.polish_cap + b.critique_attempts + b.confirmation_attempts + 2
    return max(100, laps * 6 + 10)


def _run_fingerprint(config: Config, question: str, seed: str | None) -> str:
    """What a resumed run must still be: the same question, the same seed, the same
    roster and the same budgets. Resuming a checkpoint under different inputs would
    answer a question nobody asked."""
    import hashlib

    payload = json.dumps(
        {
            "question": question,
            "seed_hash": report_mod.artifact_hash(seed) if seed else None,
            "roster": config.roster.model_dump(),
            "budgets": config.budgets.model_dump(),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ResumeMismatch(RuntimeError):
    """The stored run was started with different inputs than this invocation."""


class GracefulStop(RuntimeError):
    """The process was asked to shut down and the run stopped at a node boundary.

    Not a failure: the checkpoint is durable and the run is resumable from exactly
    where it paused. Carries the run id so callers can say how to pick it back up.
    """

    def __init__(self, message: str, run_id: str) -> None:
        super().__init__(message)
        self.run_id = run_id


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
    stop: threading.Event | None = None,
    seed_format: str | None = None,
    seed_source: str | None = None,
    seed_warnings: list[str] | None = None,
    owner: str | None = None,
) -> dict:
    """`seed` is markdown. Callers holding a PDF, a .docx or a URL convert it first
    with `ingest`, at the edge, and pass the provenance it returns through the
    `seed_*` parameters.

    `owner` is the identity this run belongs to, and is what makes a CLI run visible
    in the web interface. It stays out of `_run_fingerprint` on purpose: the
    fingerprint guards against a run resuming under changed *inputs*, and who owns a
    run is not one — attributing an existing run must never cost it its checkpoint.
    """
    rt = build_runtime(config, run_id, client)
    if owner:
        rt.store.owner(owner)
    checkpointer = checkpointer if checkpointer is not None else _checkpointer(rt)
    compiled = build_graph(rt).compile(checkpointer=checkpointer)
    invoke_config = {
        "recursion_limit": _recursion_limit(config),
        "configurable": {"thread_id": rt.store.run_id},
    }

    # Resumability is the point of the checkpointer: this is a slow local-model
    # system, and losing an hour of critique to a dropped connection is the failure
    # mode that matters. An unfinished thread continues from its last completed node;
    # a fresh one starts at intake.
    fingerprint = _run_fingerprint(config, question, seed)
    initial: State | None = {
        "run_id": rt.store.run_id,
        "question": question,
        "seed": seed,
        "seed_format": seed_format,
        "seed_source": seed_source,
        "seed_warnings": list(seed_warnings or []),
        "fingerprint": fingerprint,
    }
    if checkpointer is not None:
        snapshot = compiled.get_state(invoke_config)
        stored = snapshot.values.get("fingerprint")
        if stored and stored != fingerprint:
            raise ResumeMismatch(
                f"run '{rt.store.run_id}' was started with a different question, seed, "
                f"roster or budget set; refusing to resume it under new inputs"
            )
        if snapshot.next:
            log.info("resuming run %s at %s", rt.store.run_id, snapshot.next)
            rt.store.event("resume", resumed_at=list(snapshot.next))
            initial = None
        elif snapshot.values.get("terminal_status"):
            log.info("run %s already terminated", rt.store.run_id)
            return {**snapshot.values, "run_dir": str(rt.store.dir), "run_id": rt.store.run_id}

    final = _drive(compiled, initial, invoke_config, rt, stop)
    final["run_dir"] = str(rt.store.dir)
    final["run_id"] = rt.store.run_id
    return final


def _drive(compiled: Any, initial: State | None, invoke_config: dict, rt: Runtime,
           stop: threading.Event | None) -> dict:
    """Run the graph, stopping at a node boundary if asked to shut down.

    This is `invoke()` unrolled. Streaming is the only way to observe "a node just
    completed and its result is checkpointed", which is the one instant where stopping
    is free — the node's model calls are paid for and persisted, and the resume picks up
    at the next node. Stopping anywhere else either discards work already paid for or
    persists a half-finished node as though it were complete.

    `durability="sync"` is deliberate but not a bug fix. The default is `"async"`, which
    hands the checkpoint write to a background executor awaited as the *next* step runs;
    closing the stream cleanly still flushes it, because the executor is shut down as the
    loop's ExitStack unwinds. Both modes were measured here and both keep the work.
    What `"sync"` removes is the dependency on that teardown ordering — the checkpoint is
    already durable when we observe the node completing, rather than durable because
    something else cleans up correctly afterwards. At minutes per node, an inline sqlite
    write is free, so the weaker guarantee buys nothing.

    Deliberately not finer-grained: `_critique` fans out across lenses, and
    short-circuiting the ones that have not started yet would checkpoint a partially
    critiqued round as if it were whole.
    """
    final: dict | None = None
    stream = compiled.stream(initial, config=invoke_config, stream_mode="values",
                             durability="sync")
    with closing(stream):
        for values in stream:
            final = values
            if stop is not None and stop.is_set():
                pending = list(compiled.get_state(invoke_config).next)
                log.info("pausing run %s at %s for shutdown", rt.store.run_id, pending)
                rt.store.event("pause", reason="shutdown", next=pending)
                raise GracefulStop(
                    f"run '{rt.store.run_id}' paused for shutdown before {pending or 'the end'}; "
                    f"it resumes from this point",
                    rt.store.run_id,
                )

    if final is None:  # pragma: no cover - stream always yields at least the input
        raise RuntimeError(f"run '{rt.store.run_id}' produced no state")
    return final
