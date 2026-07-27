#!/usr/bin/env bash
#
# Fails when docs/decisions.md gives the same decision number to two sections.
#
# A decision number (`## D<n>`) is allocated by whoever writes the PR, and the number is
# not just prose: it appears in config/, src/, tests/ and several docs, so a collision
# costs a repo-wide rename. Two PRs open at once each pick the same next-free number
# against main and then collide when both merge (issue #71).
#
# The fix keeps authoring-time allocation but refuses the collision at the gate. On a
# `pull_request` event GitHub checks out the *merge ref* — the PR already merged into its
# base branch — so the file this script sees is the file that will exist on main once the
# PR lands. A duplicate here is therefore a collision that would otherwise reach main, and
# the required check goes red before it can. Two simultaneously-open PRs that both add
# D<n> do not collide against each other's unmerged branches; the first to merge advances
# main, and the second's merge ref then carries two D<n> and fails — which is why branch
# protection should require branches to be up to date before merging.
#
# Pure and offline: it reads one file and nothing else — no git, no network, no token, no
# secret — so the tests drive it with fixtures and it fits the secret-free PR gate.

set -euo pipefail

FILE="${1:-docs/decisions.md}"

if [[ ! -f "$FILE" ]]; then
  echo "decision-number check: '$FILE' not found" >&2
  exit 2
fi

# A decision section is `## D<n>` alone at the start of a line. Everything else is a
# reference, not an allocation, and is ignored: prose mentioning D26, and the D1–D19
# summary rows in the top table (`| D1 | … |`), are matched by neither the anchor nor
# the heading level.
numbers="$(grep -oE '^## D[0-9]+' "$FILE" | grep -oE '[0-9]+' || true)"

dupes="$(printf '%s\n' "$numbers" | sed '/^$/d' | sort -n | uniq -d)"

if [[ -n "$dupes" ]]; then
  echo "Duplicate decision numbers in $FILE:" >&2
  while IFS= read -r n; do
    count="$(grep -cE "^## D${n}([^0-9]|$)" "$FILE")"
    echo "  D${n} is defined ${count} times" >&2
  done <<< "$dupes"
  echo >&2
  echo "Each '## D<n>' must be unique. Renumber the new section to the next free number" >&2
  echo "(and update its references in config/, src/, tests/ and docs)." >&2
  exit 1
fi

echo "Decision numbers in $FILE are unique."
