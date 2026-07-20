"""Web search as a model tool: the Brave Search API behind a budgeted, throttled client.

Three properties this module owns, all of them load-bearing:

1. **The credential never reaches a prompt.** It is read from the environment (prod)
   or a gitignored file (local dev) and lives only in a request header.
2. **A query budget is enforced per run, not per call.** The free tier is 2,000
   queries/month; an unbounded agentic loop across writers and revisions would drain
   it in an afternoon. When the budget is gone the tool returns an explicit
   "budget exhausted" result rather than silently returning nothing — a writer that
   believes it searched and found nothing is worse than one told it cannot search.
3. **Results are data, never instructions.** This module returns structured records;
   :mod:`.prompts` is what fences them. Search results are the most untrusted text in
   the system — arbitrary attacker-controlled web pages entering a model's context —
   so RA-010 applies to them with full force.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import prompts

log = logging.getLogger(__name__)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class SearchError(RuntimeError):
    """Search backend failure. Never fatal to a run — surfaced to the model as text."""


class SearchConfigError(RuntimeError):
    """Missing/unreadable credential. Fatal at startup, before any tokens are spent."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str
    age: str | None = None


def resolve_token(env_var: str, token_file: str | Path | None) -> str:
    """Environment first, file second.

    Env wins when both are present so a production deployment never silently reads a
    stale checked-out file that happens to be lying around the image.
    """
    value = os.environ.get(env_var)
    if value and value.strip():
        return value.strip()

    if token_file:
        path = Path(token_file)
        if path.exists():
            content = path.read_text().strip()
            if content:
                return content
            raise SearchConfigError(f"fail closed: token file {path} is empty")

    raise SearchConfigError(
        f"fail closed: web search is enabled but no credential was found. "
        f"Set ${env_var}, or place the key in {token_file or '<token_file unset>'}."
    )


class QueryBudget:
    """Process-wide query counter. Thread-safe: critics and writers run concurrently."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._used >= self._limit:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._used >= self._limit


class BraveSearch:
    """Brave Search API client.

    Throttled to `min_interval` seconds between requests because the free tier caps at
    1 request/second and `max_concurrency` writers/critics would otherwise burst
    straight into HTTP 429.
    """

    def __init__(
        self,
        token: str,
        *,
        budget: QueryBudget,
        max_results: int = 5,
        timeout: float = 20.0,
        min_interval: float = 1.1,
    ) -> None:
        self._token = token
        self._budget = budget
        self._max_results = max_results
        self._timeout = timeout
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    @property
    def budget(self) -> QueryBudget:
        return self._budget

    def search(self, query: str, count: int | None = None) -> list[SearchResult]:
        query = (query or "").strip()
        if not query:
            raise SearchError("empty query")
        if not self._budget.take():
            raise SearchError(
                f"search budget exhausted for this run "
                f"({self._budget.limit} queries). No further searches are possible."
            )
        self._throttle()

        count = max(1, min(count or self._max_results, 20))
        url = f"{BRAVE_ENDPOINT}?" + urllib.parse.urlencode({"q": query, "count": count})
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # 429 is the one a caller can act on (back off); everything else is opaque.
            raise SearchError(f"brave search HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            raise SearchError(f"brave search failed: {exc}") from exc

        return _parse_results(payload)

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


def _parse_results(payload: dict) -> list[SearchResult]:
    raw = (payload.get("web") or {}).get("results") or []
    out: list[SearchResult] = []
    for entry in raw:
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        out.append(
            SearchResult(
                title=_clean(entry.get("title")),
                url=url,
                description=_clean(entry.get("description")),
                age=entry.get("age") or entry.get("page_age") or None,
            )
        )
    return out


def _clean(value: str | None) -> str:
    """Brave marks query-term matches with <strong> tags; strip the markup so it does
    not read as structure once the result is fenced into a prompt."""
    text = (value or "").replace("<strong>", "").replace("</strong>", "")
    return " ".join(text.split())


def make_tool_handler(client: BraveSearch) -> Callable[[str, str], str]:
    """Bind a search client into the (name, raw_arguments) -> result-text callback
    that :meth:`LLMClient.complete` drives.

    Every failure path returns *text describing the failure* rather than raising: a
    search that 429s or runs out of budget must not abort a half-written report, but
    the model has to be told, or it will read the silence as "nothing exists".
    """

    def handle(name: str, raw_arguments: str) -> str:
        if name != "web_search":
            return prompts.search_error_block(f"unknown tool {name!r}")
        try:
            query = str((json.loads(raw_arguments or "{}") or {}).get("query", "")).strip()
        except (json.JSONDecodeError, AttributeError, TypeError):
            return prompts.search_error_block("arguments were not valid JSON")
        if not query:
            return prompts.search_error_block("no query supplied")
        try:
            results = client.search(query)
        except SearchError as exc:
            log.warning("search failed for %r: %s", query, exc)
            return prompts.search_error_block(str(exc))
        log.info("search %r -> %d results (%d/%d used)",
                 query, len(results), client.budget.used, client.budget.limit)
        return prompts.search_results_block(query, results)

    return handle


#: The OpenAI-format tool definition handed to the model.
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the live web for current information and real, citable sources. "
            "Use this before asserting any material fact you are not certain of, and "
            "to obtain the real URL and title of every source you cite."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific; prefer several "
                    "narrow queries over one broad one.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
