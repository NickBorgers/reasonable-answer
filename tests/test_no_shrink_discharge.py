"""Removing a cited source does not discharge a finding for free (D-no-shrink-discharge)."""

from __future__ import annotations

import json

from fakes import FakeClient

from reasonable_answer import report as report_mod
from reasonable_answer.graph import Runtime, _critique_one
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity
from reasonable_answer.triage import mechanical_shrink_issues, validate_issue

A, B, C = "https://example.org/a", "https://example.org/b", "https://example.org/c"
CLAIM_1 = "Textile exports from the region fell sharply across the nineteenth century"
CLAIM_2 = "Per capita income stagnated for roughly two hundred years afterwards"


def draft(body: str, *entries: tuple[int, str]) -> str:
    sources = "\n\n".join(f"[{n}] Paper {n}. {url}" for n, url in entries)
    return f"## Findings\n\n{body}\n\n## Sources\n\n{sources}\n"


PREVIOUS = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, A), (2, B))
READ = {A: "full_text", B: "full_text", C: "full_text"}


def shrink(previous, report, read_log=READ, **kw):
    return mechanical_shrink_issues(previous, report, report_mod.parse(report), read_log, **kw)


def test_a_dropped_source_with_its_claim_left_uncited_mints_one_finding():
    report = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    [issue] = shrink(PREVIOUS, report)
    assert issue.category is Category.UNCITED_CLAIM
    assert issue.severity is Severity.MAJOR
    assert issue.claim_span == f"{CLAIM_1}."
    assert "not a resolution" in issue.instruction
    validate_issue(Lens.EVIDENCE, issue, report_mod.parse(report))


def test_a_claim_repointed_at_an_already_cited_source_still_mints():
    report = draft(f"{CLAIM_1} [2]. {CLAIM_2} [2].", (2, B))
    [issue] = shrink(PREVIOUS, report)
    assert issue.claim_span.startswith("Textile exports")


def test_a_withdrawn_claim_mints_nothing():
    report = draft(f"{CLAIM_2} [2].", (2, B))
    assert shrink(PREVIOUS, report) == []


def test_a_narrowed_claim_below_the_overlap_mints_nothing():
    report = draft(f"Cotton cloth exports to Europe declined [2]. {CLAIM_2} [2].", (2, B))
    assert shrink(PREVIOUS, report) == []


def test_replacement_by_a_newly_read_source_mints_nothing():
    report = draft(f"{CLAIM_1} [3]. {CLAIM_2} [2].", (2, B), (3, C))
    assert shrink(PREVIOUS, report) == []


def test_replacement_by_a_new_source_nobody_read_still_mints():
    report = draft(f"{CLAIM_1} [3]. {CLAIM_2} [2].", (2, B), (3, C))
    assert len(shrink(PREVIOUS, report, {A: "full_text", B: "full_text"})) == 1
    assert len(shrink(PREVIOUS, report, {C: "paywalled"})) == 1


def test_a_first_draft_mints_nothing():
    assert shrink("", PREVIOUS) == []


def test_no_dropped_source_mints_nothing():
    assert shrink(PREVIOUS, PREVIOUS) == []
    renumbered = draft(f"{CLAIM_1} [2]. {CLAIM_2} [1].", (1, B), (2, A))
    assert shrink(PREVIOUS, renumbered) == []


def test_a_dropped_source_nobody_cited_before_mints_nothing():
    before = draft(f"{CLAIM_1} [1].", (1, A), (2, B))
    after = draft(f"{CLAIM_1} [1].", (1, A))
    assert shrink(before, after) == []


def test_min_overlap_is_a_knob():
    reworded = draft(f"{CLAIM_1} between 1800 and 1900. {CLAIM_2} [2].", (2, B))
    assert len(shrink(PREVIOUS, reworded, min_overlap=0.5)) == 1
    assert shrink(PREVIOUS, reworded, min_overlap=0.95) == []


