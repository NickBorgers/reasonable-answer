## D-existence-vs-body — a citation's *existence* is verifiable for free; its body usually is not, and the two must never be confused

**The problem.** D-source-verification fetches the URLs a report cites so the evidence lens can check them, and D-notfound-fabrication
mints `fabricated_citation` mechanically when one of those fetches returns a definitive not-found.
Both are sound, and both share a blind spot: **a direct fetch can fail for reasons that have
nothing to do with whether the source is real.** A paywalled journal or a newspaper that refuses an
automated client hands back a `blocked` — indistinguishable from a source nobody could check for
any other reason, and one HTTP status away from looking like a source nobody ever published.
Verification was therefore strongest exactly where a citation is easiest to fake (a fabricated blog
URL 404s) and weakest exactly where a source is refused rather than absent.

**The refusal, first.** There is no omni-passport service that hands over paywalled bodies, and the
ways of faking one are all off the table. This system does not spoof a browser user agent to defeat
a bot wall, does not solve CAPTCHAs, does not launder paywalled text through archive.org, and does
not replay a cookie jar or an institutional credential to impersonate a subscriber. `fetch.py` has
carried the reason since D-source-verification — *"pretending to be a browser to get around that would be the wrong
kind of clever"* — and that comment is doctrine, not decoration: a system whose whole claim is that
its citations are checkable cannot obtain them by circumventing the access controls of the people
who published them. Where a body is not lawfully readable, the honest answer is to say so.

**The asymmetry that IS the design.** Citation verification asks two questions, and they are not
equally expensive:

1. *Does this source exist, and is it what the report says it is?* — answerable for free from
   bibliographic registries, for any citation carrying a DOI, an arXiv id, a PMID or a PMCID.
2. *Does the source say what the report claims?* — needs the body, and often no lawful copy exists.

(1) is the cheapest and by far the largest win, because answering it is what stops a paywalled
source looking like a fabricated one. It must **not** be allowed to leak into (2). An abstract is a
summary the authors wrote; a claim's absence from an abstract is not evidence that the paper does
not make the claim. So `misrepresented_source` still sharpens only when some source's *body*
actually arrived, `prompts.fetched_sources_block` gains a third entry shape that announces
existence before anything else and labels an abstract as explicitly not the full text, and the
rules list forbids raising `misrepresented_source` against a source shown only as metadata.
`fabricated_citation` is likewise not sharpened toward the critic — D-notfound-fabrication mints it mechanically, and
a second copy would double-report one defect at its blocking floor.

**Decision.** A new `resolve/` package (a peer of `web/`) runs a two-rung ladder, and only when a
direct fetch yielded no body.

*Tier 0 — identifiers.* Extract a DOI / arXiv id / PMID / PMCID from the cited URL by regex alone
and ask the configured registries. With the default roster — Crossref and OpenAlex — that answers
*existence* for DOIs and PMIDs; arXiv ids and PMCIDs are covered when arXiv and Europe PMC are
added to `sources.identifiers.providers` (both ship in the open-access roster, so a free copy is
still sought for them there). A confirmed record yields the title, authors, year, venue and
abstract, and — the point — *existence*. It runs even when tier 1 succeeds, because the attributed
title is worth checking against the report whether or not a body arrived.

*Tier 1 — open access.* OpenAlex's `best_oa_location`, Unpaywall, Europe PMC, arXiv. When one names
a free copy, the direct/PDF path is re-entered **exactly once**, via an explicit depth argument
rather than a convention, and never recursively.

Every provider is a keyless GET through `fetch.http_get`, which stays the single egress point for
the whole codebase; `tests/test_resolve.py` asserts mechanically that no module under `resolve/`
imports `urllib.request`. CORE is deliberately excluded: it needs an API key, and a
credential-bearing request is a different security posture (header handling, no-redirect opener,
fail-closed startup validation) that this change does not take on.

`fetch` does **not** import `resolve` — the tiers need `search.QueryBudget`, which sits downstream
of `fetch`, so the dependency would close a cycle. The resolver is built in `graph._build_resolver`
and injected into `SourceFetcher`, exactly as `_build_searcher` builds the searcher: network
clients are assembled at startup, so the graph performs no I/O and the suite stays offline (D-seed-conversion).

**The new outcomes, and how conservative each is.** `METADATA_ONLY` means existence confirmed and
no body. `PAYWALLED` requires *both* a registry corroborating existence *and* a direct fetch that
was refused — it is never guessed from a status code, because HTTP 402 is vanishingly rare and a
real paywall usually answers 200 with a teaser. `BUDGET_EXHAUSTED` says a tier that could have
answered was out of per-run calls, so an operator does not read a column of `blocked` and blame the
sites; it deliberately never overwrites `NOT_FOUND`, since a run that exhausted its budget at
source five would otherwise silently stop reporting D-notfound-fabrication's finding for sources six through twelve —
turning a tier on must never weaken a defect the pipeline raises without it.

