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
* ``supported`` and ``unreadable`` mint nothing per pair, and are counted — except that a
  sentence whose **every** citation is an *anchored* non-body mints one ``uncited_claim``
  (D-unreadable-citation-finding, ``claim_check.unreadable_finding``): an ``unreadable``
  verdict anchored by a verbatim span of the page shown, or a pair whose fetch outcome is
  mechanically not a body. An unanchored ``unreadable`` is the checker's say-so and mints
  nothing (D-anchored-unreadable).

Everything fails **toward the writer, never against it**: a checker call that fails,
answers outside the schema, or quotes a span that is not in the page leaves the pair
*unchecked*, and an unchecked pair mints nothing. After ``max_consecutive_failures``
such calls in a row the rest of the pass is skipped as ``aborted`` rather than met with
one timeout per pair (D-claim-check-inconclusive-verdicts). The critic's own
whole-document ``misrepresented_source`` judgement is kept exactly for the pairs the
checker did not *settle*, and dropped for the ones it did — a settled verdict is
authoritative for that category, so a claim is never counted twice under two spans.
Settled means ``supported``, ``contradicted``, or ``absent`` from a page shown whole:
``unreadable`` and ``absent`` from a page shown in part are not readings of the page,
so they neither mint a per-pair finding nor retire one a critic made
(D-claim-check-inconclusive-verdicts). A page cut before its end — at the fetch byte
cap, at a PDF's page cap — is never "shown whole", however short what survived.

A page longer than ``page_max_chars`` is no longer only shown in part
(D-whole-page-claim-check): with ``claim_check.whole_page`` it is checked chunk by chunk to
its end, each chunk in its own fresh context with the same closed verdict and span rule,
most relevant chunk first, stopping at the first verified ``supported`` or ``contradicted``.
``absent`` from **every** chunk of a body that was not itself cut is ``absent`` from the page —
settled, and minting like a page shown whole. Any chunk not checked to a verdict, any cap
hit, and any cut body leave the pair ``absent_partial`` and mint nothing.

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
from .fetch import _SOURCES_HEADING, FetchedSource, SourceOutcome, _clean_url
from .llm import (
    LLMClient,
    MalformedOutputError,
    ModelCallError,
    ProviderAccountError,
    per_call_timeout,
)
from .schemas import MAX_INSTRUCTION, MAX_RATIONALE, MAX_SPAN, ClaimVerdict, RawIssue, StructuralRef
from .taxonomy import Category, Severity
from .triage import _normalize

#: Fetch outcomes that are mechanically not a document body, observed by the pipeline
#: rather than claimed by a model. These never reach the checker (`FetchedSource.ok` is
#: false), so the pair is anchored without a call. `ERROR`, `EMPTY`, `NOT_FOUND` and the
#: budget outcomes say nothing about what the page is and stay out (D-anchored-unreadable).
_NON_BODY = frozenset(
    {
        SourceOutcome.PAYWALLED,
        SourceOutcome.BLOCKED,
        SourceOutcome.METADATA_ONLY,
        SourceOutcome.UNREADABLE,
    }
)

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
    #: The pair is evidence the cited response is not the work's own text, mechanically:
    #: an `unreadable` verdict whose `support_span` was found verbatim in the page shown,
    #: or a pair whose fetch outcome is not a document body (`_NON_BODY`).
    #: An `unreadable` verdict without this is the checker's say-so and mints nothing
    #: (D-anchored-unreadable).
    anchored: bool = False
    #: The pair was checked chunk by chunk (D-whole-page-claim-check); `chunk_calls` is the
    #: checker calls actually made for it (memo hits are free) and `cap_hit` that a per-pair
    #: or per-pass cap left chunks unexamined.
    chunked: bool = False
    chunk_calls: int = 0
    cap_hit: bool = False

    @property
    def checked(self) -> bool:
        return self.verdict in ("supported", "contradicted", "absent", "unreadable")

    @property
    def settled(self) -> bool:
        """The verdict is a reading of what the page says about the sentence — the only
        kind that may stand in for the critic's own judgement of that sentence
        (D-claim-check-inconclusive-verdicts). `unreadable` says the checker was shown
        no body to read, and `absent` from a page shown in part says only that the
        excerpts do not address the point; neither reads the page, so neither may
        retire a finding a critic made about it."""
        if self.verdict in ("supported", "contradicted"):
            return True
        return self.verdict == "absent" and self.complete


