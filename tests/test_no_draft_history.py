"""The report never talks about earlier drafts of itself (D-no-draft-history)."""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient

from reasonable_answer import prompts
from reasonable_answer import report as report_mod
from reasonable_answer.config import Budgets, Config, RepairConfig, RevisionConfig
from reasonable_answer.controller import select_shipped_index
from reasonable_answer.graph import Runtime, _critique_one, _generate
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity, clamp_to_floor
from reasonable_answer.triage import (
    draft_history_sentences,
    mechanical_draft_history_issues,
    validate_issue,
)

# Sentences shaped like production run run-62eba4c35567, whose shipped report narrated its
# own revision history in 40 sentences.
HISTORY = [
    "The two URLs this revision was able to open support only the party's self-presentation [1].",
    "The previously cited page is an error page and supports none of the earlier draft's claims [1].",
    "That figure could not be verified and is removed as unsupported.",
    "**Nevada 2024 Measure — Removed:** No source supports the assertion.",
    "**Candidate Runs — Claim Removed:** No source lists those candidates.",
    "The previous draft stated that the party was founded in 2021.",
    "No source read for the current revision confirms the founders.",
]
LOOKALIKES = [
    "The earlier draft of the bill required disclosure.",
    "The first revision of ISO 9001 appeared in 1994.",
    "Tariffs on steel were removed in 2019.",
    "The previously cited study [1] agrees.",
    "The original version of the treaty was signed in 1957.",
]


def sentences(text: str, question: str = "") -> list[tuple[int, int, str]]:
    return draft_history_sentences(text, report_mod.parse(text), question)


def mint(text: str, question: str = ""):
    return mechanical_draft_history_issues(text, report_mod.parse(text), question)


def report(*paragraphs: str) -> str:
    body = "\n\n".join(paragraphs)
    return f"## Findings\n\n{body}\n\n## Sources\n\n[1] A source. https://example.org/a\n"


@pytest.mark.parametrize("sentence", HISTORY)
def test_a_sentence_about_an_earlier_draft_is_found(sentence):
    assert len(sentences(report(sentence))) == 1


@pytest.mark.parametrize("sentence", LOOKALIKES)
def test_a_sentence_about_some_other_document_is_not(sentence):
    assert sentences(report(sentence)) == []


def test_a_draft_of_this_report_is_still_this_report():
    assert len(sentences(report("An earlier draft of this report overstated the count."))) == 1


def test_a_question_that_asks_about_drafts_may_say_draft():
    text = report("The earlier draft required registration of every model.")
    assert sentences(text, "How did the draft EU AI Act change?") == []
    assert len(sentences(text, "What does the EU AI Act require?")) == 1


def test_the_sources_section_is_not_read():
    text = "## Findings\n\nWater boils at 100 C [1].\n\n## Sources\n\n[1] Previous draft guidance. https://x.org\n"
    assert sentences(text) == []


def test_one_blocking_unclear_structure_finding_per_paragraph():
    text = report(" ".join(HISTORY[:3]), HISTORY[5], "A clean paragraph about the party [1].")
    issues = mint(text)
    assert len(issues) == 2
    for issue in issues:
        assert issue.category is Category.UNCLEAR_STRUCTURE
        assert issue.severity is Severity.BLOCKING
        assert clamp_to_floor(issue.category, issue.severity) is Severity.BLOCKING
        validate_issue(Lens.COMPLETENESS, issue, report_mod.parse(text))
    assert issues[0].claim_span == HISTORY[0]


def test_the_finding_names_the_way_out_of_a_false_positive():
    [issue] = mint(report(HISTORY[5]))
    assert "name it" in issue.instruction and "Delete a withdrawn claim" in issue.instruction


def test_latest_unblocked_ships_the_draft_without_history():
    # Round 1 is free of it; round 2 narrates its revision. Before this decision both scored
    # zero blocking and the later, narrating draft shipped.
    scores = [(0, 3, 0), (len(mint(report(HISTORY[0]))), 1, 0)]
    assert select_shipped_index(scores, "latest_unblocked") == 0


# -- prompts ------------------------------------------------------------------------------


def test_every_writer_is_told_the_report_has_no_history():
    for system in (prompts.writer_system(False), prompts.writer_system(True, True)):
        assert "only document your reader ever receives" in system


def test_a_revision_is_told_that_removing_means_deleting():
    text = prompts.writer_revision("q", report("A claim [1]."), [], polish=False, mode="ops")
    assert "never says that a claim was removed" in text


def _repair(**kw):
    args = dict(
        markerless=False, scope_gate=False, source_entries=1, body_markers=1,
        cited_sources_dropped=0, out_of_scope=0,
    )
    return prompts.writer_repair_turn("q", "draft", None, [], **{**args, **kw})


def test_the_repair_turn_carries_a_count_and_never_the_sentences():
    text = _repair(draft_history=3)
    assert "3 sentence(s) of your draft talk about an earlier draft" in text
    assert _repair(draft_history=0) == _repair()


# -- wiring ---------------------------------------------------------------------------------

NARRATED = report("The earlier draft claimed growth; that claim is removed as unsupported [1].")
CLEAN = report("The party reports growth on its own site [1].")


def _rt(tmp_path, identities, config, report_fn=lambda n: CLEAN, **review):
    cfg = config.model_copy(update={"review": config.review.model_copy(update=review)})
    client = FakeClient(
        identities=identities, critique_fn=lambda a, u: CritiqueOutput(issues=[]), report_fn=report_fn
    )
    return Runtime(config=cfg, client=client, identities=identities, store=RunStore(tmp_path, "run-hist"))


def _review(rt, lens, text=NARRATED):
    alias = f"{lens.value}-spec"
    return _critique_one(rt, lens, alias, "q?", text, "h" * 64, "vendor-a/model-a", 1)


def test_the_completeness_lens_mints_it_while_in_force(tmp_path, identities, config):
    rt = _rt(tmp_path, identities, config)
    [issue] = _review(rt, Lens.COMPLETENESS).issues
    assert issue.severity is Severity.BLOCKING
    assert _review(rt, Lens.COMPLETENESS, CLEAN).issues == []
    assert _review(rt, Lens.LOGIC).issues == []
    off = _rt(tmp_path, identities, config, draft_history_check=False)
    assert _review(off, Lens.COMPLETENESS).issues == []


def test_a_narrating_draft_gets_one_repair_turn_before_any_critic(tmp_path, identities, roster):
    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=4),
        runs_dir=tmp_path / "runs",
        revision=RevisionConfig(mode="rewrite", repair=RepairConfig(enabled=True)),
    )
    drafts = [NARRATED, CLEAN]
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: drafts[n - 1],
    )
    rt = Runtime(config=cfg, client=client, identities=identities, store=RunStore(tmp_path, "run-rep"))
    state = {
        "question": "Is it so?",
        "report": CLEAN,
        "author_identity": identities["writer-a"],
        "run_date": "2026-10-29",
        "defects": [],
    }
    out = _generate(state, rt)

    assert out["report"] == CLEAN.strip()
    writer_calls = [c for c in client.calls if c.schema is None]
    assert len(writer_calls) == 2
    assert "talk about an earlier draft" in writer_calls[1].user
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    event = next(e for e in map(json.loads, lines) if e["kind"] == "generate")
    assert event["repair_reason"] == "draft_history"
    assert event["repair_draft_history"] == 1
    assert event["repair_resolved"] == 1
    assert event["draft_history_refs"] == 0
