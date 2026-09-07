"""The reviewer's one retry, driven as shell against a fake `docker`.

A reviewer that dies takes the whole cycle with it: the judge requires every selected role
to be present and fail-closes to `pipeline_error` when one is missing, so a transient
provider error publishes a NO-GO nobody reviewed and the next read costs five reviewers
instead of one. PR #196 lost a cycle that way to `API Error: 400` two turns in
(D-reviewer-retry-transient).

What makes the retry safe is not the loop but what it refuses to repeat, and that is what
these pin: only a caller that opts in, never a resumed session, never a slow failure, and
never with the previous attempt's artifact still on disk.

Fully offline. The `Run agent` step's shell is extracted from `action.yml` and run under
`bash` with a fake `docker` on PATH; nothing here pulls an image, reaches the network, or
runs a container.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ACTION = REPO_ROOT / ".github" / "actions" / "review-agent-run" / "action.yml"
REVIEWER = REPO_ROOT / ".github" / "workflows" / "review-reviewer.yml"
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"
RESOLVER = REPO_ROOT / ".github" / "workflows" / "resolve-issue.yml"


def _run_agent_script() -> str:
    """The `Run agent` step's shell, with its `${{ }}` expressions already substituted by
    the environment the step declares — the only part of the composite under test."""
    spec = yaml.safe_load(AGENT_ACTION.read_text(encoding="utf-8"))
    steps = [s for s in spec["runs"]["steps"] if s.get("name") == "Run agent"]
    assert len(steps) == 1, "the composite no longer has exactly one `Run agent` step"
    return steps[0]["run"]


def _fake_docker(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text("#!/usr/bin/env bash\n" + body)
    fake.chmod(0o755)


def _invoke(
    tmp_path: Path,
    *,
    docker_body: str,
    max_attempts: str = "1",
    session_host_dir: str = "",
    retry_within_seconds: str = "600",
) -> subprocess.CompletedProcess:
    """Run the extracted step with a workspace laid out the way the composite leaves it."""
    workspace = tmp_path / "ws"
    (workspace / "pr-head" / ".review-output").mkdir(parents=True)
    (workspace / ".github" / "actions" / "review-agent-run").mkdir(parents=True)
    (workspace / ".github/actions/review-agent-run/run-in-container.sh").write_text("#!/bin/sh\n")
    env_file = tmp_path / "env"
    env_file.write_text("X=1\n")
    bin_dir = tmp_path / "bin"
    _fake_docker(bin_dir, docker_body)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(workspace),
        "WORKSPACE_FOLDER": "pr-head",
        "CI_AGENT_IMAGE": "example/ci:test",
        "CI_AGENT_ENV_FILE": str(env_file),
        "SESSION_HOST_DIR": session_host_dir,
        "AGENT": "claude",
        "ROLE": "invariant",
        "RESULT_BASENAME": "",
        "MAX_ATTEMPTS": max_attempts,
        "RETRY_WITHIN_SECONDS": retry_within_seconds,
        # The loop sleeps between attempts; the shim below makes that free.
        "COUNT_FILE": str(tmp_path / "attempts"),
    }
    script = "sleep() { :; }\n" + _run_agent_script()
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=120
    )


#: A fake docker that fails every invocation, recording each one.
_ALWAYS_FAILS = """
echo "attempt" >> "$COUNT_FILE"
exit 1
"""

#: Fails the first invocation and succeeds after, like a provider that 400s once.
_FAILS_ONCE = """
echo "attempt" >> "$COUNT_FILE"
[ "$(wc -l < "$COUNT_FILE")" -gt 1 ] && exit 0
exit 1
"""


def _attempts(tmp_path: Path) -> int:
    counts = tmp_path / "attempts"
    return len(counts.read_text().splitlines()) if counts.exists() else 0


def test_a_transient_failure_is_retried_and_the_step_succeeds(tmp_path: Path) -> None:
    """The whole point: one 400 no longer costs a panel."""
    result = _invoke(tmp_path, docker_body=_FAILS_ONCE, max_attempts="2")
    assert result.returncode == 0, result.stderr
    assert _attempts(tmp_path) == 2


def test_a_retry_that_also_fails_still_fails_the_step(tmp_path: Path) -> None:
    """Fail-closed is unchanged. A retry buys one more attempt, never a pass."""
    result = _invoke(tmp_path, docker_body=_ALWAYS_FAILS, max_attempts="2")
    assert result.returncode != 0
    assert _attempts(tmp_path) == 2


def test_the_default_is_no_retry_at_all(tmp_path: Path) -> None:
    """Callers that push, open a PR, or hold a session must opt in — and none of them do."""
    result = _invoke(tmp_path, docker_body=_ALWAYS_FAILS)
    assert result.returncode != 0
    assert _attempts(tmp_path) == 1


def test_a_resumed_session_is_never_retried(tmp_path: Path) -> None:
    """`run-in-container.sh` contains a failed resume by exiting 0 with a sentinel so the
    cold fallback runs. Re-running it here would race that handoff, so the mount alone
    disables the retry even when the caller asked for one."""
    result = _invoke(
        tmp_path, docker_body=_ALWAYS_FAILS, max_attempts="2", session_host_dir=str(tmp_path)
    )
    assert result.returncode != 0
    assert _attempts(tmp_path) == 1


def test_a_slow_failure_is_not_retried(tmp_path: Path) -> None:
    """A deadline-shaped failure has already spent the job's budget, and a second identical
    attempt is the one most likely to repeat it. `retry_within_seconds: 0` is the same rule
    at a threshold a test can reach without waiting for one."""
    result = _invoke(
        tmp_path, docker_body=_ALWAYS_FAILS, max_attempts="2", retry_within_seconds="0"
    )
    assert result.returncode != 0
    assert _attempts(tmp_path) == 1
    assert "too slow to retry" in result.stdout + result.stderr


def test_a_retry_cannot_inherit_the_failed_attempt_s_artifact(tmp_path: Path) -> None:
    """A half-written result JSON from a dead attempt must not be what the judge reads, and
    the failed transcript must survive for diagnostics even when the retry goes green."""
    docker_body = """
