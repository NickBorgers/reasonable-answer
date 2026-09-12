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


def test_every_explicit_question_part_is_an_answer_obligation():
    system = prompts.WRITER_SYSTEM
    assert "every explicit part of the question as an answer obligation" in system
    assert "answer each in the conclusion and support each in the body" in system
    assert "For a question about change or comparison, state the baseline and contrast" in system
    assert "Do not substitute an adjacent question or invent an unstated goal" in system


def test_no_top_level_title():
    # export_markdown already emits `# {question}` above the body; a template H1
    # would double it.
    assert "no top-level" in prompts.REPORT_SKELETON


def test_first_draft_references_the_frame():
    assert "required section frame" in prompts.writer_first_draft("q")


# ------------------------------------------------- revision scope (D-scoped-revision)


def _defect():
    from reasonable_answer.schemas import Defect, StructuralRef
    from reasonable_answer.taxonomy import Category, Severity

    return Defect(
        locus=StructuralRef(section=2, paragraph=1),
        category=Category.UNCITED_CLAIM,
        severity=Severity.MAJOR,
        claim_span="Water boils at 100 degrees Celsius",
        rationale="no citation attached",
        instruction="cite a source or remove the claim",
    )


def test_rewrite_mode_is_byte_identical_to_the_pre_scoped_revision_prompt():
    """`revision.mode: rewrite` is the A/B control arm. If it drifts, the comparison
    it exists to enable is measuring two changes at once."""
    text = prompts.writer_revision("q", "r", [_defect()], polish=False, mode="rewrite")
    assert text.endswith(prompts.WRITER_REWRITE_CLOSE)
    assert prompts.WRITER_PATCH_CLOSE not in text
    # The default is the control arm, so every existing call site is unaffected.
    assert prompts.writer_revision("q", "r", [_defect()], polish=False) == text


def test_patch_mode_demands_byte_identical_untouched_paragraphs():
    text = prompts.writer_revision("q", "r", [_defect()], polish=False, mode="patch")
    assert text.endswith(prompts.WRITER_PATCH_CLOSE)
    # "byte-identical" is the load-bearing wording: "keep the meaning" licenses exactly
    # the paraphrase this exists to stop.
    assert "byte-identical" in prompts.WRITER_PATCH_CLOSE
    # Still the whole document — the artifact hash is taken over the whole document.
    assert "the whole document, not a diff" in text


def test_a_polish_pass_is_never_scoped():
    """Rule 9 fires only when `material == 0`, so there are no defect loci to scope to;
    polish is a clarity pass over the entire report by definition."""
    text = prompts.writer_revision("q", "r", [], polish=True, mode="patch")
    assert text.endswith(prompts.WRITER_REWRITE_CLOSE)
    assert prompts.WRITER_PATCH_CLOSE not in text


# ------------------------------------------ claim-scoped patch (D-claim-scoped-patch)


def test_patch_mode_carries_a_fix_to_every_restatement_of_the_claim():
    """The frame restates a claim in the conclusion, the key findings and the body; a
    task names one locus. A paragraph-only licence left the copies unfixed and set the
    patched copy against them, which is what the next pass then flagged."""
    close = prompts.WRITER_PATCH_CLOSE
    assert "The unit of a fix is the claim, not the paragraph" in close
    assert "every other passage that restates that claim" in close
    # The licence is the restatements and nothing else: byte-identical still governs
    # every paragraph that neither a task nor a restated claim implicates.
    assert "Those restatement edits are in scope; nothing else is." in close
    assert "byte-identical" in close


def test_patch_mode_forbids_placeholders_and_heading_changes():
    """Two observed ways a patch chain decays: a section returned as
    '(No changes required.)', and headings numbered, renumbered or dropped."""
    close = prompts.WRITER_PATCH_CLOSE
    assert "Reproduce every paragraph you are not editing in full" in close
    assert "(no changes required)" in close
    assert "do not number, renumber, drop, or merge sections" in close
