"""Triage: mechanical floors, fail-closed validation, depersonalization, clean records."""

from __future__ import annotations

import pytest

from reasonable_answer import report as report_mod
from reasonable_answer.schemas import LensResult, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity
from reasonable_answer.triage import (
    LensValidationError,
    ViolationCode,
    clamp,
    clean_records,
    material_count,
    signal_signature,
    tally,
    to_defects,
    validate_issue,
)

REPORT = """# Title

Intro paragraph making a claim.

## Body

A second claim, cited [1].

Another paragraph.
"""

STRUCTURE = report_mod.parse(REPORT)


def issue(category: Category, severity: Severity, section=1, paragraph=1) -> RawIssue:
    return RawIssue(
        category=category,
        severity=severity,
        locus=StructuralRef(section=section, paragraph=paragraph),
        claim_span="Intro paragraph making a claim.",
        rationale="no citation attached",
        instruction="cite a source or remove the claim",
    )


def result(lens: Lens, issues: list[RawIssue], failed=False, critic="vendor-x/critic") -> LensResult:
    return LensResult(
        lens=lens,
        artifact_hash="h" * 64,
        critic_alias="critic",
        critic_identity=critic,
        artifact_author_identity="vendor-a/author",
        failed=failed,
        issues=issues,
    )


def test_critics_can_escalate_but_never_downgrade():
    escalated = clamp([issue(Category.UNCITED_CLAIM, Severity.BLOCKING)])
    assert escalated[0].severity is Severity.BLOCKING  # above the floor: preserved

    downgraded = clamp([issue(Category.FABRICATED_CITATION, Severity.MINOR)])
    assert downgraded[0].severity is Severity.BLOCKING  # below the floor: clamped up


def test_out_of_scope_category_fails_the_lens():
    with pytest.raises(LensValidationError):
        validate_issue(Lens.LOGIC, issue(Category.UNCITED_CLAIM, Severity.MAJOR), STRUCTURE)


def test_nonexistent_locus_fails_the_lens():
    with pytest.raises(LensValidationError):
        validate_issue(
            Lens.LOGIC,
            issue(Category.OVERSTATED_CLAIM, Severity.MAJOR, section=99, paragraph=99),
            STRUCTURE,
        )


def test_over_length_span_is_rejected_by_the_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RawIssue(
            category=Category.OVERSTATED_CLAIM,
            severity=Severity.MAJOR,
            locus=StructuralRef(section=1, paragraph=1),
            claim_span="x" * 401,
            rationale="r",
            instruction="i",
        )


def test_a_failed_lens_never_contributes_counts_or_clean_records():
    results = [
        result(Lens.LOGIC, [issue(Category.OVERSTATED_CLAIM, Severity.MAJOR)], failed=True),
        result(Lens.EVIDENCE, []),
    ]
    _, totals = tally(results)
    assert material_count(totals) == 0  # partial counts discarded
    assert [r.lens for r in clean_records(results)] == [Lens.EVIDENCE]


def test_defects_carry_no_provenance():
    """Principle 3: the generator must not learn which lens or model raised anything."""
    results = [result(Lens.EVIDENCE, [issue(Category.UNCITED_CLAIM, Severity.MAJOR)])]
    defects = to_defects(results)
    assert len(defects) == 1
    dumped = defects[0].model_dump()
    assert "lens" not in dumped and "critic_identity" not in dumped
    assert set(dumped) <= {
        "locus",
        "category",
        "severity",
        "claim_span",
        "rationale",
        "instruction",
        "related_span",
        "citation_id",
        "expected_support",
        # D-writer-disputes: a bare boolean — "this task was independently reviewed and stands".
        # It carries no verdict prose and no provenance; anything beyond a bool
        # here needs a new decision entry.
        "adjudicated",
    }


def test_stylistic_issues_never_reach_the_generator_or_block():
    results = [result(Lens.LOGIC, [issue(Category.STYLISTIC, Severity.MINOR)])]
    assert to_defects(results) == []
    _, totals = tally(results)
    assert material_count(totals) == 0
    assert clean_records(results)  # stylistic-only is still a clean lens


def test_defects_are_ordered_by_severity_then_position():
    results = [
        result(
            Lens.LOGIC,
            [
                issue(Category.OVERSTATED_CLAIM, Severity.MAJOR, section=2, paragraph=1),
                issue(Category.CONTRADICTED_CLAIM, Severity.BLOCKING, section=2, paragraph=2),
            ],
        )
    ]
    order = [d.severity for d in to_defects(results)]
    assert order == [Severity.BLOCKING, Severity.MAJOR]


def test_identical_defects_from_two_lenses_are_deduplicated():
    dup = issue(Category.STYLISTIC, Severity.MINOR)
    a = issue(Category.OVERSTATED_CLAIM, Severity.MAJOR)
    results = [result(Lens.LOGIC, [a, dup]), result(Lens.LOGIC, [a])]
    assert len(to_defects(results)) == 1


