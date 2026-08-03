"""The fixer's author-session quarantine, run as code rather than read as YAML.

A wedged agent session does not heal, but the pipeline had no memory of one: the same
`Author-Session:` trailer is read on every cycle and on every re-review, so a session that
had already failed to resume was resumed again, and the cold fixer that does the real work
started only after that attempt gave up (D-resume-stall-guard).

Two `review-fixer.yml` steps carry the memory, and neither is checkable by grepping:

* `burned` decides whether to attempt a resume at all, from a repo-wide artifact named for
  the session. It must **fail open** — an API error means "resume as before", not "never
  resume again" — and it must ignore expired markers, because an expired marker outlives
  nothing that matters.
* `quarantine` writes that marker's contents, naming the session and the reason. It runs
  from the containment sentinel `run-in-container.sh` leaves, and must still produce a
  usable marker when that sentinel is missing.

The steps' `run:` blocks are extracted from the workflow and driven under `bash`, the same
technique `tests/test_ci_fixer_decisions_driver.py` uses on the sync step. Offline: a fake
`gh` on PATH serves canned artifact JSON, and nothing leaves tmp_path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"

# The `burned` step delegates its filtering to `gh api --jq`, so the fake `gh` shells out to
# real jq to run the workflow's actual filter. Without jq the filter would have to be
# stubbed, and the test would stop covering the thing most likely to be wrong.
pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq to emulate `gh --jq`")


def _step_run(step_id: str) -> str:
    """The shell body of the fixer step with this id."""
    spec = yaml.safe_load(FIXER.read_text(encoding="utf-8"))
    for step in spec["jobs"]["fix"]["steps"]:
        if step.get("id") == step_id:
            return step["run"]
    raise AssertionError(f"review-fixer.yml has no step with id '{step_id}'")


def _fake_gh(bin_dir: Path, *, payload: str | None, exit_code: int = 0) -> None:
    """A `gh` that answers one `api ... --jq FILTER` call from a canned payload.

    `payload=None` stands for the API being unreachable — the case the step must fail open
    on. The requested URL is recorded so the query itself can be asserted.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    body = [
        "#!/usr/bin/env bash",
        'printf "%s\\n" "$@" > "$GH_ARGV_LOG"',
        f"exit_code={exit_code}",
        'if [ "$exit_code" -ne 0 ]; then echo "gh: boom" >&2; exit "$exit_code"; fi',
        # Pull the filter out of `gh api URL --jq FILTER`.
        'filter=""',
        'while [ $# -gt 0 ]; do case "$1" in --jq) filter="$2"; shift 2 ;; *) shift ;; esac; done',
        'jq -r "$filter" "$GH_PAYLOAD"',
    ]
    gh.write_text("\n".join(body) + "\n")
    gh.chmod(0o755)
    if payload is not None:
        (bin_dir.parent / "payload.json").write_text(payload)


def _run_burned(
    tmp_path: Path,
    *,
    payload: str | None,
    gh_exit: int = 0,
    agent: str = "claude",
    run_id: str = "30726196351",
) -> tuple[dict[str, str], subprocess.CompletedProcess[str]]:
    """Run the `burned` step; return its $GITHUB_OUTPUT as a dict, plus the process."""
    bin_dir = tmp_path / "bin"
    _fake_gh(bin_dir, payload=payload, exit_code=gh_exit)

    script = tmp_path / "burned.sh"
    script.write_text(_step_run("burned"))
    out_file = tmp_path / "github_output"
    out_file.touch()

    proc = subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "GH_TOKEN": "not-a-real-token",
            "REPO": "owner/repo",
            "AGENT": agent,
            "RUN_ID": run_id,
            "GITHUB_OUTPUT": str(out_file),
            "GH_ARGV_LOG": str(tmp_path / "gh-argv.txt"),
            "GH_PAYLOAD": str(tmp_path / "payload.json"),
        },
    )
    outputs = dict(
        line.split("=", 1) for line in out_file.read_text().splitlines() if "=" in line
    )
    return outputs, proc


def _artifacts(*entries: tuple[str, bool]) -> str:
    """The shape of `GET /repos/{o}/{r}/actions/artifacts`, as (name, expired) pairs."""
    items = ",".join(
        f'{{"name":"{name}","expired":{str(expired).lower()}}}' for name, expired in entries
    )
    return f'{{"total_count":{len(entries)},"artifacts":[{items}]}}'


