"""Claim-level source verification for the evidence lens (D-claim-level-verification).

One evidence critic used to hold every cited page in one context — first the pages'
opening characters, then (D-claim-anchored-excerpts) the passages nearest the report's
own citing sentences — and judge every citation from that. Measured on production, the
single context is what failed: the evidence lens was the one lens that almost never
cleared, and most of what it raised was ``misrepresented_source`` against text the
critic could not see.

This module takes the claim as the unit. The pairing is mechanical: every sentence of
the report body that carries a citation marker, paired with the fetched page the
bibliography lists under that number. Each pair is then checked in **its own fresh
context** — one sentence, its paragraph, one page — by the same model that holds the
evidence critic's slot, and answers a closed four-way verdict anchored to a verbatim
span of the page. Verdicts become findings mechanically, the way a definitive
not-found becomes ``fabricated_citation`` (D-notfound-fabrication):

* ``contradicted`` is ``misrepresented_source``, always;
* ``absent`` is ``misrepresented_source`` only when the page was shown **whole** — on a
  page shown in part, absence from the excerpts is not absence from the page;
* ``supported`` and ``unreadable`` mint nothing, and are counted.

Everything fails **toward the writer, never against it**: a checker call that fails,
answers outside the schema, or quotes a span that is not in the page leaves the pair
*unchecked*, and an unchecked pair mints nothing. The critic's own whole-document
``misrepresented_source`` judgement is kept exactly for the pairs the checker did not
settle, and dropped for the ones it did — a checked pair's verdict is authoritative
for that category, so a claim is never counted twice under two spans.

Verdicts are cached for the run per (critic identity, page, sentence): the call is a
pure function of those three, and re-running it on an unchanged sentence against an
unchanged page buys sampling noise and nothing else. The cache is keyed on the critic's
resolved identity, so a second family still forms its own view (QP2), and it is never a
clean record — clearance is minted from the completed review as before (RC-002).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import excerpt, prompts
from . import report as report_mod
from .fetch import _SOURCES_HEADING, FetchedSource
from .llm import LLMClient, MalformedOutputError, ModelCallError, ProviderAccountError
from .schemas import MAX_RATIONALE, MAX_SPAN, ClaimVerdict, RawIssue, StructuralRef
from .taxonomy import Category, Severity
from .triage import _normalize

log = logging.getLogger(__name__)

#: How many cached verdicts a runtime holds before the cache is dropped whole. A
#: bound on memory, not on correctness: a dropped verdict is simply re-derived.
CACHE_MAX_ENTRIES = 8_192


@dataclass(frozen=True)
class ClaimPair:
    """One sentence of the report, the bibliography number it cites, and the page."""

    locus: StructuralRef
    sentence: str
    paragraph: str
    number: int
    url: str


@dataclass(frozen=True)
class PairVerdict:
    pair: ClaimPair
    #: `supported` / `contradicted` / `absent` / `unreadable`, or `unchecked`.
    verdict: str
    support_span: str | None = None
    reason: str = ""
    #: The page was shown whole to the checker. Only then may `absent` be a finding.
    complete: bool = False
    cached: bool = False
    #: Why an `unchecked` pair was not checked: a closed label, never page text.
    unchecked_reason: str | None = None

    @property
    def checked(self) -> bool:
        return self.verdict in ("supported", "contradicted", "absent", "unreadable")


@dataclass
class ClaimCheck:
    """One critic slot's check of one artifact."""

    verdicts: list[PairVerdict] = field(default_factory=list)
    #: Pairs past `max_pairs`, never attempted.
    over_cap: int = 0

    def counts(self) -> dict[str, int]:
        out = {
            "pairs": len(self.verdicts) + self.over_cap,
            "checked": 0,
            "supported": 0,
            "contradicted": 0,
            "absent": 0,
            "absent_partial": 0,
            "unreadable": 0,
            "unchecked": self.over_cap,
            "cached": 0,
        }
        for v in self.verdicts:
            if v.cached:
                out["cached"] += 1
            if not v.checked:
                out["unchecked"] += 1
                continue
            out["checked"] += 1
            if v.verdict == "absent" and not v.complete:
                out["absent_partial"] += 1
            else:
                out[v.verdict] += 1
        return out

    def as_record(self) -> dict[str, Any]:
        """Content-bearing: for the run's critiques directory, never the event trail."""
        return {
            "counts": self.counts(),
            "verdicts": [
                {
                    "locus": str(v.pair.locus),
                    "number": v.pair.number,
                    "url": v.pair.url,
                    "sentence": v.pair.sentence,
                    "verdict": v.verdict,
                    "support_span": v.support_span,
                    "reason": v.reason,
                    "complete": v.complete,
                    "cached": v.cached,
                    "unchecked_reason": v.unchecked_reason,
                }
                for v in self.verdicts
            ],
        }


