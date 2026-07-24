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
| `resolve-issue.yml` | issue opened/reopened/unlabeled, `/autoresolve` comment | `[self-hosted, homelab]` | an agent implements the issue and opens a PR |
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
       ├─ docs          Codex      ├─ read-only, each emits a JSON artifact
       ├─ security      Codex      │
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
claims to have closed. Nothing in the judge inspects the fixer's diff. That is what stops
the fixer clearing its own work — the fixed SHA is graded by its own cycle, by reviewers
that actually read it.

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
choice in an `/autoresolve` comment outranks it.

### The fixer

`review-fixer.yml` is the only stage that may write to the PR branch. It runs **after the
reviewers and before the judge**, which is load-bearing: the judge then grades the SHA the
reviewers actually read, and the fixed SHA earns its own cycle with its own reviewers.
Judging the post-fix tree would let the fixer clear its own work unread.

It runs in one of two modes.

**`cold`** — the fallback, and still the one to optimise for. `author-resume` can only fire
on a PR that `resolve-issue.yml` opened. Now that filing an issue starts an agent, those
are no longer rare — but any PR opened by hand, or by a coding agent on a laptop, carries
no session and lands here. A cold fixer exercises **grounded judgment** (D23): it may apply
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

#### Context reconstruction

Because cold is the normal path, the fixer rebuilds what it can of the author's intent
before triaging anything, into `$PR_CONTEXT_PATH`:

- the PR conversation — where a human most often states the intent a reviewer then misreads
- the branch's commit messages — the author's own narration, which survives when nothing
  else about their reasoning does
- the originating issue and its comments, when the PR body cites one. A PR does not need to
  have been *opened* by an agent to say `Resolves #N`, so this is the context-from-issue
  path for PRs that have no session.

The record cuts both ways (D23). It can make the fixer **skip** — flagged behaviour it
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
4. `ruff` passes on the whole tree. Tests are deliberately **not** run here: that would
   mean installing the PR's own `pyproject.toml`, executing PR-authored build config in the
   one job holding a write-capable PAT. Tests belong to PR Validation, which runs on a
   runner with no secrets.
5. The remote branch head still equals the reviewed SHA. If a human pushed meanwhile, the
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

Unlike the design this borrows from, the fixer does **not** claim `review/pipeline` on the
SHA it just pushed. That claim would suppress the `synchronize` event — but in this graph
that event *is* cycle 2, the one that reviews the fix. The contention it guards against does
not exist here: `fix` needs all four reviewers, so when it pushes, only `ubuntu-latest` jobs
remain and no self-hosted runner is held.

### Role selection

`invariant` always runs. `docs` runs on every non-empty diff: documentation drift can
originate on either side of the docs/code boundary, so there is no file class whose change
provably cannot stale a document. `security` runs unless the change is docs-only, and
always for anything under `.github/`, `src/`, or the dependency and container files.
`test` runs for `src/`, `tests/`, `config/`, and `pyproject.toml`.

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
  `matrix.*` is unavailable in a reusable caller's `if:`. Hence static reviewer
  jobs, one per role. Getting this wrong produces a `startup_failure` with no logs.
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
- **No sandboxed path for fork PRs.** They are refused outright rather than reviewed with
  reduced privileges. A fork's code never reaches the self-hosted runners.
