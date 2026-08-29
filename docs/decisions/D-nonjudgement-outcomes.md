## D-nonjudgement-outcomes — the cost backstop and the pipeline error are not verdicts, and not spent cycles

**The finding.** Merging the eight-PR stack #180–#187 on 2026-08-18 (issue #188) walled three
already-cleared PRs off from the merge gate with NO-GO verdicts that were about the pipeline
rather than about the change. The commit statuses record the whole mechanism:

| SHA | what it is | what it carries |
|---|---|---|
| #183 `4e9c6b6` | a pure resync merge | `review/cycle 3`, then **both** `All Required Agent Reviews success GO — inherited` (13:52:58) and `failure NO-GO — review cycle cap reached (cycle 3)` (13:53:03) |
| #182 `6af6acc` | a pure resync merge | an inherited NO-GO whose source anchor was a `pipeline_error`, plus a second, racing cap NO-GO |
| #186 `e748c86` | a resync merge | `pipeline_error` NO-GO at cycle 2, with **no** `record-cycle` write behind it — finalize stamped `review/cycle 2` for a panel that never ran |

Three distinct laundering steps, each turning "the pipeline could not do its job" into "the
change is not fit to merge":

1. **An inherited verdict advanced the cycle counter.** `gather` computed `cycle = prior + 1`
   before knowing whether the run would inherit, so every resync billed one — which is
   precisely what the short-circuit exists to prevent (D-inherit-whole-range: "without this,
   routinely resyncing a long-lived branch burns cycles and can push a PR into the cap without
   a single substantive change"). Two resyncs after a cycle-1 panel reached cycle 3.
2. **`cap_exhausted` and `inherit` are not mutually exclusive, and both finalize.** The
   `inherit` job never tested the cap, and the `cap-exhausted` job never tested inherit, so on
   a capped resync both ran. Each publishes the verdict, the anchor and the merge gate, so the
   outcome is whichever API write lands last — on #183 the cap NO-GO, three seconds after the
   inherited GO, on content two panels had already cleared.
3. **A non-judgement NO-GO anchors exactly like a judgement.** `review/verdict-anchor`'s state
   was `verdict == 'GO' && 'success' || 'failure'`, so `cycle_capped` and `pipeline_error`
   both wrote `failure`, and the next resync inherited them as verdicts. The cap comment says
   in its own words that it "is a cost backstop, not a judgement about the change"; a
   `pipeline_error` says the judge could not trust its inputs. Neither is a finding.

A fourth, adjacent to the same principle: `review-finalize.yml` stamped `review/cycle` on every
path, including the ones where `record-cycle` was deliberately skipped because no reviewer's
guard cleared. "A run that reviewed nothing does not consume a cycle" was therefore true of
the writer that defers and false of the writer that finalizes — the same run billed twice over,
one commit apart (#186), and the sync-only path (D-unguarded-sync) billed despite this
pipeline's own comment stating it costs nothing.

**The decision.** A run that produced no judgement may neither publish one nor spend a cycle.

- *The counter holds on an inherited verdict.* `gather` computes the cycle after the inherit
  decision, and when inheriting it keeps the cycle the inherited verdict belongs to.
- *The cap cannot fire on an inherited run.* `cap_exhausted` is false whenever `inherit` is
  true, and the `cap-exhausted` job additionally requires `inherit != 'true'` so the two
  finalize paths stay disjoint even if that arithmetic regresses. `fix_allowed` is false there
  too: with the counter held, the arithmetic alone would otherwise offer a fix to a run with no
  findings and no panel.
- *Non-judgement outcomes anchor as `error`.* `cycle_capped` and `pipeline_error` write
  `review/verdict-anchor` with state `error` — the state D-atomic-verdict-anchor already
  reserves for "not an inheritable verdict" — and `gather` names that case explicitly rather
  than reaching it through its catch-all. `review/verdict` and the merge gate still report
  NO-GO on those paths, so nothing about the gate is loosened; only what a later run may treat
  as somebody's judgement changes.
- *Finalize stamps `review/cycle` only when one was recorded.* A new required
  `cycle_recorded` input carries `needs.record-cycle.result == 'success'` on the panel path,
  and `true` on the two panel-less paths that have their own reason to stamp: the cap records
  the count it died on, and the inherited path re-states its held count on the head it just
  cleared, which keeps that head one hop from the next resync's cycle walk.

**Why "review normally" rather than "look through to the last content verdict".** Issue #188
offered both. Looking through — walking back past an `error` anchor to the previous cycle's
anchor — buys nothing here: the commit that would be inherited from is separated from the head
by the fixer's own content commit, which the whole-range test counts as unread and refuses on
(D-inherit-whole-range, D-inherit-reviewed-anchor). It would add a second trust input, and a
loop over cycle records, to reach the same panel. Refusing converges by the shorter road: the
panel produces a real verdict, or the cap answers for a PR that genuinely spent its budget.

**What this does not change.** The cap still bounds what it was built to bound. `MAX_CYCLES`
bounds review → fix → push → review, and no stage of that loop runs on an inherited path — the
reviewers, `record-cycle` and `fix` are all gated on `inherit != 'true'` — so an inherited run
cannot advance the loop it would be billed for, and it cannot repeat without a fresh push whose
only content is a merge of a base branch somebody else moved. The counter's other rules are
untouched: a human commit still resets it, `/review` still outranks the inherit short-circuit,
and a PR whose panel cycles are genuinely spent still reaches the terminal cap NO-GO and waits
for a human, which is the documented cost policy.

**A residual, named rather than papered over.** A cycle whose panel *ran* and then died of a
`pipeline_error` still bills the counter, because `record-cycle` fired before the judge did.
Such a PR can still reach the cap on flakes alone (#182 is that shape) and needs a human. What
this decision removes is the laundering — the flake no longer travels forward as a verdict, and
no resync adds to the count — not the possibility that a real cycle is wasted by one.

**Tests.** `tests/test_ci_cycle_accounting.py` drives `gather`'s `Compute cycle` step under
`bash` against a throwaway repository: the first cycle, an agent push advancing, the cap firing,
a human push resetting, an inherited run holding its count with `fix_allowed=false`, and an
inherited run past the cap never reporting `cap_exhausted` — each paired with the
non-inheriting input that still behaves as before, so the tests fail on a revert and not on a
refactor. It also pins the two YAML facts no script can assert: that the cap and inherit
finalize conditions are disjoint, and that finalize's `review/cycle` stamp is gated on a
`cycle_recorded` input which is required and undefaulted.
`tests/test_ci_inherit_classifier.py` adds the `error` anchor reviewing normally (with the same
push inheriting when the state carries a verdict, so the anchor state is the only variable) and
pins finalize's category-to-state mapping.

**Invariants.** None of the six pipeline-core safety invariants is in reach: no model's context
is built here and no model runs. The gate this touches is D-inherit-whole-range's, and only in
the *narrowing* direction — the set of anchors a push may inherit from shrinks by two
categories, while the whole-range walk, the tree-identity recreation and their fail-closed
direction are unchanged. QP7's capped-loop requirement is preserved: the cap still bounds every
path that can push, and the path this decision exempts from it can neither push nor run a panel.
