## D-resync-defers-to-finalize — the base-moved resync waits for the verdict anchor, not just for the run

**The finding.** `sync-open-prs.yml` already waits out a review run in flight on the branch
(D-base-moved-resync), because the fixer discards a whole cycle's fixes when it finds the branch
moved. Issue #188 reports a second window, narrower and more expensive: the review pipeline
publishes the two facts `gather` needs *at different times*. `record-cycle` writes
`review/cycle` as soon as the panel has read the code, and `review/verdict-anchor` lands with
finalize, minutes later — on PR #181's cycle 1, 12:42:28 and 12:50:50 respectively, an
eight-minute gap. A resync pushed inside it hands the successor's `gather` a cycle-recorded SHA
carrying no anchor, and an anchor that cannot be verified is not an anchor
(D-inherit-reviewed-anchor): the classifier fail-closes, and a full five-role panel reads a head
whose only delta is a driver-resolved merge. The fail-closed direction is right — guessing there
is a merge-gate bypass — so the fix belongs at the other end, in the workflow that chose the
moment.

**The decision.** A PR is `deferred` when either question about the review pipeline is still
open: a run is in flight on its branch, or its head carries a `review/cycle` with no
`review/verdict-anchor` published yet. `deferred` is reported by `sync-open-prs.yml` before it
calls `scripts/sync_pr_with_base.sh`, distinct from the states the script prints; it leaves the
PR exactly as it already was, and the next push to the base branch retries it. Both waits share the one existing
10-minute deadline for the whole loop, so waiting still costs minutes in total rather than a
multiple of however many PRs are open.

**Why the wait is bounded by the age of the cycle record, not by the deadline alone.** A run
cancelled or killed between the two writes leaves that pair on the head permanently. Deferring
on it forever would wall off precisely the PR this workflow is the only unblocker for — a
cleared PR with a `docs/decisions.md` collision and no cycle left to resolve it — so a cycle
record older than 30 minutes is treated as abandoned and the resync proceeds. That is the
pre-decision baseline, which strands nobody.

**Why not the literal form the issue proposed.** Issue #188 suggested skipping any PR "whose
head carries no published `review/verdict-anchor` yet". Taken literally that is a deadlock: a
head no panel ever recorded a cycle for — a fresh PR, or one whose every reviewer guard refused
— carries no anchor and never will, and it is exactly the PR that needs a merge to become
reviewable at all. The condition is therefore the *pair*: a cycle recorded, its verdict not yet
published. A missing cycle is not a race.

**Also considered: a scheduled sweep as the retry hook.** The issue offers "next push, or a
scheduled sweep". A `schedule:` trigger cannot carry the pin this workflow's trust boundary
depends on — both checkouts and `BASE_REF` resolve
`github.event.repository.default_branch`, and a scheduled event has no repository payload — so
adding one would replace a pinned ref with a named branch in the job that holds `WORKFLOW_PAT`.
The next push to the base branch is the retry, as it already is for a run in flight.

**Tests.** `tests/test_ci_base_moved_resync.py` extracts the workflow's `resync` step and drives
it under `bash` with a stub `gh`, a stub for the sync script, and a fake clock advanced by a
stub `sleep`, so the ten-minute budget is exercised in milliseconds. Pinned: a finalized head is
offered to the script at once; a head no panel has recorded a cycle for is offered too; a
recorded cycle with no anchor defers, pushes nothing, and waits under the shared deadline; a
cycle record past the grace stops deferring; and a run in flight still defers, which is the
behaviour this generalises rather than replaces.

**Invariants.** None of the six is in reach — no model, no context, no prompt. The trust
boundary is untouched: the same two checkouts, the same trusted-root argument, the same
exclusion of forks and drafts, and no new executable path. What changes is only *when* the
existing merge is attempted, and the direction of the change is fewer pushes, never more.