@dataclass
class ClaimCheck:
    """One critic slot's check of one artifact."""

    verdicts: list[PairVerdict] = field(default_factory=list)
    #: Pairs past `max_pairs`, never attempted.
    over_cap: int = 0
    #: (section, paragraph, sentence) of every sentence that has a pair past `max_pairs`.
    #: Such a sentence has a citation nobody read, so it is never "all unreadable".
    cut_sentences: frozenset[tuple[int, int, str]] = frozenset()
    #: Chunked checking was on for this pass; only then do `counts` and the record carry the
    #: chunk fields, so with the knob off the event is what it was before.
    whole_page: bool = False

    def counts(self) -> dict[str, int]:
        out = {
            "pairs": len(self.verdicts) + self.over_cap,
            "checked": 0,
            "supported": 0,
            "contradicted": 0,
            "absent": 0,
            "absent_partial": 0,
            "unreadable": 0,
            "unreadable_anchored": 0,
            "unchecked": self.over_cap,
            "aborted": 0,
            "cached": 0,
        }
        for v in self.verdicts:
            if v.cached:
                out["cached"] += 1
            if not v.checked:
                out["unchecked"] += 1
                if v.unchecked_reason == "aborted":
                    out["aborted"] += 1
                continue
            out["checked"] += 1
            if v.verdict == "unreadable" and v.anchored:
                out["unreadable_anchored"] += 1
            if v.verdict == "absent" and not v.complete:
                out["absent_partial"] += 1
            else:
                out[v.verdict] += 1
        if self.whole_page:
            out["absent_whole_page"] = sum(
                1 for v in self.verdicts if v.chunked and v.verdict == "absent" and v.complete
            )
            out["chunk_calls"] = sum(v.chunk_calls for v in self.verdicts)
            out["chunk_cap_hit"] = sum(1 for v in self.verdicts if v.cap_hit)
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
                    "anchored": v.anchored,
                    "cached": v.cached,
                    "unchecked_reason": v.unchecked_reason,
                    **(
                        {"chunked": v.chunked, "chunk_calls": v.chunk_calls, "cap_hit": v.cap_hit}
                        if self.whole_page
                        else {}
                    ),
                }
                for v in self.verdicts
            ],
        }


@dataclass(frozen=True)
class Chunking:
    """Whole-page checking policy (D-whole-page-claim-check); `None` to `check` means off."""

    overlap: int
    max_chunks_per_pair: int
    max_chunk_calls: int

    @classmethod
    def from_config(cls, cfg: Any) -> Chunking | None:
        if not cfg.whole_page:
            return None
        return cls(cfg.chunk_overlap_chars, cfg.max_chunks_per_pair, cfg.max_chunk_calls)


@dataclass
class _Pass:
    """Mutable state of one `check` pass: the failure streak and the chunk-call count."""

    failures: int = 0
    aborted: bool = False
    chunk_calls: int = 0


def source_standing(verdicts: Iterable[PairVerdict]) -> dict[str, bool]:
    """Cleaned URL -> whether the pages this check settled are *refuted* for every claim
    they were checked against (D-no-unread-source-drop).

    `True` when at least one pair citing the URL came back `contradicted` or `absent` from
    a page shown whole (the two verdicts `issues_from` turns into findings) and none came
    back `supported`; `False` when any pair came back `supported`. A URL whose pairs were
    all unsettled (`unreadable`, `absent` from a partial page, unchecked) is left out: the
    check says nothing about it. URLs and booleans only; no page text, no sentence.
    """
    supported: set[str] = set()
    negative: set[str] = set()
    for v in verdicts:
        url = _clean_url(v.pair.url)
        if v.verdict == "supported":
            supported.add(url)
        elif v.verdict == "contradicted" or (v.verdict == "absent" and v.complete):
            negative.add(url)
    return {url: url not in supported for url in supported | negative}


