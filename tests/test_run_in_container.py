"""Tests for .github/actions/review-agent-run/run-in-container.sh.

The script runs a coding-agent CLI under a `timeout` inside the CI image. Issue #85 was
that a hung author-resume session (the CLI wedged before its first token, dying at the
25-minute deadline) failed the whole job instead of falling through to the cold fixer:
`continue-on-error` on the `uses:` composite step did not contain the failure, and a
timeout was indistinguishable from a crash.

Containment now lives at this boundary. These pin the contract the calling workflow
depends on:

- In **resume mode**, a timeout is contained — the script writes a `*-timeout.sentinel`
  and exits 0 — so the job survives to run the cold fallback. A crash and a clean-but-
  empty run are contained the same way (no sentinel; the missing result is the signal).
- In **any non-resume mode** (cold fixer, reviewers, resolver, author) there is no
  fallback, so a timeout or a missing result stays fatal, exactly as before.
- In resume mode a **silent** agent is killed on an idle deadline far short of the outer
  one, so the fallback starts in the first minutes rather than the last (D-resume-stall-guard) — and an
  agent that is quiet because it is working is not killed at all.

The whole thing is offline: a fake `claude` on PATH stands in for the CLI, and
`AGENT_TIMEOUT` / `AGENT_FIRST_OUTPUT_TIMEOUT` let deadlines be seconds instead of
minutes. Nothing here touches the network or anything outside tmp_path.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "actions"
    / "review-agent-run"
    / "run-in-container.sh"
)

# The relative paths the composite hands the script; they resolve against the run cwd,
# which the helper pins to tmp_path.
RESULT_REL = ".review-output/fixer-result.json"
OUTPUT_LOG_REL = ".review-output/fixer-output.log"
SENTINEL_REL = ".review-output/fixer-incomplete.sentinel"
STALL_FLAG_REL = ".review-output/fixer-stalled.flag"


def _write_fake_claude(bin_dir: Path, body: str) -> None:
    """Drop an executable `claude` shim on PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "claude"
    fake.write_text("#!/usr/bin/env bash\n" + body)
    fake.chmod(0o755)


