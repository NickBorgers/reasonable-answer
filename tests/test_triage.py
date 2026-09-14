"""Triage: mechanical floors, fail-closed validation, depersonalization, clean records."""

from __future__ import annotations

import pytest

from reasonable_answer import report as report_mod
from reasonable_answer.schemas import (
    MAX_CITATION_ID,
    MAX_INSTRUCTION,
    MAX_RATIONALE,
    MAX_SPAN,
    LensResult,
    RawIssue,
    StructuralRef,
)
from reasonable_answer.taxonomy import LENS_CATEGORIES, Category, Lens, Severity
from reasonable_answer.triage import (
    _LABEL_MAX,
    LensValidationError,
    ViolationCode,
    clamp,
    clean_records,
    distinct_issues,
    material_count,
    mechanical_bibliography_issues,
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
    # The source text rides `repair_excerpt()`, not the hint: it is report text, and the
    # prompt composer fences it as untrusted data (RA-010). The hint stays
    # validator-authored instruction only.
    assert "Intro paragraph making a claim." in exc.value.repair_excerpt()
    assert "Intro paragraph making a claim." not in exc.value.repair_hint()
    assert "character-for-character" in exc.value.repair_hint()


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
    assert exc.value.repair_excerpt() == ""


def test_a_rejection_names_its_class_so_a_log_can_tell_them_apart():
    """`LensValidationError` alone cannot distinguish the four rejection classes."""
    misquote = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": "a claim the report never makes"}
    )
    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, misquote, STRUCTURE)
    assert exc.value.code is ViolationCode.SPAN_NOT_VERBATIM
    assert exc.value.diagnostics(b"k" * 32)["field"] == "claim_span"
    assert exc.value.diagnostics(b"k" * 32)["locus"] == "S1.P1"

    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.COMPLETENESS, issue(Category.UNCITED_CLAIM, Severity.MAJOR), STRUCTURE)
    assert exc.value.code is ViolationCode.CATEGORY_OUT_OF_SCOPE

    invented = issue(Category.UNCITED_CLAIM, Severity.MAJOR, section=9, paragraph=9)
    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, invented, STRUCTURE)
    assert exc.value.code is ViolationCode.LOCUS_ABSENT

    empty = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": "*"}
    )
    with pytest.raises(LensValidationError) as exc:
        validate_issue(Lens.EVIDENCE, empty, STRUCTURE)
    assert exc.value.code is ViolationCode.SPAN_EMPTY
    assert exc.value.diagnostics(b"k" * 32)["field"] == "claim_span"
    assert exc.value.diagnostics(b"k" * 32)["locus"] == "S1.P1"


def test_the_span_fingerprint_separates_a_re_roll_from_a_search():
    """The question the failure message cannot answer: across repair attempts, did the
    critic re-emit the same rejected span, or a different one? Same normalized span ->
    same fingerprint; a genuinely different span -> a different one."""

    key = b"k" * 32

    def fingerprint_of(span: str, fingerprint_key: bytes = key) -> str:
        bad = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
            update={"claim_span": span}
        )
        with pytest.raises(LensValidationError) as exc:
            validate_issue(Lens.EVIDENCE, bad, STRUCTURE)
        return exc.value.fingerprint(fingerprint_key)

    repeated = fingerprint_of("a claim the report never makes")
    assert fingerprint_of("a claim the report never makes") == repeated
    # Reformatting is not a different answer — the fingerprint folds what `_normalize`
    # folds, so a re-roll that only retypes its span still reads as a re-roll.
    assert fingerprint_of("  A CLAIM   the report  never makes ") == repeated
    assert fingerprint_of("a different invention entirely") != repeated
    assert fingerprint_of("a claim the report never makes", b"z" * 32) != repeated
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
    assert secret not in " ".join(exc.value.diagnostics(b"k" * 32).values())
    # The hint and excerpt hand back the *source* text the span should have come from,
    # never the rejected span itself — so there is no path from here to a log either.
    assert secret not in exc.value.repair_hint()
    assert secret not in exc.value.repair_excerpt()


