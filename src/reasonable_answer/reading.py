"""Reading a retrieved source, as a writer tool (D-writer-source-reads).

Search (D-retrieval-opt-in) made a citation *real*: the writer can only cite a URL a
result actually returned. It did not make the citation *supported*. A Brave result is a
title, a URL and a one-line snippet, so a writer choosing what to claim has never seen
the page it is about to cite; bodies were fetched only afterwards, and only for a critic
(D-source-verification). Pinpoint locators, direct quotation and reliable claim/source
matching were therefore unavailable at the one moment they decide what the report says.

This module closes that gap and nothing wider. Four properties are load-bearing:

1. **No arbitrary-URL reader.** `read_source` accepts only a URL that a `web_search`
   result *in the same writer call* listed. The allowlist is the
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
spent), plus one this path adds: `not_retrieved`, for a URL the writer never saw.
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
from .fetch import FetchedSource, SourceOutcome

log = logging.getLogger(__name__)


class ReadBudget:
    """Whole-run caps on `read_source`: how many pages, and how much of them.

    Two counters, because they bound two different failures. `calls` bounds egress and
    wall clock — a writer that opens every result of every search across ten rounds is a
    crawler, not an author. `chars` bounds the *context*: a per-page cap cannot see the
    total, and twenty pages at the per-page cap is a prompt no model reads well, which
    is a correctness property here rather than a cost one (principle #6,
    docs/isolation.md).

    Thread-safe for the same reason `search.QueryBudget` is: writers and critics run
    concurrently, and this counter is shared by the whole run.
    """

    def __init__(self, *, max_calls: int, max_chars: int) -> None:
        self._max_calls = max_calls
        self._max_chars = max_chars
        self._used_calls = 0
        self._used_chars = 0
        self._lock = threading.Lock()

    def take_call(self) -> bool:
        with self._lock:
            if self._used_calls >= self._max_calls:
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
    def max_calls(self) -> int:
        return self._max_calls

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def exhausted(self) -> bool:
        """Either counter spent. Checked *before* a call is taken, so a read never
        starts against a character budget that cannot pay for its result."""
        with self._lock:
            return self._used_calls >= self._max_calls or self._used_chars >= self._max_chars


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
    """

    def __init__(self) -> None:
        self._offered: set[str] = set()
        self._read: dict[str, FetchedSource] = {}
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

    def record_read(self, source: FetchedSource) -> None:
        with self._lock:
            self._read.setdefault(source.url, source)

    def already_read(self, url: str) -> FetchedSource | None:
        with self._lock:
            return self._read.get(url)

    @property
    def reads(self) -> dict[str, FetchedSource]:
        """Every `read_source` outcome this call produced, keyed by URL."""
        with self._lock:
            return dict(self._read)

    @property
    def offered_count(self) -> int:
        with self._lock:
            return len(self._offered)


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

    def read(self, url: str, session: ReadSession) -> FetchedSource:
        """Resolve one URL for the writer, or say precisely why it was not resolved.

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
        if not session.offered(url):
            return FetchedSource(
                url=url,
                error=(
                    "this URL was not returned by any web_search in this conversation, "
                    "so it was not read. Only search results can be read."
                ),
                outcome=SourceOutcome.NOT_RETRIEVED,
            )

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

        result = self._clipped(self._fetcher.fetch(url))
        session.record_read(result)
        return result

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


def make_tool_handler(
    reader: SourceReader, session: ReadSession
) -> Callable[[str, str], str]:
    """Bind a reader and one call's session into the `(name, raw_arguments) -> text`
    callback :meth:`LLMClient.complete` drives.

    Every path returns text. A malformed argument, a refused URL and a dead site are
    all things the writer has to know in order to weaken the claim instead of asserting
    it, and none of them is worth aborting a half-written report over.
    """

    def handle(name: str, raw_arguments: str) -> str:
        if name != "read_source":
            return prompts.read_error_block(f"unknown tool {name!r}")
        try:
            url = str((json.loads(raw_arguments or "{}") or {}).get("url", "")).strip()
        except (json.JSONDecodeError, AttributeError, TypeError):
            return prompts.read_error_block("arguments were not valid JSON")
        if not url:
            return prompts.read_error_block("no url supplied")

        result = reader.read(url, session)
        # The URL itself is never logged (RA-016): a writer composes it from search
        # results chosen while looking at the question and the draft, and events.jsonl
        # outlives a content purge. The outcome is a closed vocabulary and the lengths
        # are numbers, which is enough to debug the tool.
        log.info(
            "read_source -> %s (url %d chars, %d/%d reads, %d/%d chars used)",
            result.outcome.value,
            len(url),
            reader.budget.used_calls,
            reader.budget.max_calls,
            reader.budget.used_chars,
            reader.budget.max_chars,
        )
        return prompts.source_read_block(result)

    return handle
