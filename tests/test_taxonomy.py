"""Taxonomy totality: every category is fully wired, or an unguarded lookup blows up.

`clamp_to_floor` and `prompts.critic_user` both do bare dict lookups by category.
A category added to the enum but not to `SEVERITY_FLOOR` / `_CATEGORY_MEANING` /
`LENS_CATEGORIES` fails at runtime, mid-run, on the first critic that raises it.
These tests move that failure to CI.
"""

from __future__ import annotations

from reasonable_answer.prompts import _CATEGORY_ANCHOR, _CATEGORY_MEANING, critic_user
from reasonable_answer.taxonomy import (
    LENS_CATEGORIES,
    LENSES,
    SEVERITY_FLOOR,
    Category,
    Lens,
    Severity,
)

#: The categories whose defect is an absence or a property of arrangement, so no span of
#: "the offending text" exists and `claim_span` must anchor to present text instead (D-absence-anchor).
ABSENCE_CATEGORIES = (
    Category.INCOMPLETE_ANSWER,
    Category.OMITTED_COUNTERARGUMENT,
    Category.UNEXAMINED_PRESUPPOSITION,
    Category.UNCLEAR_STRUCTURE,
)


def test_every_category_has_a_severity_floor():
    assert set(SEVERITY_FLOOR) == set(Category)


def test_every_category_has_a_prompt_meaning():
    assert set(_CATEGORY_MEANING) == set(Category)


def test_every_category_has_a_claim_span_anchor():
    assert set(_CATEGORY_ANCHOR) == set(Category)


def test_every_category_belongs_to_a_lens():
    reachable = {c for cats in LENS_CATEGORIES.values() for c in cats}
    assert reachable == set(Category)


def test_non_stylistic_categories_belong_to_exactly_one_lens():
    for category in Category:
        if category is Category.STYLISTIC:
            continue
        owners = [lens for lens in LENSES if category in LENS_CATEGORIES[lens]]
        assert len(owners) == 1, f"{category.value} owned by {owners}"


def test_bias_floors_match_bias_md():
    # docs/bias.md §5 is normative for these three values (D-social-bias).
    assert SEVERITY_FLOOR[Category.ONE_SIDED_SOURCING] is Severity.MAJOR
    assert SEVERITY_FLOOR[Category.LOADED_LANGUAGE] is Severity.MINOR
    assert SEVERITY_FLOOR[Category.UNEXAMINED_PRESUPPOSITION] is Severity.MAJOR


def test_conceptual_conflation_is_a_major_logic_category():
    # docs/convergence.md is normative for both values (D-conceptual-conflation):
    # `invalid_inference`'s sibling, floored with it, on the lens that reads how the
    # argument moves.
    assert SEVERITY_FLOOR[Category.CONCEPTUAL_CONFLATION] is Severity.MAJOR
    assert SEVERITY_FLOOR[Category.CONCEPTUAL_CONFLATION] is SEVERITY_FLOOR[
        Category.INVALID_INFERENCE
    ]
    owners = [lens for lens in LENSES if Category.CONCEPTUAL_CONFLATION in LENS_CATEGORIES[lens]]
    assert owners == [Lens.LOGIC]


def test_the_logic_brief_carries_both_triggers_and_their_exclusions():
    """D-conceptual-conflation. The two rules the decision adds have a wide
    false-positive surface, and the exclusions are the whole thing keeping them narrow:
    without them `conceptual_conflation` becomes a licence to demand arbitrary
    distinctions and the widened `overstated_claim` reads as "quantify everything".
    Both directions are measured by the paired controls, but the critic only ever sees
    the brief."""
    prompt = critic_user(Lens.LOGIC, "q", "# r\n\nbody\n")

    # Conflation: both halves of the trigger, and each named exclusion.
    assert "materially distinct" in prompt
    assert "carries an inference or a conclusion" in prompt
    assert "NOT a different word for the same thing" in prompt
    assert "NOT the absence of a subgroup breakdown" in prompt
    assert "genuinely covers both" in prompt
    assert "an aggregation the report" in prompt

    # Anchoring: the trigger, the kind/mechanism carve-out, and the resolvability rule
    # that keeps the fix inside the report even with retrieval off.
    assert "magnitude, prevalence, timing or change" in prompt
    assert "kind, mechanism or character needs no" in prompt
    assert "Never demand a specific dataset or document as the only fix" in prompt


