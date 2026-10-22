## D-no-shrink-discharge — removing a cited source is not a way to discharge a finding

**The finding.** D-read-before-citing, D-unreadable-citation-finding and D-anchored-unreadable make a
claim that rests on an unread or unreadable page a major finding. Their instructions offer four
ways out: read the page, re-source the claim, weaken it, or remove it. A writer under pressure takes
the cheapest edit that stops the finding matching, which is to delete the citation (and the
bibliography entry) while leaving the claim standing, often re-pointed at a long page already
cited. The finding is gone and the claim is now less supported than before. Repeated each round,
the cited set shrinks to a handful of sources carrying the whole report. Nothing in the pipeline
looked at what *happened to* a claim whose source was removed, so removal was a free discharge.

**The decision.** Effective only when the run reads and verifies (`search.read_sources` and
`search.verify_sources`, the gating of D-read-before-citing) and `review.shrink_finding` is true
(default). Otherwise no finding, event or prompt byte changes from the gating's perspective.

1. **Trigger (a rule about specific claims, not a count).** `triage.mechanical_shrink_issues` compares
   the draft with the one it replaced. A source is *dropped* exactly as the `generate` event counts
   `cited_sources_dropped`: `excerpt.citation_changes` — the previous body cited it, this body does
   not. For each previous-draft sentence that cited a dropped source, the new body is searched for
   the same claim: a sentence whose words (markers removed, case-folded) overlap the old one's by at
   least `review.shrink_min_overlap`. A claim that survives is discharged only when it now cites a
   source that was **not** cited in the previous draft **and** that some writer has read in full text
   (the run's read log). Every other surviving claim — left with no marker, re-pointed at sources the
   previous draft already cited, or at a new source nobody read — mints one finding at its new sentence.
   A claim withdrawn, or narrowed until it no longer matches, mints nothing. A first draft has no
   previous and mints nothing.
2. **Category and severity: `uncited_claim`, `major`.** No taxonomy change. What the reader is told
   is that a claim stands without the support it had, which is what `uncited_claim` names, and the
   floor is `major` so the clamp is a no-op and, as for D-read-before-citing, a `minor` would sit
   outside convergence and change no behavior. `one_sided_sourcing` was rejected: it is a judgement
   about the breadth of sourcing, and this is a fact about one claim. `incomplete_answer` was rejected
   for being about the answer's coverage, not a claim's support. Critics still may not raise
   `uncited_claim` on a marked claim; this is pipeline-minted.
3. **Always dischargeable, so it cannot loop.** The instruction offers three exits: cite a different
   source you have read in full text, narrow the claim to what a read source states, or remove the
   claim. The last needs no tool, so a spent read budget cannot make the finding undoable (unlike
   D-read-before-citing's, this finding is not suppressed when the budget is spent). The finding is a
   comparison of two *consecutive* drafts; once the claim is withdrawn or properly re-sourced the next
   comparison no longer matches it. Termination is unchanged (docs/convergence.md): the finding adds
   a `major` to the same tally, generation still advances `round` toward `hard_cap`, and rules 5/6
   still end the run there. It adds no controller input and no rule.
4. **Prompt.** `WRITER_READ_FIRST_ADDENDUM` now says that deleting a
   citation is not a resolution and that re-sourcing means citing another page the writer has read.
   This supersedes in place the wording of D-read-before-citing's writer rule, which said "re-source
   the claim to one you can" without saying that removing the citation is not that.
5. **Measurement.** A `shrink_check` event, once per draft that replaces another and only while the
   rule is in force, carries `cited_sources_dropped`, `cited_sources_added`, `entries_removed` (the
   same `citation_changes` values the `generate` event carries) and `shrink_findings`, the number of
   findings the evidence lens will mint for that draft. It is a separate event rather than a field of
   `generate` because the finding is derived at critique time, from the read log, after `generate`
   has been written.

**Knobs.** `review.shrink_finding` (bool, default true) turns the rule off. `review.shrink_min_overlap`
(default 0.6) is a matching heuristic with no measured basis: lower catches more reworded claims and
more unrelated sentences, higher lets a lightly reworded claim count as withdrawn. Both sit under
`review`, not `budgets`, so `_run_fingerprint` is unchanged. At most `search.max_source_urls` of the
previous draft's entries are considered, the bound every other mechanical check uses.

**Isolation (docs/isolation.md).** The finding's fields are the previous draft's entry numbers, their
addresses (truncated to 120 characters), the new sentence's own quotable span and fixed wording. No
page text and no other model's words enter a context. The previous draft is kept in graph state
(`draft_seen`, `draft_previous`), read only by the evidence lens's mechanical block and the event,
and never by `OrchestratorView`, the controller or a prompt. The event carries integers.

**Residuals.** (1) The finding exists only for the draft that follows the removal. If the writer
answers it by leaving the claim unmarked, the next comparison sees no further drop; the claim is then an
ordinary unmarked claim for the critics' `uncited_claim` category. (2) Word overlap misses a claim
reworded below `shrink_min_overlap` and so treats it as withdrawn. (3) A claim re-pointed at a long page
shown only in part is not caught by claim verification (`absent_partial` mints nothing by design);
that gap is separate and unchanged here.

**Rollback.** `review.shrink_finding: false`.
