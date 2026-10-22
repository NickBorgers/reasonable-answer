"""A citation nothing can fetch is a finding, not a way out (D-no-address-citation-finding)."""

from __future__ import annotations

import json

from fakes import FakeClient

from reasonable_answer import report as report_mod
from reasonable_answer.controller import latest_scores_per_artifact, select_shipped_index
from reasonable_answer.graph import Runtime, _critique, _critique_one
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity
from reasonable_answer.triage import (
    mechanical_no_address_issues,
    unaddressed_cited_entries,
    unread_cited_entries,
    validate_issue,
)

A, B = "https://example.org/a", "https://example.org/b"
CLAIM_1 = "Textile exports from the region fell sharply across the nineteenth century"
CLAIM_2 = "Per capita income stagnated for roughly two hundred years afterwards"


def draft(body: str, *entries: tuple[int, str | None]) -> str:
    sources = "\n\n".join(f"[{n}] Paper {n}." + (f" {url}" if url else " Print only.") for n, url in entries)
    return f"## Findings\n\n{body}\n\n## Sources\n\n{sources}\n"


def mint(report: str, **kw):
    return mechanical_no_address_issues(report, report_mod.parse(report), **kw)


def test_an_entry_without_an_address_mints_one_major_uncited_claim():
    report = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, None), (2, B))
    [issue] = mint(report)
    assert issue.category is Category.UNCITED_CLAIM and issue.severity is Severity.MAJOR
    assert issue.claim_span == f"{CLAIM_1} [1]."
    assert "Deleting the address" in issue.instruction
    validate_issue(Lens.EVIDENCE, issue, report_mod.parse(report))


def test_a_bare_doi_is_not_an_address():
    assert len(mint(draft(f"{CLAIM_1} [1].", (1, "doi:10.1000/xyz")))) == 1


def test_two_address_less_entries_in_one_sentence_fold_into_one_finding():
    [issue] = mint(draft(f"{CLAIM_1} [1][2].", (1, None), (2, None)))
    assert "[1]" in issue.rationale and "[2]" in issue.rationale


def test_an_addressable_entry_is_left_to_the_unread_mechanism():
    report = draft(f"{CLAIM_1} [1].", (1, A))
    assert mint(report) == []
    assert [e.number for e, _ in unread_cited_entries(report, report_mod.parse(report), {})] == [1]


def test_the_two_mechanisms_never_share_an_entry():
    report = draft(f"{CLAIM_1} [1][2].", (1, None), (2, A))
    s = report_mod.parse(report)
    assert [e.number for e in unaddressed_cited_entries(report, s)] == [1]
    assert [e.number for e, _ in unread_cited_entries(report, s, {})] == [2]


def test_an_uncited_address_less_entry_mints_nothing():
    assert mint(draft(f"{CLAIM_1} [2].", (1, None), (2, B))) == []


def test_stripping_urls_from_the_previous_drafts_entries_mints():
    before = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, A), (2, B))
    after = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, None), (2, None))
    assert mint(before) == []
    assert len(mint(after)) == 2


def test_the_finding_carries_no_page_text():
    [issue] = mint(draft(f"{CLAIM_1} [1].", (1, None)))
    assert "Paper" not in issue.rationale + issue.instruction


def test_a_url_stripped_draft_no_longer_beats_one_with_checkable_sources():
    # Before this finding the stripped draft scored zero findings and the checkable one
    # carried an unread-source major, so the stripped draft won selection.
    def row(rnd, name, major):
        return {"round": rnd, "report": name, "blocking": 0, "major": major, "minor": 0,
                "artifact_hash": name}

    checkable = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, A), (2, B))
    stripped = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, None), (2, None))
    assert len(mint(checkable)) == 0 and len(mint(stripped)) == 2
    unread_major = 1
    rows = latest_scores_per_artifact(
        [row(1, "checkable", unread_major + len(mint(checkable))), row(2, "stripped", len(mint(stripped)))]
    )
    scores = [(r["blocking"], r["major"], r["minor"]) for r in rows]
    assert rows[select_shipped_index(scores, "fewest_defects")]["report"] == "checkable"


# -- wiring ---------------------------------------------------------------------------


class _Fetcher:
    def fetch(self, url):
        raise AssertionError("no fetch in this test")

    def fetch_all(self, urls):
        return []


STRIPPED = draft(f"{CLAIM_1} [1]. {CLAIM_2} [2].", (1, None), (2, None))


def _rt(tmp_path, identities, config, **review):
    cfg = config.model_copy(update={"review": config.review.model_copy(update=review)})
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: STRIPPED,
    )
    return Runtime(
        config=cfg,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-noaddr"),
        fetcher=_Fetcher(),
    )


def _evidence(rt):
    return _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", STRIPPED, "h" * 64, "vendor-a/model-a", 1, read_log={}
    )


def test_the_evidence_lens_mints_it_only_while_in_force(tmp_path, identities, config):
    rt = _rt(tmp_path, identities, config)
    assert len(_evidence(rt).issues) == 2
    assert _evidence(_rt(tmp_path, identities, config, no_address_finding=False)).issues == []


def test_verification_off_mints_nothing(tmp_path, identities, config):
    rt = _rt(tmp_path, identities, config)
    rt.fetcher = None
    assert _evidence(rt).issues == []


def test_the_critique_node_records_the_check_once_per_draft(tmp_path, identities, config):
    rt = _rt(tmp_path, identities, config)
    state = {
        "question": "q?",
        "report": STRIPPED,
        "artifact_hash": "h" * 64,
        "author_identity": "vendor-a/model-a",
        "read_log": {},
        "round": 1,
    }
    out = _critique(state, rt)

    def events():
        lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == "no_address_check"]

    [check] = events()
    assert check["no_address_entries"] == 2 and check["no_address_findings"] == 2
    _critique({**state, **out}, rt)
    assert len(events()) == 1
