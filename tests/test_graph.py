"""End-to-end loop behaviour, driven by a scripted fake proxy (no network)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fakes import FakeClient
from pydantic import ValidationError

from reasonable_answer import prompts
from reasonable_answer.config import Budgets, Config, ConfigError, RepairConfig, RevisionConfig
from reasonable_answer.graph import _lens_results, run
from reasonable_answer.llm import ModelCallError
from reasonable_answer.schemas import CritiqueOutput, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""


def test_legacy_single_result_checkpoint_shape_is_wrapped_for_resume():
    legacy = {
        "lens": "logic",
        "artifact_hash": "h" * 64,
        "critic_alias": "logic-spec",
        "critic_identity": "vendor/logic",
        "artifact_author_identity": "vendor/writer",
        "issues": [],
        "failed": False,
        "failure_reason": None,
        "attempt": 1,
    }

    assert _lens_results({"lens_results": {"logic": legacy}}) == {"logic": [legacy]}


def lens_of(user: str) -> str:
    for lens in ("logic", "evidence", "completeness"):
        if f"YOUR DIMENSION: {lens}" in user:
            return lens
    raise AssertionError("no lens in prompt")


def uncited(section=1, paragraph=1) -> RawIssue:
    return RawIssue(
        category=Category.UNCITED_CLAIM,
        severity=Severity.MAJOR,
        locus=StructuralRef(section=section, paragraph=paragraph),
        claim_span="A claim that is fully supported",
        rationale="no citation attached",
        instruction="cite a source or remove the claim",
    )


def clean(_alias, _user) -> CritiqueOutput:
    return CritiqueOutput(issues=[])


#: an in-scope material issue for whichever lens is asking — a critic that raises a
#: category outside its lens fails the lens instead, which is a different test.
LENS_CATEGORY = {
    "logic": Category.OVERSTATED_CLAIM,
    "evidence": Category.UNCITED_CLAIM,
    "completeness": Category.OMITTED_COUNTERARGUMENT,
}


def always_material(_alias, user) -> CritiqueOutput:
    return CritiqueOutput(
        issues=[uncited().model_copy(update={"category": LENS_CATEGORY[lens_of(user)]})]
    )


def make_client(identities, critique_fn=clean, report=REPORT, polish=False) -> FakeClient:
    return FakeClient(
        identities=identities,
        critique_fn=critique_fn,
        report_fn=lambda n: report,
        polish_recommended=polish,
    )


def test_a_clean_report_reaches_accepted_with_two_reviewers_per_lens(identities, config):
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] == "accepted"

    summary = json.loads((client_run_dir(final) / "final.json").read_text())
    cleared: dict[str, set[str]] = {}
    for record in summary["clean_records"]:
        cleared.setdefault(record["lens"], set()).add(record["critic_identity"])
    assert all(len(v) >= 2 for v in cleared.values()), cleared


def test_every_run_names_the_build_that_produced_it(identities, config):
    """D-run-build-stamp. Driven end to end rather than unit-tested because the value of
    the stamp is that it is written without anyone remembering to write it: the failure
    mode is a new terminal path that finalizes without one."""
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    run_dir = client_run_dir(final)

    summary = json.loads((run_dir / "final.json").read_text())
    assert set(summary["build"]) == {"commit", "dirty", "source"}

    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    startups = [e["build"] for e in events if e["kind"] == "startup"]
    assert startups == [summary["build"]], "one attempt, so one build, and it is the one that finalized"


def test_the_build_stamp_never_reaches_the_blind_orchestrator(identities, config):
    """The controller decides on an OrchestratorView that carries no identifiers. A key
    added to the store must not become a key the orchestrator can see — asserted here
    rather than argued, because the store and the view are edited by different people."""
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)

    views = (client_run_dir(final) / "signals" / "views.jsonl").read_text()
    assert "build" not in views
    assert "commit" not in views


def client_run_dir(final):
    from pathlib import Path

    return Path(final["run_dir"])


def test_min_ticks_is_enforced_on_the_seed_path(identities, config):
    """A provided report is never accepted on its first critique (RA-018)."""
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["round"] >= config.budgets.min_ticks


def test_material_issues_drive_regeneration_until_the_cap(identities, config):
    """A critic that never relents must terminate at the cap, not loop forever."""
    client = make_client(identities, critique_fn=always_material)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] in ("exhausted_unresolved", "needs_human_review")
    assert final["round"] <= config.budgets.hard_cap


def test_stagnation_exits_early(identities, tmp_path, roster):
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=20, stagnation_limit=2),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    # a genuinely new draft each tick, so this exits on the stuck *signal* rather
    # than on the cycle detector
    client.report_fn = lambda n: REPORT.replace("A claim", f"Draft {n}: a claim")
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] == "exhausted_unresolved"
    assert final["round"] < cfg.budgets.hard_cap  # stopped early, did not burn the cap


def test_stagnation_spends_one_rewrite_then_exits(identities, tmp_path, roster):
    """D-scoped-revision: the valve fires once, is bounded, and does not become a loop."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=20, stagnation_limit=2, rewrite_cap=1),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    client.report_fn = lambda n: REPORT.replace("A claim", f"Draft {n}: a claim")
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "exhausted_unresolved"
    assert final["round"] < cfg.budgets.hard_cap
    assert final["rewrites_used"] == 1

    events = _events(cfg, final)
    rewrites = [e for e in events if e["kind"] == "generate" and e.get("full_rewrite")]
    assert len(rewrites) == 1, "the cap must bind — one rewrite, not one per stagnant tick"
    # Rule 13 fired twice: once to spend the rewrite, once to give up.
    rule_13 = [e for e in events if e["kind"] == "control" and e["rule"] == 13]
    assert [e["action"] for e in rule_13] == ["generate", "terminal"]


def test_two_rewrites_are_separated_by_a_judged_draft(identities, tmp_path, roster):
    """D-scoped-revision, the load-bearing `stagnation_count = 0` reset in `_control`:
    with `rewrite_cap=2` the two rewrites must be spent one per fresh stall, separated by
    at least one ordinary (rule-14) generation whose draft is judged — never in
    consecutive control ticks. Deleting the reset leaves `stagnation_count` at the limit,
    so rule 13 re-fires the very next tick and the two `full_rewrite` generations land
    adjacent; this test is what fails when that happens (`rewrite_cap=1` cannot see it,
    because the second firing is terminal regardless of the reset)."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=20, stagnation_limit=2, rewrite_cap=2),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    # A genuinely new draft each tick: the per-category signal stays stationary (so
    # stagnation recurs) while the artifact hash keeps changing (so the cycle detector
    # stays out of it) — each rewrite is granted on a fresh stall, not a repeated hash.
    client.report_fn = lambda n: REPORT.replace("A claim", f"Draft {n}: a claim")
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "exhausted_unresolved"
    assert final["round"] < cfg.budgets.hard_cap
    assert final["rewrites_used"] == 2

    events = _events(cfg, final)
    generates = [e for e in events if e["kind"] == "generate"]
    rewrite_idx = [i for i, e in enumerate(generates) if e.get("full_rewrite")]
    assert len(rewrite_idx) == 2, "the cap must bind at two — one rewrite per stall"
    between = generates[rewrite_idx[0] + 1 : rewrite_idx[1]]
    assert any(not e.get("full_rewrite") for e in between), (
        "a rewritten draft must be judged before the next rewrite is granted; the "
        "budget must not be spent in consecutive control ticks (the stagnation_count reset)"
    )
    # Rule 13 fired three times: spend, spend, give up.
    rule_13 = [e for e in events if e["kind"] == "control" and e["rule"] == 13]
    assert [e["action"] for e in rule_13] == ["generate", "generate", "terminal"]


def test_a_stagnant_run_with_no_rewrite_budget_behaves_exactly_as_before(
    identities, tmp_path, roster
):
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=20, stagnation_limit=2, rewrite_cap=0),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    client.report_fn = lambda n: REPORT.replace("A claim", f"Draft {n}: a claim")
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "exhausted_unresolved"
    assert final["rewrites_used"] == 0
    assert not [e for e in _events(cfg, final) if e.get("full_rewrite")]


def test_patch_mode_measures_scope_without_rejecting_anything(identities, tmp_path, roster):
    """The scope check is warn-only: a writer that rewrites everything still ships."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    assert cfg.revision.mode == "patch"  # the shipped default
    client = make_client(identities, critique_fn=always_material)
    # Every fix task names S1.P1; this writer leaves S1.P1 alone and rewrites the
    # paragraph nobody asked about — precisely the behaviour the check exists to see.
    client.report_fn = lambda n: REPORT.replace("A real-looking source.", f"Source variant {n}.")
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] in ("exhausted_unresolved", "needs_human_review")
    revisions = [
        e
        for e in _events(cfg, final)
        if e["kind"] == "generate" and "changed_paragraphs" in e
    ]
    assert revisions, "a revision under patch mode must carry the scope measurement"
    assert any(e["out_of_scope"] > 0 for e in revisions)
    # Warn-only: nothing was rejected, so no writer attempt was burned on it.
    assert not [e for e in _events(cfg, final) if e["kind"] == "generate_failed"]


