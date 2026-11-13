## D-failed-attempt-search-carry — a failed writer attempt hands its search results to the next attempt of the same draft

*(Supersedes in part D-writer-source-reads: the `read_source` allowlist stays scoped to one
generation's writer walk, not to one `complete()` call. Bodies, queries and reads stay per call.)*

**The finding.** In a production run, rounds 2 to 4 went: one writer drafted without reading
(`unread_draft`, held); a second failed with `unparsed_tool_markup`; a third failed with
`tool_loop_no_answer`; the held draft shipped. The two failing attempts ran 37 of the run's 60
queries. Their results were thrown away with their `ReadSession`, and the drafts that shipped in
those rounds ran no searches, so the run paid for evidence no draft ever saw. That observation is
the motivation. The warrant is the mechanism, which is checkable in this repository: `_generate`
builds a fresh `ReadSession` per attempt, the query budget is charged per query whether or not the
attempt ships, and nothing passes one attempt's results to the next.

**Why per-call was chosen, and why it does not fit this case.** D-writer-source-reads made the
allowlist per call for two reasons, both stated in `reading.ReadSession`'s docstring:

1. A run-wide list would let a later writer open a page an earlier writer merely found, "a
   cross-context affordance bought for nothing, since a writer has to name a URL and can only
   have learned one by searching".
2. A session shared across attempts would have `support.check` rule on bodies the shipping writer
   never saw.

Reason 2 is about bodies, and it still holds: nothing here shares a read. Reason 1 is right for
rounds, and wrong for a failed attempt. Inside one generation, the affordance is not bought for
nothing. It buys back queries the run has already paid for, and the alternative is a writer that
either spends the budget again or drafts with no evidence, which is what shipped. Fresh context
(principle #6) is about the volume and the provenance of text in a context. Both stay bounded
below.

**Decision.** Effective while `search.carry_max_results > 0` (default 20).

1. **What carries.** After each attempt that does not ship (a call failure, an empty reply,
   unapplicable operations, or a set-aside draft), the result records its own `web_search` calls
   returned (title, URL, snippet, date, the same capped fields `search._parse_results` produces)
   are added to a per-generation list, one per URL, earliest first, capped at
   `search.carry_max_results`. The list is discarded when the generation ends. Nothing carries
   across rounds.
2. **How it reaches the next attempt.** The next attempt's user prompt gains
   `prompts.carried_results_block`. It uses the same `UNTRUSTED_NOTE`, data fence and
   neutralisation as a live result block, plus fixed wording: the results were retrieved by an
   earlier attempt at this draft, the searches are spent, the writer may read and cite them, and
   should search only for what they do not cover. The block shows **no query**: a query is the
   earlier model's words. It does not say which model failed or why.
3. **Allowlist.** The carried URLs form a third set in the new `ReadSession`, beside `_offered` (this
   call's own results) and `_cited` (the draft's sources). `admitted()` accepts all three.
   `offered_count` still means "what this call's search returned".
4. **No body carries.** A carried URL is read afresh by the session that reads it. The run-lifetime
   fetch cache means no second download. The read is charged to `read_char_budget` again, because
   that budget bounds what enters a writer's context, and this text is entering a new one.
   `support.check` still rules only on bodies the shipping writer read.
5. **The budget still pays, once.** Carried results are not refunded to the query budget, and
   carrying them costs no query. A writer that fails every time still spends at most what it spent
   before, and the run's `query_budget` is still the bound on that.
6. **Measurement.** `generate` carries `carried_results`, the number of result URLs the shipping
   call was handed (an integer, RA-016).

**Why not refund the failed attempt's queries instead.** A refund keeps the budget and loses the
evidence: the next writer has to find the same pages again, or does not. A refund also makes a
failing model's searches free, so a provider that fails every call could search without limit
across a run. Carrying keeps what was found and spends nothing more. With the default unbounded
`query_budget` (D-unbounded-evidence) a refund would change nothing at all.

**Isolation (docs/isolation.md).** What enters writer B's context from attempt A is a bounded list
of third-party result records, the same class of untrusted text as B's own search results, behind
the same fence. A's queries, reasoning, draft, reads and failure do not enter it. Which results A
received was shaped by A's queries, the way an evidence card's passages are shaped by a reader
model's selection (D-writer-evidence-card), and like that selection it carries no model's words.
Author exclusion, critic contexts, the `OrchestratorView` and the controller are untouched: no
critic sees a carried result unless the draft cites it, and the controller sees nothing.

**Residuals.** (1) A carried snippet that addresses its reader reaches one more context than
before. The fence and the note are the same as for a live result, and the cap bounds how much.
(2) Re-reading a carried page charges the character budget twice across the generation.
(3) When the walk ends by shipping the held draft, carried results are unused, as before.

**Rollback.** `search.carry_max_results: 0` restores per-attempt isolation byte for byte: no block,
no third set, and `carried_results: 0`.
