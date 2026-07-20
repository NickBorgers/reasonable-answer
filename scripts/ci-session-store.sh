#!/usr/bin/env bash
#
# Agent session store — moves a coding agent's conversation state between workflow runs.
#
# Why this exists
# ---------------
# When `resolve-issue.yml` opens a PR, the agent that wrote it holds context no
# artifact can reconstruct: which alternatives it rejected, what it deliberately left
# out, why a given shape was chosen. The review pipeline then hands that PR to a fixer.
# A cold fixer reading only reviewer JSON cannot tell "this is a bug" from "the reviewer
# misread the intent" — it has no intent to compare against, so it patches the surface
# of both.
#
# Resuming the original author instead means the fixer answers reviewers from the same
# conversation that produced the code, and can push back on a misread rather than
# writing a bogus fix for it.
#
# Transit model
# -------------
# GitHub Actions artifact. The author run packs its session dir with `pack`, uploads it
# as `author-session-<agent>-<run-id>`, and writes an `Author-Session: <agent>/<run-id>`
# trailer into the PR body. The fixer parses that trailer, downloads the artifact from
# the original run, and `unpack`s it into a fresh job-local dir bind-mounted into the
# resume container.
#
# Job-local dirs (${RUNNER_TEMP:-/tmp}/ci-sessions/...) rather than persistent host
# paths: the homelab runners are ephemeral and there is more than one of them, so a
# session written on runner-3 is simply absent on runner-1. Cross-run state must travel
# through the artifact, never through the host filesystem.
#
# Keying by (agent, issue, run-id) is load-bearing. Both CLIs resume "the most recent
# session in this directory" — `claude --continue`, `codex exec resume --last`. A dir
# holding exactly one session makes that unambiguous; a dir accumulating every
# /autoresolve attempt on the same issue would silently resume the wrong one.

set -euo pipefail

SESSION_ROOT="${CI_SESSION_ROOT:-${RUNNER_TEMP:-/tmp}/ci-sessions}"

usage() {
  cat <<'EOF'
usage: ci-session-store.sh <command> [args]

  prepare   <agent> <issue> <run-id>          create+echo host session dir
  home-path <agent>                           echo the container mount point
  pack      <agent> <issue> <run-id> <tar>    tar the session dir; echo tar path
  unpack    <agent> <issue> <run-id> <tar>    extract into a fresh dir; echo dir
  validate  <agent> <issue> <run-id>          exit 0 if the dir holds a real session
  parse-trailer <pr-body>                     echo "<agent>\t<run-id>" from the trailer

agent is `claude` or `codex`.
EOF
}

require_agent() {
  case "$1" in
    claude | codex) ;;
    *)
      echo "ci-session-store: unknown agent '$1'" >&2
      return 1
      ;;
  esac
}

session_dir() {
  local agent="$1" issue="$2" run_id="$3"
  printf '%s/%s/issue-%s/run-%s' "$SESSION_ROOT" "$agent" "$issue" "$run_id"
}

# Where the session dir must land inside the container.
#
# Codex is deliberately scoped to the `sessions` subdirectory. Mounting over the whole
# of $HOME/.codex shadows the standalone runtime that lives at .codex/packages/, and
# `command -v codex` then fails inside the container with an error that looks nothing
# like a mount problem.
home_path() {
  local agent="$1"
  case "$agent" in
    claude) printf '/home/ci/.claude' ;;
    codex) printf '/home/ci/.codex/sessions' ;;
  esac
}

cmd_prepare() {
  local agent="$1" issue="$2" run_id="$3"
  require_agent "$agent"
  local dir
  dir="$(session_dir "$agent" "$issue" "$run_id")"
  mkdir -p "$dir"
  # The container runs as uid 1000 and the runner as a different uid; without this the
  # agent cannot write its own session state into the mount.
  chmod -R a+rwX "$dir"
  printf '%s\n' "$dir"
}

cmd_home_path() {
  local agent="$1"
  require_agent "$agent"
  home_path "$agent"
  printf '\n'
}

