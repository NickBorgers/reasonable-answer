"""Claim-anchored excerpts (D-claim-anchored-excerpts).

The property under test: the part of a cited page the evidence critic is shown is the
part the report's claim would be in, wherever on the page it sits — not the page's
first N characters. Everything here is offline string work.
"""

from __future__ import annotations

import pytest

from reasonable_answer import excerpt, prompts
from reasonable_answer.fetch import FetchedSource
from reasonable_answer.taxonomy import Lens

REPORT = """## Conclusion

Production accounts for roughly 80% of the climate impact of textiles [1]. A towel roll's
production is about half of its water use after a hundred washes [2].

## Key findings

- The use phase contributes about 14% and end of life about 3% [1].
- Bleaching is chemical-intensive [3].

## Sources

[1] EEA (2022). Textiles and the environment. https://example.org/eea
[2] Lindström (2023). Towel roll footprint. https://example.org/towel
[3] Hashem & Farag (2025). Cotton bleaching. https://example.org/bleach
"""

#: A page whose opening is navigation and summary, with the cited figures far past
#: a 6,000-character budget — the shape of the EEA briefing behind the motivating run.
FILLER = "Navigation. Cookie notice. About this briefing. " * 300  # ~14,000 chars
PAGE = (
    "Textiles and the environment: the role of design. Published 2022.\n"
    + FILLER
    + "Across the life cycle, the production phase accounts for 80% of the climate change "
    "impact, distribution 3%, the use phase 14% and end of life 3%.\n"
    + FILLER
)


# ------------------------------------------------------------------ anchors


def test_citing_sentences_follow_the_markers():
    assert excerpt.citing_sentences(REPORT, 1) == [
        "Production accounts for roughly 80% of the climate impact of textiles [1].",
        "- The use phase contributes about 14% and end of life about 3% [1].",
    ]
    assert excerpt.citing_sentences(REPORT, 3) == ["- Bleaching is chemical-intensive [3]."]


@pytest.mark.parametrize(
    "marker, cited",
    [
        ("[3]", {3}),
        ("[1, 3]", {1, 3}),
        ("[1,3]", {1, 3}),
        ("[2-4]", {2, 3, 4}),
        ("[2–4]", {2, 3, 4}),
        ("[1; 5]", {1, 5}),
        ("[1-400]", {1}),  # a typo, not four hundred citations
    ],
)
def test_marker_shapes_writers_produce(marker, cited):
    text = f"A claim {marker}.\n\n## Sources\n\n- https://example.org/x\n"
    for n in range(1, 8):
        assert (n in cited) == bool(excerpt.citing_sentences(text, n)), (marker, n)


def test_the_bibliography_never_cites_itself():
    text = "## Sources\n\n[1] Something [1] https://example.org/a\n"
    assert excerpt.citing_sentences(text, 1) == []


def test_entry_numbers_are_the_reports_own():
    assert excerpt.entry_numbers(REPORT) == {
        "https://example.org/eea": [1],
        "https://example.org/towel": [2],
        "https://example.org/bleach": [3],
    }


def test_entry_numbers_are_positional_for_bullets_and_explicit_otherwise():
    text = (
        "## Sources\n\n"
        "- https://example.org/a\n"
        "- Entry with no URL at all\n"
        "- https://example.org/c\n"
    )
    assert excerpt.entry_numbers(text) == {
        "https://example.org/a": [1],
        "https://example.org/c": [3],
    }
    text = "## Sources\n\n[7] https://example.org/a\n[9] https://example.org/a\n"
    assert excerpt.entry_numbers(text) == {"https://example.org/a": [7, 9]}


def test_anchors_are_every_sentence_citing_the_url():
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    assert len(anchors) == 2
    assert all("[1]" in a for a in anchors)
    assert excerpt.anchors_for(REPORT, "https://example.org/unlisted") == []


# ------------------------------------------------------------------ selection


def test_a_body_that_fits_is_shown_whole():
    chosen = excerpt.select("short page", ["anything [1]"], budget=6_000)
    assert chosen.complete
    assert chosen.shown_chars == len("short page")
    assert excerpt.render(chosen).startswith("Page text (complete, 10 characters):")


