## D-claim-level-verification — each cited claim is checked against its page in its own context, and the verdict is minted, not judged

**The finding.** D-claim-anchored-excerpts makes the characters an evidence critic sees relevant to
the report's citing sentences, but deliberately leaves every fetched page in one bounded evidence
context. Its own "deliberately not done" names the per-source sub-context D-unbounded-evidence
scoped as its follow-up. This decision supplies that sub-context at claim granularity.

Two properties of the single evidence context are what this decision changes, and neither is
addressed by choosing better excerpts:

* **Every page competes for one context.** `source_char_budget` is a per-artifact bound on page text
  shown to one critic. Pages beyond that budget are `FETCHED, TEXT WITHHELD` — reachable, unread —
  and the critic is told to raise nothing about them. The excerpts sharpen what the critic sees of
  the pages that fit; they do not change how many fit. Principle #6 of [isolation.md](../isolation.md) names this
  exact failure — retrieval degrades for material in the middle of a long context — and the
  `source_char_budget` comment has called the single context INTERIM since D-unbounded-evidence.
* **Absence was a judgement.** The critic is asked to hold in mind, per page, whether the shown part
  was the whole, and to raise absence only against a whole page. That distinction is currently a
  prompt instruction rather than a mechanically enforced condition.

The warrant is therefore mechanically inspectable offline: one context contains multiple pages, a
budget can withhold some of them, and an absence finding depends on whether the page was shown whole.

**The decision.** The unit of source verification becomes the **claim**, and each claim is checked
in its own context.

1. **Pairing is mechanical.** `claimcheck.pairs` walks the report's own structure — the loci
   critics quote — and pairs every sentence carrying a citation marker with the page the bibliography
   lists under that number, using `excerpt`'s sentence split, marker expansion (`[1, 3]`, `[2-4]`)
   and `entry_numbers`. The `## Sources` section pairs with nothing. The report's own sentence is
   "what the evidence is supposed to show": no writer manifest is consulted, because a manifest is
   the writer's account of its own support and letting it steer the check would let the writer grade
   its own review (the same reason D-writer-source-reads keeps the manifest out of the stop
   decision).
