"""A claim that the evidence is missing must rest on a search for it (D-absence-claim-check)."""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient

from reasonable_answer import report as report_mod
from reasonable_answer import triage
from reasonable_answer.graph import Runtime, _critique, _generate
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.search import QueryBudget, SearchError, SearchResult
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Severity

ABSENT = (
    "No source reviewed here measures neighbourhood-level ownership by income."
)
REPORT = (
    "## Findings\n\nPoorer households are less likely to own their homes [1]. "
    f"{ABSENT}\n\n## Sources\n\n[1] Paper. https://example.org/a\n"
)
ON_TOPIC = "neighbourhood ownership by income census tracts"
OFF_TOPIC = "homeownership rate trends"


def _sentences(text):
    return [s for *_, s in triage.absence_claim_sentences(report_mod.parse(text))]


def _body(sentence):
    return f"## Findings\n\n{sentence}\n"


# --------------------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "sentence",
    [
        ABSENT,
        "There is no evidence that rent control lowers ownership.",
        "No published study has examined this population.",
        "None of the sources reports tract-level figures.",
        "This question has not been studied directly.",
        "The effect has never yet been systematically measured.",
        "Investor ownership remains largely unmeasured.",
        "The literature is silent on this point.",
        "Data are lacking for rural counties.",
        "This is a gap in the literature.",
        "There is a lack of reliable data on informal tenure.",
        "No data exist for the period before 1990.",
    ],
)
def test_absence_claims_are_detected(sentence):
    assert _sentences(_body(sentence)) == [sentence]


@pytest.mark.parametrize(
    "sentence",
    [
        "No fewer than three studies report the gap [1].",
        "It is by no means the only study of the question [2].",
        "The bureau no longer reports the series annually [1].",
        "Ownership fell by four points between 2000 and 2020 [1].",
        "No one disputes that incomes matter.",
    ],
)
def test_ordinary_sentences_are_not_absence_claims(sentence):
    assert _sentences(_body(sentence)) == []


def test_the_sources_section_is_not_scanned():
    text = "## Findings\n\nClaim [1].\n\n## Sources\n\n[1] No data exist: a note. https://x.org\n"
    assert _sentences(text) == []


# --------------------------------------------------------------------------- backing


def test_an_on_topic_query_backs_the_claim_and_an_off_topic_one_does_not():
    assert triage.absence_backed(ABSENT, [ON_TOPIC], 2)
    assert not triage.absence_backed(ABSENT, [OFF_TOPIC], 2)
    assert not triage.absence_backed(ABSENT, [], 2)


def test_plurals_are_folded_and_absence_words_do_not_count():
    sentence = "No study measures owner occupancy in qualified census tracts."
    assert triage.absence_backed(sentence, ["owner occupancy tract"], 2)
    assert not triage.absence_backed(sentence, ["study measures data evidence"], 2)


def test_a_sentence_with_no_topic_words_is_backed_by_any_query():
    assert triage.absence_backed("No data exist.", ["anything"], 2)
    assert not triage.absence_backed("No data exist.", [], 2)


# --------------------------------------------------------------------------- the finding


def test_an_unbacked_claim_mints_one_major_uncited_claim_with_fixed_wording():
    [issue] = triage.mechanical_absence_issues(report_mod.parse(REPORT), frozenset())
    assert issue.category is Category.UNCITED_CLAIM and issue.severity is Severity.MAJOR
    assert issue.claim_span == ABSENT
    assert "web_search" in issue.instruction
    assert ON_TOPIC not in issue.rationale + issue.instruction


def test_a_backed_claim_mints_nothing():
    backed = frozenset({triage.absence_key(ABSENT)})
    assert triage.mechanical_absence_issues(report_mod.parse(REPORT), backed) == []


def test_one_finding_per_paragraph():
    text = _body(f"{ABSENT} No data exist for 1990. The literature is silent.")
    assert len(triage.mechanical_absence_issues(report_mod.parse(text), frozenset())) == 1