def test_a_cut_body_is_never_complete_and_says_so():
    """`FetchedSource.truncated` reaches the reader: a body that fits the budget but was
    cut at the fetch cap is every retained character and still not the page
    (D-claim-check-inconclusive-verdicts)."""
    chosen = excerpt.select("short page", ["anything [1]"], budget=6_000, truncated=True)
    assert not chosen.complete
    assert chosen.all_retained_shown
    rendered = excerpt.render(chosen)
    assert rendered.startswith("Page text: the opening 10 characters of the page, shown in full.")
    assert "continues past the last character retained" in rendered
    assert rendered.rstrip().endswith("[…]")
    # Excerpted from a cut body: the same sentence, on the partial header.
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    partial = excerpt.select(PAGE, anchors, budget=6_000, truncated=True)
    assert "continues past the last character retained" in excerpt.render(partial)
    assert "continues past" not in excerpt.render(excerpt.select(PAGE, anchors, budget=6_000))


def test_the_cited_figure_is_shown_wherever_on_the_page_it_sits():
    """The motivating defect: the figure is 14,000 characters in; a 6,000-character
    prefix never reaches it, and the critic files `misrepresented_source` against a
    page that states the claim verbatim."""
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    chosen = excerpt.select(PAGE, anchors, budget=6_000)

    shown = "".join(e.text for e in chosen.excerpts)
    assert "production phase accounts for 80%" in shown
    assert "the use phase 14% and end of life 3%" in shown
    assert not chosen.complete
    assert chosen.shown_chars <= 6_000
    # The opening is always there: title, date and scope live at the top of a page.
    assert chosen.excerpts[0].start == 0
    assert "Published 2022" in chosen.excerpts[0].text


def test_the_budget_is_honoured_and_offsets_are_true():
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    for budget in (1_000, 2_500, 6_000):
        chosen = excerpt.select(PAGE, anchors, budget=budget)
        assert chosen.shown_chars <= budget
        for e in chosen.excerpts:
            assert PAGE[e.start : e.end] == e.text
        # Document order, no overlap.
        starts = [e.start for e in chosen.excerpts]
        assert starts == sorted(starts)
        for a, b in zip(chosen.excerpts, chosen.excerpts[1:], strict=False):
            assert a.end < b.start


def test_with_no_anchors_the_opening_is_shown_as_before():
    """A source the body never cites, or a claim with no distinctive token, gets what
    it always got: the first `budget` characters. Never less than that."""
    chosen = excerpt.select(PAGE, [], budget=6_000)
    assert len(chosen.excerpts) == 1
    assert chosen.excerpts[0].start == 0
    assert chosen.excerpts[0].end == 6_000
    unmatched = excerpt.select(PAGE, ["Zebra quartz [1]."], budget=6_000)
    assert unmatched.excerpts[0].end == 6_000


def test_numbers_outweigh_words_and_percentages_are_never_weak():
    tokens = excerpt._tokens("Production accounts for roughly 80% of the impact in 2022 [1].")
    assert tokens["80%"] == excerpt._NUMBER_WEIGHT
    assert tokens["2022"] == excerpt._WORD_WEIGHT  # a year is not distinctive
    assert tokens["production"] == excerpt._WORD_WEIGHT
    assert "[1]" not in tokens and "1" not in tokens  # the marker is not a figure
    assert "roughly" in tokens and "the" not in tokens


def test_selection_is_deterministic():
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    assert excerpt.select(PAGE, anchors, budget=6_000) == excerpt.select(
        PAGE, anchors, budget=6_000
    )


def test_excerpts_open_and_close_on_whitespace():
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    chosen = excerpt.select(PAGE, anchors, budget=6_000)
    for e in chosen.excerpts[1:]:
        assert PAGE[e.start - 1].isspace(), "an excerpt opened mid-word"
    for e in chosen.excerpts:
        if e.end < len(PAGE):
            assert PAGE[e.end].isspace(), "an excerpt closed mid-word"


# ------------------------------------------------------------------ rendering


