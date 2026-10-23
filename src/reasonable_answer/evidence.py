"""The per-source evidence reader behind `read_source` (D-writer-evidence-card).

`read_source` used to hand a writer the first `search.read_max_chars` characters of a
page, while the pipeline retained up to `search.fetch_body_max_chars` of it and the
claim checker was shown claim-anchored excerpts of that whole body. The writer — the
role that decides what to claim — had the narrowest view in the pipeline: a figure past
the opening was invisible to it.

This module gives the writer the whole retained page without putting the whole page in
its context. One fresh, isolated call reads the body (split into overlapping chunks when
it is longer than `evidence_reader.chunk_chars`) and returns an **evidence card**:

* passages of the page relevant to the run's question and the writer's stated purpose,
  each a quote the model proposes and this module **finds in the body** — exactly, or
  differing only in whitespace and letter case, in which case the page's own characters
  are what is kept. A quote that is not found is dropped, never repaired. A quote that
  is too long is dropped, never clipped: a verbatim prefix can lose the qualification at
  the end of its sentence;
* closed-vocabulary labels — what kind of page this is, and what kind of statement each
  passage is.

Nothing else. The card carries **no model-authored free text**: a summary written by a
model that has just read an untrusted page is the one channel by which that page could
address the writer in a voice other than its own (RA-010). What reaches the writer is
page text, fenced as page text always is (`prompts.evidence_card_block`), plus labels
from a fixed list.

The reader runs under the drafting writer's own alias. It reviews nothing, so author
exclusion — which governs who may *judge* an artifact — has nothing to bind; and using the
writer's own model means no second model's judgement shapes the writer's evidence.

Everything fails toward the writer seeing *something*: a failed call, a schema
violation, a spent call budget or a card with no verified passage leaves
`reading.SourceReader` to hand back the raw opening exactly as before, and the outcome
is counted as a fallback. A fallback is never rendered as a card.

Cards are cached for the run per (identity, URL, body, question, purpose, prompt): the
call is a pure function of those, and repeating it buys sampling noise. Keyed on the
identity, so one writer model is never handed another's selection.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import prompts
from .fetch import FetchedSource
from .llm import MalformedOutputError, ModelCallError, call_purpose, per_call_timeout

log = logging.getLogger(__name__)

#: What the page shown is, as the reader classifies it. Advisory to the writer only: it
#: never changes a `SourceOutcome`, the read log, or any finding — a model's reading of
#: a page is not a mechanical fact about it (D-writer-evidence-card).
PageKind = Literal[
    "document_text",
    "abstract_or_summary",
    "landing_or_catalog",
    "paywall_or_login",
    "navigation_or_listing",
    "other",
]

#: What one passage states.
PassageKind = Literal["finding", "figure", "method", "scope", "limitation", "definition", "other"]

#: Characters of writer-supplied `purpose` handed to the reader; the rest is cut.
PURPOSE_MAX_CHARS = 300

#: Cached cards a runtime holds before the cache is dropped whole — a memory bound, not
#: a correctness one: a dropped card is re-derived.
CACHE_MAX_ENTRIES = 4_096


# The two docstrings below are sent to the model as the schema's descriptions, so they
# are written to it. Why the answer has no free-text field is in the module docstring.
class ReaderPassage(BaseModel):
    """One passage copied verbatim from the page text."""

    model_config = ConfigDict(extra="forbid")

    quote: str
    kind: PassageKind


class ReaderOutput(BaseModel):
    """What kind of page this is, and the passages of it that bear on the question."""

    model_config = ConfigDict(extra="forbid")

    page_kind: PageKind
    passages: list[ReaderPassage] = Field(default_factory=list)


@dataclass(frozen=True)
class Passage:
    """A verified passage: the page's own characters, and where they sit in the body."""

    text: str
    kind: str
    start: int


@dataclass(frozen=True)
class Card:
    """What the writer is shown in place of the raw opening."""

    page_kind: str
    #: In page order.
    passages: tuple[Passage, ...]
    #: Length of the retained body the reader was shown, across all chunks.
    body_chars: int
    #: The retained body is itself a prefix of the page (byte cap, PDF page cap, …).
    body_truncated: bool

    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.passages)

    @property
    def text(self) -> str:
        """The passages as one string — what `ReadSession` records as the text this
        writer saw, so `support.check` rules on these passages and nothing wider."""
        return "\n\n[…]\n\n".join(p.text for p in self.passages)

    def within(self, chars: int) -> Card | None:
        """The leading passages (in page order) that fit in `chars`, or None if none do."""
        kept: list[Passage] = []
        used = 0
        for passage in self.passages:
            if used + len(passage.text) > chars:
                break
            kept.append(passage)
            used += len(passage.text)
        if not kept:
            return None
        return Card(self.page_kind, tuple(kept), self.body_chars, self.body_truncated)


