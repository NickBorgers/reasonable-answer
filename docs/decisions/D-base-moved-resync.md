## D-base-moved-resync — the merge driver runs when the base moves, not only when a cycle does

> **Retired by D-decision-per-file.** This entry added the missing call site for a driver that is now
> retired, so `sync-open-prs.yml` still runs on every push to `main` and finds nothing to push: its
> own no-driver baseline step returns `plain` or `conflicts` for every PR, neither of which pushes.
> The underlying need is gone rather than unmet — a decision is its own file, so the base moving no
> longer invalidates every open decision-bearing PR. Deleting the workflow is a follow-up.

**The problem.** D-decisions-merge-driver is correct, and it does resolve the collision it was built
for. PR #158's own three-way inputs — merge base `75e7f1a` and base tip `bfa6277`, both on `main`,
against its head `8c236c7` on `refs/pull/158/head` — conflict under `git merge-file` and merge
cleanly under `scripts/merge_decisions.py`. It nonetheless did not resolve that PR. Every call site
the decision added — `review-fixer.yml`'s two sync steps and `review-pipeline.yml`'s inherit
recreation — is reachable only from a review cycle, and PR #158 had no cycle left: its last panel
finished at 03:00 UTC on 2026-08-04, `bfa6277` landed on `main` at 04:00:50, and auto-merge was
enabled eight seconds later. Nothing in the pipeline runs on "the base branch moved", so the driver
was never invoked. The PR sat conflicted for three days and was freed by a hand merge in the GitHub
web editor (`aade6d3`, the head of `refs/pull/158/head`) — the exact gesture the decision exists to
abolish, on the exact shape it recognizes.

Repository contents alone cannot cover this gap. [Git defines a custom merge driver's command in
`$GIT_DIR/config` or `$HOME/.gitconfig`, not in
`.gitattributes`](https://git-scm.com/docs/gitattributes#_defining_a_custom_merge_driver), which is
why every registration in this codebase is a `git config` call in a checkout. PR #158 supplies the
repository-specific observation: the unconfigured merge stayed conflicted while the registered
driver resolved the same three-way inputs. A PR whose sole obstacle is this collision therefore
stays blocked until a configured checkout performs the merge.

**The decision.** A new workflow, `sync-open-prs.yml`, fires on push to the base branch and re-merges
the base into open PRs — but only where the driver is what makes the merge succeed. Per PR it runs
the merge twice through `scripts/sync_pr_with_base.sh`: once with no driver registered as the
unconfigured baseline, and if that conflicts, once with the trusted driver
registered. It pushes only when the first conflicts and the second is clean. Everything else is a
state the PR was already in and keeps: `none` (already contains the base tip), `plain` (merges
without the driver, so nothing here is what unblocks it), `conflicts` (a shape the driver declines — an
edited section, an edited Open-items list, a same-slug collision — left for the review cycle's
agentic resolution or a human, exactly as before), `moved` (the branch changed under the merge).

**Why not re-merge every PR that is behind.** The reviewed SHA is the key for dedup, the cycle
counter, the artifact names and the merge-gate status. Pushing a merge no one needed churns all of
them, on every PR, on every push to `main`. The narrow rule also states the benefit exactly: this
workflow adds nothing GitHub could have done itself.

**Why a push, and why with `WORKFLOW_PAT`.** The merge has to reach the branch for auto-merge to see
a mergeable PR, and the new head needs the merge-gate status republished on it or auto-merge simply
waits on a check that can never arrive. [GitHub documents that a PR `synchronize` event caused by
`GITHUB_TOKEN` creates a workflow run in an approval-required state, while a personal access token
allows it to run automatically](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow).
The PAT is therefore what republishes the gate without a human approval step. That run does not cost
a cycle: a base-resync merge is precisely what D-inherit-whole-range's classifier inherits through,
and because both this workflow and that classifier register the same trusted driver, a
driver-resolved merge recreates tree-identically there too.

The merge is authored as `AGENT_COMMIT_EMAIL`. `review-pipeline.yml` resets the cycle counter to 1 on
any commit not authored that way, reading it as a human who answered the blockers; a resync is nobody
answering anything, and billing it as a human intervention would hand a capped PR a fresh budget of
agent cycles for a merge no one wrote.

**Racing the fixer.** The fixer re-reads the remote head before pushing and discards a whole cycle's
fixes if the branch moved, so a push landing underneath it is not merely redundant. A review run in
flight also does not need help: gather detects the drift and the fixer performs the same merge with
the same driver. So the workflow waits for an in-flight run on that branch rather than racing it,
under one 10-minute deadline for the whole loop, and skips the PR if the run outlasts it — the next
push to the base branch retries. `sync_pr_with_base.sh` carries the matching backstop, refusing to
push when the remote head is no longer the SHA it merged from.

**Trust boundary.** Two checkouts: `trusted`, the base branch, checked out with no token and the only
thing ever executed; and `work`, which holds the credential that can push and is reset to a
contributor's branch, never run from. This is the same split, for the same reason, as the sync and
inherit steps — a PR's own `scripts/merge_decisions.py` must not run in a job that can push to any
branch in the repository. The workflow has no manual-dispatch entry point, and both checkouts plus
`BASE_REF` are pinned to `github.event.repository.default_branch`; an event-selected ref must never
decide which code runs with `WORKFLOW_PAT`. Fork PRs are excluded outright: their branches are not
ours to push to.
Nothing here fails the workflow for a PR it cannot help, because not syncing is the pre-decision
baseline and strands nobody, while a red X on `main` for a PR the script declined to touch is noise
on every push. The one exception is a driver-routed path that comes back merged while still carrying
conflict markers: pushing that would put markers into a normative spec file under a commit message
claiming a clean sync, so it stops instead.

**Invariants.** None of the six tabulated pipeline-core safety invariants is in reach — none
constrains how a model's context is built, and no model is involved here at all. The one gate this
touches is D-inherit-whole-range's, and only by feeding it more of the input it already handles: the
classifier itself, its tree-identity test and its fail-closed direction are unchanged, and a merge
this workflow could not make cleanly is never pushed for it to classify. QP7's capped-loop
requirement is preserved at the new entry point — the in-flight wait is bounded by a single deadline
shared across the whole loop, and the commit authorship keeps `MAX_CYCLES` from being reset by a
machine merge.
