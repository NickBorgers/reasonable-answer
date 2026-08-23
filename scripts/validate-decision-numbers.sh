#!/usr/bin/env bash
#
# Checks the shape of the decision registry: one file per decision, each defining its own
# slug exactly once, and no slug defined twice anywhere.
#
# A decision is identified by a slug derived from its subject (`D-source-verification`),
# not by a number from a shared counter (D-decision-slugs, which supersedes D-decision-gate).
# Slugs are collision-free by construction: two concurrently-open PRs cannot pick the same
# identifier, because each is coined from its own decision's content rather than from the
# highest number on main. So this gate no longer exists to catch a numbering race — it exists
# to catch the one thing slugs do not prevent: the *same* slug defined twice.
#
# A decision has two surface forms, and both are definitions:
#   * its own file      docs/decisions/D-<slug>.md, first line `## D-<slug> — <title>`
#   * an index-table row  `| D-<slug> | … |`   (first cell is the slug, in docs/decisions.md)
# The predecessor gate read only `^## D<n>` and never the table rows, so it could not answer
# "is this defined twice?" for the table half — and four numbers had in fact each named two
# different decisions there, unseen (D-decision-slugs). This check reads BOTH forms and refuses
# a slug that appears as a definition more than once across their union. The old-number mapping
# table at the top of the index is not a definition (its first cell is an old numeric id, not a
# slug), so it is ignored.
#
# One file per decision is D-decision-per-file: prose sections used to be appended to a single
# file immediately before a fixed tail marker, which made every pair of decision-bearing PRs
# conflict on the same line. The three structural checks below are what replaced that anchor —
# a filename that disagrees with its heading, two headings in one file, or a prose section left
# behind in the index would each reintroduce a way for a slug's definition to be ambiguous
# about which file owns it.
#
# Pure and offline: it reads one file and one directory and nothing else — no git, no network,
# no token, no secret — so the tests drive it with fixtures and it fits the secret-free PR gate.

set -euo pipefail

INDEX="${1:-docs/decisions.md}"
# The per-decision directory is a sibling of the index, named for it: docs/decisions.md
# indexes docs/decisions/. Deriving it keeps the script single-argument, which is what lets
# the tests point the whole check at a fixture tree.
DIR="$(dirname "$INDEX")/$(basename "$INDEX" .md)"

if [[ ! -f "$INDEX" ]]; then
  echo "decision-registry check: index '$INDEX' not found" >&2
  exit 2
fi

if [[ ! -d "$DIR" ]]; then
  echo "decision-registry check: decision directory '$DIR' not found" >&2
  exit 2
fi

fail=0

# --- structural: every entry in the directory is one decision, named for it -----------------
#
# `D-<slug>.md` and nothing else. A stray file here would be a decision the citation tests
# cannot resolve, or a slug two files could both claim to define.
file_slugs=""
shopt -s nullglob
for path in "$DIR"/*; do
  name="$(basename "$path")"

  if [[ ! -f "$path" || ! "$name" =~ ^(D-[a-z0-9-]+)\.md$ ]]; then
    echo "$DIR/$name is not a decision file: every entry must be a regular file named D-<slug>.md" >&2
    fail=1
    continue
  fi
  slug="${BASH_REMATCH[1]}"

  # Exactly one prose definition per file, and it must be the first line — so the file's
  # name, its heading and the decision it defines cannot disagree.
  headings="$(grep -cE '^## D-[a-z0-9-]+ — ' "$path" || true)"
  if [[ "$headings" != "1" ]]; then
    echo "$DIR/$name defines $headings decisions; it must define exactly one (## D-<slug> — <title>)" >&2
    fail=1
    continue
  fi

  heading_slug="$(head -n 1 "$path" | grep -oE '^## D-[a-z0-9-]+' | grep -oE 'D-[a-z0-9-]+' || true)"
  if [[ -z "$heading_slug" ]]; then
    echo "$DIR/$name does not open with its '## D-<slug> — <title>' heading" >&2
    fail=1
    continue
  fi
  if [[ "$heading_slug" != "$slug" ]]; then
    echo "$DIR/$name is named for $slug but its heading defines $heading_slug" >&2
    fail=1
    continue
  fi

  file_slugs+="$slug"$'\n'
done
shopt -u nullglob

# --- structural: the index holds no prose sections ------------------------------------------
#
# The index carries the identifier scheme, the finding tables and the open items. A
# `## D-<slug> — …` section left in it is a decision in two places at once, and the shared
# insertion point D-decision-per-file removed.
stray="$(grep -oE '^## D-[a-z0-9-]+' "$INDEX" || true)"
if [[ -n "$stray" ]]; then
  echo "$INDEX still contains decision prose sections; each belongs in its own $DIR/D-<slug>.md:" >&2
  printf '%s\n' "$stray" | sed 's/^## /  /' >&2
  fail=1
fi

# --- uniqueness: no slug defined twice across both forms ------------------------------------
#
# The table anchor requires the slug to be the row's first cell. A bare `D-<slug>` mentioned
# in prose, or the slug sitting in the *second* column of the mapping table, matches neither.
table_slugs="$(grep -oE '^\| D-[a-z0-9-]+ \|' "$INDEX" | grep -oE 'D-[a-z0-9-]+' || true)"

all_slugs="$(printf '%s\n%s\n' "$file_slugs" "$table_slugs" | sed '/^$/d')"
dupes="$(printf '%s\n' "$all_slugs" | sed '/^$/d' | sort | uniq -d)"

if [[ -n "$dupes" ]]; then
  echo "Duplicate decision identifiers in the registry rooted at $INDEX:" >&2
  while IFS= read -r s; do
    files="$(printf '%s\n' "$file_slugs" | grep -cxF "$s" || true)"
    table="$(grep -cE "^\| ${s} \|" "$INDEX" || true)"
    echo "  ${s} is defined $((files + table)) times (files: ${files}, index table: ${table})" >&2
  done <<< "$dupes"
  echo >&2
  echo "Each decision slug must be defined once, in one form. Rename the new decision, or" >&2
  echo "fold a stray index-table row into the file that supersedes it." >&2
  fail=1
fi

if [[ "$fail" != "0" ]]; then
  exit 1
fi

count="$(printf '%s\n' "$all_slugs" | sed '/^$/d' | grep -c . || true)"
echo "Decision registry at $INDEX is well-formed: ${count} definitions, no duplicate identifiers."