@dataclass(frozen=True)
class CardOutcome:
    """One reader pass: the card, or the closed reason there is none."""

    card: Card | None
    #: `None` when a card was built; otherwise one of `FALLBACK_REASONS`.
    fallback: str | None = None
    kept: int = 0
    dropped: int = 0
    calls: int = 0
    seconds: float = 0.0
    cached: bool = False


#: Why a read fell back to the raw opening. A closed vocabulary, so the event can count it.
FALLBACK_REASONS = (
    "no_verified_passage",
    "reader_failed",
    "reader_budget_spent",
    "reader_unavailable",
)


@dataclass
class CardTally:
    """One writer call's reader activity, for the `generate` event. Counts only."""

    cards: int = 0
    passages_kept: int = 0
    passages_dropped: int = 0
    cached: int = 0
    calls: int = 0
    seconds: float = 0.0
    fallbacks: dict[str, int] = field(default_factory=dict)

    def add(self, outcome: CardOutcome) -> None:
        if outcome.card is not None:
            self.cards += 1
        else:
            reason = outcome.fallback or "reader_failed"
            self.fallbacks[reason] = self.fallbacks.get(reason, 0) + 1
        self.passages_kept += outcome.kept
        self.passages_dropped += outcome.dropped
        self.cached += int(outcome.cached)
        self.calls += outcome.calls
        self.seconds += outcome.seconds

    def as_event(self) -> dict:
        return {
            "cards_built": self.cards,
            "card_passages_kept": self.passages_kept,
            "card_passages_dropped": self.passages_dropped,
            "card_fallbacks": dict(sorted(self.fallbacks.items())),
            "cards_cached": self.cached,
            "reader_calls": self.calls,
            "reader_seconds": round(self.seconds, 1),
        }


