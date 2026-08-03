"""Tests for scripts/validate-workflow-refs.sh.

actionlint checks workflow *syntax*; this script checks that what a workflow points at
actually exists, because a `uses:` or a script path that does not resolve fails at runtime as
a `startup_failure` with no logs.

It is a required PR check, so a false failure blocks every PR in the repository — which is
what happened when the merge driver introduced `"${GITHUB_WORKSPACE}/scripts/x.sh"` call
sites. The matcher cannot see inside `${...}`, so the path arrived looking absolute
(`/scripts/x.sh`), matched nothing on disk, and the gate went red on `main` for a file that
was sitting right there. Every branch cut from `main` afterwards inherited the failure, and
the review pipeline's reviewer guards — which refuse to read a SHA whose validation failed —
then declined to run at all.

These pin both directions: the ways a workflow legitimately names a script all resolve, and a
genuinely missing script is still caught.

Offline: each case builds a throwaway repo tree in tmp_path and runs the script there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "validate-workflow-refs.sh"


def _repo(tmp_path: Path, *, workflow: str, scripts: dict[str, int] | None = None) -> Path:
    """A minimal repo: one workflow file, plus scripts with the given permission bits.

    The script `cd`s to its own parent's parent, so it must be copied into the fixture rather
    than run from the real tree.
    """
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
    (root / ".github" / "workflows" / "w.yml").write_text(workflow)
    for name, mode in (scripts or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\n")
        path.chmod(mode)
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def _workflow(*invocations: str) -> str:
    steps = "\n".join(f"      - run: {inv}" for inv in invocations)
    return "on: push\njobs:\n  j:\n    steps:\n" + steps + "\n"


# ── the ways a workflow legitimately names a script ──────────────────────────


def test_a_workspace_rooted_invocation_resolves(tmp_path: Path) -> None:
    """The regression. `${GITHUB_WORKSPACE}` expands at runtime, so the matcher sees only the
    tail — which looks absolute and matches nothing unless the leading segment is dropped."""
    root = _repo(
        tmp_path,
        workflow=_workflow('"${GITHUB_WORKSPACE}/scripts/helper.sh" arg'),
        scripts={"scripts/helper.sh": 0o755},
    )
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_invocation_through_a_named_checkout_resolves(tmp_path: Path) -> None:
    """A job that checks the trusted tree out under `path:` reaches it through that prefix."""
    root = _repo(
        tmp_path,
        workflow=_workflow('"${GITHUB_WORKSPACE}/main-checkout/scripts/helper.sh"'),
        scripts={"scripts/helper.sh": 0o755},
    )
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_plain_relative_invocation_resolves(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        workflow=_workflow("./scripts/helper.sh"),
        scripts={"scripts/helper.sh": 0o755},
    )
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_nested_scripts_directory_is_not_truncated(tmp_path: Path) -> None:
    """`.github/scripts/review/x.sh` must resolve as itself, not as `scripts/review/x.sh`."""
    root = _repo(
        tmp_path,
        workflow=_workflow("./.github/scripts/review/render.sh"),
        scripts={".github/scripts/review/render.sh": 0o755},
    )
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr


# ── and what it must still catch ─────────────────────────────────────────────


def test_a_missing_script_still_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, workflow=_workflow("./scripts/gone.sh"))
    r = _run(root)
    assert r.returncode != 0
    assert "gone.sh" in r.stderr


def test_a_missing_workspace_rooted_script_still_fails(tmp_path: Path) -> None:
    """Dropping leading segments must not degrade into "anything resolves": the tail is what
    is looked up, and there is no `gone.sh` to find."""
    root = _repo(tmp_path, workflow=_workflow('"${GITHUB_WORKSPACE}/scripts/gone.sh"'))
    r = _run(root)
    assert r.returncode != 0
    assert "gone.sh" in r.stderr


def test_a_non_executable_script_is_reported(tmp_path: Path) -> None:
    """A workflow invoking a mode-644 script fails at runtime with a bare 'Permission
    denied'. It is reported against the path that resolved, not the one the YAML spelled."""
    root = _repo(
        tmp_path,
        workflow=_workflow('"${GITHUB_WORKSPACE}/scripts/helper.sh"'),
        scripts={"scripts/helper.sh": 0o644},
    )
    r = _run(root)
    assert r.returncode != 0
    assert "not executable" in r.stderr


def test_a_missing_local_uses_target_still_fails(tmp_path: Path) -> None:
    """The other half of the gate, unchanged — kept here so this file covers the script."""
    root = _repo(
        tmp_path,
        workflow="on: push\njobs:\n  j:\n    steps:\n      - uses: ./.github/actions/nope\n",
    )
    r = _run(root)
    assert r.returncode != 0
    assert "nope" in r.stderr


# ── and the real tree passes ─────────────────────────────────────────────────


def test_this_repository_resolves(tmp_path: Path) -> None:
    """The gate's actual job. It ran red on `main` for two days' worth of branches."""
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