class VerdictCache:
    """Per-runtime verdict memo, keyed on what the checker call is a function of."""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self._entries: dict[tuple[str, str, str, str], PairVerdict] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    @staticmethod
    def key(identity: str, system: str, user: str) -> tuple[str, str, str, str]:
        system_hash = hashlib.sha256(system.encode("utf-8")).hexdigest()
        user_hash = hashlib.sha256(user.encode("utf-8")).hexdigest()
        return (identity, system_hash, user_hash, "v4")

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
    max_consecutive_failures: int | None = None,
    timeout: float | None = None,
    chunking: Chunking | None = None,
) -> ClaimCheck:
    """Check every claim/page pair of `report_text` under one critic slot.

    Raises `ProviderAccountError` through: an account that cannot pay is a fact about
    the deployment, and the caller defers the run exactly as it does for the critic's
    own call (D-credit-exhaustion-defers). Every other failure lands on the pair as
    `unchecked`.

    `max_consecutive_failures` is the circuit breaker (D-claim-check-inconclusive-verdicts):
    once that many calls in a row have ended unchecked — a transport failure, or output
    outside the schema past the repair budget — the remaining pairs are recorded
    `aborted` without a call. A dead proxy is then met with a few timeouts inside the
    critic's slot, not `max_pairs` of them each with its own retry budget. A memo hit
    is not a call and neither breaks nor resets the streak; a verdict resets it.

    `chunking` (D-whole-page-claim-check) turns on chunk-by-chunk checking of a body longer
    than `page_max_chars`; `None` is byte-identical to the earlier behaviour. Chunk calls
    feed the same failure streak.
    """
    by_url = {s.url: s for s in sources}
    everything, _ = pairs(report_text)
    kept = everything[:max_pairs]
    cut = frozenset((p.locus.section, p.locus.paragraph, p.sentence) for p in everything[max_pairs:])
    result = ClaimCheck(
        over_cap=len(everything) - len(kept), cut_sentences=cut, whole_page=chunking is not None
    )
    state = _Pass()
    for pair in kept:
        source = by_url.get(pair.url)
        if source is None or not source.ok or not source.text:
            # The fetch outcome is the pipeline's own observation, so a non-body outcome
            # anchors the pair without a checker call (D-anchored-unreadable).
            verdict = PairVerdict(
                pair,
                "unchecked",
                unchecked_reason="page_not_read",
                anchored=source is not None and source.outcome in _NON_BODY,
            )
        elif state.aborted:
            verdict = PairVerdict(pair, "unchecked", unchecked_reason="aborted")
        elif chunking is not None and len(source.text) > page_max_chars:
            verdict = _check_chunked(
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
                timeout=timeout,
                chunking=chunking,
                state=state,
                max_consecutive_failures=max_consecutive_failures,
            )
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
                timeout=timeout,
            )
            if verdict.cached:
                pass
            elif verdict.checked:
                state.failures = 0
            else:
                _note_failure(state, alias, max_consecutive_failures)
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
    timeout: float | None = None,
) -> PairVerdict:
    # A body cut before its end is never "shown whole", however short what survived:
    # absence from a prefix of the page is not absence from the page.
    excerpted = excerpt.select(
        source.text, [pair.sentence], budget=page_max_chars, truncated=source.truncated
    )
    shown = excerpt.render(excerpted)
    user = prompts.claim_check_user(
        pair.sentence,
        pair.paragraph,
        pair.number,
        pair.url,
        shown,
        current_date=current_date,
    )
    asked = _ask(
        client,
        alias,
        identity,
        pair,
        user,
        shown,
        complete=excerpted.complete,
        max_tokens=max_tokens,
        repair_retries=repair_retries,
        cache=cache,
        timeout=timeout,
    )
    assert asked is not None  # `allow_call` defaults to true
    return asked[0]


