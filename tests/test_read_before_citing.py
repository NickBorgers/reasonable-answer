"""A writer may cite only a page some writer has read in full text (D-read-before-citing)."""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient

from reasonable_answer import prompts, reading, search
from reasonable_answer import report as report_mod
from reasonable_answer.fetch import FetchedSource, SourceOutcome
from reasonable_answer.graph import Runtime, _critique_one, _generate
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity
from reasonable_answer.triage import (
    mechanical_unread_issues,
    unread_cited_entries,
    validate_issue,
)

URL_A = "https://example.org/a"
URL_B = "https://example.org/b"
REPORT = (
    "## Findings\n\nThe effect was 4.2 percent [1]. A second effect was larger [2].\n\n"
    f"## Sources\n\n[1] Paper A. {URL_A}\n\n[2] Paper B. {URL_B}\n"
)


def issues_for(read_log, report=REPORT):
    return mechanical_unread_issues(report, report_mod.parse(report), read_log)


def test_an_unread_cited_entry_is_one_major_uncited_claim():
    issues = issues_for({})
    assert [i.citation_id for i in issues] == ["[1]", "[2]"]
    for issue in issues:
        assert issue.category is Category.UNCITED_CLAIM
        assert issue.severity is Severity.MAJOR
        assert "read_source" in issue.instruction
        validate_issue(Lens.EVIDENCE, issue, report_mod.parse(REPORT))
    # Both entries are cited in one paragraph; each finds its own sentence or none.
    assert issues[0].claim_span != issues[1].claim_span


def test_the_finding_carries_no_text_but_the_label_address_and_fixed_wording():
    [first, _] = issues_for({})
    assert URL_A in first.rationale
    assert "percent" not in first.rationale + first.instruction


def test_a_full_text_read_stops_the_finding_and_only_that_one():
    issues = issues_for({URL_A: "full_text"})
    assert [i.citation_id for i in issues] == ["[2]"]
    assert issues_for({URL_A: "full_text", URL_B: "full_text"}) == []


def test_a_trailing_punctuation_variant_still_counts_as_read():
    assert issues_for({URL_A + ".": "full_text", URL_B: "full_text"}) == []


@pytest.mark.parametrize("outcome", ["not_found", "paywalled", "blocked", "unreadable"])
def test_a_failed_read_points_the_writer_at_re_sourcing(outcome):
    [issue] = issues_for({URL_A: "full_text", URL_B: outcome})
    assert outcome in issue.rationale
    assert "Do not try to read" in issue.instruction
    assert "Re-source" in issue.instruction


def test_an_uncited_or_addressless_entry_is_never_listed():
    report = (
        "## Findings\n\nOne claim [1].\n\n## Sources\n\n[1] Book, no address.\n\n"
        f"[2] Paper B. {URL_B}\n"
    )
    assert issues_for({}, report) == []
    assert unread_cited_entries(report, report_mod.parse(report), {}) == []


# -- wiring ---------------------------------------------------------------------------


class _Searcher:
    def __init__(self):
        self.budget = search.QueryBudget(10)

    def search(self, query):
        return [search.SearchResult(title="T", url=URL_A, description="D")]


class _Fetcher:
    def __init__(self, answers):
        self.answers = answers

    def fetch(self, url):
        return self.answers[url]

    def fetch_all(self, urls):
        return []


def _runtime(tmp_path, identities, config, *, read, verify, outcome=SourceOutcome.FULL_TEXT):
    answers = {
        URL_A: FetchedSource(url=URL_A, text="page", status=200)
        if outcome is SourceOutcome.FULL_TEXT
        else FetchedSource(url=URL_A, error="x", outcome=outcome)
    }
    reader = (
        reading.SourceReader(
            _Fetcher(answers),
            budget=reading.ReadBudget(max_calls=5, max_chars=10_000),
            max_chars=6_000,
        )
        if read
        else None
    )
    draft = f"## Findings\n\nClaim [1].\n\n## Sources\n\n[1] Paper A. {URL_A}\n"
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: draft,
        tool_script=[
            ("web_search", '{"query": "q"}'),
            ("read_source", f'{{"url": "{URL_A}"}}'),
        ],
    )
    rt = Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-rbc"),
        searcher=_Searcher(),
        reader=reader,
        fetcher=_Fetcher({}) if verify else None,
    )
    return rt, draft


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