def test_patch_mode_wires_claim_spans_into_restatement_measurement(identities, tmp_path, roster):
    """A triaged defect's claim span reaches the generate event's scope measurement."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    claim = "A repeated claim that is presented as fully supported by the available evidence."
    qualified = "The available evidence offers qualified support for the repeated claim."
    report = f"""# Answer

{claim}

## Detail

{claim}

## Sources

[1] A real-looking source.
"""

    def repeated_claim(_alias, user) -> CritiqueOutput:
        return CritiqueOutput(
            issues=[
                uncited().model_copy(
                    update={
                        "category": LENS_CATEGORY[lens_of(user)],
                        "claim_span": claim,
                    }
                )
            ]
        )

    client = make_client(identities, critique_fn=repeated_claim)
    client.report_fn = lambda _n: report.replace(claim, qualified)
    final = run(cfg, question="Is it so?", seed=report, client=client)

    revisions = [
        e
        for e in _events(cfg, final)
        if e["kind"] == "generate" and "changed_paragraphs" in e
    ]
    assert revisions
    assert revisions[0]["in_scope"] == 1
    assert revisions[0]["restated"] == 1
    assert revisions[0]["out_of_scope"] == 0


def test_patch_mode_records_additive_only_on_generate_event(identities, tmp_path, roster):
    """The wired graph publishes an additive-only repair on its generate event."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    client.report_fn = lambda _n: REPORT.replace(
        "A claim that is fully supported [1].",
        "A claim that is fully supported [1], with an added qualifier.",
    )
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    revisions = [
        e
        for e in _events(cfg, final)
        if e["kind"] == "generate" and "changed_paragraphs" in e
    ]
    assert revisions
    assert revisions[0]["additive_only"] == 1


