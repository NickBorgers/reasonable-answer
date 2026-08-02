"""Observed source-verification coverage (D-observed-source-coverage).

`verify_sources: true` used to mean the run shipped as "consensus-reviewed with verified
sourcing" whatever the fetches returned — fifteen cited entries, three of them addressable,
three bodies read, one label that read as though all fifteen had been checked. These tests
pin the measurement that replaced it: the denominator is the shipped draft's own
bibliography, the tally is keyed to *that* draft rather than the terminal one, and every
surface renders the same counts.

Fully offline. The fetchers here are fakes that return `FetchedSource` values directly, so
nothing in this module opens a socket.
"""

from __future__ import annotations

import json

from fakes import FakeClient

from reasonable_answer import export, fetch
from reasonable_answer.fetch import FetchedSource, SourceMetadata, SourceOutcome
from reasonable_answer.taxonomy import Lens

REPORT = """# Title

A claim [1], another [2], a third [3], a fourth [4], and one nobody can address [5].

## Sources

[1] Readable. https://example.org/read
[2] Registry-confirmed. https://example.org/meta
[3] Refused. https://example.org/blocked
[4] Gone. https://example.org/gone
[5] Smith, J. (2019). A book with no URL at all. Publisher.
"""


# ----------------------------------------------------------------- entry parsing


def test_entries_are_counted_from_the_sources_section():
    assert len(fetch.source_entries(REPORT)) == 5


def test_an_entry_with_no_url_is_still_an_entry():
    """The whole point of the denominator: an unaddressable citation is something the
    report stands on, and counting only fetchable URLs is what hid that."""
    entries = fetch.source_entries(REPORT)
    assert fetch.entry_url(entries[-1]) is None
    assert fetch.entry_url(entries[0]) == "https://example.org/read"


def test_wrapped_entries_fold_into_the_line_that_started_them():
    report = (
        "# T\n\n## Sources\n\n"
        "- Smith, J. (2019). A long title that the writer\n"
        "  wrapped across two lines. https://example.org/a\n"
        "- Jones, K. (2020). https://example.org/b\n"
    )
    entries = fetch.source_entries(report)
    assert len(entries) == 2
    assert fetch.entry_url(entries[0]) == "https://example.org/a"


def test_an_unmarked_section_counts_one_entry_per_line():
    report = (
        "# T\n\n## Sources\n\n"
        "Smith 2019, https://example.org/a\n"
        "Jones 2020, https://example.org/b\n"
    )
    assert len(fetch.source_entries(report)) == 2


def test_no_sources_section_means_nothing_cited():
    assert fetch.source_entries("# T\n\nJust prose.\n") == []
    assert fetch.coverage("# T\n\nJust prose.\n").cited == 0


def test_trailing_punctuation_is_not_part_of_the_entry_url():
    """The entry's URL and the fetched URL must be the same key, or every entry looks
    unattempted."""
    entry = "[1] A source. https://example.org/a."
    assert fetch.entry_url(entry) == "https://example.org/a"
    assert fetch.entry_url(entry) in fetch.extract_source_urls(
        "## Sources\n\n" + entry + "\n"
    )


# ---------------------------------------------------------------------- tallying


def _mixed_outcomes() -> dict[str, FetchedSource]:
    return {
        "https://example.org/read": FetchedSource(
            url="https://example.org/read", text="body"
        ),
        "https://example.org/meta": FetchedSource(
            url="https://example.org/meta",
            error="HTTP 403; crossref confirms the source exists",
            outcome=SourceOutcome.METADATA_ONLY,
            metadata=SourceMetadata(title="A paper", registry="crossref"),
        ),
        "https://example.org/blocked": FetchedSource(
            url="https://example.org/blocked", status=403, error="HTTP 403"
        ),
        "https://example.org/gone": FetchedSource(
            url="https://example.org/gone", status=410, error="HTTP 410"
        ),
    }


