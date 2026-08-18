"""`scripts/sync_pr_with_base.sh`, run as code rather than read as YAML.

The decisions merge driver already existed and already worked; what it never had was a call
site reachable from the event that needs it (D-base-moved-resync). Every registration
D-decisions-merge-driver added lives inside a review cycle, so a PR that has already been
cleared — the state PR #158 sat in for three days with auto-merge enabled — had nothing to
run the driver on its behalf, and its unconfigured merge remained conflicted.

This script is that call site, and three of its properties are safety-relevant:

* it pushes only when the driver is what made the merge succeed. A PR that merges cleanly
  with no driver registered is not blocked on anything and must be left alone, because the
  push would churn a SHA the reviewers, the dedup and the cycle counter are all keyed on;
* the driver it runs must come from the *trusted* checkout, never the PR one — this runs
  with a token that can push to any branch in the repository;
* a merge it cannot make cleanly must leave the branch exactly as it found it, since not
  syncing is the pre-decision baseline and strands nobody.

So these tests drive the script under `bash` against throwaway git repositories, the same
technique `tests/test_ci_fixer_decisions_driver.py` uses on the fixer's sync step. Fully
offline: real `git`, no `gh`, no network, no token.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC = REPO_ROOT / "scripts" / "sync_pr_with_base.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync-open-prs.yml"

_FIXED_DATE = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

_AGENT_EMAIL = "ci@reasonable-answer.local"

_HEAD = """# Decision log

## D-existing — something already decided

Body.

"""
_TAIL = """## Open items for a future round

- nothing
"""


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


def _section(slug: str) -> str:
    return f"## D-{slug} — a decision appended by one side\n\nBody.\n\n"


def _decisions(repo: Path, *appended: str, head: str = _HEAD) -> None:
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "decisions.md").write_text(
        head + "".join(appended) + _TAIL, encoding="utf-8"
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Bench:
    """An `origin` holding `main` and a `pr` branch, a `work` clone the script drives, and a
    `trusted` directory standing in for the checkout the driver is executed from.

    By default the two branches append one decision section each — the collision the driver
    exists to absorb, and the one GitHub cannot resolve for itself.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.origin = tmp_path / "origin"
        self.work = tmp_path / "work"
        self.trusted = tmp_path / "trusted"

        _git(tmp_path, "init", "-q", "-b", "main", str(self.origin))
        _git(self.origin, "config", "user.email", "seed@example.com")
        _git(self.origin, "config", "user.name", "Seed")
        # `git push` into a non-bare repository is refused for the branch it has checked
        # out. `main` stays checked out here and the script only ever pushes `pr`.
        (self.origin / ".gitattributes").write_text(
            "docs/decisions.md merge=decisions-append\n", encoding="utf-8"
        )
        _decisions(self.origin)
        self.base = _commit(self.origin, "seed")

        (self.trusted / "scripts").mkdir(parents=True)
        for name in ("register_decisions_driver.sh", "merge_decisions.py"):
            dest = self.trusted / "scripts" / name
            dest.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC)

    def diverge(self, *, pr: str, main: str) -> None:
        """Put one commit on each branch. `pr`/`main` name what each side did."""
        _git(self.origin, "checkout", "-q", "-b", "pr")
        self._side(pr)
        self.pr_head = _commit(self.origin, f"pr: {pr}")

        _git(self.origin, "checkout", "-q", "main")
        self._side(main)
        self.main_head = _commit(self.origin, f"main: {main}")

        _git(self.origin, "checkout", "-q", "main")
        _git(self.root, "clone", "-q", "--branch", "pr", str(self.origin), str(self.work))

    def _side(self, what: str) -> None:
        if what == "append-pr":
            _decisions(self.origin, _section("from-the-pr"))
        elif what == "append-main":
            _decisions(self.origin, _section("from-main"))
        elif what.startswith("edit-existing-"):
            _decisions(self.origin, head=_HEAD.replace("Body.", f"Rewritten by {what[13:]}."))
        elif what == "unrelated":
            (self.origin / "README.md").write_text("touched by one side\n", encoding="utf-8")
        else:  # pragma: no cover - a typo in a test's own setup, not a behaviour
            raise AssertionError(f"unknown side {what!r}")

    def sabotage_trusted_driver(self, body: str) -> None:
        driver = self.trusted / "scripts" / "merge_decisions.py"
        driver.write_text(body, encoding="utf-8")
        driver.chmod(driver.stat().st_mode | stat.S_IEXEC)

    def run(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SYNC), str(self.trusted), str(self.work), "main", "pr"],
            capture_output=True,
            text=True,
            env={**os.environ, **_FIXED_DATE},
        )

    def state(self, result: subprocess.CompletedProcess) -> str:
        states = [
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("state=")
        ]
        assert len(states) == 1, f"expected exactly one state line, got {states!r}\n{result.stdout}"
        return states[0]

    def remote_pr_head(self) -> str:
        return _git(self.origin, "rev-parse", "refs/heads/pr")

    def pushed_decisions(self) -> str:
        return _git(self.origin, "show", "refs/heads/pr:docs/decisions.md")