# ── deciding whether to resume ───────────────────────────────────────────────


def test_a_live_marker_quarantines_the_session(tmp_path: Path) -> None:
    outputs, proc = _run_burned(
        tmp_path, payload=_artifacts(("session-hung-claude-30726196351", False))
    )
    assert proc.returncode == 0, proc.stderr
    assert outputs["quarantined"] == "true"
    # The pipeline surfaces the skipped resume rather than silently changing mode.
    assert "::warning::" in proc.stdout


def test_no_marker_leaves_the_session_resumable(tmp_path: Path) -> None:
    outputs, proc = _run_burned(tmp_path, payload=_artifacts())
    assert proc.returncode == 0, proc.stderr
    assert outputs["quarantined"] == "false"


def test_an_expired_marker_does_not_quarantine(tmp_path: Path) -> None:
    """Markers outlive the session artifacts they describe, so an expired one describes a
    session that can no longer be resumed anyway — but reading it as live would keep
    condemning a run id whose evidence is gone."""
    outputs, proc = _run_burned(
        tmp_path, payload=_artifacts(("session-hung-claude-30726196351", True))
    )
    assert proc.returncode == 0, proc.stderr
    assert outputs["quarantined"] == "false"


def test_an_unreachable_api_fails_open(tmp_path: Path) -> None:
    """The cost of failing open is one bounded resume attempt. The cost of failing closed
    is losing the resume path entirely on an unrelated API blip, silently and for good."""
    outputs, proc = _run_burned(tmp_path, payload=None, gh_exit=1)
    assert proc.returncode == 0, proc.stderr
    assert outputs["quarantined"] == "false"
    assert "::warning::" in proc.stdout


def test_the_query_is_keyed_to_this_session(tmp_path: Path) -> None:
    """A query that matched on anything less specific than (agent, run-id) would quarantine
    sessions that never failed — every PR shares this step."""
    _run_burned(tmp_path, payload=_artifacts(), agent="codex", run_id="424242")
    argv = (tmp_path / "gh-argv.txt").read_text()
    assert "repos/owner/repo/actions/artifacts?name=session-hung-codex-424242" in argv


# ── writing the marker ───────────────────────────────────────────────────────


def _run_quarantine(tmp_path: Path, *, sentinel: str | None) -> subprocess.CompletedProcess[str]:
    workspace = tmp_path / "ws"
    out_dir = workspace / "pr-head" / ".review-output"
    out_dir.mkdir(parents=True)
    if sentinel is not None:
        (out_dir / "fixer-incomplete.sentinel").write_text(sentinel)

    script = tmp_path / "quarantine.sh"
    script.write_text(_step_run("quarantine"))
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()

    return subprocess.run(
        ["bash", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_WORKSPACE": str(workspace),
            "PR_WORKSPACE": "pr-head",
            "GITHUB_RUN_ID": "999",
            "AGENT": "claude",
            "RUN_ID": "30726196351",
            "PR_NUMBER": "141",
            "CYCLE": "1",
        },
    )


def test_the_marker_names_the_session_and_the_reason(tmp_path: Path) -> None:
    proc = _run_quarantine(tmp_path, sentinel="stalled: no output at all within 180s\n")
    assert proc.returncode == 0, proc.stderr
    marker = (tmp_path / "runner-temp" / "session-hung" / "reason.txt").read_text()
    assert "claude/30726196351" in marker
    assert "no output at all within 180s" in marker
    # Without the observer the marker cannot be traced back to the run that wrote it, which
    # is the only way to tell a stale quarantine from a current one.
    assert "run 999" in marker


def test_the_marker_is_still_written_without_a_sentinel(tmp_path: Path) -> None:
    """`run-in-container.sh` writes the sentinel for every contained outcome, but the
    fallback also fires on a missing result alone. A marker that failed here would leave the
    next cycle repeating exactly the attempt this one just watched fail."""
    proc = _run_quarantine(tmp_path, sentinel=None)
    assert proc.returncode == 0, proc.stderr
    marker = (tmp_path / "runner-temp" / "session-hung" / "reason.txt").read_text()
    assert "claude/30726196351" in marker
    assert "no fixer-result.json" in marker