def test_the_first_draft_carries_no_scope_measurement(identities, tmp_path, roster):
    """Absent means "not applicable", never "in scope" — the A/B must not average the
    first draft, a polish pass, or a rule-13 rewrite into the out-of-scope rate."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    final = run(cfg, question="Is it so?", client=client)
    first = [e for e in _events(cfg, final) if e["kind"] == "generate"][0]
    assert "changed_paragraphs" not in first


def _events(cfg: Config, final: dict) -> list[dict]:
    path = Path(cfg.runs_dir) / final["run_id"] / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_blocking_issue_at_the_cap_needs_human_review(identities, config):
    def blocking(_alias, _user) -> CritiqueOutput:
        return CritiqueOutput(
            issues=[
                uncited().model_copy(
                    update={
                        "category": Category.FABRICATED_CITATION,
                        "severity": Severity.MINOR,  # floored up to blocking by triage
                    }
                )
            ]
        )

    client = make_client(
        identities,
        critique_fn=lambda a, u: blocking(a, u) if lens_of(u) == "evidence" else CritiqueOutput(issues=[]),
    )
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] == "needs_human_review"


def test_the_triage_event_records_the_severity_tuple_the_selection_reads(identities, config):
    """D-latest-unblocked-selection: `material` alone left the shipped round
    unauditable — two independent reviews of production runs could not tell a considered
    selection from a bug, because the count does not say how many issues were blocking."""

    def blocking(_alias, _user) -> CritiqueOutput:
        return CritiqueOutput(
            issues=[
                uncited().model_copy(
                    update={
                        "category": Category.FABRICATED_CITATION,
                        "severity": Severity.MINOR,  # floored up to blocking by triage
                    }
                )
            ]
        )

    client = make_client(
        identities,
        critique_fn=lambda a, u: blocking(a, u) if lens_of(u) == "evidence" else CritiqueOutput(issues=[]),
    )
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    triages = [
        json.loads(line)
        for line in (client_run_dir(final) / "events.jsonl").read_text().splitlines()
        if json.loads(line)["kind"] == "triage"
    ]
    assert triages
    for event in triages:
        # `material` is kept — nothing reading the old trail loses a field.
        assert event["material"] == event["blocking"] + event["major"]
        assert event["blocking"] >= 1
        assert event["minor"] == 0


def test_a_failing_lens_can_never_produce_an_accept(identities, config):
    """Fail-closed: a lens that keeps returning garbage aborts the run rather than
    letting the other two lenses accept the report."""

    def hostile(alias, user):
        if lens_of(user) == "evidence":
            raise RuntimeError("provider exploded")
        return CritiqueOutput(issues=[])

    from reasonable_answer.llm import ModelCallError

    def critique_fn(alias, user):
        if lens_of(user) == "evidence":
            raise ModelCallError("provider exploded")
        return CritiqueOutput(issues=[])

    client = make_client(identities, critique_fn=critique_fn)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] == "aborted"


def test_an_out_of_scope_category_fails_the_lens_not_the_issue(identities, config):
    """The evidence lens returning a logic category must fail the whole lens —
    silently dropping it would let a fabricated clean review through."""

    def critique_fn(alias, user):
        if lens_of(user) == "evidence":
            return CritiqueOutput(
                issues=[uncited().model_copy(update={"category": Category.INVALID_INFERENCE})]
            )
        return CritiqueOutput(issues=[])

    client = make_client(identities, critique_fn=critique_fn)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    assert final["terminal_status"] == "aborted"


def test_the_generator_is_never_the_author_of_the_draft_it_revises(identities, config):
    client = make_client(identities, critique_fn=always_material)
    run(config, question="Is it so?", seed=REPORT, client=client)

    writers = [c.alias for c in client.calls if c.schema is None]
    assert writers  # sanity
    # strict=False is deliberate: this pairs each writer with its successor, so the two
    # sequences differ in length by one by construction.
    assert all(a != b for a, b in zip(writers, writers[1:], strict=False)), writers


def test_every_critique_call_excludes_the_author(identities, config):
    client = make_client(identities, critique_fn=always_material)
    run(config, question="Is it so?", seed=REPORT, client=client)

    author = None
    for call in client.calls:
        if call.schema is None:  # a generation
            author = identities[call.alias]
        elif call.schema == "CritiqueOutput" and author is not None:
            assert identities[call.alias] != author


def test_rule_2_retries_keep_rotating_after_the_critic_pool_is_exhausted(
    identities, config, tmp_path
):
    """docs/convergence.md promises that once every eligible critic has reviewed a lens,
    successive rule-2 retries rotate through the pool instead of re-asking the model that
    just failed. `used_critics` is a set of distinct identities, so its length stops
    growing at exhaustion; the rotation index must come from a monotonic per-lens counter
    (`critique_rounds`), not `len(used_critics)` — which would freeze `attempt` and pin
    every later retry on one fallback (run-3b4fe4760289 spent 11 of 12 attempts on one
    critic and aborted). This drives the round-level `_critique` so the graph, not just
    `pick_critic`, is exercised.

    At the default review depth of 2 (D-front-loaded-depth) the first pass draws both
    eligible models at once, so the pool is exhausted one pass sooner; the guarantee
    being pinned here is about the retries *after* that, which is where the regression
    was."""
    from reasonable_answer.graph import Runtime, _critique
    from reasonable_answer.llm import ModelCallError
    from reasonable_answer.store import RunStore

    def always_fails(_alias, _user):
        raise ModelCallError("provider exploded")

    client = FakeClient(
        identities=identities,
        critique_fn=always_fails,
        report_fn=lambda n: REPORT,
    )
    rt = Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-rotate"),
    )

    # Author is writer-a, so the logic lens's eligible non-author pool is
    # {logic-spec, writer-b} — exactly two identities.
    pool = {identities["logic-spec"], identities["writer-b"]}
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "artifact_hash": "h" * 64,
        "author_identity": identities["writer-a"],
        "pending_lenses": ["logic"],
        "run_date": "2026-07-28",
    }

    picks: list[list[str]] = []
    seen = 0
    for _ in range(5):
        out = _critique(state, rt)
        state = {**state, **out}
        results = state["lens_results"]["logic"]
        assert all(r["failed"] for r in results)
        picks.append([r["critic_identity"] for r in results[seen:]])
        seen = len(results)

    # The depth-2 first pass exhausts the pool; the guarantee is about what follows.
    assert set(picks[0]) == pool, picks
    later = [p[0] for p in picks[1:]]
    assert all(len(p) == 1 for p in picks[1:]), picks  # nothing fresh left to double up
    assert set(later) == pool, picks  # later retries still cover the whole pool
    assert later[0] != later[1], picks  # consecutive post-exhaustion retries alternate


def test_intake_rejects_a_seed_without_a_question(identities, config):
    client = make_client(identities)
    with pytest.raises(ConfigError, match="question is required"):
        run(config, question="   ", seed=REPORT, client=client)


def test_intake_rejects_an_oversized_seed(identities, config):
    client = make_client(identities)
    with pytest.raises(ConfigError, match="seed exceeds"):
        run(config, question="q?", seed="x" * (config.max_report_chars + 1), client=client)


def plant_unfit_verdict(config: Config, identity: str, tmp_path: Path) -> None:
    """Point `config` at a cache holding one silent-critic verdict for `identity` on the
    logic lens. Real corpus and prompt hashes: anything else and the entry is discarded
    as not-about-this-harness, and a gate test would pass for the wrong reason."""
    from reasonable_answer import audition

    config.audition.cache_path = tmp_path / "audition.json"
    silent = audition.Metrics(
        alias="logic-spec", identity=identity, lens=Lens.LOGIC,
        calls=10, planted_total=6, obvious_total=6, control_runs=4, control_clean_runs=4,
        # Graded on every fixture it owed: the verdict under test is silence, not the
        # coverage gate, which would otherwise reach `unfit` first for the wrong reason.
        fixtures_owed=5,
    )
    assert audition.judge(silent, config.audition.thresholds).verdict is audition.Verdict.UNFIT
    audition.save_cache(
        config.audition.cache_path,
        {
            audition.cache_key(identity, Lens.LOGIC): audition.CacheEntry(
                metrics=silent,
                corpus_hash=audition.load_fixtures().corpus_hash,
                prompt_hash=audition.prompt_hash(),
                rubric_hash=audition.rubric_hash(),
                require_verbatim_spans=config.require_verbatim_spans,
                structured_output_mode="json_schema",
                repetitions=config.audition.repetitions,
                recorded_at=time.time(),
            )
        },
    )


def test_audition_enforcement_refuses_to_start_before_spending_anything(
    identities, config, tmp_path
):
    """D-critic-audition's opt-in fail-closed. A lens staffed by a measured-unfit critic is not being
    reviewed, so the run must not begin — and must not pay for the structured-output
    probes on its way to finding that out."""
    plant_unfit_verdict(config, identities["logic-spec"], tmp_path)
    config.audition.enforce = True

    client = make_client(identities)
    with pytest.raises(ConfigError, match="unfit"):
        run(config, question="Is it so?", seed=REPORT, client=client)
    assert client.calls == [], "the gate spent tokens before failing closed"


def test_an_unfit_critic_is_only_a_warning_while_enforcement_is_off(
    identities, config, tmp_path
):
    """The shipped posture. Same cache, same roster, `enforce` off — the run proceeds."""
    plant_unfit_verdict(config, identities["logic-spec"], tmp_path)

    final = run(config, question="Is it so?", seed=REPORT, client=make_client(identities))
    assert final["terminal_status"] == "accepted"


def test_the_audit_trail_records_every_stage(identities, config):
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    events = [
        json.loads(line)
        for line in (client_run_dir(final) / "events.jsonl").read_text().splitlines()
    ]
    kinds = {e["kind"] for e in events}
    assert {"startup", "intake", "critique", "triage", "orchestrate", "control", "finalize"} <= kinds


def test_the_orchestrator_runs_on_its_configured_model_not_the_first_writer(
    identities, tmp_path, roster
):
    """Before this was configurable the referee was implicitly writers[0], so merely
    reordering the writer pool changed who adjudicated polish."""
    cfg = Config(
        roster=roster.model_copy(update={"orchestrator": "referee"}),
        budgets=Budgets(min_ticks=2, hard_cap=5, polish_cap=1),
        runs_dir=tmp_path / "runs",
    )
    client = make_client({**identities, "referee": "vendor-f/referee"})
    run(cfg, question="Is it so?", seed=REPORT, client=client)

    orchestrations = [c for c in client.calls if c.schema == "OrchestratorRecommendation"]
    assert orchestrations, "the orchestrator never ran"
    assert {c.alias for c in orchestrations} == {"referee"}


def test_run_directory_is_private(identities, config):
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    mode = client_run_dir(final).stat().st_mode & 0o777
    assert mode == 0o700


# ------------------------------------------------------- a dud writer is routed around


class DudWriterClient(FakeClient):
    """A proxy where the named writers answer generation with nothing at all.

    Critique still works — the point is that one bad *writer* must not decide the run.
    """

    dud_writers: set[str] = set()

    def complete(self, alias, *, system, user, **kwargs):
        completion = super().complete(alias, system=system, user=user, **kwargs)
        if alias in self.dud_writers and "YOUR DIMENSION" not in user:
            return completion.__class__(
                text="",
                model_reported=alias,
                prompt_tokens=0,
                completion_tokens=0,
            )
        return completion


def make_dud_client(identities, duds, critique_fn=clean) -> DudWriterClient:
    client = DudWriterClient(
        identities=identities,
        critique_fn=critique_fn,
        report_fn=lambda n: REPORT,
    )
    client.dud_writers = set(duds)
    return client


def events_of(final) -> list[dict]:
    return [
        json.loads(line)
        for line in (client_run_dir(final) / "events.jsonl").read_text().splitlines()
    ]


def test_an_empty_writer_falls_through_to_the_next_one(identities, config):
    """Run run-4d350e1d27a8 died here: one writer returned nothing and the whole run
    aborted with the defects of round 1 still open."""
    client = make_dud_client(identities, duds={"writer-a"})
    final = run(config, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "accepted"
    assert not final["fatal"]
    # The dud never authored anything; the fallback did.
    assert final["author_identity"] == identities["writer-b"]

    failures = [e for e in events_of(final) if e["kind"] == "generate_failed"]
    assert failures, "the discarded attempt must stay on the record"
    assert failures[0]["author"] == identities["writer-a"]
    assert "empty report" in failures[0]["reason"]


def test_a_pool_of_duds_is_still_fatal(identities, config):
    """Resilience is routing around a bad model, not inventing a report."""
    client = make_dud_client(identities, duds={"writer-a", "writer-b"})
    final = run(config, question="Is it so?", seed=REPORT, client=client)

    assert final["fatal"]
    assert "every eligible writer failed" in final["fatal_reason"]


class FlakyWriterClient(FakeClient):
    """A proxy where nominated *generation calls* come back empty, by ordinal.

    Not keyed by alias, unlike `DudWriterClient`: the failure being reproduced is
    transient, so the same model has to fail once and then work.
    """

    empty_generations: set[int] = set()

    def complete(self, alias, *, system, user, **kwargs):
        completion = super().complete(alias, system=system, user=user, **kwargs)
        if self.generations in self.empty_generations:
            return completion.__class__(
                text="", model_reported=alias, prompt_tokens=0, completion_tokens=0
            )
        return completion


def test_the_only_eligible_writer_is_asked_again_rather_than_the_run_aborted(
    identities, config
):
    """D-provider-retry, and the shape of the three runs that aborted on 2026-07-29.

    Author exclusion applies to writers, so from round two a two-writer roster leaves
    exactly ONE eligible model. `attempts` used to be `min(len(pool), writer_attempts)`,
    which made the retry budget 1 — every abort logged `writer attempt 1/1` — so a
    single empty completion ended the run with its defects still open.
    """
    client = FlakyWriterClient(
        identities=identities,
        critique_fn=always_material,  # keeps the loop generating past round one
        # A distinct draft each round, or rule 12 freezes the run for a repeated
        # artifact before the writer fallback is ever reached.
        report_fn=lambda n: f"{REPORT}\nRevision {n}.\n",
    )
    # The second generation is the first on a one-deep pool: generation one ran with
    # both writers eligible (a human seed excludes nobody).
    client.empty_generations = {2}

    final = run(config, question="Is it so?", seed=REPORT, client=client)

    assert not final["fatal"], "a transient empty completion must not end the run"

    events = events_of(final)
    failures = [e for e in events if e["kind"] == "generate_failed"]
    assert len(failures) == 1
    assert "empty report" in failures[0]["reason"]

    # The sharp end: the draft that followed the failure was written by the SAME model,
    # because it was the only eligible one. Before D-provider-retry there was no second attempt to
    # make, and this was `terminal=aborted`.
    generated = [e for e in events if e["kind"] == "generate"]
    assert generated[1]["author"] == failures[0]["author"]

    # And it waited first, rather than re-asking a model mid-wobble inside a second.
    assert client.writer_backoffs == [1]


def test_writer_attempts_bounds_the_retry_of_a_single_eligible_writer(identities, config):
    """The other half: wrapping the rotation must not become an unbounded retry. Three
    attempts, then rule 1, exactly as `budgets.writer_attempts` says."""
    client = FlakyWriterClient(
        identities=identities,
        critique_fn=always_material,
        report_fn=lambda n: f"{REPORT}\nRevision {n}.\n",
    )
    # Generation 1 succeeds; every attempt after it comes back empty.
    client.empty_generations = set(range(2, 40))

    final = run(config, question="Is it so?", seed=REPORT, client=client)

    assert final["fatal"]
    assert "every eligible writer failed" in final["fatal_reason"]
    failures = [e for e in events_of(final) if e["kind"] == "generate_failed"]
    assert len(failures) == config.budgets.writer_attempts == 3
    # One wait per retry, never before the first attempt.
    assert client.writer_backoffs == [1, 2]


class RaisingWriterClient(FakeClient):
    """A proxy where generation raises the error a named class of defect produces."""

    error: Exception | None = None

    def complete(self, alias, *, system, user, **kwargs):
        if self.error is not None and "YOUR DIMENSION" not in user:
            raise self.error
        return super().complete(alias, system=system, user=user, **kwargs)


def test_a_failed_writer_attempt_records_which_defect_it_failed_on(identities, config):
    """D-writer-failure-class. `reason` names the alias and quotes the provider, so it
    is unique per attempt and cannot be grouped; counting "how often did this writer
    emit unparsed tool-call markup" needs a stable token.

    This is not hypothetical. Eight consecutive `nemotron-3-ultra` tool loops were read
    off a run as a broken model, and the defect was the upstream provider OpenRouter
    happened to route each call to — a distinction `reason` alone could not carry, and
    that a per-class count against a pinned provider makes plain.
    """
    client = RaisingWriterClient(
        identities=identities,
        critique_fn=clean,
        report_fn=lambda n: REPORT,
    )
    client.error = ModelCallError(
        "writer-a: emitted unparsed tool-call markup as content",
        failure_class="unparsed_tool_markup",
    )

    final = run(config, question="Is it so?", client=client)

    assert final["fatal"]
    failures = [e for e in events_of(final) if e["kind"] == "generate_failed"]
    assert failures, "a writer that raised must still be recorded"
    assert {e["failure_class"] for e in failures} == {"unparsed_tool_markup"}


def test_a_writer_that_answers_with_whitespace_is_its_own_failure_class(identities, config):
    """Distinct from anything `llm` raises: the call succeeded and the model answered.
    Folding it into the transport classes would hide the one defect no retry budget or
    provider pin can fix."""
    client = FlakyWriterClient(
        identities=identities,
        critique_fn=always_material,
        report_fn=lambda n: f"{REPORT}\nRevision {n}.\n",
    )
    client.empty_generations = {2}

    final = run(config, question="Is it so?", seed=REPORT, client=client)

    failures = [e for e in events_of(final) if e["kind"] == "generate_failed"]
    assert [e["failure_class"] for e in failures] == ["empty_report"]
def test_seed_warnings_from_ingest_reach_the_final_record(identities, config):
    """Ingest runs at the edge, so anything it noticed about the seed has to be carried
    into the run to be visible at all — the run page and final.json read `warnings`."""
    final = run(
        config,
        question="Does it hold?",
        seed="Prose with no headings whatsoever.",
        seed_format="pdf",
        seed_source="file:draft.pdf",
        seed_warnings=["seed converted from pdf but no headings were recovered"],
        client=make_client(identities),
    )
    assert any("no headings were recovered" in w for w in final["warnings"])


def test_seed_provenance_lands_on_the_intake_event(identities, config):
    """Provenance belongs in the audit trail: it answers 'where did R1 come from?'
    without any node routing on it."""
    final = run(
        config,
        question="Does it hold?",
        seed=REPORT,
        seed_format="docx",
        seed_source="file:q3.docx",
        client=make_client(identities),
    )
    events = [
        json.loads(line)
        for line in (Path(final["run_dir"]) / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    intake = next(e for e in events if e["kind"] == "intake")
    assert intake["seed_format"] == "docx"
    assert intake["seed_source"] == "file:q3.docx"


def test_a_seeded_run_stores_the_exact_bytes_it_hashed(identities, config):
    """Resume reproduces `_run_fingerprint` from `seed.md`, so those bytes must match
    the seed exactly — `reports/r01-*.md` cannot serve, it carries an author header.
    Written by the graph rather than only the web worker, so a CLI-started run is
    resumable too.
    """
    seed = "# Draft\n\nBody."
    final = run(config, question="Does it hold?", seed=seed, client=make_client(identities))
    assert (Path(final["run_dir"]) / "seed.md").read_text() == seed


# ------------------------------------ citation census (D-writer-citation-continuity)


def _generate_events(cfg, final):
    return [e for e in _events(cfg, final) if e["kind"] == "generate"]


def test_every_generate_event_carries_the_citation_census(identities, tmp_path, roster):
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    client.report_fn = lambda _n: REPORT
    final = run(cfg, question="Is it so?", client=client)

    generations = _generate_events(cfg, final)
    assert len(generations) >= 2
    first, revision = generations[0], generations[1]
    for event in (first, revision):
        assert event["source_entries"] == 1
        assert event["body_markers"] == 1
        assert event["cited_entries"] == 1
        assert event["dangling_markers"] == 0
    # The comparison needs a draft to compare with.
    assert "cited_sources_dropped" not in first
    assert revision["cited_sources_dropped"] == 0
    assert revision["cited_sources_added"] == 0
    assert revision["entries_removed"] == 0


def test_a_writer_that_strips_every_marker_is_visible_and_not_rejected(
    identities, tmp_path, roster, caplog
):
    """Warn-only: the draft ships to review as written, and the event says what happened."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
    )
    client = make_client(identities, critique_fn=always_material)
    client.report_fn = lambda _n: REPORT.replace(" [1]", "")
    with caplog.at_level("WARNING"):
        final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    revision = _generate_events(cfg, final)[0]
    assert revision["body_markers"] == 0
    assert revision["source_entries"] == 1
    assert revision["cited_sources_dropped"] == 1
    assert "its body carries no [n] marker" in caplog.text