def test_render_states_how_much_of_the_page_is_shown():
    anchors = excerpt.anchors_for(REPORT, "https://example.org/eea")
    rendered = excerpt.render(excerpt.select(PAGE, anchors, budget=6_000))
    assert rendered.startswith("Page text: ")
    assert f"of {len(PAGE):,} characters shown" in rendered
    assert "[…]" in rendered
    assert "(characters 0–" in rendered


# ------------------------------------------------------- the block, end to end


def _source(url: str, text: str) -> FetchedSource:
    return FetchedSource(url=url, title="T", text=text, status=200)


def test_the_block_shows_the_passage_the_claim_would_be_in():
    block = prompts.fetched_sources_block(
        [_source("https://example.org/eea", PAGE)],
        char_budget=60_000,
        report=REPORT,
        excerpt_chars=6_000,
    )
    assert "production phase accounts for 80%" in block
    assert "characters shown" in block
    # Labelled with the report's own bibliography number, not the fetch-list index.
    assert "[1] https://example.org/eea" in block


def test_the_block_labels_by_the_reports_numbering():
    """The fetch list is deduplicated and ordered by first appearance; the report's
    `[n]` is what the critic pairs an excerpt with, so the label must be that."""
    report = (
        "Body [2] and [3].\n\n## Sources\n\n"
        "[1] No URL here\n[2] https://example.org/b\n[3] https://example.org/c\n"
    )
    block = prompts.fetched_sources_block(
        [_source("https://example.org/b", "b"), _source("https://example.org/c", "c")],
        report=report,
        excerpt_chars=6_000,
    )
    assert "[2] https://example.org/b" in block
    assert "[3] https://example.org/c" in block
    assert "[1] https://example.org/b" not in block


def test_without_a_report_the_block_is_what_it_was():
    """Every existing caller — and the audition hash — passes no report, and must see
    the page from its start under the old header."""
    block = prompts.fetched_sources_block([_source("https://example.org/a", PAGE)])
    assert "Page text (truncated):" in block
    assert "[1] https://example.org/a" in block
    assert block.index("Navigation.") < block.index("production phase accounts for 80%")


def test_the_char_budget_counts_what_is_shown_not_what_was_fetched():
    """Withholding is decided on excerpt size: two 20,000-character pages excerpted to
    6,000 each fit a 15,000 budget, where the raw bodies would not."""
    report = (
        "One [1]. Two [2].\n\n## Sources\n\n"
        "[1] https://example.org/a\n[2] https://example.org/b\n"
    )
    sources = [_source("https://example.org/a", PAGE), _source("https://example.org/b", PAGE)]
    block = prompts.fetched_sources_block(
        sources, char_budget=15_000, report=report, excerpt_chars=6_000
    )
    assert "TEXT WITHHELD: this page" not in block  # the entry shape, not the rules text
    assert block.count("characters shown") == 2


def test_critic_user_threads_the_report_through():
    from reasonable_answer import report as report_mod

    prompt = prompts.critic_user(
        Lens.EVIDENCE,
        "q?",
        report_mod.render_with_loci(REPORT),
        [_source("https://example.org/eea", PAGE)],
        report_text=REPORT,
        excerpt_chars=6_000,
    )
    assert "production phase accounts for 80%" in prompt
    assert "NOT evidence that the page lacks it" in prompt


def test_critique_once_hands_the_raw_report_to_the_prompt(monkeypatch):
    """The anchors are read from the raw artifact's `## Sources` section; the loci
    rendering the critic reads does not carry the heading in that shape."""
    from reasonable_answer import critique as critique_mod

    seen = {}

    def fake_critic_user(*args, **kwargs):
        seen.update(kwargs)
        return "prompt"

    monkeypatch.setattr(prompts, "critic_user", fake_critic_user)

    class _Client:
        class budgets:
            critic_repair_retries = 0

        def structured(self, *a, **k):
            raise critique_mod.MalformedOutputError("stop here")

    critique_mod.critique_once(
        _Client(),
        "alias",
        "p/m",
        Lens.EVIDENCE,
        "q?",
        REPORT,
        "hash",
        "author",
        sources=[_source("https://example.org/eea", PAGE)],
        excerpt_chars=6_000,
    )
    assert seen["report_text"] == REPORT
    assert seen["excerpt_chars"] == 6_000

    seen.clear()
    critique_mod.critique_once(
        _Client(), "alias", "p/m", Lens.EVIDENCE, "q?", REPORT, "hash", "author"
    )
    assert seen["report_text"] is None and seen["excerpt_chars"] is None


