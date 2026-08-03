# CI and the agentic review pipeline

What runs, when, and which properties are load-bearing. For the one-time setup, see
[ci-setup.md](./ci-setup.md).

The design owes a lot to the pipeline in `hide-my-list`, and in particular to its
`docs/agentic-pipeline-learnings.md`, which is the rationale document behind most of the
non-obvious choices repeated here. This file states *this* repository's contracts rather
than reproducing that archaeology.

## Workflows

| workflow | trigger | runner | what it does |
|---|---|---|---|
| `pr-validation.yml` | every PR | `ubuntu-latest` | ruff, offline pytest on 3.11 + 3.12, lockfile check, strict docs build, actionlint, judge unit tests, decision-number collision check, docker build + smoke test |
| `docker-release.yml` | push to `main`, `v*` tags | `ubuntu-latest` | multi-arch build and push to GHCR, then pull back **by digest** and smoke test |
| `pages.yml` | push to `main` touching `docs/**`, `mkdocs.yml`, `pyproject.toml`, `uv.lock`, or `pages.yml`; manual | `ubuntu-latest` | strict MkDocs build, then deploy `docs/` to GitHub Pages |
| `ci-image.yml` | changes to `.github/ci/**`, manual | `ubuntu-latest` | builds the agent image and verifies every tool inside it runs |
| `resolve-issue.yml` | issue opened/reopened/unlabeled, `/autoresolve` comment | `[self-hosted, homelab]` | an agent implements the issue and opens a PR |
| `review-entry.yml` → `review-pipeline.yml` | PR events, `/review` | mixed | authorize → gather → reviewers → judge → finalize |

## PR validation is secret-free, on purpose

The test suite is entirely offline — a scriptable fake proxy drives the whole graph, so
no network and no API key are required. That is why validation runs on ephemeral
GitHub-hosted runners with read-only permissions and no secrets: nothing in that workflow
*can* leak a credential, because none is present.

Preserving that property is a reviewer's explicit job. A test that needs the real proxy
must carry the `live` marker, and CI always passes `-m "not live"`.

## Decision identifiers are slugs, and duplicates are refused at the gate

Each decision in [decisions.md](./decisions.md) is identified by a slug derived from its
subject (`## D-source-verification`), coined by the authoring PR rather than allocated from a
shared counter (D-decision-slugs supersedes D-decision-gate). Because a slug comes from the
decision's own content, two concurrently-open PRs cannot pick the same identifier, so the
merge-time collision the old numbering produced — two PRs racing for the same next-free number,
each rename costing a repo-wide edit and a fresh review cycle — cannot arise.
`scripts/validate-decision-numbers.sh` still runs at the gate, now guarding the one thing slugs
do not prevent: the *same* slug defined twice. It reads **both** definition forms — the
`## D-<slug> —` prose sections and the `| D-<slug> | … |` top-table rows — so a duplicate
hiding in the table form (as four reused numbers once did) is caught, which the predecessor
gate could not do. It is pure and offline — one file, no git, no network — so it fits the
secret-free gate and is unit-tested by `tests/test_decision_numbers.py`, while
`tests/test_citation_resolution.py` additionally fails any PR that cites a decision slug the
registry does not define. The `tests` job skips docs-only PRs, so the collision check runs as
its own path-filtered job to cover a PR that touches nothing but `decisions.md`.

## The review graph

```
review-entry            authorize · fork-reject · resolve SHA · prior-GO check · dedup claim
  └─ review-pipeline    gather (cycle, inherit, cap, classify)
       ├─ invariant     Claude · claude-opus-5   ─┐
       ├─ docs          Codex  · gpt-5.6-luna     │
       ├─ security      Codex  · gpt-5.6-sol      ├─ read-only, each emits a JSON artifact
       ├─ test          Claude · claude-sonnet-5  │
       ├─ quality       Codex  · gpt-5.6-sol     ─┘
       ├─ record-cycle  writes review/cycle — only if a reviewer actually ran
       ├─ fix           the ONLY branch-writing stage; syncs with the base branch and
       │                addresses blockers; skipped on the last cycle, but still runs a
       │                non-agentic, clean-merge-only sync pass when the panel was guarded
       │                off and the branch is behind the base (D-unguarded-sync)
       ├─ judge         deterministic, from main, contents: read
       └─ finalize      labels · summary comment · merge gate
```

Roles run on different model families deliberately. This project's own design argues that
decorrelated blind spots are what make independent review worth more than repeated
review; the same reasoning applies to its CI.

Each role also **names its model**, not merely its family (D-ci-model-pinning). `agent:`
selects a family; the checkpoint inside it used to be whatever the CLI happened to default to,
which meant a role's effective reviewer could change with no diff to review — the panel's
composition was not a property of this repository. The pins are sized to the *shape* of each
role's work: `invariant` is the never-abstain backstop on the six invariants and the merge gate,
so its tier is chosen for headroom; `docs` checks text against text, so it takes a lower one.
That sizing is a revisable bet, not a measured result — no benchmark was run, and this repo has
no per-role cost telemetry (D-ci-model-pinning). The family split those pins sit inside is what
[QP3](./quality-principles.md) protects, and it is unchanged — three Codex roles, two Claude,
with `quality` cross-family from `invariant`. Both the roles and the models are visible in one
place, `review-pipeline.yml`.

### Reviewer contract

