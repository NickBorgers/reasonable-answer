"""The tier ladder: what to try when the cited URL itself yields no body (D39).

Source verification (D18) fetches the pages a report cites so the evidence lens can check
them. Most *good* citations fail that fetch — paywalled journals and newspapers refuse
automated clients as a matter of course — and D38 then leaves them looking identical to
citations that could not be checked for any other reason.

The asymmetry this package is built on: **for citation verification you usually do not
need the paywalled body.** Two questions matter, and they are not equally expensive.

1. *Does this source exist?* Free, from bibliographic registries, and answering it is
   what stops a real paywalled paper looking like a fabricated one.
2. *Does it say what the report claims?* Needs the body, and often there is no lawful,
   non-clever way to get one.

So tier 0 always answers (1); tier 1 tries for (2) where a free copy genuinely exists; and
tier 2 (D40) pays a rendering service to read the cited URL when this process cannot —
which buys JavaScript and bot walls, and buys nothing at all against a hard paywall.

Ordered by cost rather than by likelihood. Extraction is the likelier fix for a news
citation, and still runs last, because a registry answer is worth having even on a source
whose body later arrives: it is what lets a critic check the *title* the report attributes
rather than only its prose.

What this package will **not** do is listed in D39 and D40 and is not negotiable: no
browser-user-agent spoofing, no CAPTCHA solving, no archive.org paywall laundering, no
cookie-jar credential replay — and, for the rendering tier specifically, no stealth mode,
which is that same impersonation bought by the page. `fetch.py`'s "the wrong kind of
clever" comment is doctrine.

**Three caches, three key spaces, one lock.**

* the final `FetchedSource` per cited URL — `SourceFetcher`'s own, upstream of here;
* `SourceMetadata` per normalised identifier, so two URLs naming one DOI share a single
  Crossref call;
* identifier -> best open-access URL, where a stored `None` means *asked, and none
  exists*. Distinguishing that from *not asked* is what stops a twelve-source report
  making twelve redundant Unpaywall calls for the same absence.

All three are **monotone within a run**: an entry is written once and never re-resolved,
never invalidated, never retried. Every round of a run therefore judges the same
evidence, and a provider that was flaky at round two does not become authoritative at
round six. The two caches here share one `threading.Lock` because critics run at
concurrency 3 and a second lock would only introduce an ordering to get wrong; it is
never held across a network call.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

from ..fetch import (
    FetchedSource,
    Provider,
    ResolutionTier,
    SourceMetadata,
    SourceOutcome,
)
from ..search import QueryBudget
from . import identifiers
from .base import MetadataProvider, OpenAccessProvider, ProviderUnavailable
from .extraction import EXTRACTION_PROVIDERS
from .identifiers import Identifier
from .scholarly import PROVIDERS, ApiKeyRequired, ContactEmailRequired, UnknownProvider

log = logging.getLogger(__name__)

#: Direct outcomes that establish nothing about whether the source exists, and may
#: therefore be overwritten by a registry's denial. A 2xx that merely could not be *read*
#: (UNREADABLE, EMPTY) is excluded on purpose: the server served something, so the URL
#: resolves, and no registry's coverage gap may turn that into a fabricated citation.
#: BLOCKED is excluded for the same reason — a 403 is a live server refusing a client,
#: which is evidence about the client, not about the document.
_UNESTABLISHED = frozenset({SourceOutcome.NOT_FOUND, SourceOutcome.ERROR})

#: The direct outcome that, combined with a registry's confirmation, means "paywalled".
#: Never inferred from a status code alone: HTTP 402 is vanishingly rare and a real
#: paywall usually answers 200 with a teaser, so the corroboration is the whole claim.
_REFUSED = SourceOutcome.BLOCKED


@dataclass(frozen=True)
class _Known:
    """Tier 0's conclusion about one identifier, as cached.

    `exists` is tri-state and the distinction is load-bearing: True confirms, False is a
    denial by every authoritative registry consulted, and None means nobody could answer
    — which must never be read as either.
    """

    exists: bool | None
    metadata: SourceMetadata | None = None
    provider: Provider | None = None
    #: A provider that could have answered was out of per-run calls. Recorded so the
    #: operator sees `budget_exhausted` rather than a column of `blocked` and a wrong
    #: conclusion about the sites.
    starved: bool = False


class SourceResolver:
    """Runs the ladder for one run. Construct via `build`, not directly."""

    def __init__(
        self,
        *,
        metadata_providers: list[MetadataProvider] | None = None,
        open_access_providers: list[OpenAccessProvider] | None = None,
        metadata_budget: QueryBudget | None = None,
        open_access_budget: QueryBudget | None = None,
        extractor=None,
        extraction_budget: QueryBudget | None = None,
        max_chars: int = 6_000,
    ) -> None:
        self._metadata_providers = list(metadata_providers or [])
        self._open_access_providers = list(open_access_providers or [])
        self._metadata_budget = metadata_budget or QueryBudget(0)
        self._open_access_budget = open_access_budget or QueryBudget(0)
        self._extractor = extractor
        self._extraction_budget = extraction_budget or QueryBudget(0)
        #: The critic-facing character cap. Every other rung gets this applied by
        #: `SourceFetcher`, which reads the body; extraction receives markdown straight
        #: from a provider, so it is the one rung that must apply the cap itself.
        self._max_chars = max_chars
        self._known: dict[str, _Known] = {}
        self._oa_urls: dict[str, tuple[str, Provider] | None] = {}
        self._lock = threading.Lock()

    def resolve(self, url: str, direct: FetchedSource, fetch_body) -> FetchedSource:
        """The whole ladder: the free rungs, then the paid one if they came up short.

        Ordered by cost, not by likelihood. The free tiers run first even though
        extraction is the likelier fix for a news citation, because a registry answer is
        worth having on a source whose body later arrives anyway — it is what lets a
        critic check the *title* the report attributes, not just the prose.
        """
        resolved = self._free_ladder(url, direct, fetch_body)
        if resolved.ok or self._extractor is None:
            return resolved
        return self._extract(url, resolved)

    def _free_ladder(self, url: str, direct: FetchedSource, fetch_body) -> FetchedSource:
        """Registries and open-access copies, given a direct fetch that yielded no body.

        `fetch_body` re-enters the direct/PDF path exactly once for an open-access URL;
        `SourceFetcher` binds the depth, so nothing here can recurse.
        """
        ident = identifiers.extract(url)
        if ident is None:
            # Nothing to ask a registry about. The overwhelming majority of news and
            # blog citations land here, and leaving the direct verdict untouched is the
            # correct, quiet outcome.
            return direct

        known = self._identify(ident)
        body: FetchedSource | None = None
        provider: Provider | None = None
        oa_starved = False
        if known.exists is not False:
            # Not chased when every authoritative registry has denied the identifier:
            # there is no open-access copy of a paper nobody has heard of, and the call
            # would spend budget to confirm a foregone conclusion.
            body, provider, oa_starved = self._open_access(ident, fetch_body)

        if body is not None:
            return replace(
                body,
                # The cited URL stays the identity of the result: this is still that
                # citation, read from somewhere else. `body_source_url` is what tells
                # every consumer — the prompt, the dispute guard — that it is a copy.
                url=url,
                body_source_url=body.url,
                metadata=known.metadata,
                tier=ResolutionTier.OPEN_ACCESS,
                provider=provider,
            )

        if known.exists is True:
            outcome = (
                SourceOutcome.PAYWALLED
                if direct.outcome is _REFUSED
                else SourceOutcome.METADATA_ONLY
            )
            return FetchedSource(
                url=url,
                status=direct.status,
                error=f"{direct.error}; {known.provider.value} confirms the source exists",
                outcome=outcome,
                metadata=known.metadata,
                tier=ResolutionTier.IDENTIFIER,
                provider=known.provider,
            )

        if known.exists is False and direct.outcome in _UNESTABLISHED:
            # The only path in this package that can raise a blocking defect (D38 mints
            # `fabricated_citation` from `unresolvable`), so it is gated twice over: every
            # authoritative registry consulted denied the identifier, AND the direct fetch
            # established nothing either. A refusal or an unreadable 200 keeps its own
            # verdict.
            return replace(
                direct,
                error=f"{direct.error}; no registry has a record of {ident.kind.value}",
                outcome=SourceOutcome.NOT_FOUND,
                tier=ResolutionTier.IDENTIFIER,
                provider=known.provider,
            )

        if (known.starved or oa_starved) and direct.outcome is not SourceOutcome.NOT_FOUND:
            # NOT_FOUND is deliberately exempt. Overwriting it here would let a run that
            # exhausts a tier's budget at source five silently stop reporting D38's
            # mechanical finding for sources six through twelve — turning a tier on would
            # weaken a defect the pipeline already raises without it.
            return replace(
                direct,
                error=f"{direct.error}; the resolver was out of calls for this run",
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )

        return direct

    # ------------------------------------------------------------------ tier 2

    def _extract(self, url: str, resolved: FetchedSource) -> FetchedSource:
        """Ask the rendering provider for the cited page, keeping what tier 0 learned.

        Tried on every outcome the free ladder could not resolve except `NOT_FOUND`:
        there is nothing for a renderer to render at a URL the server says is not there,
        and spending a paid call to confirm that would also risk a success overwriting
        D38's mechanical finding on the strength of a soft-404 landing page.
        """
        if resolved.outcome is SourceOutcome.NOT_FOUND:
            return resolved
        if not self._extraction_budget.take():
            # Never over NOT_FOUND, which the guard above already returned — the same
            # rule the free tiers follow, and for the same reason: turning a tier on must
            # not weaken a defect the pipeline raises without it.
            return replace(
                resolved,
                error=f"{resolved.error}; the resolver was out of extraction calls",
                outcome=SourceOutcome.BUDGET_EXHAUSTED,
            )
        try:
            markdown = self._extractor.extract(url)
        except ProviderUnavailable as exc:
            log.info(
                "resolver: %s could not answer (%s)", self._extractor.name.value, exc
            )
            return resolved
        if not markdown:
            return resolved

        return FetchedSource(
            url=url,
            text=" ".join(markdown.split())[: self._max_chars],
            status=resolved.status,
            outcome=SourceOutcome.FULL_TEXT,
            # No `body_source_url`. This *is* the cited page, read by a client that can
            # run its JavaScript — not a copy from somewhere else. So unlike an
            # open-access mirror it may settle a dispute (D39's guard in `dispute.py`
            # keys off exactly that field), and saying so here is the whole distinction.
            metadata=resolved.metadata,
            tier=ResolutionTier.EXTRACTION,
            provider=self._extractor.name,
        )

    # ------------------------------------------------------------------ tier 0

    def _identify(self, ident: Identifier) -> _Known:
        with self._lock:
            cached = self._known.get(ident.key)
        if cached is not None:
            return cached

        found: SourceMetadata | None = None
        provider_name: Provider | None = None
        denials = 0
        starved = False
        for provider in self._metadata_providers:
            if not provider.supports(ident.kind):
                continue
            if not self._metadata_budget.take():
                starved = True
                break
            try:
                record = provider.metadata(ident)
            except ProviderUnavailable as exc:
                # Only the provider name and the failure class (RA-016) — never the
                # identifier, which is derived from a URL private to this run.
                log.info("resolver: %s could not answer (%s)", provider.name.value, exc)
                continue
            if record is None:
                if provider.authoritative(ident.kind):
                    denials += 1
                continue
            if found is None:
                found, provider_name = record, provider.name
            # Deliberately not `break`. Crossref is cheap and gives the citation details;
            # OpenAlex is what tier 1 then reads `best_oa_location` from, and asking it
            # here is what puts that answer in the cache. Running tier 0 to completion is
            # the point — the metadata is useful even when a body arrives.

        if found is not None:
            known = _Known(exists=True, metadata=found, provider=provider_name)
        elif denials and not starved:
            known = _Known(exists=False, starved=False)
        else:
            known = _Known(exists=None, starved=starved)

        with self._lock:
            # setdefault, not assignment: monotone means the first writer wins, so two
            # critics resolving the same DOI concurrently cannot disagree about it later.
            known = self._known.setdefault(ident.key, known)
        return known

    # ------------------------------------------------------------------ tier 1

    def _open_access(
        self, ident: Identifier, fetch_body
    ) -> tuple[FetchedSource | None, Provider | None, bool]:
        """(body, provider, starved). A body only when a free copy both exists and reads."""
        with self._lock:
            cached = self._oa_urls.get(ident.key, _MISSING)
        if cached is not _MISSING:
            # None here means "asked, and none exists" — the distinction that stops a
            # twelve-source report making twelve identical Unpaywall calls.
            if cached is None:
                return None, None, False
            url, provider_name = cached
            return self._read(url, fetch_body), provider_name, False

        found: tuple[str, Provider] | None = None
        starved = False
        for provider in self._open_access_providers:
            if not provider.supports(ident.kind):
                continue
            if not self._open_access_budget.take():
                starved = True
                break
            try:
                candidate = provider.open_access_url(ident)
            except ProviderUnavailable as exc:
                log.info("resolver: %s could not answer (%s)", provider.name.value, exc)
                continue
            if candidate:
                found = (candidate, provider.name)
                break  # the next provider would answer the same question at a cost

        if starved and found is None:
            # Not cached: the budget refused the question, so it was never asked, and
            # recording "asked, none" would be a lie the rest of the run believed.
            return None, None, True

        with self._lock:
            found = self._oa_urls.setdefault(ident.key, found)
        if found is None:
            return None, None, False
        url, provider_name = found
        return self._read(url, fetch_body), provider_name, False

    def _read(self, url: str, fetch_body) -> FetchedSource | None:
        page = fetch_body(url)
        if page.ok and page.text:
            return page
        # A mirror that will not read is simply not a body. The direct fetch's verdict
        # stands and the source falls back to metadata, which is the honest description
        # of what was actually learned.
        return None


class _Missing:
    """Sentinel: `None` is a real cached answer here, so it cannot mean 'absent'."""


_MISSING = _Missing()


def build(
    *,
    identifier_providers: list[str],
    identifier_timeout: float,
    identifier_budget: int,
    open_access_providers: list[str],
    open_access_timeout: float,
    open_access_budget: int,
    contact_email: str = "",
    core_api_key: str = "",
    extraction_provider: str = "",
    extraction_api_key: str = "",
    extraction_timeout: float = 45.0,
    extraction_budget: int = 0,
    max_chars: int = 6_000,
) -> tuple[SourceResolver, list[str]]:
    """Construct the resolver and whatever warnings its construction earned.

    A disabled tier is passed an empty provider list and therefore constructs nothing —
    no client, no budget spent, no possibility of a call. Enabling one tier can never
    turn on the other, which is what the two config switches are for.

    Raises `UnknownProvider` for a name this package does not implement; the caller turns
    that into a fail-closed startup error.
    """
    warnings: list[str] = []
    metadata_providers: list[MetadataProvider] = []
    open_access: list[OpenAccessProvider] = []

    for name in identifier_providers:
        built = _construct(name, identifier_timeout, contact_email, warnings, core_api_key)
        if built is not None and hasattr(built, "metadata"):
            metadata_providers.append(built)
        elif built is not None:
            raise UnknownProvider(f"'{name}' answers open-access questions, not existence")

    for name in open_access_providers:
        built = _construct(name, open_access_timeout, contact_email, warnings, core_api_key)
        if built is not None and hasattr(built, "open_access_url"):
            open_access.append(built)
        elif built is not None:
            raise UnknownProvider(f"'{name}' answers existence questions, not open access")

    extractor = _construct_extractor(extraction_provider, extraction_api_key, extraction_timeout)

    return (
        SourceResolver(
            metadata_providers=metadata_providers,
            open_access_providers=open_access,
            metadata_budget=QueryBudget(identifier_budget),
            open_access_budget=QueryBudget(open_access_budget),
            extractor=extractor,
            extraction_budget=QueryBudget(extraction_budget) if extractor else None,
            max_chars=max_chars,
        ),
        warnings,
    )


def _construct_extractor(name: str, api_key: str, timeout: float):
    """The rendering provider, or None when the tier is off.

    No default provider. An enabled tier with no name is refused by the caller before it
    gets here, because falling back to whichever entry happens to be first in the
    registry would send a paid call to a vendor nobody chose.
    """
    if not name:
        return None
    factory = EXTRACTION_PROVIDERS.get(name)
    if factory is None:
        raise UnknownProvider(
            f"unknown extraction provider '{name}'; known providers are "
            f"{sorted(EXTRACTION_PROVIDERS)}"
        )
    return factory(api_key=api_key, timeout=timeout)


def _construct(
    name: str, timeout: float, contact_email: str, warnings: list[str], api_key: str = ""
):
    factory = PROVIDERS.get(name)
    if factory is None:
        raise UnknownProvider(
            f"unknown source provider '{name}'; known providers are "
            f"{sorted(PROVIDERS)}"
        )
    # A declared flag, not a `TypeError` probe on the constructor. Probing looked tidy
    # and was wrong twice over: it cannot tell "this provider takes no key" from "this
    # provider was called wrongly", and a retry inside the handler escapes the
    # `ContactEmailRequired` arm below, since an `except` clause does not cover code
    # running in another one.
    extra = {"api_key": api_key} if getattr(factory, "NEEDS_API_KEY", False) else {}
    try:
        return factory(timeout=timeout, contact_email=contact_email, **extra)
    except ApiKeyRequired:
        # Fatal rather than skipped, unlike a missing contact email. A keyed provider
        # without a key makes no successful call ever, so continuing would spend the
        # tier's budget on 401s and report them as coverage.
        raise
    except ContactEmailRequired:
        warnings.append(
            f"sources: provider '{name}' needs a contact email and is being skipped; "
            f"set the environment variable named by sources.contact_email_env"
        )
        return None


__all__ = ["SourceResolver", "UnknownProvider", "build"]