# ------------------------------------ citation census (D-writer-citation-continuity)


def test_the_census_of_a_fully_cited_report():
    assert excerpt.citation_census(REPORT) == {
        "source_entries": 3,
        "body_markers": 4,
        "cited_entries": 3,
        "dangling_markers": 0,
    }


def test_a_bibliography_with_no_body_marker_is_counted_as_one():
    """The run-116cc shape: eleven entries, and not one sentence carrying a marker."""
    body, sources = REPORT.split("## Sources")
    stripped = body.replace(" [1]", "").replace(" [2]", "").replace(" [3]", "")
    census = excerpt.citation_census(stripped + "## Sources" + sources)
    assert census["source_entries"] == 3
    assert census["body_markers"] == 0
    assert census["cited_entries"] == 0


def test_ranges_and_lists_cite_every_entry_they_name():
    report = (
        "## Conclusion\n\nOne [1-3]. Two [2, 3].\n\n## Sources\n\n"
        "[1] https://example.org/a\n[2] https://example.org/b\n[3] https://example.org/c\n"
    )
    census = excerpt.citation_census(report)
    assert census["body_markers"] == 2
    assert census["cited_entries"] == 3


def test_a_marker_with_no_entry_is_dangling_and_counted_once_per_number():
    report = (
        "## Conclusion\n\nOne [1]. Two [4]. Again [4].\n\n## Sources\n\n"
        "[1] https://example.org/a\n"
    )
    census = excerpt.citation_census(report)
    assert census["dangling_markers"] == 1
    assert census["cited_entries"] == 1


def test_the_census_carries_integers_only():
    for value in excerpt.citation_census(REPORT).values():
        assert type(value) is int
    for value in excerpt.citation_changes(REPORT, REPORT).values():
        assert type(value) is int


def test_renumbering_a_bibliography_is_not_a_drop():
    """Identity is the entry's URL, not its number — so a reviser that reorders the list
    and moves every marker with it has lost nothing."""
    renumbered = (
        REPORT.replace("[1]", "[@1]").replace("[3]", "[1]").replace("[@1]", "[3]")
    )
    assert renumbered != REPORT
    assert excerpt.citation_changes(REPORT, renumbered) == {
        "cited_sources_dropped": 0,
        "cited_sources_added": 0,
        "entries_removed": 0,
    }


def test_a_dropped_marker_and_a_removed_entry_are_counted_by_source():
    revised = REPORT.replace(" [3]", "").replace(
        "[3] Hashem & Farag (2025). Cotton bleaching. https://example.org/bleach\n", ""
    )
    assert excerpt.citation_changes(REPORT, revised) == {
        "cited_sources_dropped": 1,
        "cited_sources_added": 0,
        "entries_removed": 1,
    }
    uncited = REPORT.replace(" [3]", "")
    assert excerpt.citation_changes(REPORT, uncited)["cited_sources_dropped"] == 1
    assert excerpt.citation_changes(REPORT, uncited)["entries_removed"] == 0
    assert excerpt.citation_changes(uncited, REPORT)["cited_sources_added"] == 1


def test_an_entry_without_a_url_is_identified_by_its_text_not_its_number():
    before = "## Conclusion\n\nA [1]. B [2].\n\n## Sources\n\n1. Alpha book.\n2. Beta book.\n"
    after = "## Conclusion\n\nA [2]. B [1].\n\n## Sources\n\n1. Beta book.\n2. Alpha book.\n"
    assert excerpt.citation_changes(before, after)["cited_sources_dropped"] == 0
    assert excerpt.citation_changes(before, after)["entries_removed"] == 0