def test_typographic_punctuation_does_not_make_an_honest_quote_a_misquote():
    report = "# T\n\nThe agency’s 2015 update — 0.7 mg/L — still stands…\n"
    structure = report_mod.parse(report)
    retyped = issue(Category.UNCITED_CLAIM, Severity.MAJOR).model_copy(
        update={"claim_span": "The agency's 2015 update - 0.7 mg/L - still stands..."}
    )

    validate_issue(Lens.EVIDENCE, retyped, structure)  # does not raise


# --------------------------------------------------- bibliography integrity (D-bibliography-integrity)


CLEAN_BIBLIOGRAPHY = """# Fluoride

## Findings

Community water fluoridation is set at 0.7 mg/L in the United States [1].

Skeletal effects appear above 1.5 mg/L [2].

## Sources

[1] CDC. Community Water Fluoridation. https://www.cdc.gov/fluoridation/index.html

[2] WHO (2004). Fluoride in Drinking-water. https://www.who.int/publications/i/item/9241563192
"""


def bibliography_issues(report_text: str, sources=None, **kwargs):
    return mechanical_bibliography_issues(
        report_text, report_mod.parse(report_text), sources, **kwargs
    )


def owning_lens(category: Category) -> Lens:
    """The lens whose closed schema the category belongs to."""
    return next(lens for lens, cats in LENS_CATEGORIES.items() if category in cats)


def assert_quotable(issues: list[RawIssue], report_text: str) -> None:
    """Every minted finding would survive `validate_issue`.

    Production bypasses it — the fields are pipeline-authored, exactly as
    `mechanical_citation_issues`' are — so this is the check that a change here cannot
    quietly mint a finding the writer could not locate.
    """
    structure = report_mod.parse(report_text)
    assert issues
    for minted in issues:
        validate_issue(owning_lens(minted.category), minted, structure)


def test_a_clean_bibliography_mints_nothing():
    assert bibliography_issues(CLEAN_BIBLIOGRAPHY) == []


def test_a_marker_with_no_entry_is_an_uncited_claim():
    report = CLEAN_BIBLIOGRAPHY.replace(
        "Skeletal effects appear above 1.5 mg/L [2].",
        "Skeletal effects appear above 1.5 mg/L [2]. Enforcement rests on the DEA schedule [11].",
    )
    issues = bibliography_issues(report)
    assert [i.category for i in issues] == [Category.UNCITED_CLAIM]
    dangling = issues[0]
    assert dangling.severity is Severity.MAJOR
    assert dangling.citation_id == "[11]"
    assert "[11]" in dangling.claim_span
    assert_quotable(issues, report)


def test_a_long_marker_number_is_bounded_in_the_minted_issue():
    number = "9" * 150
    report = CLEAN_BIBLIOGRAPHY.replace(
        "Skeletal effects appear above 1.5 mg/L [2].",
        f"Skeletal effects appear above 1.5 mg/L [2]. Another claim [{number}].",
    )
    issues = bibliography_issues(report)
    dangling = next(i for i in issues if i.category is Category.UNCITED_CLAIM)
    assert dangling.citation_id == f"[{number}]"[:_LABEL_MAX]


def test_a_range_marker_is_expanded_before_the_entry_is_looked_for():
    report = CLEAN_BIBLIOGRAPHY.replace(
        "set at 0.7 mg/L in the United States [1]", "set at 0.7 mg/L in the United States [1-3]"
    )
    issues = bibliography_issues(report)
    assert [i.citation_id for i in issues if i.category is Category.UNCITED_CLAIM] == ["[3]"]


