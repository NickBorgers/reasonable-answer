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
| `pr-validation.yml` | every PR | `ubuntu-latest` | ruff, offline pytest on 3.11 + 3.12, lockfile check, actionlint, judge unit tests, docker build + smoke test |
| `docker-release.yml` | push to `main`, `v*` tags | `ubuntu-latest` | multi-arch build and push to GHCR, then pull back **by digest** and smoke test |
| `ci-image.yml` | changes to `.github/ci/**`, manual | `ubuntu-latest` | builds the agent image and verifies every tool inside it runs |
| `resolve-issue.yml` | `/autoresolve` comment | `[self-hosted, homelab]` | an agent implements the issue and opens a PR |
| `review-entry.yml` → `review-pipeline.yml` | PR events, `/review` | mixed | authorize → gather → reviewers → judge → finalize |

## PR validation is secret-free, on purpose

The test suite is entirely offline — a scriptable fake proxy drives the whole graph, so
no network and no API key are required. That is why validation runs on ephemeral
GitHub-hosted runners with read-only permissions and no secrets: nothing in that workflow
*can* leak a credential, because none is present.

Preserving that property is a reviewer's explicit job. A test that needs the real proxy
must carry the `live` marker, and CI always passes `-m "not live"`.

## The review graph

```
review-entry            authorize · fork-reject · resolve SHA · prior-GO check · dedup claim
  └─ review-pipeline    gather (cycle, inherit, cap, classify)
       ├─ invariant     Claude    ─┐
       ├─ security      Codex      ├─ read-only, each emits a JSON artifact
       ├─ test          Claude    ─┘
       ├─ fix           the ONLY branch-writing stage; skipped on the last cycle
       ├─ judge         deterministic, from main, contents: read
       └─ finalize      labels · summary comment · merge gate
```

Roles run on different model families deliberately. This project's own design argues that
decorrelated blind spots are what make independent review worth more than repeated
review; the same reasoning applies to its CI.

### Reviewer contract

A reviewer is strictly read-only. It produces a JSON artifact conforming to
[`reviewer-v1.json`](../.github/scripts/review/schema/reviewer-v1.json) and a PR comment,
and has no path to the branch. **No stage in this pipeline can push.**

The judge consumes only those artifacts — never PR comments, never PR reviews. A reviewer
that wants a human's inline comment to block must ingest it via `gh api` and fold it into
`blocking_issues[]` with `source: "inline_comment"`.

Blocker ids must be stable across cycles for the same underlying problem, because the
judge namespaces them as `role/id`.

### The judge fails closed

[`aggregate.mjs`](../.github/scripts/review/aggregate.mjs) returns NO-GO rather than
guessing whenever it cannot trust its inputs: reviewer artifacts spanning multiple
`reviewed_sha` values or multiple cycles, a fix result that started from a different SHA,
an empty reviewer set, or every reviewer abstaining. It has unit tests, which
`pr-validation.yml` runs whenever `.github/scripts/review/**` changes.

There is no automated fixer: a NO-GO goes back to a human. `judge.mjs` therefore
synthesizes the no-op fix result that describes what actually happened, rather than
relaxing the aggregator — the epoch checks stay live, and adding a real fixer later is a
one-line change.

Two properties make the judge trustworthy, and both are structural rather than
conventional: it checks out **`main`**, so a PR cannot modify the code that judges it, and
it holds `contents: read`, so it could not push if it tried.

### Cycle control

- **GO is terminal.** A commit already carrying a successful merge gate is not
  re-reviewed. Re-reviewing identical content can only cost tokens and risk a different
  verdict.
- **NO-GO is not.** A push that tries to address the blockers gets reviewed again.
- **`/review` always forces a fresh cycle.** It is the human override.
- **A human commit on top of a GO resets the counter** — that is a new conversation, and
  it should get a full review budget. Machine commits are identified by the author email
  `ci@reasonable-answer.local`.
- **A merge of the base branch into the PR inherits the previous verdict** instead of
  burning a cycle. Without this, routinely resyncing a long-lived branch can push a PR
  into the cap without a single substantive change.
- **`MAX_CYCLES: 2`** is a real loop breaker now that the fixer exists. The loop it bounds
  is review → fix → push → review. At 2: cycle 1 reviews and may fix; cycle 2 reviews that
  fix and may not fix again; a third cycle is capped and finalizes NO-GO. So a PR gets at
  most one automated fix attempt, and that attempt is always reviewed by a fresh panel
  before it can merge. GO-is-terminal is what keeps the bound safe — a converged PR never
  re-enters the loop.

### The fixer

`review-fixer.yml` is the only stage that may write to the PR branch. It runs **after the
reviewers and before the judge**, which is load-bearing: the judge then grades the SHA the
reviewers actually read, and the fixed SHA earns its own cycle with its own reviewers.
Judging the post-fix tree would let the fixer clear its own work unread.

