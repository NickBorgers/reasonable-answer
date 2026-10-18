## D-unreadable-citation-finding — a claim whose every cited page the checker could not read is an `uncited_claim`

**The finding.** With the claim check on (D-claim-level-verification), an `unreadable` verdict
meant the checker was shown navigation, a catalog landing page or a paywall instead of the work.
D-claim-check-inconclusive-verdicts correctly made that verdict mint nothing and retire nothing: it
is not a reading of the page. But it left a hole. A sentence whose only citations are unreadable
pages was never read by anyone, yet nothing flagged it, and a run could be accepted with a
persistent fraction of its claims resting on pages no reader — human or model — could check. The
run label's "bodies read" count had the same blind spot, since a landing page is a body.

**The decision.**

1. **Rule.** `claimcheck.issues_from(..., unreadable_finding=True)` mints one `uncited_claim` per
   sentence when every pair of that sentence is an `unreadable` verdict from a completed checker
   call. A sentence with any `supported`, `contradicted` or settled `absent` pair is not covered:
   one readable page that states it is enough, and a readable page that contradicts it is already a
   `misrepresented_source`. A sentence with any `unchecked` pair (failed call, `aborted`, no page,
   cut by `max_pairs`) mints nothing — fail-toward-the-writer stands; only a definite verdict counts.
2. **Category: `uncited_claim`.** "A material claim with no citation" is the nearest existing
   meaning: a citation no reader can check does the work of none. `misrepresented_source` would
   assert the source says something different, which is exactly what was not established.
   `taxonomy.py` is unchanged.
3. **Severity: the category floor, `major`.** It blocks acceptance, because the claim is
   unverifiable as cited and acceptance certifies that no eligible reviewer found a material defect.
   It cannot be lower: floors clamp up only, and `uncited_claim`'s floor is `major`. Termination is
   not endangered: the writer can always discharge it by citing a readable copy, citing another
   source, or narrowing or removing the claim, and a run that does none of these ends under the
   ordinary `hard_cap`/rule 6 path like any other open `major`. On a topic whose good sources are
   paywalled the instruction names the narrowing exit explicitly.
4. **Per slot, no cross-slot veto.** The claim check runs inside each evidence critic's slot and
   its verdicts are memoised per critic identity (QP2). The finding is that slot's view, like any
   finding it raises; another slot's `supported` on the same pair does not suppress it. Requiring
   agreement would need a cross-slot join in the graph for a case a dispute can already answer.
5. **Text.** Pipeline-authored from the entry numbers: a fixed rationale and a fixed instruction
   asking for a readable copy of the same work (open-access version, full text rather than a
   catalog, abstract or paywall page), another source, or a narrower claim. Never page text and never
   the checker's `reason` (docs/isolation.md: untrusted text never reaches a generator as instruction).
6. **Knob.** `claim_check.unreadable_finding` (default `true`, in `config/roster.yaml`'s
   `claim_check:` block) is the rollback; `false` restores `unreadable` minting nothing. It is not
   under `budgets`, so the run fingerprint is unchanged.
7. **`reconcile` unchanged.** `unreadable` still retires no finding of a critic's own.

This supersedes, in place, the part of D-claim-check-inconclusive-verdicts that said `unreadable`
mints nothing; the rest of that decision (settle rule, cut page, circuit breaker) stands.

**Invariants.** *Fail-closed lens validation:* unchecked still mints nothing; the finding needs a
completed verdict from every pair. *Severity floors:* minted at the category floor, clamped up only.
*Termination:* no new rule or budget; dischargeable. *Untrusted text:* none enters the finding.
*Author exclusion / blind orchestrator:* untouched; the controller sees counts only.

**Deliberately not done.** The run label still counts a landing page as a "body read"; that badge is
separate. No cross-slot agreement. No change to the checker prompt or the `unreadable` definition.