2. **One pair, one fresh context, the critic's own model.** Each pair is checked by the model
   holding the evidence critic's slot, in a context holding the sentence, its paragraph, and **one**
   page — as claim-anchored excerpts anchored on that one sentence up to `claim_check.page_max_chars`
   (30,000), shown whole when it fits (`excerpt.select`, the plumbing PR #208 supplied). It sees no
   other page, no other sentence, not the question, not the report, and not the critic's review. The
   schema is closed (`ClaimVerdict`): `supported`, `contradicted`, `absent`, `unreadable`, a
   `support_span`, a bounded `reason`. A `supported` or `contradicted` verdict whose span is not
   verbatim in the text shown is rejected on the repair loop and, past the budget, leaves the pair
   *unchecked*. A verdict nobody can point at in the page is not a check.
3. **Verdicts become findings mechanically**, in the shape D-notfound-fabrication established for
   the 404: `contradicted` mints `misrepresented_source` at its major floor, with the page's own span
   as `related_span`; `absent` mints the same **only when the page was shown whole**, because on a
   page shown in part absence from the excerpts is not absence from the page — the rule the critic
   was asked to keep in its head, now enforced by a boolean the excerpter reports; `supported`,
   `unreadable` and every unchecked pair mint nothing. The findings ride the critic's `LensResult`,
   so they clamp, deduplicate, count, withhold the clean record and reach the writer as tasks exactly
   as a critic's own would.
4. **A checked pair's verdict is authoritative for its category.** The critic still reads the whole
   report with the excerpts, and still raises everything the evidence lens raises. Its own
   `misrepresented_source` findings are kept for the pairs the checker did not settle — no page, an
   unreadable page, a failed call, past the cap — and dropped for the pairs it did
   (`claimcheck.reconcile`: same paragraph, the critic's span inside the checked sentence). One claim
   is never counted twice under two spans, and where the checker could not act the lens is exactly
   what it was.
5. **Every failure lands toward the writer.** A call that fails, answers outside the schema, or
   quotes a span the page lacks leaves the pair unchecked and mints nothing; the lens stays
   completed on the strength of the critic's own review. The one exception is the one every model
   call already has: a 402 during checking fails the lens with the account failure class, before the
   critic's larger call is spent, so `_defer_if_account_refused` defers the run
   (D-credit-exhaustion-defers).
6. **Verdicts are memoised for the runtime**, keyed on the critic's resolved identity and hashes of
   the complete system and user prompts. The key therefore covers the sentence, its paragraph,
   source number and URL, rendered page text, current date, and prompt wording. Re-running an
   identical prompt buys sampling noise and nothing else. This is the one point
   the design debated, and it is decided on purpose: the memo is keyed on the critic's identity, so a
   second family still forms its own view and cross-model confirmation is untouched; it is a memo of
   a *verdict*, never a clean record — clearance is still minted from the completed review of the
   current artifact and still resets on every generation (RC-002); and it is what turns the cost from
   "every pair, every round" into "changed pairs, every round". A checkpoint from the previous build
   resumes with an empty memo and re-derives.

Opt-in, off by default, `claim_check.enabled: false`, on the D-retrieval-opt-in pattern: with it off
the evidence lens is byte-identical to a build without it (no call, no event, no prompt change), and
enabling it without `search.verify_sources` fails closed at load, because there are no pages to
check. Counts go to a `claim_check` audit event per critic per artifact — pairs, checked, supported,
contradicted, absent, absent_partial, unreadable, unchecked, cached — and the sentences, spans and
reasons go to the run's purgeable critiques directory, never the event trail (RA-016).

**Invariants.** Author exclusion: the checker runs under the critic's slot with the critic's alias,
so `assert_author_exclusion` has already run for it, and no new party is introduced. Cross-model
confirmation: each evidence family runs its own checks and the memo is keyed on identity. Fail-closed
lens validation: unchanged — a bad critic field still fails the whole lens; the checker's own bad
field fails only the pair, in the direction that adds no finding. Severity floors: the minted finding
is at the floor and clamps like any other. Blind orchestrator: nothing new reaches
`OrchestratorView`; a minted finding is a count like any other. Untrusted text: the page and the
report sentence are fenced and marker-scrubbed in the checker's context as they are in the
critic's; the `related_span` the writer sees is a bounded page quote inside a structured task, the
same class of thing a critic's rationale already carries. Termination: no new rule, field or budget;
calls per pass are bounded by citation markers × `review.depth` and the anti-pathological
`claim_check.max_pairs`, and budgets count passes, never calls. Dispute adjudication: a dispute
against a minted finding has the checker's page span to argue against, and the mechanical path
already checks quotes against the retained body.

**What this does not fix, stated plainly.** `uncited_claim` and per-sentence citation demands stay a
critic judgement. A page that truly is a menu is `unreadable`, which is the honest answer, and the
writer still has to cite a better page — `search.read_sources` is the lever for that, and it was off
in the runs measured. The logic lens's alternating-critic variance (one critic filing four to ten
issues per pass where its alternates file zero to two on the same drafts) is a roster question and
is not touched. Cost is real: on a fresh draft the checker makes one call per citing sentence per
evidence critic, serially within the critic's slot under the existing `max_concurrency`; the memo
makes later rounds cheap, and the `cached` count on the event is how to see whether it is doing so.

**Deliberately not done.** No change to `LENS_CATEGORIES`: the critic may still raise
`misrepresented_source`, because the checker does not reach every citation and a lens that could not
raise the category for an unchecked page would be weaker than before. No chunk-and-aggregate for
pages past `page_max_chars`: the excerpter already puts the sentence's own passages in front of the
checker, and `absent` on such a page is deliberately not a finding. No parallelism inside a slot:
`max_concurrency` bounds proxy load and the checker respects it. No audition question for the
checker yet: `ra audition` measures critics on the critique prompt, and a claim-checker fixture set is
its own piece of work. No live A/B in this PR: the measurement is the evidence lens's clearance rate
and the `claim_check` event counts on the next runs, read per
[run-provenance.md](../run-provenance.md).