It runs in one of two modes.

**`cold`** — the common case, and the one to optimise for. `author-resume` can only fire on
a PR that `resolve-issue.yml` opened; a PR a human or a coding agent on a laptop opens
carries no session, and that is nearly all of them. A cold fixer applies only fixes passing
an explicit mechanical gate: the blocker must name a file and line, be fully determined by
its own description, stay inside reviewer-named files, and stay small. Everything else is
skipped with a reason.

**`author-resume`** — the agent that wrote the PR is resumed with its conversation intact.
It answers reviewers with the reasoning that produced the code, and may push back on a
finding by clarifying the PR body instead of changing code. A cold fixer may **not** claim
`body_clarification`; the validator rejects it, because "the reviewer misread my intent" is
not a claim an agent without that intent can make.

#### Context reconstruction

Because cold is the normal path, the fixer rebuilds what it can of the author's intent
before triaging anything, into `$PR_CONTEXT_PATH`:

- the PR conversation — where a human most often states the intent a reviewer then misreads
- the branch's commit messages — the author's own narration, which survives when nothing
  else about their reasoning does
- the originating issue and its comments, when the PR body cites one. A PR does not need to
  have been *opened* by an agent to say `Resolves #N`, so this is the context-from-issue
  path for PRs that have no session.

The record can only make the fixer **skip**, never make it apply. If it shows the flagged
behaviour was deliberate, the blocker is skipped with a citation. If it is silent, the
mechanical gate decides. Understanding why code exists does not establish that a change to
it is safe, and conflating those is how a fixer talks itself past its own gate.

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
4. `ruff` passes on the whole tree. Tests are deliberately **not** run here: that would
   mean installing the PR's own `pyproject.toml`, executing PR-authored build config in the
   one job holding a write-capable PAT. Tests belong to PR Validation, which runs on a
   runner with no secrets.
5. The remote branch head still equals the reviewed SHA. If a human pushed meanwhile, the
   fix is discarded rather than racing them.

The **host** commits, never the agent — the container runs as uid 1000 against a `.git`
owned by the runner, and agent-side git writes corrupt the index in ways that surface two
jobs later. Both fixer prompts forbid touching `.git`; the host-side commit is the other
half of that contract. The commit is authored as `ci@reasonable-answer.local`, which is what
cycle control uses to tell machine pushes from human ones.

Unlike the design this borrows from, the fixer does **not** claim `review/pipeline` on the
SHA it just pushed. That claim would suppress the `synchronize` event — but in this graph
that event *is* cycle 2, the one that reviews the fix. The contention it guards against does
not exist here: `fix` needs all three reviewers, so when it pushes, only `ubuntu-latest` jobs
remain and no self-hosted runner is held.

### Role selection

`invariant` always runs. `security` runs unless the change is docs-only, and always for
anything under `.github/`, `src/`, or the dependency and container files. `test` runs for
`src/`, `tests/`, `config/`, and `pyproject.toml`.

**`invariant` must never abstain**, and its prompt says so. Selecting it unconditionally
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

## Container topology

Every knob lives in [`review-agent-run`](../.github/actions/review-agent-run/action.yml):

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
  `matrix.*` is unavailable in a reusable caller's `if:`. Hence three static reviewer
  jobs. Getting this wrong produces a `startup_failure` with no logs.
- An `if: always()` aggregate job bypasses `needs`-based skipping, so it must repeat the
  fork check inline.
- `skipped` must count as a pass in `PR Validation Required`, or every docs-only PR fails.
- `review-dedup` refuses a claim only when the existing status is `pending`. Terminal
  states are re-claimable by design — do not "harden" this.
- `cleanup-claim` runs under `if: always()`. Without it a crashed reviewer leaves the SHA
  claimed forever and no future run, including `/review`, can proceed.
- `hashFiles()` silently returns empty for paths outside the workspace, so gate steps on
  step outputs instead.
- `gh pr comment`, never `gh pr review --approve` — the latter fails whenever the PAT user
  authored the PR, which is the common case here.
- `$GITHUB_ENV` rejects comment and blank lines, so `versions.env` is filtered before it
  is loaded.
- `github.repository` preserves capitalisation and Docker rejects uppercase; the image
  name is lowercased everywhere.

## Deliberately not built

- **No agent-driven merge-conflict resolution.** The fixer works on the reviewed SHA and
  never merges the base branch. If the branch has drifted, that is a human's call — an
  agent picking resolutions at conflict markers is exactly the kind of unreviewable change
  this pipeline exists to catch, not to generate.
- **No two-lens security split.** Folding a confidence threshold and an exclusion list
  into one prompt gets most of the value without a second reviewer job, a merger module,
  and a vendored-prompt pin.
- **No auto-trigger on `issues: opened`.** `/autoresolve` is opt-in, so filing a thought
  does not spend an agent run.