@pytest.fixture()
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


# --- the shape the script exists for ---------------------------------------------------


def test_the_append_only_collision_is_merged_and_pushed(bench: Bench) -> None:
    """The whole point: a PR blocked only by two decisions landing at the same anchor is
    unblocked without a review cycle and without a human."""
    bench.diverge(pr="append-pr", main="append-main")

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert bench.state(result) == "synced"
    assert bench.remote_pr_head() != bench.pr_head

    merged = bench.pushed_decisions()
    assert "## D-from-the-pr —" in merged
    assert "## D-from-main —" in merged
    assert "<<<<<<<" not in merged


def test_the_pushed_merge_is_authored_as_the_agent(bench: Bench) -> None:
    """review-pipeline.yml resets the cycle counter on any commit not authored as
    AGENT_COMMIT_EMAIL, reading it as a human answering the blockers. A resync is nobody
    answering anything, and billing it as a human intervention would hand a capped PR a
    fresh budget of agent cycles."""
    bench.diverge(pr="append-pr", main="append-main")

    assert bench.state(bench.run()) == "synced"

    assert _git(bench.origin, "log", "-1", "--format=%ae", "refs/heads/pr") == _AGENT_EMAIL
    assert _git(bench.origin, "rev-list", "--parents", "-n", "1", "refs/heads/pr").count(" ") == 2


# --- the cases it must decline ---------------------------------------------------------


def test_a_branch_that_merges_without_the_driver_is_left_alone(bench: Bench) -> None:
    """Being behind is not being blocked. A merge that needs no driver is one a server-side
    merge can compute for itself, so pushing here would churn the SHA the reviewers, the
    dedup and the cycle counter are keyed on to unblock nothing at all."""
    bench.diverge(pr="append-pr", main="unrelated")

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert bench.state(result) == "plain"
    assert bench.remote_pr_head() == bench.pr_head


def test_a_branch_already_containing_the_base_is_left_alone(bench: Bench) -> None:
    bench.diverge(pr="append-pr", main="unrelated")
    _git(bench.origin, "checkout", "-q", "pr")
    _git(bench.origin, "merge", "-q", "--no-edit", "main")
    merged_head = _git(bench.origin, "rev-parse", "HEAD")
    _git(bench.origin, "checkout", "-q", "main")

    result = bench.run()

    assert bench.state(result) == "none"
    assert bench.remote_pr_head() == merged_head


def test_a_conflict_the_driver_declines_is_left_exactly_as_it_was(bench: Bench) -> None:
    """Both sides edited the same existing section — a real disagreement, not an ordering
    accident. The pre-decision baseline is a conflict a human or the review cycle resolves,
    and that is what must be left behind."""
    bench.diverge(pr="edit-existing-the-pr", main="edit-existing-main")

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert bench.state(result) == "conflicts"
    assert bench.remote_pr_head() == bench.pr_head
    assert "<<<<<<<" not in bench.pushed_decisions()


def test_a_trusted_checkout_predating_the_helper_pushes_nothing(bench: Bench) -> None:
    """Every base branch older than D-decisions-merge-driver has no helper to call, and the
    first run of this workflow after it lands is one of them. Falling back to the conflict
    the PR already had is the only safe answer; failing would only paint the base branch red."""
    bench.diverge(pr="append-pr", main="append-main")
    (bench.trusted / "scripts" / "register_decisions_driver.sh").unlink()
    (bench.trusted / "scripts" / "merge_decisions.py").unlink()

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert bench.state(result) == "conflicts"
    assert bench.remote_pr_head() == bench.pr_head


