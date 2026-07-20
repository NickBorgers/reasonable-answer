"""Resolve the URLs a report cites, so the evidence lens can read them.

Search (RA-018) made citations *real* — the writer can no longer invent a URL. It did
nothing about whether a cited page **supports the claim attached to it**, because no
critic could open it. The evidence lens has always owned two categories it could not
actually falsify:

* ``fabricated_citation`` — a URL that does not resolve. Previously a judgement about
  plausibility; now a fact this module reports.
* ``misrepresented_source`` — a page that does not say what the report claims. Only
  answerable with the page text in hand.

**Not an SSRF boundary.** This fetches URLs a model chose, which is exposure by
construction; the deployment is expected to constrain egress at the network layer. The
bounds here — timeout, byte cap, redirect cap, http(s) only — exist so one slow or
enormous page cannot stall or exhaust a run, not as a security control.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

log = logging.getLogger(__name__)

#: A plain, honest UA. Some sites 403 an unknown client, and pretending to be a
#: browser to get around that would be the wrong kind of clever.
USER_AGENT = "reasonable-answer/1.0 (citation verification)"

_SOURCES_HEADING = re.compile(r"^#{1,6}\s*sources\s*$", re.IGNORECASE | re.MULTILINE)
_URL = re.compile(r"https?://[^\s<>\"'\)\]]+")
_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


@dataclass(frozen=True)
class FetchedSource:
    """One resolved citation. `error` set means the fetch failed; `text` is then empty."""

    url: str
    title: str = ""
    text: str = ""
    status: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def extract_source_urls(report: str, limit: int = 20) -> list[str]:
    """Every URL in the report's '## Sources' section, in order, deduplicated.

    Scoped to that section deliberately: a URL mentioned in passing in the body is not
    a citation the report is standing behind, and fetching it would spend budget on
    something no claim depends on.
    """
    match = _SOURCES_HEADING.search(report or "")
    if not match:
        return []
    tail = report[match.end() :]
    # Stop at the next heading — '## Sources' is conventionally last, but nothing
    # guarantees it.
    next_heading = re.search(r"^#{1,6}\s+\S", tail, re.MULTILINE)
    if next_heading:
        tail = tail[: next_heading.start()]

    seen: list[str] = []
    for raw in _URL.findall(tail):
        url = raw.rstrip(".,;:")
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


class _TextExtractor(HTMLParser):
    """Visible text only. Not a renderer — just enough to tell whether a page says
    what a report claims it says."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())[:300]

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class SourceFetcher:
    """Fetches and caches cited pages for the lifetime of a run.

    Cached by URL because the same '## Sources' list is re-verified on every round; a
    ten-round run would otherwise re-download the same four pages ten times.
    """

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_bytes: int = 400_000,
        max_chars: int = 6_000,
        max_redirects: int = 3,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_chars = max_chars
        self._max_redirects = max_redirects
        self._cache: dict[str, FetchedSource] = {}
        self._lock = threading.Lock()

    def fetch_all(self, urls: list[str]) -> list[FetchedSource]:
        return [self.fetch(u) for u in urls]

    def fetch(self, url: str) -> FetchedSource:
        with self._lock:
            cached = self._cache.get(url)
        if cached is not None:
            return cached

        result = self._fetch_uncached(url)
        with self._lock:
            self._cache[url] = result
        return result

    def _fetch_uncached(self, url: str) -> FetchedSource:
        if not url.lower().startswith(("http://", "https://")):
            return FetchedSource(url=url, error="not an http(s) URL")

        opener = urllib.request.build_opener(_BoundedRedirects(self._max_redirects))
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
        )
        try:
            with opener.open(req, timeout=self._timeout) as resp:  # noqa: S310
                status = getattr(resp, "status", None)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "html" not in ctype and "text" not in ctype:
                    # A PDF is a legitimate citation but not something this can read.
                    # Saying so is more useful than pretending the page was empty.
                    return FetchedSource(
                        url=url,
                        status=status,
                        error=f"unreadable content type ({ctype or 'unknown'})",
                    )
                raw = resp.read(self._max_bytes)
        except urllib.error.HTTPError as exc:
            return FetchedSource(url=url, status=exc.code, error=f"HTTP {exc.code}")
        except Exception as exc:
            return FetchedSource(url=url, error=f"{type(exc).__name__}: {exc}"[:200])

        try:
            body = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - decode with errors= never raises
            return FetchedSource(url=url, status=status, error=f"decode failed: {exc}")

        parser = _TextExtractor()
        try:
            parser.feed(body)
        except Exception as exc:  # malformed HTML
            log.debug("parse failed for %s: %s", url, exc)
        text = parser.text[: self._max_chars]
        if not text.strip():
            return FetchedSource(
                url=url, status=status, title=parser.title, error="no readable text"
            )
        return FetchedSource(url=url, title=parser.title, text=text, status=status)


class _BoundedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but not forever."""

    def __init__(self, limit: int) -> None:
        self.max_redirections = limit
