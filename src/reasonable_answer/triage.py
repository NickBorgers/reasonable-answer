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
    MAX_SPAN,
    CleanRecord,
    Defect,
    LensResult,
    OrchestratorView,
    RawIssue,
    SeverityCounts,
    StructuralRef,
)
from .taxonomy import (
    LENS_CATEGORIES,
    Category,
    Lens,
    Severity,
    clamp_to_floor,
    counts_for_convergence,
)

#: How much of the source text a repair hint may quote back. A paragraph is normally
#: well under this; the cap exists so a pathological block cannot crowd out the rest
#: of the repair prompt.
REPAIR_HINT_MAX_CHARS = 1200


class LensValidationError(ValueError):
    """An issue violated the closed schema for its lens. Fails the whole lens.

    Carries the text the offending field *should* have been drawn from, because the
    message alone ("claim_span at S2.P1 is not a verbatim quote") names the problem
    without naming the fix: a critic told only that its quote was wrong has no more
    information than it had the first time, and re-rolls the same failure. Whoever is
    retrying the call reads `repair_hint()` and hands the source text back, which is
    what turns a retry into a repair.
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self._hint = hint

    def repair_hint(self) -> str:
        """The correction guidance, or "" when this violation has nothing concrete to
        offer (a category out of scope for the lens is a reading failure, not a
        recoverable slip)."""
        return self._hint


def _quote_hint(field: str, scope: str, source_text: str) -> str:
    """Guidance for a span that did not appear in its source: hand the source back."""
    excerpt = source_text[:REPAIR_HINT_MAX_CHARS]
    truncated = " (truncated)" if len(source_text) > REPAIR_HINT_MAX_CHARS else ""
    return (
        f"`{field}` must be copied character-for-character from the {scope} below"
        f"{truncated}. Choose a span that appears in it verbatim, or drop the issue.\n"
        f"---\n{excerpt}\n---"
    )


#: Categories whose `related_span` must itself be text from the artifact.
IN_ARTIFACT_RELATED = frozenset(
    {Category.CONTRADICTED_CLAIM, Category.INVALID_INFERENCE, Category.OVERSTATED_CLAIM}
)


#: Typographic characters a report carries but a model retyping a quote emits in their
#: ASCII form. Folding them is not a loosening of the check: it removes a difference
#: that is invisible to the reader and therefore cannot distinguish an honest quote
#: from an invented one. Citation markers like `[41]` are deliberately NOT stripped —
#: dropping them would let a span match text it does not actually appear in.
_PUNCTUATION_FOLD = str.maketrans(
    {
        "‘": "'",  # left single quote
        "’": "'",  # right single quote / apostrophe
        "‚": "'",
        "‛": "'",
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "„": '"',
        "′": "'",  # prime
        "″": '"',  # double prime
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "–": "-",  # en dash
        "—": "-",  # em dash
        "―": "-",  # horizontal bar
        "−": "-",  # minus sign
        " ": " ",  # non-breaking space
        " ": " ",  # figure space
        " ": " ",  # narrow no-break space
        "​": "",  # zero-width space
        "﻿": "",  # zero-width no-break space
        "…": "...",  # ellipsis
    }
)


def _normalize(text: str) -> str:
    """Whitespace- and case-insensitive, with markdown emphasis stripped and
    typographic punctuation folded to ASCII, so an honest quote survives reformatting
    while an invented one does not."""
    folded = text.translate(_PUNCTUATION_FOLD)
    stripped = re.sub(r"[*_`]+", "", folded)
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
            f"locus {issue.locus} does not exist in the artifact under review",
            # The markers are already in the prompt, but a critic that invented one has
            # demonstrably not read them off the artifact; listing the real ones is the
            # difference between a re-roll and a correction.
            hint=(
                "`locus` must name an existing [S<section>.P<paragraph>] marker. "
                f"The artifact has exactly these: {_loci_list(structure)}."
            ),
        )
    if require_verbatim_spans:
        # The quote fields cross to the writer carrying apparent authority ("here is
        # the offending text"). Anchoring them to the artifact means a critic can
        # only forward words the report already contains.
        paragraph = structure.text_at(issue.locus) or ""
        _require_quote(issue.claim_span, paragraph, "claim_span", issue.locus, "cited paragraph")
        if issue.related_span is not None and issue.category in IN_ARTIFACT_RELATED:
            # For a contradiction or a bad inference, both halves are in the report,
            # so the second quote is checked against the whole artifact. For the
            # evidence categories `related_span` describes the *source* — text that
            # by definition is not in the artifact — so requiring a quote there
            # would fail every honest citation finding.
            _require_quote(
                issue.related_span,
                structure.full_text,
                "related_span",
                issue.locus,
                "artifact",
            )


def _loci_list(structure: Structure) -> str:
    return ", ".join(f"S{p.section}.P{p.paragraph}" for p in structure.paragraphs) or "(none)"


def _require_quote(span: str, source_text: str, field: str, locus, scope: str) -> None:
    needle = _normalize(span)
    if not needle:
        # "*" or "``" satisfy the schema's min_length but normalize away, and the
        # empty string is a substring of everything — an issue anchored to nothing.
        raise LensValidationError(
            f"{field} at {locus} contains no quotable text",
            hint=_quote_hint(field, scope, source_text),
        )
    if needle not in _normalize(source_text):
        raise LensValidationError(
            f"{field} at {locus} is not a verbatim quote from the {scope}",
            hint=_quote_hint(field, scope, source_text),
        )


def _locate_url(url: str, structure: Structure) -> StructuralRef:
    """The locus of the paragraph that cites ``url``.

    The URL was pulled from the report's own ``## Sources`` section, so it is present;
    the last-paragraph fallback is defensive — a fabricated citation must never be
    dropped for want of an anchor, and Sources is conventionally the final section.
    """
    for p in structure.paragraphs:
        if url in p.text:
            return StructuralRef(section=p.section, paragraph=p.paragraph)
    if structure.paragraphs:
        last = structure.paragraphs[-1]
        return StructuralRef(section=last.section, paragraph=last.paragraph)
    return StructuralRef(section=0, paragraph=0)


def mechanical_citation_issues(sources: list, structure: Structure) -> list[RawIssue]:
    """``fabricated_citation`` findings a fetch *settles*, not that a critic judges (D-notfound-fabrication).

    A cited URL that returns a definitive not-found (HTTP 404 / 410 Gone) does not
    resolve — the page does not exist. That is the one fetch outcome that establishes
    ``fabricated_citation`` as fact rather than plausibility (docs/convergence.md), so it
    is raised here, mechanically, and never depends on a critic model electing to make
    it. Every other failure class (403, a connection error/timeout, an unreadable content
    type, an empty body) is "could not read", not "absent", and is deliberately excluded.

    Mechanically minted, so this deliberately bypasses ``validate_issue`` (a verbatim
    span check aimed at model-authored fields): the ``claim_span`` is the cited URL and
    ``severity`` is already its category floor, which the clamp keeps.
    """
    issues: list[RawIssue] = []
    for source in sources:
        if not source.unresolvable:
            continue
        issues.append(
            RawIssue(
                category=Category.FABRICATED_CITATION,
                severity=Severity.BLOCKING,
                locus=_locate_url(source.url, structure),
                claim_span=source.url[:MAX_SPAN],
                rationale=(
                    f"The cited URL returned HTTP {source.status}: the page does not "
                    "exist, so the citation cannot be what it claims on its face."
                ),
                instruction=(
                    "Remove this citation or replace it with a source that resolves and "
                    "supports the claim; do not invent a URL."
                ),
            )
        )
    return issues


def clamp(issues: list[RawIssue]) -> list[RawIssue]:
    """Apply mechanical severity floors — critics may escalate, never downgrade."""
    out: list[RawIssue] = []
    for issue in issues:
        clamped = issue.model_copy(update={"severity": clamp_to_floor(issue.category, issue.severity)})
        out.append(clamped)
    return out


def to_defects(
    results: list[LensResult], overruled: set[tuple[str, str]] | None = None
) -> list[Defect]:
    """The generator-facing handoff. Provenance (lens, model) is dropped here —
    it lives on in the audit store only (principle 3).

    `overruled` holds registry keys (category, normalized claim_span) of defects a
    writer disputed and lost (D-writer-disputes): those are marked `adjudicated=True` so the next
    writer is told the task was independently reviewed and stands."""
    defects: list[Defect] = []
    seen: set[tuple] = set()
    overruled = overruled or set()
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
                    adjudicated=(issue.category.value, _normalize(issue.claim_span))
                    in overruled,
                )
            )
    order = {Severity.BLOCKING: 0, Severity.MAJOR: 1, Severity.MINOR: 2}
    defects.sort(key=lambda d: (order[d.severity], d.locus.section, d.locus.paragraph))
    return defects


def suppress(
    results: list[LensResult], keys: set[tuple[str, str]]
) -> tuple[list[LensResult], list[dict]]:
    """Drop issues matching upheld adjudication keys (D-writer-disputes) — applied ONCE, before
    `tally`, `clean_records`, `to_defects` and `signal_signature`, so counts,
    clearance and fix-tasks all see the same filtered stream.

    Failed lenses pass through untouched: suppression must never turn an
    incomplete review into a countable one (rule 2 semantics). Every suppression
    is returned for logging — a silent suppression would be an invisible hole in
    the audit trail."""
    if not keys:
        return results, []
    filtered: list[LensResult] = []
    suppressed: list[dict] = []
    for result in results:
        if result.failed:
            filtered.append(result)
            continue
        kept: list[RawIssue] = []
        for issue in result.issues:
            key = (issue.category.value, _normalize(issue.claim_span))
            if key in keys:
                suppressed.append(
                    {
                        "lens": result.lens.value,
                        "category": issue.category.value,
                        "locus": str(issue.locus),
                    }
                )
            else:
                kept.append(issue)
        filtered.append(result.model_copy(update={"issues": kept}))
    return filtered, suppressed


def defect_provenance(results: list[LensResult]) -> dict[str, list[str]]:
    """Registry key -> sorted raising critic identities, for surviving material
    issues. Audit-side only: consumed by arbiter *eligibility* (deterministic
    code), never by any prompt (D-writer-disputes)."""
    provenance: dict[str, set[str]] = {}
    for result in results:
        if result.failed:
            continue
        for issue in clamp(result.issues):
            if not counts_for_convergence(issue.category, issue.severity):
                continue
            cat, span = issue.category.value, _normalize(issue.claim_span)
            provenance.setdefault(f"{cat}|{span}", set()).add(result.critic_identity)
    return {k: sorted(v) for k, v in provenance.items()}


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
        # clearance either — even if the critic escalated its severity. That exclusion
        # lives in `counts_for_convergence`, which the audition grader shares.
        if any(counts_for_convergence(i.category, i.severity) for i in clamp(result.issues)):
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
