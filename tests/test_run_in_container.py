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

The whole thing is offline: a fake `claude` on PATH stands in for the CLI, and
`AGENT_TIMEOUT` lets the deadline be a couple of seconds instead of minutes. Nothing
here touches the network or anything outside tmp_path.
"""

from __future__ import annotations

import subprocess
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
) -> subprocess.CompletedProcess[str]:
    """Invoke run-in-container.sh against a fake `claude`, in an isolated tmp cwd.

    `model` mirrors the composite's now-mandatory `AGENT_MODEL`; pass None to exercise the
    caller that forgot to pin one (D-ci-model-pinning).
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


def test_the_codex_path_writes_the_pinned_model_into_its_config(tmp_path: Path) -> None:
    """The positive half: codex takes its model from config.toml, not from `--model`.

    Without this, `test_an_unpinned_model_is_refused_on_the_codex_path` would still pass if
    the branch stopped honouring AGENT_MODEL entirely.
    """
    r = _run_codex(tmp_path, model="gpt-5.6-luna")
    assert r.returncode == 0, r.stderr
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert 'model = "gpt-5.6-luna"' in config
