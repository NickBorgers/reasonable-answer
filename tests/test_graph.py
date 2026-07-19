"""End-to-end loop behaviour, driven by a scripted fake proxy (no network)."""

from __future__ import annotations

import json

import pytest

from fakes import FakeClient
from reasonable_answer.config import Budgets, Config, ConfigError
from reasonable_answer.graph import run
from reasonable_answer.schemas import CritiqueOutput, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Severity

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

    client = make_client(identities, critique_fn=lambda a, u: blocking(a, u) if lens_of(u) == "evidence" else CritiqueOutput(issues=[]))
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
    assert all(a != b for a, b in zip(writers, writers[1:])), writers


def test_every_critique_call_excludes_the_author(identities, config):
    client = make_client(identities, critique_fn=always_material)
    run(config, question="Is it so?", seed=REPORT, client=client)

    author = None
    for call in client.calls:
        if call.schema is None:  # a generation
            author = identities[call.alias]
        elif call.schema == "CritiqueOutput" and author is not None:
            assert identities[call.alias] != author


def test_intake_rejects_a_seed_without_a_question(identities, config):
    client = make_client(identities)
    with pytest.raises(ConfigError, match="question is required"):
        run(config, question="   ", seed=REPORT, client=client)


def test_intake_rejects_an_oversized_seed(identities, config):
    client = make_client(identities)
    with pytest.raises(ConfigError, match="seed exceeds"):
        run(config, question="q?", seed="x" * (config.max_report_chars + 1), client=client)


def test_the_audit_trail_records_every_stage(identities, config):
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    events = [
        json.loads(line)
        for line in (client_run_dir(final) / "events.jsonl").read_text().splitlines()
    ]
    kinds = {e["kind"] for e in events}
    assert {"startup", "intake", "critique", "triage", "orchestrate", "control", "finalize"} <= kinds


def test_run_directory_is_private(identities, config):
    client = make_client(identities)
    final = run(config, question="Is it so?", seed=REPORT, client=client)
    mode = client_run_dir(final).stat().st_mode & 0o777
    assert mode == 0o700