A reviewer is strictly read-only. It produces a JSON artifact conforming to
[`reviewer-v1.json`](https://github.com/NickBorgers/reasonable-answer/blob/main/.github/scripts/review/schema/reviewer-v1.json) and a PR comment,
and has no path to the branch. **No stage in this pipeline can push.**

The judge consumes only those artifacts — never PR comments, never PR reviews. A reviewer
that wants a human's inline comment to block must ingest it via `gh api` and fold it into
`blocking_issues[]` with `source: "inline_comment"`.

Blocker ids must be stable across cycles for the same underlying problem, because the
judge namespaces them as `role/id`.

Adding a reviewer role means touching **two** schemas. `reviewer-v1.json`'s `role` enum
lets the role publish; `fix-result-v1.json`'s `id` pattern lets the fixer *name* that
role's blockers in `addressed[]`/`skipped[]`. The `docs` role shipped with only the first,
so for every PR since, a `docs` blocker was one the fixer could never claim to have
addressed — its artifact would have failed validation against main's schema if it tried.

**`blocking_issues[]` and `non_blocking_notes[]` must admit the same fields**
(D-reviewer-confidence-field). They describe the same findings at different confidence — every
prompt's 0.7 ladder is literally "same finding, other array" — and both set
`additionalProperties: false`. So a field only the blocker admits is one a reviewer will
eventually attach to a note, and that rejects the *whole* artifact: every finding that reviewer
had is lost, not just the offending one. The run then looks like the reviewer never produced
anything, and the judge fails closed on a reviewer that returned no valid artifact — correctly,
but with a diagnostic pointing nowhere near the cause.

This has cost a review cycle on `id`, `decision_ref` (#29), `category` (#35), and `confidence`
(#75, and then seven cycles across six PRs in one afternoon, #131). Each was fixed by admitting
the one field, which is why it kept returning: that closes an instance, not the class. Two things
close the class. First, the leak-prone set is not "fields a blocker has" but *every field named
anywhere in a reviewer's output contract or prompt* — `confidence` leaked because it is required
on `fix_suggestions[]` and cited in every prompt's ladder, not because blockers carry it. Second,
[`schema-parity.test.mjs`](https://github.com/NickBorgers/reasonable-answer/blob/main/.github/scripts/review/schema-parity.test.mjs)
asserts the two property sets stay equal and that fields required on `fix_suggestions[]` appear on
both, so admitting a field to one array fails PR validation until it is on both. A deliberately
one-sided field goes in that test's `ASYMMETRIC` map with its reason. Both arrays keep
`additionalProperties: false`, which the same test pins: parity is not a licence to accept
whatever a reviewer invents.

Note what this cannot protect against: a reviewer artifact is validated against **main's** copy of
the schema, so a PR that admits a new field does not help its own reviewers. The fix has to be on
main before the field stops killing artifacts.

### The judge fails closed

[`aggregate.mjs`](https://github.com/NickBorgers/reasonable-answer/blob/main/.github/scripts/review/aggregate.mjs) returns NO-GO rather than
guessing whenever it cannot trust its inputs: reviewer artifacts spanning multiple
`reviewed_sha` values or multiple cycles, a fix result that started from a different SHA,
an empty reviewer set, or every reviewer abstaining. It has unit tests, which
`pr-validation.yml` runs whenever `.github/scripts/review/**` changes.

The judge fails closed on its own inputs too, not just the aggregator's. When every
reviewer was skipped — each reviewer's Guard concluded `ok=false`, e.g. because PR
Validation failed on the reviewed SHA — no reviewer artifact is uploaded, and
`download-artifact` leaves the `reviewer-artifacts` directory wholly absent rather than
empty. `judge.mjs` treats that as a `pipeline_error` NO-GO (`pipeline could not trust its
inputs: no reviewer artifacts (reviews skipped?)`) instead of letting `readdirSync` die
with a raw `ENOENT`. The distinction matters operationally: a crash publishes no verdict,
so the merge gate stays un-green with nothing to say why and the cycle burns silently,
whereas a NO-GO verdict is recorded on the SHA and the finalize comment can explain it.

`judge.mjs` reads the fixer's artifact when one exists. When it does not — no blockers to
fix, the cycle cap forbade fixing, or the fixer failed — it synthesizes the no-op fix
result rather than relaxing the aggregator, so the epoch checks stay live and every
blocker reads as unaddressed. That is the fail-closed direction. A fix result that is
present but unparseable *does* fail the job: a broken fixer must not be indistinguishable
from an idle one.

What the verdict covers is worth being precise about. The reviewers read the **pre-fix**
tree and so does the verdict; `addressed[]` only records which of their blockers the fixer
claims to have closed. Nothing in the judge inspects the fixer's diff — so the fixer cannot
clear its own work on the strength of this file, but it does not need to: by design (D-fixer-merges-not-rebases),
the fixed SHA is not reviewed again. The fixer claims its own post-push SHA before a second
pipeline can start, and the merge gate is written on that SHA from this verdict plus the
fixer's own gates (schema, lint, marker gate, remote-head check), not from a fresh reading
of the post-fix tree.

`addressed[]` is credited **only when the fix was actually pushed** — `new_sha` differs
from `input_sha`, which the host sets only after a successful push. The fixer uploads its
artifact under `if: always()`, so a run that recorded its work and then died at a later
gate (lint, the remote-head check) leaves behind a truthful-looking record of changes that
are not in the tree. PR #49 reported one of three blockers unaddressed when in fact none of
the three had landed. The finalize comment now says so explicitly, because "the fixer
claimed fixes but pushed nothing" and "the fixer did nothing" send an operator to two
different places.

A reviewer only publishes its artifact under the name the judge consumes **if it
validated**, and the judge separately requires every role the classifier selected to be
present. Both halves are needed. Without the first, an artifact that had just failed
schema validation was still aggregated into the verdict. Without the second, a reviewer
that fails publishes nothing, silently leaves the review set, and the survivors clear the
merge on its behalf — so a crashed reviewer would *reduce* scrutiny instead of blocking.
That combination produced a real GO on a run where a reviewer had failed; a failed
reviewer is now a fail-closed `pipeline_error`.

Two further properties make the judge trustworthy, and both are structural rather than
conventional: it checks out **`main`**, so a PR cannot modify the code that judges it, and
it holds `contents: read`, so it could not push if it tried.

### Cycle control

- **GO is terminal.** A commit already carrying a successful merge gate is not
  re-reviewed. Re-reviewing identical content can only cost tokens and risk a different
  verdict.
- **NO-GO is not.** A push that tries to address the blockers gets reviewed again.
- **`/review` always forces a fresh review run.** It is the human override, and it now
  outranks every "should this cycle run at all" short-circuit: the prior-GO check is
  `pull_request`-only, and `force_review` suppresses the merge-from-base inherit path. That
  second one mattered — a PR whose head was a merge from the base could not be re-reviewed
  by any gesture, because the override was answered by skipping the panel and re-publishing
  the prior verdict. It still goes through the SHA-keyed dedup claim like every other
  trigger, so it cannot start while a pending `review/pipeline` claim is held on that SHA.
  It does not *bypass* the counter, but on a human-authored head it does effectively reset
  it, since the reset below keys on the author of HEAD rather than on what triggered the
  run. On a head the fixer authored, `/review` advances the count as usual.
- **A human commit resets the counter to 1.** `MAX_CYCLES` bounds the *agent* loop —
  review → fix → push → review — so only agent-authored commits are billed against it.
  A human push means someone read the blockers and answered them: that is a new
  conversation and it gets a full budget, including its own fixer attempt. Machine commits
  are identified by the author email `ci@reasonable-answer.local` and never reset.
  This does not weaken the bound: the fixer authors as that email, so the automated loop
  cannot refresh its own budget, and a human has to intervene for the counter to move
  back — that intervention *is* the bound. It used to reset only on top of a GO, which
  billed humans for exactly the iteration the blockers had asked them to do; PR #49 walked
  to `cycle_capped` in three human pushes, one of which was repairing the CI failure that
  started the burn, and force-pushing was the only way out.
- **A push that is *only* a merge of the base branch inherits the previous verdict**
  instead of burning a cycle. Without this, routinely resyncing a long-lived branch can
  push a PR into the cap without a single substantive change. It re-stamps that verdict
  without reading anything, which is why `/review` overrides it: inheriting a NO-GO is the
  right answer to an automatic resync and the wrong answer to a person asking to be
  re-reviewed.

  **"Only" is measured across the whole push, and against the tree (D-inherit-whole-range).**
  Two tests must both pass. Every commit reachable from the head but from neither the last
  reviewed SHA nor the base branch has to be a merge whose every merged-in parent is
  already on the base branch; and re-creating that merge — `git merge-tree --write-tree` of
  the last reviewed SHA with the base commit the head merged — has to reproduce the head's
  tree exactly. Either test failing means the panel reads the push. This replaced a test on
  the *shape of the head commit alone*, which was silent about everything committed
  underneath it: content, then `git merge origin/main`, inherited the prior verdict with no
  reviewer reading a line. The tree test is what a hand-resolved conflict cannot pass, and
  it is deliberately fail-closed — a merge that cannot be re-created cleanly is reviewed.
  One conflict shape is an exception: `docs/decisions.md`'s append-only collision resolves
  deterministically through a trusted merge driver run from `main`, not a judgement call, so
  a merge that shape resolved does re-create cleanly and can still inherit — see "Syncing
  with the base branch" below and D-decisions-merge-driver.
- **A fixer-authored commit has no per-author exemption from the merge-from-base rule.** A clean,
  tree-identical base resync is inherited; a conflict-resolved merge is reviewed under
  D-inherit-whole-range. A prior version of this rule (PR #65, responding to PR #49) refused
  to inherit onto a commit authored as `ci@reasonable-answer.local`, on the theory that "the
  fixed SHA earns its own cycle" was a property worth enforcing here. That was an agent's
  invention, not the owner's intent: the owner has since confirmed fixer output is meant to
  reach main without a further review cycle, matching the design this repository borrows
  from, and the per-author check has been removed. See D-fixer-merges-not-rebases's residual:
  wrong-but-clean fixer output can still reach main unread on the fixer's normal path (below),
  where no successor inherit decision runs.
- **A run that reviewed nothing does not consume a cycle.** `review/cycle` is written by
  `record-cycle`, after the panel has read the code, and only when at least one reviewer's
  guard cleared. Every guard refusing — PR Validation red on the reviewed SHA, the branch
  moved on mid-run, an untrusted author — means no code was read, and the next push starts
  from the same cycle number. This is fail-open on the counter and safe because the only
  way `fix` can push on an unrecorded cycle is the **sync-only** path (D-unguarded-sync), which
  addresses no blockers and writes no `review/cycle` of its own: it commits a clean merge of
  the base and nothing else, so it cannot advance the review → fix → push → review loop the
  cap bounds. Every *blocker-fixing* run still needs `record-cycle` to have succeeded. PR
  #49 is the case that forced the deferred write: its first run recorded cycle 1 while all
  four guards refused, and the push that repaired validation arrived as cycle 2, where
  `fix_allowed` is already false — spending the PR's one automated fix attempt on a run
  that read nothing.
- **`MAX_CYCLES: 2`** is now mostly a race-window backstop, not the review → fix → push →
  review loop breaker it once was. The fixer claims its own post-push SHA (see "The fixer
  claims its own SHA" below), so the `synchronize` event that push fires is normally
  suppressed and no second pipeline reviews the fix at all — the owner's intent is that
  the fix reaches main on the strength of the pre-fix panel plus the fixer's own gates,
  without a fresh cycle (D-fixer-merges-not-rebases). A genuine cycle 2 now only happens if that claim loses its
  race against GitHub scheduling the event; `fix_allowed` is false by cycle 2, so that
  accidental re-review cannot also re-fix, and a third cycle is capped and finalizes
  NO-GO. Day to day, this cap bounds repeated NO-GO iterations on a PR a human keeps
  pushing to, since every human commit resets the counter (above).

### Issue resolution, and the retry gesture

Filing an issue starts an agent. There are four entry points: `issues` `opened`,
`reopened`, and `unlabeled`, plus an `/autoresolve` comment on an existing issue. All four
are gated on the issue author being OWNER/MEMBER/COLLABORATOR; on `issues` events the
actor who filed, reopened, or unlabeled is checked too, because label edits are open to
anyone with triage rights and those need not be a collaborator.

**The `<agent>-started` label is never removed by the workflow.** It means "an agent has
attempted this", and a human removing it is how you ask for another attempt.

That asymmetry is load-bearing rather than tidy. Every label write uses `WORKFLOW_PAT`,
and PAT-driven events *do* trigger workflows — so a workflow that cleared its own label
would fire its own `unlabeled` trigger on the way out and retry itself forever. The
existing-PR check does not bound that: it only skips once a PR exists, which is exactly
not the case when the agent failed, which is exactly when a retry fires. Adding a label
emits `labeled`, which is deliberately absent from the trigger list, so marking an issue
started is safe.

An `agent:claude` / `agent:codex` label is a persistent per-issue override; an explicit
choice in an `/autoresolve` comment outranks it. With neither, the agent is
`vars.CI_AGENT_DEFAULT`, which defaults to **codex** (D-ci-model-pinning): resolving an issue end
to end is the pipeline's longest-running agent task by its configured budget (60 minutes against
the reviewers' 30), and it is a stage where family diversity is not at stake, since all five
reviewer roles read the result afterwards whatever wrote it.

Because that agent is chosen at runtime, the resolver and the fixer cannot carry a fixed
`model:` the way a reviewer role does. Each resolves it through an `agent_model()` shell
function written inline in its own workflow.

That duplication is deliberate, and the reason is worth stating because it is easy to
rediscover the hard way. The reviewer and fixer jobs run the pipeline's own logic from **main's
checkout**, so that a PR cannot edit the pipeline that reviews it. The corollary is that a
`scripts/` helper *added by a PR does not exist for that PR's own review* — the step dies at
exit 127, in a job nobody was reading, with no reviewer having found anything wrong. Workflow
YAML ships with the PR; a new file in the tree does not. So the map lives in the YAML, and
`tests/test_ci_model_pins.py` asserts the two copies stay identical — the anti-drift job a
shared file would have done, moved to a place the pipeline can actually reach.

### The fixer

`review-fixer.yml` is the only stage that may write to the PR branch. It runs **after the
reviewers and before the judge**, which is load-bearing: the judge grades the SHA the
reviewers actually read — the pre-fix tree — so the fixer cannot clear its own work unread.
The fixed SHA itself is **not** reviewed again in the normal case: the fixer claims its own
post-push SHA so no second panel runs, and the fix reaches main on the strength of that
pre-fix verdict plus the fixer's own gates (see "The fixer claims its own SHA" below, and
D-fixer-merges-not-rebases in `docs/decisions.md`).

It does two jobs: it syncs the branch with the base, and it addresses reviewer blockers.
Either one alone is enough to make it run.

#### Syncing with the base branch

Almost every PR here is agent-authored, so when the base moves there is no human in the
loop to resync. Before the agent is invoked, the host attempts `git merge --no-commit
--no-ff origin/<base>` in the PR workspace, with three outcomes:

| state | what happens |
|---|---|
| `none` | the branch already contains the base tip; nothing to do |
| `clean` | merged without conflict, committed by the host, pushed with whatever else this cycle produces |
| `conflicts` | markers are left in the working tree and the conflicted paths written to a file; the agent resolves them as ordinary file edits |

The `.git` contract is unchanged: the agent edits files and never runs a git write. The
host seals the merge. A clean merge needs no agent at all — paying for a self-hosted runner
and a model round-trip to deliver a merge the host already made would be waste.

**The marker gate.** Before committing a merge the host checks two things, because they
fail independently: no unmerged index entries (`git ls-files -u`), and no conflict markers
in the staged content (`git diff --check --cached`). A file staged with its markers intact
has no unmerged entry and no resolution — the first check alone would pass it. If either
finds something, the merge is aborted, the PR is labelled `needs-human-review` with a
comment naming the unresolved paths, and the job fails. That is the intended landing place
for a conflict the agent should not be guessing at: the prompts tell it to leave a marker
it cannot reconcile honestly rather than invent a resolution nobody can check.

A **clean** merge commit lands on the merge-from-base inherit path like any other, so it
does not burn a cycle. A merge the agent had to **resolve** no longer does
(D-inherit-whole-range): its tree is not the tree re-creating the merge produces, so the
inherit test refuses it and the panel reads it. That is a deliberate narrowing of what this
paragraph used to promise — a resolution is a judgement nobody has checked, and it is the
one place arbitrary content can sit inside a correctly-shaped merge.

**One conflict shape resolves deterministically enough to still inherit
(D-decisions-merge-driver).** `docs/decisions.md`'s append-only collision — two PRs each
adding a decision immediately before the same tail marker, nothing else touched — is common
enough to carry its own merge driver (`scripts/merge_decisions.py`), registered from the
trusted `main` checkout in both this sync step and the inherit step's own recreation, never
from the PR under review. Because both sides run the identical trusted, deterministic
function on the same inputs, a merge that shape resolves still recreates tree-identical and
still inherits. A `docs/decisions.md` conflict of any other shape — an edited existing
section, an edited Open-items list, a genuine same-slug collision — still falls through to a
hand resolution nobody has checked, and is reviewed exactly as before; every other file's
conflicts are unaffected.

The driver is registered by `scripts/register_decisions_driver.sh`, which smoke-tests it
first and registers nothing if that fails. This is not belt-and-braces: a merge driver whose
command cannot start does **not** degrade to the plain-git behaviour. Git marks the path
conflicted but leaves "ours" in the worktree with no conflict markers at all, and the commit
step's `git add -A` then stages that as resolved — the marker gate below sees nothing to
object to and the pipeline pushes a merge that silently dropped every base-side change to a
normative spec file. An unregistered driver is the real no-driver baseline; a broken
registered one is a hole in the marker gate. The sync step additionally refuses outright if
a path routed to the driver comes back conflicted with no markers in it.

It costs a cycle only on a PR that actually conflicted with its base in a shape nothing
resolves deterministically. The conflicted-path list travels in a file
rather than an environment variable: paths are contributor-controlled, and the agent's
environment is assembled from an `--env-file`, where a newline in a path would inject
arbitrary variables.

**The sync still runs when the panel was guarded off (D-unguarded-sync).** Every reviewer guard refusing
normally means the fixer does not run either, because `fix` was gated on `record-cycle`
having written a cycle. That left the D-fixer-merges-not-rebases sync unreachable on any PR whose guards refuse for
a reason a sync would repair — most sharply a PR whose `PR Validation Required` check is
absent or red while the base has moved underneath it. So `fix` has a second way in: when
`record-cycle` was skipped *and* `gather` reports the reviewed SHA is behind the base, the
fixer runs a **sync-only** pass. It does not require `fix_allowed` — that bars a blocker-fix
on the last cycle because the fix would go unreviewed, but a sync addresses no blockers and
its pushed SHA becomes reviewable once mergeable. It still honours `cap_exhausted`, so a genuinely
exhausted PR reaches the terminal cap-exhausted verdict and waits for a human rather than
being kept alive by an endless resync.

**The sync-only pass is non-agentic, and that bounds what it can fix.** It is entered
precisely when no reviewer has read the SHA and PR Validation may be absent or red, so the
tree, the PR body, and any conflict contents are contributor-controlled input that nothing
has vetted. The fixer agent runs on the self-hosted runner with host networking and pipeline
credentials, so the pass is passed `sync_only: true` and reduced to what needs no judgement:
a clean host-side merge, committed and pushed, or nothing. A merge that *conflicts* is
abandoned (`merge_state=blocked`), the tree returns to the reviewed SHA, and the PR waits for
a human — an agent may not resolve conflicts on code no reviewer cleared.

The consequence is worth stating plainly: this does **not** rescue a PR that already
conflicts with its base. Such a PR has no computable merge ref, so GitHub fires no
`pull_request` event, PR Validation never runs, every guard refuses, and the sync-only pass
it reaches then hits conflicts and blocks. Clearing a true conflict still takes a human
merging the base in by hand (as #54 and #56 did). What D-unguarded-sync fixes is the strictly larger,
non-conflicting case: a behind-the-base PR whose panel was guarded off for any reason gets
its sync, becomes mergeable, earns its `pull_request` event, gets validated, and becomes
reachable by a panel — see the residual below for why that is "reachable" and not "reviewed".

**And the sync-only successor is the one SHA the fixer does not claim.** Normally the fixer
claims `review/pipeline` on the SHA it pushes, so dedup suppresses the `synchronize` event and
no second panel re-reads the fix (D-fixer-merges-not-rebases) — licensed by "the pre-fix panel plus the fixer's own
gates *are* the review". A sync-only pass has no pre-fix panel to point at: it runs because
every guard refused and nothing was read. Claiming there would leave a successor SHA with no
verdict, no way to earn one, and therefore no way through the merge gate — the original
deadlock moved one commit forward. So `sync_only` suppresses the claim, and the merged SHA
gets the panel the pass itself could not run.

**And it is not passed to finalize as `post_fix_sha`.** That input is where `review/cycle`,
`review/verdict`, and the merge gate land, and stamping them on the pushed SHA is only safe
because the fixer's claim means no other run will ever write them there. Suppressing the claim
removes that guarantee, so a sync-only successor would reach its own panel pre-stamped with a
cycle it never spent — and at cycle 2 would arrive already capped, before the panel it was
synced to receive. So the sync-only path passes the *pre-sync* SHA instead: the statuses stay
where the review actually happened, and the successor stays clean for the run that will read
it. Claim, stamp, and cycle have to move together; fixing any one alone puts the deadlock back
somewhere else.

One residual is worth naming rather than papering over: the successor is a clean
merge-from-base and nothing else — the pass abandons on conflict — so it is exactly the
shape the inherit short-circuit is built to pass, and if a prior verdict exists on the
pre-sync SHA it is re-stamped instead of opening a panel. What D-inherit-whole-range changed
is the blast radius, not this case: the successor's tree really is the merge of a reviewed
tree with the base, and nothing else is riding along under it. That is also the fail-closed
direction — a stale NO-GO, never a stale GO — and `/review` overrides it, which is what that
gesture is for. The guarantee this path actually makes is narrower than "it will be
reviewed": the successor is mergeable, validated, and *reachable* by a panel, where before it
was none of the three.

It runs in one of two modes.

**`cold`** — the fallback, and still the one to optimise for. `author-resume` can only fire
on a PR that `resolve-issue.yml` opened. Now that filing an issue starts an agent, those
are no longer rare — but any PR opened by hand, or by a coding agent on a laptop, carries
no session and lands here. The cold path pins one agent — **codex** — regardless of who
authored the PR: with no session to resume, the author's identity buys nothing, and a fixed
choice keeps the path predictable (D-ci-model-pinning). A cold fixer exercises **grounded judgment** (D-fixer-grounded-judgment): it may apply
any fix it can anchor in the repository's existing content and structure, the PR's
reconstructed intent, and the reviewer's own finding — including work that spans files no
reviewer named, such as writing a missing test by mirroring the tests beside it or adopting
a deployment pattern the docs already prescribe. What it may not do is invent: a fix that
requires a design decision the repository has not already made is skipped with a reason,
as is anything the context record shows to be deliberate.

**`author-resume`** — the agent that wrote the PR is resumed with its conversation intact.
It answers reviewers with the reasoning that produced the code, and may push back on a
finding by clarifying the PR body instead of changing code. A cold fixer may **not** claim
`body_clarification`; the validator rejects it, because "the reviewer misread my intent" is
not a claim an agent without that intent can make.

`author-resume` is best-effort, and a resumed session that wedges must fall through to the
cold fixer rather than fail the PR (D-resume-timeout). That containment lives in `run-in-container.sh`,
not in a `continue-on-error` on the composite step — which does not reliably stop an inner
composite-step failure from aborting the job. On a resume the script captures the `timeout`
exit code (adding `--kill-after` so a CLI that ignores SIGTERM is still killed at the
deadline): every contained outcome — timeout, crash, or a clean exit with no artifact —
writes a `fixer-incomplete.sentinel` naming the reason, and exits 0. The workflow falls back
whenever that sentinel is present (or, belt-and-braces, the result is missing). The sentinel
is written for a crash rather than the crash being inferred from a missing result, because
an agent can write `fixer-result.json` and *then* exit nonzero: inferring would read that as
a fix and push work the agent never vouched for. The condition carries `!cancelled()` so an
implicit `success()` is not ANDed onto it — which would skip the fallback on the very hang it
exists to catch — while still stopping a cancelled run from entering a step that resets the
tree and replays the base merge. In cold mode there is no fallback, so a timeout stays fatal.

Containment made a hung resume survivable; two further mechanisms make it cheap
(D-resume-stall-guard), because the wasted 25 minutes were the pipeline's largest single
latency — and they were spent *before* the cold fixer that does the work even started.

**The stall guard.** Every hang observed to date emitted zero stream-json events: the failure
is silence, not slowness. So `run-in-container.sh` watches the output log beside a resumed
agent and kills it when the log stops growing — measuring idleness rather than shortening the
total budget, because a resume that is genuinely working needs as much time as a cold fixer.
Two thresholds: **3 minutes** before the first byte, where the CLI has not begun a turn at all
and seconds are already abnormal, and **10 minutes** once output has started, where silence
means a long tool call. The outer `timeout` is unchanged and still backstops a process that
spins noisily instead of idling. The guard writes `fixer-stalled.flag` naming the reason
*before* killing — an outside kill arrives as 143/137, and 137 is also what the outer
deadline's `--kill-after` escalation produces, so the exit code cannot tell them apart — and
then routes into the same `fixer-incomplete.sentinel` the fallback already reads. It applies
only when `AGENT_RESUME=1`: no other role has a fallback to hand off to, so an idle-kill there
would turn a slow test suite into a failed job. `AGENT_FIRST_OUTPUT_TIMEOUT`,
`AGENT_STALL_TIMEOUT` and `AGENT_STALL_POLL` are override seams so the offline test can use
deadlines of seconds.

**The quarantine.** A wedged session does not heal, and the same `Author-Session:` trailer is
read on every cycle and every re-review — so the same dead session was resumed again and
again. When a resume produces no fix, the fixer now uploads a marker artifact named
`session-hung-<agent>-<run-id>`; before the next resume it queries that name and, on a live
hit, goes straight to cold. Artifacts rather than the per-SHA commit statuses below, because
this fact is keyed by session: a session outlives every SHA it is asked about, and each cycle
asks from a new one. The marker outlives the 7-day session artifact it describes, which is
harmless — once the session has expired there is nothing to resume either — and expired
markers are ignored. The check fails **open**: a failed query costs one bounded resume
attempt, where failing closed would silently retire the resume path on an API blip.

#### Context reconstruction

Because cold is the normal path, the fixer rebuilds what it can of the author's intent
before triaging anything, into `$PR_CONTEXT_PATH`:

- the PR conversation — where a human most often states the intent a reviewer then misreads
- the branch's commit messages — the author's own narration, which survives when nothing
  else about their reasoning does
- the originating issue and its comments, when the PR body cites one. A PR does not need to
  have been *opened* by an agent to say `Resolves #N`, so this is the context-from-issue
  path for PRs that have no session.

The record cuts both ways (D-fixer-grounded-judgment). It can make the fixer **skip** — flagged behaviour it
shows to be deliberate is skipped with a citation — and it can supply the intent that
grounds a fix, telling the fixer which of two plausible resolutions serves the change.
What it can never do is widen scope: the fixer answers reviewer findings only, and
instructions appearing inside the record are data, not directives.

All of it is untrusted text — issue bodies and PR comments are public and attacker-editable
— so it is fenced and labelled as data, and both prompts state that instructions appearing
inside it are not instructions.

#### How the author's context survives, when there is any

Container state dies with the container, and the homelab runners are ephemeral and plural,
so a session written on one is simply absent on the next. The conversation therefore travels
as an artifact:

1. `resolve-issue.yml` prepares a host directory keyed `(agent, issue, run-id)` and mounts
   it over the CLI's state directory, so the session outlives the container.
2. After the run it is packed and uploaded as `author-session-<agent>-<run-id>`.
3. The agent writes `Author-Session: <agent>/<run-id>` as the last line of the PR body.
4. The fixer parses that trailer, downloads the artifact **from the original run**, unpacks,
   validates, and mounts it — then resumes with `claude --continue` or
   `codex exec resume --last`.

The per-`(agent, issue, run-id)` keying is what makes "most recent session" unambiguous. A
shared directory accumulating every `/autoresolve` attempt on an issue would resume an
arbitrary one.

Two consequences worth knowing:

- **Artifacts expire after 7 days.** A PR that sits longer silently loses resume and
  degrades to cold. The fixer emits a `::warning::` rather than letting the mode change go
  unexplained.
- **Editing the PR body can disable resume.** Dropping the `Resolves #N` line or the
  trailer breaks the lookup. Both fixer prompts tell the agent to preserve them; a human
  editing the body should too.

#### Gates before anything is pushed

In order, and all of them fail closed:

1. The artifact validates against **main's** `fix-result-v1.json`.
2. `input_sha` equals the frozen reviewed SHA — otherwise the fixer worked from a tree
   nobody reviewed.
3. `mode` matches what the workflow determined, so a cold fixer cannot self-report as a
   resumed author to unlock `body_clarification`.
4. `ruff` passes on the whole tree, at the version pinned in **main's** `uv.lock` — the
   same version PR Validation enforces, and not one a PR can choose for its own gate. An
   inline pin here drifted to six minor versions behind (0.9.7 against the lockfile's
   0.15.22) before anyone noticed. Installing the linter and running it are separate steps
   so a registry or egress failure cannot report itself as "the tree does not lint" and
   spend the PR's one fix attempt on a network error. Tests are deliberately **not** run
   here: that would mean installing the PR's own `pyproject.toml`, executing PR-authored
   build config in the one job holding a write-capable PAT. Tests belong to PR Validation,
   which runs on a runner with no secrets. Reading a version string out of a lockfile
   executes nothing.
5. No conflict marker and no unmerged index entry survives, when a merge is in flight —
   see the marker gate above. This one aborts the merge and labels the PR rather than
   silently pushing a tree with `<<<<<<<` in it.
6. The remote branch head still equals the reviewed SHA. If a human pushed meanwhile, the
   fix is discarded rather than racing them.

Artifacts are validated with a JSON-schema validator **pinned by a committed lockfile**
(`.github/scripts/review/validator/`), never `npx --yes ajv-cli@5`. A floating range
resolves at runtime, and the fixer job holds a push-capable PAT — a new 5.x release or a
compromised registry account would execute package code there, before the gates that
decide whether anything gets pushed. The lockfile pins the transitive tree by integrity
hash. `review-reviewer.yml` uses the same pinned validator; it holds no push credential,
but it does run on a self-hosted runner on the tailnet.

Before ajv runs, both workflows normalize the artifact through
`.github/scripts/review/normalize-artifact.mjs`, which shortens any string exceeding a
`maxLength` **main's** schema declares. This exists because an invariant reviewer emitted a
510-character `summary` against a 500-character cap and lost a full cycle — three minutes of
agent time and a blocked merge gate — to a ten-character overshoot on a field that only ever
renders into a PR comment. The prompt already stated the cap. A model cannot count the
characters it is about to emit, so `maxLength` is a hard cliff on a quantity the producer
cannot measure, and instructions can reduce the overshoot rate but not eliminate it.

Length is the **only** tolerance. Normalization never adds a missing field, coerces a type,
or drops an unknown property, so every structural violation still reaches ajv and still fails
the run closed — a wrong SHA, an invalid decision, or a blocker with no message is a real
failure and stays one. Each truncation emits a `::warning::` naming the field and both
lengths, so a prompt that routinely overshoots is visible and gets fixed at the source.
Truncation cuts from the end, which is why the invariant prompt requires its `Alignment
check:` and `Scope check:` lines at the **start** of `summary`: the first artifact to hit this
path would otherwise have published without its scope verdict.

The **host** commits, never the agent — the container runs as uid 1000 against a `.git`
owned by the runner, and agent-side git writes corrupt the index in ways that surface two
jobs later. Both fixer prompts forbid touching `.git`; the host-side commit is the other
half of that contract. The commit is authored as `ci@reasonable-answer.local`, which is what
cycle control uses to tell machine pushes from human ones.

#### The fixer claims its own SHA

Like the design this borrows from, the fixer claims `review/pipeline` on the SHA it just
pushed — **after** pushing, not before. The commit-status API 422s on a SHA it has not
seen, and the new SHA does not exist on the remote until the push completes, so a
pre-push claim is structurally impossible; push then claim is the only ordering that
works. See the "Push new SHA, then claim it" step in `review-fixer.yml`.

That claim exists so the `synchronize` event the push fires finds the SHA already taken
and `review-dedup` refuses to start a second pipeline for it. The owner's intent is that
fixer output — a fix, a conflict resolution, or both — reaches main on the strength of
the pre-fix panel plus the fixer's own gates (schema validation, `ruff`, the marker gate,
the remote-head check), without a further cycle reading the post-fix tree. This repo
previously suppressed the claim on the theory that the fix itself needed its own review
cycle; that was an agent's invention, not a decision the owner had made, and it has been
reverted (see D-fixer-merges-not-rebases in `docs/decisions.md`).

The **sync-only** pass (D-unguarded-sync) is the single exception, and it does not weaken the rule — it
turns on it. The claim is licensed by "the pre-fix panel is the review", and a sync-only pass
runs precisely because there was no panel. Claiming there would produce a successor SHA with
no verdict and no event left to earn one. So `sync_only: true` suppresses the claim; every
other push still makes it.

Because the event is suppressed, nothing else will ever publish `review/cycle`,
`review/verdict`, or the merge gate for that SHA — `review-finalize.yml` does it in the
same run, taking `post_fix_sha` as an explicit input rather than defaulting to the pre-fix
`reviewed_sha`. Writing the merge gate on the wrong SHA would leave the PR's actual head
permanently ungated, which is exactly the failure this stamping exists to prevent.
`cleanup-claim` (bottom of `review-pipeline.yml`) advances `review/pipeline` to a terminal
state on both the reviewed SHA and the post-fix SHA for the same reason: a claim this run
makes and never revisits would otherwise leak `pending` forever and block every future
event for that SHA, including a human's `/review`.

The race the post-push claim leaves open — GitHub takes a few seconds to schedule the
`synchronize` event — is bounded by the cycle cap (`MAX_CYCLES`, above), not eliminated:
worst case is one wasted extra cycle, not a loop.

### Role selection

`invariant` always runs. `docs` runs on every non-empty diff: documentation drift can
originate on either side of the docs/code boundary, so there is no file class whose change
provably cannot stale a document. `security` runs unless the change is docs-only, and
always for anything under `.github/`, `src/`, or the dependency and container files.
`test` runs for `src/`, `tests/`, `config/`, and `pyproject.toml`. `quality` runs for
`src/`, `config/`, every `docs/*.md` (empirical claims live there), and the review
pipeline's own files (`.github/workflows/review-*`, `.github/scripts/review/`,
`.github/actions/review-*`) — the surfaces where the design could drift off its evidence
base ([quality-principles.md](./quality-principles.md), D-quality-reviewer). Unlike `invariant` and
`docs` it **may abstain**, because it is conditionally selected and those two remain the
never-abstain backstop; its prompt bounds abstention to diffs where no principle row has
surface.

**`invariant` and `docs` must never abstain**, and their prompts say so. Selecting it unconditionally
is what guarantees the judge never sees an empty or wholly-abstaining review set — and
the judge treats all-abstain as a fail-closed `pipeline_error`. So a role that is always
selected but permitted to abstain produces exactly the vacuous outcome the unconditional
selection exists to prevent: every infrastructure-only PR would NO-GO with an error about
the pipeline rather than the change. A diff with no invariant surface is an `approve` that
says why, which is a real finding.

**Spec-critical markdown is carved out of "docs-only".** `docs/DESIGN.md`,
`isolation.md`, `convergence.md`, `architecture.md`, `decisions.md`, and every prompt file
are normative — the docs *are* the spec, and the prompts *are* the reviewers' instructions.
That carve-out is an allowlist, so it is wrong by default for anything new: a new
spec-bearing or prompt-bearing document must be added to it.

## Permissions

`GITHUB_TOKEN` is declared read-only at `review-entry.yml`. This is not belt-and-braces:
GitHub validates that a reusable callee declares a **subset** of its caller's permissions,
and that rule is transitive. A read-only declaration at the entry point therefore *forces*
every downstream workflow read-only — no reachable job can obtain write access through
`GITHUB_TOKEN`, whatever it asks for.

Every actual write — comments, labels, commit statuses, the resolver's branch — uses
`secrets.WORKFLOW_PAT`.

### The merge gate

One deliberate exception:

> The `All Required Agent Reviews` commit status **must** be written with `GITHUB_TOKEN`,
> not `WORKFLOW_PAT`.

Branch protection only honours required-status contexts published by the GitHub Actions
app (integration_id 15368). A status posted by a personal access token is recorded and
displayed identically but does not satisfy the protection rule — so using the PAT here
leaves the gate permanently un-green with nothing anywhere to explain why.

It is also written on `post_fix_sha`, not `reviewed_sha` — see "The fixer claims its own
SHA" above. When the fixer pushed, the PR's actual head is the post-fix commit, and that
commit's own `synchronize` event was deliberately suppressed, so this is the only run that
will ever gate it.

## Container topology

Every knob lives in [`review-agent-run`](https://github.com/NickBorgers/reasonable-answer/blob/main/.github/actions/review-agent-run/action.yml):

- `--network host` — the only thing granting tailnet reachability to the proxy.
- `.review-output` is `chmod 777` before the run: the runner uid is not the container's
  uid 1000.
- `GIT_CONFIG_COUNT` / `safe.directory=/workspace` — the bind-mounted `.git` is owned by
  the runner uid, so without this every `git diff` inside the container fails on dubious
  ownership and the agent burns turns working around it.
- `sudo chown -R` after the run, under `if: always()` — otherwise uid-1000 files persist
  and the *next* job's checkout dies on `.git/index.lock`.
- Secrets arrive in a mode-600 `--env-file`, not on the command line where they would be
  visible in the process table. Only the active agent's credentials are forwarded.
- The runner script and the prompts are mounted from the `main` checkout, not the PR's,
  so a pull request cannot rewrite the instructions used to review it.

## Things that will bite

- `strategy.matrix` cannot be used on a job that `uses:` a reusable workflow, and
  `matrix.*` is unavailable in a reusable caller's `if:`. Hence static reviewer
  jobs, one per role. Getting this wrong produces a `startup_failure` with no logs.
- An `if: always()` aggregate job bypasses `needs`-based skipping, so it must repeat the
  fork check inline.
- `skipped` must count as a pass in `PR Validation Required`, or every docs-only PR fails.
- `review-dedup` refuses a claim only when the existing status is `pending`. Terminal
  states are re-claimable by design — do not "harden" this.
- `cleanup-claim` runs under `if: always()`. Without it a crashed reviewer leaves the SHA
  claimed forever and no future run, including `/review`, can proceed.
- A reviewer caller job reports `success` when its inner `review` job was *skipped* by the
  guard, so job `result` cannot answer "did anyone review this?". `review-reviewer.yml`
  exposes the guard's decision as a workflow output (`reviewed`) for that reason — read it,
  not `needs.<job>.result`.
- `hashFiles()` silently returns empty for paths outside the workspace, so gate steps on
  step outputs instead.
- `gh pr comment`, never `gh pr review --approve` — the latter fails whenever the PAT user
  authored the PR, which is the common case here.
- `$GITHUB_ENV` rejects comment and blank lines, so `versions.env` is filtered before it
  is loaded.
- `github.repository` preserves capitalisation and Docker rejects uppercase; the image
  name is lowercased everywhere.
- `mkdocs build --strict` fails on any relative link that leaves `docs/`, because the site
  is built from that directory alone. The links to the README, to the icons README under
  `src/`, and to files under `.github/` are absolute `https://github.com/...` URLs for
  exactly that reason. Making one relative again turns `Docs Build` red, and the diff does
  not explain why.
- Mermaid renders in the browser, so the strict build **cannot** fail on a broken diagram —
  it sees an opaque code block. A diagram change is verified by eye with `make docs-serve`.
  Note the escaping asymmetry the blocks rely on: `&amp;` inside a flowchart label, a raw
  `&` inside a `sequenceDiagram` message. Both survive the site build byte-for-byte, so a
  block that renders on github.com renders on the site.
- The published site fetches Mermaid at runtime from `https://unpkg.com/mermaid@11/...`.
  That URL is baked into Material's own bundle, is **not** pinned below the major, and is
  not something the build vendors: diagrams therefore depend on a third-party CDN at read
  time, and a Mermaid release inside 11.x can change how they render without any change
  here. Nothing else on the site needs egress. Readers behind a filtering proxy see the
  prose and no diagrams — which is also why the diagrams carry no information that the
  surrounding text does not.
- Material injects each rendered diagram into a **closed** shadow root, so
  `document.querySelector(".mermaid svg")` finds nothing even when rendering succeeded.
  Anything automating a check of the diagrams has to measure the host `div`'s laid-out
  height instead, or it will report a false failure.
- A `classDef` with a hard-coded pale `fill:` goes unreadable in dark mode: the label
  colour follows the theme and the fill does not. `classDef ... color:` does not rescue it,
  because Material's injected theme CSS sets `.nodeLabel { color: … }` and wins. Style
  those nodes with `stroke:` alone and let the renderer pick the background.

## Deliberately not built

- **No two-lens security split.** Folding a confidence threshold and an exclusion list
  into one prompt gets most of the value without a second reviewer job, a merger module,
  and a vendored-prompt pin.
- **No sandboxed path for fork PRs.** They are refused outright rather than reviewed with
  reduced privileges. A fork's code never reaches the self-hosted runners.
