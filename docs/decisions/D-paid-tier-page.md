## D-paid-tier-page — the paid tiers render a page, and never disguise who is asking for it

**The problem.** D-existence-vs-body made a citation's *existence* verifiable for free, which stops a paywalled
paper looking like a fabricated one. It did nothing for the other half: a body that never arrives
because the page needs JavaScript, or because a bot wall refuses an unknown HTTP client. Those are
not paywalls and they are not fabrications — they are a client capability gap, and the honest fix is
a better client.

**The decision.** Two more rungs on D-existence-vs-body's ladder, both off by default and both fail-closed at
startup. `sources.extraction` sends the cited URL to a rendering service (Firecrawl is the one
reference implementation; the registry is open) and takes back markdown. `sources.delivery` is a
config shape and a registry entry with **no provider behind it**.

**The line, which is the whole decision.** A rendering provider can be pointed at a page in two
registers: read it as a normal client, or disguise the client to defeat anti-bot defences —
residential IP rotation, fingerprint randomisation. On the provider integrated here, Firecrawl, the
second is a `proxy` mode, and `resolve/extraction.py` pins the request to `proxy: "basic"` while
naming `"stealth"` and `"auto"` in `FORBIDDEN_PROXY_MODES` as values it must never send — `"auto"`
because it starts basic and escalates into stealth silently when a site refuses. That is the
industrial form of the browser impersonation `fetch.py` has refused since D-source-verification and D-existence-vs-body records as
doctrine, bought by the page instead of coded by hand. **Rendering a page is not disguising who is
asking for it, and only the first is in scope.**

So `proxy` is pinned to `"basic"` in `resolve/extraction.py` and there is no configuration field
that can change it — absent, not defaulted-off, because a knob makes doctrine an operator
preference. `tests/test_extraction.py` asserts against the serialised request body rather than the
options constant, so a future code path assembling its own payload is caught too. The doctrine is
now something CI fails on rather than a comment somebody can delete. It is also, incidentally, one
credit per page instead of five; that is a coincidence and not the argument.

**What the paid tier does and does not buy.** It reads JavaScript-rendered pages and is not turned
away by the bot walls that refuse an unknown HTTP client — two reasons a cited body fails to arrive
that are neither a paywall nor a fabrication. It does **not** pass a hard paywall: a subscription
wall serves a teaser to a real browser too. Anyone reading "paid tier" as "universal access" is
wrong, and the module docstring, the roster comment and this record all say so, because that
misreading is the one most likely to be made.

**Why delivery ships empty.** A document-delivery provider would return a paywalled body under some
licensing terms, and whether those terms permit splicing a delivered document into a model's context
is a licensing question, not an engineering one — one this repository has not answered. Building a
speculative adapter against an API nobody here holds credentials for produces untested code that
will be wrong when someone finally needs it, so the seam exists with no provider behind it. That the
seam is *inert rather than half-built* is enforced, not merely asserted: a `SourcesConfig` validator
makes `sources.delivery.enabled: true` with `provider: ""` fatal at load, because a tier that can
name no provider can make no call.

**Ordering, and why extraction runs last.** By cost, not by likelihood. Extraction is the likelier
fix for a news citation and still runs after the free rungs, because a registry answer is worth
having even on a source whose body later arrives: it is what lets a critic check the *title* the
report attributes rather than only its prose. Extraction is skipped entirely against a definitive
not-found — there is nothing to render at a URL the server says is not there, and a success against
a soft-404 landing page would overwrite D-notfound-fabrication's mechanical `fabricated_citation`.

**A rendered body may settle a dispute; a mirror may not.** D-existence-vs-body refuses adjudication on anything
carrying `body_source_url`, because an open-access preprint is a *different document* from the
version of record. A rendered page is the cited URL itself read by a better client, so it carries no
such marker and stays usable. That distinction is the reason `ResolutionTier.EXTRACTION` exists
separately from `OPEN_ACCESS` rather than both being "we got the body somehow".

**Credentials.** `FIRECRAWL_API_KEY` and `CORE_API_KEY`, resolved through `search.resolve_token` —
environment first, gitignored file second — and both passed into the container explicitly in
`compose.yaml`. A tier enabled without its key refuses to start, unlike D-existence-vs-body's contact email, which
is a courtesy and only warns: a keyed provider with no key makes no successful call ever, so
starting would spend the tier's whole budget on 401s and report them as coverage. An enabled tier
naming no provider is fatal for the same reason a default would be wrong — a paid call must never go
to a vendor nobody chose.

CORE joins the open-access tier here rather than in D-existence-vs-body because it is keyed, and inherits this
fail-closed posture for that reason alone. It is deliberately absent from the default provider list,
so that enabling open access does not silently become "and also supply a CORE key or fail to boot".

**The call ceiling bounds a bug, not a bill.** Unset, `extraction.max_calls_per_run` derives from
`search.max_sources * budgets.hard_cap` — the most distinct URLs a run could ever cite, every
citation replaced every round. Derived rather than written down so raising `hard_cap` cannot
silently start starving the tier at the old number. It is generous on purpose: `SourceFetcher`
caches per URL for the whole run, so three critics re-verifying one `## Sources` list across eight
rounds cost one call per URL rather than twenty-four, and what remains to guard against is a fetch
loop that ignores that cache.

**Egress.** Unchanged, and narrower than what already passes. These are fixed first-party hosts, and
the credentialled POST goes through `fetch._request` — the same hardened opener as everything else —
with `allowed_hosts` naming only the provider's own host and a redirect cap of zero. Belt and
braces: `_BoundedRedirects` strips the credential on any redirect regardless, because a leaked key
is not a recoverable mistake.

**Invariants.** Untouched. Rendered text is third-party content entering a critic's context under
RA-010 and reaches only the evidence lens; provider names are a closed vocabulary safe for the audit
trail while their request URLs, which carry the key, are never logged (RA-016); the dispute channel
still returns only `True` or `None`; and no tier can raise a defect the pipeline would not otherwise
raise, only fail to suppress one.