class VerdictCache:
    """Per-runtime verdict memo, keyed on what the checker call is a function of."""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self._entries: dict[tuple[str, str, str, str], PairVerdict] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    @staticmethod
    def key(identity: str, page_text: str, sentence: str) -> tuple[str, str, str, str]:
        body = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
        return (identity, body, _normalize(sentence), "v1")

    def get(self, key: tuple[str, str, str, str]) -> PairVerdict | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: tuple[str, str, str, str], verdict: PairVerdict) -> None:
        with self._lock:
            if len(self._entries) >= self._max:
                self._entries.clear()
            self._entries[key] = verdict


# ------------------------------------------------------------------ pairing


def pairs(report_text: str, limit: int | None = None) -> tuple[list[ClaimPair], int]:
    """Every (citing sentence, bibliography number, URL) triple in the report body.

    Deterministic string work over the report's own structure: the loci are the ones
    critics quote, the sentence split is `excerpt`'s, the markers expand as `excerpt`
    expands them, and the number-to-URL map is `excerpt.entry_numbers`. A marker whose
    number lists no URL pairs with nothing — there is no page to check it against. The
    `## Sources` section itself is skipped: an entry does not cite itself.

    Returns the pairs kept and how many were dropped past `limit`.
    """
    structure = report_mod.parse(report_text)
    numbers = excerpt.entry_numbers(report_text)
    by_number: dict[int, str] = {}
    for url, listed in numbers.items():
        for number in listed:
            by_number.setdefault(number, url)

    out: list[ClaimPair] = []
    dropped = 0
    for p in structure.paragraphs:
        title = structure.section_titles[p.section] if p.section < len(structure.section_titles) else ""
        if _SOURCES_HEADING.match(f"## {title}"):
            continue
        for sentence in excerpt._SENTENCE_END.split(p.text):
            sentence = sentence.strip()
            if not sentence:
                continue
            cited: list[int] = []
            for marker in excerpt._MARKER.findall(sentence):
                for number in sorted(excerpt._cited(marker)):
                    if number not in cited:
                        cited.append(number)
            for number in cited:
                url = by_number.get(number)
                if url is None:
                    continue
                if limit is not None and len(out) >= limit:
                    dropped += 1
                    continue
                out.append(
                    ClaimPair(
                        locus=StructuralRef(section=p.section, paragraph=p.paragraph),
                        sentence=sentence,
                        paragraph=p.text,
                        number=number,
                        url=url,
                    )
                )
    return out, dropped


# ------------------------------------------------------------------ checking


def check(
    client: LLMClient,
    alias: str,
    identity: str,
    report_text: str,
    sources: Iterable[FetchedSource],
    *,
    page_max_chars: int,
    max_pairs: int,
    max_tokens: int,
    repair_retries: int,
    cache: VerdictCache | None = None,
    current_date: str | None = None,
    on_pair: Callable[[PairVerdict], None] | None = None,
) -> ClaimCheck:
    """Check every claim/page pair of `report_text` under one critic slot.

    Raises `ProviderAccountError` through: an account that cannot pay is a fact about
    the deployment, and the caller defers the run exactly as it does for the critic's
    own call (D-credit-exhaustion-defers). Every other failure lands on the pair as
    `unchecked`.
    """
    by_url = {s.url: s for s in sources}
    kept, dropped = pairs(report_text, limit=max_pairs)
    result = ClaimCheck(over_cap=dropped)
    for pair in kept:
        source = by_url.get(pair.url)
        if source is None or not source.ok or not source.text:
            verdict = PairVerdict(pair, "unchecked", unchecked_reason="page_not_read")
        else:
            verdict = _check_pair(
                client,
                alias,
                identity,
                pair,
                source,
                page_max_chars=page_max_chars,
                max_tokens=max_tokens,
                repair_retries=repair_retries,
                cache=cache,
                current_date=current_date,
            )
        result.verdicts.append(verdict)
        if on_pair is not None:
            on_pair(verdict)
    return result


