"""The answer card (D-answer-card).

A report that follows the D-report-template frame is rendered conclusion-first on its
page: the `## Conclusion` section becomes a card above the page's own furniture, the
counterargument is boxed where it stands, and a trailing `## Sources` folds behind its
count on screen while a `print-only` duplicate keeps it on paper. Everything fails
open — the report is model-written, so any body that does not open with a Conclusion
h2 gets exactly the plain article it got before this existed.
"""

from reasonable_answer.web.markdown import to_html
from reasonable_answer.web.registry import RunSummary
from reasonable_answer.web.render import _split_sections, render_report

FRAMED = (
    "## Conclusion\n\nYes, on the evidence [1].\n\n"
    "## Key findings\n\n- The main fact [1]\n- Another fact [2]\n\n"
    "## The strongest counterargument\n\nCritics note X [2], but it does not hold because Y [1].\n\n"
    "## Background\n\nThe long-form analysis.\n\n"
    "## Sources\n\n1. [One](https://example.org/one)\n2. [Two](https://example.org/two)\n"
)


def _summary(**overrides):
    fields = dict(
        run_id="run-card",
        status="accepted",
        question="Is it so?",
        rounds=2,
        started_at=None,
        finished_at=None,
    )
    fields.update(overrides)
    return RunSummary(**fields)


def _page(report: str) -> str:
    return render_report(_summary(), report, {"status": "accepted", "chosen_round": 2})


def test_a_framed_report_leads_with_the_answer_card():
    page = _page(FRAMED)
    assert '<article class="report answer-card"><h2>Conclusion</h2>' in page
    # The card sits above the page's own furniture — status, run link, share row.
    assert page.index("answer-card") < page.index("Run status")
    assert page.index("answer-card") < page.index("back to the run")


def test_the_counterargument_is_boxed_where_it_stands():
    page = _page(FRAMED)
    assert '<section class="counter"><h2>The strongest counterargument</h2>' in page
    # Boxed in place, inside the article flow — never folded away from its answer.
    assert "<details" not in page.split('class="counter"')[1].split("</section>")[0]


def test_sources_fold_behind_their_count_and_survive_print():
    page = _page(FRAMED)
    assert '<details class="sources-fold screen-only"><summary>Sources (2)</summary>' in page
    # The fold's body drops the duplicate heading; the print block keeps the full section.
    assert '<div class="report print-only"><h2>Sources</h2>' in page


def test_an_unframed_report_gets_the_plain_article_unchanged():
    plain = "# Answer\n\nA claim [1].\n\n## Sources\n\n1. <https://example.org>\n"
    page = _page(plain)
    assert 'class="report answer-card"' not in page
    assert '<details class="sources-fold' not in page
    assert '<article class="report">' in page
    # Fail-open keeps the old order: furniture first, then the article.
    assert page.index("Run status") < page.index('<article class="report">')


def test_split_fails_open_on_anything_off_frame():
    # Preamble before the first heading.
    assert _split_sections(to_html("A stray line.\n\n## Conclusion\n\nYes.\n")) is None
    # First heading is not the conclusion.
    assert _split_sections(to_html("## Findings\n\nA fact.\n")) is None
    # Inline markup in the heading breaks the bare-<h2> shape the splitter trusts.
    assert _split_sections(to_html("## *Conclusion*\n\nYes.\n")) is None
    # The frame itself splits: one section per h2, conclusion first.
    sections = _split_sections(to_html(FRAMED))
    assert sections is not None
    assert [h for h, _ in sections] == [
        "conclusion",
        "key findings",
        "the strongest counterargument",
        "background",
        "sources",
    ]


def test_a_framed_report_without_sources_still_gets_the_card():
    page = _page("## Conclusion\n\nYes.\n\n## Key findings\n\n- A fact.\n")
    assert 'class="report answer-card"' in page
    assert '<details class="sources-fold' not in page