def test_an_entry_nothing_cites_is_an_orphan():
    report = CLEAN_BIBLIOGRAPHY + (
        "\n[3] European Commission SCHER (2019). "
        "https://ec.europa.eu/health/scientific_committees/scher_o_182.pdf\n"
    )
    issues = bibliography_issues(report)
    assert [i.category for i in issues] == [Category.UNCLEAR_STRUCTURE]
    orphan = issues[0]
    assert orphan.severity is Severity.MINOR
    assert orphan.citation_id == "[3]"
    assert "SCHER" in orphan.claim_span
    assert_quotable(issues, report)


def test_a_long_entry_number_is_bounded_in_the_minted_issue():
    number = "9" * 150
    report = CLEAN_BIBLIOGRAPHY + f"\n[{number}] Long-number source. https://example.org/long\n"
    orphan = next(i for i in bibliography_issues(report) if i.category is Category.UNCLEAR_STRUCTURE)
    assert orphan.citation_id == f"[{number}]"[:_LABEL_MAX]


def test_one_url_listed_twice_is_a_duplicate_entry():
    report = CLEAN_BIBLIOGRAPHY.replace(
        "Skeletal effects appear above 1.5 mg/L [2].",
        "Skeletal effects appear above 1.5 mg/L [2], reaffirmed in 2022 [3].",
    ) + "\n[3] WHO (2022). Fluoride in Drinking-water. https://www.who.int/publications/i/item/9241563192\n"
    issues = bibliography_issues(report)
    assert [i.category for i in issues] == [Category.UNCLEAR_STRUCTURE]
    duplicate = issues[0]
    assert duplicate.severity is Severity.MINOR
    assert duplicate.citation_id == "[2], [3]"
    # The writer is also told never to renumber (D-writer-citation-continuity); a fix task
    # that asked for renumbering would hand it two opposite instructions.
    assert "lower-numbered" in duplicate.instruction
    assert "Do not renumber" in duplicate.instruction
    assert_quotable(issues, report)


def test_a_report_with_no_sources_section_mints_nothing():
    assert bibliography_issues(REPORT) == []


UNMARKED_BIBLIOGRAPHY = CLEAN_BIBLIOGRAPHY.replace(" [1]", "").replace(" [2]", "")


def test_a_bibliography_the_body_never_cites_is_one_uncited_claim():
    """Every entry would be an orphan; the defect is one fact — no claim can be traced to
    a source — so it is exactly one `uncited_claim` at the first body sentence, with no
    per-entry findings beside it (D-uncited-bibliography)."""
    issues = bibliography_issues(UNMARKED_BIBLIOGRAPHY)
    assert len(issues) == 1
    finding = issues[0]
    assert finding.category is Category.UNCITED_CLAIM
    assert finding.severity is Severity.MAJOR
    assert finding.locus == StructuralRef(section=2, paragraph=1)
    assert finding.claim_span == "Community water fluoridation is set at 0.7 mg/L in the United States."
    assert finding.citation_id is None
    assert "lists 2 entries" in finding.rationale
    assert "No new source is needed" in finding.instruction
    assert not [i for i in issues if i.category is Category.UNCLEAR_STRUCTURE]
    assert_quotable(issues, UNMARKED_BIBLIOGRAPHY)


def test_an_entrys_own_marker_inside_sources_is_not_a_citation():
    report = UNMARKED_BIBLIOGRAPHY.replace(
        "[2] WHO (2004). Fluoride in Drinking-water.",
        "[2] WHO (2004). Fluoride in Drinking-water, revising [1].",
    )
    issues = bibliography_issues(report)
    assert [(i.category, i.locus.section) for i in issues] == [(Category.UNCITED_CLAIM, 2)]


@pytest.mark.parametrize("listing", ["None.", "No sources were consulted.", "- None found"])
def test_a_sources_section_that_lists_no_reference_mints_nothing(listing):
    report = "# T\n\nA claim with nothing behind it.\n\n## Sources\n\n" + listing + "\n"
    assert bibliography_issues(report) == []


def test_one_body_marker_brings_back_the_per_entry_checks():
    report = CLEAN_BIBLIOGRAPHY.replace(" [2]", "")
    issues = bibliography_issues(report)
    assert [(i.category, i.citation_id) for i in issues] == [(Category.UNCLEAR_STRUCTURE, "[2]")]