def _check_pair(
    client: LLMClient,
    alias: str,
    identity: str,
    pair: ClaimPair,
    source: FetchedSource,
    *,
    page_max_chars: int,
    max_tokens: int,
    repair_retries: int,
    cache: VerdictCache | None,
    current_date: str | None,
) -> PairVerdict:
    excerpted = excerpt.select(source.text, [pair.sentence], budget=page_max_chars)
    shown = excerpt.render(excerpted)
    key = VerdictCache.key(identity, shown, pair.sentence)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return PairVerdict(
                pair,
                hit.verdict,
                hit.support_span,
                hit.reason,
                complete=hit.complete,
                cached=True,
            )

    shown_normalized = _normalize(shown)

    def validate(output: ClaimVerdict) -> None:
        if output.verdict in ("supported", "contradicted"):
            span = _normalize(output.support_span or "")
            if not span or span not in shown_normalized:
                raise ValueError(
                    f"a `{output.verdict}` verdict must quote `support_span` verbatim "
                    "from the page text shown; the span given is not in it"
                )

    user = prompts.claim_check_user(
        pair.sentence,
        pair.paragraph,
        pair.number,
        pair.url,
        shown,
        current_date=current_date,
    )
    try:
        output = client.structured(
            alias,
            system=prompts.CLAIM_CHECK_SYSTEM,
            user=user,
            schema=ClaimVerdict,
            max_tokens=max_tokens,
            repair_retries=repair_retries,
            validate=validate,
        )
    except ProviderAccountError:
        raise
    except ModelCallError as exc:
        log.warning("claim check by %s failed at %s: %s", alias, pair.locus, str(exc)[:200])
        return PairVerdict(pair, "unchecked", unchecked_reason=exc.failure_class)
    except (MalformedOutputError, ValueError) as exc:
        # Outside the schema, or a span the page does not contain, past the repair
        # budget. Content-free in the log: the pair's locus, never its text.
        log.info("claim check by %s unanchored at %s: %s", alias, pair.locus, type(exc).__name__)
        return PairVerdict(pair, "unchecked", unchecked_reason="schema_violation")

    verdict = PairVerdict(
        pair,
        output.verdict,
        output.support_span if output.verdict in ("supported", "contradicted") else None,
        output.reason,
        complete=excerpted.complete,
    )
    if cache is not None:
        cache.put(key, verdict)
    return verdict


# ------------------------------------------------------------------ findings


def issues_from(check_result: ClaimCheck) -> list[RawIssue]:
    """The ``misrepresented_source`` findings the verdicts settle.

    Mechanically minted like `triage.mechanical_citation_issues`, so this bypasses
    `validate_issue`: the `claim_span` is a prefix of the checked sentence, which is
    verbatim in the paragraph by construction, and the severity is the category floor.
    """
    issues: list[RawIssue] = []
    seen: set[tuple[int, int, str]] = set()
    for v in check_result.verdicts:
        if v.verdict == "contradicted":
            what = "states something materially different"
        elif v.verdict == "absent" and v.complete:
            what = "shown whole, does not address the point"
        else:
            continue
        span = _clip(v.pair.sentence, MAX_SPAN)
        key = (v.pair.locus.section, v.pair.locus.paragraph, span)
        if key in seen:
            continue
        seen.add(key)
        reason = f"Checked sentence-by-sentence against the fetched page for [{v.pair.number}]: "
        reason += f"the page, {what}. {v.reason}".strip()
        issues.append(
            RawIssue(
                category=Category.MISREPRESENTED_SOURCE,
                severity=Severity.MAJOR,
                locus=v.pair.locus,
                claim_span=span,
                rationale=_clip(reason, MAX_RATIONALE),
                instruction=(
                    f"Qualify the sentence to what source [{v.pair.number}] states, cite a "
                    "source that states it, or remove the attribution; do not invent support."
                ),
                related_span=_clip(v.support_span, MAX_SPAN) if v.support_span else None,
                citation_id=str(v.pair.number),
            )
        )
    return issues


def reconcile(critic_issues: list[RawIssue], check_result: ClaimCheck) -> list[RawIssue]:
    """Drop a critic's own `misrepresented_source` findings on pairs the checker settled.

    A checked pair's verdict is authoritative for that category: the checker read the
    page for that sentence in its own context, and the critic read excerpts of every
    page in one. Keeping both would count one claim twice under two spans. Findings on
    pairs the checker could not settle — no page, an unreadable page, a failed call —
    keep the critic's judgement exactly as before. Every other category passes through.
    """
    checked = [v for v in check_result.verdicts if v.checked]
    if not checked:
        return list(critic_issues)
    out: list[RawIssue] = []
    for issue in critic_issues:
        if issue.category is not Category.MISREPRESENTED_SOURCE:
            out.append(issue)
            continue
        if any(_covers(v, issue) for v in checked):
            continue
        out.append(issue)
    return out


def _covers(verdict: PairVerdict, issue: RawIssue) -> bool:
    """Same paragraph, and the critic's quoted span lies inside the checked sentence (or
    quotes more than it). The citation number is deliberately not enough on its own: a
    paragraph routinely cites one source from two sentences, and only one may have
    been checked."""
    pair = verdict.pair
    if pair.locus != issue.locus:
        return False
    span = _normalize(issue.claim_span)
    sentence = _normalize(pair.sentence)
    return bool(span) and (span in sentence or sentence in span)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut


__all__ = [
    "CACHE_MAX_ENTRIES",
    "ClaimCheck",
    "ClaimPair",
    "PairVerdict",
    "VerdictCache",
    "check",
    "issues_from",
    "pairs",
    "reconcile",
]
