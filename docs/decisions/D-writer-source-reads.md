## D-writer-source-reads — the writer reads the page before it cites it, and says where the support is

**The problem.** D-retrieval-opt-in made a citation *real*: a writer holding `web_search` may cite only a
URL a result returned, so it can no longer invent one. It did not make the citation
*supported*. A Brave result is a title, a URL and a one-line snippet, and the writer chooses
what to claim from exactly that. Bodies were fetched only afterwards, and only for the evidence
lens (D-source-verification) — which is to say the system could read a page to *check* a claim
and could not read one to *make* it.

The originating issue identified the resulting traceability gap: a snippet does not expose a
page, chapter or section locator, does not establish that a narrow claim occurs in the cited
source, and leaves snippet-sourced and read-and-quoted citations looking identical in the report.
The private run that motivated the issue is design context, not public evidence for a general
claim.

**Decision.** Writers get a second, bounded tool — `read_source` — and, where configured, are
asked afterwards to record where each cited claim's support actually sits.

*The tool.* `reading.SourceReader` resolves one URL per call through the **existing**
`fetch.SourceFetcher`, hence through `fetch.http_get`'s bounded http(s)-only opener.
`reading.py` opens no socket; there is still one egress point for the whole package. The
fetcher instance is shared with source verification when both are on, so a page a writer read
is not downloaded again for the evidence lens, and both see the same bytes.

*The allowlist is the writer's own search results.* This is the part that is not negotiable.
`read_source` accepts a URL only if a `web_search` result **in the same writer call** listed it;
`reading.ReadSession` is that allowlist and is created per `LLMClient.complete` call. Per-call
rather than per-run deliberately: a run-wide list would let round five's writer open a page
round one's writer found, which trades a fresh-context property (principle #6) for nothing,
since a writer has to name a URL and can only have learned one by searching. A refused URL never
reaches the fetch boundary and costs no budget — otherwise a writer could exhaust the run's
reads on addresses it was never offered and arrive at the real ones with nothing to spend.

