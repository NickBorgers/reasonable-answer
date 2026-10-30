## D-renderer-identity — the rendering tier reads as the browser its provider presents; the direct fetch stays honest

> Amends **D-paid-tier-page**, whose title line — "never disguise who is asking for it" — was not
> true of the provider it integrated, and **D-existence-vs-body**, whose "no browser-user-agent
> spoofing" no longer covers the extraction tier. Both stand as the record of what was intended.

**The finding.** D-paid-tier-page drew its line at Firecrawl's `proxy` mode: `basic` was taken to
be "read it as a normal client" and `stealth` to be the disguise. Measured on 2026-10-29, `basic`
is itself a disguise. A scrape of a header-echo page shows what the provider sends on our behalf:

| header | value |
|---|---|
| `User-Agent` | `Mozilla/5.0 (X11; Linux x86_64) … Chrome/155.0.0.0 Safari/537.36` |
| `Sec-Ch-Ua` | `"Google Chrome";v="155", "Chromium";v="155", …` |
| `Referer` | `https://www.google.com/` |
| `Sec-Fetch-Site` / `Sec-Fetch-User` | `cross-site` / `?1` |

That is a desktop Chrome user arriving from a Google result — not a citation checker. The
`proxy` pin and its test were correct about residential-IP rotation and said nothing about this,
because the identity is the provider's default rather than anything this repository sends.

**What the disguise buys.** Most of the tier. Over 79 cited URLs (38 from production reports, 41
journal, news and government pages), counting pages that yielded a long article body:

| client | long body |
|---|---|
| direct fetch, `fetch.USER_AGENT` | 21 |
| self-hosted headless Chromium, `HeadlessChrome` user agent | 27 |
| self-hosted headless Chromium, `fetch.USER_AGENT` | 39 |
| direct fetch, then Firecrawl `basic` (the production ladder) | 68 |

Firecrawl accepts a `headers` override, and with `User-Agent` set to `fetch.USER_AGENT` (the
`Referer` and client hints are not overridable) eight of ten hard walls — NEJM, WSJ, Reuters,
SAGE, CBO, JAMA, Health Affairs, RAND — failed with a provider HTTP 500; a control run minutes
later with the default identity read WSJ and CBO. An honest renderer exists and is the third row.
Choosing it would cost roughly 28 of those 79 bodies.

**The decision.** The extraction tier reads cited pages under whatever browser identity its
provider presents by default. This deployment is one operator collecting information for their own
research, and a citation-checking system that cannot read the cited page is weaker at the one
thing it claims; that is judged to outweigh presenting as a browser to a site that would refuse a
declared bot. No self-hosted browser is added: on top of the production ladder it read 4 more of
the 79.

**What does not change.**

- `fetch.USER_AGENT` stays the identity of every request this process composes — the direct
  fetch, the registries, Brave, and the credentialled POST to the provider itself. Nothing here
  licenses a browser user agent in `fetch.py`; it would not pass a JavaScript challenge anyway.
- `proxy` stays pinned to `basic`; `stealth` and `auto` stay in `FORBIDDEN_PROXY_MODES` with no
  configuration field. Residential-IP rotation borrows other people's addresses, which is a
  different act from a browser header, and costs five credits a page besides.
- No CAPTCHA solving, no archive.org paywall laundering, no cookie-jar or institutional
  credential replay (D-existence-vs-body). A hard paywall still serves a teaser to this browser.
- Rendered text is still third-party content reaching only the evidence lens (RA-010).

**The request carries no `headers` field.** The identity accepted here is the one measured above;
a code path that composed its own headers would change it unmeasured, in either direction.
`tests/test_extraction.py` asserts the serialised body has none, next to the `proxy` assertion.

**For anyone else running this.** The extraction tier ships disabled. Enabling it now means
accepting this identity, and the roster comment says so.

**Not done.** A bot-challenge or login-wall page that a rung returns is still counted as a body
read — production critics have described cited sources as "a captcha page" and "a cookie consent
page", and Firecrawl returned JSTOR's login wall as a long body. Detecting those is independent of
this decision and is left to its own change.

**Invariants.** Untouched. This changes what a third-party server is told about the client, not
what enters any context, who critiques what, or how the controller decides.