echo "attempt" >> "$COUNT_FILE"
OUT="${GITHUB_WORKSPACE}/pr-head/.review-output"
if [ "$(wc -l < "$COUNT_FILE")" -gt 1 ]; then
  echo '{"role":"invariant"}' > "$OUT/invariant-result.json"
  echo "second transcript" > "$OUT/invariant-output.log"
  exit 0
fi
echo '{"role":"inv' > "$OUT/invariant-result.json"   # truncated by the crash
echo "first transcript" > "$OUT/invariant-output.log"
exit 1
"""
    result = _invoke(tmp_path, docker_body=docker_body, max_attempts="2")
    assert result.returncode == 0, result.stderr
    out = tmp_path / "ws" / "pr-head" / ".review-output"
    assert (out / "invariant-result.json").read_text().strip() == '{"role":"invariant"}'
    assert (out / "invariant-attempt1-output.log").read_text().strip() == "first transcript"
    assert (out / "invariant-output.log").read_text().strip() == "second transcript"


def _agent_run_steps(workflow: Path) -> list[dict]:
    spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = []
    for job in spec["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).endswith("review-agent-run"):
                steps.append(step)
    return steps


def test_the_reviewer_is_the_only_caller_that_opts_in() -> None:
    """The read-only role retries; every writing one stays at the default. A fixer that
    pushed half its work and then re-ran would not be repeating an attempt, it would be
    starting a second one on a tree the first had already changed."""
    reviewer = _agent_run_steps(REVIEWER)
    assert len(reviewer) == 1
    assert reviewer[0]["with"].get("max_attempts") == "2"

    for workflow in (FIXER, RESOLVER):
        for step in _agent_run_steps(workflow):
            assert "max_attempts" not in step["with"], (
                f"{workflow.name} opts a writing caller into retries; only a read-only "
                f"invocation is a true repeat (D-reviewer-retry-transient)"
            )


@pytest.mark.parametrize("caller", [FIXER, RESOLVER])
def test_every_caller_of_the_action_is_accounted_for(caller: Path) -> None:
    """Guards the test above: it is vacuous if the parse finds no invocations."""
    assert _agent_run_steps(caller), f"no review-agent-run step found in {caller.name}"
