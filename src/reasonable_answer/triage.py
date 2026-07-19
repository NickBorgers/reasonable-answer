"""Triage — mechanical, no LLM.

Takes this tick's per-lens results and produces the two outputs that leave the
critique stage:

* a **DefectList** for the next writer — depersonalized fix-tasks, no lens, no
  model, no verdict language;
* an **OrchestratorView** for the blind referee — counts only.

Also mints the per-lens clean records that acceptance rests on.

Fail-closed is enforced *upstream* of counting: if any lens failed, its issues are
never mixed into the counts (controller rule 2 re-critiques instead).
"""

from __future__ import annotations

import re

from .report import Structure
from .schemas import (
    CleanRecord,
    Defect,
    LensResult,
    OrchestratorView,
    RawIssue,
    SeverityCounts,
)
from .taxonomy import (
    LENS_CATEGORIES,
    Category,
    Lens,
    Severity,
    clamp_to_floor,
    is_material,
)


class LensValidationError(ValueError):
    """An issue violated the closed schema for its lens. Fails the whole lens."""


#: Categories whose `related_span` must itself be text from the artifact.
IN_ARTIFACT_RELATED = frozenset(
    {Category.CONTRADICTED_CLAIM, Category.INVALID_INFERENCE, Category.OVERSTATED_CLAIM}
)


def _normalize(text: str) -> str:
    """Whitespace- and case-insensitive, with markdown emphasis stripped, so an
    honest quote survives reformatting while an invented one does not."""
    stripped = re.sub(r"[*_`]+", "", text)
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def validate_issue(
    lens: Lens, issue: RawIssue, structure: Structure, require_verbatim_spans: bool = True
) -> None:
    """Fail-closed validation. Anything off-schema fails the lens, never a silent drop."""
    if issue.category not in LENS_CATEGORIES[lens]:
        raise LensValidationError(
            f"category '{issue.category.value}' is out of scope for lens '{lens.value}'"
        )
    if not structure.contains(issue.locus):
        raise LensValidationError(
            f"locus {issue.locus} does not exist in the artifact under review"
        )
    if require_verbatim_spans:
        # The quote fields cross to the writer carrying apparent authority ("here is
        # the offending text"). Anchoring them to the artifact means a critic can
        # only forward words the report already contains.
        paragraph = structure.text_at(issue.locus) or ""
        _require_quote(
            issue.claim_span, _normalize(paragraph), "claim_span", issue.locus, "cited paragraph"
        )
        if issue.related_span is not None and issue.category in IN_ARTIFACT_RELATED:
            # For a contradiction or a bad inference, both halves are in the report,
            # so the second quote is checked against the whole artifact. For the
            # evidence categories `related_span` describes the *source* — text that
            # by definition is not in the artifact — so requiring a quote there
            # would fail every honest citation finding.
            _require_quote(
                issue.related_span,
                _normalize(structure.full_text),
                "related_span",
                issue.locus,
                "artifact",
            )


def _require_quote(span: str, haystack: str, field: str, locus, scope: str) -> None:
    needle = _normalize(span)
    if not needle:
        # "*" or "``" satisfy the schema's min_length but normalize away, and the
        # empty string is a substring of everything — an issue anchored to nothing.
        raise LensValidationError(f"{field} at {locus} contains no quotable text")
    if needle not in haystack:
        raise LensValidationError(
            f"{field} at {locus} is not a verbatim quote from the {scope}"
        )


def clamp(issues: list[RawIssue]) -> list[RawIssue]:
    """Apply mechanical severity floors — critics may escalate, never downgrade."""
    out: list[RawIssue] = []
    for issue in issues:
        clamped = issue.model_copy(update={"severity": clamp_to_floor(issue.category, issue.severity)})
        out.append(clamped)
    return out


