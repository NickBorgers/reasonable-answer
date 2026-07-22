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

RESUME="${AGENT_RESUME:-0}"

echo "run-in-container: ${REVIEW_ROLE:-agent} via ${CI_AGENT}, timeout ${TIMEOUT}, resume=${RESUME}"

# `< /dev/null` matters: without it the CLIs wait on stdin and hang until the timeout.
case "$CI_AGENT" in
  claude)
    model_args=()
    [ -n "${AGENT_MODEL:-}" ] && model_args=(--model "$AGENT_MODEL")

    # --continue resumes the most recent session in the mounted state directory. That
    # directory is keyed per (agent, issue, run-id) by ci-session-store.sh and therefore
    # holds exactly one session, which is what makes "most recent" unambiguous. A shared
    # directory accumulating every attempt on an issue would resume an arbitrary one.
    resume_args=()
    [ "$RESUME" = "1" ] && resume_args=(--continue)

    # --output-format=stream-json emits one JSON event per line as the turn unfolds,
    # so `tee` captures the transcript incrementally. The default `text` format buffers
    # and prints only on a clean exit: when a run is killed by `timeout` (SIGTERM) it
    # flushes nothing, which is why a hung fixer left a 148-byte, transcript-less
    # artifact and could not be diagnosed. Streaming means even a killed run leaves a
    # partial log that shows where it stalled. `--verbose` is required alongside it.
    timeout "$TIMEOUT" claude -p \
      --dangerously-skip-permissions \
      --permission-mode=bypassPermissions \
      "${resume_args[@]}" \
      "${model_args[@]}" \
      --verbose \
      --output-format=stream-json \
      "$PROMPT_BODY" \
      < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    ;;
  codex)
    # Codex does NOT honour OPENAI_BASE_URL. Left unconfigured it dials
    # wss://api.openai.com/v1/responses directly and fails with 401 against a proxy
    # placeholder key. Pointing it at LiteLLM requires a provider block in config.toml.
    mkdir -p "$HOME/.codex"
    cat > "$HOME/.codex/config.toml" <<EOF
model = "${AGENT_MODEL:-gpt-5.5}"
model_provider = "litellm"

[model_providers.litellm]
name = "LiteLLM"
base_url = "${OPENAI_BASE_URL:?OPENAI_BASE_URL must be set for the codex path}"
env_key = "OPENAI_API_KEY"
EOF

    # The model is selected by config.toml above; passing --model as well would
    # override the provider-qualified default.
    model_args=()

    # `codex exec resume --last` picks the most recent rollout under the mounted
    # sessions directory — unambiguous for the same reason as claude's --continue.
    if [ "$RESUME" = "1" ]; then
      timeout "$TIMEOUT" codex exec resume --last \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY" \
        < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    else
      timeout "$TIMEOUT" codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY" \
        < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    fi
    ;;
  *)
    echo "::error::run-in-container: unknown CI_AGENT '$CI_AGENT'"
    exit 1
    ;;
esac

# An agent that ran to completion but produced nothing is a failure, not a silent pass.
# Letting it through would hand the judge an empty reviewer set, and the fail-closed
# contract would turn that into a confusing pipeline_error rather than a clear one here.
#
# Not every caller works that way. The resolver's deliverable is a pull request, not a
# JSON artifact — its prompt never asks for one — so it tripped this check on every run,
# including the successful ones. A resolver that had just opened a good PR still reported
# `failure`, which then made its own "did the agent succeed?" reporting meaningless and
# took the transcript upload down with it.
if [ "${EXPECT_RESULT:-1}" = "1" ]; then
  if [ ! -s "$RESULT_PATH" ]; then
    echo "::error::run-in-container: ${REVIEW_ROLE:-agent} did not produce $RESULT_PATH"
    exit 1
  fi
  echo "run-in-container: ${REVIEW_ROLE:-agent} produced $RESULT_PATH"
else
  echo "run-in-container: ${REVIEW_ROLE:-agent} completed; no JSON artifact expected"
fi
