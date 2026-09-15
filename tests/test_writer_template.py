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


def test_writer_shows_the_arithmetic_and_sizes_the_comparison():
    """D-decisive-quantities, writer half. The motivating failure is a report that says
    it divides by a PUE and then does not: a derivation described but not performed reads
    as done. The comparison bullets are the symmetric half of the completeness lens's
    magnitude and decisive-consideration triggers — a counterargument whose size is never
    stated can headline a section while being orders of magnitude too small to matter."""
    system = prompts.WRITER_SYSTEM
    assert "You state the argument that settles the question" in system
    assert "a derivation you describe is a derivation you perform" in system
    assert "its inputs, its units and its result" in system
    assert "a magnitude for each side" in system
    assert "how large a counterargument is relative to the main effect" in system


def test_writer_holds_headings_and_ambiguous_readings_to_the_same_standard():
    """The heading standard is writer-side only, deliberately: heading text is not
    quotable, so no critic can raise it fail-closed (see the decision, and
    `tests/test_taxonomy.py::test_heading_text_is_not_quotable_so_no_heading_trigger_ships`).
    The reading standard is the writer half of the `unexamined_presupposition` widening."""
    system = prompts.WRITER_SYSTEM
    assert "A heading claims no more than the section beneath it supports" in system
    assert "asserts what its own prose goes on to disclaim" in system
    assert "you say which reading you answer" in system


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


# ------------------------------------- no hedge discharge (D-no-hedge-discharge)


def test_a_task_is_not_resolved_by_appending_a_qualifier():
    """The cheapest compliant edit was to keep the claim and hedge it, which made the
    flagged sentence stop matching its finding while the claim, its figure and its
    citation all survived. The revision prompt now says what resolving means."""
    standard = prompts.WRITER_RESOLUTION_STANDARD
    assert "never by appending a qualifier to a claim you keep" in standard
    # "Weaken the claim" is the critics' guaranteed escape; this is what it means.
    assert "restricting it to what the support establishes" in standard
    assert "will be filed against it again" in standard


def test_an_evaluative_qualifier_is_not_a_citation():
    standard = prompts.WRITER_RESOLUTION_STANDARD
    assert "Never substitute an evaluative qualifier for a citation" in standard
    assert "anecdotal" in standard
    assert "restated as this report's own inference and labelled as one" in standard


def test_a_caveat_is_not_copied_into_every_restatement():
    """D-claim-scoped-patch carries a *fix* to every restatement of a claim. A caveat
    pasted into all three is how one conclusion ended up with the same clause twice."""
    assert "State a limitation once, where it applies" in prompts.WRITER_RESOLUTION_STANDARD


def test_both_revision_modes_carry_the_resolution_standard():
    """Appending a qualifier is as available under `rewrite` as under `patch`, so
    scoping the edit never addressed it and the standard is not mode-specific. The
    closes themselves are untouched, so the D-scoped-revision A/B still differs in
    exactly one thing."""
    for mode in ("patch", "rewrite"):
        text = prompts.writer_revision("q", "r", [_defect()], polish=False, mode=mode)
        assert prompts.WRITER_RESOLUTION_STANDARD in text
    assert prompts.WRITER_RESOLUTION_STANDARD not in prompts.WRITER_PATCH_CLOSE
    assert prompts.WRITER_RESOLUTION_STANDARD not in prompts.WRITER_REWRITE_CLOSE


def test_a_polish_pass_carries_no_resolution_standard():
    """Rule 9 fires only when `material == 0`: there are no fix tasks to discharge."""
    text = prompts.writer_revision("q", "r", [], polish=True, mode="patch")
    assert prompts.WRITER_RESOLUTION_STANDARD not in text


def test_the_goal_sentence_names_the_change_not_the_qualifier():
    text = prompts.writer_revision("q", "r", [_defect()], polish=False)
    assert "not by qualifying a claim you leave standing" in text


def test_an_absence_claim_is_searched_for_like_any_other_claim():
    """"No source addresses this" is a claim about the literature. It was shipped
    repeatedly with no search behind it, including where the deciding assessment was
    public and from the same body as the report's most-cited reference."""
    addendum = prompts.WRITER_SEARCH_ADDENDUM
    assert "Before writing that the evidence does not cover something" in addendum
    assert "held to the same standard as every other claim you make" in addendum
    # Search-gated, because it asks the writer to go and look.
    assert "does not cover something" not in prompts.writer_system(False)
    assert "does not cover something" in prompts.writer_system(True)


def test_currency_is_checked_against_the_run_date():
    """The writer holds the run date (D-run-date-grounding) and never asked what had
    changed since its newest source."""
    addendum = prompts.WRITER_SEARCH_ADDENDUM
    assert "Check currency against the date you are given" in addendum
    assert "more than a year older" in addendum
    assert "how recent the evidence you are relying on is" in addendum
    assert "Check currency" not in prompts.writer_system(False)


# ------------------------- citation continuity (D-writer-citation-continuity)


def test_a_citation_is_a_marker_in_the_sentence_it_supports():
    """A production report kept its `## Sources` list and lost every inline marker. Nothing
    in the system prompt said a listed source cites nothing by itself."""
    for search in (False, True):
        system = prompts.writer_system(search)
        assert "A citation is the [n] marker inside the sentence it supports" in system
        assert "or listing it under Sources, cites nothing" in system
        assert "Every Sources entry is cited by at least one marker in the body" in system
        assert "every marker in the body has an entry with that number" in system
    assert "per source the body cites with an inline marker" in prompts.REPORT_SKELETON


def test_revising_keeps_markers_never_renumbers_and_defines_removing_an_attribution():
    rules = prompts.WRITER_CITATION_REVISION
    assert "Keep every [n] marker on a claim you keep" in rules
    assert "remove a marker only together with the claim it supports" in rules
    assert "Delete a Sources entry only when no remaining sentence cites it" in rules
    assert "Never renumber" in rules
    assert "a new source takes the next number after the highest one" in rules
    assert "Where a task says to remove an attribution" in rules
    assert "Never leave the claim standing as fact with no marker" in rules
    assert "read it first when `read_source` is available" in rules


def test_both_revision_modes_carry_the_citation_rules_and_the_closes_do_not():
    """Carried inside the resolution standard, so the D-scoped-revision A/B still differs
    in exactly its close."""
    assert prompts.WRITER_CITATION_REVISION in prompts.WRITER_RESOLUTION_STANDARD
    for mode in ("patch", "rewrite"):
        text = prompts.writer_revision("q", "r", [_defect()], polish=False, mode=mode)
        assert prompts.WRITER_CITATION_REVISION in text
    assert "Never renumber" not in prompts.WRITER_PATCH_CLOSE
    assert "Never renumber" not in prompts.WRITER_REWRITE_CLOSE


def test_the_reread_addendum_is_offered_only_to_a_reviser_that_reads():
    """D-writer-rereads-cited-sources. The addendum names no URL: the addresses are the
    draft's, and the draft is untrusted text fenced in the user prompt."""
    addendum = prompts.WRITER_REREAD_ADDENDUM
    assert "http" not in addendum
    assert addendum in prompts.writer_system(True, True, reread=True)
    assert addendum not in prompts.writer_system(True, True)
    assert addendum not in prompts.writer_system(True, False, reread=True)
    assert addendum not in prompts.writer_system(False, False, reread=True)
    # The system prompt a non-rereading writer receives is unchanged by the flag's existence.
    assert prompts.writer_system(True, True, reread=False) == prompts.writer_system(True, True)