def _ask(
    client: LLMClient,
    alias: str,
    identity: str,
    pair: ClaimPair,
    user: str,
    shown: str,
    *,
    complete: bool,
    max_tokens: int,
    repair_retries: int,
    cache: VerdictCache | None,
    timeout: float | None,
    allow_call: bool = True,
) -> tuple[PairVerdict, bool] | None:
    """One checker call over `shown` (or its memoised verdict). Returns the verdict and
    whether a call was made, or `None` when the memo misses and `allow_call` is false."""
    key = VerdictCache.key(identity, prompts.CLAIM_CHECK_SYSTEM, user)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return (
                PairVerdict(
                    pair,
                    hit.verdict,
                    hit.support_span,
                    hit.reason,
                    complete=hit.complete,
                    cached=True,
                    anchored=hit.anchored,
                ),
                False,
            )
    if not allow_call:
        return None

    shown_normalized = _normalize(shown)

    def validate(output: ClaimVerdict) -> None:
        if output.verdict in ("supported", "contradicted"):
            span = _normalize(output.support_span or "")
            if not span or span not in shown_normalized:
                raise ValueError(
                    f"a `{output.verdict}` verdict must quote `support_span` verbatim "
                    "from the page text shown; the span given is not in it"
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
            # The evidence critic's slot, so the critic's timeout (D-role-call-timeouts).
            **per_call_timeout(timeout),
        )
    except ProviderAccountError:
        raise
    except ModelCallError as exc:
        log.warning("claim check by %s failed at %s: %s", alias, pair.locus, str(exc)[:200])
        return PairVerdict(pair, "unchecked", unchecked_reason=exc.failure_class), True
    except (MalformedOutputError, ValueError) as exc:
        # Outside the schema, or a span the page does not contain, past the repair
        # budget. Content-free in the log: the pair's locus, never its text.
        log.info("claim check by %s unanchored at %s: %s", alias, pair.locus, type(exc).__name__)
        return PairVerdict(pair, "unchecked", unchecked_reason="schema_violation"), True

    # An `unreadable` span is evidence only if the page shown contains it verbatim; a
    # span that is not there leaves a valid verdict that is merely unanchored.
    span = _normalize(output.support_span or "")
    anchored = output.verdict == "unreadable" and bool(span) and span in shown_normalized
    verdict = PairVerdict(
        pair,
        output.verdict,
        output.support_span if output.verdict in ("supported", "contradicted") or anchored else None,
        output.reason,
        complete=complete,
        anchored=anchored,
    )
    if cache is not None:
        cache.put(key, verdict)
    return verdict, True


def _note_failure(state: _Pass, alias: str, limit: int | None) -> None:
    state.failures += 1
    if limit is not None and state.failures >= limit:
        state.aborted = True
        log.warning(
            "claim check by %s: %d consecutive calls ended unchecked; "
            "skipping the remaining pairs of this pass",
            alias,
            state.failures,
        )


def _chunk_page_text(
    text: str, start: int, end: int, index: int, count: int, truncated: bool
) -> str:
    """A chunk as the checker is shown it: a header that says it is one section of a longer
    page — so absence from it is read as `absent`, never `contradicted` — then the text."""
    cut = (
        " The fetch stopped at its cap before the end of the page, so the page continues "
        "past the last character retained."
        if truncated
        else ""
    )
    return (
        f"Page text: section {index} of {count} of a longer page — characters {start:,}–{end:,} "
        f"of {len(text):,} retained. The other sections are checked separately, so text outside "
        f"this section may say anything.{cut}\n"
        f"(characters {start:,}–{end:,})\n{text[start:end]}"
    )


