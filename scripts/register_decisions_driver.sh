#!/usr/bin/env bash
# Registers the docs/decisions.md append-only merge driver — but only after proving the
# exact command git will run actually works (D-decisions-merge-driver).
#
# Why the proof, rather than a bare `git config`: a merge driver that cannot START does not
# degrade to the no-driver baseline. Git marks the path conflicted but leaves "ours" in the
# worktree with NO conflict markers and the path only `UU` in the index. review-fixer.yml's
# commit step runs `git add -A` before its marker gate, which resolves that entry, after
# which `git ls-files -u` and `git diff --check --cached` are both empty — the gate passes
# and the pipeline pushes a merge that silently dropped every base-side change to a
# normative spec file. The gate is built on the assumption that an unresolved conflict
# leaves markers, and a non-executing driver is the one thing that breaks it.
#
# So: smoke-test first, register only on success. An unregistered driver is exactly the
# no-driver baseline — a real conflict, with real markers, reviewed by a human or an agent
# as before — which is the behaviour the decision promises for every case the driver cannot
# confirm. Failing the job instead would turn a missing file into a stuck PR for no safety
# gain, so this warns and stands down.
#
# Usage: register_decisions_driver.sh TRUSTED_ROOT [TARGET_REPO]
#   TRUSTED_ROOT  checkout the driver is executed FROM — must be a trusted one (the `main`
#                 checkout in CI), never the PR checkout under review: the sync steps hold
#                 WORKFLOW_PAT, and the inherit step is a verifier that must not run code
#                 supplied by the commit it is verifying.
#   TARGET_REPO   repo whose .git/config is written (default: the current directory).
set -euo pipefail

TRUSTED_ROOT="${1:?usage: register_decisions_driver.sh TRUSTED_ROOT [TARGET_REPO]}"
TARGET_REPO="${2:-.}"
DRIVER="${TRUSTED_ROOT}/scripts/merge_decisions.py"

warn() {
  # Clear any earlier registration as well as declining to add one. The fixer registers in
  # two steps against the same repo, and a stale entry from the first would keep a driver
  # this run just proved broken in force for the merge the second one replays.
  git -C "${TARGET_REPO}" config --unset-all merge.decisions-append.driver 2>/dev/null || true
  # ::warning:: is a GitHub annotation and plain text everywhere else; both are visible.
  echo "::warning::decisions merge driver not registered: $1 — docs/decisions.md will merge exactly as it did before this driver existed"
}

smoke=$(mktemp -d)
trap 'rm -rf "${smoke}"' EXIT

# A minimal instance of the shape the fast path exists for: both sides append one whole
# section before an untouched tail marker. Exercised end to end, not merely `test -x`, so a
# missing python3, an unreadable file, or a syntax error introduced later all land here
# rather than at merge time.
head='# Decision log

## D-smoke-base — a decision that predates both sides

Body.

'
tail='## Open items for a future round

- nothing
'
ours_section='## D-smoke-ours — appended by one side

Body.

'
theirs_section='## D-smoke-theirs — appended by the other side

Body.

'
printf '%s%s' "${head}" "${tail}" > "${smoke}/base"
printf '%s%s%s' "${head}" "${ours_section}" "${tail}" > "${smoke}/ours"
printf '%s%s%s' "${head}" "${theirs_section}" "${tail}" > "${smoke}/theirs"
printf '%s%s%s%s' "${head}" "${ours_section}" "${theirs_section}" "${tail}" > "${smoke}/expected"

cp "${smoke}/ours" "${smoke}/result"
if ! python3 "${DRIVER}" "${smoke}/base" "${smoke}/result" "${smoke}/theirs" >/dev/null 2>&1; then
  warn "\`python3 ${DRIVER}\` did not run cleanly on a known-good append"
  exit 0
fi
if ! cmp -s "${smoke}/result" "${smoke}/expected"; then
  warn "\`python3 ${DRIVER}\` ran but did not produce the expected merge of a known-good append"
  exit 0
fi

# The other half of the contract: a shape the driver declines must still come back as a
# real conflict. If the fallback to `git merge-file` were broken, the driver would report
# success on input it never merged — the same silent-drop failure, one layer in.
printf '%s%s' "${head/Body./Ours edited this body.}" "${tail}" > "${smoke}/conflict-ours"
printf '%s%s' "${head/Body./Theirs edited this body.}" "${tail}" > "${smoke}/conflict-theirs"
cp "${smoke}/conflict-ours" "${smoke}/conflict-result"
if python3 "${DRIVER}" "${smoke}/base" "${smoke}/conflict-result" "${smoke}/conflict-theirs" >/dev/null 2>&1; then
  warn "\`python3 ${DRIVER}\` reported a clean merge for a genuine same-section conflict"
  exit 0
fi
if ! grep -q '^<<<<<<<' "${smoke}/conflict-result"; then
  warn "\`python3 ${DRIVER}\` declined a conflict without leaving conflict markers"
  exit 0
fi

# Absolute path on purpose: git runs the driver from the worktree top of whatever repo is
# merging, so a relative path would resolve against that repo — which on the fixer's and the
# inherit step's paths is the untrusted PR checkout, the thing TRUSTED_ROOT exists to avoid.
# `-C` moves where the config is written, not where the command later runs from.
git -C "${TARGET_REPO}" config merge.decisions-append.driver "python3 ${DRIVER} %O %A %B"
echo "Registered the docs/decisions.md append-only merge driver from ${DRIVER}."
