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

# ── Stall guard: resume only ─────────────────────────────────────────────────
#
# The wedge this contains is not slowness, it is silence. Every observed hung resume
# produced *zero* stream-json events and then died at the 25-minute deadline, so the whole
# budget was spent proving something that was already knowable in the first minute — and
# the cold fallback only started 25 minutes late (D-resume-stall-guard).
#
# The fix is not a shorter total budget: a resume that is genuinely working needs the same
# time a cold fixer gets, and cutting the deadline to "long enough to notice a hang" would
# kill working resumes mid-fix. What is diagnostic is *idleness*, so that is what is
# measured — the output log's growth, which stream-json makes incremental.
#
# Two thresholds, because the two silences mean different things. Before the first byte the
# CLI has not started a turn at all; that is the observed failure and seconds are already
# abnormal, so the deadline is tight. After output has started, silence means a tool call
# is running — a test suite, a dependency sync — and legitimately long, so the deadline is
# generous. Both are far below the outer `timeout`, which stays as the backstop for a
# process that is spinning noisily rather than sitting idle.
STALL_FLAG_PATH="${OUTPUT_LOG_PATH%-output.log}-stalled.flag"
rm -f "$STALL_FLAG_PATH"

FIRST_OUTPUT_TIMEOUT="${AGENT_FIRST_OUTPUT_TIMEOUT:-180}"
STALL_TIMEOUT="${AGENT_STALL_TIMEOUT:-600}"
STALL_POLL="${AGENT_STALL_POLL:-5}"

# Watches the log beside the running agent and kills it when it goes quiet for too long.
# Writes the flag *before* killing, so the reason survives the exit code: a stall-kill
# arrives as 143/137, which is otherwise indistinguishable from the outer deadline.
watch_for_stall() {
  local pid="$1"
  local idle=0 last=0 size limit

  while kill -0 "$pid" 2>/dev/null; do
    sleep "$STALL_POLL"
    size=$(wc -c < "$OUTPUT_LOG_PATH" 2>/dev/null || echo 0)
    if [ "${size:-0}" -gt "$last" ]; then
      last="$size"
      idle=0
      continue
    fi

    idle=$((idle + STALL_POLL))
    limit="$STALL_TIMEOUT"
    [ "$last" -eq 0 ] && limit="$FIRST_OUTPUT_TIMEOUT"
    [ "$idle" -ge "$limit" ] || continue

    if [ "$last" -eq 0 ]; then
      printf 'no output at all within %ss — wedged before its first token\n' "$limit" \
        > "$STALL_FLAG_PATH"
    else
      printf 'no output for %ss — stalled mid-run\n' "$limit" > "$STALL_FLAG_PATH"
    fi
    # TERM the `timeout` wrapper, which relays it to the CLI; escalate if it is ignored,
    # for the same reason `timeout` itself carries --kill-after.
    kill -TERM "$pid" 2>/dev/null || true
    sleep 5
    kill -KILL "$pid" 2>/dev/null || true
    return 0
  done
}

# Runs the agent command in "$@" and leaves `timeout`'s own exit status in `rc`.
#
# The non-resume path is deliberately byte-for-byte the old pipeline — reviewers, the cold
# fixer, the resolver and the author all keep their existing behaviour, and none of them has
# a fallback for a guard to hand off to. Only a resume gets the watchdog, and it needs the
# CLI as a *tracked* child rather than the head of a pipeline, so the log is written through
# a process substitution instead of `| tee`. `rc` is then `timeout`'s status directly, which
# is the same value PIPESTATUS[0] carried before.
run_agent() {
  if [ "$RESUME" != "1" ]; then
    set +e
    "$@" < /dev/null 2>&1 | tee "$OUTPUT_LOG_PATH"
    rc=${PIPESTATUS[0]}
    set -e
    return 0
  fi

  # The watchdog reads this file's size from its first tick; create it so an absent file
  # is never confused with "no output yet".
  : > "$OUTPUT_LOG_PATH"

  local agent_pid watch_pid
  set +e
  "$@" < /dev/null > >(tee "$OUTPUT_LOG_PATH") 2>&1 &
  agent_pid=$!
  watch_for_stall "$agent_pid" &
  watch_pid=$!
  wait "$agent_pid"
  rc=$?
  set -e

  kill "$watch_pid" 2>/dev/null || true
  wait "$watch_pid" 2>/dev/null || true

  # `wait` covers the agent, not the `tee` behind the process substitution, and bash does
  # not reap process substitutions. Whatever tee has buffered but not yet written is the
  # *tail* of the transcript — the last thing the CLI said before it stalled or died, which
  # is exactly the part a hang is diagnosed from. Wait for the file to stop growing, briefly
  # and boundedly: on a kill there is nothing in flight and this returns immediately.
  local settle=0 prev=-1 now
  while [ "$settle" -lt 20 ]; do
    now=$(wc -c < "$OUTPUT_LOG_PATH" 2>/dev/null || echo 0)
    [ "$now" = "$prev" ] && break
    prev="$now"
    settle=$((settle + 1))
    sleep 0.1
  done
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
    # `run_agent` leaves `timeout`'s own status in rc — not tee's — which is what makes
    # 124/137 (deadline) distinct from a crash, and adds the stall guard on a resume.
    run_agent timeout --kill-after="$KILL_AFTER" "$TIMEOUT" claude -p \
      --dangerously-skip-permissions \
      --permission-mode=bypassPermissions \
      "${resume_args[@]}" \
      "${model_args[@]}" \
      --verbose \
      --output-format=stream-json \
      "$PROMPT_BODY"
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
    # Same rc discipline as the claude branch: rc is `timeout`'s own status.
    if [ "$RESUME" = "1" ]; then
      run_agent timeout --kill-after="$KILL_AFTER" "$TIMEOUT" codex exec resume --last \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY"
    else
      run_agent timeout --kill-after="$KILL_AFTER" "$TIMEOUT" codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        "${model_args[@]}" \
        "$PROMPT_BODY"
    fi
    ;;
  *)
    echo "::error::run-in-container: unknown CI_AGENT '$CI_AGENT'"
    exit 1
    ;;
esac

# ── Interpret the exit code ──────────────────────────────────────────────────
# The stall guard is read first, and by its flag rather than by an exit code. Killing the
# agent from outside `timeout` produces 143 (or 137 after the escalation), which says
# nothing about *why* it died — 137 in particular is the same code the outer deadline's
# --kill-after escalation produces. The flag is written before the kill, so it is the only
# statement of cause that survives. It is only ever written on the resume path, so this
# block cannot change any other role's behaviour.
if [ -s "$STALL_FLAG_PATH" ]; then
  STALL_REASON="$(tr -d '\n' < "$STALL_FLAG_PATH")"
  echo "::warning::run-in-container: ${REVIEW_ROLE:-agent} resume stalled — ${STALL_REASON}"
  mark_incomplete "stalled: ${STALL_REASON}"
  echo "run-in-container: killed a stalled resume after far less than ${TIMEOUT}; exiting 0 so the cold fallback runs"
  exit 0
fi

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
