"""What the review pipeline bills against `MAX_CYCLES`, run as code rather than read as YAML.

The counter is what stands between a PR and a terminal `cycle_capped` NO-GO, and on
2026-08-18 an eight-PR stack showed it billing runs that spend nothing. Two shapes, both
observed:

* **An inherited verdict advanced the count.** A pure merge-from-base reads nothing, fixes
  nothing and re-publishes the verdict its anchor already carried — the whole reason the
  short-circuit exists is that resyncing a long-lived branch must not walk it into the cap.
  It advanced the counter anyway, so two resyncs after a cycle-1 panel reached "cycle 3", and
  because the cap is evaluated from the same output, `cap-exhausted` and `inherit` *both*
  finalized on one SHA: PR #183's 4e9c6b6 carries an inherited GO at 13:52:53 and a cycle-cap
  NO-GO at 13:52:56, on identical content, with the NO-GO landing last and becoming the
  anchor everything later inherited.
* **A run that reviewed nothing was billed one cycle further along.** `record-cycle` defers
  the counter's write until a panel has actually read the code, precisely so a run whose
  guards all refused starts the next push from the same number — and `review-finalize.yml`
  stamped `review/cycle` regardless. PR #186's e748c86 has no `record-cycle` write, a
  fail-closed `pipeline_error` NO-GO, and a `cycle 2` stamped by finalize for a panel that
  never ran.

So these tests drive `gather`'s `Compute cycle` step under `bash` against a throwaway git
repository, the same technique `tests/test_ci_inherit_classifier.py` uses on the step above it,
and pin the two YAML conditions that keep the finalize paths disjoint (D-nonjudgement-outcomes).
Fully offline: real `git`, no `gh`, no network, no token.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "review-pipeline.yml"

_FIXED_DATE = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

_HUMAN_EMAIL = "someone@example.invalid"


def _spec() -> dict:
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def _cycle_step_script() -> str:
    """The shell body of the `cycle` step, exactly as CI runs it."""
    for step in _spec()["jobs"]["gather"]["steps"]:
        if step.get("id") == "cycle":
            return step["run"]
    raise AssertionError("review-pipeline.yml no longer has a gather step with id 'cycle'")


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


class Bench:
    """A repository with one machine-authored and one human-authored commit.

    The step reads the author of the SHA it is given — that is the whole human-reset rule —
    so both kinds have to be real commits rather than an environment variable.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.repo = tmp_path / "pr-head"
        self.repo.mkdir()
        self.output = tmp_path / "github_output"
        # Read from the workflow, not restated here: a change to the cap or to the machine
        # identity must move these tests with it rather than leave them asserting a number
        # the pipeline no longer uses.
        env = _spec()["env"]
        self.max_cycles = str(env["MAX_CYCLES"])
        self.agent_email = env["AGENT_COMMIT_EMAIL"]

        _git(self.repo, "init", "-q", "-b", "main")
        self.agent_sha = self._commit("agent.txt", self.agent_email)
        self.human_sha = self._commit("human.txt", _HUMAN_EMAIL)

    def _commit(self, name: str, email: str) -> str:
        (self.repo / name).write_text(f"{name}\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "-c", f"user.email={email}", "-c", "user.name=test",
             "commit", "-q", "-m", f"commit {name}")
        return _git(self.repo, "rev-parse", "HEAD")

    def run(self, script: str, *, prior: int, sha: str | None = None, inherit: str = "false") -> dict:
        self.output.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "SHA": sha or self.agent_sha,
            "PRIOR": str(prior),
            "INHERIT": inherit,
            "MAX_CYCLES": self.max_cycles,
            "AGENT_COMMIT_EMAIL": self.agent_email,
            "GITHUB_OUTPUT": str(self.output),
        }
        proc = subprocess.run(
            ["bash", "-c", script], cwd=self.repo, capture_output=True, text=True, env=env
        )
        assert proc.returncode == 0, f"step failed:\n{proc.stdout}\n{proc.stderr}"
        parsed = {}
        for line in self.output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            parsed[key] = value
        parsed["_log"] = proc.stdout
        return parsed


@pytest.fixture()
def script() -> str:
    return _cycle_step_script()


@pytest.fixture()
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


def test_the_first_cycle_on_a_pr_is_one(bench: Bench, script: str) -> None:
    result = bench.run(script, prior=0)
    assert result["cycle"] == "1"
    assert result["cap_exhausted"] == "false"
    assert result["fix_allowed"] == "true"


def test_a_machine_push_advances_the_counter(bench: Bench, script: str) -> None:
    """The loop the cap bounds: the fixer authors as the agent, so its push is billed."""
    result = bench.run(script, prior=1)
    assert result["cycle"] == "2"
    assert result["cap_exhausted"] == "false"
    # The last permitted cycle may not fix: that fix would land unread (see MAX_CYCLES).
    assert result["fix_allowed"] == "false"