def _run(
    tmp_path: Path,
    *,
    claude_body: str,
    resume: bool,
    timeout: str = "2s",
    model: str | None = "claude-sonnet-5",
    first_output: str | None = None,
    stall: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke run-in-container.sh against a fake `claude`, in an isolated tmp cwd.

    `model` mirrors the composite's now-mandatory `AGENT_MODEL`; pass None to exercise the
    caller that forgot to pin one (D-ci-model-pinning).

    `first_output` / `stall` are the stall guard's two idle deadlines, in seconds. They are
    left unset for every test that is not about the guard, so those keep exercising the
    plain deadline path.
    """
    bin_dir = tmp_path / "bin"
    _write_fake_claude(bin_dir, claude_body)

    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the thing\n")

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI_AGENT": "claude",
        "REVIEW_ROLE": "fixer",
        "REVIEW_PROMPT_PATH": str(prompt),
        "RESULT_PATH": RESULT_REL,
        "OUTPUT_LOG_PATH": OUTPUT_LOG_REL,
        "AGENT_TIMEOUT": timeout,
        "AGENT_KILL_AFTER": "1s",
        "AGENT_RESUME": "1" if resume else "0",
        # A 1s tick keeps a deadline of a few seconds meaningful; production polls at 5s.
        "AGENT_STALL_POLL": "1",
    }
    if model is not None:
        env["AGENT_MODEL"] = model
    if first_output is not None:
        env["AGENT_FIRST_OUTPUT_TIMEOUT"] = first_output
    if stall is not None:
        env["AGENT_STALL_TIMEOUT"] = stall
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )


# A CLI that never returns — the exact failure #85 describes, a session wedged before its
# first token. `exec` so `timeout`'s SIGTERM lands on the sleep directly.
HANGS = "exec sleep 30\n"
# Writes the result artifact the prompt asks for, then exits cleanly.
SUCCEEDS = 'mkdir -p "$(dirname "$RESULT_PATH")"; printf \'{"ok":true}\' > "$RESULT_PATH"\n'
# Exits nonzero without producing anything.
CRASHES = "exit 5\n"
# Returns cleanly but leaves no artifact behind.
EMPTY_CLEAN = "exit 0\n"
# Writes the artifact and *then* dies: the case where "no result" cannot detect a crash.
WRITES_THEN_CRASHES = (
    'mkdir -p "$(dirname "$RESULT_PATH")"; printf \'{"ok":true}\' > "$RESULT_PATH"; exit 5\n'
)
# Succeeds, and records the argv it was called with. The other shims discard their arguments,
# so nothing they do can tell whether a flag actually reached the CLI.
ARGV_REL = "argv.txt"
RECORDS_ARGV = (
    f'printf "%s\\n" "$@" > {ARGV_REL}; '
    'mkdir -p "$(dirname "$RESULT_PATH")"; printf \'{"ok":true}\' > "$RESULT_PATH"\n'
)


# ── resume mode: every failure is contained so the cold fallback can run ──────


def test_resume_timeout_is_contained_and_writes_sentinel(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=HANGS, resume=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / SENTINEL_REL).exists(), "a contained resume timeout must leave the sentinel"


def test_resume_crash_is_contained_and_marked(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=CRASHES, resume=True)
    assert r.returncode == 0, r.stderr
    # Marked, not inferred from a missing result: see the test below for why absence
    # cannot stand in for a crash.
    assert (tmp_path / SENTINEL_REL).exists()
    assert "exited 5" in (tmp_path / SENTINEL_REL).read_text()


def test_a_crash_that_left_a_result_behind_is_still_marked(tmp_path: Path) -> None:
    """The signal cannot be "no result": an agent that writes its artifact and *then*
    dies leaves one behind, and reading that as a fix would push work the agent never
    vouched for — the cold fallback exists precisely for this."""
    r = _run(tmp_path, claude_body=WRITES_THEN_CRASHES, resume=True, timeout="30s")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / RESULT_REL).exists(), "the partial result is kept for diagnosis"
    assert (tmp_path / SENTINEL_REL).exists(), "a crash must be marked even with a result"


def test_resume_clean_but_empty_is_contained(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=EMPTY_CLEAN, resume=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / SENTINEL_REL).exists()
    assert not (tmp_path / RESULT_REL).exists()


# ── the stall guard: a silent resume dies in seconds, not at the deadline ─────
#
# Containment made a hung resume survivable; it did not make it cheap. Every observed hang
# emitted zero stream-json events and then sat until the 25-minute deadline, so the cold
# fixer that does the actual work started 25 minutes late and the PR's fix cycle cost close
# to an hour (D-resume-stall-guard). The guard measures idleness rather than shortening the
# total budget, because a resume that is genuinely working needs the same time a cold fixer
# gets. These pin both deadlines, and — more importantly — pin that a working agent is not
# killed by either.

# Never speaks, never returns: the observed wedge, and what the first-output deadline is for.
SILENT_HANG = "exec sleep 60\n"
# Speaks once, then wedges. The first-output deadline must not apply to this; the (longer)
# mid-run deadline must.
SPEAKS_THEN_HANGS = 'echo \'{"type":"system"}\'\nexec sleep 60\n'
# Quiet for a while — a long tool call, e.g. a test suite — then finishes properly.
QUIET_THEN_SUCCEEDS = (
    "sleep 4\n"
    'echo \'{"type":"result"}\'\n'
    'mkdir -p "$(dirname "$RESULT_PATH")"; printf \'{"ok":true}\' > "$RESULT_PATH"\n'
)


def test_a_silent_resume_is_killed_long_before_the_deadline(tmp_path: Path) -> None:
    started = time.monotonic()
    r = _run(tmp_path, claude_body=SILENT_HANG, resume=True, timeout="60s", first_output="3")
    elapsed = time.monotonic() - started

    assert r.returncode == 0, r.stderr
    assert (tmp_path / STALL_FLAG_REL).exists(), "the kill's cause must be recorded, not inferred"
    assert "first token" in (tmp_path / STALL_FLAG_REL).read_text()
    # The whole point: the cold fallback starts now rather than at the outer deadline.
    assert elapsed < 30, f"the guard did not fire; the run took {elapsed:.1f}s of its 60s budget"
    # And it still routes through the same containment the fallback reads.
    assert (tmp_path / SENTINEL_REL).exists()
    assert "stalled" in (tmp_path / SENTINEL_REL).read_text()


def test_the_first_output_deadline_stops_applying_once_the_agent_speaks(tmp_path: Path) -> None:
    """One byte of output moves the agent onto the generous deadline.

    Without this, a single threshold would have to be either too tight for a long tool call
    or too loose to catch the wedge, and the wedge is the case with evidence behind it.
    """
    started = time.monotonic()
    r = _run(
        tmp_path,
        claude_body=SPEAKS_THEN_HANGS,
        resume=True,
        timeout="60s",
        first_output="2",
        stall="8",
    )
    elapsed = time.monotonic() - started

    assert r.returncode == 0, r.stderr
    assert "mid-run" in (tmp_path / STALL_FLAG_REL).read_text()
    assert elapsed > 4, "the first-output deadline killed an agent that had already spoken"
    # The transcript before the stall is what a hang is diagnosed from, so it must survive
    # the kill rather than being lost in tee's buffer.
    assert '{"type":"system"}' in (tmp_path / OUTPUT_LOG_REL).read_text()


def test_a_quiet_but_working_resume_is_left_alone(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=QUIET_THEN_SUCCEEDS, resume=True, timeout="60s", first_output="15")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / RESULT_REL).exists()
    assert not (tmp_path / STALL_FLAG_REL).exists()
    assert not (tmp_path / SENTINEL_REL).exists()


def test_the_stall_guard_does_not_apply_outside_resume_mode(tmp_path: Path) -> None:
    """Reviewers, the cold fixer, the resolver and the author keep their old behaviour.

    None of them has a fallback to hand off to, so an idle-kill there would turn a slow
    tool call into a failed job. The deadline they already had stays their only bound.
    """
    r = _run(tmp_path, claude_body=QUIET_THEN_SUCCEEDS, resume=False, timeout="60s", first_output="1")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / RESULT_REL).exists()
    assert not (tmp_path / STALL_FLAG_REL).exists()


def test_a_stale_stall_flag_is_cleared_before_running(tmp_path: Path) -> None:
    """Same hazard as the stale sentinel: .review-output is a mounted dir that outlives a
    single attempt, and a leftover flag would report a fresh success as a stall."""
    (tmp_path / ".review-output").mkdir(parents=True)
    (tmp_path / STALL_FLAG_REL).write_text("stale\n")
    r = _run(tmp_path, claude_body=SUCCEEDS, resume=True, timeout="30s")
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / STALL_FLAG_REL).exists()
    assert not (tmp_path / SENTINEL_REL).exists()


# ── non-resume mode: no fallback exists, so failures stay fatal ───────────────


def test_cold_timeout_is_fatal(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=HANGS, resume=False)
    assert r.returncode != 0, "a cold timeout has no fallback and must fail the job"
    # Containment is resume-only: a cold run must not silently swallow the deadline.
    assert not (tmp_path / SENTINEL_REL).exists()


def test_cold_crash_is_fatal(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=CRASHES, resume=False)
    assert r.returncode == 5, "a cold crash must propagate its exit code"


def test_cold_clean_but_empty_is_fatal(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=EMPTY_CLEAN, resume=False)
    assert r.returncode != 0, "a cold run that produced no artifact must fail"


# ── success on either path leaves a result and no sentinel ────────────────────


def test_success_leaves_a_result_and_no_sentinel(tmp_path: Path) -> None:
    for resume in (True, False):
        r = _run(tmp_path, claude_body=SUCCEEDS, resume=resume, timeout="30s")
        assert r.returncode == 0, r.stderr
        assert (tmp_path / RESULT_REL).exists()
        assert not (tmp_path / SENTINEL_REL).exists()


def test_a_stale_sentinel_is_cleared_before_running(tmp_path: Path) -> None:
    """A leftover sentinel from a prior attempt in a mounted dir must not read as a fresh
    timeout when the resume then succeeds."""
    (tmp_path / ".review-output").mkdir(parents=True)
    (tmp_path / SENTINEL_REL).write_text("stale")
    r = _run(tmp_path, claude_body=SUCCEEDS, resume=True, timeout="30s")
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / SENTINEL_REL).exists()


# ── an unpinned model is refused on both paths, before any CLI is invoked ─────
#
# The script used to substitute a default when AGENT_MODEL was unset — `gpt-5.5` inline on
# the codex path, and on the claude path simply omitting `--model` so the CLI chose. Both
# meant a role could run a checkpoint nobody selected (D-ci-model-pinning). These prove the
# refusal by *running* it, rather than asserting the guard's own source text, and they cover
# the codex branch, which had no behavioural coverage at all before.


def _run_codex(tmp_path: Path, *, model: str | None) -> subprocess.CompletedProcess[str]:
    """Drive the codex branch with a fake `codex` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "codex"
    fake.write_text("#!/usr/bin/env bash\n" + SUCCEEDS)
    fake.chmod(0o755)

    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the thing\n")

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI_AGENT": "codex",
        "REVIEW_ROLE": "fixer",
        "REVIEW_PROMPT_PATH": str(prompt),
        "RESULT_PATH": RESULT_REL,
        "OUTPUT_LOG_PATH": OUTPUT_LOG_REL,
        "AGENT_TIMEOUT": "30s",
        "AGENT_KILL_AFTER": "1s",
        "AGENT_RESUME": "0",
        # The codex branch writes a provider block and refuses without a base URL.
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1/",
    }
    if model is not None:
        env["AGENT_MODEL"] = model
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )


