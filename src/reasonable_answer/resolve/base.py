"""What a provider is, and the one rule every provider in this package obeys.

**Never log a provider request URL** (RA-016). A provider *name* is a member of a closed
vocabulary (`fetch.Provider`) and is safe in a log line or an audit event; the URL it was
asked at is not, for two independent reasons:

* the polite-pool querystring carries the operator's contact email (`?mailto=`,
  `?email=`), which is personal data with no business in ordinary process logs; and
* the querystring is where vendors generally put an API token, so a codebase in the
  habit of logging provider URLs leaks the first credential-bearing provider it gains.

The identifier is no better: it is derived from a URL the report cited, which is private
run material living under the mode-0700 `runs/<id>/` tree. So what may be logged is the
provider name, the identifier *kind*, and an outcome class — never the identifier, never
the URL, and never an exception's `str()`, which several `urllib` errors build out of the
request URL. `search.BraveSearch` keeps only the exception type for exactly this reason.

`json_get`, `text_get` and `json_post` below are the only network calls in this package,
and they go through `fetch.http_get`/`fetch._request` — the single egress point for the
whole codebase. No module under `resolve/` imports `urllib.request`;
`tests/test_resolve.py` enforces that mechanically, so a future contributor cannot open a
second way out by accident.
"""

from __future__ import annotations

import json

# The exception taxonomy of the opener in `fetch`, not a second way to reach the
# network: `urllib.error` builds no opener and opens no socket. Distinguishing a
# definitive 404 from "the provider could not answer" is the whole point of importing it,
# and that distinction is what stops a flaky registry reading as a fabricated citation.
import urllib.error
import urllib.parse
from typing import Protocol

from .. import fetch
from ..fetch import Provider, SourceMetadata
from .identifiers import Identifier, IdKind

#: Registry responses are small. OpenAlex's inverted-index abstract is the largest thing
#: any of these returns and still fits comfortably; a provider that answers with more
#: than this is malfunctioning, and truncating its JSON yields a parse failure — which
#: is reported as "could not answer", never as "the record is absent".
MAX_RESPONSE_BYTES = 250_000

#: Every provider endpoint is a fixed first-party host answering a GET directly, so a
#: redirect has no legitimate purpose here — and the querystring carries the operator's
#: contact email, which CPython's redirect handler would copy onto whatever host the
#: redirect names. Same reasoning as `search._no_redirect_opener`, one step weaker in
#: consequence because an email is not a credential.
_MAX_REDIRECTS = 0


class ProviderUnavailable(RuntimeError):
    """The provider could not answer.

    Categorically different from "the registry has no such record", which is a `None`
    return. Collapsing the two is how a timed-out Crossref call would become evidence
    that a real paper does not exist — a blocking `fabricated_citation` manufactured out
    of a network condition. Every provider here raises this rather than returning None
    when it is unsure.
    """


class MetadataProvider(Protocol):
    """Tier 0: does this source exist, and what is it?"""

    name: Provider

    def supports(self, kind: IdKind) -> bool:
        """Whether this provider can be asked about that kind of identifier at all."""

    def authoritative(self, kind: IdKind) -> bool:
        """Whether this provider's *denial* is worth anything for that kind.

        The distinction that keeps D-notfound-fabrication honest. Europe PMC will happily answer a DOI
        query and will find nothing for a particle-physics paper, because its coverage is
        biomedical — its silence there is a coverage gap, not evidence of absence. Only a
        provider that is authoritative for the kind may contribute to the conclusion that
        no registry has heard of an identifier.
        """

    def metadata(self, ident: Identifier) -> SourceMetadata | None:
        """The record, or None when the registry definitively has no such record.

        Raises `ProviderUnavailable` when it could not answer; the caller must never read
        that as absence.
        """


class OpenAccessProvider(Protocol):
    """Tier 1: is there a copy of the body that can be read without payment?"""

    name: Provider

    def supports(self, kind: IdKind) -> bool: ...

    def open_access_url(self, ident: Identifier) -> str | None:
        """A URL for a free full-text copy, or None when the provider knows of none.

        Raises `ProviderUnavailable` when it could not answer.
        """


def json_get(url: str, *, timeout: float, accept: str = "application/json") -> dict | None:
    """The decoded JSON object, or None when the registry has no such record.

    None means a definitive 404/410 from the provider — the tri-state the whole ladder
    rests on. Everything else raises `ProviderUnavailable`, carrying the exception *type*
    and nothing more (RA-016: see this module's docstring).
    """
    raw = _get(url, timeout=timeout, accept=accept)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderUnavailable(f"malformed JSON: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ProviderUnavailable(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def text_get(url: str, *, timeout: float, accept: str) -> str | None:
    """As `json_get`, for the one provider that answers in something other than JSON."""
    raw = _get(url, timeout=timeout, accept=accept)
    return None if raw is None else raw.decode("utf-8", errors="replace")


def json_post(
    url: str,
    *,
    payload: dict,
    api_key: str,
    timeout: float,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> dict | None:
    """A credentialled POST to a provider, through the same hardened opener (D-paid-tier-page).

    The paid tiers need what `json_get` cannot express: a request body and an
    `Authorization` header. `fetch._request` is where that lives, so this stays inside
    the single-egress-point claim rather than becoming the exception that hollows it out.

    Two things are not configurable, both for the same reason. The host is taken from the
    caller's own constant endpoint, and `_request` is given `allowed_hosts` naming only
    that host with `max_redirects=0` — a request carrying an API key has no business
    being redirectable, and `fetch._BoundedRedirects` strips the key on any redirect
    anyway. Belt and braces, because a leaked key is not a recoverable mistake.
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    body = json.dumps(payload).encode("utf-8")
    try:
        response = fetch._request(
            url,
            method="POST",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": fetch.USER_AGENT,
            },
            timeout=timeout,
            max_bytes=max_bytes,
            max_redirects=0,
            allowed_hosts=frozenset({host.lower()}),
        )
    except urllib.error.HTTPError as exc:
        if exc.code in fetch.NOT_FOUND_STATUSES:
            return None
        raise ProviderUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise ProviderUnavailable(type(exc).__name__) from exc
    if response.truncated:
        raise ProviderUnavailable(f"response exceeded {max_bytes} bytes")
    try:
        decoded = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderUnavailable(f"malformed JSON: {type(exc).__name__}") from exc
    if not isinstance(decoded, dict):
        raise ProviderUnavailable(f"expected a JSON object, got {type(decoded).__name__}")
    return decoded


def _get(url: str, *, timeout: float, accept: str) -> bytes | None:
    try:
        response = fetch.http_get(
            url,
            timeout=timeout,
            max_bytes=MAX_RESPONSE_BYTES,
            max_redirects=_MAX_REDIRECTS,
            accept=accept,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in fetch.NOT_FOUND_STATUSES:
            return None
        raise ProviderUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise ProviderUnavailable(type(exc).__name__) from exc
    if response.truncated:
        raise ProviderUnavailable(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    return response.body


__all__ = [
    "MAX_RESPONSE_BYTES",
    "MetadataProvider",
    "OpenAccessProvider",
    "ProviderUnavailable",
    "json_get",
    "json_post",
    "text_get",
]
