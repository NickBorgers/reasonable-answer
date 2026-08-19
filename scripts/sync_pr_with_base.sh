#!/usr/bin/env bash
# Re-merges the base branch into one PR branch, but only when the docs/decisions.md
# append-only merge driver is the thing that makes that merge succeed (D-base-moved-resync).
#
# INERT since D-decision-per-file. That driver is retired — decisions are one file each, so
# there is no shared insertion point to resolve — which means step 2 below can never differ
# from step 1's no-driver baseline. Every PR therefore lands on `plain` or `conflicts`, both of
# which push nothing. That is this script's own documented fallback, not a new behaviour, which
# is why retiring the driver did not need to touch it.
#
# Why this exists at all. D-decisions-merge-driver registered the driver at every place the
# *review pipeline* merges: review-fixer.yml's two sync sites and review-pipeline.yml's
# inherit recreation. All three are reachable only from a review cycle. A PR that has
# already been cleared has no cycle left to run, so when the base moves under it the driver
# never executes — and GitHub's own merge (mergeability, the merge button, auto-merge) does
# not consult a repo-local merge driver at all. PR #158 sat with auto-merge enabled for
# three days on a collision this driver resolves in milliseconds, and was freed by a hand
# merge in the web editor. This script is the missing call site: the event that needs the
# driver is the base branch moving, not a review cycle starting.
#
# Usage: sync_pr_with_base.sh TRUSTED_ROOT REPO_DIR BASE_REF HEAD_REF
#   TRUSTED_ROOT  checkout the driver is executed FROM. Must be a trusted one — the caller
#                 pushes with WORKFLOW_PAT, so the PR's own copy of scripts/ must never run
#                 here. Same rule, and the same reason, as every other registration site.
#   REPO_DIR      a checkout whose `origin` can be pushed to. Reset to the PR head here.
#   BASE_REF      branch that moved (e.g. main).
#   HEAD_REF      the PR's head branch, in this repository (never a fork).
#
# Prints exactly one `state=<...>` line on stdout, which is the whole contract:
#   none        the branch already contains the base tip; nothing to do
#   plain       merges cleanly with no driver registered — the shape a server-side merge
#               can compute for itself, so nothing is pushed. Re-merging every behind PR on
#               every push to the base would churn a SHA the reviewers, the dedup and the
#               cycle counter are all keyed on, to unblock nothing.
#   conflicts   conflicts the driver does not resolve. Left exactly as it was: the review
#               cycle's agentic resolution, or a human, handles it as before this script.
#   moved       the branch changed under us between the merge and the push; nothing pushed.
#   synced      the driver resolved it; the merge is committed and pushed.
#
# Nothing here fails the caller for a PR it cannot help. Not syncing is the pre-decision
# baseline and strands nobody, whereas a red X on the base branch for a PR this script
# declined to touch is noise on every push. A genuine should-never-happen — a driver-routed
# path coming back merged but still carrying conflict markers — is the one exception, and
# exits non-zero.
set -euo pipefail

TRUSTED_ROOT="${1:?usage: sync_pr_with_base.sh TRUSTED_ROOT REPO_DIR BASE_REF HEAD_REF}"
REPO_DIR="${2:?usage: sync_pr_with_base.sh TRUSTED_ROOT REPO_DIR BASE_REF HEAD_REF}"
BASE_REF="${3:?usage: sync_pr_with_base.sh TRUSTED_ROOT REPO_DIR BASE_REF HEAD_REF}"
HEAD_REF="${4:?usage: sync_pr_with_base.sh TRUSTED_ROOT REPO_DIR BASE_REF HEAD_REF}"

# Absolute before the cd, because the caller may well have named it relatively and the
# registration helper below writes it into git config, where a relative path would later
# resolve against whatever repository git happens to be merging.
TRUSTED_ROOT="$(cd "${TRUSTED_ROOT}" && pwd)"
cd "${REPO_DIR}"

state() {
  echo "state=$1"
  exit 0
}

# The merge commit must be authored as the agent, not as whoever's token is pushing it.
# review-pipeline.yml resets the cycle counter to 1 on any commit NOT authored as
# AGENT_COMMIT_EMAIL, on the theory that a human read the blockers and answered them. A
# resync is neither, and billing it as a human intervention would hand a capped PR a fresh
# budget of agent cycles for a merge nobody wrote.
git config user.email "${AGENT_COMMIT_EMAIL:-ci@reasonable-answer.local}"
git config user.name  "${AGENT_COMMIT_NAME:-reasonable-answer agent}"