def test_clean_record_requires_no_material_issue_in_the_lens():
    minor_only = result(Lens.COMPLETENESS, [issue(Category.UNCLEAR_STRUCTURE, Severity.MINOR)])
    material = result(Lens.LOGIC, [issue(Category.INVALID_INFERENCE, Severity.MINOR)])
    records = clean_records([minor_only, material])
    # invalid_inference is floored to major, so the logic lens is NOT clean
    assert [r.lens for r in records] == [Lens.COMPLETENESS]


def test_signal_signature_ignores_minor_noise():
    a = tally([result(Lens.LOGIC, [issue(Category.OVERSTATED_CLAIM, Severity.MAJOR)])])[0]
    b = tally(
        [
            result(
                Lens.LOGIC,
                [
                    issue(Category.OVERSTATED_CLAIM, Severity.MAJOR),
                    issue(Category.STYLISTIC, Severity.MINOR),
                ],
            )
        ]
    )[0]
    assert signal_signature(a) == signal_signature(b)


# --------------------------------------------- conceptual conflation (D-conceptual-conflation)


def test_conceptual_conflation_clamps_up_to_major_and_is_logic_only():
    clamped = clamp([issue(Category.CONCEPTUAL_CONFLATION, Severity.MINOR)])
    assert clamped[0].severity is Severity.MAJOR
    for lens in (Lens.EVIDENCE, Lens.COMPLETENESS):
        with pytest.raises(LensValidationError):
            validate_issue(lens, issue(Category.CONCEPTUAL_CONFLATION, Severity.MAJOR), STRUCTURE)


def test_conceptual_conflation_related_span_must_be_artifact_text():
    """D-conceptual-conflation puts the category in `IN_ARTIFACT_RELATED`, unlike the
    three bias categories: both poles of a substitution are passages the report
    contains, so `related_span` is the report's own statement of the concept being
    substituted away — not a description of a pattern. A critic that paraphrases it
    instead would forward words the report never used, carrying the apparent authority
    of quoted text, which is exactly what the verbatim check exists to stop."""
    base = issue(Category.CONCEPTUAL_CONFLATION, Severity.MAJOR)

    quoted = base.model_copy(update={"related_span": "A second claim, cited [1]."})
    validate_issue(Lens.LOGIC, quoted, STRUCTURE)  # elsewhere in the artifact: fine

    described = base.model_copy(
        update={"related_span": "the eligibility rule, as the report frames it earlier"}
    )
    with pytest.raises(LensValidationError):
        validate_issue(Lens.LOGIC, described, STRUCTURE)


def test_conceptual_conflation_may_omit_related_span_entirely():
    """The field stays optional, so a single sentence that fuses the two concepts with
    no second passage anywhere in the report is still reportable."""
    validate_issue(Lens.LOGIC, issue(Category.CONCEPTUAL_CONFLATION, Severity.MINOR), STRUCTURE)


# ------------------------------------------------------- social-bias categories (D-social-bias)


def test_bias_category_floors_clamp_up():
    clamped = clamp(
        [
            issue(Category.ONE_SIDED_SOURCING, Severity.MINOR),
            issue(Category.UNEXAMINED_PRESUPPOSITION, Severity.MINOR),
            issue(Category.LOADED_LANGUAGE, Severity.MINOR),
        ]
    )
    assert clamped[0].severity is Severity.MAJOR
    assert clamped[1].severity is Severity.MAJOR
    assert clamped[2].severity is Severity.MINOR  # floor is minor: proposal preserved


def test_loaded_language_escalation_survives_the_clamp():
    # docs/bias.md §3: the critic may propose major for pervasive framing and it sticks.
    escalated = clamp([issue(Category.LOADED_LANGUAGE, Severity.MAJOR)])
    assert escalated[0].severity is Severity.MAJOR


def test_bias_categories_are_lens_scoped():
    with pytest.raises(LensValidationError):
        validate_issue(Lens.LOGIC, issue(Category.ONE_SIDED_SOURCING, Severity.MAJOR), STRUCTURE)
    with pytest.raises(LensValidationError):
        validate_issue(
            Lens.EVIDENCE, issue(Category.UNEXAMINED_PRESUPPOSITION, Severity.MAJOR), STRUCTURE
        )
    with pytest.raises(LensValidationError):
        validate_issue(Lens.COMPLETENESS, issue(Category.LOADED_LANGUAGE, Severity.MINOR), STRUCTURE)


