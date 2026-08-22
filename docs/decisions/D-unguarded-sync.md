## D-unguarded-sync — the base-branch sync runs even when the panel was guarded off, and stays non-agentic when it does

**The problem.** The base-branch sync D-fixer-merges-not-rebases built to keep agent-authored PRs mergeable was
unreachable in a family of cases it exists for. `fix` was gated on `record-cycle` succeeding, and
`record-cycle` only writes when at least one reviewer's guard cleared. Every reviewer's guard
requires a completed, successful `PR Validation Required` check on the reviewed SHA. So whenever
that check is absent or red while the base has moved underneath the branch, every guard refuses,
the panel is skipped, `record-cycle` writes nothing, and the one stage that could resync never
runs.

The sharpest form is a PR that already *conflicts* with its base. GitHub cannot compute a merge
ref for it, so it fires no `pull_request` event, so `pr-validation.yml` never runs, so the check
never appears, and the deadlock is self-sustaining:

```
conflicted PR → no merge ref → no pull_request event → no PR Validation
  → every reviewer guard refuses → no reviewers → record-cycle skipped → no fixer
  → the conflict is never resolved
```

`/review` reaches the graph through `issue_comment`, which fires regardless of merge state, but
with the panel guarded off the run did nothing but re-publish a fail-closed NO-GO. PRs #54 and #56
both sat in this state and had to be unstuck by a human merging `main` in by hand.

**Rejected: fixing it at PR Validation.** #67 added a `push` trigger to `pr-validation.yml` — a
push needs no merge ref. But that workflow's concurrency group keys on `head.ref`/`head.sha` with
`cancel-in-progress: true`, so a push and its paired `pull_request` event land in the same group
and one *cancels* the other. Both publish a check named `PR Validation Required`, and a cancelled
required check is not a success, so the PR could show that required check as failed when nothing
had failed. It was reverted in #68. The other options weighed in #68 — separate concurrency groups
(doubles CI on every push), a second check name the guard also accepts (widens what "validated"
means), or relaxing the guard to proceed when validation is *absent* (loosens the gate that keeps
reviewers off unvalidated code) — each pay a cost on the healthy path to fix the stuck one.