def _check_chunked(
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
    timeout: float | None,
    chunking: Chunking,
    state: _Pass,
    max_consecutive_failures: int | None,
) -> PairVerdict:
    """Check `pair` against the whole retained body of a page longer than `page_max_chars`
    (D-whole-page-claim-check): chunks most relevant first, each in a fresh context; the
    first verified `supported` or `contradicted` is the pair's verdict; `absent` is
    settled (`complete`) only when every chunk of an uncut body was checked to `absent`."""
    text = source.text
    bounds = excerpt.chunk_bounds(text, size=page_max_chars, overlap=chunking.overlap)
    order = excerpt.rank_chunks(text, bounds, [pair.sentence])
    count = len(bounds)
    cap_hit = len(order) > chunking.max_chunks_per_pair
    results: list[PairVerdict] = []
    first_failure: str | None = None
    calls = 0
    for position in order[: chunking.max_chunks_per_pair]:
        if state.aborted:
            break
        start, end = bounds[position]
        shown = _chunk_page_text(text, start, end, position + 1, count, source.truncated)
        user = prompts.claim_check_user(
            pair.sentence, pair.paragraph, pair.number, pair.url, shown, current_date=current_date
        )
        asked = _ask(
            client,
            alias,
            identity,
            pair,
            user,
            shown,
            complete=False,
            max_tokens=max_tokens,
            repair_retries=repair_retries,
            cache=cache,
            timeout=timeout,
            allow_call=state.chunk_calls < chunking.max_chunk_calls,
        )
        if asked is None:
            cap_hit = True
            break
        verdict, called = asked
        if called:
            state.chunk_calls += 1
            calls += 1
            if verdict.checked:
                state.failures = 0
            else:
                _note_failure(state, alias, max_consecutive_failures)
        if not verdict.checked:
            first_failure = first_failure or verdict.unchecked_reason
            continue
        results.append(verdict)
        if verdict.verdict in ("supported", "contradicted"):
            # Early stop: a verdict that read the page settles the pair. A contradiction in
            # a less relevant chunk is not looked for after a support (D-whole-page-claim-check).
            return PairVerdict(
                pair,
                verdict.verdict,
                verdict.support_span,
                verdict.reason,
                cached=calls == 0,
                chunked=True,
                chunk_calls=calls,
                cap_hit=cap_hit,
            )

    def out(kind: str, **kw: Any) -> PairVerdict:
        return PairVerdict(
            pair,
            kind,
            cached=calls == 0,
            chunked=True,
            chunk_calls=calls,
            cap_hit=cap_hit,
            **kw,
        )

    kinds = {v.verdict for v in results}
    if len(results) == count and kinds == {"unreadable"}:
        # Every chunk was no document text: anchored only when every chunk quoted its line.
        anchored = all(v.anchored for v in results)
        return out(
            "unreadable",
            support_span=next((v.support_span for v in results if v.anchored), None) if anchored else None,
            reason=results[0].reason,
            anchored=anchored,
        )
    if "absent" in kinds:
        # Settled only when every chunk answered `absent`: an unreadable chunk, an unchecked
        # one, a capped one or a cut body each leave this a reading of part of the page.
        whole = len(results) == count and kinds == {"absent"} and not source.truncated and not cap_hit
        return out("absent", reason=results[0].reason, complete=whole)
    if "unreadable" in kinds:
        return out("unreadable", reason=results[0].reason)
    reason = first_failure or ("aborted" if state.aborted else "chunk_cap")
    return PairVerdict(
        pair,
        "unchecked",
        unchecked_reason=reason,
        chunked=True,
        chunk_calls=calls,
        cap_hit=cap_hit,
    )


# ------------------------------------------------------------------ findings