def test_an_unrunnable_driver_pushes_nothing(bench: Bench) -> None:
    """The registration helper smoke-tests before registering, so a driver that cannot start
    is never registered and the merge is the plain one — which conflicts. Nothing is pushed,
    which is the same place the PR already was."""
    bench.diverge(pr="append-pr", main="append-main")
    (bench.trusted / "scripts" / "merge_decisions.py").unlink()

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert "not registered" in result.stdout + result.stderr
    assert bench.state(result) == "conflicts"
    assert bench.remote_pr_head() == bench.pr_head


# --- the trust boundary ----------------------------------------------------------------


def test_the_driver_comes_from_the_trusted_checkout_not_the_pr(bench: Bench) -> None:
    """This script runs where a push to any branch is possible, so a contributor's edit to
    `scripts/merge_decisions.py` must never be the code that runs. The PR branch ships a
    driver that would corrupt the merge; the trusted one is used instead and the result is
    the correct merge."""
    bench.diverge(pr="append-pr", main="append-main")
    _git(bench.origin, "checkout", "-q", "pr")
    hostile = bench.origin / "scripts"
    hostile.mkdir(parents=True, exist_ok=True)
    (hostile / "merge_decisions.py").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "open(sys.argv[2], 'w').write('owned by the PR checkout\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    (hostile / "register_decisions_driver.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    bench.pr_head = _commit(bench.origin, "pr ships its own merge driver")
    _git(bench.origin, "checkout", "-q", "main")
    _git(bench.work, "fetch", "-q", "origin")

    result = bench.run()

    assert bench.state(result) == "synced"
    merged = bench.pushed_decisions()
    assert "owned by the PR checkout" not in merged
    assert "## D-from-the-pr —" in merged
    assert "## D-from-main —" in merged


def test_a_clean_merge_still_carrying_markers_is_refused(bench: Bench) -> None:
    """The one should-never-happen worth failing over. A driver that exits 0 while leaving
    markers behind produces no conflict for git to report, so the push would put conflict
    markers into a normative spec file under a commit message claiming a clean sync."""
    bench.diverge(pr="append-pr", main="append-main")
    bench.sabotage_trusted_driver(
        "#!/usr/bin/env python3\n"
        "# Behaves for the registration smoke test, then 'merges' the real file into\n"
        "# something that still carries markers.\n"
        "import runpy\n"
        "import sys\n"
        "if 'D-smoke-base' in open(sys.argv[1]).read():\n"
        "    sys.exit(runpy.run_path(__file__.replace('merge_decisions.py', 'real.py'),\n"
        "                            run_name='__main__'))\n"
        "open(sys.argv[2], 'w').write('<<<<<<< ours\\nnot a merge\\n>>>>>>> theirs\\n')\n"
        "sys.exit(0)\n"
    )
    real = bench.trusted / "scripts" / "real.py"
    real.write_bytes((REPO_ROOT / "scripts" / "merge_decisions.py").read_bytes())

    result = bench.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert "carrying conflict markers" in result.stdout + result.stderr
    assert bench.remote_pr_head() == bench.pr_head
    assert not (bench.work / ".git" / "MERGE_HEAD").exists()


def test_the_workflow_calls_the_script_from_the_trusted_checkout() -> None:
    """Which root the script is handed is the workflow's decision, not the script's, so the
    trust boundary has to be pinned where it is actually made. `trusted` is checked out
    without a token and is the only thing executed; `work` holds the credential that can
    push and is only ever data."""
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["sync"]["steps"]
    assert "workflow_dispatch:" not in workflow_text
    checkouts = {
        step["with"]["path"]: step["with"]
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout")
    }
    assert set(checkouts) == {"trusted", "work"}
    assert "token" not in checkouts["trusted"]
    assert "WORKFLOW_PAT" in checkouts["work"]["token"]
    default_branch = "${{ github.event.repository.default_branch }}"
    assert checkouts["trusted"]["ref"] == default_branch
    assert checkouts["work"]["ref"] == default_branch

    resync = next(step for step in steps if step.get("id") == "resync")
    assert resync["env"]["BASE_REF"] == default_branch
    run = resync["run"]
    assert 'TRUSTED="${GITHUB_WORKSPACE}/trusted"' in run
    assert '"${TRUSTED}/scripts/sync_pr_with_base.sh" "${TRUSTED}" "${WORK}"' in run
    assert "${WORK}/scripts" not in run
    # Fork branches are not ours to push to, and a draft is not waiting on a merge button.
    assert "select(.head.repo.full_name == env.REPO)" in run
    assert "select(.draft == false)" in run