def test_bias_categories_reach_their_lens_prompt():
    expected = {
        Lens.EVIDENCE: Category.ONE_SIDED_SOURCING,
        Lens.LOGIC: Category.LOADED_LANGUAGE,
        Lens.COMPLETENESS: Category.UNEXAMINED_PRESUPPOSITION,
    }
    for lens, category in expected.items():
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        assert category.value in prompt
    # ...and never the other lenses' prompts (scope stays closed).
    assert Category.ONE_SIDED_SOURCING.value not in critic_user(Lens.LOGIC, "q", "# r\n\nbody\n")


def test_claim_span_anchor_reaches_each_lens_prompt_and_only_its_own():
    """A lens is told what to anchor for every category it may raise, and for none it
    may not — the anchors follow the same closed scope as the meanings table (D-absence-anchor)."""
    for lens in LENSES:
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        for category in Category:
            anchor = _CATEGORY_ANCHOR[category]
            if category in LENS_CATEGORIES[lens]:
                assert anchor in prompt, f"{lens.value} prompt omits {category.value} anchor"
            else:
                assert anchor not in prompt, f"{lens.value} prompt leaks {category.value} anchor"


def test_absence_categories_anchor_to_present_text():
    """The gap this closes: a defect of absence has no span of the missing thing, so a
    critic told only "quote the offending text" quotes what is *not* in the paragraph,
    fails `_require_quote` through the whole repair budget, and fails the lens closed.

    Four of five lens failures in a 48h production window were exactly this, all on
    completeness, across two critic models. So each absence category must name the
    present text it anchors to, and the general rule must say so in the prompt body."""
    prompt = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")
    assert "the report does NOT say" in prompt
    assert "Never quote or compose the missing material" in prompt
    for category in ABSENCE_CATEGORIES:
        assert category in LENS_CATEGORIES[Lens.COMPLETENESS]
        assert _CATEGORY_ANCHOR[category] in prompt
    # The three whose defect is missing *content* must redirect that content to a field
    # that is not span-validated, or the advice is "drop the issue" by implication.
    assert "`rationale`" in _CATEGORY_ANCHOR[Category.INCOMPLETE_ANSWER]
    assert "`instruction`" in _CATEGORY_ANCHOR[Category.OMITTED_COUNTERARGUMENT]
    assert "`rationale`" in _CATEGORY_ANCHOR[Category.UNEXAMINED_PRESUPPOSITION]


def test_completeness_scope_covers_literal_obligations_and_rejects_easy_substitutes():
    prompt = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")
    assert "every explicit, material part of the question" in prompt
    assert "answers an adjacent question in its place" in prompt
    assert "does not challenge a load-bearing conclusion" in prompt
    assert "Do not invent an unstated goal" in prompt


def test_the_evidence_brief_asks_direction_and_scope_with_its_exclusions():
    """D-source-fidelity-direction-and-scope. The lens asked only "does the page contain
    this sentence?", and three defect classes passed it: a source quoted verbatim for a
    proposition its own finding cuts against, a source about a different population
    restated as if it were about this one, and a negative about page content asserted
    from a body nobody read.

    The exclusions are the whole thing keeping the widened category narrow — without
    them it reads as a licence to object to any source that is not a perfect match, and
    that is the direction the audition's invented-issue rate measures."""
    prompt = critic_user(Lens.EVIDENCE, "q", "# r\n\nbody\n")

    # The two questions, each with the trigger that makes it checkable.
    assert "does the page assert this, in this direction?" in prompt
    assert "is the page's scope the claim's scope?" in prompt
    assert "cuts the other way" in prompt
    assert "stays attached to" in prompt

    # The three narrowing exclusions, and the resolvable fixes they point at.
    assert "NOT a stylistic mismatch of wording" in prompt
    assert "merely BROADER than the claim" in prompt
    assert "NOT a demand for a source the writer cannot get" in prompt
    assert "restricting the claim to the scope the source covers" in prompt

    # Scope is this category, not the logic lens's — and the logic lens is never told to
    # look for it, because it does not hold the page.
    assert "`misrepresented_source`" in prompt
    assert Category.CONCEPTUAL_CONFLATION.value not in prompt