def test_a_mixed_bibliography_tallies_every_outcome_separately():
    observed = fetch.coverage(REPORT, _mixed_outcomes())

    assert observed.cited == 5
    assert observed.addressable == 4
    assert observed.not_addressable == 1
    assert observed.attempted == 4
    assert observed.not_attempted == 0
    assert observed.bodies_read == 1
    assert observed.metadata_only == 1
    assert observed.blocked_or_unreadable == 1
    assert observed.not_found == 1
    assert observed.budget_exhausted == 0


def test_existence_confirmed_counts_a_registry_hit_but_not_a_refusal():
    """D-existence-vs-body survives in the columns: a registry record proves the source
    exists without being its text, and a 403 proves nothing either way."""
    observed = fetch.coverage(REPORT, _mixed_outcomes())
    assert observed.existence_confirmed == 2  # body read + metadata only
    assert observed.not_independently_checked == 2  # blocked + unaddressable


def test_a_definitive_not_found_is_checked_and_found_absent():
    """D-notfound-fabrication: 404/410 is an independent determination, not an
    unchecked entry, even though it cannot confirm existence."""
    observed = fetch.coverage(REPORT, _mixed_outcomes())
    assert observed.not_found == 1
    assert observed.not_independently_checked == 2


def test_a_blocked_entry_is_never_counted_as_absent():
    """D-notfound-fabrication in the tally: only a definitive not-found lands in
    `not_found`, and nothing else may be read as fabrication."""
    blocked_only = {
        f"https://example.org/{name}": FetchedSource(
            url=f"https://example.org/{name}", status=status, error=f"HTTP {status}"
        )
        for name, status in (("read", 403), ("meta", 429), ("blocked", 451), ("gone", 401))
    }
    observed = fetch.coverage(REPORT, blocked_only)
    assert observed.not_found == 0
    assert observed.blocked_or_unreadable == 4


def test_the_partitions_sum_to_the_number_of_entries():
    observed = fetch.coverage(REPORT, _mixed_outcomes())
    assert observed.addressable + observed.not_addressable == observed.cited
    assert (
        observed.bodies_read
        + observed.metadata_only
        + observed.blocked_or_unreadable
        + observed.not_found
        + observed.budget_exhausted
        + observed.not_attempted
        + observed.not_addressable
        == observed.cited
    )


def test_an_addressable_entry_nobody_fetched_is_reported_as_unattempted():
    """Distinct from unaddressable: this one *could* have been checked — the run ran out
    of `search.max_sources` before it, or the lens never got that far."""
    observed = fetch.coverage(REPORT, {})
    assert observed.addressable == 4
    assert observed.not_attempted == 4
    assert observed.attempted == 0
    assert observed.not_independently_checked == 5


def test_two_entries_citing_one_url_are_two_entries():
    """Counted in entries, not fetches: the per-run cache collapses them into one call,
    and both are still things the report stands on."""
    report = (
        "# T\n\n## Sources\n\n[1] https://example.org/a\n[2] Also https://example.org/a\n"
    )
    observed = fetch.coverage(
        report, {"https://example.org/a": FetchedSource(url="https://example.org/a", text="b")}
    )
    assert observed.cited == 2
    assert observed.bodies_read == 2


def test_the_sentence_reads_off_the_persisted_shape():
    observed = fetch.coverage(REPORT, _mixed_outcomes())
    assert fetch.coverage_sentence(observed.as_dict()) == (
        "source review: 5 cited; 4 addressable; 2 existence confirmed; "
        "1 body read; 2 not independently checked"
    )


def test_the_sentence_survives_a_record_it_does_not_recognise():
    """An older or truncated record renders as fewer checks, never as a crash and never
    as more coverage than the record supports."""
    assert fetch.coverage_sentence({"cited": 4}) == (
        "source review: 4 cited; 0 addressable; 0 existence confirmed; "
        "0 bodies read; 0 not independently checked"
    )
    assert fetch.coverage_sentence({}) == "source review: the shipped draft cites no sources"