def test_a_polish_pass_that_drops_a_cited_source_is_logged_not_rejected(caplog):
    """The polish goal says "remove no citation". A whole-document polish that drops one
    anyway is the motivating failure shape of D-writer-citation-continuity: it is counted
    and warned about, and nothing rejects the draft."""
    from reasonable_answer.graph import _citation_fields

    with caplog.at_level("WARNING"):
        fields = _citation_fields(REPORT, REPORT.replace(" [1]", ""), polish=True)

    assert fields["cited_sources_dropped"] == 1
    assert "polish pass dropped 1 cited source" in caplog.text


def test_a_revision_that_drops_a_cited_source_is_not_warned_about_as_a_polish(caplog):
    """Dropping a citation is a legitimate revision when a task removes the claim; only a
    polish pass was told to keep every citation."""
    from reasonable_answer.graph import _citation_fields

    with caplog.at_level("WARNING"):
        fields = _citation_fields(REPORT, REPORT.replace(" [1]", ""), polish=False)

    assert fields["cited_sources_dropped"] == 1
    assert "polish pass dropped" not in caplog.text


# --------------------------------------------------- census-gated repair (D-census-gated-repair)


def _multi_paragraph_report(n: int) -> str:
    """`n` blank-line-separated paragraphs in one unheaded section (section 0), each
    long enough that `report.only_added_words`/`restates` never mistake one for another."""
    return "\n\n".join(f"Paragraph number {i} says something specific to itself." for i in range(1, n + 1))


