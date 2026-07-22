"""Writer disputes and their adjudication (D25).

A writer that believes a fix-task is factually wrong may dispute it instead of
falsifying the report to satisfy it. Adjudication is **mechanical-first** — a
citation dispute whose evidence checks out against the fetched page is upheld
without any model's judgment — and otherwise goes to a fresh-context **arbiter**
whose resolved identity is neither the disputing writer nor any critic that
raised the finding.

Everything here fails closed **toward the finding**: an invalid dispute, an
inconclusive fetch, a missing arbiter, an arbiter error, or an exhausted budget
all leave the defect standing. Suppression is earned only by an explicit
`upheld` verdict.
"""

from __future__ import annotations

from . import fetch, prompts
from .config import Roster
from .llm import LLMClient
from .schemas import AdjudicationRecord, ArbiterVerdict, Defect, Dispute, WriterDisputes
from .taxonomy import Category
from .triage import _normalize

#: Categories a fetched page can settle without judgment: the dispute stands or
#: falls on whether the cited source says what the writer claims it says.
MECHANICAL_CATEGORIES = frozenset(
    {Category.FABRICATED_CITATION, Category.MISREPRESENTED_SOURCE}
)


def registry_key(category: Category, claim_span: str) -> tuple[str, str]:
    """The adjudicated-facts registry key: the triage dedup key minus locus,
    because paragraphs shift between revisions while a verbatim span of unchanged
    text does not. Matching uses triage's quote normalization."""
    return (category.value, _normalize(claim_span))


def validate_disputes(
    raw: WriterDisputes, defects: list[Defect], max_per_pass: int
) -> list[tuple[Dispute, Defect]]:
    """Pair each dispute with the defect it targets. Never raises: a malformed
    dispute pass degrades to fewer (or no) disputes, it must not fail the run."""
    accepted: list[tuple[Dispute, Defect]] = []
    seen_indices: set[int] = set()
    for dispute in raw.disputes:
        if len(accepted) >= max_per_pass:
            break
        if dispute.task_index in seen_indices:
            continue
        if not 0 <= dispute.task_index < len(defects):
            continue
        defect = defects[dispute.task_index]
        if defect.adjudicated:
            continue  # already ruled on: the task stands, re-disputing is refused
        seen_indices.add(dispute.task_index)
        accepted.append((dispute, defect))
    return accepted


def adjudicate_mechanical(
    dispute: Dispute, defect: Defect, report_text: str, fetcher
) -> bool | None:
    """True = dispute upheld on the page's own text. None = inconclusive (falls
    through to the arbiter). NEVER False: page text is truncated and fetches
    fail for transient reasons, so absence is not refutation.

    Upheld requires all of: a mechanical category, an `evidence_url` the report
    already cites (a writer cannot point at an arbitrary corroborating page — the
    critic saw, or could have seen, this same source), a successful fetch, and
    the evidence quote present verbatim in the fetched text."""
    if fetcher is None or defect.category not in MECHANICAL_CATEGORIES:
        return None
    if not dispute.evidence_url or not dispute.evidence_quote:
        return None
    cited = fetch.extract_source_urls(report_text)
    if dispute.evidence_url not in cited:
        return None
    page = fetcher.fetch(dispute.evidence_url)
    if not page.ok or not page.text:
        return None
    quote = _normalize(dispute.evidence_quote)
    if quote and quote in _normalize(page.text):
        return True
    return None


def eligible_arbiters(
    roster: Roster,
    identities: dict[str, str],
    disputer_identity: str,
    raiser_identities: set[str],
) -> list[str]:
    """Aliases whose resolved identity is neither the disputer nor any raiser,
    deduped by identity. Critic-only specialists first: an identity that never
    writes has no authorship stake anywhere in the run."""
    excluded = {disputer_identity} | raiser_identities
    writer_identities = {identities[w] for w in roster.writers}
    picked: dict[str, str] = {}
    for alias in roster.all_aliases:
        identity = identities.get(alias)
        if identity is None or identity in excluded or identity in picked:
            continue
        picked[identity] = alias
    return sorted(
        picked.values(),
        key=lambda a: (identities[a] in writer_identities, a),
    )


def adjudicate_one(
    client: LLMClient,
    alias: str,
    defect: Defect,
    dispute: Dispute,
    paragraph_text: str,
    question: str,
    evidence_page=None,
    max_tokens: int = 4000,
) -> ArbiterVerdict:
    """One fresh-context arbiter call. Errors propagate; the caller records the
    dispute as dismissed/`arbiter_failed` and the defect stands."""
    return client.structured(
        alias,
        system=prompts.ARBITER_SYSTEM,
        user=prompts.arbiter_user(defect, dispute, paragraph_text, question, evidence_page),
        schema=ArbiterVerdict,
        max_tokens=max_tokens,
    )


def suppression_keys(records: list[AdjudicationRecord]) -> set[tuple[str, str]]:
    """Only `upheld` records suppress. Every dismissal path leaves the defect
    standing — that is the fail-closed direction."""
    return {
        registry_key(r.category, r.claim_span) for r in records if r.verdict == "upheld"
    }


def overruled_keys(records: list[AdjudicationRecord]) -> set[tuple[str, str]]:
    return {
        registry_key(r.category, r.claim_span) for r in records if r.verdict == "overruled"
    }


__all__ = [
    "MECHANICAL_CATEGORIES",
    "adjudicate_mechanical",
    "adjudicate_one",
    "eligible_arbiters",
    "overruled_keys",
    "registry_key",
    "suppression_keys",
    "validate_disputes",
]
