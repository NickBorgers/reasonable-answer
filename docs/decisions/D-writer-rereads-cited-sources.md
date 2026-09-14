## D-writer-rereads-cited-sources — a reviser may reopen the pages the draft it revises cites, and nothing a previous writer merely found

**The finding.** D-writer-source-reads bounded `read_source` to URLs a `web_search` result returned
*in the same writer call*. For a first draft that is the whole story. For a revision it is not: the
reviser is handed a draft whose `## Sources` names the pages its claims rest on, and fix tasks that
concern exactly those claims, and it can open none of them unless it happens to search each one up
again. In production run `run-116cc0ea4cac` reading was on (its startup event shows
`read_sources: true`, and one writer read ten bodies), yet a reviser that never searched —
mistral-large-3 in that run — worked every fix task from the draft's prose alone. The prompt rule
D-writer-citation-continuity adds ("a source already in the list may be cited for another claim it
supports; read it first") is unsatisfiable without this.

D-writer-source-reads rejected a run-wide allowlist on the ground that "a writer has to name a URL and
can only have learned one by searching". A reviser has learned the draft's URLs another way: it holds
the draft. That premise is what this decision revisits, and only for those URLs.

**Decision.** On a revision, `reading.ReadSession` is seeded with the draft's cited URLs.

*The seed.* `graph._cited_seed(rt, previous)` is `graph._verified_urls(config, previous)` — the one
helper source verification also calls, `fetch.extract_source_urls(report, limit=search.max_source_urls)`
— computed once from `state["report"]`. It is empty unless all of these hold:

- a previous draft exists (a first draft is seeded with nothing);
- `Runtime.read_sources` — the writer holds `read_source` at all;
- `Runtime.verify_sources` — the run already fetches exactly this set for the evidence lens;
- `search.read_cited_sources` (default `true`) — a rollback switch.

The knob has no load-time rule, because a default of `true` would otherwise refuse every existing
config that reads without verifying; the runtime conjunction does the gating instead. It sits in
`SearchConfig`, not `Budgets`, because `graph._run_fingerprint` hashes `Budgets` and a paused run
must survive the deploy.

*The session.* Still constructed inside `_generate`'s retry loop, as `ReadSession(cited=seed)`. The
seed is a separate frozen set from the search-offered set, so `sources_offered` keeps meaning "what
search returned". `ReadSession.admitted(url)` checks both sets, exactly and then after
`fetch._clean_url`, and returns the allowlisted string — so what reaches the fetcher is always a member
of a set, never the writer's punctuated variant of it. `SourceReader.read` checks it before any fetch
and before any budget is spent, as before.

*How the writer is told.* Never with a URL in trusted text. The URLs come from the draft, which is
model output and arrives fenced as untrusted data in the user prompt; copying them into the system
prompt or the tool definition would promote them into instructions. `writer_system(search, read,
reread)` appends `WRITER_REREAD_ADDENDUM` — read a listed source when a fix task concerns a claim that
cites it, or before attaching it to a new claim; copy the URL exactly from the DRAFT REPORT — and
`reading.read_source_tool(cited)` returns a description that mentions the draft's `## Sources`. With no
seed, both return exactly what they returned before (`READ_SOURCE_TOOL` is the same object), and the
user prompts are untouched, so the D-scoped-revision control arm is too. The `not_retrieved` refusal
and its label now say "not returned by a search in this conversation or listed in the draft's
## Sources".

*Budget.* Re-reads draw on the same `read_char_budget` and `read_max_chars` as every other read, and
the addendum aims them at claims fix tasks name. `_read_fields` gains `cited_readable` (the seed's size)
and `cited_reads` (read attempts on a seeded URL), so a budget spent on re-reads is visible next to
`budget_exhausted` outcomes.

**Hazards from D-writer-source-reads, walked one by one.**

* *A read tool that accepts any URL is an SSRF-shaped affordance a model steers.* Still closed. The
  seed is the URL set verification fetches for the same draft, computed by the same function with the
  same ceiling, and there is no seed without `verify_sources` — so seeding never puts an address in
  front of the fetch boundary that the run would not already send there. The writer still cannot name
  an arbitrary URL: a refused URL never reaches the fetcher and costs no budget
  (`test_a_url_neither_searched_nor_cited_is_refused_before_any_fetch_or_spend`), and a
  punctuation-variant match fetches the allowlisted string, not the variant.
* *Page text is the largest untrusted body a writer ever holds.* Unchanged in kind and in bound. A
  re-read body arrives in the same `prompts.source_read_block`, with the untrusted note repeated inside
  it, under the same per-read and per-run caps. The output channel is still free-text markdown. What
  widens is which pages can enter, and each of them is one the previous writer chose to cite and the
  evidence lens was shown — not one an attacker can newly steer the reviser to.
* *A run-wide allowlist would leak one writer's retrieval into another's context; the subtle case is
  the retry loop.* Still closed. The seed is a function of the draft, which every attempt already holds
  in its user prompt, so seeding it carries nothing from attempt one to attempt two. Attempt one's
  *search results* still never carry over, because the session holding them is still built per attempt
  (`test_a_retried_reviser_may_read_what_the_draft_cites_but_not_what_its_predecessor_searched`, beside
  the unchanged `test_a_retried_writer_starts_with_an_empty_allowlist`). Across rounds, uncited search
  results and read logs still do not carry: only a URL that survived into the draft does, and the
  reviser was already holding it.
* *Building a fetcher for reading could switch on the evidence-lens page channel by accident.*
  Unaffected. The seed *reads* `Runtime.verify_sources`; it sets nothing, and `Runtime.fetcher` is still
  assigned only for verification.
* *Sharing one fetcher shares one character cap.* Unaffected: a re-read goes through `SourceReader`,
  which clips to `read_max_chars`, and verification still holds its `CappedFetcher` view.
* *Sharing one fetcher shares resolver call budgets.* Not widened in practice. The seeded URLs are the
  ones verification resolved for the draft under revision, and the fetch cache is run-lifetime and
  monotone, so a re-read is normally a cache hit; `_extraction_call_ceiling` is unchanged.
* *A writer-authored manifest feeding acceptance would be a writer grading itself.* Unaffected. The
  manifest is still checked only against `session.reads` — bodies this writer read in this call, now
  possibly including re-reads (`test_the_manifest_is_checked_against_a_re_read_body`) — and is still
  audit-side.
* *An abstract, or an open-access copy, read as full-text support.* Unaffected: the same outcomes and
  verdicts apply to a re-read.
* *Manifest spans and URLs in `events.jsonl` would outlive a content purge.* The new fields are
  integers; the URL a writer re-read is never logged (RA-016).

**Isolation, stated plainly.** This widens the writer's context: a reviser may now see the text of
pages a *different* writer chose to cite. It does not widen author exclusion — the reviser is still
never the draft's author — and it does not carry the previous writer's reasoning, searches or reads,
only third-party pages the draft names. docs/isolation.md records it on the writer's SEES line and in
the allowlist bullet.

**Deployment profile drift, corrected in passing.** docs/deployment-profile.md listed
`search.enabled` and `search.verify_sources` for production and omitted `read_sources: true` and
`support_manifest: true`, both of which production runs (run-116cc0ea4cac's startup event). Because this
decision's gate is exactly `read_sources ∧ verify_sources`, the profile now says so.

**Deliberately not done.**

- Handing critic-fetched bodies to writers: the reviser reads through its own budget, so what it sees
  is what it asked for and is accounted as a writer read.
- Listing re-readable URLs in trusted prompt text, for the injection reason above.
- A run-wide allowlist, or carrying search results or read logs between rounds or attempts.
- `defect_citation_scope` still uses `extract_source_urls`' default limit of 20 rather than
  `max_source_urls`; noted, out of scope here.
