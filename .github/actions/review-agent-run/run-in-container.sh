#!/usr/bin/env bash
#
# Runs inside the CI agent image. Mounted read-only from the action checkout, not from
# the PR workspace, so a pull request cannot modify the script that reviews it.
#
# Contract with the calling composite:
#   in  — REVIEW_PROMPT_PATH, RESULT_PATH, OUTPUT_LOG_PATH, CI_AGENT, AGENT_TIMEOUT_MINUTES
#   out — a JSON artifact at RESULT_PATH, conforming to schema/reviewer-v1.json

set -euo pipefail

: "${CI_AGENT:?CI_AGENT must be set}"
: "${REVIEW_PROMPT_PATH:?REVIEW_PROMPT_PATH must be set}"
: "${RESULT_PATH:?RESULT_PATH must be set}"
: "${OUTPUT_LOG_PATH:?OUTPUT_LOG_PATH must be set}"

TIMEOUT="${AGENT_TIMEOUT_MINUTES:-30}m"

if [ ! -f "$REVIEW_PROMPT_PATH" ]; then
  echo "::error::run-in-container: prompt file not found at $REVIEW_PROMPT_PATH"
  exit 1
fi

PROMPT_BODY="$(cat "$REVIEW_PROMPT_PATH")"

mkdir -p "$(dirname "$RESULT_PATH")" "$(dirname "$OUTPUT_LOG_PATH")"

# The base branch is needed to compute the diff under review. The workspace is checked
# out at the PR head, which may not have the base ref locally.
if [ -n "${BASE_REF:-}" ]; then
  git fetch --quiet --depth=50 origin "$BASE_REF" 2>/dev/null || true
fi

echo "run-in-container: ${REVIEW_ROLE:-agent} via ${CI_AGENT}, timeout ${TIMEOUT}"

# `< /dev/null` matters: without it the CLIs wait on stdin and hang until the timeout.
case "$CI_AGENT" in
  claude)
    model_args=()
    [ -n "${AGENT_MODEL:-}" ] && model_args=(--model "$AGENT_MODEL")
    timeout "$TIMEOUT" claude -p \
      --dangerously-skip-permissions \
      --permission-mode=bypassPermissions \
      "${model_args[@]}" \
      --verbose \
      "$PROMPT_BODY" \
      < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    ;;
  codex)
    model_args=()
    [ -n "${AGENT_MODEL:-}" ] && model_args=(--model "$AGENT_MODEL")
    timeout "$TIMEOUT" codex exec \
      --dangerously-bypass-approvals-and-sandbox \
      "${model_args[@]}" \
      "$PROMPT_BODY" \
      < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    ;;
  *)
    echo "::error::run-in-container: unknown CI_AGENT '$CI_AGENT'"
    exit 1
    ;;
esac

# An agent that ran to completion but produced nothing is a failure, not a silent pass.
# Letting it through would hand the judge an empty reviewer set, and the fail-closed
# contract would turn that into a confusing pipeline_error rather than a clear one here.
if [ ! -s "$RESULT_PATH" ]; then
  echo "::error::run-in-container: ${REVIEW_ROLE:-agent} did not produce $RESULT_PATH"
  exit 1
fi

echo "run-in-container: ${REVIEW_ROLE:-agent} produced $RESULT_PATH"
