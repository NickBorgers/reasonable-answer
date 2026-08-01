"""The conclusion-first report frame (D-report-template).

The skeleton rides the writer's *system* prompt so that every writer call — first
draft, revision, polish — holds the same structural standard, and so a seeded run
(which never sees the first-draft prompt) is steered toward the frame at its first
revision. These tests pin the parts other code depends on: the frame's presence in
both system-prompt variants, the byte-exact `## Sources` heading that
`fetch._SOURCES_HEADING` matches, and the ordering the reader experience is built
around.
"""

from reasonable_answer import prompts
from reasonable_answer.fetch import _SOURCES_HEADING


def test_skeleton_present_with_and_without_search():
    assert prompts.REPORT_SKELETON in prompts.writer_system(False)
    assert prompts.REPORT_SKELETON in prompts.writer_system(True)


def test_skeleton_orders_conclusion_first_and_sources_last():
    s = prompts.REPORT_SKELETON
    conclusion = s.index("## Conclusion")
    findings = s.index("## Key findings")
    counter = s.index("## The strongest counterargument")
    sources = s.index("## Sources")
    assert conclusion < findings < counter < sources
    assert "Nothing before `## Conclusion`" in s
    assert "nothing after `## Sources`" in s


def test_sources_heading_matches_the_mechanical_extractor():
    # fetch.extract_source_urls only reads a section whose heading text is exactly
    # "sources"; the skeleton must mandate a heading that regex accepts.
    assert _SOURCES_HEADING.search("## Sources") is not None
    assert "`## Sources`" in prompts.REPORT_SKELETON


def test_counterargument_must_be_engaged_not_merely_raised():
    # An objection raised and left unanswered is worse than never raising it
    # (D-report-template); the skeleton says so explicitly, and forbids the strawman.
    s = prompts.REPORT_SKELETON
    assert "Never raise an objection you then leave unanswered" in s
    assert "Never present a weakened version" in s


def test_no_top_level_title():
    # export_markdown already emits `# {question}` above the body; a template H1
    # would double it.
    assert "no top-level" in prompts.REPORT_SKELETON


def test_first_draft_references_the_frame():
    assert "required section frame" in prompts.writer_first_draft("q")
