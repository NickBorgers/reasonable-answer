"""Resolve the URLs a report cites, so the evidence lens can read them.

Search (D-retrieval-opt-in) made citations *real* — the writer can no longer invent a URL. It did
nothing about whether a cited page **supports the claim attached to it**, because no
critic could open it. The evidence lens has always owned two categories it could not
actually falsify:

* ``fabricated_citation`` — a URL that does not resolve. Previously a judgement about
  plausibility; now a fact this module reports.
* ``misrepresented_source`` — a page that does not say what the report claims. Only
  answerable with the page text in hand.

Which of those a given result can speak to depends on *how* the fetch went, so results
carry a :class:`SourceOutcome` rather than a bare success flag. A 404 on a URL nobody
ever published and a 403 from a paywalled newspaper produce the same `error` string and
are entirely different facts; only the first is evidence of anything. Collapsing them is
what obliged :mod:`.prompts` to tell the evidence critic to disregard every failed
fetch — throwing away the one case verification exists to catch.

When the cited URL yields no body at all — which is what a paywalled journal does — an
injected resolver (:mod:`.resolve`, D-existence-vs-body) may still establish that the source *exists*, or
find an open-access copy of it. That is an addition to this module's vocabulary, not to
its reach: the ladder is constructed in `graph._build_resolver` and handed in, so `fetch`
never imports `resolve`, and every provider call goes back out through `http_get` below.

**Not an SSRF boundary** (D-source-verification). This fetches URLs a model chose, which is exposure by
construction; the deployment is expected to constrain egress at the network layer. The
bounds here — timeout, byte cap, redirect cap, http(s) only — exist so one slow or
enormous page cannot stall or exhaust a run, not as a security control. One worked way to
supply that network-layer egress control is in docs/ssrf-egress-isolation.md.

Fetched pages are untrusted third-party text entering a **critic's** context (RA-010),
and reach only the evidence lens — see docs/isolation.md.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from html.parser import HTMLParser
from typing import Any

from . import textconv

log = logging.getLogger(__name__)

#: A plain, honest UA. Some sites 403 an unknown client, and pretending to be a
#: browser to get around that would be the wrong kind of clever.
USER_AGENT = "reasonable-answer/1.0 (citation verification)"

#: HTTP statuses that establish a cited URL does not exist — "not found", not "could
#: not read". 404 (Not Found) and 410 (Gone) are the definitive not-found codes; every
#: other failure class (403, timeout, unreadable content type, empty body) is
#: unreadable, not absent, and must never be read as fabrication
#: (D-notfound-fabrication, docs/convergence.md).
NOT_FOUND_STATUSES = frozenset({404, 410})

_SOURCES_HEADING = re.compile(r"^#{1,6}\s*sources\s*$", re.IGNORECASE | re.MULTILINE)
_URL = re.compile(r"https?://[^\s<>\"'\)\]]+")


@dataclass(frozen=True)
class RawResponse:
    """An un-interpreted http(s) response body, with the bounds already applied.

    Exists so the seed ingest path (which must accept PDFs and DOCX) can reuse the
    hardened opener below without inheriting `SourceFetcher`'s text-only content-type
    gate or its citation-sized character cap.
    """

    url: str
    status: int | None
    content_type: str
    body: bytes
    #: The body hit `max_bytes` and is therefore incomplete. Survivable for text;
    #: fatal for a binary format, whose parser would be handed a mangled file.
    truncated: bool


class SourceOutcome(str, Enum):
    """Why a citation ended up the way it did — a closed vocabulary, not a message.

    The point of the distinction is that `error` alone cannot carry it. A 404 on a URL
    nobody ever published and a 403 from a paywalled newspaper are the same string
    shape and utterly different facts, and collapsing them is what forced
    :mod:`.prompts` to tell the evidence critic to disregard *every* failed fetch —
    discarding the one case where verification could actually catch a fabricated
    citation (D-source-verification, docs/decisions.md).

    `error` keeps the human-readable tail; this carries the machine decision. Two
    fields, two jobs.
    """

    FULL_TEXT = "full_text"
    #: Existence confirmed by a bibliographic registry; the body was not readable.
    METADATA_ONLY = "metadata_only"
    #: Body withheld behind payment, existence corroborated. Deliberately not emitted
    #: yet: HTTP 402 is rare and a real paywall usually returns 200 with a teaser, so
    #: nothing here can honestly distinguish one until a registry can corroborate it.
    PAYWALLED = "paywalled"
    #: The client was refused (403/429/451/999, bot wall). Existence unconfirmed —
    #: which is not the same as absent, and must never be read as fabrication.
    BLOCKED = "blocked"
    #: 404/410. The strongest signal available, and still not proof of fabrication.
    NOT_FOUND = "not_found"
    UNREADABLE = "unreadable"
    EMPTY = "empty"
    ERROR = "error"
    #: A tier that could have resolved this was out of per-run calls. Without this,
    #: an operator reads a column of `blocked` and blames the sites.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: Nothing was fetched: a writer asked `read_source` for a URL that no search
    #: result in its own context had offered (D-writer-source-reads). A refusal, not a
    #: failure — the site was never contacted, so this says nothing whatever about the
    #: source, and it must never be read alongside `blocked` or `not_found`.
    NOT_RETRIEVED = "not_retrieved"


#: Statuses that mean "refused", as distinct from "not there". 999 is LinkedIn's
#: non-standard bot-wall code and shows up in real citation lists. The not-found
#: counterpart is `NOT_FOUND_STATUSES` above, which D-notfound-fabrication already owns.
_BLOCKED_STATUSES = frozenset({401, 402, 403, 405, 406, 423, 429, 451, 999})


class ResolutionTier(str, Enum):
    """Which rung of the ladder produced a result (D-existence-vs-body).

    In the audit trail across *every* source, not only the failures: a tally of
    `{"direct": 5, "open_access": 2, "identifier": 4}` is what tells an operator whether
    a tier is earning the calls it spends. A closed vocabulary for the same reason
    `SourceOutcome` is one — it goes into `events.jsonl`, where free text does not
    belong (RA-016).
    """

    #: The cited URL answered by itself. Every result was this before D-existence-vs-body, and most
    #: still are.
    DIRECT = "direct"
    #: A bibliographic registry answered for the identifier embedded in the cited URL.
    IDENTIFIER = "identifier"
    #: A body arrived from an open-access copy that is not the cited URL.
    OPEN_ACCESS = "open_access"
    #: A rendering service read the cited URL itself (D-paid-tier-page). Distinct from `OPEN_ACCESS`
    #: in the way that matters downstream: this is the cited page's own body, so it
    #: carries no `body_source_url` and may settle a dispute.
    EXTRACTION = "extraction"


class Provider(str, Enum):
    """Which registry answered. Names are safe to log; their request URLs are not —
    see `resolve/base.py`, which owns that rule and the reason for it (RA-016)."""

    CROSSREF = "crossref"
    OPENALEX = "openalex"
    UNPAYWALL = "unpaywall"
    EUROPE_PMC = "europe_pmc"
    ARXIV = "arxiv"
    #: Keyed, and on the paid ladder for that reason rather than for a fee (D-paid-tier-page).
    CORE = "core"
    #: The rendering provider. Renders JavaScript and survives a bot wall; does not and
    #: cannot pass a hard paywall.
    FIRECRAWL = "firecrawl"


#: Per-field caps on registry metadata, in the manner of `search.py`'s `MAX_TITLE_CHARS`.
#: A registry record is third-party text entering a critic's context exactly as a search
#: snippet is, and the field count alone does not bound it: one pathological abstract
#: could otherwise dominate what the evidence lens reads about twelve sources.
MAX_METADATA_TITLE_CHARS = 300
MAX_METADATA_AUTHORS = 12
MAX_METADATA_AUTHOR_CHARS = 120
MAX_METADATA_VENUE_CHARS = 200
MAX_METADATA_DOI_CHARS = 200
MAX_METADATA_ABSTRACT_CHARS = 2_000


@dataclass(frozen=True)
class SourceMetadata:
    """What a bibliographic registry knows about a citation — existence, chiefly (D-existence-vs-body).

    This is deliberately *not* a body. It answers "does this source exist, and is it the
    thing the report says it is", which is the question paywalls make unanswerable and
    which is what stops a real paywalled journal looking like a fabricated citation. It
    cannot answer "does the source support this claim": an abstract is a summary the
    authors wrote, and `prompts` must say so wherever it renders one.

    `registry` names which provider answered, so a reader of the run can tell a Crossref
    record from an arXiv one without consulting the event log.
    """

    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str = ""
    doi: str = ""
    registry: str = ""
    abstract: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _clip(self.title, MAX_METADATA_TITLE_CHARS))
        object.__setattr__(
            self,
            "authors",
            tuple(
                _clip(a, MAX_METADATA_AUTHOR_CHARS)
                for a in self.authors[:MAX_METADATA_AUTHORS]
                if _clip(a, MAX_METADATA_AUTHOR_CHARS)
            ),
        )
        object.__setattr__(self, "venue", _clip(self.venue, MAX_METADATA_VENUE_CHARS))
        object.__setattr__(self, "doi", _clip(self.doi, MAX_METADATA_DOI_CHARS))
        object.__setattr__(
            self, "abstract", _clip(self.abstract, MAX_METADATA_ABSTRACT_CHARS)
        )


def _clip(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


@dataclass(frozen=True)
class FetchedSource:
    """One resolved citation. `error` set means the fetch failed; `text` is then empty.

    Three invariants that callers depend on, and that must survive any change here:

    1. `error is None` **iff** `text` is non-empty **iff** `outcome is FULL_TEXT`.
       `ok` is all three. `dispute.adjudicate_mechanical` gates on `ok and text`, so
       this equivalence is what stops a future non-body outcome (an abstract, say)
       from being quotable evidence in a dispute.
    2. `text` is exactly what the critic was shown. Never populate it with something
       :mod:`.prompts` does not render.
    3. `outcome` is a closed vocabulary safe to put in the audit trail; `error` is free
       text that may embed a URL and is not (RA-016). `tier` and `provider` are closed
       vocabularies too, for the same reason and the same destination.

    D-existence-vs-body adds `body_source_url` and `metadata`, both additive and both defaulting to the
    pre-D-existence-vs-body shape. `body_source_url` is set **only** when `text` came from somewhere
    other than `url` — an open-access mirror — because a preprint is not the version of
    record, and `dispute.adjudicate_mechanical` must refuse to settle a dispute about
    the cited URL on a different copy's wording.
    """

    url: str
    title: str = ""
    text: str = ""
    status: int | None = None
    error: str | None = None
    outcome: SourceOutcome = SourceOutcome.FULL_TEXT
    #: Where `text` actually came from, when that is not `url`. None means the body is
    #: the cited page's own — which is the only case anything may quote against `url`.
    body_source_url: str | None = None
    #: What a registry knows about this citation, when a tier asked one. Present
    #: alongside a body as well as instead of one: the attributed title is checkable
    #: even when the fetch succeeded.
    metadata: SourceMetadata | None = None
    tier: ResolutionTier = ResolutionTier.DIRECT
    provider: Provider | None = None

    def __post_init__(self) -> None:
        # Invariant 1 enforced, not merely documented. A caller that sets `error`
        # without naming an outcome gets an honest generic one rather than a silent
        # claim that the body was read — and an outcome that carries no body can never
        # be constructed as if it did, which is what keeps `ok` trustworthy for
        # `dispute.adjudicate_mechanical`.
        if self.error is not None and self.outcome is SourceOutcome.FULL_TEXT:
            object.__setattr__(self, "outcome", classify_status(self.status))
        elif self.error is None and self.outcome is not SourceOutcome.FULL_TEXT:
            raise ValueError(f"{self.outcome.value} cannot carry readable body text")
        # Same discipline for the mirror field: "the body came from elsewhere" is only
        # meaningful when there is a body. A result that named a mirror while carrying
        # no text would make the dispute guard look satisfied on a page nobody read.
        if self.body_source_url is not None and self.outcome is not SourceOutcome.FULL_TEXT:
            raise ValueError(f"{self.outcome.value} carries no body to have a source")

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def unresolvable(self) -> bool:
        """The fetch *proves* the URL does not resolve — a fact, not a judgement.

        True only for a definitive not-found (HTTP 404 / 410 Gone): the page does not
        exist, which is exactly ``fabricated_citation`` under source verification. Every
        other failure (403, timeout, unreadable content type, empty body) is "could not
        read", which is never evidence of fabrication (D-notfound-fabrication, docs/convergence.md).

        Expressed against `outcome` rather than `status` so the two cannot drift, and so
        a not-found established by something other than an HTTP code — a registry that
        has never heard of the identifier — lands here too when those tiers arrive.
        """
        return self.outcome is SourceOutcome.NOT_FOUND


def classify_status(status: int | None) -> SourceOutcome:
    """What an HTTP status means for a citation, as opposed to for a request.

    `BLOCKED` and `NOT_FOUND` are the distinction that earns this function: the first
    says nothing about whether the source exists, the second is the only evidence this
    system can offer that it does not.
    """
    if status in NOT_FOUND_STATUSES:
        return SourceOutcome.NOT_FOUND
    if status in _BLOCKED_STATUSES:
        return SourceOutcome.BLOCKED
    return SourceOutcome.ERROR


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
        if tag in textconv.SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in textconv.SKIP_TAGS:
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
    ten-round run would otherwise re-download the same four pages ten times. The cache
    is monotone: an entry is written once and never invalidated, so every round of a
    run sees the same evidence and a failure is not silently retried into a success
    halfway through.

    `resolver` is injected rather than imported (D-existence-vs-body). The tiers need `search.QueryBudget`
    and `prompts`, both of which sit downstream of this module, so `fetch -> resolve`
    would close a cycle. `graph._build_resolver` constructs it, exactly as
    `_build_searcher` constructs the searcher, which keeps network I/O out of the graph
    and the test suite offline (D-seed-conversion).
    """

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_bytes: int = 400_000,
        max_chars: int = 6_000,
        max_redirects: int = 3,
        read_pdfs: bool = False,
        pdf_max_bytes: int = 25_000_000,
        pdf_max_pages: int = 40,
        resolver: Any | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_chars = max_chars
        self._max_redirects = max_redirects
        self._read_pdfs = read_pdfs
        self._pdf_max_bytes = pdf_max_bytes
        self._pdf_max_pages = pdf_max_pages
        self._resolver = resolver
        self._cache: dict[str, FetchedSource] = {}
        self._lock = threading.Lock()

    def fetch_all(self, urls: list[str]) -> list[FetchedSource]:
        return [self.fetch(u) for u in urls]

    def fetch(self, url: str) -> FetchedSource:
        with self._lock:
            cached = self._cache.get(url)
        if cached is not None:
            return cached

        result = self._resolved(url)
        with self._lock:
            self._cache[url] = result
        return result

    def _resolved(self, url: str, depth: int = 0) -> FetchedSource:
        """The direct fetch, and — only if it yielded no body — the resolver ladder.

        `depth` is explicit and passed down rather than inferred from a flag or a call
        stack, because the one thing the ladder must never do is recurse: a tier hands
        back an open-access URL, that URL is fetched *once*, and if it too fails it is
        simply a failure. A mirror that redirected into another mirror could otherwise
        walk a run's whole fetch budget on one citation.
        """
        result = self._fetch_uncached(url)
        if result.ok or self._resolver is None or depth > 0:
            return result
        return self._resolver.resolve(
            url,
            result,
            # Bound to depth+1, so whatever the ladder hands back is fetched by the same
            # direct/PDF path and cannot re-enter the ladder.
            fetch_body=lambda mirror: self._resolved(mirror, depth + 1),
        )

    def _fetch_uncached(self, url: str) -> FetchedSource:
        if not url.lower().startswith(("http://", "https://")):
            return FetchedSource(
                url=url, error="not an http(s) URL", outcome=SourceOutcome.ERROR
            )

        try:
            resp = http_get(
                url,
                timeout=self._timeout,
                max_bytes=self._max_bytes,
                max_redirects=self._max_redirects,
                accept=self._accept(),
                # Don't spend the byte budget on a body this can't read anyway — and
                # not on a PDF either, whose own cap is sixty times larger and is
                # applied on the second request below.
                want_body=lambda ct: "html" in ct or "text" in ct,
            )
        except urllib.error.HTTPError as exc:
            return FetchedSource(
                url=url,
                status=exc.code,
                error=f"HTTP {exc.code}",
                outcome=classify_status(exc.code),
            )
        except Exception as exc:
            return FetchedSource(
                url=url,
                error=f"{type(exc).__name__}: {exc}"[:200],
                outcome=SourceOutcome.ERROR,
            )

        status = resp.status
        if "html" not in resp.content_type and "text" not in resp.content_type:
            if self._read_pdfs and _looks_like_pdf(url, resp.content_type):
                return self._fetch_pdf(url)
            # Anything else is a legitimate citation this cannot read. Saying so is
            # more useful than pretending the page was empty.
            return FetchedSource(
                url=url,
                status=status,
                error=f"unreadable content type ({resp.content_type or 'unknown'})",
                outcome=SourceOutcome.UNREADABLE,
            )

        try:
            body = resp.body.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - decode with errors= never raises
            return FetchedSource(
                url=url,
                status=status,
                error=f"decode failed: {exc}",
                outcome=SourceOutcome.ERROR,
            )

        parser = _TextExtractor()
        try:
            parser.feed(body)
        except Exception as exc:  # malformed HTML
            log.debug("parse failed for %s: %s", url, exc)
        text = parser.text[: self._max_chars]
        if not text.strip():
            return FetchedSource(
                url=url,
                status=status,
                title=parser.title,
                error="no readable text",
                outcome=SourceOutcome.EMPTY,
            )
        return FetchedSource(
            url=url,
            title=parser.title,
            text=text,
            status=status,
            outcome=SourceOutcome.FULL_TEXT,
        )

    def _accept(self) -> str:
        base = "text/html,text/plain;q=0.9"
        return f"{base},application/pdf;q=0.8" if self._read_pdfs else base

    def _fetch_pdf(self, url: str) -> FetchedSource:
        """Re-fetch the same URL under the PDF byte cap and convert it.

        A second request rather than one large first request: the citation cap is
        400 KB precisely so an enormous *page* cannot exhaust a run, and raising it for
        everything to accommodate PDFs would give that up. The first request read no
        body at all (`want_body` declined the content type), so what is repeated is the
        round trip, not the download.
        """
        try:
            resp = http_get(
                url,
                timeout=self._timeout,
                max_bytes=self._pdf_max_bytes,
                max_redirects=self._max_redirects,
                accept="application/pdf",
            )
        except urllib.error.HTTPError as exc:
            return FetchedSource(
                url=url,
                status=exc.code,
                error=f"HTTP {exc.code}",
                outcome=classify_status(exc.code),
            )
        except Exception as exc:
            return FetchedSource(
                url=url,
                error=f"{type(exc).__name__}: {exc}"[:200],
                outcome=SourceOutcome.ERROR,
            )

        if resp.truncated:
            # A truncated PDF is a mangled file, not a shorter document, and pypdf
            # given one either raises or emits nonsense. Refusing is the honest
            # outcome; the same rule governs a truncated seed (ingest.from_url).
            return FetchedSource(
                url=url,
                status=resp.status,
                error=f"PDF exceeds the {self._pdf_max_bytes}-byte cap and cannot be parsed",
                outcome=SourceOutcome.UNREADABLE,
            )

        try:
            markdown = textconv.pdf_to_markdown(resp.body, max_pages=self._pdf_max_pages)
        except textconv.ConversionError as exc:
            return FetchedSource(
                url=url,
                status=resp.status,
                error=str(exc)[:200],
                outcome=SourceOutcome.UNREADABLE,
            )

        text = " ".join(markdown.split())[: self._max_chars]
        if not text.strip():
            # Overwhelmingly a scanned paper with no text layer. Distinguish it from an
            # empty HTML page: nothing about this URL will ever get better, whereas an
            # EMPTY page might be a rendering problem.
            return FetchedSource(
                url=url,
                status=resp.status,
                error="the PDF carries no text layer (a scan, most likely)",
                outcome=SourceOutcome.UNREADABLE,
            )
        return FetchedSource(
            url=url,
            text=text,
            status=resp.status,
            outcome=SourceOutcome.FULL_TEXT,
        )


