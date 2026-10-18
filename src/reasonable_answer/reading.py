"""Reading a retrieved source, as a writer tool (D-writer-source-reads).

Search (D-retrieval-opt-in) made a citation *real*: the writer can only cite a URL a
result actually returned. It did not make the citation *supported*. A Brave result is a
title, a URL and a one-line snippet, so a writer choosing what to claim has never seen
the page it is about to cite; bodies were fetched only afterwards, and only for a critic
(D-source-verification). Pinpoint locators, direct quotation and reliable claim/source
matching were therefore unavailable at the one moment they decide what the report says.

This module closes that gap and nothing wider. Four properties are load-bearing:

1. **No arbitrary-URL reader.** `read_source` accepts only a URL that a `web_search`
   result *in the same writer call* listed, or — on a revision — a URL the `## Sources`
   of the draft being revised lists (D-writer-rereads-cited-sources). The allowlist is the
   :class:`ReadSession`, which is created per `complete()` call and thrown away with
   it — see the class docstring for why per-call rather than per-run.
2. **One egress path.** Every byte still leaves through `fetch.SourceFetcher`, hence
   through `fetch.http_get`'s bounded http(s)-only opener, and the run-lifetime fetch
   cache is shared with source verification so a page read here is not downloaded
   again for the evidence lens. This module opens no socket of its own.
3. **Bounded.** Calls and characters are capped for the whole run by
   :class:`ReadBudget`, and each page is capped again on its own. Bytes off the wire
   were already capped by `search.fetch_max_bytes` inside the fetcher.
4. **Page text is untrusted data (RA-010).** This module returns
   :class:`~.fetch.FetchedSource` records; :mod:`.prompts` is what fences them. A body
   is the highest-volume untrusted text a writer ever sees — far more room to address
   its reader than a snippet has — so it carries the fence and the explicit note, and
   the writer is told again, inside the block, that a page may not instruct it.

Every outcome the verification path distinguishes survives to the writer unchanged
(body read, registry metadata only, paywalled, blocked, not found, unreadable, budget
spent), plus one this path adds: `not_retrieved`, for a URL the writer was never offered.
Collapsing them would be the same mistake `SourceOutcome` exists to prevent — a writer
told "could not read" for a page that does not exist will keep the claim.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import replace

from . import prompts
from .evidence import Card, CardOutcome, CardTally
from .fetch import FetchedSource, SourceOutcome, _clean_url

log = logging.getLogger(__name__)


class ReadBudget:
    """Whole-run caps on `read_source`: how many pages, and how much of them.

    Two counters, because they bound two different failures. `chars` bounds the
    *context*: a per-page cap cannot see the total, and twenty pages at the per-page cap
    is a prompt no model reads well, which is a correctness property here rather than a
    cost one (principle #6, docs/isolation.md). `calls` bounds egress and wall clock — a
    writer that opens every result of every search across ten rounds is a crawler, not an
    author.

    `max_calls=None` counts without refusing (D-unbounded-evidence): the call cap was
    doing spend work that `chars` already does on correctness grounds, and it bit early
    enough to leave citations unread. The character bound is the one that must stay.

    Thread-safe for the same reason `search.QueryBudget` is: writers and critics run
    concurrently, and this counter is shared by the whole run.
    """

    def __init__(self, *, max_calls: int | None, max_chars: int) -> None:
        self._max_calls = max_calls
        self._max_chars = max_chars
        self._used_calls = 0
        self._used_chars = 0
        self._lock = threading.Lock()

    def take_call(self) -> bool:
        with self._lock:
            if self._max_calls is not None and self._used_calls >= self._max_calls:
                return False
            self._used_calls += 1
            return True

    def take_chars(self, wanted: int) -> int:
        """Grant up to `wanted` characters, returning what was actually granted.

        A partial grant rather than a refusal: a page that runs into the last of the
        budget is still worth its first few thousand characters, and the writer is told
        the text is truncated either way.
        """
        with self._lock:
            grant = max(0, min(wanted, self._max_chars - self._used_chars))
            self._used_chars += grant
            return grant

    @property
    def used_calls(self) -> int:
        with self._lock:
            return self._used_calls

    @property
    def used_chars(self) -> int:
        with self._lock:
            return self._used_chars

    @property
    def max_calls(self) -> int | None:
        return self._max_calls

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def exhausted(self) -> bool:
        """Either counter spent. Checked *before* a call is taken, so a read never
        starts against a character budget that cannot pay for its result."""
        with self._lock:
            calls_spent = self._max_calls is not None and self._used_calls >= self._max_calls
            return calls_spent or self._used_chars >= self._max_chars


class ReadSession:
    """One writer call's retrieval memory: what search offered it, and what it read.

    This is the whole of the `read_source` allowlist, and it is scoped to a single
    `LLMClient.complete` call **deliberately**. A run-wide allowlist would let round
    five's writer open a page round one's writer found — a cross-context affordance
    bought for nothing, since a writer has to name a URL and can only have learned one
    by searching. Per-call is the tighter of the two and is the one that matches what
    the tool description promises the model.

    "Per call" is meant literally, and the case that makes it bite is `_generate`'s
    retry loop rather than the next round: a failed or empty completion rotates to the
    **next model in the pool**, so a session shared across attempts would hand writer B
    what writer A's search found, and would have `support.check` rule on bodies B never
    saw. The construction therefore lives inside that loop.

    The read log is the other half of the job: :mod:`.support` needs the bodies this
    writer actually saw in order to check its support spans against them, and the
    session is the only place that knows.

    `cited` seeds a second, separate set: the URLs the draft under revision lists in its
    `## Sources` (D-writer-rereads-cited-sources). It is a function of the draft, which
    every attempt in the retry loop already holds, so seeding it carries nothing from one
    attempt to the next. It is kept apart from `_offered` so `offered_count` still means
    "what search returned".

    With evidence cards on (D-writer-evidence-card) the session also remembers which
    reads were answered with a card, so a repeat read in the same call renders the same
    card, and tallies the reader's activity for the `generate` event. A read recorded
    from a card holds the card's passages as its `text` — what this writer saw — so
    `support.check` and the manifest pass work from exactly that.
    """

    def __init__(self, cited: Iterable[str] = ()) -> None:
        self._offered: set[str] = set()
        self._cited: frozenset[str] = frozenset(url for url in cited if url)
        self._read: dict[str, FetchedSource] = {}
        self._cards: dict[str, Card] = {}
        self._tally = CardTally()
        self._lock = threading.Lock()

    def record_results(self, results: Iterable) -> None:
        """Admit every URL a search result listed. Called by the search tool handler."""
        with self._lock:
            for result in results:
                url = getattr(result, "url", "")
                if url:
                    self._offered.add(url)

    def offered(self, url: str) -> bool:
        with self._lock:
            return url in self._offered

    def admitted(self, url: str) -> str | None:
        """The allowlisted address `url` names, or None when it names none.

        Exact first, then after `fetch._clean_url`, because a writer copying a URL out of
        a sentence brings its trailing punctuation with it. What is returned is always a
        member of one of the two sets, so the string that reaches the fetcher is one the
        allowlist holds — never the writer's variant of it.
        """
        cleaned = _clean_url(url)
        with self._lock:
            for candidate in (url, cleaned):
                if candidate in self._offered or candidate in self._cited:
                    return candidate
        return None

    def readable(self, url: str) -> bool:
        return self.admitted(url) is not None

    def is_cited(self, url: str) -> bool:
        """`url` is one the draft under revision lists (the seed, not search)."""
        return url in self._cited or _clean_url(url) in self._cited

    def record_read(self, source: FetchedSource) -> None:
        with self._lock:
            self._read.setdefault(source.url, source)

    def already_read(self, url: str) -> FetchedSource | None:
        with self._lock:
            return self._read.get(url)

    def record_card(self, url: str, card: Card) -> None:
        with self._lock:
            self._cards.setdefault(url, card)

    def card(self, url: str) -> Card | None:
        """The card this call's read of `url` was answered with, or None — a raw read,
        a fallback, a refusal, or no read at all."""
        with self._lock:
            return self._cards.get(url)

    def record_card_outcome(self, outcome: CardOutcome) -> None:
        with self._lock:
            self._tally.add(outcome)

    @property
    def card_tally(self) -> CardTally:
        with self._lock:
            return replace(self._tally, fallbacks=dict(self._tally.fallbacks))

    @property
    def reads(self) -> dict[str, FetchedSource]:
        """Every `read_source` outcome this call produced, keyed by URL."""
        with self._lock:
            return dict(self._read)

    @property
    def offered_count(self) -> int:
        with self._lock:
            return len(self._offered)

    @property
    def cited_count(self) -> int:
        return len(self._cited)


class SourceReader:
    """Binds the run's fetcher and read budget; the session supplies the allowlist.

    `fetcher` is injected rather than constructed, so the reader and the evidence
    lens's verification path share one run-lifetime cache: a page the writer read is
    not downloaded a second time when the critic checks the citation, and a page that
    failed stays failed for the run rather than being retried into a success halfway
    through (the monotonicity `fetch.SourceFetcher` already promises).
    """

    def __init__(self, fetcher, *, budget: ReadBudget, max_chars: int) -> None:
        self._fetcher = fetcher
        self._budget = budget
        self._max_chars = max_chars

    @property
    def budget(self) -> ReadBudget:
        return self._budget

    def read(
        self,
        url: str,
        session: ReadSession,
        *,
        card: Callable[[FetchedSource], CardOutcome] | None = None,
    ) -> FetchedSource:
        """Resolve one URL for the writer, or say precisely why it was not resolved.

        `card`, when given, builds an evidence card from a full-text body
        (D-writer-evidence-card). A card that is built is charged to the character
        budget at its own length and recorded on the session; anything else — no card
        function, a body that is not full text, a reader fallback — takes today's path
        and hands back the page's opening, clipped as before.

        Never raises. A read that fails is a fact the writer must be told — a writer
        that believes it read a page and saw nothing is worse than one told the page
        was refused, exactly as `search.make_tool_handler` argues for a failed query.
        """
        url = (url or "").strip()
        if not url:
            return FetchedSource(
                url="", error="no URL supplied", outcome=SourceOutcome.ERROR
            )

        # The allowlist, checked before anything else and before any budget is spent:
        # refusing costs nothing, and a refusal must not be purchasable by exhausting
        # the budget first.
        admitted = session.admitted(url)
        if admitted is None:
            return FetchedSource(
                url=url,
                error=(
                    "this URL was not returned by a search in this conversation or listed "
                    "in the draft's ## Sources, so it was not read. Only those can be read."
                ),
                outcome=SourceOutcome.NOT_RETRIEVED,
            )
        url = admitted

        # Re-asking for a page already read in this call is free and returns the same
        # bytes. Charging it again would let a loop spend the run's budget on one page,
        # and handing back different text for the same URL would make the support
        # manifest uncheckable.
        seen = session.already_read(url)
        if seen is not None:
            return seen

        # `exhausted` covers the character counter, `take_call` the call counter — and
        # taking rather than testing is what makes the second one race-free, since a
        # test-then-take could hand two concurrent writers the same last read.
        if self._budget.exhausted or not self._budget.take_call():
            result = FetchedSource(
                url=url,
                error=(
                    f"the source-reading budget for this run is spent "
                    f"({self._budget.max_calls} reads, "
                    f"{self._budget.max_chars} characters). No further pages can be read."
                ),
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )
            session.record_read(result)
            return result

        fetched = self._fetcher.fetch(url)
        if card is not None and fetched.outcome is SourceOutcome.FULL_TEXT and fetched.text:
            carded = self._carded(fetched, card, session)
            if carded is not None:
                session.record_read(carded)
                return carded
        result = self._clipped(fetched)
        session.record_read(result)
        return result

    def _carded(
        self,
        source: FetchedSource,
        card: Callable[[FetchedSource], CardOutcome],
        session: ReadSession,
    ) -> FetchedSource | None:
        """The read as a card, or None to fall back to the raw opening.

        The character budget pays for the passages shown, not for the body the reader
        read: `read_char_budget` bounds what enters a *writer's* context. A partial
        grant keeps the leading passages that fit; a grant that fits none of them is a
        spent budget, exactly as a raw read into an empty budget is.

        The outcome stays the fetch's (`full_text`): the card changes what the writer is
        shown, never what the run knows about the page, so the read log and
        D-read-before-citing see a card-backed read as the full-text read it is.
        """
        outcome = card(source)
        session.record_card_outcome(outcome)
        if outcome.card is None:
            return None
        grant = self._budget.take_chars(outcome.card.chars)
        shown = outcome.card.within(grant)
        if shown is None:
            return FetchedSource(
                url=source.url,
                title=source.title,
                status=source.status,
                error="the source-reading character budget for this run is spent",
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )
        session.record_card(source.url, shown)
        # `truncated`: a card is a selection, so absence from it is never absence from
        # the page, wherever this record is read.
        return replace(source, text=shown.text, truncated=True)

    def _clipped(self, source: FetchedSource) -> FetchedSource:
        """Apply the per-read cap and draw down the run's character budget.

        Only a body costs characters. A registry record, a refusal or a not-found is
        already bounded by `fetch`'s own per-field caps and is the answer the writer
        most needs to hear, so it is never the thing the budget silences.
        """
        if source.outcome is not SourceOutcome.FULL_TEXT:
            return source
        grant = self._budget.take_chars(min(len(source.text), self._max_chars))
        if grant >= len(source.text):
            return source
        if grant <= 0:  # pragma: no cover - `exhausted` is checked before the fetch
            return FetchedSource(
                url=source.url,
                title=source.title,
                status=source.status,
                error="the source-reading character budget for this run is spent",
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )
        return replace(source, text=source.text[:grant])


#: The OpenAI-format tool definition handed to the model, alongside `search.SEARCH_TOOL`.
#: Exactly what every call is offered when no cited URL was seeded — see `read_source_tool`.
READ_SOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_source",
        "description": (
            "Read the text of a page a previous web_search result listed, so you can "
            "quote it and cite the exact place the support appears. A snippet shows "
            "that a page exists; only the body shows what it says. Use this before "
            "attaching a source to a specific claim."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL, copied exactly from a search result in "
                    "this conversation. No other URL can be read.",
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}


#: What changes when the allowlist was seeded with the draft's cited URLs
#: (D-writer-rereads-cited-sources). No address appears here: the definition is trusted
#: text, and the addresses are the draft's, which is not.
_CITED_DESCRIPTION = (
    "Read the text of a page a previous web_search result listed, or a page listed in "
    "the '## Sources' section of the draft report you are revising, so you can quote it "
    "and cite the exact place the support appears. A snippet shows that a page exists; "
    "only the body shows what it says. Use this before attaching a source to a specific "
    "claim."
)
_CITED_URL_DESCRIPTION = (
    "The URL, copied exactly from a search result in this conversation or from the "
    "draft report's ## Sources. No other URL can be read."
)


#: The optional argument evidence cards add (D-writer-evidence-card). It steers which
#: passages are selected and is never echoed back to the writer.
_PURPOSE_PROPERTY = {
    "type": "string",
    "description": "Optional. In a few words, what you need from this page — the claim, "
    "figure, population or period — so the passages returned are chosen for it.",
}


def read_source_tool(cited: bool, cards: bool = False) -> dict:
    """The tool definition for one writer call. `cited` says its session was seeded with
    the draft's cited URLs; `cards` that reads answer with evidence cards, which adds the
    optional `purpose` argument. With neither, today's `READ_SOURCE_TOOL`, unchanged."""
    if not cited and not cards:
        return READ_SOURCE_TOOL
    function = READ_SOURCE_TOOL["function"]
    parameters = function["parameters"]
    properties = dict(parameters["properties"])
    if cited:
        properties["url"] = {**properties["url"], "description": _CITED_URL_DESCRIPTION}
    if cards:
        properties["purpose"] = _PURPOSE_PROPERTY
    return {
        "type": "function",
        "function": {
            **function,
            "description": _CITED_DESCRIPTION if cited else function["description"],
            "parameters": {**parameters, "properties": properties},
        },
    }


def make_tool_handler(
    reader: SourceReader,
    session: ReadSession,
    cards: Callable[[FetchedSource, str], CardOutcome] | None = None,
) -> Callable[[str, str], str]:
    """Bind a reader and one call's session into the `(name, raw_arguments) -> text`
    callback :meth:`LLMClient.complete` drives.

    Every path returns text. A malformed argument, a refused URL and a dead site are
    all things the writer has to know in order to weaken the claim instead of asserting
    it, and none of them is worth aborting a half-written report over.

    `cards(source, purpose)` builds an evidence card (D-writer-evidence-card). A read the
    session recorded with a card renders as one; every other read renders through
    `prompts.source_read_block` exactly as before, so a fallback is never shown as a card.
    """

    def handle(name: str, raw_arguments: str) -> str:
        if name != "read_source":
            return prompts.read_error_block(f"unknown tool {name!r}")
        try:
            arguments = json.loads(raw_arguments or "{}") or {}
            url = str(arguments.get("url", "")).strip()
            purpose = str(arguments.get("purpose", "") or "").strip()
        except (json.JSONDecodeError, AttributeError, TypeError):
            return prompts.read_error_block("arguments were not valid JSON")
        if not url:
            return prompts.read_error_block("no url supplied")

        card_fn = None
        if cards is not None:

            def card_fn(source: FetchedSource) -> CardOutcome:
                return cards(source, purpose)

        result = reader.read(url, session, card=card_fn)
        # The URL itself is never logged (RA-016): a writer composes it from search
        # results chosen while looking at the question and the draft, and events.jsonl
        # outlives a content purge. The outcome is a closed vocabulary and the lengths
        # are numbers, which is enough to debug the tool.
        log.info(
            "read_source -> %s (url %d chars, %d/%s reads, %d/%d chars used)",
            result.outcome.value,
            len(url),
            reader.budget.used_calls,
            "unbounded" if reader.budget.max_calls is None else reader.budget.max_calls,
            reader.budget.used_chars,
            reader.budget.max_chars,
        )
        card = session.card(result.url) if result.ok else None
        if card is not None:
            return prompts.evidence_card_block(result, card)
        return prompts.source_read_block(result)

    return handle
