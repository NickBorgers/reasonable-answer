# CI setup

Everything here is manual and has to be done by a repository admin. It is ordered by
dependency — later steps assume earlier ones. Nothing in this list is needed before the
ops PR merges; the PR is admin-bypass merged because the review pipeline cannot review
the commit that introduces it (every review stage reads `main`'s copy of the judge and
schema, and on that PR they are not on `main` yet).

## 1. Register the self-hosted runners

The agent jobs need to reach the LiteLLM proxy, which lives on the tailnet. GitHub-hosted
runners cannot, so those jobs run on `[self-hosted, homelab]`. Build and test jobs stay on
`ubuntu-latest` and need none of this.

On the homelab host, register a repository-scoped runner for
`NickBorgers/reasonable-answer` with the labels `self-hosted,homelab`. It can share the
machine with other repositories' runners.

**Register at least two.** The three reviewer roles run in parallel; with a single runner
they serialise and every review cycle takes roughly three times as long.

Then, on that host:

```bash
# The runner user must be able to run containers and to reclaim files afterwards.
sudo usermod -aG docker <runner-user>
sudo visudo -f /etc/sudoers.d/runner-chown   # <runner-user> ALL=(ALL) NOPASSWD: /bin/chown

# Bind-mount sources must exist first — docker silently creates a root-owned empty
# directory instead of failing when the source is missing.
mkdir -p ~/.config/gh ~/.claude ~/.codex

# --network host means the container inherits exactly the runner user's reachability.
# Verify it as that user, not as yourself.
sudo -u <runner-user> curl -sf "$LLM_PROXY_BASE_URL" >/dev/null && echo reachable
```

## 1a. Protect the runners — this repository is public

A self-hosted runner attached to a public repository is the highest-risk configuration
in GitHub Actions: anyone can fork, open a pull request, and — absent a gate — have their
code execute on your hardware. Here that hardware sits on your tailnet and the agents run
with tool permissions bypassed, so the blast radius is real.

Four independent layers stop this. They are independent on purpose: any one of them
failing should not be sufficient.

| layer | where | what it stops |
|---|---|---|
| Fork-PR approval policy | repository setting | a fork PR triggering *any* workflow run without your explicit approval |
| Entry authorization | `review-entry.yml` / `resolve-issue.yml` | fork PRs and non-collaborator authors, before anything is dispatched |
| Local guard | `review-reviewer.yml` `guard` job, on a hosted runner | reaching a self-hosted runner even if a future caller forgets to authorize |
| Job-level conditions | `resolve-issue.yml` resolve job | the same, restated where the runner is actually claimed |

The repository setting is the one that is not in version control, so verify it:

```bash
gh api repos/NickBorgers/reasonable-answer/actions/permissions/fork-pr-contributor-approval
# expected: {"approval_policy":"all_external_contributors"}
```

If it reads `first_time_contributors`, fix it — that default lets anyone who has had a
single trivial PR merged run workflows on your runners with no approval:

```bash
gh api -X PUT repos/NickBorgers/reasonable-answer/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=all_external_contributors
```

Also worth doing, outside this repo's control:

- **Scope the runners to this repository**, not to the user or an org runner group. A
  repo-scoped runner cannot be borrowed by another repository's workflows.
- Keep the runner user unprivileged, and remember `--network host` means the agent
  container inherits the runner's full tailnet reachability.

Note that `pull_request_target` appears nowhere in this repository, and must not be
introduced: it runs workflows from the *base* branch with *write* permissions in the
context of a fork's PR, which would defeat every layer above.

## 2. Create the secret

| name | type | scope |
|---|---|---|
| `WORKFLOW_PAT` | repository secret | fine-grained PAT on this repository: Contents R/W, Pull requests R/W, Issues R/W, Commit statuses R/W, Workflows R/W |

Every write in the pipeline uses this token. `GITHUB_TOKEN` is declared read-only at the
entry workflow, which — via the reusable-workflow subset rule — forces every downstream
workflow read-only too.