The one path that can *raise* a defect is gated hardest. An identifier no registry has heard of is
`NOT_FOUND`, which D-notfound-fabrication mints as a blocking `fabricated_citation`, so it requires all of: a
confidently-extracted identifier (a mangled one is an identifier no registry holds, which is why
`identifiers.py` prefers to return nothing); a denial from **every consulted provider that is
authoritative for that identifier kind** (Europe PMC answers DOI queries but is authoritative only
for PubMed ids — its silence about a physics DOI is a coverage boundary, not evidence about the
world); and a direct fetch that established nothing either (a not-found, or a host that did not
resolve). A 403, or a 200 that merely would not parse, keeps its own verdict — a live server
refusing a client says something about the client, and a served page proves the URL resolves.
Symmetrically, a registry that *confirms* the identifier outranks a 404 on the cited URL: the
citation names a real document, and a dead link is a dead link, not a fabrication.

**The dispute invariant under the new outcomes.** `dispute.adjudicate_mechanical` returns `True` or
`None` and never `False`. Two new hazards, both closed:

* an abstract must never uphold a dispute — free, via `.ok`, because `FetchedSource`'s first
  invariant forbids a non-`FULL_TEXT` outcome from carrying text at all, so `METADATA_ONLY` is not
  `ok` and there is nothing for a quote to match;
* an open-access mirror's body must never uphold a dispute about the cited URL. A preprint often
  differs materially from the version of record, and a quote present in arXiv v1 and absent from
  the published paper is a real failure mode — so a result whose `body_source_url` is set is
  inconclusive by construction, and the finding stands. The arbiter and the evidence critic are
  both told, in the prompt, when they are reading a mirror rather than the cited page.

**Off by default, budgeted per tier.** `sources.identifiers` and `sources.open_access` each carry
their own `enabled`, provider list, timeout and `max_calls_per_run` (reusing `search.QueryBudget`,
already generic and thread-safe), under the existing `sources.enabled` master switch. Two switches
per tier is the pattern `sources.pdf` established: enabling one tier must never turn on another.
Both are off in both shipped rosters, asserted in `tests/test_shipped_rosters.py`;
`config/roster.default.yaml` mentions them not at all, because its job is booting with no network
and no credential.

A contact email for Crossref and Unpaywall's polite pool is configurable via
`sources.contact_email_env` (default `RA_CONTACT_EMAIL`, resolved from the environment like
`ProxyConfig.api_key`). Its absence is a **warning**, not fatal, and the warning names what is lost
— demotion to the anonymous rate-limit pool, which is degraded service rather than a broken config.
Contrast a missing search credential, which is fatal because without it the feature cannot function
at all. Unpaywall is the single exception, refusing anonymous requests outright; it is dropped with
its own warning rather than failing the tier. `compose.yaml` passes the variable in explicitly,
because one the operator exports outside the container never reaches the process inside it, and
that failure would otherwise be silent.

**Cache monotonicity.** Three caches, three key spaces: the final `FetchedSource` by cited URL
(`SourceFetcher`'s own, and self-describing enough that a `METADATA_ONLY` entry — empty text,
non-`None` error — is already treated as a failure by every consumer written before this change);
`SourceMetadata` by normalised identifier, so two URLs naming one DOI share a single Crossref call;
and identifier → best open-access URL, where a stored `None` means *asked, and none exists* — the
distinction that stops a twelve-source report making twelve identical Unpaywall calls. All three
are monotone within a run: written once, never invalidated, never re-resolved. Every round
therefore judges the same evidence, and a provider that was flaky at round two does not become
authoritative at round six. The two caches inside the resolver share one lock, because critics run
at concurrency 3 and a second lock would only add an ordering to get wrong.

**Audit trail.** RA-016 sharpens rather than relaxes: provider *names* are a closed vocabulary and
are safe to log, provider request *URLs* are not — the polite-pool querystring carries the
operator's email, and the querystring is where the next vendor will put a token. The identifier is
no better, being derived from a URL private to the run. `resolve/base.py` owns that rule in its
docstring, and a test asserts that no request URL, contact email or identifier reaches a log
record. The `fetch_sources` event gains a tally of resolution tier across **all** sources, not just
the failures: `{"direct": 5, "open_access": 2, "identifier": 4}` is what tells an operator whether
a tier is earning its calls, and a source the open-access tier rescued is a success that leaves no
trace in the failure tally. `_failure_reasons` keeps tallying the closed outcome vocabulary and is
not regressed to free text.

**Egress posture unchanged.** `docs/ssrf-egress-isolation.md` describes a Squid gateway that denies
every private / loopback / link-local / tailnet destination and then `http_access allow all`, with
an explicit note that there is deliberately no domain allowlist because arbitrary public fetching is
the feature. These five fixed first-party hosts therefore need no new rule, and they are
categorically *narrower* than what D-source-verification already permits: model-chosen URLs. The bounds that do apply
are the ones already in `fetch.http_get` — timeout, byte cap, http(s)-only opener — plus a redirect
cap of zero for provider calls specifically, on `search.py`'s reasoning that an endpoint which is a
constant has no business being redirectable when its querystring carries something personal.

**What this does not claim.** A confirmed DOI shows that a source exists and that the report's
attributed title, authors, year and venue match a real record. It does not show the source is
correct, and it does not show the report characterises it fairly. That remains the residual blind
spot RA-011 names, now materially smaller: the class of citation that cannot be checked at all has
shrunk from "everything paywalled" to "everything paywalled and carrying no identifier".