def _direct_generate(tmp_path, cfg, client, identities, state, run_id="run-repair"):
    """Drive `_generate` directly, the way `test_rule_2_retries_...` drives `_critique` —
    full control over `state` without paying for a whole graph run."""
    from reasonable_answer.graph import Runtime, _generate
    from reasonable_answer.store import RunStore

    rt = Runtime(config=cfg, client=client, identities=identities, store=RunStore(tmp_path, run_id))
    out = _generate(state, rt)
    events = [
        json.loads(line)
        for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    generate_event = next(e for e in events if e["kind"] == "generate")
    return out, generate_event


@pytest.mark.parametrize(
    ("field", "value"),
    [("repair_cap", 0), ("repair_cap", 6), ("max_out_of_scope", 0), ("max_out_of_scope", 101)],
)
def test_repair_config_bounds(field, value):
    with pytest.raises(ValidationError):
        RepairConfig(**{field: value})


def test_a_markerless_revision_triggers_one_repair_and_ships_the_repaired_draft(
    identities, tmp_path, roster, caplog
):
    """Gate 1 (D-census-gated-repair): a revision that drops every [n] marker gets one
    extra call to the writer that dropped it, and the repaired draft is what ships."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True)),
    )
    markerless = REPORT.replace(" [1]", "")
    drafts = [markerless, REPORT]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    with caplog.at_level("WARNING"):
        out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == REPORT.strip()
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2, "the draft, plus exactly one repair call"
    assert "REPAIR REQUIRED" in writer_calls[1].user
    assert writer_calls[1].alias == writer_calls[0].alias, "the same writer repairs its own draft"
    # A patch-mode revision carries the patch licence, so its repair names the block to
    # restore from (D-scoped-revision's byte-identical rule, applied to the right draft).
    assert "byte-for-byte from PREVIOUS DRAFT" in writer_calls[1].user
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    assert event["repair_resolved"] == 1
    # Post-repair census, not the failed draft's — existing A/B readers see what shipped.
    assert event["body_markers"] == 1
    assert event["source_entries"] == 1
    assert caplog.text.count("its body carries no [n] marker") == 1, (
        "the post-repair census must not repeat the generation's warning"
    )


def test_an_out_of_scope_revision_over_threshold_triggers_repair(identities, tmp_path, roster):
    """Gate 2: a patch-mode revision that rewrites far more than its fix tasks named
    gets one repair call, and a fully restored draft resolves it."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(
            mode="patch", repair=RepairConfig(enabled=True, max_out_of_scope=6)
        ),
    )
    previous = _multi_paragraph_report(9)
    # Rewrites paragraphs 2-8 (7 of them) that no task named — one over the threshold.
    over_threshold = "\n\n".join(
        f"Paragraph number {i} was rewritten wholesale for no stated reason."
        if 2 <= i <= 8
        else f"Paragraph number {i} says something specific to itself."
        for i in range(1, 10)
    )
    drafts = [over_threshold, previous]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": previous,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [uncited(section=0, paragraph=1).model_dump(mode="json")],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == previous
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "out_of_scope"
    assert event["repair_resolved"] == 1
    assert event["out_of_scope"] == 0


