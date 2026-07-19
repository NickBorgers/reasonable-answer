#!/usr/bin/env bash
#
# Renders the per-cycle summary comment on the PR.
#
# Must work on paths where no verdict artifact exists (cycle cap, inherited verdict), so
# every read of the artifact directories is guarded. A missing file degrades the comment;
# it must never fail the job, because this stage also writes the merge gate.

set -euo pipefail

: "${REPO:?}" "${PR_NUMBER:?}" "${REVIEWED_SHA:?}" "${CYCLE:?}" "${VERDICT:?}" "${CATEGORY:?}"

VERDICT_DIR="${VERDICT_DIR:-verdict}"
REVIEWER_DIR="${REVIEWER_DIR:-reviewer-artifacts}"
RUN_URL="${RUN_URL:-}"

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

short_sha="${REVIEWED_SHA:0:7}"

case "$VERDICT" in
  GO)    headline="✅ **GO** — cleared at cycle ${CYCLE}" ;;
  *)     headline="🚫 **NO-GO** — cycle ${CYCLE}" ;;
esac

{
  echo "## Agent review — \`${short_sha}\`"
  echo
  echo "$headline"
  echo

  case "$CATEGORY" in
    cycle_capped)
      echo "> The review cycle cap was reached. This is a cost backstop, not a judgement"
      echo "> about the change. Push a fix and comment \`/review\` to start a fresh cycle."
      echo
      ;;
    pipeline_error)
      echo "> The pipeline could not trust its own inputs, so it failed closed. This is"
      echo "> usually a reviewer or orchestration bug rather than a problem with the change."
      echo
      ;;
    inherited)
      echo "> This commit merges the base branch into the PR and introduces no new content,"
      echo "> so it inherits the previous verdict rather than consuming a review cycle."
      echo
      ;;
  esac
} > "$BODY"

# Per-reviewer table, when there are artifacts to describe.
if [ -d "$REVIEWER_DIR" ]; then
  rows=""
  while IFS= read -r result; do
    [ -n "$result" ] || continue
    role=$(jq -r '.role // "?"' "$result")
    decision=$(jq -r '.decision // "?"' "$result")
    blockers=$(jq -r '.blocking_issues | length' "$result")
    notes=$(jq -r '.non_blocking_notes | length' "$result")
    summary=$(jq -r '.summary // ""' "$result" | tr '\n' ' ' | cut -c1-160)

    url_file="${result%-result.json}-comment-url.txt"
    if [ -f "$url_file" ]; then
      role_cell="[\`${role}\`]($(tr -d '[:space:]' < "$url_file"))"
    else
      role_cell="\`${role}\`"
    fi

    rows="${rows}| ${role_cell} | ${decision} | ${blockers} | ${notes} | ${summary} |"$'\n'
  done < <(find "$REVIEWER_DIR" -name '*-result.json' | sort)

  if [ -n "$rows" ]; then
    {
      echo "| reviewer | decision | blocking | notes | summary |"
      echo "|---|---|---:|---:|---|"
      printf '%s' "$rows"
      echo
    } >> "$BODY"
  fi
fi

# Blocking issues, with the reason each one blocks.
VERDICT_JSON="$(find "$VERDICT_DIR" -name 'verdict-*.json' 2>/dev/null | head -1 || true)"
if [ -n "$VERDICT_JSON" ] && [ -f "$VERDICT_JSON" ]; then
  reasons=$(jq -r '.reasons[]?' "$VERDICT_JSON")
  if [ -n "$reasons" ]; then
    {
      echo "### Why"
      # shellcheck disable=SC2001  # prefixing every line of a multi-line string
      echo "$reasons" | sed 's/^/- /'
      echo
    } >> "$BODY"
  fi
fi

if [ -d "$REVIEWER_DIR" ]; then
  blocking=$(find "$REVIEWER_DIR" -name '*-result.json' -exec \
    jq -r '.blocking_issues[]? | "- **\(.severity)** `\(.id)`\(if .decision_ref then " (\(.decision_ref))" else "" end) — \(.message)"' {} \; 2>/dev/null || true)
  if [ -n "$blocking" ]; then
    {
      echo "### Blocking issues"
      echo "$blocking"
      echo
    } >> "$BODY"
  fi
fi

{
  echo "---"
  if [ -n "$RUN_URL" ]; then
    echo "<sub>Cycle ${CYCLE} · [run log](${RUN_URL}) · \`/review\` forces a fresh cycle.</sub>"
  else
    echo "<sub>Cycle ${CYCLE} · \`/review\` forces a fresh cycle.</sub>"
  fi
} >> "$BODY"

gh pr comment "$PR_NUMBER" --repo "$REPO" --body-file "$BODY"
echo "render-finalize-comment: posted ${VERDICT} (${CATEGORY}) for ${short_sha}"