# ------------------------------------------------------------------- in the graph


class _MixedFetcher:
    """Offline: hands back a fixed outcome per URL, in the order asked."""

    def fetch_all(self, urls):
        mapping = _mixed_outcomes()
        return [mapping[url] for url in urls]


def _runtime(tmp_path, identities, config, run_id="run-coverage", fetcher=None, searcher=None):
    from reasonable_answer.graph import Runtime
    from reasonable_answer.schemas import CritiqueOutput
    from reasonable_answer.store import RunStore

    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
    )
    return Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, run_id),
        fetcher=fetcher,
        searcher=searcher,
    )


def test_the_evidence_lens_records_coverage_for_the_artifact_it_read(
    tmp_path, identities, config
):
    from reasonable_answer.graph import _critique_one

    rt = _runtime(tmp_path, identities, config, fetcher=_MixedFetcher())
    sink: dict[str, dict] = {}
    _critique_one(
        rt,
        Lens.EVIDENCE,
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
        set(),
        attempt=1,
        coverage_sink=sink,
    )

    assert set(sink) == {"h" * 64}
    assert sink["h" * 64]["cited"] == 5
    assert sink["h" * 64]["bodies_read"] == 1
    assert sink["h" * 64]["not_independently_checked"] == 2
    assert sink["h" * 64]["verification_enabled"] is True


def test_another_lens_records_no_coverage(tmp_path, identities, config):
    """Only the evidence lens fetches, so only it can say what was reached."""
    from reasonable_answer.graph import _critique_one

    rt = _runtime(tmp_path, identities, config, fetcher=_MixedFetcher())
    sink: dict[str, dict] = {}
    _critique_one(
        rt,
        Lens.LOGIC,
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
        set(),
        attempt=1,
        coverage_sink=sink,
    )
    assert sink == {}


def test_verification_off_still_counts_the_bibliography(tmp_path, identities, config):
    """"None of it was independently checked" is a fact about a retrieval-only run that
    its configuration label cannot state."""
    from reasonable_answer.graph import _critique_one

    rt = _runtime(tmp_path, identities, config, fetcher=None, searcher=object())
    sink: dict[str, dict] = {}
    _critique_one(
        rt,
        Lens.EVIDENCE,
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
        set(),
        attempt=1,
        coverage_sink=sink,
    )

    observed = sink["h" * 64]
    assert observed["verification_enabled"] is False
    assert observed["cited"] == 5
    assert observed["attempted"] == 0
    assert observed["not_independently_checked"] == 5


def test_coverage_reaches_the_audit_trail_as_counts_only(tmp_path, identities, config):
    """RA-016: the event carries integers derived from the artifact's own text — no URL,
    no page text, no model identity."""
    from reasonable_answer.graph import _critique_one

    rt = _runtime(tmp_path, identities, config, fetcher=_MixedFetcher())
    _critique_one(
        rt,
        Lens.EVIDENCE,
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
        set(),
        attempt=1,
        coverage_sink={},
    )

    events = [
        json.loads(line) for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
    ]
    event = [e for e in events if e["kind"] == "source_coverage"][-1]
    assert event["cited"] == 5
    assert "example.org" not in json.dumps(event)


# --------------------------------------------------------- keyed to the shipped draft


EARLIER = REPORT
LATER = REPORT.replace("A claim [1]", "A revised claim [1]")