def test_markerless_and_out_of_scope_revision_records_both(identities, tmp_path, roster):
    """Both mechanical gates feed the closed `repair_reason` enum and prompt path."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(
            mode="patch", repair=RepairConfig(enabled=True, max_out_of_scope=6)
        ),
    )
    source_line = "\n\n## Sources\n\n[1] A real-looking source."
    previous = _multi_paragraph_report(9) + source_line
    failing = "\n\n".join(
        f"Paragraph number {i} was rewritten wholesale for no stated reason."
        if 2 <= i <= 8
        else f"Paragraph number {i} says something specific to itself."
        for i in range(1, 10)
    ) + source_line
    client = FakeClient(
        identities=identities,
        critique_fn=clean,
        report_fn=lambda n: [failing, REPORT][n - 1],
    )
    state = {
        "question": "Is it so?",
        "report": previous,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [uncited(section=0, paragraph=1).model_dump(mode="json")],
    }

    _, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert event["repair_reason"] == "both"
    repair_call = [call for call in client.calls if call.schema is None][1]
    assert "body carries 0 [n] marker(s)" in repair_call.user
    assert "changed 7 paragraph(s)" in repair_call.user


def test_out_of_scope_at_the_threshold_does_not_trigger_repair(identities, tmp_path, roster):
    """`out_of_scope > max_out_of_scope`, not `>=` — six rewritten paragraphs is the
    documented default ceiling, not yet an over-threshold draft."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(
            mode="patch", repair=RepairConfig(enabled=True, max_out_of_scope=6)
        ),
    )
    previous = _multi_paragraph_report(9)
    # Exactly six paragraphs (2-7) rewritten with no task naming them.
    at_threshold = "\n\n".join(
        f"Paragraph number {i} was rewritten wholesale for no stated reason."
        if 2 <= i <= 7
        else f"Paragraph number {i} says something specific to itself."
        for i in range(1, 10)
    )
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: at_threshold)
    state = {
        "question": "Is it so?",
        "report": previous,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [uncited(section=0, paragraph=1).model_dump(mode="json")],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == at_threshold
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 1, "at the ceiling, no repair is spent"
    assert "repair_attempted" not in event
    assert event["out_of_scope"] == 6


def test_repair_disabled_restores_warn_only_behaviour_exactly(identities, tmp_path, roster):
    """`revision.repair.enabled: false` must mean zero extra writer calls, even when
    both gates would otherwise fire."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(
            mode="patch", repair=RepairConfig(enabled=False, max_out_of_scope=6)
        ),
    )
    previous = _multi_paragraph_report(9)
    markerless_and_over_scope = "\n\n".join(
        f"Paragraph number {i} was rewritten wholesale for no stated reason."
        if 2 <= i <= 8
        else f"Paragraph number {i} says something specific to itself."
        for i in range(1, 10)
    )
    client = FakeClient(
        identities=identities, critique_fn=clean, report_fn=lambda _n: markerless_and_over_scope
    )
    state = {
        "question": "Is it so?",
        "report": previous,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [uncited(section=0, paragraph=1).model_dump(mode="json")],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == markerless_and_over_scope
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 1
    assert "repair_attempted" not in event
    assert event["out_of_scope"] == 7


def test_a_failed_repair_call_keeps_the_original_draft(identities, tmp_path, roster):
    """The repair call itself failing must not abort the run or lose the draft
    (D-census-gated-repair): the unrepaired text ships and the event says the repair
    was tried and did not resolve anything."""
    markerless = REPORT.replace(" [1]", "")

    def flaky(n: int) -> str:
        if n == 1:
            return markerless
        raise ModelCallError("provider exploded mid-repair")

    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True)),
    )
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=flaky)
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == markerless.strip(), "the repair failed, so the original draft ships"
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    assert event["repair_resolved"] == 0
    assert event["body_markers"] == 0


@pytest.mark.parametrize(
    ("overrides", "mode"),
    [
        pytest.param({}, "patch", id="first_draft"),
        pytest.param({"polish_next": True, "report": REPORT}, "patch", id="polish"),
        pytest.param({"full_rewrite_next": True, "report": REPORT}, "patch", id="rewrite"),
        pytest.param({"report": REPORT}, "rewrite", id="global_rewrite_mode"),
        # The same three whole-document generations under an ops deployment: the repair
        # turn must ask for the whole report, never for operations (D-ops-revision).
        pytest.param({}, "ops", id="first_draft_ops"),
        pytest.param({"polish_next": True, "report": REPORT}, "ops", id="polish_ops"),
        pytest.param({"full_rewrite_next": True, "report": REPORT}, "ops", id="rewrite_ops"),
    ],
)
def test_gate_2_is_exempt_where_scope_measurement_is_silent_but_gate_1_is_not(
    identities, tmp_path, roster, overrides, mode
):
    """`_scope_fields` stays silent for the first draft, a rule-9 polish pass and a
    rule-13 rewrite (D-scoped-revision), so gate 2 can never fire there — but gate 1
    (marker-less body) applies to every draft including these three, and to a
    `revision.mode: rewrite` deployment. None of these four generations was held to the
    patch close, so none of their repair turns may ask for byte-for-byte restoration
    (D-census-gated-repair inherits the licence, never widens it)."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(mode=mode, repair=RepairConfig(enabled=True)),
    )
    source_line = "\n\n## Sources\n\n[1] A real-looking source."
    markerless = "# Answer\n\nAn unmarked claim entirely." + source_line
    repaired = "# Answer\n\nA marked claim [1]." + source_line
    drafts = [markerless, repaired]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
        **overrides,
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == repaired
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    if mode == "patch":
        assert "out_of_scope" not in event
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    assert "byte-for-byte" not in writer_calls[1].user, (
        "a generation asked for the whole document must not be told to restore paragraphs"
    )
    assert "Return operations" not in writer_calls[1].user
    assert "@@ replace" not in writer_calls[1].user
    assert "Return the complete report" in writer_calls[1].user


def test_repair_cap_bounds_the_number_of_extra_writer_calls(identities, tmp_path, roster):
    """A writer that keeps failing the same gate must not be re-asked forever
    (D-census-gated-repair): `repair_cap` bounds the extra calls, and the draft ships
    unresolved once it is spent."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True, repair_cap=2)),
    )
    markerless = REPORT.replace(" [1]", "")
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: markerless)
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == markerless.strip()
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 3, "the draft plus exactly repair_cap (2) repair calls, never more"
    assert event["repair_attempted"] == 1
    assert event["repair_resolved"] == 0


def test_repair_continues_after_an_unresolved_attempt_within_the_cap(
    identities, tmp_path, roster
):
    """The bounded loop re-measures each attempt and may resolve on a later call."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True, repair_cap=2)),
    )
    markerless = REPORT.replace(" [1]", "")
    drafts = [markerless, markerless, REPORT]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == REPORT.strip()
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 3, "the draft plus two repair attempts"
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    assert event["repair_resolved"] == 1


def test_gate_2_stays_silent_under_a_global_rewrite_mode(identities, tmp_path, roster):
    """`revision.mode: rewrite` as the deployment's standing configuration is a different
    path from a rule-13 `full_rewrite_next` tick: `_scope_fields` still measures
    `out_of_scope` there, so only `_repair_gates`'s own `mode == "patch"` check keeps gate 2
    from firing on every round of a deployment that asked for whole-document revisions
    (D-census-gated-repair)."""
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(
            mode="rewrite", repair=RepairConfig(enabled=True, max_out_of_scope=6)
        ),
    )
    previous = _multi_paragraph_report(9)
    over_threshold = "\n\n".join(
        f"Paragraph number {i} was rewritten wholesale for no stated reason."
        if 2 <= i <= 8
        else f"Paragraph number {i} says something specific to itself."
        for i in range(1, 10)
    )
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: over_threshold)
    state = {
        "question": "Is it so?",
        "report": previous,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [uncited(section=0, paragraph=1).model_dump(mode="json")],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == over_threshold
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 1, "under mode: rewrite a high out_of_scope is the mode working"
    assert "repair_attempted" not in event
    assert event["out_of_scope"] == 7, "the measurement itself is still recorded"


def test_the_repair_call_offers_no_tool_even_when_the_drafting_call_did(
    identities, tmp_path, roster
):
    """D-census-gated-repair withholds `web_search`/`read_source` from the repair turn.
    That is only testable with retrieval *on*: the drafting call must carry the tool and
    the repair call, to the same writer, must not."""
    from reasonable_answer.graph import Runtime, _generate
    from reasonable_answer.search import QueryBudget, SearchResult
    from reasonable_answer.store import RunStore

    class _Searcher:
        budget = QueryBudget(10)

        def search(self, query, count=None):
            return [SearchResult(title="T", url="https://example.org/x", description="D")]

    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True)),
    )
    markerless = REPORT.replace(" [1]", "")
    drafts = [markerless, REPORT]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    rt = Runtime(
        config=cfg,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-repair-tools"),
        searcher=_Searcher(),
    )
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out = _generate(state, rt)

    assert out["report"] == REPORT.strip()
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    assert writer_calls[0].tools == ["web_search"], "retrieval is on for the drafting call"
    assert "web_search" in writer_calls[0].system
    assert writer_calls[1].tools == [], "the repair turn is offered no tool"
    assert "web_search" not in writer_calls[1].system
    assert writer_calls[1].alias == writer_calls[0].alias


def test_an_empty_repair_completion_keeps_the_original_draft(identities, tmp_path, roster):
    """The second failure path of `_writer_repair`: a completion that is empty rather
    than an error. Same caller-visible outcome as a failed call — the unrepaired draft
    ships, the event says the repair was tried and did not resolve."""
    markerless = REPORT.replace(" [1]", "")
    drafts = [markerless, "   \n"]
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True)),
    )
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == markerless.strip()
    assert event["repair_attempted"] == 1
    assert event["repair_resolved"] == 0
    assert event["body_markers"] == 0


