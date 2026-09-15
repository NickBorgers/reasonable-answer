"""Citations a reader can follow (D-citation-links).

Pure string work over the report: no run directory, no app. What is under test is that a
marker becomes a link to the right entry, that only a verified passage becomes a deep link,
and that everything the rewrite does not understand is left exactly as written.
"""

from __future__ import annotations

import re

from reasonable_answer import citelinks, claimcheck
from reasonable_answer.citelinks import VerifiedSpan, deep_link, linked_markdown, select_spans, text_fragment
from reasonable_answer.web.markdown import to_html

REPORT = """# Answer

## Conclusion

Output rose 3% in 2024 [1]. Two surveys agree [2-3]. A list cites [1, 3].

An uncatalogued claim [7].

## Sources

[1] Alpha report. https://example.org/alpha.
[2] Beta survey <https://example.com/beta#methods>
[3] Gamma paper https://example.net/gamma.pdf
"""


def _sentence(number: int, contains: str) -> str:
    pairs, _ = claimcheck.pairs(REPORT)
    [pair] = [p for p in pairs if p.number == number and contains in p.sentence]
    return pair.sentence


def _record(*verdicts: dict) -> dict:
    return {"counts": {}, "verdicts": list(verdicts)}


def _verdict(number: int, sentence: str, url: str, verdict: str, span: str | None = None) -> dict:
    return {"number": number, "sentence": sentence, "url": url, "verdict": verdict, "support_span": span}


# ------------------------------------------------------------------ markers


def test_a_marker_links_to_its_entry_and_renders_as_the_marker():
    html = to_html(linked_markdown(REPORT))

    assert '<a href="https://example.org/alpha" rel="noreferrer noopener">[1]</a>' in html


def test_a_range_and_a_list_link_every_number_they_cite():
    linked = linked_markdown(REPORT)

    assert "agree [[2]](https://example.com/beta#methods)[[3]](https://example.net/gamma.pdf)." in linked
    assert "cites [[1]](https://example.org/alpha)[[3]](https://example.net/gamma.pdf)." in linked
    html = to_html(linked)
    for number, url in ((2, "https://example.com/beta#methods"), (3, "https://example.net/gamma.pdf")):
        assert f'<a href="{url}" rel="noreferrer noopener">[{number}]</a>' in html


def test_a_marker_with_no_entry_stays_plain():
    linked = linked_markdown(REPORT)

    assert "An uncatalogued claim [7]." in linked


def test_a_marker_mixing_a_listed_and_an_unlisted_number_links_only_the_listed_one():
    report = REPORT.replace("An uncatalogued claim [7].", "A mixed claim [1, 7].")

    assert "A mixed claim [[1]](https://example.org/alpha)[7]." in linked_markdown(report)


def test_markers_inside_the_sources_section_are_not_rewritten():
    sources = linked_markdown(REPORT).split("## Sources", 1)[1]

    assert "[[" not in sources
    assert "[1] Alpha report." in sources


def test_markers_in_code_existing_links_and_escapes_are_left_alone():
    body = (
        "# Answer\n\nIn code `[1]` and\n\n```\nfenced [1]\n```\n\n"
        "a link [see [1]](https://other.example/x) and an escape \\[1] here.\n\n"
    )
    report = body + REPORT.split("## Sources", 1)[1].join(["## Sources", ""])
    linked = linked_markdown(report)

    assert linked.split("## Sources", 1)[0] == body


def test_a_range_excerpt_would_not_expand_is_left_as_written():
    report = REPORT.replace("An uncatalogued claim [7].", "A typo [1-400].")

    assert "A typo [1-400]." in linked_markdown(report)


def test_a_number_already_defined_as_a_link_reference_is_not_wrapped_again():
    report = "# Answer\n\nA claim [1].\n\n[1]: https://example.org/ref\n\n## Sources\n\n[1] https://example.org/alpha\n"

    assert "A claim [1]." in linked_markdown(report)


def test_a_report_with_no_markers_still_gets_clickable_sources():
    report = "# Answer\n\nNo markers here.\n\n## Sources\n\n- Alpha https://example.org/alpha\n"
    linked = linked_markdown(report)

    assert "No markers here." in linked
    assert "- Alpha <https://example.org/alpha>" in linked
    assert '<a href="https://example.org/alpha" rel="noreferrer noopener">' in to_html(linked)


def test_trailing_punctuation_stays_outside_a_sources_autolink():
    assert "[1] Alpha report. <https://example.org/alpha>." in linked_markdown(REPORT)


def test_a_report_without_sources_is_unchanged():
    report = "# Answer\n\nA claim [1].\n"

    assert linked_markdown(report) == report
    assert linked_markdown("") == ""


def test_a_url_that_would_break_markdown_is_made_safe_as_a_destination():
    report = "# Answer\n\n| a |\n|---|\n| x [1] |\n\n## Sources\n\n[1] https://example.org/a|b(c\n"
    html = to_html(linked_markdown(report))

    assert '<td>x <a href="https://example.org/a%7Cb(c" rel="noreferrer noopener">[1]</a></td>' in html


# ------------------------------------------------------------------ fragments


def test_a_short_span_is_matched_whole_and_encoded():
    assert text_fragment("  rose  by\n3%  ") == ":~:text=rose%20by%203%25"


