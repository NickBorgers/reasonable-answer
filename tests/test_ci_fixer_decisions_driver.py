"""The fixer's base-branch sync, run as code rather than read as YAML.

`review-fixer.yml`'s "Sync with the base branch" step is the call site the decisions merge
driver exists for: it is where the repeated hand-resolved `merge: resolve conflicts with
origin/main` commits were being paid for (D-decisions-merge-driver). Two properties of that
step are safety-relevant and neither is visible by grepping the YAML:

* the driver it registers must come from the *trusted* checkout, never the PR one — this job
  carries `WORKFLOW_PAT`, so a contributor's edit to `scripts/merge_decisions.py` must not
  execute here before a reviewer has read it;
* a driver that cannot run must leave the *no-driver baseline* behind — a real conflict with
  real markers. Git does not give that for free: a merge driver whose command fails to start
  leaves "ours" in the worktree with no markers at all and the path merely `UU` in the index,
  and the commit step's `git add -A` then stages that as resolved. The marker gate sees a
  clean tree and the pipeline pushes a merge that dropped every base-side change to a
  normative spec file (inv-driver-exec-failure-1).

So these tests extract the `run:` block from the workflow and drive it under `bash` against
throwaway git repositories, the same technique `tests/test_ci_inherit_classifier.py` uses on
the inherit step. Fully offline: real `git`, no `gh`, no network, no token.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"
REGISTER = REPO_ROOT / "scripts" / "register_decisions_driver.sh"

_FIXED_DATE = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

# The tail marker every decision is appended before, and a head that predates both sides.
_HEAD = """# Decision log

## D-existing — something already decided

Body.

"""
_TAIL = """## Open items for a future round

- nothing
"""


def _step(step_id: str) -> dict:
    spec = yaml.safe_load(FIXER.read_text(encoding="utf-8"))
    for step in spec["jobs"]["fix"]["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"review-fixer.yml no longer has a fix step with id {step_id!r}")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_FIXED_DATE},
    )
    return out.stdout.strip()


def _decisions(repo: Path, *appended: str) -> None:
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "decisions.md").write_text(
        _HEAD + "".join(appended) + _TAIL, encoding="utf-8"
    )


def _section(slug: str) -> str:
    return f"## D-{slug} — a decision appended by one side\n\nBody.\n\n"


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Bench:
    """An `origin` plus a working clone standing in for `PR_WORKSPACE`, and a separate
    `trusted` directory standing in for the `main` checkout the driver is executed from.

    `main` and the PR branch each append one decision section, which is precisely the
    collision the driver exists to absorb.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.origin = tmp_path / "origin"
        self.clone = tmp_path / "pr-head"
        self.trusted = tmp_path / "trusted"

        _git(tmp_path, "init", "-q", "-b", "main", str(self.origin))
        _git(self.origin, "config", "user.email", "seed@example.com")
        _git(self.origin, "config", "user.name", "Seed")
        (self.origin / ".gitattributes").write_text(
            "docs/decisions.md merge=decisions-append\n", encoding="utf-8"
        )
        _decisions(self.origin)
        self.base = _commit(self.origin, "seed")

        _git(self.origin, "checkout", "-q", "-b", "pr")
        _decisions(self.origin, _section("from-the-pr"))
        _commit(self.origin, "pr appends a decision")

        _git(self.origin, "checkout", "-q", "main")
        _decisions(self.origin, _section("from-main"))
        _commit(self.origin, "main appends a different decision")

        _git(tmp_path, "clone", "-q", "--branch", "pr", str(self.origin), str(self.clone))
        _git(self.clone, "config", "user.email", "agent@example.com")
        _git(self.clone, "config", "user.name", "Agent")
        (self.clone / ".review-output").mkdir()

        # The trusted checkout the workflow resolves the driver out of. Only the two scripts
        # the step actually reaches for are needed.
        (self.trusted / "scripts").mkdir(parents=True)
        for name in ("register_decisions_driver.sh", "merge_decisions.py"):
            dest = self.trusted / "scripts" / name
            dest.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC)

    def sabotage_driver(self, body: str) -> None:
        """Replace the trusted checkout's driver with `body`, keeping it executable."""
        driver = self.trusted / "scripts" / "merge_decisions.py"
        driver.write_text(body, encoding="utf-8")
        driver.chmod(driver.stat().st_mode | stat.S_IEXEC)

    def run_sync(self, *, sync_only: str = "false") -> subprocess.CompletedProcess:
        outputs = self.root / "github-output"
        outputs.write_text("", encoding="utf-8")
        return subprocess.run(
            ["bash", "-c", _step("sync")["run"]],
            cwd=self.clone,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                **_FIXED_DATE,
                "GITHUB_WORKSPACE": str(self.trusted),
                "GITHUB_OUTPUT": str(outputs),
                "AGENT_COMMIT_EMAIL": "agent@example.com",
                "AGENT_COMMIT_NAME": "Agent",
                "BASE_REF": "main",
                "HEAD_REF": "pr",
                "SYNC_ONLY": sync_only,
                "CONFLICT_LIST": ".review-output/merge-conflicts.txt",
            },
        )

    def merge_state(self) -> str:
        text = (self.root / "github-output").read_text(encoding="utf-8")
        states = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("merge_state=")]
        assert len(states) == 1, f"expected exactly one merge_state, got {states!r}"
        return states[0]

    def decisions(self) -> str:
        return (self.clone / "docs" / "decisions.md").read_text(encoding="utf-8")