class CappedFetcher:
    """A read-through view of a :class:`SourceFetcher` that clips bodies to one
    consumer's character cap (D-writer-source-reads).

    Two consumers now share one fetcher so that a page is downloaded once and both see
    the same bytes — but they do not share a cap. The underlying fetcher must therefore
    store the *larger* of the two, and the smaller consumer needs its own limit applied
    somewhere. Here, rather than at each render site, because there are three of them
    (`prompts.fetched_sources_block`, `dispute.adjudicate_mechanical`, and the
    evidence-URL check beside it) and one of them decides whether a defect is suppressed:
    a longer body makes `adjudicate_mechanical`'s containment test more likely to uphold
    a dispute, so an unclipped view would let `search.read_sources` change the stop
    decision. `search.verify_sources` alone must decide what the verification path sees,
    and that is only true if the cap travels with the handle rather than with the cache.

    Clipping is a no-op when the caps are equal, which is the default and every
    configuration that leaves `read_max_chars` at or below `fetch_max_chars`.
    """

    def __init__(self, inner: SourceFetcher, *, max_chars: int) -> None:
        self._inner = inner
        self._max_chars = max_chars

    def fetch(self, url: str) -> FetchedSource:
        return clip_body(self._inner.fetch(url), self._max_chars)

    def fetch_all(self, urls: list[str]) -> list[FetchedSource]:
        return [self.fetch(u) for u in urls]