**The decision.** Fix it at the fixer's gate instead, which is where the maintainer's analysis in
#68 landed: *let the fix job run without the guard's PR-Validation precondition when the only work
is a base-branch sync — a sync consumes no reviewer findings, so the precondition buys nothing
there.* `gather` now computes `needs_sync` (is the reviewed SHA behind `origin/<base>`, by the same
`merge-base --is-ancestor` test the fixer's sync uses), and `fix` has a second, disjoint way in:

- **normal path** — `record-cycle` succeeded (a reviewer ran) *and* `fix_allowed`. Blockers plus,
  if the base moved, a sync in the same commit. Unchanged.
- **sync-only path** — `record-cycle` was *skipped* (every guard refused) *and* `needs_sync`. The
  fixer is called with `sync_only: true`.

**`sync_only` makes the pass non-agentic, and that is the whole safety argument.** The first draft
of this change simply dropped the `record-cycle` precondition and reasoned that with no reviewer
artifacts the fixer would count zero blockers and "do nothing but the merge". That was wrong, and
the security lens caught it on review: `review-fixer.yml`'s work gate sets `agent=true` when
blockers are non-zero **or the base merge conflicts**. A conflicting merge is exactly the state the
motivating case produces, so the sync-only path would have reached `review-agent-run` — the
write-capable fixer, on the self-hosted runner, with host networking and pipeline credentials —
carrying a tree, a PR body, and conflict contents that are all contributor-controlled and that *no
reviewer has read and PR Validation may never have checked*. On the normal path a cleared reviewer
guard is what licenses handing the agent that material; on this path there is no such clearance.

So the pass is reduced to what needs no judgement at all: a clean host-side merge, committed and
pushed, or nothing. `sync_only` forces `agent=false` unconditionally — not as a consequence of the
blocker count happening to be zero — and a merge that conflicts is *abandoned* (`git merge --abort`,
`merge_state=blocked`, nothing pushed) rather than left as markers for an agent to resolve. The
abort matters twice: it keeps the agent out, and it means no later step can mistake a half-merged
tree for something pushable.

**What this does and does not fix.** It does not rescue a PR that already conflicts with its base;
that still takes a human merge, as #54 and #56 got. Automating it would mean an unreviewed,
unvalidated tree driving a credentialed agent, which is a worse trade than a human doing one merge.
What it does fix is the strictly larger non-conflicting case: any behind-the-base PR whose panel was
guarded off — validation red, the branch moved mid-run, an untrusted author — now gets its sync,
becomes mergeable, earns its `pull_request` event, gets validated, and is reachable by its own panel
like every other D-fixer-merges-not-rebases sync. A conflicted PR at least now fails visibly, with `merge_state=blocked`
and the conflicting paths in the run log, instead of silently doing nothing.

**The sync-only successor is the one SHA the fixer does not claim.** The second thing review caught
was a contradiction between this decision and D-fixer-merges-not-rebases. Normally the fixer claims `review/pipeline` on
the SHA it pushes so that dedup swallows the `synchronize` event and no second panel re-reads the
fix — licensed by "the pre-fix panel plus the fixer's own gates *are* the review". A sync-only pass
has no pre-fix panel to point at; it runs because every guard refused and nothing was read. Claiming
there would have produced a successor with no verdict, no event left to earn one, and therefore no
route through the merge gate: the deadlock this path exists to break, moved one commit forward. So
`sync_only` suppresses the claim. Every other push still makes it, and the rule is unchanged — the
exception falls out of the same justification.

What this buys is worth stating exactly, since the first draft of this decision overstated it twice.
The successor is a merge-from-base, so where a prior verdict exists in its chain the inherit
short-circuit may re-stamp it rather than open a panel; that is fail-closed (a stale NO-GO, never a
stale GO) and `/review` overrides it. The guarantee is therefore narrower than "it will be
reviewed": the successor is mergeable, validated, and *reachable* by a panel, where before it was
none of the three.

**Why the sync-only path drops `fix_allowed` but keeps `cap_exhausted`.** `fix_allowed` bars a
blocker-fix on the last permitted cycle because that fix would never be reviewed (its cycle is
capped). A sync addresses no blockers, and its pushed SHA becomes reviewable the moment it is mergeable,
so the reason does not apply — and the stuck PRs (#54, #56) were at a cycle where `fix_allowed` was
already false, so honouring it would have left the deadlock intact. `cap_exhausted` is still
honoured on both paths: a genuinely exhausted PR takes the terminal cap-exhausted NO-GO and waits
for a human, rather than being kept alive indefinitely by resyncs.

**Why this does not weaken the loop bound.** `MAX_CYCLES` bounds the *agent fix loop*
(review → fix → push → review). The sync-only path writes no `review/cycle` (that is
`record-cycle`'s job, and it was skipped), so it consumes no cycle — consistent with "a run that
reviewed nothing does not consume a cycle". It cannot advance the fix loop because it runs no agent
and addresses no blockers, and it cannot run away: once merged, the SHA contains the base,
`needs_sync` reads false, and no further sync fires until the base moves again — one sync per base
movement, which is external and legitimate.

An earlier draft of this decision claimed here that "a fixer-authored commit is never inherited", so
the merged SHA would earn its own panel rather than re-stamp a stale verdict. That rule no longer
exists: the inherit short-circuit in `review-pipeline.yml` is purely topological — a merge whose
second parent is already on the base branch, with a prior verdict to copy — and checks no author.
The sentence also contradicted this decision's own residual two paragraphs down, which says the
opposite and is the accurate one. It is removed rather than repaired, because nothing in this
decision needs it: the successor's protection is that it arrives *unclaimed and unstamped*, which is
what makes a panel reachable at all. Whether one opens automatically is the inherit rule's business,
and `/review` is the documented override when it does not.

That property is not free, and review caught it going unbacked. `review-finalize.yml` stamps
`review/cycle` and `review/verdict` on `post_fix_sha` — sound everywhere else, because the fixer's
claim guarantees no other run will ever write them for that SHA. Suppressing the claim breaks that
guarantee, so a sync-only successor would have arrived at its own panel already stamped with this
run's cycle, consuming the cycle this decision says it does not and — at cycle 2 — reaching that
panel already capped. So a sync-only push is not passed as `post_fix_sha` at all: the statuses stay
on the pre-sync SHA, which the mergeable successor supersedes, and the successor is left clean for
the run that will actually read it. The three pieces only work together — suppress the claim, skip
the stamp, leave the cycle unwritten — and each one alone would have reintroduced the deadlock in a
different place.

**Invariants.** No blocker-fixing code lands unreviewed: the sync-only path pushes a clean merge and
nothing else — no blocker fix rides it, so there is no unreviewed *fix* to land. The merge itself is
reviewable, on the same "reachable, not guaranteed" terms as any D-fixer-merges-not-rebases sync (above); what makes it safe
is that its content is the base branch, already reviewed on its way to main, plus a PR-side delta of
zero. Author
exclusion, the blind orchestrator, fail-closed lenses, severity floors, controller termination, and
the untrusted-text boundary all live in the Python review core and the convergence controller, none
of which this touches — this is CI gating in `review-pipeline.yml` and `review-fixer.yml`. The
untrusted-text boundary is in fact *tightened*: one path that could have fed unvetted conflict
contents to a generator no longer exists. The judge still fails closed on the sync-only cycle's
empty reviewer set (pre-existing behaviour when guards refuse), publishing a NO-GO on the pre-sync
SHA that the mergeable successor supersedes.