@pytest.fixture()
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


# --- the step, driven end to end -------------------------------------------------------


def test_sync_absorbs_the_append_only_collision(bench: Bench) -> None:
    """The whole point: two sides each appending a decision sync without an agent."""
    result = bench.run_sync()

    assert result.returncode == 0, result.stderr
    assert bench.merge_state() == "clean"
    merged = bench.decisions()
    assert "## D-from-the-pr —" in merged
    assert "## D-from-main —" in merged
    assert "<<<<<<<" not in merged


def test_sync_falls_back_to_a_real_conflict_when_the_driver_cannot_run(bench: Bench) -> None:
    """A driver that cannot start must leave the no-driver baseline, not a silent merge.

    The registration helper smoke-tests before registering, so an unrunnable driver is never
    registered at all and git performs its ordinary 3-way merge — which conflicts, with
    markers, exactly as it did before this driver existed.
    """
    (bench.trusted / "scripts" / "merge_decisions.py").unlink()

    result = bench.run_sync()

    assert result.returncode == 0, result.stderr
    assert "not registered" in result.stdout + result.stderr
    assert bench.merge_state() == "conflicts"
    assert "<<<<<<<" in bench.decisions()
    conflicts = (bench.clone / ".review-output" / "merge-conflicts.txt").read_text()
    assert conflicts.split() == ["docs/decisions.md"]


def test_sync_works_against_a_trusted_checkout_that_predates_the_helper(bench: Bench) -> None:
    """Every `main` older than this decision has no helper to call. The step must fall back
    to the merge it always did rather than failing the job on a missing file — otherwise the
    change cannot land without breaking the run that reviews it."""
    (bench.trusted / "scripts" / "register_decisions_driver.sh").unlink()
    (bench.trusted / "scripts" / "merge_decisions.py").unlink()

    result = bench.run_sync()

    assert result.returncode == 0, result.stderr
    assert bench.merge_state() == "conflicts"
    assert "<<<<<<<" in bench.decisions()


def test_sync_refuses_a_conflict_the_driver_left_without_markers(bench: Bench) -> None:
    """The belt to the helper's braces (inv-driver-exec-failure-1).

    A driver that passes the smoke test and then fails on the real file leaves a `UU` path
    whose worktree copy is plain "ours" — no markers for an agent to resolve and nothing for
    the commit step's marker gate to catch once `git add -A` has staged it. The step must
    stop rather than hand that tree onwards.
    """
    bench.sabotage_driver(
        "#!/usr/bin/env python3\n"
        "# Behaves for the registration smoke test, then refuses to merge anything real\n"
        "# without writing markers — the shape the marker gate cannot see.\n"
        "import sys\n"
        "base = open(sys.argv[1]).read()\n"
        "if 'D-smoke-base' in base:\n"
        "    import runpy\n"
        "    sys.exit(runpy.run_path(__file__.replace('merge_decisions.py', 'real.py'),\n"
        "                            run_name='__main__'))\n"
        "sys.exit(1)\n"
    )
    real = bench.trusted / "scripts" / "real.py"
    real.write_bytes((REPO_ROOT / "scripts" / "merge_decisions.py").read_bytes())

    result = bench.run_sync()

    assert result.returncode == 1
    assert "carries no conflict markers" in result.stdout + result.stderr
    # The merge was abandoned, so nothing half-resolved can ride onwards.
    assert not (bench.clone / ".git" / "MERGE_HEAD").exists()