def to_defects(results: list[LensResult]) -> list[Defect]:
    """The generator-facing handoff. Provenance (lens, model) is dropped here —
    it lives on in the audit store only (principle 3)."""
    defects: list[Defect] = []
    seen: set[tuple] = set()
    for result in sorted(results, key=lambda r: r.lens.value):
        if result.failed:
            continue
        for issue in clamp(result.issues):
            if issue.category is Category.STYLISTIC:
                continue  # never blocks; not worth a rewrite instruction
            key = (issue.locus.section, issue.locus.paragraph, issue.category, issue.claim_span)
            if key in seen:
                continue
            seen.add(key)
            defects.append(
                Defect(
                    locus=issue.locus,
                    category=issue.category,
                    severity=issue.severity,
                    claim_span=issue.claim_span,
                    rationale=issue.rationale,
                    instruction=issue.instruction,
                    related_span=issue.related_span,
                    citation_id=issue.citation_id,
                    expected_support=issue.expected_support,
                )
            )
    order = {Severity.BLOCKING: 0, Severity.MAJOR: 1, Severity.MINOR: 2}
    defects.sort(key=lambda d: (order[d.severity], d.locus.section, d.locus.paragraph))
    return defects


def tally(results: list[LensResult]) -> tuple[dict[str, SeverityCounts], SeverityCounts]:
    per_category: dict[str, SeverityCounts] = {}
    totals = SeverityCounts()
    for result in results:
        if result.failed:
            continue  # partial counts are never used (rule 2)
        for issue in clamp(result.issues):
            if issue.category is Category.STYLISTIC:
                # "ignored for convergence" has to mean ignored: counted here, a
                # stylistic nitpick could authorize a polish rewrite (rule 9) and
                # risk a substantive regression for a finding declared irrelevant.
                continue
            bucket = per_category.setdefault(issue.category.value, SeverityCounts())
            setattr(bucket, issue.severity.value, getattr(bucket, issue.severity.value) + 1)
            setattr(totals, issue.severity.value, getattr(totals, issue.severity.value) + 1)
    return per_category, totals


def material_count(totals: SeverityCounts) -> int:
    return totals.blocking + totals.major


def clean_records(results: list[LensResult]) -> list[CleanRecord]:
    """A per-lens clean record exists only when that lens *completed* and found no
    material issue in its own categories (RC-001)."""
    records: list[CleanRecord] = []
    for result in results:
        if result.failed:
            continue
        # A stylistic finding is ignored for convergence, so it must not withhold
        # clearance either — even if the critic escalated its severity.
        blocking_issues = [
            i for i in clamp(result.issues) if i.category is not Category.STYLISTIC
        ]
        if any(is_material(i.severity) for i in blocking_issues):
            continue
        records.append(
            CleanRecord(
                artifact_hash=result.artifact_hash,
                lens=result.lens,
                critic_identity=result.critic_identity,
                artifact_author_identity=result.artifact_author_identity,
            )
        )
    return records


def build_view(
    *,
    per_category: dict[str, SeverityCounts],
    totals: SeverityCounts,
    delta_material_vs_prev: int,
    lenses_failed: int,
    round_no: int,
    min_ticks: int,
    hard_cap: int,
    roster_size: int,
    lens_cleared: dict[Lens, int],
    acceptance: str,
    polish_used: int,
    polish_cap: int,
    stagnation_count: int,
    cycle_detected: bool,
) -> OrchestratorView:
    """The projection that makes the orchestrator's blindness structural (RA-002):
    it is built *outside* any node, and artifact-bearing state has no path into it."""
    return OrchestratorView(
        counts=per_category,
        totals=totals,
        delta_material_vs_prev=delta_material_vs_prev,
        lenses_failed=lenses_failed,
        round=round_no,
        min_ticks=min_ticks,
        hard_cap=hard_cap,
        roster_size=roster_size,
        lens_cleared={lens.value: n for lens, n in lens_cleared.items()},
        acceptance=acceptance,  # type: ignore[arg-type]
        polish_used=polish_used,
        polish_cap=polish_cap,
        stagnation_count=stagnation_count,
        cycle_detected=cycle_detected,
    )


def signal_signature(per_category: dict[str, SeverityCounts]) -> tuple:
    """The stagnation key: the per-category {blocking, major} multiset."""
    return tuple(
        sorted((cat, c.blocking, c.major) for cat, c in per_category.items() if c.blocking or c.major)
    )
