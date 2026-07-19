"""Triage: mechanical floors, fail-closed validation, depersonalization, clean records."""

from __future__ import annotations

import pytest

from reasonable_answer import report as report_mod
from reasonable_answer.schemas import LensResult, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity
from reasonable_answer.triage import (
    LensValidationError,
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