def test_a_branch_that_moved_under_the_merge_is_not_pushed(bench: Bench) -> None:
    """The head was read before the merge. If anything pushed in between — the fixer sealing
    a cycle, an agent still working — our commit is built on a tree that is no longer the
    branch, and the fixer discards a whole cycle's fixes when it finds the branch moved. The
    `post-commit` hook here stands in for that push landing at the worst possible moment."""
    bench.diverge(pr="append-pr", main="append-main")
    hook = bench.work / ".git" / "hooks" / "post-commit"
    hook.write_text(
        f"#!/usr/bin/env bash\n"
        f"git -C {bench.origin} update-ref refs/heads/pr {bench.base}\n",
        encoding="utf-8",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    result = bench.run()

    assert result.returncode == 0, result.stderr
    assert bench.state(result) == "moved"
    assert bench.remote_pr_head() == bench.base


# --- the loop that decides which PRs the script is even offered ------------------------
#
# The workflow's own step, not the script's: which PRs it hands over, and when it waits.
# `sync_pr_with_base.sh` above answers "may this branch be merged"; this half answers "is
# now the moment", and getting it wrong costs a five-role reviewer panel rather than a merge
# (D-resync-defers-to-finalize). Driven under `bash` with a stub `gh`, a stub for the sync
# script itself, and a fake clock, so the ten-minute wait budget is exercised in milliseconds.

_SHA = "1a3c5283a231c252389887961a6887a91f2f854d"


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class Loop:
    """The `resync` step, with the two questions it asks the API answered from files."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.stubs = tmp_path / "stubs"
        self.stubs.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (tmp_path / "trusted" / "scripts").mkdir(parents=True)
        (tmp_path / "work").mkdir()

        self.calls = self.stubs / "sync-calls"
        self.clock_file = self.stubs / "clock"
        self.start = int(time.time())
        self.clock_file.write_text(f"{self.start}\n", encoding="utf-8")
        self.inflight(0)
        self.statuses(_SHA, [])
        self.candidates([(7, "pr", _SHA)])

        sync = tmp_path / "trusted" / "scripts" / "sync_pr_with_base.sh"
        self._script(sync, f'printf "%s\\n" "$*" >> "{self.calls}"\necho "state=synced"\n')

        # Every stub the step reaches for. `gh` answers the pulls listing, the in-flight run
        # count and the head's commit statuses; `sleep` advances a fake clock that `date`
        # reads, so the loop's real deadline is honoured without the test waiting for it.
        # `date -d` keeps its real behaviour: parsing a status timestamp is not the clock.
        self._script(
            self.bin / "gh",
            f'args="$*"\n'
            f'case "$args" in\n'
            f'  *"/pulls"*) cat "{self.stubs}/candidates" ;;\n'
            f'  *"review-entry.yml/runs"*) cat "{self.stubs}/inflight" ;;\n'
            f'  *"/statuses"*)\n'
            f'    sha="$(printf "%s\\n" "$args" | grep -oE "[0-9a-f]{{40}}" | head -1)"\n'
            f'    if [ -f "{self.stubs}/statuses-${{sha}}" ]; then\n'
            f'      cat "{self.stubs}/statuses-${{sha}}"\n'
            f'    else\n'
            f'      echo "[]"\n'
            f'    fi ;;\n'
            f'  *) echo "gh stub: unexpected query: $args" >&2; exit 1 ;;\n'
            f'esac\n',
        )
        self._script(
            self.bin / "sleep",
            f'now="$(cat "{self.clock_file}")"\n'
            f'printf "%s\\n" "$((now + ${{1%%.*}}))" > "{self.clock_file}"\n',
        )
        self._script(
            self.bin / "date",
            f'if [ "${{1:-}}" = "-d" ]; then exec {shutil.which("date")} "$@"; fi\n'
            f'cat "{self.clock_file}"\n',
        )

    @staticmethod
    def _script(path: Path, body: str) -> None:
        path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def candidates(self, prs: list[tuple[int, str, str]]) -> None:
        (self.stubs / "candidates").write_text(
            "".join(f"{num} {ref} {sha}\n" for num, ref, sha in prs), encoding="utf-8"
        )

    def inflight(self, count: int) -> None:
        (self.stubs / "inflight").write_text(f"{count}\n", encoding="utf-8")

    def statuses(self, sha: str, entries: list[tuple[str, str]]) -> None:
        """`(context, created_at)` pairs, as the commit-statuses API returns them."""
        (self.stubs / f"statuses-{sha}").write_text(
            json.dumps([{"context": ctx, "created_at": at, "state": "success"}
                        for ctx, at in entries]),
            encoding="utf-8",
        )

    def cycle_recorded(self, sha: str, *, age: int, anchored: bool) -> None:
        entries = [("review/cycle", _iso(self.start - age))]
        if anchored:
            entries.append(("review/verdict-anchor", _iso(self.start - age + 1)))
        self.statuses(sha, entries)

    def run(self) -> subprocess.CompletedProcess:
        step = next(
            s for s in yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sync"]["steps"]
            if s.get("id") == "resync"
        )
        return subprocess.run(
            ["bash", "-c", step["run"]],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "GH_TOKEN": "stub",
                "REPO": "owner/repo",
                "BASE_REF": "main",
                "GITHUB_WORKSPACE": str(self.root),
            },
        )

    def waited(self) -> int:
        return int(self.clock_file.read_text(encoding="utf-8")) - self.start

    def synced(self) -> list[str]:
        return (
            self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        )


@pytest.fixture()
def loop(tmp_path: Path) -> Loop:
    return Loop(tmp_path)


def test_a_finalized_head_is_offered_to_the_script_at_once(loop: Loop) -> None:
    """The ordinary case: the last cycle published its verdict, so nothing is being raced."""
    loop.cycle_recorded(_SHA, age=3600, anchored=True)

    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert "#7 (pr): synced" in result.stdout
    assert len(loop.synced()) == 1
    assert loop.waited() == 0


def test_a_head_no_panel_has_recorded_is_offered_too(loop: Loop) -> None:
    """No `review/cycle` at all is not the race — it is a PR whose panel never recorded one,
    and a driver-resolved merge is the only thing that will let it earn one. Deferring on a
    missing anchor alone would wall those off permanently."""
    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert "#7 (pr): synced" in result.stdout
    assert loop.waited() == 0


def test_a_cycle_awaiting_its_anchor_is_deferred(loop: Loop) -> None:
    """The window this closes. `record-cycle` writes `review/cycle` when the panel has read
    the code; the verdict anchor lands minutes later. A merge pushed in between gives the
    successor's `gather` a cycle-recorded SHA with no anchor on it, which fail-closes to a
    full five-role panel on a head whose only delta is that merge."""
    loop.cycle_recorded(_SHA, age=60, anchored=False)

    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert f"deferred — a recorded cycle on {_SHA} has no published verdict anchor yet" in result.stdout
    assert loop.synced() == [], "nothing may be pushed under a cycle that is still finalizing"
    # Waited, but only under the one deadline the whole loop shares.
    assert 600 <= loop.waited() <= 660
    assert "deferred 1" in result.stdout


def test_an_abandoned_cycle_stops_deferring(loop: Loop) -> None:
    """A run cancelled between the two writes leaves that pair on the head forever. Waiting
    on a status nothing will ever complete would wall off exactly the PR this workflow is the
    only unblocker for, so the grace is bounded by the age of the cycle record."""
    loop.cycle_recorded(_SHA, age=7200, anchored=False)

    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert "treating it as abandoned" in result.stdout + result.stderr
    assert "#7 (pr): synced" in result.stdout
    assert loop.waited() == 0


def test_an_unreadable_cycle_timestamp_does_not_wedge_the_loop(loop: Loop) -> None:
    """A record that cannot be aged cannot be waited on with a bound, and its own arithmetic
    would abort the step. It reads as abandoned, which is the direction that strands nobody."""
    loop.statuses(_SHA, [("review/cycle", "not-a-timestamp")])

    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert "unreadable review/cycle timestamp" in result.stdout + result.stderr
    assert "#7 (pr): synced" in result.stdout


def test_a_review_run_in_flight_is_deferred(loop: Loop) -> None:
    """Unchanged behaviour, now reported as a defer rather than a warning: a run in flight
    performs this merge itself if it needs one, and the fixer discards a whole cycle's fixes
    when it finds the branch moved underneath it."""
    loop.inflight(1)
    loop.cycle_recorded(_SHA, age=3600, anchored=True)

    result = loop.run()

    assert result.returncode == 0, result.stderr
    assert "deferred — a review run is in flight" in result.stdout
    assert loop.synced() == []
    assert 600 <= loop.waited() <= 660