# --------------------------------------------------------------------------- wiring


class _Searcher:
    def __init__(self, limit=10):
        self.budget = QueryBudget(limit)

    def search(self, query, count=None):
        if not self.budget.take():
            raise SearchError("search budget exhausted for this run")
        return [SearchResult(title="T", url="https://example.org/a", description="D")]


def _runtime(tmp_path, identities, config, query, *, searcher=True, limit=10):
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
        tool_script=[("web_search", json.dumps({"query": query}))] if query else [],
    )
    return Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-absence"),
        searcher=_Searcher(limit) if searcher else None,
    )


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


def _evidence_issues(rt, state):
    out = _critique({**state, "pending_lenses": ["evidence"]}, rt)
    results = out["lens_results"]["evidence"]
    assert results and not any(r["failed"] for r in results)
    # Every evidence critic's result carries the same pipeline-minted finding; one is enough.
    return [i for i in results[0]["issues"] if i["claim_span"] == ABSENT]


def test_generate_backs_a_claim_its_own_on_topic_search_covered(tmp_path, identities, config):
    rt = _runtime(tmp_path, identities, config, ON_TOPIC)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["absence_backed"] == [triage.absence_key(ABSENT)]
    gen = _events(rt, "generate")[-1]
    assert gen["absence_claims"] == 1 and gen["absence_unbacked"] == 0
    assert _evidence_issues(rt, {**out, "question": "q?"}) == []


def test_an_off_topic_search_leaves_the_claim_unbacked_and_the_evidence_lens_flags_it(
    tmp_path, identities, config
):
    rt = _runtime(tmp_path, identities, config, OFF_TOPIC)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["absence_backed"] == []
    assert _events(rt, "generate")[-1]["absence_unbacked"] == 1
    [issue] = _evidence_issues(rt, {**out, "question": "q?"})
    assert issue["category"] == "uncited_claim" and issue["severity"] == "major"
    [check] = _events(rt, "absence_check")
    assert check["absence_claims"] == 1 and check["absence_findings"] == 1
    assert ABSENT not in (rt.store.dir / "events.jsonl").read_text()


def test_a_refused_query_does_not_back_anything(tmp_path, identities, config):
    rt = _runtime(tmp_path, identities, config, ON_TOPIC, limit=1)
    rt.searcher.budget.take()  # spent before the writer asks
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["absence_backed"] == []


def test_backing_carries_forward_while_the_sentence_survives(tmp_path, identities, config):
    rt = _runtime(tmp_path, identities, config, None)
    prior = [triage.absence_key(ABSENT)]
    out = _generate({"question": "q?", "round": 1, "report": REPORT, "absence_backed": prior}, rt)
    assert out["absence_backed"] == prior
    assert _events(rt, "generate")[-1]["absence_unbacked"] == 0


def test_the_finding_is_suppressed_once_the_query_budget_is_spent(tmp_path, identities, config):
    rt = _runtime(tmp_path, identities, config, OFF_TOPIC, limit=1)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert rt.searcher.budget.exhausted
    assert _evidence_issues(rt, {**out, "question": "q?"}) == []
    assert _events(rt, "absence_check")[-1]["absence_findings"] == 0


def test_search_off_is_untouched(tmp_path, identities, config):
    rt = _runtime(tmp_path, identities, config, None, searcher=False)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert "absence_backed" not in out
    assert "absence_claims" not in _events(rt, "generate")[-1]
    assert _evidence_issues(rt, {**out, "question": "q?"}) == []
    assert _events(rt, "absence_check") == []


def test_the_knob_off_is_untouched(tmp_path, identities, config):
    config.review.absence_check = False
    rt = _runtime(tmp_path, identities, config, OFF_TOPIC)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert "absence_backed" not in out
    assert _evidence_issues(rt, {**out, "question": "q?"}) == []
