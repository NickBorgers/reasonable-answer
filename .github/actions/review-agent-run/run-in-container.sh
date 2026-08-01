#!/usr/bin/env bash
#
# Runs inside the CI agent image. Mounted read-only from the action checkout, not from
# the PR workspace, so a pull request cannot modify the script that reviews it.
#
# Contract with the calling composite:
#   in  — REVIEW_PROMPT_PATH, RESULT_PATH, OUTPUT_LOG_PATH, CI_AGENT, AGENT_MODEL,
#         AGENT_TIMEOUT_MINUTES
#   out — a JSON artifact at RESULT_PATH, conforming to schema/reviewer-v1.json

set -euo pipefail

: "${CI_AGENT:?CI_AGENT must be set}"
# Required for both agents, not just codex. Every role pins its model (D-ci-model-pinning);
# an unset one used to mean "whatever the CLI defaults to", which is precisely the silent
# re-composition of the review panel that decision exists to stop. Guarded here rather than
# per-branch so the two paths cannot drift into different answers for the same mistake.
: "${AGENT_MODEL:?AGENT_MODEL must be set: every role pins its model}"
: "${REVIEW_PROMPT_PATH:?REVIEW_PROMPT_PATH must be set}"
: "${RESULT_PATH:?RESULT_PATH must be set}"
: "${OUTPUT_LOG_PATH:?OUTPUT_LOG_PATH must be set}"

# Duration string handed to coreutils `timeout`. The composite always sets minutes;
# AGENT_TIMEOUT is an override seam the offline tests use to pass a short duration like
# `2s`, since even the smallest useful minute value is far too slow for a unit test.
TIMEOUT="${AGENT_TIMEOUT:-${AGENT_TIMEOUT_MINUTES:-30}m}"

# How long after the deadline's SIGTERM to escalate to SIGKILL. A wedged CLI that traps
# or ignores SIGTERM would otherwise sit until the job's own timeout; --kill-after makes
# the deadline enforceable. The escalated kill surfaces as exit 137, handled alongside
# 124 below.
KILL_AFTER="${AGENT_KILL_AFTER:-30s}"

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

# A resumed session that wedges must be both distinguishable from a crash and contained,
# because the whole point of the cold fallback is that a hung resume hands off to a fresh
# agent instead of leaving the PR unfixed. Containment lives here, at the boundary that
# actually fails: an expression-valued `continue-on-error` on a `uses:` composite step
# does not reliably keep an inner-step failure from aborting the job. The sentinel is the
# signal the calling workflow reads to decide whether to fall back; it sits beside the
# output log under .review-output, which is bind-mounted back to the host. Clear any
# stale one from a prior attempt in the same mounted dir before running.
#
# It marks "this resume did not complete" for *every* contained reason — deadline, crash,
# or a clean exit with no artifact — and names the reason inside. An earlier draft
# signalled only the deadline and left a crash to be inferred from a missing result, but a
# resumed agent that writes `fixer-result.json` and *then* exits nonzero leaves a result
# behind, so the inference read that crash as a success and skipped the fallback the docs
# promise. A positive signal for every contained path removes the inference.
#
# Its stem is taken from OUTPUT_LOG_PATH rather than REVIEW_ROLE so it always matches the
# log and result it sits beside: the composite builds those from ARTIFACT_BASE
# (`${RESULT_BASENAME:-$ROLE}`), which is deliberately allowed to differ from the role.
# Were they to diverge, a role-named sentinel would stop matching the name the workflow
# looks for and the fallback would silently stop firing — which is this whole bug again.
INCOMPLETE_SENTINEL_PATH="${OUTPUT_LOG_PATH%-output.log}-incomplete.sentinel"
rm -f "$INCOMPLETE_SENTINEL_PATH"

# Records why a resume was contained, for the workflow to read and log. Only ever called
# on the resume path; every other mode keeps its old fatal behaviour.
mark_incomplete() {
  printf '%s\n' "$1" > "$INCOMPLETE_SENTINEL_PATH"
}

# The model is named here, not just the agent: `agent` selects only a *family*, and which
# checkpoint ran inside it is the thing a verdict has to be attributable to (D-ci-model-pinning).
echo "run-in-container: ${REVIEW_ROLE:-agent} via ${CI_AGENT} on ${AGENT_MODEL}, timeout ${TIMEOUT}, resume=${RESUME}"

# `timeout`'s own exit status, captured per-branch below via PIPESTATUS[0] so a deadline
# is told apart from a crash. Defaulted here so the interpretation block after the case
# is total even on a path that somehow leaves it unset.
rc=0