def clip_body(source: FetchedSource, max_chars: int) -> FetchedSource:
    """Trim a fetched body to `max_chars`, leaving every other outcome untouched.

    Only a `FULL_TEXT` result carries a body; a registry record, a refusal or a
    not-found is already bounded by this module's own per-field caps and is the answer
    a consumer most needs, so it is never what a cap silences.
    """
    if source.outcome is not SourceOutcome.FULL_TEXT or len(source.text) <= max_chars:
        return source
    return replace(source, text=source.text[:max_chars])


def _looks_like_pdf(url: str, content_type: str) -> bool:
    """Content type first, then the path.

    The path check is not redundant: repositories routinely serve a PDF as
    `application/octet-stream`, and the magic bytes are unavailable here because the
    body was deliberately not read.
    """
    if "pdf" in content_type:
        return True
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".pdf")


def http_get(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_redirects: int = 3,
    accept: str,
    user_agent: str = USER_AGENT,
    want_body: Callable[[str], bool] | None = None,
) -> RawResponse:
    """Fetch one http(s) URL through the bounded, http(s)-only opener.

    The plain-GET shape, which is every fetch of a page some model named: citation
    verification and seed ingest. No credential is involved, so no redirect allowlist is
    imposed — a cited page legitimately redirects wherever its publisher likes.

    Raises whatever `urllib` raises; callers map exceptions to their own error shape.
    `want_body`, when given, is consulted with the lowercased content-type *before* the
    body is read: returning False yields an empty body rather than spending the byte
    budget on something the caller cannot use.
    """
    return _request(
        url,
        method="GET",
        data=None,
        headers={"User-Agent": user_agent, "Accept": accept},
        timeout=timeout,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        want_body=want_body,
    )


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
    max_redirects: int = 3,
    want_body: Callable[[str], bool] | None = None,
    allowed_hosts: frozenset[str] | None = None,
) -> RawResponse:
    """The single egress point for the whole package.

    Everything that leaves this process for the network arrives here, so
    `_http_only_opener` and `_BoundedRedirects` stay the only way out. That is the
    reason this exists apart from `http_get`: a provider API needing a POST and an
    `Authorization` header would otherwise have had to build its own opener, and an
    exemption from the hardened path is exactly what the hardened path is for.

    `allowed_hosts` names the hosts a *redirect* may land on; None permits any, which is
    what a cited page needs. A caller sending a credential should pass both a host set
    and `max_redirects=0` — the credential is stripped on any redirect regardless (see
    `_BoundedRedirects`), but a request carrying a key has no business being
    redirectable at all.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"not an http(s) URL: {url}")

    opener = _http_only_opener(max_redirects, allowed_hosts)
    req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310
        status = getattr(resp, "status", None)
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if want_body is not None and not want_body(ctype):
            return RawResponse(url, status, ctype, b"", truncated=False)
        # One byte past the cap: enough to tell a body that just fits from one that
        # was cut off, which decides whether a binary parse is safe to attempt.
        raw = resp.read(max_bytes + 1)

    truncated = len(raw) > max_bytes
    return RawResponse(url, status, ctype, raw[:max_bytes], truncated)


def _http_only_opener(
    max_redirects: int, allowed_hosts: frozenset[str] | None = None
) -> urllib.request.OpenerDirector:
    """An opener that can speak http(s) and nothing else.

    `build_opener()` installs `FTPHandler`, `FileHandler` and `DataHandler` alongside
    the HTTP ones, so the *default* opener can service `ftp:`, `file:` and `data:` URLs.
    Assembling the director by hand means an unexpected scheme has no handler at all,
    rather than relying solely on the scheme checks to catch it.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        # No-arg ProxyHandler reads HTTP_PROXY/HTTPS_PROXY/NO_PROXY from the
        # environment. The egress-isolation deployment (docs/ssrf-egress-isolation.md)
        # puts this process on a network whose only internet path is that proxy;
        # without this handler every fetch there dead-ends instead of egressing.
        urllib.request.ProxyHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        _BoundedRedirects(max_redirects, allowed_hosts),
    ):
        opener.add_handler(handler)
    return opener


