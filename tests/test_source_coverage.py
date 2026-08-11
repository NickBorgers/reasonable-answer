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


def test_plain_numbered_list_entries_are_split():
    report = (
        "# T\n\n## Sources\n\n"
        "1. Smith 2019. https://example.org/a\n"
        "2) Jones 2020. https://example.org/b\n"
    )
    entries = fetch.source_entries(report)
    assert len(entries) == 2
    assert fetch.entry_url(entries[1]) == "https://example.org/b"


def test_a_flat_bullet_list_is_one_entry_per_bullet():
    """The fourth shape writers actually produce, alongside `[n]`, `1.` and `1)`."""
    report = (
        "# T\n\n## Sources\n\n"
        "- Smith 2019. https://example.org/a\n"
        "* Jones 2020. https://example.org/b\n"
        "+ Nakamura 2021. https://example.org/c\n"
    )
    assert len(fetch.source_entries(report)) == 3


# The shape issue #168 was filed against: every reference carries an indented
# sub-bullet of commentary, which `\s{0,3}` read as a second, URL-less entry — so the
# bibliography counted double and exactly half of it reported as unaddressable.
ANNOTATED = """# T

A claim [1], another [2], and a third [3].

## Sources

[1] Luccioni, A. S., et al. (2023). "Power Hungry Processing." https://example.org/a
   - Estimates energy use per query and discusses data centre intensity.
[2] IEA (2024). Electricity 2024. https://example.org/b
   - Projects data centre demand to 2026.
[3] Smith, J. (2019). A book with no URL at all. Publisher.
   - Background monograph; no online edition.
"""


def test_an_annotation_indented_under_a_reference_is_not_a_second_entry():
    entries = fetch.source_entries(ANNOTATED)
    assert len(entries) == 3
    assert fetch.entry_url(entries[0]) == "https://example.org/a"
    assert fetch.entry_url(entries[2]) is None
    assert "Estimates energy use" in entries[0]


def test_an_annotated_bibliography_reports_only_its_real_unaddressable_entry():
    """The number the label ships: one reference here carries no URL, and the count of
    unchecked entries must be that one — not one per annotation
    (D-bibliography-entry-nesting, D-observed-source-coverage)."""
    observed = fetch.coverage(
        ANNOTATED,
        {
            "https://example.org/a": FetchedSource(url="https://example.org/a", text="body"),
            "https://example.org/b": FetchedSource(url="https://example.org/b", text="body"),
        },
    )
    assert observed.cited == 3
    assert observed.addressable == 2
    assert observed.not_addressable == 1
    assert observed.not_independently_checked == 1


def test_a_tab_indented_annotation_folds_the_same_way():
    report = (
        "# T\n\n## Sources\n\n"
        "- Smith 2019. https://example.org/a\n"
        "\t- Annotated in a tab-indented sub-bullet.\n"
        "- Jones 2020. https://example.org/b\n"
    )
    assert len(fetch.source_entries(report)) == 2


def test_references_nested_under_grouping_bullets_are_still_the_entries():
    """The converse of the annotation case, and the reason the entry depth is anchored
    at the shallowest marker that carries an address rather than at column 0: here the
    outermost bullets are headings and the references sit under them. Collapsing three
    references into two group bullets would understate the denominator, which reads as
    *more* of the bibliography verified than actually was."""
    report = (
        "# T\n\n## Sources\n\n"
        "- Peer-reviewed:\n"
        "  - Smith 2019. https://example.org/a\n"
        "  - Jones 2020. https://example.org/b\n"
        "- Institutional:\n"
        "  - IEA 2024. https://example.org/c\n"
    )
    entries = fetch.source_entries(report)
    assert len(entries) == 3
    assert fetch.entry_url(entries[2]) == "https://example.org/c"


def test_a_mixed_nested_bibliography_keeps_an_unaddressable_reference():
    report = (
        "# T\n\n## Sources\n\n"
        "- Smith, J. (2019). Title. Publisher.\n"
        "  - Available at: https://example.org/a\n"
        "- Jones, K. (2020). Book, no online edition.\n"
        "- Nakamura (2021). Title.\n"
        "  - Available at: https://example.org/c\n"
    )
    observed = fetch.coverage(report)
    assert observed.cited == 3
    assert observed.addressable == 2
    assert observed.not_addressable == 1