def test_hyphen_comma_ampersand_and_non_ascii_are_percent_encoded():
    assert text_fragment("a-b, c & d café") == ":~:text=a%2Db%2C%20c%20%26%20d%20caf%C3%A9"


def test_a_long_span_becomes_a_start_and_an_end():
    span = "one two three four five six seven eight nine ten eleven"

    start, end = "one%20two%20three%20four%20five", "seven%20eight%20nine%20ten%20eleven"

    assert text_fragment(span) == f":~:text={start},{end}"


def test_an_empty_span_has_no_fragment():
    assert text_fragment(" \n ") == ""
    assert deep_link("https://example.org/a", "   ") == "https://example.org/a"


def test_a_url_with_a_fragment_gets_the_directive_appended_not_a_second_hash():
    assert deep_link("https://example.com/beta#methods", "a b") == "https://example.com/beta#methods:~:text=a%20b"
    assert deep_link("https://example.com/beta", "a b") == "https://example.com/beta#:~:text=a%20b"
    assert deep_link("https://example.com/beta#:~:text=x", "a b") == "https://example.com/beta#:~:text=x"


def test_a_pdf_gets_no_fragment():
    assert deep_link("https://example.net/gamma.pdf", "a b") == "https://example.net/gamma.pdf"
    assert deep_link("https://example.net/GAMMA.PDF?page=2", "a b") == "https://example.net/GAMMA.PDF?page=2"


def test_a_verified_span_deep_links_the_marker_it_was_checked_for():
    sentence = _sentence(1, "Output rose")
    spans = {(1, sentence): VerifiedSpan("https://example.org/alpha", "output rose by 3% in 2024")}
    linked = linked_markdown(REPORT, spans)

    assert "2024 [[1]](https://example.org/alpha#:~:text=output%20rose%20by%203%25%20in%202024)." in linked
    # The same entry cited by a different sentence was not checked there: a plain link.
    assert "cites [[1]](https://example.org/alpha)[[3]]" in linked


def test_a_span_verified_against_a_different_url_is_not_used():
    sentence = _sentence(1, "Output rose")
    spans = {(1, sentence): VerifiedSpan("https://mirror.example/alpha", "output rose")}

    assert ":~:text=" not in linked_markdown(REPORT, spans)


def test_a_verified_span_on_a_pdf_entry_gives_a_plain_link():
    sentence = _sentence(3, "Two surveys")
    spans = {(3, sentence): VerifiedSpan("https://example.net/gamma.pdf", "surveys agree")}

    assert ":~:text=" not in linked_markdown(REPORT, spans)


def test_the_span_key_is_the_claim_check_pair_key():
    """Every pair claim check would record gets its fragment: the sentence split, marker
    expansion and number map here are the ones `claimcheck.pairs` uses."""
    pairs, _ = claimcheck.pairs(REPORT)
    spans = {(p.number, p.sentence): VerifiedSpan(p.url, f"span for {p.number}") for p in pairs}
    linked = linked_markdown(REPORT, spans)

    non_pdf = [p for p in pairs if not p.url.endswith(".pdf")]
    assert len(re.findall(r":~:text=", linked)) == len(non_pdf) == 3


# ------------------------------------------------------------------ records


def test_no_records_means_plain_links():
    assert select_spans([]) == {}
    assert ":~:text=" not in linked_markdown(REPORT, select_spans([]))


def test_a_supported_verdict_with_a_span_is_selected():
    record = _record(_verdict(1, "S.", "https://a", "supported", "the span"))

    assert select_spans([record]) == {(1, "S."): VerifiedSpan("https://a", "the span")}


def test_only_supported_verdicts_with_a_span_count():
    record = _record(
        _verdict(1, "S.", "https://a", "supported", "  "),
        _verdict(2, "S.", "https://a", "absent", "near miss"),
        _verdict(3, "S.", "https://a", "unreadable", None),
        _verdict(4, "S.", "https://a", "unchecked", None),
    )

    assert select_spans([record]) == {}


def test_a_contradicted_pair_gets_no_fragment_even_when_another_record_supports_it():
    supported = _record(_verdict(1, "S.", "https://a", "supported", "backs it"))
    contradicted = _record(_verdict(1, "S.", "https://a", "contradicted", "refutes it"))

    assert select_spans([supported, contradicted]) == {}
    assert select_spans([contradicted, supported]) == {}


def test_the_earliest_record_wins():
    first = _record(_verdict(1, "S.", "https://a", "supported", "first span"))
    second = _record(_verdict(1, "S.", "https://a", "supported", "second span"))

    assert select_spans([first, second])[(1, "S.")].span == "first span"


def test_malformed_records_are_skipped_not_raised():
    records = [
        "not a record",
        {"verdicts": "nope"},
        _record("nope", {"number": "1", "sentence": "S.", "url": "https://a", "verdict": "supported"}),
        _record({"number": True, "sentence": "S.", "url": "https://a", "verdict": "supported",
                 "support_span": "x"}),
        _record(_verdict(2, "T.", "https://b", "supported", "kept")),
    ]

    assert select_spans(records) == {(2, "T."): VerifiedSpan("https://b", "kept")}


def test_the_module_reuses_the_excerpt_marker_grammar():
    """One definition of what a citation marker is, shared with claim check."""
    assert citelinks.excerpt._MARKER.pattern == claimcheck.excerpt._MARKER.pattern