# --- the registration helper itself ----------------------------------------------------


def _register(trusted: Path, target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(REGISTER), str(trusted), str(target)],
        capture_output=True,
        text=True,
        env={**os.environ, **_FIXED_DATE},
    )


def _configured_driver(repo: Path) -> str | None:
    out = subprocess.run(
        ["git", "config", "--get", "merge.decisions-append.driver"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None


def test_register_points_the_driver_at_the_trusted_checkout(bench: Bench) -> None:
    assert _register(bench.trusted, bench.clone).returncode == 0
    configured = _configured_driver(bench.clone)
    assert configured == f"python3 {bench.trusted}/scripts/merge_decisions.py %O %A %B"
    # An absolute path out of the trusted tree, never one resolved against the PR checkout.
    assert str(bench.clone) not in configured


def test_register_declines_a_driver_that_merges_nothing(bench: Bench) -> None:
    """Exit 0 and silence would be the dangerous answer: git would report a conflict it
    left no markers for. The helper must decline instead."""
    bench.sabotage_driver("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")

    result = _register(bench.trusted, bench.clone)

    assert result.returncode == 0
    assert "not registered" in result.stdout
    assert _configured_driver(bench.clone) is None


def test_register_declines_a_driver_that_swallows_a_real_conflict(bench: Bench) -> None:
    """The other half of the contract: declining a shape must still produce markers."""
    bench.sabotage_driver(
        "#!/usr/bin/env python3\n"
        "import shutil, sys\n"
        "shutil.copyfile(sys.argv[3], sys.argv[2])\n"  # take "theirs" wholesale, always clean
        "sys.exit(0)\n"
    )

    result = _register(bench.trusted, bench.clone)

    assert result.returncode == 0
    assert "not registered" in result.stdout
    assert _configured_driver(bench.clone) is None


def test_register_clears_a_stale_registration_when_the_driver_breaks(bench: Bench) -> None:
    """The fixer registers twice against the same repo. A first-step registration must not
    survive a second step that just proved the driver unusable."""
    assert _register(bench.trusted, bench.clone).returncode == 0
    assert _configured_driver(bench.clone) is not None

    (bench.trusted / "scripts" / "merge_decisions.py").unlink()
    assert _register(bench.trusted, bench.clone).returncode == 0

    assert _configured_driver(bench.clone) is None


def test_a_broken_driver_really_does_produce_a_markerless_conflict(bench: Bench) -> None:
    """The premise the whole guard rests on, pinned so it cannot quietly stop being true.

    Registered directly, bypassing the helper: git reports the merge as failed, the path is
    unmerged in the index, and the worktree copy is plain "ours" with no markers anywhere.
    """
    subprocess.run(
        [
            "git",
            "config",
            "merge.decisions-append.driver",
            f"python3 {bench.trusted}/scripts/does-not-exist.py %O %A %B",
        ],
        cwd=bench.clone,
        check=True,
    )
    merged = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "origin/main"],
        cwd=bench.clone,
        capture_output=True,
        text=True,
        env={**os.environ, **_FIXED_DATE},
    )

    assert merged.returncode != 0
    unmerged = _git(bench.clone, "diff", "--name-only", "--diff-filter=U")
    assert unmerged == "docs/decisions.md"
    assert "<<<<<<<" not in bench.decisions()


# --- cheap structural assertions, so a rename cannot silently unhook the above ----------


def test_both_fixer_merge_sites_register_from_the_trusted_checkout() -> None:
    sync = _step("sync")["run"]
    fallback = _step("fallback")["run"]

    for run in (sync, fallback):
        assert 'REGISTER_DRIVER="${GITHUB_WORKSPACE}/scripts/register_decisions_driver.sh"' in run
    assert '"${REGISTER_DRIVER}" "${GITHUB_WORKSPACE}"\n' in sync
    assert "${PR_WORKSPACE}/scripts/" not in sync
    assert '"${REGISTER_DRIVER}" "${GITHUB_WORKSPACE}" "${PR_DIR}"' in fallback
    assert "${PR_DIR}/scripts/" not in fallback
