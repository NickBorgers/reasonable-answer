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
# Empty, or equal to REVIEWED_SHA, whenever no fix commit landed this cycle.
POST_FIX_SHA="${POST_FIX_SHA:-}"

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
      # Says what was actually checked, not what the head commit looks like. Everything
      # pushed since the last reviewed commit is a base-branch merge, and re-doing that
      # merge reproduces this tree exactly — so there is no content here to read
      # (D-inherit-whole-range). The old wording claimed this of a push it had only
      # inspected the head of.
      #
      # "the commit this PR's last verdict was published for", not "the last reviewed
      # commit" as the cycle counter records it: those differ whenever the fixer pushed,
      # and measuring from the second compared that push with itself
      # (D-inherit-reviewed-anchor). Worded to stay true of the case where that commit
      # *is* this one and the range is empty.
      echo "> Every commit pushed since the one this PR's last verdict was published for"
      echo "> is a merge of the base branch, and re-creating those merges reproduces this"
      echo "> commit's tree exactly, so there is no new content to read. It inherits the"
      echo "> previous verdict rather than consuming a review cycle. Comment \`/review\` to"
      echo "> read it anyway."
      echo
      ;;
  esac
} > "$BODY"

# What the judge credited the fixer with, keyed the way the judge keys it: `<role>/<id>`.
# Two reviewers may raise the same bare id, and matching on the bare one would report a
# still-open blocker as fixed. Read once, used by both the table and the lists below.
VERDICT_JSON="$(find "$VERDICT_DIR" -name 'verdict-*.json' 2>/dev/null | head -1 || true)"
addressed_ids=""
if [ -n "$VERDICT_JSON" ] && [ -f "$VERDICT_JSON" ]; then
  addressed_ids=$(jq -r '(.addressed_blocker_ids // [])[]' "$VERDICT_JSON" 2>/dev/null || true)
fi

# Absent field, absent artifact, unreadable JSON: everything reads as outstanding. The
# comment must fail toward showing a blocker, never toward filing an open one under "fixed".
is_addressed() {
  [ -n "$addressed_ids" ] || return 1
  printf '%s\n' "$addressed_ids" | grep -Fxq -- "$1"
}

# Per-reviewer table, when there are artifacts to describe.
if [ -d "$REVIEWER_DIR" ]; then
  rows=""
  while IFS= read -r result; do
    [ -n "$result" ] || continue
    role=$(jq -r '.role // "?"' "$result")
    decision=$(jq -r '.decision // "?"' "$result")
    # Split rather than totalled. `request_changes` with a bare `2` on a GO comment is the
    # same contradiction the lists below fix: the reviewer did raise two, and neither still
    # stands.
    blockers_open=0
    blockers_fixed=0
    while IFS= read -r bid; do
      [ -n "$bid" ] || continue
      if is_addressed "${role}/${bid}"; then
        blockers_fixed=$((blockers_fixed + 1))
      else
        blockers_open=$((blockers_open + 1))
      fi
    done < <(jq -r '.blocking_issues[]?.id' "$result" 2>/dev/null || true)
    if [ "$blockers_fixed" -gt 0 ]; then
      blockers="${blockers_open} (${blockers_fixed} fixed)"
    else
      blockers="${blockers_open}"
    fi
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

# The judge's own reasons, verbatim.
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

# Blockers, split by whether the fixer already cleared them.
#
# One undifferentiated list was actively misleading. A GO comment would announce "cleared
# at cycle 1" and then print a "Blocking issues" heading listing findings that no longer
# blocked anything — the fixer had addressed them in the same cycle, and the only trace of
# that was a blocker *count* buried in the Why line. A reader reasonably concluded the merge
# gate had let outstanding blockers through (D-addressed-blockers-visible).
#
# The judge already decides this: `addressed_blocker_ids` is the namespaced set it credited
# the fixer with, and it is the same set the verdict was computed from — so the comment and
# the gate cannot disagree about which blockers still stand.
#
# Ids are namespaced `<role>/<id>` here for the same reason the judge namespaces them: two
# reviewers may raise the same bare id, and a lookup on the bare one would credit both when
# only one was fixed. That means iterating per artifact, since the role comes from the file.
if [ -d "$REVIEWER_DIR" ]; then
  outstanding=""
  cleared=""
  while IFS= read -r result; do
    [ -n "$result" ] || continue
    role=$(jq -r '.role // "?"' "$result")
    # Tab-separated so the raw id stays available for the lookup while the rendered line
    # carries the namespaced one. The message is flattened first: an embedded newline would
    # otherwise split into a second, id-less iteration and render as an unowned bullet.
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      id="${line%%$'\t'*}"
      rendered="${line#*$'\t'}"
      if is_addressed "${role}/${id}"; then
        cleared="${cleared}- ✅ ${rendered}"$'\n'
      else
        outstanding="${outstanding}- ⛔ ${rendered}"$'\n'
      fi
    done < <(jq -r --arg role "$role" '
      .blocking_issues[]?
      | "\(.id)\t**\(.severity)** `\($role)/\(.id)`"
        + (if .decision_ref then " (\(.decision_ref))" else "" end)
        + " — " + ((.message // "") | gsub("[\n\r]+"; " "))
    ' "$result" 2>/dev/null || true)
  done < <(find "$REVIEWER_DIR" -name '*-result.json' | sort)

  if [ -n "$outstanding" ]; then
    {
      echo "### Blocking issues — still outstanding"
      printf '%s' "$outstanding"
      echo
    } >> "$BODY"
  fi

  if [ -n "$cleared" ]; then
    {
      # Named as history, not as an open list. The heading has to survive being read alone,
      # because that is how a long comment gets skimmed.
      echo "### Blocking issues raised this cycle — fixed by the fixer"
      echo
      if [ -n "$POST_FIX_SHA" ] && [ "$POST_FIX_SHA" != "$REVIEWED_SHA" ]; then
        echo "These were raised against \`${short_sha}\` and resolved in \`${POST_FIX_SHA:0:7}\`."
      else
        echo "These were raised against \`${short_sha}\` and resolved in this cycle."
      fi
      echo "They are listed for the record and are **not** outstanding."
      echo
      printf '%s' "$cleared"
      echo
    } >> "$BODY"
  fi
fi

{
  echo "---"
  # `/review` re-runs the panel on the same SHA; it does not reset the counter, and saying
  # otherwise was actively misleading on a PR already at the cap — where it returns a
  # reviewer-less `cycle_capped` NO-GO. Pushing a human-authored commit is what resets the
  # count (docs/ci-pipeline.md, cycle control).
  if [ -n "$RUN_URL" ]; then
    echo "<sub>Cycle ${CYCLE} · [run log](${RUN_URL}) · \`/review\` re-runs the panel; pushing a commit resets the cycle count.</sub>"
  else
    echo "<sub>Cycle ${CYCLE} · \`/review\` re-runs the panel; pushing a commit resets the cycle count.</sub>"
  fi
} >> "$BODY"

gh pr comment "$PR_NUMBER" --repo "$REPO" --body-file "$BODY"
echo "render-finalize-comment: posted ${VERDICT} (${CATEGORY}) for ${short_sha}"