def issues_from(check_result: ClaimCheck, *, unreadable_finding: bool = False) -> list[RawIssue]:
    """The ``misrepresented_source`` findings the verdicts settle, and — with
    `unreadable_finding` — an ``uncited_claim`` for each sentence whose every cited page
    was unreadable (D-unreadable-citation-finding).

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
    if unreadable_finding:
        issues.extend(_unreadable_issues(check_result))
    return issues


def _unreadable_issues(check_result: ClaimCheck) -> list[RawIssue]:
    """One ``uncited_claim`` per sentence every citation of which is anchored non-body.

    A sentence qualifies only when it has at least one pair and every pair is anchored
    (D-anchored-unreadable): an `unreadable` verdict whose quoted span was found in the
    page shown, or a pair whose fetch outcome was observed not to be a document body. A
    pair left unchecked for any other reason, cut by `max_pairs`, settled as anything else,
    or `unreadable` on the checker's say-so alone, leaves the sentence alone. The text is
    pipeline-authored from the entry numbers alone: never page text, the span, or the
    checker's `reason` (docs/isolation.md), and it states only what was observed.
    """
    by_sentence: dict[tuple[int, int, str], list[PairVerdict]] = {}
    for v in check_result.verdicts:
        key = (v.pair.locus.section, v.pair.locus.paragraph, v.pair.sentence)
        by_sentence.setdefault(key, []).append(v)
    out: list[RawIssue] = []
    for key, group in by_sentence.items():
        if key in check_result.cut_sentences or not all(_anchored_non_body(v) for v in group):
            continue
        numbers = sorted({v.pair.number for v in group})
        cited = ", ".join(f"[{n}]" for n in numbers)
        rationale = (
            f"Every source cited for this sentence ({cited}) came back, as fetched, as access-wall, "
            "catalog or navigation text or with no readable body, so the claim cannot be checked "
            "against its citation here."
        )
        out.append(
            RawIssue(
                category=Category.UNCITED_CLAIM,
                severity=Severity.MAJOR,
                locus=group[0].pair.locus,
                claim_span=_clip(group[0].pair.sentence, MAX_SPAN),
                rationale=_clip(rationale, MAX_RATIONALE),
                instruction=_clip(
                    f"Cite a readable copy of the same work for {cited} (an open-access version, "
                    "or the full text rather than a catalog, abstract or paywall page), cite a "
                    "different source that states it, or narrow or remove the claim.",
                    MAX_INSTRUCTION,
                ),
                citation_id=str(numbers[0]),
            )
        )
    return out


def _anchored_non_body(v: PairVerdict) -> bool:
    if v.verdict == "unreadable":
        return v.anchored
    return v.verdict == "unchecked" and v.unchecked_reason == "page_not_read" and v.anchored


def reconcile(critic_issues: list[RawIssue], check_result: ClaimCheck) -> list[RawIssue]:
    """Drop a critic's own `misrepresented_source` findings on pairs the checker settled.

    A settled verdict is authoritative for that category: the checker read the page for
    that sentence in its own context, and the critic read excerpts of every page in
    one. Keeping both would count one claim twice under two spans. Findings on pairs
    the checker did not settle keep the critic's judgement exactly as before — no
    page, a failed call, past the cap, and also `unreadable` and `absent` from a page
    shown in part (`PairVerdict.settled`): those verdicts are not readings of the page,
    and a finding retired by one would be a finding suppressed by nothing
    (D-claim-check-inconclusive-verdicts). Every other category passes through.
    """
    settled = [v for v in check_result.verdicts if v.settled]
    if not settled:
        return list(critic_issues)
    out: list[RawIssue] = []
    for issue in critic_issues:
        if issue.category is not Category.MISREPRESENTED_SOURCE:
            out.append(issue)
            continue
        if any(_covers(v, issue) for v in settled):
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
    "Chunking",
    "ClaimCheck",
    "ClaimPair",
    "PairVerdict",
    "VerdictCache",
    "check",
    "issues_from",
    "pairs",
    "reconcile",
]
