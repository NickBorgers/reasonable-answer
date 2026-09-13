## D-claim-check-inconclusive-verdicts — an inconclusive checker verdict retires nothing, a cut page is never whole, and a dead proxy costs a few failures rather than every pair

**The finding.** A whole-diff review of the eight PRs merged on 2026-09-12 and 2026-09-13 found
three things in D-claim-level-verification's implementation standing between
`claim_check.enabled: true` and a production deployment. None was visible in the PR that shipped
the feature; each is an interaction with something else.

1. **A finding could be suppressed by nothing.** `claimcheck.reconcile` dropped the critic's own
   `misrepresented_source` finding on any sentence the checker had returned *any* verdict for —
   `PairVerdict.checked` includes `unreadable` and `absent` on a page shown in part — and neither
   of those verdicts mints a replacement. D-claim-level-verification's own item 4 said the critic's
   finding is kept for an unreadable page; the code disagreed. Combined with the checker's
   instruction to answer `absent` when unsure, every page longer than `claim_check.page_max_chars`
   became a path by which a critic's direction or scope finding
   (D-source-fidelity-direction-and-scope, merged the same day) vanished with no verdict standing
   in for it. That is the shape D-writer-disputes forbids for adjudication: nothing suppressed
   without an explicit record that read the evidence.
2. **A cut page could be called whole.** `fetch.SourceFetcher` discarded `RawResponse.truncated`
   on the HTML path, and the PDF path had no way to learn that `sources.pdf.max_pages` had dropped
   pages. A page cut at `search.fetch_max_bytes` whose surviving text fit `page_max_chars` was shown
   to the checker under the "complete" header, so `absent` from that prefix minted a major finding
   against a page that may state the claim on the next line. Article pages that carry hundreds of
   kilobytes of markup before the body are ordinary, not pathological.
3. **A dead proxy was met with every pair.** `claimcheck.check` ran each pair serially and
   unconditionally, so a proxy that had stopped answering cost `max_pairs` calls, each spending the
   client's full timeout and retry budget, inside one critic's slot — for a pass that would mint
   nothing. D-failing-critic-sidelined records what that incident looks like at critique
   granularity; the checker multiplied it by the citation count.

**The decision.** Three rules, each in the direction D-claim-level-verification already claims —
toward the writer, never against it; toward the finding, never away from it.

1. **Only a settled verdict is authoritative.** `PairVerdict.settled` is `supported`,
   `contradicted`, or `absent` from a page shown whole: the verdicts that are readings of the
   page. `reconcile` drops a critic's `misrepresented_source` finding only for a settled pair.
   `unreadable` and `absent`-in-part mint nothing and retire nothing; the critic's judgement on
   those sentences stands exactly as it did before the checker existed. `checked` keeps its meaning
   for the counts, so the `claim_check` event is unchanged in shape apart from the new `aborted`.
2. **A cut body is never whole.** `FetchedSource.truncated` — additive, default `False`, so every
   constructor that predates it means what it meant — is set when the wire read hit `max_bytes`,
   when a PDF lost pages to `max_pages` (`textconv.pdf_to_markdown_bounded` reports it), when the
   extraction tier's markdown outran the cache cap, and when `CappedFetcher` clipped the text.
   `excerpt.select` carries it into `Excerpted.truncated`; `Excerpted.complete` is false for a cut
   body however short, so `absent` on it is `absent_partial` and mints nothing; and `excerpt.render`
   tells the reader — checker and critic alike — that the page continues past the last character
   retained, closing with `[…]`. The rule the critic already had, "absence from what you were shown
   is not absence from the page", now covers a cut the critic could not otherwise see.
3. **Consecutive failures break the circuit.** `claim_check.max_consecutive_failures` (default 3;
   under `claim_check`, not `budgets`, so `_run_fingerprint` is unchanged) bounds how many checker
   calls in a row may end unchecked — a transport failure, or output outside the schema past the
   repair budget — before the remaining pairs of the pass are recorded `aborted` without a call. A
   verdict resets the streak; a memo hit is not a call and touches it neither way. `aborted` is
   counted on the `claim_check` event, so a pass that gave up is distinguishable in the trail from
   one that ran.

**Invariants.** None move. *Fail-closed lens validation:* a checker failure still fails only the
pair, in the direction that adds no finding, and now fails fewer of them sooner. *Severity floors:*
minting is unchanged in kind and stays at the category floor. *Blind orchestrator:* counts only,
as before. *Untrusted text:* the truncation sentence is pipeline-authored prose inside the same
fence the page text already sits in. *Termination:* fewer calls, no new rule, no new budget
field. *Dispute adjudication:* untouched — a finding the checker did not settle is the critic's and
is disputable exactly as before; a minted finding still carries the page span to argue against.
*Docs-as-spec:* D-claim-level-verification items 4 and 5 are amended in place to point here, and
[convergence.md](../convergence.md) states the settle rule and the cut-page row beside the verdict
table.

**Deliberately not done.** No parallelism inside a critic's slot; D-claim-level-verification's
reason stands. No retry of an aborted pass within the same critique; the next round re-derives,
and the memo means only the pairs that were never answered are re-asked. No chunk-and-aggregate
for pages past `page_max_chars`. The production profile's `claim_check.enabled: true` describes the
posture this decision makes safe, and takes effect when the deployed configuration is changed to
match it.