def test_an_unread_body_licenses_no_finding_about_content():
    """A 403 and a navigation-chrome extraction both produced `misrepresented_source`
    findings in production, and a blocked page produced an `uncited_claim` against a
    claim that already carried a citation — whose only fix is deleting a good one."""
    prompt = critic_user(Lens.EVIDENCE, "q", "# r\n\nbody\n")

    assert "licenses no finding about what a page contains" in prompt
    for label in ("BLOCKED", "COULD NOT READ", "NO READABLE TEXT", "NOT ATTEMPTED"):
        assert label in prompt
    assert "plainly not the article" in prompt
    assert "never `uncited_claim`" in prompt


def test_the_widened_meaning_is_in_the_source_less_prompt_every_lens_sees():
    """The widening is not verification-gated: with retrieval off the bar stays
    "plainly", and what widens is the kinds of failure. So the evidence lens carries the
    direction and scope clauses whether or not any page was fetched — which is also why
    `audition.prompt_hash`, computed over exactly this surface, changes."""
    meaning = _CATEGORY_MEANING[Category.MISREPRESENTED_SOURCE]
    assert "plainly does not support the claim as stated" in meaning
    assert "(direction)" in meaning and "(scope)" in meaning
    assert meaning in critic_user(Lens.EVIDENCE, "q", "# r\n\nbody\n", None)


def test_review_scaffolding_is_never_a_defect_and_findings_are_not_withdrawn_in_place():
    """Two hygiene rules, all three lenses. Section markers are addressing scaffolding
    `report.render_with_loci` adds, and a section whose paragraphs are elsewhere leaves an
    apparent gap that critics have filed as a structure defect. And the schema gives a
    critic no way to retract, so one withdrew inside the JSON and triage shipped the
    withdrawal at `major`."""
    for lens in LENSES:
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        assert "=== SECTION n: title ===" in prompt
        assert "addressing scaffolding added for this review" in prompt
        assert "Section numbers are handles" in prompt
        assert "Never file an issue in order to withdraw it" in prompt
        assert "There is no retraction" in prompt
# ------------------------------------------------- D-decisive-quantities


def test_the_logic_brief_owns_arithmetic_distance_and_absence_of_evidence():
    """D-decisive-quantities. Three defects that sit in plain sight in the text the
    critic already holds, and that no lens was told to look for: a derivation the report
    states and does not perform, a contradiction whose two halves are sections apart, and
    an "insufficient data" step read as "no effect". Each ships with its narrowing —
    without those the arithmetic rule becomes a precision-nitpick generator and the
    absence-of-evidence rule fires on every honest statement of uncertainty."""
    prompt = critic_user(Lens.LOGIC, "q", "# r\n\nbody\n")

    # Arithmetic: the trigger, the recomputation duty, and both narrowings.
    assert "reproduce it from the inputs the report itself states" in prompt
    assert "a unit that changes between premise and result" in prompt
    assert "put the recomputed value in the rationale" in prompt
    assert "stated to fewer significant figures than its inputs" in prompt
    assert "an input the report never states is not a defect of the derivation" in prompt

    # Distance is explicitly not a restriction, and `related_span` carries the other half.
    assert "however many sections apart the two passages sit" in prompt
    assert "differ by more than their stated precision" in prompt

    # Absence of evidence, with the over-firing carve-out.
    assert "Absence of evidence is not evidence of absence" in prompt
    assert "concludes accordingly has read it correctly" in prompt