def test_two_dropped_entries_on_one_sentence_fold_into_one_finding():
    before = draft(f"{CLAIM_1} [1][3]. {CLAIM_2} [2].", (1, A), (2, B), (3, C))
    after = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    [issue] = shrink(before, after)
    assert A in issue.rationale and C in issue.rationale


def test_the_entry_limit_bounds_the_dropped_entries_considered():
    after = draft(f"{CLAIM_1}. {CLAIM_2}.", (2, B))
    assert len(shrink(PREVIOUS, after)) == 2
    # Only entry 1 is within the limit, so only its claim is found.
    [issue] = shrink(PREVIOUS, after, limit=1)
    assert issue.claim_span.startswith("Textile")


def test_the_finding_carries_no_page_text_and_stays_within_the_schema():
    report = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    [issue] = shrink(PREVIOUS, report)
    assert A in issue.rationale
    assert "Paper" not in issue.rationale + issue.instruction
    assert len(issue.rationale) <= 2_000


# -- wiring ---------------------------------------------------------------------------


class _Fetcher:
    def fetch(self, url):
        raise AssertionError("no fetch in this test")

    def fetch_all(self, urls):
        return []


def _rt(tmp_path, identities, config, **review):
    from reasonable_answer import reading

    reader = reading.SourceReader(
        _Fetcher(), budget=reading.ReadBudget(max_calls=5, max_chars=10_000), max_chars=6_000
    )
    cfg = config.model_copy(update={"review": config.review.model_copy(update=review)})
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: PREVIOUS,
    )
    return Runtime(
        config=cfg,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-shrink"),
        reader=reader,
        fetcher=_Fetcher(),
    )


def _critique(rt, report, previous):
    return _critique_one(
        rt,
        Lens.EVIDENCE,
        "evidence-spec",
        "q?",
        report,
        "h" * 64,
        "vendor-a/model-a",
        1,
        read_log=READ,
        previous_report=previous,
    )


def test_the_evidence_lens_mints_it_only_while_in_force(tmp_path, identities, config):
    report = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    rt = _rt(tmp_path, identities, config)
    assert len(_critique(rt, report, PREVIOUS).issues) == 1
    assert _critique(rt, report, None).issues == []
    off = _rt(tmp_path, identities, config, shrink_finding=False)
    assert _critique(off, report, PREVIOUS).issues == []


def test_a_spent_read_budget_does_not_suppress_it(tmp_path, identities, config):
    rt = _rt(tmp_path, identities, config)
    rt.reader.budget.take_chars(10_000)
    assert rt.reader.budget.exhausted
    report = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    assert len(_critique(rt, report, PREVIOUS).issues) == 1


def test_the_critique_node_records_the_shrink_check_once_per_draft(tmp_path, identities, config):
    from reasonable_answer.graph import _critique

    rt = _rt(tmp_path, identities, config)
    report = draft(f"{CLAIM_1}. {CLAIM_2} [2].", (2, B))
    state = {
        "question": "q?",
        "report": report,
        "artifact_hash": "h" * 64,
        "author_identity": "vendor-a/model-a",
        "read_log": READ,
        "draft_seen": {"hash": "p" * 64, "text": PREVIOUS},
        "round": 2,
    }
    out = _critique(state, rt)
    assert out["draft_seen"]["hash"] == "h" * 64 and out["draft_previous"] == PREVIOUS
    events = [json.loads(x) for x in (rt.store.dir / "events.jsonl").read_text().splitlines()]
    [check] = [e for e in events if e["kind"] == "shrink_check"]
    assert check["shrink_findings"] == 1 and check["cited_sources_dropped"] == 1
    assert A not in json.dumps(check)
    # A re-critique of the same draft keeps its baseline and records nothing further.
    _critique({**state, **out}, rt)
    events = [json.loads(x) for x in (rt.store.dir / "events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["kind"] == "shrink_check"]) == 1
