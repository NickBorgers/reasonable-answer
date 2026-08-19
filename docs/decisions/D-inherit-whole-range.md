## D-inherit-whole-range — a verdict is inherited by what the push contains, not by the shape of its head

Found by reading three pipeline runs on 2026-08-01/02 (#126, #127, #130) that all reported
"introduces no new content" over pushes that plainly did.

**The problem.** `review-pipeline.yml`'s merge-from-base short-circuit classified a push by the
head commit alone:

```bash
parents=$(git rev-list --parents -n 1 "$SHA" | wc -w)
if [ "$parents" -ge 3 ]; then
  if git merge-base --is-ancestor "${SHA}^2" "origin/${BASE_REF}"; then
    # ... inherit the prior verdict, run no reviewer
```

Nothing looked underneath that merge. The comment above it said the optimisation exists so "a
resync should not burn a cycle" — the right goal, tested with the wrong predicate: it asks *is
the head a resync commit*, not *is the push only a resync*. So a push of `content, content,
git merge origin/main` had the head of a pure resync and the body of a normal change, and the
pipeline took the inherit path: no reviewer role executed, and the prior verdict was re-stamped
onto content no model had read.

The three observed cases were all the annoying direction. #126 (2 content commits: the citation
fetchability fix answering a QP9 blocker, plus an unplanted-defect repair), #127 (a QP9 doctrine
correction across 3 files) and #130 (a `RUBRIC_VERSION` bump and fixture reshape) each had a
stale NO-GO re-published over the fixes that answered it, so the panel never read the answer and
the PR could not converge.

**The direction that had not happened yet is the reason this is a bug and not a cost defect.**
Push the content, then `git merge origin/main`, and a prior **GO** is re-stamped just as
readily. That is a merge-gate bypass available to any author, needing nothing but ordinary git
commands in an ordinary order, and leaving a run log that says the content was checked.

**Decision.** Inherit only when the *whole pushed range* is a base resync, established by two
tests that must both pass:

1. **Range shape.** Every commit reachable from the head but from neither `PRIOR_CYCLE_SHA` nor
   `origin/<base>` — `git rev-list "$PRIOR..$SHA" "^origin/$BASE"` — must be a merge whose every
   merged-in parent is already on the base branch. The `^origin/<base>` exclusion is what keeps a
   genuine resync from tripping this: the commits it carries across are reachable from the base
   branch, so they are never billed to the push. Without that exclusion the whole optimisation
   dies, because every base commit merged in reads as a non-merge commit in the range.
2. **Tree identity.** `git merge-tree --write-tree "$PRIOR_CYCLE_SHA" "${SHA}^2"` must produce
   exactly `${SHA}^{tree}`.

Two guards precede them: `PRIOR_CYCLE_SHA` must be non-empty (unchanged — a first cycle is always
read) and must still be an ancestor of the head, since after a force-push "everything since the
reviewed SHA" names nothing measurable.

**Why both, when the tree test is strictly stronger.** It is stronger, and it alone would be
sound. The range test is kept for two reasons that are about operating the pipeline rather than
about correctness. It names the offending commits in the run log — "`<sha>` is a content commit
under the head merge" — where a tree mismatch can only report two hashes, and diagnosing an
inherit that should have happened from two hashes is miserable. And it is pure plumbing available
on every git, so the cheap, legible test runs first and the strong one confirms it.

**Why the tree test is not optional.** Range shape is still a shape test. A merge that conflicts
is resolved by a human or by the fixer agent, and a resolution can put arbitrary content into a
commit whose every parent passes test 1 — the same bypass, one step further along. The tree test
is the only one that asks what the commit actually *contains*. It also incidentally rejects an
octopus merge whose third parent is off the base branch, which the old `^2`-only check never
looked at.

**It fails closed, and that costs something.** `--write-tree` needs git ≥ 2.38, and a re-created
merge that conflicts exits non-zero; both land on "review normally". The price is one spent cycle
on a PR that genuinely conflicted with its base, or on a runner with an old git. The alternative —
inheriting when we could not verify — is the defect this decision fixes, so the direction is not
a close call.

**This narrows D-fixer-merges-not-rebases.** `docs/ci-pipeline.md` used to say the fixer's sync
merge "lands on the merge-from-base inherit path like any other". That now holds for a **clean**
host merge and not for one the agent had to resolve: a resolution changes the tree, so the panel
reads it. The residual that decision names — a fixer-authored merge whose conflict resolutions
are wrong-but-clean reaching main unread — is closed for the inherit path specifically. It is
untouched on the fixer's normal path, where the fixer claims its own pushed SHA and no second
panel runs at all; that is the owner's intent and is not in scope here. The D-unguarded-sync
sync-only successor still inherits, correctly: that pass abandons on conflict, so what it pushes
is a clean merge of a reviewed tree with the base and nothing else.

**Guard rails preserved.** `/review` still outranks the whole path (PR #56) and is now checked
first. An empty `PRIOR_CYCLE_SHA` still reviews. A verified-pure resync still inherits, which is
the optimisation that made the anchor-conflict rebase churn across eight concurrent PRs
affordable.

**Tested by running the step, not by reading it.** `tests/test_ci_inherit_classifier.py` extracts
the `run:` block from `review-pipeline.yml` and drives it under `bash` over throwaway git
repositories, with a stub `gh` answering the one status query it makes — offline, no token. Seven
of its fourteen cases fail against the old classifier, including both directions of the bypass
and the hand-resolved conflict. A predicate this cheap to get wrong and this expensive to get
wrong is not one to leave to a reviewer noticing a diff in YAML.