def test_bias_related_spans_may_describe_a_pattern_not_a_quote():
    """D-social-bias: the bias categories are excluded from IN_ARTIFACT_RELATED, because
    their related_span describes a pattern (a source cluster, the question's
    framing) rather than a second quotable span. An honest finding whose
    related_span is not artifact text must validate cleanly — on every lens."""
    cases = (
        (Lens.EVIDENCE, Category.ONE_SIDED_SOURCING),
        (Lens.LOGIC, Category.LOADED_LANGUAGE),
        (Lens.COMPLETENESS, Category.UNEXAMINED_PRESUPPOSITION),
    )
    for lens, category in cases:
        described = issue(category, Severity.MINOR).model_copy(
            update={"related_span": "the question's framing, which the report never examines"}
        )
        validate_issue(lens, described, STRUCTURE)  # must not raise


# ------------------------------------------------------------------ repair guidance


def test_a_misquote_carries_the_paragraph_it_should_have_quoted():
    """The message names the problem; the hint names the fix. Without the paragraph
    text a retry is a re-roll, which is what exhausted two production runs."""
    bad = issue(Category.UNCITED_CLAIM, Severity.MAJOR)
    bad = bad.model_copy(update={"claim_span": "a claim the report never makes"})

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, bad, STRUCTURE)

    assert "not a verbatim quote" in str(exc.value)
    assert "Intro paragraph making a claim." in exc.value.repair_hint()


def test_an_invented_locus_is_told_which_loci_exist():
    bad = issue(Category.UNCITED_CLAIM, Severity.MAJOR, section=9, paragraph=9)

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, bad, STRUCTURE)

    assert "S1.P1" in exc.value.repair_hint()


def test_a_category_out_of_scope_offers_no_hint():
    """Not every rejection is a recoverable slip: a critic raising another lens's
    category misread its brief, and there is no text to hand back that fixes that."""
    wrong = issue(Category.UNCITED_CLAIM, Severity.MAJOR)  # evidence category

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.COMPLETENESS, wrong, STRUCTURE)

    assert exc.value.repair_hint() == ""


def test_a_rejection_names_its_class_so_a_log_can_tell_them_apart():
    """`LensValidationError` alone is all a production log could say, and the three
    classes imply different things about the critic — a misread brief, an invented
    structural reference, a quoting slip."""
    misquote = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": "a claim the report never makes"}
    )
    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, misquote, STRUCTURE)
    assert exc.value.code is ViolationCode.SPAN_NOT_VERBATIM
    assert exc.value.diagnostics()["field"] == "claim_span"
    assert exc.value.diagnostics()["locus"] == "S1.P1"

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.COMPLETENESS, issue(Category.UNCITED_CLAIM, Severity.MAJOR), STRUCTURE)
    assert exc.value.code is ViolationCode.CATEGORY_OUT_OF_SCOPE

    invented = issue(Category.UNCITED_CLAIM, Severity.MAJOR, section=9, paragraph=9)
    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, invented, STRUCTURE)
    assert exc.value.code is ViolationCode.LOCUS_ABSENT


def test_the_span_fingerprint_separates_a_re_roll_from_a_search():
    """The question the failure message cannot answer: across repair attempts, did the
    critic re-emit the same rejected span, or a different one? Same normalized span ->
    same fingerprint; a genuinely different span -> a different one."""

    def fingerprint_of(span: str) -> str:
        bad = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
            update={"claim_span": span}
        )
        with pytest.raises(LensValidationError) as exc:
            validate_issue(Lens.EVIDENCE, bad, STRUCTURE)
        return exc.value.fingerprint()

    repeated = fingerprint_of("a claim the report never makes")
    assert fingerprint_of("a claim the report never makes") == repeated
    # Reformatting is not a different answer — the fingerprint folds what `_normalize`
    # folds, so a re-roll that only retypes its span still reads as a re-roll.
    assert fingerprint_of("  A CLAIM   the report  never makes ") == repeated
    assert fingerprint_of("a different invention entirely") != repeated
    assert len(repeated) == 8


def test_a_rejected_span_never_reaches_the_error_message():
    """RA-016. `critique_once` puts this message into `LensResult.failure_reason`, which
    is persisted and logged at WARNING — both outside the 0700 run tree. The span may
    travel in the repair hint, which stays inside the run; it must not travel here."""
    secret = "a claim the report never makes"
    bad = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": secret}
    )

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, bad, STRUCTURE)

    assert secret not in str(exc.value)
    assert secret not in " ".join(exc.value.diagnostics().values())
    # The hint hands back the *source* text the span should have come from, never the
    # rejected span itself — so there is no path from here to a log either.
    assert secret not in exc.value.repair_hint()


def test_typographic_punctuation_does_not_make_an_honest_quote_a_misquote():
    report = "# T\n\nThe agency’s 2015 update — 0.7 mg/L — still stands…\n"
    structure = report_mod.parse(report)
    retyped = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": "The agency's 2015 update - 0.7 mg/L - still stands..."}
    )

    validate_issue(Lens.EVIDENCE, retyped, structure)  # does not raise