# Start from no registration, whatever the caller's environment or an earlier PR in the
# same loop left behind. The plain merge below is only the no-driver baseline if it really
# ran without the driver.
git config --unset-all merge.decisions-append.driver 2>/dev/null || true

git fetch --quiet origin \
  "+refs/heads/${BASE_REF}:refs/remotes/origin/${BASE_REF}" \
  "+refs/heads/${HEAD_REF}:refs/remotes/origin/${HEAD_REF}"

git checkout --quiet --detach "refs/remotes/origin/${HEAD_REF}"
git clean --quiet -ffd

HEAD_SHA="$(git rev-parse HEAD)"

# Same test the fixer's sync step and gather's drift detector use, so all three agree on
# when a sync is due.
if git merge-base --is-ancestor "refs/remotes/origin/${BASE_REF}" HEAD; then
  state none
fi

# Undoes a merge left half-applied by `--no-commit`, whether it succeeded or conflicted.
# `git merge --abort` alone is not enough: it errors out when there is no MERGE_HEAD, and
# under `set -e` that would turn a state this script handles into a job failure.
unmerge() {
  git merge --abort >/dev/null 2>&1 || true
  git reset --quiet --hard "${HEAD_SHA}"
  git clean --quiet -ffd
}

# Step 1 — the no-driver baseline. A merge driver lives in a `.git/config` that only a
# checkout has, so a merge with none registered is the only kind a server-side merge can
# compute; clean here means the PR is not blocked on its base and there is nothing for this
# script to unblock.
if git merge --no-commit --no-ff "refs/remotes/origin/${BASE_REF}" >/dev/null 2>&1; then
  unmerge
  state plain
fi
unmerge

# Step 2 — the same merge with the trusted driver registered. The helper smoke-tests the
# driver against a known-good append and a known-bad conflict before registering anything,
# and registers nothing if either fails; an unregistered driver simply reproduces step 1's
# conflict, which is the baseline this script promises to fall back to.
REGISTER_DRIVER="${TRUSTED_ROOT}/scripts/register_decisions_driver.sh"
if [ ! -x "${REGISTER_DRIVER}" ]; then
  echo "::warning::${REGISTER_DRIVER} is not present in the trusted checkout; ${HEAD_REF} keeps the conflict it already had"
  state conflicts
fi
"${REGISTER_DRIVER}" "${TRUSTED_ROOT}" "${REPO_DIR}"
if ! git config --get merge.decisions-append.driver >/dev/null; then
  state conflicts  # the helper declined to register; step 1's conflict stands
fi

if ! git merge --no-commit --no-ff "refs/remotes/origin/${BASE_REF}"; then
  unmerge
  state conflicts
fi

# A driver that ran but wrote nothing leaves "ours" in the worktree with no markers and the
# path merely `UU`, which `git merge` still reports as a conflict — so that case is already
# step 2's `conflicts`. The inverse is the one that would be invisible: a driver reporting
# success while leaving markers in what it wrote. Pushing that would put conflict markers
# into a normative spec file under a commit message claiming a clean sync, so it is the
# single condition here worth failing over rather than declining quietly.
while IFS= read -r changed; do
  [ -n "${changed}" ] || continue
  [ -f "${changed}" ] || continue
  case "$(git check-attr merge -- "${changed}")" in
    *": merge: decisions-append") ;;
    *) continue ;;
  esac
  if grep -q '^<<<<<<< ' "${changed}"; then
    echo "::error::${changed} came back from the merge driver reported clean but carrying conflict markers; refusing to push ${HEAD_REF}"
    unmerge
    exit 1
  fi
done < <(git diff --name-only --cached HEAD)

git commit --quiet -m "Merge origin/${BASE_REF} into ${HEAD_REF}" \
                   -m "Pipeline-driven sync to keep the PR mergeable; no PR-side content delta."

# The head was read before the merge. If anything pushed to the branch in between — the
# fixer sealing a cycle, an agent still working, a human — our commit is built on a tree
# that is no longer the branch. The push would be rejected anyway; refusing here says so
# with a reason instead of a git error, and leaves the rest of the caller's loop unaffected.
REMOTE_HEAD="$(git ls-remote --heads origin "refs/heads/${HEAD_REF}" | awk 'NR==1 {print $1}')"
if [ "${REMOTE_HEAD:-}" != "${HEAD_SHA}" ]; then
  echo "::warning::${HEAD_REF} moved from ${HEAD_SHA} to ${REMOTE_HEAD:-<gone>} while it was being merged; nothing pushed"
  state moved
fi

git push --quiet origin "HEAD:refs/heads/${HEAD_REF}"
state synced