*Bounded three ways.* `search.read_budget` caps reads per run; `search.read_char_budget` caps
the total page text handed to writers, because a per-page cap cannot see the total and retrieval
degrades when relevant information is buried in long context
([Liu et al. 2023](https://arxiv.org/abs/2307.03172); principle #6);
`search.read_max_chars` caps one page. Bytes off the wire were already capped by
`search.fetch_max_bytes` inside the fetcher. The character budget truncates rather than refusing
— a page that runs into the last of the budget is still worth its first few thousand characters
— and a non-body outcome costs no characters at all, because a refusal is the answer the writer
most needs and must never be the thing the budget silences.

*Every outcome survives, distinguishably.* `SourceOutcome` already separated body-read,
registry-metadata-only, paywalled, blocked, not-found, unreadable and budget-exhausted, and
`prompts.source_read_block` renders each with its own framing and its own closing instruction. A
writer told "could not read" about a page that does not exist will keep the claim, which is the
same argument D-source-verification made for the critic-facing block. One outcome is added:
`NOT_RETRIEVED`, for a URL the writer was never offered. It is a statement about the *request*,
not about the source — nothing was contacted — and is worded so it cannot be read as one.

*Page text is untrusted data (RA-010), and this is the largest such body a writer ever holds.*
It carries the fence and the explicit note, repeated inside the block rather than relied on from
the top of the system prompt, and the writer is told again that anything in a page addressing it
is data to report on. Note what does **not** widen: the writer's output channel is free-text
markdown either way, so reading adds evidence, not a new way to emit anything — the same
argument D-source-verification makes for the evidence critic. See docs/isolation.md.

**The traceability half.** Reading the page is what makes a claim-level contract possible, so
`search.support_manifest` adds one: after the draft, a separate structured call asks the writer
for `citation_id -> url -> locator -> support_span -> claim`, one entry per claim that rests on
a page it read. `support.check` then rules on each entry **mechanically**, by string containment
against text this run holds — `triage._normalize`, so reformatting does not decide a verdict and
invention still does. A claim or support span that normalizes to no text fails before containment;
otherwise markup-only strings would exploit the fact that the empty string is a substring of every
report and page.

Six verdicts, and three of them exist to stop "unchecked" being read as "unsupported":

| verdict | what was established |
|---|---|
| `supported` | the claim is in the report and the span is in the cited page's own body |
| `span_not_found` | a body was read and does not contain the span |
| `claim_not_in_report` | the entry points at text the report does not contain |
| `different_document` | the body came from an open-access copy, not the cited URL |
| `body_not_read` | metadata, an abstract, a paywall, a block — no body to check against |
| `not_retrieved` | the writer cited a source it never opened |

`different_document` is the same rule `dispute.adjudicate_mechanical` already applies: a preprint
is not the version of record, so a span found in it does not show that the cited document
contains it. `body_not_read` covers the abstract case D-existence-vs-body settled — an abstract is a summary
the authors wrote, and presence in one is not full-text support. `not_retrieved` is common and
legitimate: a snippet-level citation is still a citation, and recording it as such is precisely
the provenance-depth signal that was missing. `locator_coverage` is counted separately because
the bibliography-level gap is invisible in a support tally — an entry naming a whole book can be
perfectly `supported` on a span from page one.

**The manifest is audit-side, and that is load-bearing.** Nothing it produces enters another
model's context, becomes a `Defect`, appears on `OrchestratorView`, or reaches the controller. It
answers "can a human reading this run trace the claim to the page?", which is a property of the
record rather than a term in the stop decision (docs/convergence.md). It also cannot be
otherwise: the **writer** authors the manifest, so wiring it into acceptance would hand a writer
a lever on its own review. The full manifest goes to `support/`, a `CONTENT_DIRS` directory — its
every field is quoted report and page text — and only closed-vocabulary verdict counts go to
`events.jsonl`, which survives a content purge (RA-016). The pass is never fatal: any failure
degrades to no manifest, and only the exception *type* is recorded, never a message built from
the rejected input.

**Fail-closed configuration.** `SearchConfig` refuses two combinations at load, before a token is
spent. `read_sources` without `enabled` offers a tool whose allowlist can never be non-empty:
every call is refused, the writer spends its tool rounds discovering that, and the run costs more
to produce what it produced before. `support_manifest` without `read_sources` collects spans no
body can check — a manifest that looks like verification and is not, which is the exact failure
this decision exists to remove. Both switches are off by default, in code and in the shipped
roster: reading fetches model-chosen URLs, so it inherits D-source-verification's posture that
the egress boundary is a deployment concern (docs/ssrf-egress-isolation.md), and it is enabled
only where one exists.

**What did not change.** `search.verify_sources` still decides, alone, whether the evidence lens
receives fetched pages, how much of a page it receives, and whether disputes can be adjudicated
mechanically. Resolver call availability is the exception recorded by D-writer-resolver-budget:
writer reads and verification share each enabled tier's whole-run call pool. `Runtime.fetcher` is
still set only for verification, and it is a
`fetch.CappedFetcher` view clipped to `fetch_max_chars` rather than the shared cache itself —
"alone" has to cover the *volume* as well as the channel, because mechanical adjudication turns on
string containment and a longer body upholds more disputes. The reader holds its own reference to
the same cache, so a page is still downloaded once. The controller, the 14 rules, the taxonomy, the
severity floors, the `OrchestratorView` and the terminal statuses are untouched.

**What this still does not establish.** A read page shows what a page says, not that the page is
right; `supported` means the chain is traceable, not that the claim is true. The output label is
unchanged for that reason — "consensus-reviewed with retrieved sourcing" is still the honest
ceiling. And a locator is the writer's assertion: `support.check` can prove a span is on the page
it names, and cannot prove the span is on page 4.

The manifest is also **per draft**, not per run: a revision round that reads nothing produces no
manifest, because its writer has no body to quote from. The record is still complete for the
claims it covers — spans are verbatim, so a claim that survives unchanged from the round that
traced it is traceable through that round's `support/rNN.json` — but "the final draft has no
manifest" is a legitimate outcome and does not mean its claims went unsupported. Carrying reads
forward across rounds would fix the shape at the cost of the per-call allowlist, and was not worth
that trade.

**Deliberately not done.** No arbitrary-URL reader, no reading for critics (the evidence lens has
its own fetch path and its own reasons), no manifest-derived defect category, and no change to
what the controller counts. Reading is not offered on the dispute-elicitation call, which is a
separate toolless pass by design.

**Hazards considered, and where each is closed.** Stated as prose rather than as an `RG-<n>`
table: those prefixes are enumerated as ranges by `.github/scripts/review/prompts/invariant.md`,
and extending one from a feature PR would drift the reviewer's own citation range — the drift
D-decision-slugs removed for decisions and `tests/test_reviewer_prompt_ranges.py` still guards for
findings.

* *A read tool that accepts any URL is an SSRF-shaped affordance a model steers.* The allowlist is
  the writer's own `web_search` results, checked before any egress and before any budget is spent;
  `tests/test_reading.py` asserts a refused URL never reaches the fetcher.
* *Page text is the largest untrusted body a writer ever holds.* Fenced, with the untrusted note
  repeated inside the block, capped per read and per run — and the writer's output channel is
  unchanged, so reading adds evidence rather than a way to emit anything.
* *A run-wide allowlist would leak one writer's retrieval into another's context.* `ReadSession` is
  per `complete()` call; the next writer starts with an empty allowlist. The subtle case is the
  **retry loop**, not the next round: `_generate` rotates through the pool on a failed or empty
  completion, so attempt two is a different model, and a session hoisted out of that loop would let
  it open a page attempt one's search found and would let its manifest be checked against bodies it
  never saw. The session is therefore constructed inside the loop, and
  `test_a_retried_writer_starts_with_an_empty_allowlist` drives exactly that path.
* *Building a fetcher for reading could switch on the evidence-lens page channel by accident.*
  `Runtime.fetcher` is set only when `verify_sources`; the reader holds the shared instance
  separately, and the existing verification tests are what would catch a regression.
* *Sharing one fetcher shares one character cap, and the caps are not the same.* The cache must
  store the larger of `fetch_max_chars` and `read_max_chars` or the reader would be silently
  clipped to the critic's cap — so verification is handed a `fetch.CappedFetcher` view that clips
  back to `fetch_max_chars`, and the cap travels with the handle rather than with the cache. Left
  unclipped this would not merely show a critic more text: `dispute.adjudicate_mechanical` upholds
  on string containment, an upheld dispute suppresses a finding, and `search.read_sources` would
  thereby have acquired a path into the stop decision. The resolver ladder is built with the cache
  cap for the same reason, so a body reached through an open-access mirror is bounded like one
  fetched directly.
* *Sharing one fetcher also shares resolver call budgets.* Writer reads are allowed to benefit from
  the same identifier, open-access and extraction tiers as verification, so candidate reads can
  spend calls before the evidence lens verifies the final citations. D-writer-resolver-budget
  records that whole-run coupling and gives the derived extraction ceiling room for both consumers;
  an operator-pinned cap remains an intentional tighter limit.
* *A writer-authored manifest feeding acceptance would be a writer grading itself.* Closed by
  construction — no `Defect`, no `OrchestratorView` field, no controller input — and asserted:
  `tests/test_reading.py` checks the manifest enters no other model's context.
* *An abstract, or an open-access copy, read as full-text support.* `body_not_read` and
  `different_document` are separate verdicts, and the writer-facing block says which it is holding
  before it says anything else.
* *Manifest spans and URLs in `events.jsonl` would outlive a content purge.* The manifest goes to
  `support/`, a `CONTENT_DIRS` entry; the event carries counts and closed-vocabulary verdicts only
  (RA-016).