def test_a_repair_that_deletes_the_sources_section_is_not_resolved(identities, tmp_path, roster):
    """Gate 1 is `source_entries > 0`. A repair that empties `## Sources` makes the gate
    false without restoring a marker — the delete-to-discharge shape the gate exists to
    catch — so `repair_resolved` stays 0 even though neither gate fires afterwards."""
    markerless = REPORT.replace(" [1]", "")
    sourceless = markerless.split("## Sources")[0].rstrip() + "\n"
    assert "## Sources" not in sourceless
    drafts = [markerless, sourceless]
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(repair=RepairConfig(enabled=True)),
    )
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == sourceless.strip(), "the repaired draft still ships — the gate never rejects"
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    assert event["repair_resolved"] == 0
    assert event["source_entries"] == 0


# ------------------------------------------------- operations revision (D-ops-revision)


OPS_PREVIOUS = """## Conclusion

Water boils at 100 degrees Celsius at sea level [1].

## Body

The boiling point paragraph restates the claim about boiling [1].

The freezing point paragraph says water freezes at zero degrees [2].

## Sources

[1] Boiling source. https://example.org/boil

[2] Freezing source. https://example.org/freeze
"""

OPS_REPLY = """@@ replace S2.P2 tasks=T1
The freezing point paragraph now says water freezes at zero degrees Celsius [2].
@@ end
"""

OPS_SPLICED = OPS_PREVIOUS.strip().replace(
    "The freezing point paragraph says water freezes at zero degrees [2].",
    "The freezing point paragraph now says water freezes at zero degrees Celsius [2].",
)


def _ops_cfg(roster, tmp_path, **revision):
    return Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(mode="ops", **revision),
    )


def _ops_state(identities, defects=None, **overrides):
    return {
        "question": "Is it so?",
        "report": OPS_PREVIOUS,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": (
            defects if defects is not None else [uncited(section=2, paragraph=2).model_dump(mode="json")]
        ),
        **overrides,
    }


def test_an_ops_reply_is_spliced_and_the_event_carries_the_counts(identities, tmp_path, roster):
    """The artifact is the splice, never the reply: hash, store and scope fields all
    describe the spliced Markdown, and every `ops_*` count rides the event as an int."""
    from reasonable_answer import ops as ops_mod
    from reasonable_answer.report import artifact_hash

    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: OPS_REPLY)
    cfg = _ops_cfg(roster, tmp_path)
    out, event = _direct_generate(tmp_path, cfg, client, identities, _ops_state(identities))

    assert out["report"] == OPS_SPLICED
    assert out["artifact_hash"] == artifact_hash(OPS_SPLICED)
    assert event["artifact_hash"] == artifact_hash(OPS_SPLICED)
    assert event["revision_mode"] == "ops"
    for key in (*ops_mod.PARSE_FIELDS, *ops_mod.SPLICE_FIELDS):
        assert isinstance(event[key], int), key
    assert event["ops_total"] == 1
    assert event["ops_applied"] == 1
    # Scope is measured on the spliced text against the previous artifact, as under patch.
    assert event["changed_paragraphs"] == 1
    assert event["in_scope"] == 1
    assert event["out_of_scope"] == 0
    assert event["body_markers"] == 3
    assert event["dangling_markers"] == 0
    # The writer saw the labelled draft and the numbered tasks, and was asked for operations.
    writer_call = [c for c in client.calls if c.schema is None][0]
    assert "[S2.P2]" in writer_call.user
    assert '"task_id": "T1"' in writer_call.user
    assert "OUTPUT FORMAT — OPERATIONS ONLY" in writer_call.user


def test_critics_see_plain_markdown_never_the_operations(identities, config):
    """End to end on the graph: a critic flags the seed, the writer answers with
    operations, the critics then read the spliced Markdown — never a protocol line —
    and the run accepts the spliced report."""
    cfg = config.model_copy(update={"revision": RevisionConfig(mode="ops")})
    span = "The freezing point paragraph says water freezes at zero degrees"

    def critique(_alias, user):
        # `uncited_claim` is the evidence lens's category; the others fail closed on it.
        if span not in user or lens_of(user) != "evidence":
            return CritiqueOutput(issues=[])
        issue = RawIssue(
            category=Category.UNCITED_CLAIM,
            severity=Severity.MAJOR,
            locus=StructuralRef(section=2, paragraph=2),
            claim_span=span,
            rationale="the claim is unsupported as stated",
            instruction="restate the claim precisely",
        )
        return CritiqueOutput(issues=[issue])

    client = FakeClient(identities=identities, critique_fn=critique, report_fn=lambda _n: "")
    # A revision under ops is answered with operations; any whole-document call (there
    # should be none on this run, but a polish pass would be one) gets the report.
    client.report_fn = lambda _n: (
        OPS_REPLY if "OPERATIONS ONLY" in client.calls[-1].user else OPS_SPLICED
    )
    final = run(cfg, question="Is it so?", seed=OPS_PREVIOUS, client=client)

    assert not final["fatal"]
    assert final["terminal_status"] == "accepted"
    assert final["report"] == OPS_SPLICED
    generates = [e for e in events_of(final) if e["kind"] == "generate"]
    assert generates and all(e["revision_mode"] == "ops" for e in generates)
    assert generates[0]["ops_applied"] == 1
    critic_calls = [c for c in client.calls if c.schema == "CritiqueOutput"]
    assert critic_calls
    for call in critic_calls:
        assert "@@ " not in call.user
        assert "OPERATIONS ONLY" not in call.user


def test_a_malformed_ops_reply_is_its_own_failure_class_and_the_next_writer_authors(
    identities, tmp_path, roster
):
    """No operation parsed: there is no draft to ship or repair, so the attempt fails the
    way an empty reply does and the next pool member gets the call (D-ops-revision)."""
    replies = ["I revised the report as you asked.", OPS_REPLY]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: replies[n - 1])
    cfg = _ops_cfg(roster, tmp_path)
    # A seeded previous draft excludes no writer, so both pool members are eligible and
    # the retry is observably a different one.
    state = _ops_state(identities, author_identity="external/seed")
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == OPS_SPLICED
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    assert writer_calls[0].alias != writer_calls[1].alias, "the retry goes to the next pool member"
    assert out["author_identity"] == identities[writer_calls[1].alias]
    events = [
        json.loads(line) for line in (tmp_path / "run-repair" / "events.jsonl").read_text().splitlines()
    ]
    failures = [e for e in events if e["kind"] == "generate_failed"]
    assert [e["failure_class"] for e in failures] == ["malformed_ops"]
    assert "no applicable operations" in failures[0]["reason"]
    assert not any(k.startswith("ops_") for k in failures[0]), "generate_failed gains no field"


def test_an_all_refused_ops_reply_is_malformed_too(identities, tmp_path, roster):
    replies = ["@@ replace S9.P9 tasks=T1\nnowhere\n@@ end", OPS_REPLY]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: replies[n - 1])
    cfg = _ops_cfg(roster, tmp_path)
    out, _event = _direct_generate(tmp_path, cfg, client, identities, _ops_state(identities))
    assert out["report"] == OPS_SPLICED
    events = [
        json.loads(line) for line in (tmp_path / "run-repair" / "events.jsonl").read_text().splitlines()
    ]
    assert [e["failure_class"] for e in events if e["kind"] == "generate_failed"] == ["malformed_ops"]


def test_every_writer_malformed_is_fatal(identities, tmp_path, roster):
    from reasonable_answer.graph import Runtime, _generate
    from reasonable_answer.store import RunStore

    cfg = _ops_cfg(roster, tmp_path)
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: "no operations here")
    rt = Runtime(config=cfg, client=client, identities=identities, store=RunStore(tmp_path, "run-fatal"))
    out = _generate(_ops_state(identities), rt)
    assert out["fatal"]
    assert "every eligible writer failed" in out["fatal_reason"]
    assert "no applicable operations" in out["fatal_reason"]


