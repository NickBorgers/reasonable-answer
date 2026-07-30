"""Tier 2: a rendering service reads the cited URL when this process cannot (D-paid-tier-page).

**What this buys, precisely.** A rendering provider runs a real browser, so it reads a
page whose text arrives via JavaScript, and it is not refused by the bot walls that turn
away an unknown HTTP client. That is a large share of why citation fetches fail. It is
**not** a way past a hard paywall: a subscription wall serves a teaser to a browser too,
and no setting here changes that. Anyone reading "paid tier" as "universal access" should
stop at this paragraph.

**Where the line is, and why it is not a config option.** Every provider in this market
also sells a stealth mode — residential IP rotation, fingerprint randomisation, anti-bot
defeat — and on Firecrawl it is one string away (`proxy: "stealth"`, or `"auto"`, which
escalates into it silently on failure). That is the industrial form of the browser
impersonation `fetch.py` has refused since D-source-verification, and D-existence-vs-body
records as doctrine: a system
whose whole claim is that its citations are checkable cannot obtain them by circumventing
the access controls of the people who published them.

So `proxy` is pinned to `"basic"` in `_SCRAPE_OPTIONS` and there is no knob to change it.
Not defaulted-off — absent. `tests/test_extraction.py` asserts the request body carries
`basic` and contains no stealth mode anywhere, which turns the doctrine from a comment
into something CI fails on. Rendering a page is not disguising who is asking for it, and
only the first is in scope.

The body this returns is the cited URL's *own* body, not a copy from somewhere else, so
it carries no `body_source_url` and may settle a dispute — unlike an open-access mirror
(D-existence-vs-body). It is the same page, read by a better client.
"""

from __future__ import annotations

import logging

from ..fetch import Provider
from . import base

log = logging.getLogger(__name__)

#: What is asked of the renderer, and the whole of it. `formats: ["markdown"]` because
#: the caller wants prose for a critic, not a DOM; `onlyMainContent` drops the nav and
#: cookie banners that would otherwise eat the character budget a critic reads.
#:
#: `proxy: "basic"` is the doctrine (see the module docstring). "auto" is specifically
#: excluded rather than merely not chosen: it *starts* basic and silently escalates to
#: stealth when a site refuses, which would make the system's behaviour toward a bot wall
#: depend on whether the bot wall worked.
_SCRAPE_OPTIONS = {
    "formats": ["markdown"],
    "onlyMainContent": True,
    "proxy": "basic",
}

#: Mode strings that must never appear in a request this module builds. Named here so the
#: test asserting their absence reads as an intent rather than as three string literals.
FORBIDDEN_PROXY_MODES = frozenset({"stealth", "auto"})


class Firecrawl:
    """`api.firecrawl.dev/v2/scrape` — one URL in, rendered markdown out.

    One reference implementation rather than three half-tested ones. The registry in
    `resolve/__init__.py` is open, and the shape another provider would have to satisfy is
    exactly this: `name`, `extract(url) -> str | None`, raising `ProviderUnavailable` when
    it could not answer.
    """

    name = Provider.FIRECRAWL
    ENDPOINT = "https://api.firecrawl.dev/v2/scrape"

    def __init__(self, *, api_key: str, timeout: float) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def extract(self, url: str) -> str | None:
        """The page's markdown, or None when the provider could read nothing.

        None is "no body", never "the page does not exist" — this provider has no
        standing to say the latter, and the ladder must not read it that way. A URL the
        renderer 404s on is still only `NOT_FOUND` if the *cited* fetch said so, or if a
        registry denied the identifier.
        """
        payload = base.json_post(
            self.ENDPOINT,
            payload={"url": url, **_SCRAPE_OPTIONS},
            api_key=self._api_key,
            timeout=self._timeout,
            # A rendered article is prose, and prose is bigger than a registry record.
            max_bytes=2_000_000,
        )
        if payload is None:
            return None
        if not payload.get("success", True):
            # The provider said it failed. Its own message is third-party text about a
            # URL private to this run, so only the fact travels (RA-016).
            raise base.ProviderUnavailable("provider reported failure")
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        markdown = data.get("markdown")
        return markdown if isinstance(markdown, str) and markdown.strip() else None


#: Extraction providers, by config name. Open for a second implementation; `provider: ""`
#: with the tier enabled is fatal at load rather than defaulting into whichever entry
#: happens to be first, because a paid call should never go to a vendor nobody named.
EXTRACTION_PROVIDERS = {"firecrawl": Firecrawl}