# `< /dev/null` matters: without it the CLIs wait on stdin and hang until the timeout.
case "$CI_AGENT" in
  claude)
    model_args=(--model "$AGENT_MODEL")

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
    # `set +e` so pipefail cannot abort before PIPESTATUS is read; rc is `timeout`'s
    # status, not tee's, which is what makes 124/137 (deadline) distinct from a crash.
    set +e
    timeout --kill-after="$KILL_AFTER" "$TIMEOUT" claude -p \
      --dangerously-skip-permissions \
      --permission-mode=bypassPermissions \
      "${resume_args[@]}" \
      "${model_args[@]}" \
      --verbose \
      --output-format=stream-json \
      "$PROMPT_BODY" \
      < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    rc=${PIPESTATUS[0]}
    set -e
    ;;
  codex)
    # Codex does NOT honour OPENAI_BASE_URL. Left unconfigured it dials
    # wss://api.openai.com/v1/responses directly and fails with 401 against a proxy
    # placeholder key. Pointing it at LiteLLM requires a provider block in config.toml.
    mkdir -p "$HOME/.codex"
    cat > "$HOME/.codex/config.toml" <<EOF
model = "${AGENT_MODEL}"
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
    # Same PIPESTATUS discipline as the claude branch: rc is `timeout`'s own status.
    if [ "$RESUME" = "1" ]; then
      set +e
      timeout --kill-after="$KILL_AFTER" "$TIMEOUT" codex exec resume --last \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY" \
        < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
      rc=${PIPESTATUS[0]}
      set -e
    else
      set +e
      timeout --kill-after="$KILL_AFTER" "$TIMEOUT" codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY" \
        < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
      rc=${PIPESTATUS[0]}
      set -e
    fi
    ;;
  *)
    echo "::error::run-in-container: unknown CI_AGENT '$CI_AGENT'"
    exit 1
    ;;
esac

# ── Interpret the exit code ──────────────────────────────────────────────────
# A timeout is the failure this containment exists for: `timeout` exits 124 when it
# sends SIGTERM at the deadline, and 137 when --kill-after escalates to SIGKILL because
# the agent ignored SIGTERM. In resume mode a wedged session must hand control to the
# cold fallback rather than fail the job, so record the sentinel and exit 0. In any other
# mode (cold fixer, reviewers, resolver, author) there is no fallback, so a timeout stays
# fatal, exactly as before.
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
  echo "::warning::run-in-container: ${REVIEW_ROLE:-agent} exceeded ${TIMEOUT} (rc=${rc})"
  if [ "$RESUME" = "1" ]; then
    mark_incomplete "timeout after ${TIMEOUT} (rc=${rc})"
    echo "run-in-container: resume timed out; wrote $(basename "$INCOMPLETE_SENTINEL_PATH") and exiting 0 so the cold fallback runs"
    exit 0
  fi
  exit "$rc"
elif [ "$rc" -ne 0 ]; then
  # A crash — any other nonzero. In resume mode it is still best-effort, but it is marked
  # rather than inferred: an agent that wrote its result and *then* died leaves a result
  # behind, so "no result" cannot stand in for "crashed". A nonzero exit means the agent
  # did not vouch for whatever it left on disk, and that must not be pushed as a fix.
  # Elsewhere it stays fatal.
  echo "::error::run-in-container: ${REVIEW_ROLE:-agent} exited ${rc}"
  if [ "$RESUME" = "1" ]; then
    mark_incomplete "agent exited ${rc}"
    echo "run-in-container: resume exited ${rc}; exiting 0 so the cold fallback runs"
    exit 0
  fi
  exit "$rc"
fi

# An agent that ran to completion but produced nothing is a failure, not a silent pass.
# Letting it through would hand the judge an empty reviewer set, and the fail-closed
# contract would turn that into a confusing pipeline_error rather than a clear one here.
#
# Not every caller works that way. The resolver's deliverable is a pull request, not a
# JSON artifact — its prompt never asks for one — so it tripped this check on every run,
# including the successful ones. A resolver that had just opened a good PR still reported
# `failure`, which then made its own "did the agent succeed?" reporting meaningless and
# took the transcript upload down with it.
#
# In resume mode this is best-effort as well: a clean exit that left no artifact is not
# fatal, because the cold fallback is precisely the recovery for it. Exit 0 and let the
# workflow's result-presence check route to the cold fixer.
if [ "${EXPECT_RESULT:-1}" = "1" ]; then
  if [ ! -s "$RESULT_PATH" ]; then
    if [ "$RESUME" = "1" ]; then
      mark_incomplete "clean exit with no ${RESULT_PATH}"
      echo "::warning::run-in-container: ${REVIEW_ROLE:-agent} resumed cleanly but produced no $RESULT_PATH; the cold fallback will run"
      exit 0
    fi
    echo "::error::run-in-container: ${REVIEW_ROLE:-agent} did not produce $RESULT_PATH"
    exit 1
  fi
  echo "run-in-container: ${REVIEW_ROLE:-agent} produced $RESULT_PATH"
else
  echo "run-in-container: ${REVIEW_ROLE:-agent} completed; no JSON artifact expected"
fi