#: The only request headers allowed to survive a redirect. An allowlist rather than a
#: list of credential names, because the credential names are the part nobody can
#: enumerate: `Authorization`, `X-API-Key`, `X-Subscription-Token`, `Api-Key`, `Cookie`
#: and whatever the next provider invents all have to be dropped, and a blocklist that
#: misses one replays a key at a host nobody here chose. Everything this package sends
#: on its own behalf is content negotiation or identification, so default-deny costs
#: nothing today and cannot rot as callers are added.
_REDIRECT_SAFE_HEADERS = frozenset(
    {"user-agent", "accept", "accept-language", "accept-encoding"}
)


class _BoundedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but not forever, not out of http(s), and never with a secret.

    Python's stock handler allows a redirect target whose scheme is `http`, `https`
    **or `ftp`** (`HTTPRedirectHandler.http_error_302`), and `build_opener()` installs
    an `FTPHandler` by default. Checking only the *initial* URL therefore does not give
    http(s)-only fetching: a cited page can 302 verification into a different egress
    protocol. This narrows the allowlist to what the module actually claims.

    It also copies every request header except content-length/content-type onto the
    redirect target — cross-host, with no comparison of old host to new
    (`redirect_request` in CPython's urllib/request.py). So the moment `_request` grew a
    `headers` argument, a provider endpoint that 302s could hand our API key to whatever
    host it named. Two independent guards, because either alone is thin: the credential
    is stripped from the follow-up request, and `allowed_hosts` bounds where a follow-up
    may be sent at all.
    """

    def __init__(self, limit: int, allowed_hosts: frozenset[str] | None = None) -> None:
        self.max_redirections = limit
        #: Hosts a redirect may land on, or None for any. None is right for a cited
        #: page, which legitimately redirects wherever its publisher likes; a request
        #: carrying a credential should name the one host it trusts.
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Count the hops ourselves. The stock handler consults `max_redirections` only
        # once `redirect_dict` exists, which is to say from the *second* hop onwards —
        # so a limit of N actually permits N+1, and a limit of 0 permits one. That
        # off-by-one is cosmetic for a cited page and load-bearing for a request that
        # carries a credential, where a single hop is enough to replay an API key at a
        # host nobody here chose.
        if len(getattr(req, "redirect_dict", None) or ()) >= self.max_redirections:
            raise urllib.error.HTTPError(
                newurl, code, f"refused redirect past the cap: {newurl}", headers, fp
            )
        if not newurl.lower().startswith(("http://", "https://")):
            raise urllib.error.HTTPError(
                newurl, code, f"refused redirect to non-http(s) URL: {newurl}", headers, fp
            )
        if self.allowed_hosts is not None:
            host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
            if host not in self.allowed_hosts:
                raise urllib.error.HTTPError(
                    newurl,
                    code,
                    f"refused redirect to a host outside the allowlist: {host}",
                    headers,
                    fp,
                )

        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            # Strip the credential the stock handler just copied over. Unconditional,
            # including onto an allowlisted host: the allowlist says the hop is
            # somewhere this caller expects to talk to, not that the key was issued for
            # it, and a redirect within a provider's own CDN is still a second place
            # that key comes to rest.
            new.headers = {
                k: v for k, v in new.headers.items() if k.lower() in _REDIRECT_SAFE_HEADERS
            }
        return new