def test_an_unpinned_model_is_refused_on_the_claude_path(tmp_path: Path) -> None:
    r = _run(tmp_path, claude_body=SUCCEEDS, resume=False, timeout="30s", model=None)
    assert r.returncode != 0
    assert "AGENT_MODEL" in r.stderr
    # Refused before the CLI ran at all: no artifact, so nothing downstream can mistake this
    # for a completed review.
    assert not (tmp_path / RESULT_REL).exists()


def test_an_unpinned_model_is_refused_on_the_codex_path(tmp_path: Path) -> None:
    r = _run_codex(tmp_path, model=None)
    assert r.returncode != 0
    assert "AGENT_MODEL" in r.stderr
    assert not (tmp_path / RESULT_REL).exists()
    # And no config was left behind naming some fallback model.
    assert not (tmp_path / ".codex" / "config.toml").exists()


def test_the_claude_path_forwards_the_pinned_model_to_the_cli(tmp_path: Path) -> None:
    """The positive half for claude, mirroring the codex config.toml test below.

    The refusal test above fires on the *shared* top-level `AGENT_MODEL` guard, so it would
    still pass if this branch reverted to `model_args=()` — silently restoring the
    CLI-default-following behaviour D-ci-model-pinning exists to remove, on both Claude
    reviewer roles. Asserting the flag reaches the CLI is what closes that.
    """
    r = _run(
        tmp_path,
        claude_body=RECORDS_ARGV,
        resume=False,
        timeout="30s",
        model="claude-sonnet-5",
    )
    assert r.returncode == 0, r.stderr
    argv = (tmp_path / ARGV_REL).read_text().splitlines()
    assert "--model" in argv, f"--model never reached the CLI; argv was {argv}"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"


def test_the_codex_path_writes_the_pinned_model_into_its_config(tmp_path: Path) -> None:
    """The positive half: codex takes its model from config.toml, not from `--model`.

    Without this, `test_an_unpinned_model_is_refused_on_the_codex_path` would still pass if
    the branch stopped honouring AGENT_MODEL entirely.
    """
    r = _run_codex(tmp_path, model="gpt-5.6-luna")
    assert r.returncode == 0, r.stderr
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.6-luna"' in config