def test_an_unnumbered_bibliography_with_an_address_still_counts_as_references():
    report = (
        "# T\n\nA claim with nothing behind it.\n\n## Sources\n\n"
        "- CDC. Community Water Fluoridation. https://www.cdc.gov/fluoridation/index.html\n"
    )
    assert [i.category for i in bibliography_issues(report)] == [Category.UNCITED_CLAIM]


def test_a_numbered_bibliography_without_an_address_still_counts_as_references():
    report = "# T\n\nA claim with nothing behind it.\n\n## Sources\n\n[1] Author, Title, 2020.\n"
    assert [i.category for i in bibliography_issues(report)] == [Category.UNCITED_CLAIM]


def test_a_long_emphasized_first_sentence_stays_quotable():
    sentence = "**Fluoridation " + "at community scale " * 40 + "is set at 0.7 mg/L.**"
    report = UNMARKED_BIBLIOGRAPHY.replace(
        "Community water fluoridation is set at 0.7 mg/L in the United States.",
        sentence + " A second sentence follows.",
    )
    issues = bibliography_issues(report)
    assert len(issues) == 1
    assert len(issues[0].claim_span) <= MAX_SPAN
    assert len(issues[0].rationale) <= MAX_RATIONALE
    assert len(issues[0].instruction) <= MAX_INSTRUCTION
    assert_quotable(issues, report)


def test_a_paragraph_with_no_quotable_text_is_passed_over_for_the_next():
    report = UNMARKED_BIBLIOGRAPHY.replace("## Findings\n\n", "## Findings\n\n**\n\n")
    issues = bibliography_issues(report)
    assert [i.locus for i in issues] == [StructuralRef(section=2, paragraph=2)]
    assert_quotable(issues, report)


def test_a_body_with_no_quotable_sentence_mints_nothing():
    report = "# T\n\n**\n\n## Sources\n\n[1] A source. https://example.org/one\n"
    assert bibliography_issues(report) == []


def test_two_critics_of_one_lens_count_an_uncited_bibliography_once():
    minted = bibliography_issues(UNMARKED_BIBLIOGRAPHY)
    results = [
        result(Lens.EVIDENCE, list(minted), critic="vendor-x/critic"),
        result(Lens.EVIDENCE, list(minted), critic="vendor-y/critic"),
    ]
    assert len(distinct_issues(results)) == 1
    per_category, totals = tally(results)
    assert per_category["uncited_claim"].major == 1
    assert totals.major == 1


def test_a_failed_evidence_review_contributes_no_uncited_bibliography_finding():
    minted = bibliography_issues(UNMARKED_BIBLIOGRAPHY)
    assert minted
    assert distinct_issues([result(Lens.EVIDENCE, list(minted), failed=True)]) == []


def test_the_entry_budget_bounds_the_work():
    report = CLEAN_BIBLIOGRAPHY + "\n[3] An orphan. https://example.org/three\n"
    assert bibliography_issues(report, limit=2) == []
    assert len(bibliography_issues(report, limit=3)) == 1


def test_the_entry_budget_does_not_turn_later_entries_into_missing_entries():
    report = CLEAN_BIBLIOGRAPHY.replace(
        "Skeletal effects appear above 1.5 mg/L [2].",
        "Skeletal effects appear above 1.5 mg/L [2]. A later source supports this [3].",
    ) + "\n[3] Later source. https://example.org/three\n"
    assert bibliography_issues(report, limit=2) == []


