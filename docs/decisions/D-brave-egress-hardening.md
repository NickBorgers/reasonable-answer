## D-brave-egress-hardening — the Brave search request carries the fetch user agent and a bounded read

**The finding.** `BraveSearch.search` (`search.py`) built its `urllib.request.Request` with no
`User-Agent` header at all, so the request egressed under urllib's Python-version-dependent default
— directly contradicting docs/deployment-profile.md's "the outbound user agent is fixed" and
AGENTS.md's "the outbound user agent is fixed… tests assert it". No test anywhere asserted the
claim for this path; the one `User-Agent` literal in the test suite (`tests/test_fetch.py`'s
`_CREDENTIALLED` fixture) was unrelated canned data that did not even match `fetch.USER_AGENT`'s
real value, so it could not have caught the gap. The same request also read its response with a bare
`resp.read()` — no byte cap — while the neighbouring fetch-mediated paths named here
(`fetch.SourceFetcher`'s `max_bytes`, `resolve/base.py`'s `json_post`) bound what they will read from
an endpoint that answers with more than expected.

**The decision.** `search.py` imports `fetch.USER_AGENT` rather than duplicating the string, and
sends it on the Brave request exactly as `resolve/base.py`'s `json_post` does. The read is bounded
to a new `search.MAX_RESPONSE_BYTES` (500,000 — double `resolve/base.py`'s `MAX_RESPONSE_BYTES`,
because Brave's JSON envelope carries fields this module never parses — `meta_url` objects,
thumbnails, alternate result types — alongside the `web.results` `_parse_results` actually reads),
read one byte past the cap in the same style as `fetch._request`, and refused with a `SearchError`
when the body is oversized rather than silently truncated and parsed. `tests/test_search.py` pins
three things directly: the request headers carry exactly `fetch.USER_AGENT`, a response at the cap
parses normally, and a response one byte past it is refused — plus a bare assertion on
`fetch.USER_AGENT`'s literal value, so a future change to the constant trips a test instead of
silently drifting the two egress paths apart again. `tests/test_fetch.py`'s stale fixture now reads
`USER_AGENT` from `fetch` rather than a copy of it, so it can never again go stale relative to what
ships.

The response cap is the anti-pathological retrieval bound permitted by QP10 and
D-unbounded-evidence, not a spend control. Brave's JSON envelope is untrusted endpoint output, so
the transport must not accept an arbitrarily large body; reading one byte past the cap makes the
bound explicit and refuses the search with `SearchError` rather than silently truncating a response
and presenting missing results as evidence absence.

**What was deliberately left alone.** The Brave request's opener (`_no_redirect_opener`, i.e.
`fetch._http_only_opener(0)`) still names no `allowed_hosts`, unlike `json_post`'s paid-tier POSTs.
This is not an omission this decision closes: at `max_redirects=0`, `fetch._BoundedRedirects`
raises on the very first redirect attempt before it ever reaches the host check, so a host
allowlist on this particular request would guard nothing a redirect cap of zero does not already
guard. docs/deployment-profile.md is corrected to say this rather than to claim a host allowlist
that was never true for this path.

**Invariants.** None of the six is in reach. This changes a request header and adds a byte cap to
one egress path; no model call, prompt, critic assignment, severity, or controller rule is touched.