cmd_pack() {
  local agent="$1" issue="$2" run_id="$3" tar_path="$4"
  require_agent "$agent"
  local dir
  dir="$(session_dir "$agent" "$issue" "$run_id")"

  if [ ! -d "$dir" ]; then
    echo "ci-session-store: nothing to pack, $dir does not exist" >&2
    return 1
  fi
  # An empty session dir packs to a valid tarball that unpacks to nothing, and the
  # failure then surfaces two workflow runs later as an inexplicable resume fallback.
  # Refuse here, where the cause is still visible.
  if [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then
    echo "ci-session-store: refusing to pack an empty session dir $dir" >&2
    return 1
  fi

  mkdir -p "$(dirname "$tar_path")"
  tar -czf "$tar_path" -C "$dir" .
  printf '%s\n' "$tar_path"
}

cmd_unpack() {
  local agent="$1" issue="$2" run_id="$3" tar_path="$4"
  require_agent "$agent"

  if [ ! -f "$tar_path" ]; then
    echo "ci-session-store: tar not found at $tar_path" >&2
    return 1
  fi

  local dir
  dir="$(session_dir "$agent" "$issue" "$run_id")"
  rm -rf "$dir"
  mkdir -p "$dir"
  tar -xzf "$tar_path" -C "$dir"
  # The tar carries ownership from the container uid on whichever runner packed it, and
  # is being extracted on a different ephemeral runner by a different uid. Without this
  # the resume container cannot read its own prior session.
  chmod -R a+rwX "$dir"
  printf '%s\n' "$dir"
}

# A downloaded artifact is not proof of a usable session: an author run that died early
# can upload a directory holding only empty scaffolding. The fixer falls back to a cold
# run when this fails, so a false positive here costs a wasted resume attempt and a
# confusing fixer, while a false negative only costs the context benefit.
cmd_validate() {
  local agent="$1" issue="$2" run_id="$3"
  require_agent "$agent"
  local dir
  dir="$(session_dir "$agent" "$issue" "$run_id")"

  [ -d "$dir" ] || {
    echo "ci-session-store: $dir missing" >&2
    return 1
  }

  case "$agent" in
    claude)
      # Claude keeps per-project transcripts under projects/<slug>/<uuid>.jsonl.
      if ! find "$dir/projects" -name '*.jsonl' -type f -size +0c 2>/dev/null | grep -q .; then
        echo "ci-session-store: no non-empty claude transcript under $dir/projects" >&2
        return 1
      fi
      ;;
    codex)
      # Codex writes rollout-*.jsonl under a date-partitioned tree.
      if ! find "$dir" -name '*.jsonl' -type f -size +0c 2>/dev/null | grep -q .; then
        echo "ci-session-store: no non-empty codex rollout under $dir" >&2
        return 1
      fi
      ;;
  esac
}

# Anchored and enumerated on purpose. The PR body is user-editable text, and this value
# selects an artifact to download and a directory to mount into a container that holds
# a write-capable PAT. A loose match here is a supply-chain problem, not a parsing bug.
cmd_parse_trailer() {
  local body="$1"
  local line
  line="$(printf '%s\n' "$body" |
    grep -Eim1 '^Author-Session:[[:space:]]+(codex|claude)/[0-9]+[[:space:]]*$' || true)"

  [ -n "$line" ] || return 1

  local value agent run_id
  value="$(printf '%s' "$line" | sed -E 's/^[Aa]uthor-[Ss]ession:[[:space:]]+//; s/[[:space:]]*$//')"
  agent="${value%%/*}"
  run_id="${value##*/}"

  require_agent "$agent"
  printf '%s\t%s\n' "$agent" "$run_id"
}

main() {
  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    prepare)
      [ $# -eq 3 ] || {
        usage >&2
        exit 2
      }
      cmd_prepare "$@"
      ;;
    home-path)
      [ $# -eq 1 ] || {
        usage >&2
        exit 2
      }
      cmd_home_path "$@"
      ;;
    pack)
      [ $# -eq 4 ] || {
        usage >&2
        exit 2
      }
      cmd_pack "$@"
      ;;
    unpack)
      [ $# -eq 4 ] || {
        usage >&2
        exit 2
      }
      cmd_unpack "$@"
      ;;
    validate)
      [ $# -eq 3 ] || {
        usage >&2
        exit 2
      }
      cmd_validate "$@"
      ;;
    parse-trailer)
      [ $# -eq 1 ] || {
        usage >&2
        exit 2
      }
      cmd_parse_trailer "$@"
      ;;
    -h | --help | help | '')
      usage
      ;;
    *)
      echo "ci-session-store: unknown command '$cmd'" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
