"""End-to-end loop behaviour, driven by a scripted fake proxy (no network)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fakes import FakeClient

from reasonable_answer.config import Budgets, Config, ConfigError
from reasonable_answer.graph import run
from reasonable_answer.schemas import CritiqueOutput, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""


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