def test_the_completeness_brief_owns_magnitude_the_decisive_argument_and_readings():
    """D-decisive-quantities. The completeness question is what the asker would do with
    the answer, not whether every topic-list item is mentioned. The magnitude trigger's
    conditional half is the whole thing keeping it from reading as "quantify everything",
    which is unsatisfiable under `search.enabled: false` and is the noise direction the
    audition's controls measure."""
    prompt = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")

    # The frame, one sentence, not a trigger.
    assert "what the asker would do with this answer" in prompt

    # Magnitude: trigger, the in-report condition, and the two narrowings.
    assert "no order-of-magnitude estimate, no break-even" in prompt
    assert "ordinary arithmetic from facts the report states, would supply one" in prompt
    assert "not a demand for precision" in prompt
    assert "kind, mechanism or character needs no" in prompt

    # The decisive consideration, and its in-report narrowing.
    assert "The decisive consideration" in prompt
    assert "argues its way past it without ever stating it" in prompt
    assert "follows from facts the report itself states or cites" in prompt

    # Readings of the question.
    assert "admits more than one" in prompt
    assert "state the reading taken" in prompt


def test_the_widened_readings_reach_the_category_meanings_table():
    """A brief tells a critic where to look; the meanings table is what bounds the
    category it may file under. Both must carry the widening or a critic reads the
    trigger and then finds no category that admits it (D-decisive-quantities)."""
    logic = critic_user(Lens.LOGIC, "q", "# r\n\nbody\n")
    assert "derivation that does not yield the number it reports" in logic
    assert "treats an absence of evidence as evidence of absence" in logic
    assert "however many sections apart the two passages sit" in logic

    completeness = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")
    assert "with no magnitude on either side" in completeness
    assert "decisive consideration the report's own material supplies" in completeness
    assert "without saying which reading it took" in completeness


def test_decisive_quantities_adds_no_category_and_moves_no_floor():
    """The widenings are readings of existing categories, exactly as
    D-conceptual-conflation widened `overstated_claim`. `rubric_hash` hashes
    `LENS_CATEGORIES` and `SEVERITY_FLOOR`; if either moved, every cached audition
    verdict would be invalidated by a change that only edits prompt text."""
    assert SEVERITY_FLOOR[Category.INVALID_INFERENCE] is Severity.MAJOR
    assert SEVERITY_FLOOR[Category.CONTRADICTED_CLAIM] is Severity.BLOCKING
    assert SEVERITY_FLOOR[Category.INCOMPLETE_ANSWER] is Severity.MAJOR
    assert SEVERITY_FLOOR[Category.UNEXAMINED_PRESUPPOSITION] is Severity.MAJOR
    assert Category.INVALID_INFERENCE in LENS_CATEGORIES[Lens.LOGIC]
    assert Category.INCOMPLETE_ANSWER in LENS_CATEGORIES[Lens.COMPLETENESS]


def test_heading_text_is_not_quotable_so_no_heading_trigger_ships():
    """D-decisive-quantities deliberately keeps the heading rule writer-side only.

    A section heading asserting what its prose disclaims is a real defect, but
    `report.parse` puts heading text in `section_titles` and in no `Paragraph`, so it is
    absent from both the cited paragraph and `full_text` and `triage._require_quote`
    would reject any `claim_span` drawn from one — failing the lens closed through the
    whole repair budget. Telling a critic to quote a heading is telling it to fail. When
    this test starts failing, headings have become quotable and the trigger can ship."""
    from reasonable_answer import report

    structure = report.parse(
        "## Conclusion\n\nYes.\n\n## Scientific Contradictions\n\nNo contradictions were found.\n"
    )
    assert "Scientific Contradictions" in structure.section_titles
    assert "Scientific Contradictions" not in structure.full_text
    assert all("Scientific Contradictions" not in p.text for p in structure.paragraphs)

    for lens in LENSES:
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        assert "heading" not in prompt.lower()


def test_the_instruction_bullet_says_what_the_weakened_claim_would_be():
    """The resolvability contract stays — an instruction may never demand a document
    the writer cannot obtain — but "weaken the claim" is no longer left undefined, and
    an instruction whose cheap branch is a disclaimer is not offered
    (D-no-hedge-discharge). The bullet is shared by every lens."""
    for lens in Lens:
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        assert "the instruction must allow weakening the claim as an acceptable resolution" in prompt
        assert "must say what the weakened claim would be" in prompt
        assert "state that this is unverified" in prompt
        assert "Never ask for a caveat to be added to a claim that stands." in prompt
