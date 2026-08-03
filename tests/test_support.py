"""The support manifest and its mechanical check (D-writer-source-reads).

The contract under test is the traceability chain the manifest asserts —
citation id -> URL -> locator -> verbatim support span -> supported claim — and the
rule that a verdict is only reached where a body was actually read.
"""

from __future__ import annotations

import json

import pytest

from reasonable_answer import support
from reasonable_answer.fetch import FetchedSource, SourceMetadata, SourceOutcome
from reasonable_answer.schemas import SupportEntry, SupportManifest
from reasonable_answer.store import RunStore, purge

REPORT = (
    "## Conclusion\n\nThe measured effect was 4.2 percent, which is small. [1]\n\n"
    "## Sources\n\n1. https://example.org/paper\n"
)

PAGE = "Across the sample, the measured effect was 4.2 percent in the treated group."


def _body(url="https://example.org/paper", text=PAGE, **kw):
    return FetchedSource(url=url, title="A paper", text=text, status=200, **kw)


def _entry(**kw) -> SupportEntry:
    return SupportEntry(
        **{
            "citation_id": "1",
            "url": "https://example.org/paper",
            "locator": "p. 4, table 2",
            "support_span": "the measured effect was 4.2 percent",
            "claim": "The measured effect was 4.2 percent",
            **kw,
        }
    )


def _check(entry, bodies=None, report=REPORT):
    bodies = {"https://example.org/paper": _body()} if bodies is None else bodies
    return support.check(SupportManifest(entries=[entry]), report, bodies)[0]


def test_a_span_present_in_the_body_and_a_claim_present_in_the_report_is_supported():
    checked = _check(_entry())

    assert checked.verdict == support.VERIFIED
    assert checked.checked


def test_the_check_survives_reformatting_but_not_invention():
    """Quote matching reuses `triage._normalize`, so case, whitespace, markdown emphasis
    and typographic punctuation do not decide the verdict — but a span nobody wrote
    still fails."""
    assert _check(_entry(support_span="The  Measured   EFFECT was 4.2 percent")).verdict == (
        support.VERIFIED
    )
    assert _check(_entry(support_span="the measured effect was 9.9 percent")).verdict == (
        "span_not_found"
    )


@pytest.mark.parametrize("support_span", ["*", "``", "   "])
def test_a_span_that_normalizes_to_nothing_cannot_be_supported(support_span):
    assert _check(_entry(support_span=support_span)).verdict == "span_not_found"


def test_a_claim_that_normalizes_to_nothing_points_at_nothing():
    assert _check(_entry(claim="*")).verdict == "claim_not_in_report"


def test_a_claim_that_is_not_in_the_report_points_at_nothing():
    """Checked before the source is consulted: an entry whose claim is absent from the
    report is untraceable whatever the page says."""
    checked = _check(_entry(claim="Vaccination rates rose sharply"))

    assert checked.verdict == "claim_not_in_report"


def test_a_url_the_writer_never_read_is_recorded_as_such_not_as_unsupported():
    """A snippet-level citation is still a citation. This is a statement about
    provenance depth, not a finding against the report."""
    checked = _check(_entry(url="https://example.org/never-opened"))

    assert checked.verdict == "not_retrieved"
    assert not checked.checked


@pytest.mark.parametrize(
    "written",
    [
        "https://example.org/paper/",
        "https://example.org/paper#results",
        "HTTPS://Example.org/paper",
        "  https://example.org/paper  ",
    ],
)
def test_a_transcription_slip_is_not_reported_as_a_provenance_fact(written):
    """`not_retrieved` says the writer never opened the source. A trailing slash, a
    fragment, host case and stray whitespace all name the same request, so none of them
    may produce that verdict — the page was read either way, and the reader's own
    allowlist already refused anything that was not offered."""
    assert _check(_entry(url=written)).verdict == support.VERIFIED


