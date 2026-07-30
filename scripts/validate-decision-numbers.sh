#!/usr/bin/env bash
#
# Fails when docs/decisions.md defines the same decision identifier twice.
#
# A decision is identified by a slug derived from its subject (`D-source-verification`),
# not by a number from a shared counter (D-decision-slugs, which supersedes D-decision-gate).
# Slugs are collision-free by construction: two concurrently-open PRs cannot pick the same
# identifier, because each is coined from its own decision's content rather than from the
# highest number on main. So this gate no longer exists to catch a numbering race — it exists
# to catch the one thing slugs do not prevent: the *same* slug defined twice.
#
# A decision has two surface forms, and both are definitions:
#   * a prose section  `## D-<slug> — <title>`
#   * a top-table row  `| D-<slug> | … |`   (first cell is the slug)
# The predecessor gate read only `^## D<n>` and never the table rows, so it could not answer
# "is this defined twice?" for the table half — and four numbers had in fact each named two
# different decisions there, unseen (D-decision-slugs). This check reads BOTH forms and refuses
# a slug that appears as a definition more than once across their union. The old-number mapping
# table at the top of the file is not a definition (its first cell is an old numeric id, not a
# slug), so it is ignored.
#
# Pure and offline: it reads one file and nothing else — no git, no network, no token, no
# secret — so the tests drive it with fixtures and it fits the secret-free PR gate.

set -euo pipefail

FILE="${1:-docs/decisions.md}"

if [[ ! -f "$FILE" ]]; then
  echo "decision-identifier check: '$FILE' not found" >&2
  exit 2
fi

# Definitions in either form. A slug is `D-` followed by lowercase letters, digits and
# dashes. The prose anchor requires the heading level and a following ` —`; the table anchor
# requires the slug to be the row's first cell. A bare `D-<slug>` mentioned in prose, or the
# slug sitting in the *second* column of the mapping table, matches neither.
slugs="$(
  {
    grep -oE '^## D-[a-z0-9-]+ ' "$FILE" || true
    grep -oE '^\| D-[a-z0-9-]+ \|' "$FILE" || true
  } | grep -oE 'D-[a-z0-9-]+'
)"

dupes="$(printf '%s\n' "$slugs" | sed '/^$/d' | sort | uniq -d)"

if [[ -n "$dupes" ]]; then
  echo "Duplicate decision identifiers in $FILE:" >&2
  while IFS= read -r s; do
    prose="$(grep -cE "^## ${s} " "$FILE" || true)"
    table="$(grep -cE "^\| ${s} \|" "$FILE" || true)"
    echo "  ${s} is defined $((prose + table)) times (prose: ${prose}, table: ${table})" >&2
  done <<< "$dupes"
  echo >&2
  echo "Each decision slug must be defined once, in one form. Rename the new section, or" >&2
  echo "fold a stray table row into the prose section that supersedes it." >&2
  exit 1
fi

echo "Decision identifiers in $FILE are unique."