def test_a_flat_bibliography_with_no_urls_is_counted_line_by_line():
    """The fallback in `_entry_indent`: with no addressed marker to anchor on, the
    shallowest marker of any kind is the entry depth, so a wholly unaddressable
    bibliography reports every entry it has rather than none."""
    report = "# T\n\n## Sources\n\n[1] Smith, J. (2019). Publisher.\n[2] Jones, K. (2020). Publisher.\n"
    observed = fetch.coverage(report)
    assert observed.cited == 2
    assert observed.not_addressable == 2


def test_url_free_grouped_markers_use_the_shallowest_depth():
    report = (
        "# T\n\n## Sources\n\n"
        "- Books:\n"
        "  - Smith, J. (2019). Publisher.\n"
        "  - Jones, K. (2020). Publisher.\n"
        "- Reports:\n"
        "  - Nakamura (2021). Publisher.\n"
    )
    observed = fetch.coverage(report)
    assert observed.cited == 2
    assert observed.not_addressable == 2


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
    assert observed.body_backed_entries == 1
    assert observed.bodies_read == 1
    assert observed.metadata_only == 1
    assert observed.blocked_or_unreadable == 1
    assert observed.not_found == 1
    assert observed.budget_exhausted == 0


def test_a_budget_exhausted_entry_has_its_own_disposition():
    report = "# T\n\n## Sources\n\n[1] https://example.org/deferred\n"
    observed = fetch.coverage(
        report,
        {
            "https://example.org/deferred": FetchedSource(
                url="https://example.org/deferred",
                error="resolution budget exhausted",
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )
        },
    )

    assert observed.attempted == 1
    assert observed.budget_exhausted == 1
    assert observed.blocked_or_unreadable == 0


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
        observed.body_backed_entries
        + observed.metadata_only
        + observed.blocked_or_unreadable
        + observed.not_found
        + observed.budget_exhausted
        + observed.not_attempted
        + observed.not_addressable
        == observed.cited
    )


def test_an_addressable_entry_nobody_fetched_is_reported_as_unattempted():
    """Distinct from unaddressable: this one *could* have been checked — the lens never
    got that far, or the anti-pathological `search.max_source_urls` ceiling bound, which
    since D-unbounded-evidence is a bug signal rather than a budgeting outcome."""
    observed = fetch.coverage(REPORT, {})
    assert observed.addressable == 4
    assert observed.not_attempted == 4
    assert observed.attempted == 0
    assert observed.not_independently_checked == 5


def test_two_entries_citing_one_url_are_two_entries_backed_by_one_body():
    """Entry dispositions and fetched bodies are separate units: both citations count,
    but one cached URL may never be described as two bodies read."""
    report = (
        "# T\n\n## Sources\n\n[1] https://example.org/a\n[2] Also https://example.org/a\n"
    )
    observed = fetch.coverage(
        report, {"https://example.org/a": FetchedSource(url="https://example.org/a", text="b")}
    )
    assert observed.cited == 2
    assert observed.body_backed_entries == 2
    assert observed.bodies_read == 1
    assert "1 source body read (backing 2 cited entries)" in fetch.coverage_sentence(
        observed.as_dict()
    )


def test_the_sentence_reads_off_the_persisted_shape():
    observed = fetch.coverage(REPORT, _mixed_outcomes())
    assert fetch.coverage_sentence(observed.as_dict()) == (
        "source review: 5 cited; 4 addressable; 2 existence confirmed; "
        "1 source body read (backing 1 cited entry); 2 not independently checked"
    )


def test_the_sentence_survives_a_record_it_does_not_recognise():
    """An older or truncated record renders as fewer checks, never as a crash and never
    as more coverage than the record supports."""
    assert fetch.coverage_sentence({"cited": 4}) == (
        "source review: 4 cited; 0 addressable; 0 existence confirmed; "
        "0 source bodies read (backing 0 cited entries); 0 not independently checked"
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
        "evidence-spec",
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
        attempt=1,
        coverage_sink=sink,
    )

    assert set(sink) == {"h" * 64}
    assert sink["h" * 64]["cited"] == 5
    assert sink["h" * 64]["body_backed_entries"] == 1
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
        "logic-spec",
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
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
        "evidence-spec",
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
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
        "evidence-spec",
        "q?",
        REPORT,
        "h" * 64,
        "vendor-a/model-a",
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
    assert summary["source_coverage"]["body_backed_entries"] == 1
    assert summary["source_coverage"]["bodies_read"] == 1
    assert "2 existence confirmed" in summary["label"]


# ------------------------------------------------------------------- the surfaces