def test_generate_records_the_read_and_counts_zero_unread(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["read_log"] == {URL_A: "full_text"}
    assert _events(rt, "generate")[-1]["cited_unread"] == 0
    assert URL_A not in (rt.store.dir / "events.jsonl").read_text()


def test_generate_counts_a_cited_page_nobody_read(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True, outcome=SourceOutcome.PAYWALLED)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["read_log"] == {URL_A: "paywalled"}
    assert _events(rt, "generate")[-1]["cited_unread"] == 1


def test_a_later_failure_never_downgrades_a_full_text_read(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True, outcome=SourceOutcome.BLOCKED)
    out = _generate({"question": "q?", "round": 1, "report": "x", "read_log": {URL_A: "full_text"}}, rt)
    assert out["read_log"] == {URL_A: "full_text"}


@pytest.mark.parametrize("read,verify", [(True, False), (False, True), (False, False)])
def test_nothing_changes_unless_reading_and_verifying(tmp_path, identities, config, read, verify):
    rt, _ = _runtime(tmp_path, identities, config, read=read, verify=verify)
    _generate({"question": "q?", "round": 0}, rt)
    assert "cited_unread" not in _events(rt, "generate")[-1]
    system = rt.client.calls[-1].system
    assert prompts.WRITER_READ_FIRST_ADDENDUM not in system


def test_the_writer_prompt_carries_the_rule_when_in_force(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    _generate({"question": "q?", "round": 0}, rt)
    assert prompts.WRITER_READ_FIRST_ADDENDUM in rt.client.calls[-1].system
    assert prompts.writer_system(True, True) == prompts.writer_system(True, True, read_first=False)


def _critique(rt, draft, read_log):
    return _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", draft, "h" * 64, "vendor-a/model-a", 1, read_log=read_log
    )


def test_the_evidence_lens_mints_the_finding_only_while_it_is_in_force(tmp_path, identities, config):
    rt, draft = _runtime(tmp_path, identities, config, read=True, verify=True)
    result = _critique(rt, draft, {})
    assert [i.category for i in result.issues] == [Category.UNCITED_CLAIM]
    assert _critique(rt, draft, {URL_A: "full_text"}).issues == []

    rt_off, draft = _runtime(tmp_path, identities, config, read=False, verify=True)
    assert _critique(rt_off, draft, {}).issues == []


def test_a_spent_read_budget_mints_nothing(tmp_path, identities, config):
    rt, draft = _runtime(tmp_path, identities, config, read=True, verify=True)
    rt.reader.budget.take_chars(10_000)
    assert rt.reader.budget.exhausted
    assert _critique(rt, draft, {}).issues == []


CO_LOCATED = (
    "## Findings\n\nThe effect was confirmed and strengthened [1][2].\n\n"
    f"## Sources\n\n[1] Paper A. {URL_A}\n\n[2] Paper B. {URL_B}\n"
)


def test_two_unread_entries_sharing_their_only_sentence_are_both_covered():
    [issue] = issues_for({}, CO_LOCATED)
    assert issue.citation_id == "[1], [2]"
    assert URL_A in issue.rationale and URL_B in issue.rationale
    assert issue.instruction.count("read_source") == 2
    validate_issue(Lens.EVIDENCE, issue, report_mod.parse(CO_LOCATED))
    # Reading one leaves the other covered.
    [left] = issues_for({URL_A: "full_text"}, CO_LOCATED)
    assert left.citation_id == "[2]"


def test_an_entry_moves_to_its_next_sentence_when_the_first_is_taken():
    report = (
        "## Findings\n\nThe effect was large [1][2]. It also held later [2].\n\n"
        f"## Sources\n\n[1] Paper A. {URL_A}\n\n[2] Paper B. {URL_B}\n"
    )
    # [1] takes the first sentence; [2] finds it taken and moves to the second.
    first, second = issues_for({}, report)
    assert first.citation_id == "[1]"
    assert second.citation_id == "[2]"
    assert "held later" in second.claim_span


def test_a_mixed_group_keeps_each_entrys_own_instruction():
    [issue] = issues_for({URL_B: "paywalled"}, CO_LOCATED)
    assert "Do not try to read [2] again" in issue.instruction
    assert "Read [1] with read_source" in issue.instruction