def test_a_first_draft_a_polish_pass_and_a_rule_13_rewrite_are_whole_documents_under_ops(
    identities, tmp_path, roster
):
    """The mode narrows a *revision's* output and nothing else (D-ops-revision)."""
    cfg = _ops_cfg(roster, tmp_path)

    # First draft: the first-draft prompt, and the reply is the report.
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: REPORT)
    out, event = _direct_generate(
        tmp_path,
        cfg,
        client,
        identities,
        {"question": "Is it so?", "run_date": "2026-09-20", "defects": []},
        "run-first",
    )
    assert out["report"] == REPORT.strip()
    assert event["revision_mode"] == "ops"
    assert "ops_total" not in event
    assert "Write a report that answers the question below" in client.calls[0].user

    # Polish: the whole-document close, and the reply is the report.
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: REPORT)
    out, event = _direct_generate(
        tmp_path, cfg, client, identities, _ops_state(identities, defects=[], polish_next=True), "run-polish"
    )
    assert out["report"] == REPORT.strip()
    assert "ops_total" not in event
    assert client.calls[0].user.endswith(prompts.WRITER_REWRITE_CLOSE)
    assert "[S2.P2]" not in client.calls[0].user

    # Rule 13: the rewrite close, no splice, and the event says `rewrite`.
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: REPORT)
    out, event = _direct_generate(
        tmp_path, cfg, client, identities, _ops_state(identities, full_rewrite_next=True), "run-rewrite"
    )
    assert out["report"] == REPORT.strip()
    assert event["revision_mode"] == "rewrite"
    assert event["full_rewrite"] is True
    assert "ops_total" not in event
    assert client.calls[0].user.endswith(prompts.WRITER_REWRITE_CLOSE)


def test_deleting_a_still_cited_source_is_refused_end_to_end(identities, tmp_path, roster):
    reply = OPS_REPLY + "\n@@ delete S3.P1 tasks=T1\n@@ end\n"
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: reply)
    cfg = _ops_cfg(roster, tmp_path)
    out, event = _direct_generate(tmp_path, cfg, client, identities, _ops_state(identities))
    assert out["report"] == OPS_SPLICED
    assert event["ops_total"] == 2
    assert event["ops_applied"] == 1
    assert event["ops_refused_dangling"] == 1
    assert event["dangling_markers"] == 0
    assert event["source_entries"] == 2


def test_gate_2_fires_under_ops_and_the_repair_turn_returns_operations(identities, tmp_path, roster):
    """The splice cannot stop a writer operating on many unnamed paragraphs, so gate 2
    keeps its job under ops; its repair turn asks for operations on the labelled
    previous draft and the repaired draft is again a splice of `previous`."""
    previous = _multi_paragraph_report(9)
    over = "".join(
        f"@@ replace S0.P{i} tasks=T1\n"
        f"Paragraph number {i} was rewritten wholesale for no stated reason.\n@@ end\n"
        for i in range(2, 9)
    )
    # The repair reverts every one of them — operations again, not a document.
    revert = "".join(
        f"@@ replace S0.P{i} tasks=T1\nParagraph number {i} says something specific to itself.\n@@ end\n"
        for i in range(2, 9)
    )
    replies = [over, revert]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: replies[n - 1])
    cfg = _ops_cfg(roster, tmp_path, repair=RepairConfig(enabled=True, max_out_of_scope=6))
    defects = [uncited(section=0, paragraph=1).model_dump(mode="json")]
    state = _ops_state(identities, report=previous, defects=defects)
    out, event = _direct_generate(tmp_path, cfg, client, identities, state)

    assert out["report"] == previous
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    repair = writer_calls[1]
    assert repair.alias == writer_calls[0].alias
    assert "REPAIR REQUIRED" in repair.user
    assert "changed 7 paragraph(s)" in repair.user
    assert "Return operations on the labelled PREVIOUS DRAFT below" in repair.user
    # A fresh context: the block format has to travel with the repair prompt.
    assert prompts.WRITER_OPS_FORMAT in repair.user
    assert "[S0.P2] Paragraph number 2 says something specific to itself." in repair.user
    assert "byte-for-byte" not in repair.user
    assert "Return the complete report" not in repair.user
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "out_of_scope"
    assert event["repair_resolved"] == 1
    assert event["out_of_scope"] == 0
    # `ops_*` describes the drafting reply; the repair splice that actually built the
    # shipped text reports under `repair_ops_*`, so neither stands in for the other.
    assert event["ops_total"] == 7
    assert event["ops_applied"] == 7
    assert event["repair_ops_total"] == 7
    assert event["repair_ops_applied"] == 7
    assert event["repair_ops_refused_locus"] == 0


def test_a_malformed_ops_repair_reply_keeps_the_draft(identities, tmp_path, roster):
    """Gate 1 under ops with a repair reply that carries no operation: an unresolved
    attempt, the current draft ships, the run continues."""
    previous = OPS_PREVIOUS
    # Drops every marker from the body (three replaces), which trips gate 1.
    markerless = (
        "@@ replace S1.P1 tasks=T1\nWater boils at 100 degrees Celsius at sea level.\n@@ end\n"
        "@@ replace S2.P1 tasks=T1\nThe boiling point paragraph restates the claim.\n@@ end\n"
        "@@ replace S2.P2 tasks=T1\nThe freezing point paragraph says water freezes.\n@@ end\n"
    )
    replies = [markerless, "Sorry, here is the whole report instead.\n\n## Conclusion\n\nText [1]."]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: replies[n - 1])
    cfg = _ops_cfg(roster, tmp_path, repair=RepairConfig(enabled=True))
    out, event = _direct_generate(tmp_path, cfg, client, identities, _ops_state(identities, report=previous))

    assert "[1]" not in out["report"].split("## Sources")[0]
    assert "## Sources" in out["report"], "a prose reply to an ops repair is not spliced in"
    assert event["repair_attempted"] == 1
    assert event["repair_reason"] == "markerless"
    assert event["repair_resolved"] == 0
    assert event["body_markers"] == 0
    assert len([c for c in client.calls if c.schema is None]) == 2
    # Nothing was spliced by the repair, so there are no repair splice counts to report.
    assert not any(k.startswith("repair_ops_") for k in event)


def test_a_plain_patch_generation_records_its_mode(identities, tmp_path, roster):
    cfg = Config(roster=roster, budgets=Budgets(min_ticks=2, hard_cap=4), runs_dir=tmp_path / "runs")
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda _n: REPORT)
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    _, event = _direct_generate(tmp_path, cfg, client, identities, state)
    assert event["revision_mode"] == "patch"
    assert not any(k.startswith("ops_") for k in event)


def test_the_startup_event_records_the_revision_block(identities, config):
    """Production mounts its own roster, so the commit cannot say which mode or which
    repair settings a run had; the `startup` event does (D-ops-revision)."""
    cfg = config.model_copy(
        update={"revision": RevisionConfig(mode="ops", repair=RepairConfig(enabled=True, max_out_of_scope=4))}
    )
    final = run(cfg, question="Is it so?", seed=REPORT, client=make_client(identities))
    startup = next(e for e in events_of(final) if e["kind"] == "startup")
    assert startup["revision"] == {
        "mode": "ops",
        "scope_check": "warn",
        "repair_enabled": True,
        "repair_cap": 1,
        "max_out_of_scope": 4,
    }
    default = run(config, question="Is it so?", seed=REPORT, client=make_client(identities))
    assert next(e for e in events_of(default) if e["kind"] == "startup")["revision"]["mode"] == "patch"


def test_a_patch_mode_repair_carries_no_repair_ops_counts(identities, tmp_path, roster):
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(mode="patch", repair=RepairConfig(enabled=True)),
    )
    drafts = [REPORT.replace(" [1]", ""), REPORT]
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: drafts[n - 1])
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "author_identity": identities["writer-a"],
        "run_date": "2026-09-20",
        "defects": [],
    }
    _, event = _direct_generate(tmp_path, cfg, client, identities, state)
    assert event["repair_attempted"] == 1
    assert not any(k.startswith("ops_") or k.startswith("repair_ops_") for k in event)