def test_two_critics_of_one_lens_report_a_bibliography_finding_once():
    """The findings are derived from the artifact, so a depth-2 slate mints them twice;
    `_issue_key` has to collapse them or the totals double (D-front-loaded-depth)."""
    report = CLEAN_BIBLIOGRAPHY + "\n[3] An orphan entry. https://example.org/three\n"
    minted = bibliography_issues(report)
    results = [
        result(Lens.EVIDENCE, list(minted), critic="vendor-x/critic"),
        result(Lens.EVIDENCE, list(minted), critic="vendor-y/critic"),
    ]
    assert len(distinct_issues(results)) == len(minted)
    per_category, totals = tally(results)
    assert per_category["unclear_structure"].minor == 1
    assert totals.minor == 1


def test_a_failed_evidence_review_contributes_no_bibliography_findings():
    report = CLEAN_BIBLIOGRAPHY + "\n[3] An orphan entry. https://example.org/three\n"
    minted = bibliography_issues(report)
    assert distinct_issues([result(Lens.EVIDENCE, list(minted), failed=True)]) == []


def test_every_minted_finding_is_at_its_own_severity_floor():
    """Minted at the floor, so the clamp is a no-op — the direction RC-005 requires."""
    report = (
        CLEAN_BIBLIOGRAPHY.replace(
            "Skeletal effects appear above 1.5 mg/L [2].",
            "Skeletal effects appear above 1.5 mg/L [2], and elsewhere [4].",
        )
        + "\n[3] An orphan entry. https://example.org/three\n"
        + "[5] A duplicate. https://www.cdc.gov/fluoridation/index.html\n"
    )
    minted = bibliography_issues(report)
    assert {i.category for i in minted} == {Category.UNCITED_CLAIM, Category.UNCLEAR_STRUCTURE}
    assert len(minted) == 4  # dangling [4], orphan [3], orphan [5], duplicate [5]
    assert clamp(minted) == minted

    # The one finding for a bibliography the body never cites (D-uncited-bibliography).
    unmarked = bibliography_issues(UNMARKED_BIBLIOGRAPHY)
    assert [i.category for i in unmarked] == [Category.UNCITED_CLAIM]
    assert clamp(unmarked) == unmarked


def test_two_long_numbered_entries_under_one_url_still_mint_a_bounded_duplicate():
    """The overflow the review reproduced: two entries numbered with 120 digits each under
    one URL used to raise `ValidationError` from `rationale` (400-char cap) — an uncaught
    exception that escaped `_critique_one` and aborted the critique node instead of
    failing anything closed. Every minted field is clipped on construction now."""
    a, b = "1" * 120, "2" * 120
    report = (
        "# T\n\nA claim [" + a + "] and again [" + b + "].\n\n## Sources\n\n"
        "[" + a + "] First listing. https://example.org/same\n"
        "[" + b + "] Second listing. https://example.org/same\n"
    )
    issues = bibliography_issues(report)
    duplicates = [i for i in issues if i.category is Category.UNCLEAR_STRUCTURE]
    assert len(duplicates) == 1
    assert len(duplicates[0].rationale) <= MAX_RATIONALE
    assert len(duplicates[0].instruction) <= MAX_INSTRUCTION
    assert len(duplicates[0].citation_id) <= MAX_CITATION_ID
    assert_quotable(issues, report)


@pytest.mark.parametrize("digits", [1, 40, 120, 400])
def test_no_entry_number_length_can_make_minting_raise(digits):
    """Dangling marker, orphan and duplicate, each with a number of every length the
    marker parser accepts: construction never raises and every field is within bounds."""
    number = "7" * digits
    report = (
        "# T\n\nA claim [" + number + "] and another [9].\n\n## Sources\n\n"
        "[" + number + "] Listed. https://example.org/a\n"
        "[" + number + "] Listed again. https://example.org/a\n"
        "[8] Never cited. https://example.org/b\n"
    )
    minted = bibliography_issues(report)
    assert {i.category for i in minted} == {Category.UNCITED_CLAIM, Category.UNCLEAR_STRUCTURE}
    for issue_ in minted:
        assert len(issue_.rationale) <= MAX_RATIONALE
        assert len(issue_.instruction) <= MAX_INSTRUCTION
        assert len(issue_.citation_id) <= MAX_CITATION_ID
