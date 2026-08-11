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

import hashlib
import re
from enum import Enum

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
    LENSES,
    SEVERITY_RANK,
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


class ViolationCode(str, Enum):
    """Why a lens validation failed, as a label bounded enough to log.

    The classes differ in what they imply about the critic: a category out of scope is a
    reading failure, an absent locus is an invented structural reference, and a span that
    is not verbatim is a quoting slip. The message names the field and the locus but is
    deliberately content-free, so without these a production log can only say
    `LensValidationError` and cannot tell the three apart.
    """

    CATEGORY_OUT_OF_SCOPE = "category_out_of_scope"
    LOCUS_ABSENT = "locus_absent"
    SPAN_EMPTY = "span_empty"
    SPAN_NOT_VERBATIM = "span_not_verbatim"


class LensValidationError(ValueError):
    """An issue violated the closed schema for its lens. Fails the whole lens.

    Carries the text the offending field *should* have been drawn from, because the
    message alone ("claim_span at S2.P1 is not a verbatim quote") names the problem
    without naming the fix: a critic told only that its quote was wrong has no more
    information than it had the first time, and re-rolls the same failure. Whoever is
    retrying the call reads `repair_hint()` and hands the source text back, which is
    what turns a retry into a repair.

    The rejected text itself is held privately and never reaches `str(self)`. That is
    load-bearing rather than incidental: `critique.critique_once` puts the final message
    into `LensResult.failure_reason`, which the graph persists into the critique event,
    and the same message is logged at WARNING — both outside the 0700 run tree (RA-016).
    `fingerprint()` is what a log gets instead, keyed to one repair loop so the value
    cannot be correlated across calls or tested against guessed report text.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ViolationCode,
        hint: str = "",
        field: str = "",
        locus: object = None,
        rejected: str = "",
    ) -> None:
        super().__init__(message)
        self._hint = hint
        self._rejected = rejected
        self.code = code
        self.field = field
        self.locus = locus
        #: Position of the offending issue in the response, filled in by the caller that
        #: iterates them — `validate_issue` sees one issue and cannot know its index.
        self.issue_index: int | None = None
        self.issue_count: int | None = None

    def repair_hint(self) -> str:
        """The correction guidance, or "" when this violation has nothing concrete to
        offer (a category out of scope for the lens is a reading failure, not a
        recoverable slip)."""
        return self._hint

    def rejected_text(self) -> str:
        """The field value that failed, or "" when the violation has no quotable value.

        Read only by the *repair* path, which stays inside the run (D-repair-turn-context).
        It is deliberately not on `diagnostics()` and never in `str(self)`: those go to
        stdout and to `LensResult.failure_reason`, which live outside the 0700 run tree
        (RA-016). A log gets `fingerprint()`; only the model that emitted this text gets
        the text back.
        """
        return self._rejected

    def at_issue(self, index: int, *, of: int) -> None:
        """Record which issue of how many this violation came from."""
        self.issue_index = index
        self.issue_count = of

    def fingerprint(self, key: bytes) -> str:
        """Call-local 8-hex identity of the rejected text, or "" when there is none.

        A keyed hash of the *normalized* span, so attempts in one repair loop can be
        compared without exporting a dictionary-testable or cross-call identifier. It
        exists to answer the one question the failure message cannot: across repair
        attempts, did the critic re-emit the same rejected span — a re-roll, which means
        the repair carried no usable correction — or move to a different one, which means
        it is searching and cannot find a valid anchor at all. Those have different fixes,
        and nothing in the current logs distinguishes them.
        """
        if not self._rejected:
            return ""
        return hashlib.blake2s(
            _normalize(self._rejected).encode("utf-8"), key=key, digest_size=4
        ).hexdigest()

    def diagnostics(self, fingerprint_key: bytes) -> dict[str, str]:
        """Bounded, content-free fields describing this rejection, for a log line.

        Every value is a closed-enum label, a structural reference, an integer or a
        hash — never report text, never model-authored prose (RA-016).
        """
        fields: dict[str, str] = {"code": self.code.value}
        if self.field:
            fields["field"] = self.field
        if self.locus is not None:
            fields["locus"] = str(self.locus)
        if self.issue_index is not None:
            fields["issue"] = f"{self.issue_index}/{self.issue_count}"
        if self._rejected:
            fields["span"] = self.fingerprint(fingerprint_key)
        return fields


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
#:
#: `conceptual_conflation` joins the three original logic categories (D-conceptual-conflation)
#: because both poles of a substitution are passages the report contains — the one that
#: states the concept and the one that swaps the other in — exactly the premise/conclusion
#: shape `invalid_inference` already has, and not the bias categories' *pattern* shape. The
#: field stays optional, so a single sentence that fuses the two concepts with no second
#: passage anywhere is still reportable with `related_span` omitted.
IN_ARTIFACT_RELATED = frozenset(
    {
        Category.CONTRADICTED_CLAIM,
        Category.INVALID_INFERENCE,
        Category.OVERSTATED_CLAIM,
        Category.CONCEPTUAL_CONFLATION,
    }
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
            f"category '{issue.category.value}' is out of scope for lens '{lens.value}'",
            code=ViolationCode.CATEGORY_OUT_OF_SCOPE,
            field="category",
        )
    if not structure.contains(issue.locus):
        raise LensValidationError(
            f"locus {issue.locus} does not exist in the artifact under review",
            code=ViolationCode.LOCUS_ABSENT,
            field="locus",
            locus=issue.locus,
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
            code=ViolationCode.SPAN_EMPTY,
            hint=_quote_hint(field, scope, source_text),
            field=field,
            locus=locus,
        )
    if needle not in _normalize(source_text):
        raise LensValidationError(
            f"{field} at {locus} is not a verbatim quote from the {scope}",
            code=ViolationCode.SPAN_NOT_VERBATIM,
            hint=_quote_hint(field, scope, source_text),
            field=field,
            locus=locus,
            rejected=span,
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


def _issue_key(issue: RawIssue) -> tuple:
    """The identity of a finding, independent of who reported it.

    Two critics reading the same lens (D-front-loaded-depth) routinely land on the same
    defect, and with `search.verify_sources` on both evidence critics are handed the
    same mechanical `fabricated_citation` for the same dead URL. Counting that twice
    would inflate `totals`, inflate the stagnation signature, and make the view
    disagree with the defect list it is supposed to summarize — so `tally` and
    `to_defects` collapse on this one key and stay in step.
    """
    return (issue.locus.section, issue.locus.paragraph, issue.category, issue.claim_span)


def distinct_issues(results: list[LensResult]) -> list[RawIssue]:
    """Every clamped, convergence-relevant finding across the *completed* reviews of
    one artifact, collapsed to one entry per `_issue_key`.

    Where two critics report the same finding at different severities the **highest**
    survives. That is the same direction the mechanical floor clamps in (RC-005): a
    critic may escalate and never downgrade, and letting whichever review happened to
    be stored first decide would make a second reviewer able to soften the first.

    Failed reviews are skipped here, once, so `tally`, `to_defects` and everything
    derived from them see the identical stream — partial counts are never used (rule 2).
    """
    collapsed: dict[tuple, RawIssue] = {}
    for result in sorted(results, key=lambda r: r.lens.value):
        if result.failed:
            continue
        for issue in clamp(result.issues):
            if issue.category is Category.STYLISTIC:
                # "ignored for convergence" has to mean ignored: counted here, a
                # stylistic nitpick could authorize a polish rewrite (rule 9) and risk
                # a substantive regression for a finding declared irrelevant.
                continue
            key = _issue_key(issue)
            current = collapsed.get(key)
            if current is None or SEVERITY_RANK[issue.severity] > SEVERITY_RANK[current.severity]:
                collapsed[key] = issue
    return list(collapsed.values())


def reviewed_lenses(results: list[LensResult]) -> set[Lens]:
    """Lenses with at least one *completed* review among `results`."""
    return {r.lens for r in results if not r.failed}


def unreviewed_lenses(results: list[LensResult]) -> list[Lens]:
    """Lenses this artifact has no completed review for — the fail-closed unit
    (controller rules 2/3, docs/convergence.md).

    A lens is incomplete when *nothing* valid was returned for it, not when one of
    several reviews failed. With review depth above 1 those differ: a failed second
    critic leaves a whole, valid review standing, and discarding it to re-ask would
    throw away findings the fail-closed rule exists to preserve. The depth shortfall
    is not dropped — it is picked up by rule 8, which cannot let a *clean* artifact be
    accepted below full clearance.
    """
    reviewed = reviewed_lenses(results)
    return [lens for lens in LENSES if lens not in reviewed]


def to_defects(
    results: list[LensResult], overruled: set[tuple[str, str]] | None = None
) -> list[Defect]:
    """The generator-facing handoff. Provenance (lens, model) is dropped here —
    it lives on in the audit store only (principle 3).

    `overruled` holds registry keys (category, normalized claim_span) of defects a
    writer disputed and lost (D-writer-disputes): those are marked `adjudicated=True` so the next
    writer is told the task was independently reviewed and stands."""
    defects: list[Defect] = []
    overruled = overruled or set()
    for issue in distinct_issues(results):
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
                adjudicated=(issue.category.value, _normalize(issue.claim_span)) in overruled,
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
    """Count **distinct** findings, not reports of them (`_issue_key`).

    With one critic per lens the deduplication is a no-op — categories are partitioned
    by lens, so two lenses cannot raise the same key. It starts to bite at review depth
    2, where the same defect found by both critics is one defect.
    """
    per_category: dict[str, SeverityCounts] = {}
    totals = SeverityCounts()
    for issue in distinct_issues(results):
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