def chunks(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """`text` as consecutive `(offset, chunk)` pieces of at most `size` characters, each
    starting `size - overlap` after the last, so a passage up to `overlap` long that
    straddles a boundary is whole in one of them."""
    if len(text) <= size:
        return [(0, text)]
    step = size - overlap
    out = []
    start = 0
    while start < len(text):
        out.append((start, text[start : start + size]))
        if start + size >= len(text):
            break
        start += step
    return out


def _locate(quote: str, text: str) -> tuple[int, int] | None:
    """Where `quote` occurs in `text`: exactly, or differing only in whitespace and letter
    case. The span returned is into `text`, so the characters kept are the page's own.
    Anything looser — folded punctuation, dropped words, joined fragments — is not found."""
    q = quote.strip()
    if not q:
        return None
    at = text.find(q)
    if at >= 0:
        return at, at + len(q)
    pattern = r"\s+".join(re.escape(token) for token in q.split())
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return None
    return match.start(), match.end()


class EvidenceReader:
    """Run-lifetime reader: the client, the knobs, the cache, the run-wide call budget and the
    per-identity breaker. One per runtime, shared by every writer call of the run."""

    def __init__(self, client, config, *, repair_retries: int = 1) -> None:
        self._client = client
        self._config = config
        self._repair_retries = repair_retries
        self._cache: dict[tuple[str, ...], CardOutcome] = {}
        self._lock = threading.Lock()
        self._calls = 0
        #: Breaker state per reader identity (the resolved provider/model), never run-wide:
        #: one provider's bad patch is no evidence about another writer's model.
        self._failures: dict[str, int] = {}
        self._disabled: set[str] = set()

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls

    def card(
        self,
        alias: str,
        identity: str,
        source: FetchedSource,
        *,
        question: str,
        purpose: str = "",
        current_date: str | None = None,
    ) -> CardOutcome:
        """Build one card for `source`, or say why not. Never raises."""
        cfg = self._config
        purpose = (purpose or "").strip()[:PURPOSE_MAX_CHARS]
        body = source.text or ""
        key = _key(identity, source.url, body, question, purpose, cfg)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return CardOutcome(
                hit.card, hit.fallback, kept=hit.kept, dropped=hit.dropped, cached=True
            )

        pieces = chunks(body, cfg.chunk_chars, cfg.chunk_overlap_chars)
        with self._lock:
            if identity in self._disabled:
                return CardOutcome(None, "reader_unavailable")
            # All or nothing: a card that read half the page would present the passages
            # of that half as the page's relevant material.
            if self._calls + len(pieces) > cfg.max_calls:
                return CardOutcome(None, "reader_budget_spent")
            self._calls += len(pieces)

        started = time.monotonic()
        found: list[Passage] = []
        kinds: list[str] = []
        dropped = 0
        for index, (offset, chunk) in enumerate(pieces):
            user = prompts.evidence_reader_user(
                question,
                purpose,
                source.url,
                chunk,
                part=index + 1,
                parts=len(pieces),
                start=offset,
                body_chars=len(body),
                truncated=source.truncated,
                current_date=current_date,
            )
            try:
                with call_purpose("evidence_reader"):
                    output = self._client.structured(
                        alias,
                        system=prompts.evidence_reader_system(
                            cfg.max_passages, cfg.passage_max_chars
                        ),
                        user=user,
                        schema=ReaderOutput,
                        max_tokens=cfg.max_tokens,
                        repair_retries=self._repair_retries,
                        **per_call_timeout(cfg.timeout_seconds),
                    )
            except (ModelCallError, MalformedOutputError) as exc:
                # Content-free: the failure class, never the page or the URL (RA-016).
                log.warning(
                    "evidence reader by %s failed on part %d/%d: %s",
                    alias,
                    index + 1,
                    len(pieces),
                    getattr(exc, "failure_class", type(exc).__name__),
                )
                self._failed(alias, identity)
                return CardOutcome(
                    None,
                    "reader_failed",
                    calls=index + 1,
                    seconds=time.monotonic() - started,
                )
            kinds.append(output.page_kind)
            for proposed in output.passages:
                passage = self._verified(proposed, chunk, offset)
                if passage is None:
                    dropped += 1
                else:
                    found.append(passage)
        seconds = time.monotonic() - started
        with self._lock:
            self._failures[identity] = 0

        # An overlap repeat is neither kept nor a verification failure, so not counted.
        merged, _ = _merged(found)
        kept, over = self._capped(merged)
        dropped += over
        page_kind = "document_text" if "document_text" in kinds else (kinds[0] if kinds else "other")
        if not kept:
            outcome = CardOutcome(
                None, "no_verified_passage", dropped=dropped, calls=len(pieces), seconds=seconds
            )
        else:
            card = Card(page_kind, tuple(sorted(kept, key=lambda p: p.start)), len(body), source.truncated)
            outcome = CardOutcome(
                card, kept=len(kept), dropped=dropped, calls=len(pieces), seconds=seconds
            )
        with self._lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                self._cache.clear()
            self._cache[key] = outcome
        return outcome

    def _verified(self, proposed: ReaderPassage, chunk: str, offset: int) -> Passage | None:
        cfg = self._config
        span = _locate(proposed.quote, chunk)
        if span is None:
            return None
        text = chunk[span[0] : span[1]]
        if not (cfg.passage_min_chars <= len(text) <= cfg.passage_max_chars):
            return None
        return Passage(text=text, kind=proposed.kind, start=offset + span[0])

    def _capped(self, passages: list[Passage]) -> tuple[list[Passage], int]:
        """The first passages, in the reader's own order, that fit the card's bounds."""
        cfg = self._config
        kept: list[Passage] = []
        used = 0
        for passage in passages:
            if len(kept) >= cfg.max_passages or used + len(passage.text) > cfg.card_max_chars:
                continue
            kept.append(passage)
            used += len(passage.text)
        return kept, len(passages) - len(kept)

    def _failed(self, alias: str, identity: str) -> None:
        with self._lock:
            count = self._failures.get(identity, 0) + 1
            self._failures[identity] = count
            if count >= self._config.max_consecutive_failures and identity not in self._disabled:
                self._disabled.add(identity)
                log.warning(
                    "evidence reader: %d consecutive failures on %s; raw page openings for "
                    "that model for the rest of this run",
                    count,
                    alias,
                )


def _merged(passages: list[Passage]) -> tuple[list[Passage], int]:
    """Drop a passage that overlaps one already kept — chunk overlap shows the same text
    twice. The first proposed wins, so the reader's own order is preserved."""
    kept: list[Passage] = []
    repeats = 0
    for passage in passages:
        end = passage.start + len(passage.text)
        if any(passage.start < k.start + len(k.text) and k.start < end for k in kept):
            repeats += 1
            continue
        kept.append(passage)
    return kept, repeats


def _key(identity: str, url: str, body: str, question: str, purpose: str, cfg) -> tuple[str, ...]:
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    shape = (
        f"{cfg.chunk_chars}/{cfg.chunk_overlap_chars}/{cfg.max_passages}/"
        f"{cfg.passage_max_chars}/{cfg.passage_min_chars}/{cfg.card_max_chars}"
    )
    system = prompts.evidence_reader_system(cfg.max_passages, cfg.passage_max_chars)
    return (identity, url, digest(body), digest(question), digest(purpose), digest(system), shape)


__all__ = [
    "CACHE_MAX_ENTRIES",
    "FALLBACK_REASONS",
    "PURPOSE_MAX_CHARS",
    "Card",
    "CardOutcome",
    "CardTally",
    "EvidenceReader",
    "Passage",
    "ReaderOutput",
    "ReaderPassage",
    "chunks",
]