def test_finalize_reports_the_chosen_rounds_coverage_not_the_last_rounds(
    tmp_path, identities, config
):
    """A non-accepted terminal ships the best-scoring draft, which need not be the last
    one written (issue #93). The coverage a reader is shown must be the shipped draft's."""
    import reasonable_answer.graph as graph

    earlier_hash = graph.report_mod.artifact_hash(EARLIER)
    later_hash = graph.report_mod.artifact_hash(LATER)
    rt = _runtime(
        tmp_path, identities, config, run_id="run-chosen-round", fetcher=_MixedFetcher()
    )
    state = {
        "run_id": "run-chosen-round",
        "terminal_status": "exhausted_unresolved",
        "report": LATER,
        "round": 2,
        "scoreboard": [
            {
                "round": 1,
                "artifact_hash": earlier_hash,
                "report": EARLIER,
                "blocking": 0,
                "major": 0,
                "minor": 1,
                "defects": [],
            },
            {
                "round": 2,
                "artifact_hash": later_hash,
                "report": LATER,
                "blocking": 1,
                "major": 0,
                "minor": 0,
                "defects": [],
            },
        ],
        "source_coverage": {
            earlier_hash: fetch.coverage(EARLIER, _mixed_outcomes()).as_dict(),
            later_hash: fetch.coverage(LATER, {}).as_dict(),
        },
    }
    graph._finalize(state, rt)
    summary = json.loads((rt.store.dir / "final.json").read_text())

    assert summary["chosen_round"] == 1
    assert summary["artifact_hash"] == earlier_hash
    # The shipped draft's tally, not round 2's all-unattempted one.
    assert summary["source_coverage"]["attempted"] == 4
    assert summary["source_coverage"]["bodies_read"] == 1
    assert "2 existence confirmed" in summary["label"]


# ------------------------------------------------------------------- the surfaces

_FINAL = {
    "terminal_status": "accepted",
    "label": "consensus-reviewed — source review: 5 cited; 4 addressable; "
    "2 existence confirmed; 1 body read; 2 not independently checked",
    "rounds": 2,
    "chosen_round": 2,
    "artifact_hash": "a" * 64,
    "clean_records": [],
    "outstanding_defects": [],
    "source_coverage": fetch.coverage(REPORT, _mixed_outcomes()).as_dict(),
}


def test_the_markdown_export_renders_the_breakdown_and_the_caveat():
    document = export.export_markdown("Q?", REPORT, _FINAL, "run-x", exported_on="2026-01-01")
    assert "### Source review" in document
    assert "- Entries cited: 5" in document
    assert "- Body read: 1" in document
    assert "- Definitively not found (404/410): 1" in document
    assert "- Not independently checked: 2" in document
    assert export.COVERAGE_CAVEAT in document
    # The label is the measurement, and the old categorical claim is gone.
    assert "verified sourcing" not in document


def test_the_html_export_renders_the_same_breakdown():
    document = export.export_html("Q?", REPORT, _FINAL, "run-x", exported_on="2026-01-01")
    assert "Source review" in document
    assert "Blocked, paywalled or unreadable" in document
    assert "unreadable, not absent" in document


def test_a_run_without_verification_says_the_zeros_are_configuration():
    """Otherwise a column of zeros reads as a string of failed fetches."""
    final = {
        **_FINAL,
        "source_coverage": fetch.coverage(REPORT, {}, verification_enabled=False).as_dict(),
    }
    document = export.export_markdown("Q?", REPORT, final, "run-x", exported_on="2026-01-01")
    assert export.COVERAGE_VERIFICATION_OFF in document
    assert "- Addressable but not attempted: 4" in document


def test_a_record_with_no_coverage_renders_no_source_review_section():
    """Absent is not zero: a draft nothing measured must not show a table of noughts
    that reads like a bibliography of none."""
    document = export.export_markdown(
        "Q?", REPORT, {**_FINAL, "source_coverage": None}, "run-x", exported_on="2026-01-01"
    )
    assert "### Source review" not in document


def test_a_malformed_coverage_record_is_ignored_rather_than_rendered():
    for broken in ("not-an-object", {"cited": "many"}, {"bodies_read": 3}):
        prov = export.provenance("Q?", {**_FINAL, "source_coverage": broken}, "run-x")
        assert prov.source_coverage == {}
