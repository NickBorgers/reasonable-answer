## D-inherit-reviewed-anchor — the inherit check measures from the commit a panel read, not from the commit a cycle was recorded on

Found by reading PR #162's run 31190337154 on 2026-08-07 (issue #163), which reported "there is no
new content to read" over three files of fixes that had just been written to answer its own
blockers.

**The problem.** D-inherit-whole-range's two tests are sound; what they measured *from* was not.
Both anchored on `steps.prior.outputs.cycle_sha` — the SHA the cycle counter was last recorded on.
That is not the SHA a panel read. `review-finalize.yml` stamps `review/cycle` and `review/verdict`
on `post_fix_sha`, which is the fixer's own push whenever the fixer pushed, and it does so for a
good reason: the fixer claims that SHA so no second panel reviews the fix
(D-fixer-merges-not-rebases), which makes this run the only one that will ever stamp it. So a
successful fix moves the cycle marker onto a commit no reviewer has read, and the next run's
gather resolves `PRIOR_CYCLE_SHA` to **the head it is classifying**.

Both tests then pass on nothing:

- `git rev-list "$SHA..$SHA" "^origin/main"` is empty, so the whole-range walk counts zero
  unreviewed commits.
- `git merge-tree --write-tree "$SHA" "${SHA}^2"` reproduces `${SHA}^{tree}` by construction —
  `${SHA}^2` is already merged into `$SHA`, so merging it again is a no-op.

The observed run's own log states it plainly: `pure merge-from-main since
b1fbd60a5359cd8b496be4f78ab1733fc5112acb`, where `b1fbd60` *is* the reviewed SHA. Measured against
the head the panel actually read, the same recreation refuses:

```
git merge-tree --write-tree b849671 bd352e9   → c6e6ed859a8a…
git rev-parse b1fbd60^{tree}                  → d73953d75152…
git diff c6e6ed8 d73953d --name-only          → docs/decisions.md, docs/quality-principles.md, pt.log
```

Net effect: a fixer push not followed by a further commit could never be re-reviewed, so the
review → fix → re-review loop was broken at its last step. It failed in both directions, the same
pair D-inherit-whole-range names: the observed one re-stamps a stale NO-GO over real fixes; the
unobserved one re-stamps a **GO** over anything recorded onto a prior cycle — the merge-gate
bypass, resurfaced through the cycle-recording seam rather than through the head's shape.

**Decision.** Anchor both tests on the commit the inherited verdict was published *for*.
`review-finalize.yml` writes a third per-SHA status alongside the two it already writes. The original
form was `review/reviewed-sha`, on `post_fix_sha`, whose description was `reviewed_sha`; the inherit
step read it off `PRIOR_CYCLE_SHA` and ran the range walk and merge-tree recreation from that anchor.
D-atomic-verdict-anchor supersedes the separate-status pairing: the same anchor now lives in the
description of `review/verdict-anchor`, with its verdict in that status object's state.

Everything else is unchanged: `/review` still outranks the whole path, an empty prior still reviews,
a recreation that conflicts still reviews, a recorded cycle with no verdict still reviews, and the
recreation still registers the trusted `docs/decisions.md` merge driver from the `main` checkout
(D-decisions-merge-driver). The mechanics of D-inherit-whole-range are kept verbatim; only their
origin moves.

**Why not "the SHA the newest `review/verdict` status sits on",** which is what issue #163 proposed.
It resolves to the same fixer push, because finalize stamps the verdict there too — the status says
where the verdict was *recorded*, and the missing fact was which commit it was *about*. No existing
status carried that, so the fix is a new one rather than a different read of an old one.

**Two guards the new anchor needs.**

- *An unverifiable anchor is not an anchor.* A cycle recorded by a `main` older than this decision
  carries no `review/verdict-anchor`, and the two things it could mean — "the panel read this commit"
  and "the fixer pushed this commit" — are the inherit decision and its exact inverse. So an absent
  or malformed anchor (anything but a 40-hex object id) reviews normally. The cost is one full read
  for PRs mid-flight across this change, after which their next finalize writes the status and they
  inherit as before; the alternative is guessing, in a step whose wrong answer is a merge-gate
  bypass.
- *The anchor may equal the head.* When it does, the panel read this exact commit and nothing has
  been pushed since: there is no range to walk and no merge to recreate. That case now says so and
  inherits, rather than falling through to shape tests that would pass vacuously for a merge head
  and refuse a non-merge one for no reason connected to what was read. The triggers are real if
  uncommon: `review-entry.yml` fires on `reopened` and `ready_for_review` as well as `synchronize`,
  and `cleanup-claim` releases the dedup claim on every path, so an unchanged head can be entered
  again after its verdict was published. The verdict guard still applies: nothing to inherit still
  means review normally. Note the difference from the bug this fixes: the head compares against
  *itself* only when the anchor genuinely names it, never because the anchor drifted onto it.

The ancestry guard also moves above the head-shape tests. An anchor that a force-push removed from
history makes every test below it meaningless rather than merely false, so it is answered first.

**Tests.** `tests/test_ci_inherit_classifier.py` extracts the step and drives it under `bash`
against throwaway repositories, offline. Added: the observed PR #162 shape — a fixer merge carrying
content, recorded on the cycle it fixed — must review, and the same input with the anchor pointed
back at the cycle-recorded SHA must inherit, so the test fails on a revert of the anchor and on
nothing else; the same content one commit further along, under a later clean resync, where test 1
passes and only the recreation refuses; the unchanged-head case inherits, and does not inherit
without a verdict; absent and malformed anchors review.

**Invariants.** None of the six pipeline-core safety invariants is in reach: no model's context is
built here and no model runs. The gate this touches is D-inherit-whole-range's own, in the
strengthening direction — the set of pushes that can inherit shrinks to those measured against a
commit a panel actually read, plus the head-equals-anchor case that reads nothing new by
construction. QP7 and QP8's citations of that gate stand as written; QP8's "deterministic
whole-range/tree-identity gate" is now deterministic in its origin as well as its tests.