The single exception is the `All Required Agent Reviews` merge-gate status, which *must*
be written with `GITHUB_TOKEN`. See [ci-pipeline.md](./ci-pipeline.md#the-merge-gate).

A PAT is also what makes the loop work at all: **a pull request opened using
`GITHUB_TOKEN` does not trigger workflows**, so a resolver PR created that way would
never be reviewed.

## 3. Create the repository variables

| name | value | used by |
|---|---|---|
| `LLM_PROXY_BASE_URL` | `https://llm.<tailnet>.ts.net/anthropic/` | Claude-path agents |
| `LLM_PROXY_OPENAI_BASE_URL` | `https://llm.<tailnet>.ts.net/v1/` | Codex-path agents |
| `AI_API_KEY` | `fake-key` | both — the proxy is tailnet-ACL'd, not key-authenticated |
| `CI_AGENT_DEFAULT` | `claude` | which agent `/autoresolve` uses with no explicit choice |
| `CI_IMAGE` | *(optional)* override for the agent image reference | agent jobs |

These are variables rather than secrets deliberately: a tailnet hostname is not a secret,
and keeping it out of the YAML means changing proxies does not require a commit.

## 4. Create the labels

`review-finalize.yml` and `resolve-issue.yml` create these on demand, so this step only
avoids first-run noise.

| label | colour |
|---|---|
| `agent-reviews-passed` | `0E8A16` |
| `needs-human-review` | `B60205` |
| `claude-started` | `7057ff` |
| `codex-started` | `7057ff` |
| `agent:claude` | `BFD4F2` |
| `agent:codex` | `BFD4F2` |

## 5. Build the CI agent image

Nothing creates the `:latest` tag automatically on a fresh repository, and every agent
job pulls it. Dispatch it once:

```bash
gh workflow run ci-image.yml
```

The workflow's own verify step runs `claude --version`, `codex --version`, `uv`,
`python`, `node`, and `actionlint` inside the built image, so a green run means the image
is actually usable, not merely built.

## 6. Publish the runtime image

`Docker Release` runs automatically on merges to `main`. After the first successful run,
set both GHCR packages to **public** visibility:

- `reasonable-answer` — the runtime image
- `reasonable-answer-ci` — the agent image

Without this the pull-back verification job needs credentials it may not have, and nobody
else can pull the image.

## 7. Enable branch protection — last

Do this only after one green run of each workflow, so the check names are known rather
than guessed. On `main`:

- Require a pull request before merging.
- Required status checks (strict / up to date):
  - `PR Validation Required`
  - `All Required Agent Reviews`
- **Keep admin bypass enabled.** It is the escape hatch for a PR that has been wedged by
  a pipeline bug, and it is how the bootstrap PR lands in the first place.

Do not add the matrix jobs (`Tests (3.11)`, `Tests (3.12)`) individually — they are
aggregated by `PR Validation Required`, and listing them makes the required-check set
break every time the matrix changes.

`Docker Release` is not a required check; it runs after merge.

## 8. Verify the loop end to end

1. Open a trivial PR (a README typo). `PR Validation Required` should go green, and the
   review pipeline should run the `invariant` reviewer only — a docs-only change does not
   select `security` or `test`.
2. Open a PR touching `src/reasonable_answer/controller.py`. All three reviewers should
   run.
3. File an issue with the **Agent task** template, then comment `/autoresolve`. A PR
   should appear with `Resolves #N` — **and the review pipeline should fire on it.** If
   the PR appears but nothing reviews it, the resolver checkout is not using
   `WORKFLOW_PAT`.
4. Push a commit to that PR and confirm cycle 2 runs.
5. Comment `/review` on an already-cleared PR and confirm it forces a fresh cycle.

## Troubleshooting

| symptom | cause |
|---|---|
| Merge gate never turns green despite a GO | the status was written with `WORKFLOW_PAT`; branch protection only honours statuses from the GitHub Actions app (integration_id 15368) |
| Resolver opens a PR, nothing reviews it | checkout used `GITHUB_TOKEN`; PRs created with it do not trigger workflows |
| Every run on a SHA is refused as already claimed | a crashed run leaked a pending `review/pipeline` status; `cleanup-claim` should prevent this, but clear it by hand via the statuses API |
| `startup_failure` with no logs | a `uses:` path is wrong, or a reusable workflow declares a permission its caller does not — the subset rule is transitive |
| Reviewer fails on `.review-output` permissions | the runner uid does not match the container's uid 1000 and the directory was not made world-writable |
| Next job's checkout fails on `.git/index.lock` | the post-container `chown` did not run; it is under `if: always()` for this reason |