def test_the_cap_fires_past_the_last_permitted_cycle(bench: Bench, script: str) -> None:
    result = bench.run(script, prior=int(bench.max_cycles))
    assert result["cycle"] == str(int(bench.max_cycles) + 1)
    assert result["cap_exhausted"] == "true"
    assert result["fix_allowed"] == "false"


def test_a_human_push_resets_the_counter(bench: Bench, script: str) -> None:
    """A human read the blockers and answered them: a new conversation, with a full budget."""
    result = bench.run(script, prior=int(bench.max_cycles), sha=bench.human_sha)
    assert result["cycle"] == "1"
    assert result["cap_exhausted"] == "false"
    assert result["fix_allowed"] == "true"


def test_an_inherited_verdict_holds_the_counter(bench: Bench, script: str) -> None:
    """The regression PR #183 paid for: a resync must cost nothing on the counter.

    Held at the cycle the inherited verdict belongs to, not advanced — advancing it is what
    let two resyncs after a cycle-1 panel report "cycle 3" and hand the cap a PR nobody had
    reviewed twice.
    """
    inherited = bench.run(script, prior=1, inherit="true")
    assert inherited["cycle"] == "1"
    assert inherited["cap_exhausted"] == "false"
    # No findings, no panel: the arithmetic must not offer a fix either.
    assert inherited["fix_allowed"] == "false"
    assert "holding the cycle counter at 1" in inherited["_log"]

    # The same input reviewed rather than inherited is the pre-fix behaviour, so this fails
    # on a revert of the hold and on nothing else.
    assert bench.run(script, prior=1)["cycle"] == "2"


def test_an_inherited_verdict_is_never_capped(bench: Bench, script: str) -> None:
    """The cap bounds review → fix → push → review, and an inherited run runs no stage of it.

    Without this, `cap_exhausted` and `inherit` are both true on one SHA and both finalize:
    the cost-backstop NO-GO races the inherited verdict, and whichever API write lands last
    becomes the anchor every later resync inherits.
    """
    capped = bench.run(script, prior=int(bench.max_cycles) + 1, inherit="true")
    assert capped["cap_exhausted"] == "false"
    assert capped["fix_allowed"] == "false"

    # Same count, not inheriting: still capped, which is the state the cap is for.
    assert bench.run(script, prior=int(bench.max_cycles) + 1)["cap_exhausted"] == "true"


def test_the_cap_and_inherit_finalize_paths_are_disjoint() -> None:
    """Two finalize jobs on one SHA is a race, whoever wins it.

    `gather` closes this upstream by not reporting `cap_exhausted` on an inherited run; the
    job condition is the second lock, so a regression in the arithmetic cannot resurrect
    PR #183's pair of contradictory verdicts on 4e9c6b6.
    """
    jobs = _spec()["jobs"]
    cap = " ".join(jobs["cap-exhausted"]["if"].split())
    inherit = " ".join(jobs["inherit"]["if"].split())

    assert "needs.gather.outputs.cap_exhausted == 'true'" in cap
    assert "needs.gather.outputs.inherit != 'true'" in cap
    assert "needs.gather.outputs.inherit == 'true'" in inherit
    assert "cap_exhausted" not in inherit, "the inherit path deliberately outranks the cap"


def test_finalize_is_told_whether_a_cycle_was_recorded() -> None:
    """A run that reviewed nothing does not consume a cycle, on the finalize path too.

    `record-cycle` is skipped exactly when no reviewer's guard cleared, and the sync-only
    pass (D-unguarded-sync) rides the same skip. Finalize stamping `review/cycle` regardless
    billed both — PR #186's e748c86 shows the counter reaching 2 with no `record-cycle` write
    behind it at all.
    """
    jobs = _spec()["jobs"]
    assert jobs["finalize"]["with"]["cycle_recorded"] == "${{ needs.record-cycle.result == 'success' }}"
    # The two panel-less paths have their own reasons to stamp: the cap records the count it
    # died on, and the inherited path re-states a held count on the head it just cleared.
    assert jobs["cap-exhausted"]["with"]["cycle_recorded"] is True
    assert jobs["inherit"]["with"]["cycle_recorded"] is True

    finalize = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "review-finalize.yml").read_text(encoding="utf-8")
    )
    # `on` is one of YAML 1.1's boolean spellings, so the trigger block parses under the key
    # `True`. Required and not defaulted, so a new caller has to say which kind of run it is
    # instead of silently billing one.
    triggers = finalize.get("on", finalize.get(True))
    assert triggers["workflow_call"]["inputs"]["cycle_recorded"]["required"] is True
    assert "default" not in triggers["workflow_call"]["inputs"]["cycle_recorded"]
    stamp = next(
        step
        for step in finalize["jobs"]["finalize"]["steps"]
        if step.get("name") == "Stamp review/cycle on post-fix SHA"
    )
    assert stamp["if"] == "inputs.cycle_recorded"