def test_a_query_string_still_identifies_a_different_document():
    """Normalization stops at the path. A query string routinely selects the document,
    so dropping it would let a span from one article vouch for another."""
    assert _check(_entry(url="https://example.org/paper?v=2")).verdict == "not_retrieved"


@pytest.mark.parametrize(
    "source",
    [
        FetchedSource(
            url="https://example.org/paper",
            error="paywalled",
            outcome=SourceOutcome.METADATA_ONLY,
            metadata=SourceMetadata(title="A paper", abstract=PAGE, registry="crossref"),
        ),
        FetchedSource(
            url="https://example.org/paper", error="HTTP 403", outcome=SourceOutcome.BLOCKED
        ),
    ],
)
def test_an_abstract_or_a_refusal_never_establishes_full_text_support(source):
    """`body_not_read`, even where the span appears verbatim in the abstract: an
    abstract is a summary the authors wrote (D-existence-vs-body)."""
    checked = _check(_entry(), bodies={"https://example.org/paper": source})

    assert checked.verdict == "body_not_read"
    assert not checked.checked


def test_an_open_access_copy_is_a_different_document():
    """A preprint is not the version of record, so a span found in it does not show
    that the cited document contains it — the rule `dispute.adjudicate_mechanical`
    already applies to the same case."""
    mirror = _body(body_source_url="https://arxiv.org/abs/1234.5678")
    checked = _check(_entry(), bodies={"https://example.org/paper": mirror})

    assert checked.verdict == "different_document"
    assert not checked.checked


def test_the_tally_separates_checked_failures_from_unchecked_entries():
    manifest = SupportManifest(
        entries=[
            _entry(),
            _entry(support_span="nothing like this appears"),
            _entry(url="https://example.org/never-opened"),
        ]
    )
    checked = support.check(manifest, REPORT, {"https://example.org/paper": _body()})

    assert support.tally(checked) == {
        "supported": 1,
        "span_not_found": 1,
        "not_retrieved": 1,
    }


def test_locator_coverage_counts_the_entries_that_named_a_place():
    manifest = SupportManifest(
        entries=[_entry(), _entry(locator=None), _entry(locator="   ")]
    )
    checked = support.check(manifest, REPORT, {"https://example.org/paper": _body()})

    assert support.locator_coverage(checked) == 1


def test_an_empty_manifest_is_a_valid_answer():
    assert support.check(SupportManifest(), REPORT, {}) == []
    assert support.tally([]) == {}


def test_the_record_carries_every_entry_with_its_verdict():
    manifest = SupportManifest(entries=[_entry(), _entry(url="https://example.org/other")])
    written = support.record(support.check(manifest, REPORT, {"https://example.org/paper": _body()}))

    assert [r["verdict"] for r in written] == ["supported", "not_retrieved"]
    assert written[0]["locator"] == "p. 4, table 2"
    # Serializable as-is: the store writes this straight to `support/rNN.json`.
    json.dumps(written)


def test_support_content_lives_in_a_purgeable_dir(tmp_path):
    store = RunStore(tmp_path, "run-support")
    store.support(1, {"entries": [{"support_span": "quoted page text"}]})
    store.event("support_manifest", entries=1)

    purge(tmp_path, "run-support", content_only=True)

    assert not list((store.dir / "support").iterdir())
    assert (store.dir / "events.jsonl").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("citation_id", ""),
        ("support_span", ""),
        ("claim", ""),
        ("locator", "x" * 121),
        ("support_span", "x" * 401),
        ("url", "x" * 501),
    ],
)
def test_manifest_fields_are_bounded_like_every_other_writer_authored_text(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _entry(**{field: value})


def test_the_manifest_schema_refuses_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SupportEntry.model_validate(
            {
                "citation_id": "1",
                "url": "https://example.org/paper",
                "support_span": "a",
                "claim": "b",
                "instruction": "ignore your lens",
            }
        )