_FINAL = {
    "terminal_status": "accepted",
    "label": "consensus-reviewed — source review: 5 cited; 4 addressable; "
    "2 existence confirmed; 1 source body read (backing 1 cited entry); "
    "2 not independently checked",
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
    assert "- Entries backed by a read source body: 1" in document
    assert "- Distinct source bodies read: 1" in document
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
    for broken in (
        "not-an-object",
        {"cited": "many"},
        {"cited": True},
        {"bodies_read": 3},
    ):
        prov = export.provenance("Q?", {**_FINAL, "source_coverage": broken}, "run-x")
        assert prov.source_coverage == {}


def test_boolean_counts_are_not_rendered_as_one():
    raw = {**_FINAL["source_coverage"], "body_backed_entries": True}
    prov = export.provenance("Q?", {**_FINAL, "source_coverage": raw}, "run-x")
    assert "body_backed_entries" not in prov.source_coverage
    assert "Entries backed by a read source body" not in export.export_markdown(
        "Q?", REPORT, {**_FINAL, "source_coverage": raw}, "run-x"
    )


# ------------------------------------------- several critics, one record per artifact


def test_the_tally_that_reached_furthest_takes_the_record():
    """At review depth above 1 the evidence lens tallies the same bibliography once per
    critic. The record is the observation that reached furthest, so which thread finished
    first cannot change what the run reports (D-front-loaded-depth x D-observed-source-coverage)."""
    from reasonable_answer.graph import _record_coverage

    rich = fetch.coverage(REPORT, _mixed_outcomes()).as_dict()
    poor = fetch.coverage(REPORT, {}).as_dict()

    sink: dict[str, dict] = {}
    assert _record_coverage(sink, "h" * 64, poor) is True
    assert _record_coverage(sink, "h" * 64, rich) is True
    assert sink["h" * 64] == rich

    # The other arrival order reaches the same record — that is the whole property.
    sink = {}
    assert _record_coverage(sink, "h" * 64, rich) is True
    assert _record_coverage(sink, "h" * 64, poor) is False
    assert sink["h" * 64] == rich


def test_a_body_read_outranks_a_registry_confirmation_at_equal_reach():
    """Two tallies can check the same number of entries and still differ in how far they
    got: reading the body reaches past a registry saying the entry exists
    (D-existence-vs-body)."""
    from reasonable_answer.graph import _record_coverage

    body = fetch.coverage(REPORT, _mixed_outcomes()).as_dict()
    registry = {
        **body,
        "body_backed_entries": 0,
        "bodies_read": 0,
        "metadata_only": 2,
    }

    sink: dict[str, dict] = {}
    _record_coverage(sink, "h" * 64, registry)
    assert _record_coverage(sink, "h" * 64, body) is True
    assert sink["h" * 64]["bodies_read"] == 1


def test_equal_reach_has_a_stable_information_order():
    """A registry confirmation and a definitive absence both independently check one
    entry, but the selected record must not depend on which critic finished first."""
    from reasonable_answer.graph import _record_coverage

    report = "# T\n\n## Sources\n\n[1] https://example.org/a\n"
    metadata = FetchedSource(
        url="https://example.org/a",
        error="HTTP 403; crossref confirms the source exists",
        outcome=SourceOutcome.METADATA_ONLY,
        metadata=SourceMetadata(title="A", registry="crossref"),
    )
    not_found = FetchedSource(url="https://example.org/a", status=404, error="HTTP 404")
    confirmed = fetch.coverage(report, {metadata.url: metadata}).as_dict()
    absent = fetch.coverage(report, {not_found.url: not_found}).as_dict()

    for first, second in ((confirmed, absent), (absent, confirmed)):
        sink: dict[str, dict] = {}
        _record_coverage(sink, "h" * 64, first)
        _record_coverage(sink, "h" * 64, second)
        assert sink["h" * 64] == confirmed


def test_an_equal_tally_does_not_displace_the_record():
    """Two critics that saw the same thing produce one record and one audit event, not a
    second write that says nothing new."""
    from reasonable_answer.graph import _record_coverage

    observed = fetch.coverage(REPORT, _mixed_outcomes()).as_dict()
    sink: dict[str, dict] = {}
    assert _record_coverage(sink, "h" * 64, observed) is True
    assert _record_coverage(sink, "h" * 64, dict(observed)) is False


def test_record_updates_and_audit_callbacks_cannot_interleave():
    """The event for a displaced record must finish before the better record and its
    event take the lock, so the final event reconstructs the final state."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from reasonable_answer.graph import _record_coverage

    poor = fetch.coverage(REPORT, {}).as_dict()
    rich = fetch.coverage(REPORT, _mixed_outcomes()).as_dict()
    first_callback_started = threading.Event()
    release_first_callback = threading.Event()
    emitted: list[dict] = []

    def emit(record):
        emitted.append(record)
        if record is poor:
            first_callback_started.set()
            assert release_first_callback.wait(timeout=2)

    sink: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            _record_coverage, sink, "h" * 64, poor, on_recorded=emit
        )
        assert first_callback_started.wait(timeout=2)
        second = pool.submit(
            _record_coverage, sink, "h" * 64, rich, on_recorded=emit
        )
        assert not second.done()
        release_first_callback.set()
        assert first.result() is True
        assert second.result() is True

    assert emitted == [poor, rich]
    assert emitted[-1] == sink["h" * 64]


class _DegradingFetcher:
    """Hands the first caller a bibliography it could barely reach and the second one it
    read properly — the simultaneous-cache-miss window, made deterministic.

    `SourceFetcher`'s real cache is monotone but last-write-wins, so two evidence critics
    that both miss on the same URL can genuinely observe different outcomes. Which critic
    gets which is up to the thread pool; the record must not be.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._calls = 0

    def fetch_all(self, urls):
        with self._lock:
            self._calls += 1
            first = self._calls == 1
        if first:
            return [
                FetchedSource(url=url, status=403, error="HTTP 403") for url in urls
            ]
        mapping = _mixed_outcomes()
        return [mapping[url] for url in urls]


def _critique_state(identities) -> dict:
    return {
        "question": "Is it so?",
        "report": REPORT,
        "artifact_hash": _hash(REPORT),
        "author_identity": identities["writer-a"],
        "pending_lenses": ["evidence"],
        "run_date": "2026-07-28",
    }


def _hash(text: str) -> str:
    from reasonable_answer import report as report_mod

    return report_mod.artifact_hash(text)


def _coverage_events(rt) -> list[dict]:
    events = [
        json.loads(line) for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
    ]
    return [e for e in events if e["kind"] == "source_coverage"]


def _events(cfg, run_id: str) -> list[dict]:
    path = cfg.runs_dir / run_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_two_evidence_critics_leave_one_record_for_the_artifact(
    tmp_path, identities, config
):
    """Review depth 2 is the shipped default, so the evidence lens reads every draft
    twice. The artifact still gets exactly one coverage record."""
    from reasonable_answer.graph import _critique

    assert config.review.depth_for(Lens.EVIDENCE) == 2
    rt = _runtime(tmp_path, identities, config, fetcher=_MixedFetcher())
    out = _critique(_critique_state(identities), rt)

    assert len(out["lens_results"]["evidence"]) == 2  # both critics really ran
    assert set(out["source_coverage"]) == {_hash(REPORT)}


def test_the_record_is_the_furthest_reaching_critics_tally_not_the_first_to_finish(
    tmp_path, identities, config
):
    """Whichever critic wins the race, the run reports the bibliography that was actually
    read — never the degraded observation that happened to land first."""
    from reasonable_answer.graph import _critique

    rt = _runtime(tmp_path, identities, config, fetcher=_DegradingFetcher())
    out = _critique(_critique_state(identities), rt)

    observed = out["source_coverage"][_hash(REPORT)]
    assert observed["body_backed_entries"] == 1
    assert observed["bodies_read"] == 1
    assert observed["not_independently_checked"] == 2  # not the all-blocked 5


def test_the_last_coverage_event_is_always_the_one_the_record_carries(
    tmp_path, identities, config
):
    """Only a tally that took the record is emitted, so reconstructing a run's coverage
    from the audit trail means reading the last event for the artifact — the same
    convention `fetch_sources` already uses."""
    from reasonable_answer.graph import _critique

    rt = _runtime(tmp_path, identities, config, fetcher=_DegradingFetcher())
    out = _critique(_critique_state(identities), rt)

    events = _coverage_events(rt)
    assert events, "the evidence lens must record what it reached"
    last = {k: v for k, v in events[-1].items() if k not in ("kind", "ts", "artifact_hash")}
    assert last == out["source_coverage"][_hash(REPORT)]


def test_verification_off_gives_both_critics_the_same_thing_to_see(
    tmp_path, identities, config
):
    """With no fetching there is no race at all: the tally is derived from the artifact's
    own text, so both critics compute it identically and one event is emitted."""
    from reasonable_answer.graph import _critique

    rt = _runtime(tmp_path, identities, config, fetcher=None, searcher=object())
    out = _critique(_critique_state(identities), rt)

    assert len(_coverage_events(rt)) == 1
    assert out["source_coverage"][_hash(REPORT)]["not_independently_checked"] == 5


def test_each_artifacts_record_survives_the_rounds_after_it(
    tmp_path, identities, config
):
    """`source_coverage` is merged, not replaced: a later draft's tally must not evict the
    earlier draft's, because the run may ship the earlier one (issue #93)."""
    from reasonable_answer.graph import _critique

    rt = _runtime(tmp_path, identities, config, fetcher=_MixedFetcher())
    state = _critique_state(identities)
    state |= _critique(state, rt)

    revised = REPORT.replace("A claim [1]", "A revised claim [1]")
    state |= {
        "report": revised,
        "artifact_hash": _hash(revised),
        "pending_lenses": ["evidence"],
        "lens_results": {},
    }
    out = _critique(state, rt)

    assert set(out["source_coverage"]) == {_hash(REPORT), _hash(revised)}


# ------------------------------------------------------- the whole graph, end to end


def test_a_verified_run_carries_its_measured_coverage_all_the_way_out(
    tmp_path, identities, roster, monkeypatch
):
    """The first end-to-end exercise of `verify_sources` through the whole graph: the
    evidence lens fetches on every pass, both critics of the depth-2 slate tally the same
    bibliography, and one measurement reaches `final.json`, the label and the export.

    Offline like the rest of the suite — the real `SourceFetcher` is used, cache, lock and
    all, with only its network rung replaced. That is deliberate: the per-URL cache is
    exactly what makes a depth-2 slate cost one fetch per URL, so a fake fetcher would
    skip the thing this test is here to cover.
    """
    from reasonable_answer.config import Budgets, Config, SearchConfig
    from reasonable_answer.graph import run
    from reasonable_answer.schemas import CritiqueOutput

    outcomes = _mixed_outcomes()
    fetched: list[str] = []

    def offline(self, url, depth=0):
        fetched.append(url)
        return outcomes[url]

    monkeypatch.setattr(fetch.SourceFetcher, "_resolved", offline)

    cfg = Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=5, retry_backoff_seconds=0.0),
        search=SearchConfig(verify_sources=True),
        runs_dir=tmp_path / "runs",
    )
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
    )
    state = run(cfg, question="Is it so?", seed=REPORT, client=client)
    summary = json.loads(
        (cfg.runs_dir / state["run_id"] / "final.json").read_text()
    )

    observed = summary["source_coverage"]
    assert observed["cited"] == 5
    assert observed["body_backed_entries"] == 1
    assert observed["bodies_read"] == 1
    assert observed["existence_confirmed"] == 2
    assert observed["not_independently_checked"] == 2
    assert observed["verification_enabled"] is True

    # Keyed to the draft that shipped — `intake` normalises the seed, so the shipped
    # artifact is not the literal text this module holds, which is exactly why the
    # lookup goes through the hash rather than assuming the terminal draft.
    assert state["source_coverage"][summary["artifact_hash"]] == observed

    # The label is the arithmetic, not a posture — the whole point of the decision.
    assert "source review: 5 cited" in summary["label"]
    assert "2 existence confirmed" in summary["label"]
    assert "verified sourcing" not in summary["label"]

    # D-run-build-stamp rides out on the same record; the merge that brought these two
    # together must not have dropped either.
    assert summary["build"]["source"] in ("image", "git", "unknown")

    # One record for the artifact however many critics and passes read it, and the audit
    # trail agrees with what shipped.
    coverage_events = [
        e for e in _events(cfg, state["run_id"]) if e["kind"] == "source_coverage"
    ]
    assert [e["artifact_hash"] for e in coverage_events] == [summary["artifact_hash"]]

    # The per-URL cache is what keeps a depth-2 slate to one fetch per URL, across every
    # pass of the run — not one per critic.
    assert sorted(set(fetched)) == sorted(fetched) == sorted(outcomes)

    document = export.export_markdown(
        "Is it so?", state["report"], summary, state["run_id"], exported_on="2026-01-01"
    )
    assert "### Source review" in document
    assert "- Entries cited: 5" in document
    assert export.COVERAGE_CAVEAT in document
    # Both provenance surfaces the merge joined, on one page.
    assert "- Built from: `" in document
