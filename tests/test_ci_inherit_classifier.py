"""The merge-from-base inherit short-circuit, run as code rather than read as YAML.

`review-pipeline.yml`'s "Detect merge-from-main" step decides whether a push is re-stamped
with the previous verdict or handed to the panel. Getting that predicate wrong is a merge-gate
bypass, not a cost bug: for the life of the pipeline it tested the *shape of the head commit*
— "HEAD is a merge whose second parent is on the base branch" — which is silent about
everything committed underneath that merge. Push content, then `git merge origin/main`, and
the prior verdict was re-published over content nobody read (D-inherit-whole-range; observed
on #126, #127 and #130, each having real fixes re-stamped with a stale NO-GO).

The second version of the same failure was not in the predicate but in what it measured
from: both tests anchored on the SHA the cycle counter was recorded on, which *is* the head
being classified whenever the fixer's push carried it. `SHA..SHA` is empty and a merge-tree
of a head against its own already-merged parent reproduces that head's tree, so a fixer push
never followed by another commit could not be re-reviewed at all (#163, observed on PR #162).
The anchor is now the `review/reviewed-sha` status — the commit the inherited verdict was
published for (D-inherit-reviewed-anchor).

There is no way to exercise that step short of running it, so these tests extract the `run:`
block from the workflow and drive it under `bash` against throwaway git repositories built
here. Fully offline: real `git`, a stub `gh` on `PATH` that answers the one status query the
step makes, no network and no token.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "review-pipeline.yml"

PRIOR_VERDICT = "GO"


def _inherit_step_script() -> str:
    """The shell body of the `inherit` step, exactly as CI runs it."""
    spec = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    for step in spec["jobs"]["gather"]["steps"]:
        if step.get("id") == "inherit":
            return step["run"]
    raise AssertionError("review-pipeline.yml no longer has a gather step with id 'inherit'")


# Pinned so a commit's SHA depends only on its content and its parents, which keeps a failing
# case reproducible from the log it printed.
_FIXED_DATE = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


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


def _write(repo: Path, name: str, body: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Bench:
    """An `origin` bare repo plus a working clone, standing in for `pr-head` in CI.

    `main` is the base branch and `pr` is the PR branch. Every helper returns the SHA it
    created, because that is what the step is keyed on.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "pr-head"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True
        )
        subprocess.run(
            ["git", "clone", "-q", str(self.origin), str(self.work)], check=True
        )
        _git(self.work, "config", "user.email", "test@example.invalid")
        _git(self.work, "config", "user.name", "test")
        _write(self.work, "README.md", "base\n")
        self.base_root = _commit(self.work, "root")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "checkout", "-q", "-b", "pr")

    def advance_main(self, name: str, body: str = "") -> str:
        """A commit on the base branch, as another merged PR would leave it."""
        current = _git(self.work, "rev-parse", "--abbrev-ref", "HEAD")
        _git(self.work, "checkout", "-q", "main")
        _write(self.work, name, body or f"{name}\n")
        sha = _commit(self.work, f"main: {name}")
        _git(self.work, "push", "-q", "origin", "main")
        _git(self.work, "checkout", "-q", current)
        return sha

    def commit_on_pr(self, name: str, body: str = "") -> str:
        _write(self.work, name, body or f"{name}\n")
        return _commit(self.work, f"pr: {name}")

    def seed_shared_files(self, files: dict[str, str]) -> str:
        """Commit files while `pr` and `main` still coincide (right after construction),
        then fast-forward both refs. Used to give `main` and `pr` a common ancestor that
        already carries docs/decisions.md and .gitattributes, before either branch appends
        a decision of its own (D-decisions-merge-driver)."""
        for name, body in files.items():
            _write(self.work, name, body)
        sha = _commit(self.work, "seed " + ", ".join(sorted(files)))
        _git(self.work, "push", "-q", "origin", "HEAD:main")
        _git(self.work, "branch", "-f", "main", "HEAD")
        return sha

    def install_trusted_driver_script(self) -> None:
        """Populate `$GITHUB_WORKSPACE/main-checkout/scripts/` with the real driver and the
        registration helper that smoke-tests it, mirroring review-pipeline.yml's trusted
        `main-checkout` layout — the registration under test runs from there, not from the
        untrusted `pr-head` working copy. Omitting this stands in for a `main` older than
        D-decisions-merge-driver, where the step registers nothing at all."""
        scripts = self.work.parent / "main-checkout" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for name in ("merge_decisions.py", "register_decisions_driver.sh"):
            dest = scripts / name
            dest.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC)

    def merge_main(self) -> str:
        _git(self.work, "fetch", "-q", "origin", "main")
        _git(self.work, "merge", "-q", "--no-ff", "-m", "merge main into pr", "origin/main")
        return _git(self.work, "rev-parse", "HEAD")

    def run(
        self,
        script: str,
        sha: str,
        prior_sha: str,
        *,
        force_review: str = "false",
        verdict: str = PRIOR_VERDICT,
        last_reviewed: str | None = None,
    ) -> dict:
        """Run the extracted step and return its `$GITHUB_OUTPUT` as a dict.

        `last_reviewed` is the `review/reviewed-sha` anchor the step reads off the
        cycle-recorded SHA — the commit the prior verdict was published *for*
        (D-inherit-reviewed-anchor). It defaults to `prior_sha` because that is what the
        two coincide to on every path where the fixer did not push; pass it explicitly to
        model a fixer push recorded onto the cycle it fixed, or `""` for a cycle recorded
        before this status existed.
        """
        output = self.work.parent / "github_output"
        output.write_text("", encoding="utf-8")
        stub_bin = self.work.parent / "bin"
        stub_bin.mkdir(exist_ok=True)
        gh = stub_bin / "gh"
        # Two API calls, both "what does this context say on the cycle-recorded SHA".
        # Dispatched on the context named in the --jq filter, so a step that queried the
        # wrong one would read an empty answer rather than silently getting the other's.
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'if printf "%s\\n" "$@" | grep -q "review/reviewed-sha"; then\n'
            '  if [ -n "${GH_STUB_REVIEWED_SHA:-}" ]; then printf "%s\\n" "$GH_STUB_REVIEWED_SHA"; fi\n'
            'elif printf "%s\\n" "$@" | grep -q "review/verdict"; then\n'
            '  if [ -n "${GH_STUB_VERDICT:-}" ]; then printf "%s\\n" "$GH_STUB_VERDICT"; fi\n'
            "else\n"
            '  echo "gh stub: unexpected query: $*" >&2; exit 1\n'
            "fi\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)

        env = {
            **os.environ,
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": "stub",
            "GH_STUB_VERDICT": verdict,
            "GH_STUB_REVIEWED_SHA": prior_sha if last_reviewed is None else last_reviewed,
            "REPO": "owner/repo",
            "SHA": sha,
            "BASE_REF": "main",
            "PRIOR_CYCLE_SHA": prior_sha,
            "FORCE_REVIEW": force_review,
            "GITHUB_OUTPUT": str(output),
            # Real review-pipeline.yml sets this for every job; the docs-decisions.md
            # driver registration resolves the trusted script relative to it
            # (D-decisions-merge-driver). Harmless for tests that never touch
            # docs/decisions.md -- the driver is only invoked when that path conflicts.
            "GITHUB_WORKSPACE": str(self.work.parent),
        }
        proc = subprocess.run(
            ["bash", "-c", script],
            cwd=self.work,
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, f"step failed:\n{proc.stdout}\n{proc.stderr}"
        parsed = {}
        for line in output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            parsed[key] = value
        parsed["_log"] = proc.stdout
        return parsed


@pytest.fixture()
def script() -> str:
    return _inherit_step_script()


@pytest.fixture()
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


def test_pure_resync_still_inherits(bench: Bench, script: str) -> None:
    """The optimisation the short-circuit exists for has to survive the fix.

    A long-lived branch resyncing with a moved base is why this path exists at all — the
    anchor-conflict rebase churn across eight concurrent PRs was only affordable because a
    resync does not burn a cycle.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "true"
    assert result["inherited_verdict"] == PRIOR_VERDICT


def test_stacked_resyncs_still_inherit(bench: Bench, script: str) -> None:
    """Several base merges in one push are still only base merges."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("first.txt")
    bench.merge_main()
    bench.advance_main("second.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "true"


def test_content_under_a_head_merge_is_reviewed(bench: Bench, script: str) -> None:
    """The bug, in the direction that bypasses the merge gate.

    Content commits, then `git merge origin/main` on top. The head is a textbook base-resync
    merge and the push is not.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.commit_on_pr("smuggled.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert result["inherited_verdict"] == ""
    assert "content commit under the head merge" in result["_log"]


def test_content_between_two_base_merges_is_reviewed(bench: Bench, script: str) -> None:
    """The observed shape on #126: content sandwiched between resyncs."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("first.txt")
    bench.merge_main()
    bench.commit_on_pr("fix.txt")
    bench.advance_main("second.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"


def test_merge_of_an_unrelated_branch_under_the_head_is_reviewed(bench: Bench, script: str) -> None:
    """Only merges *from the base branch* are free; a side branch is new content."""
    reviewed = bench.commit_on_pr("feature.txt")
    _git(bench.work, "checkout", "-q", "-b", "side", bench.base_root)
    bench.commit_on_pr("side.txt")
    _git(bench.work, "checkout", "-q", "pr")
    _git(bench.work, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "which is not on main" in result["_log"]


def test_head_merge_of_an_unrelated_branch_is_reviewed(bench: Bench, script: str) -> None:
    """A head that directly merges a side branch fails the top-level second-parent guard."""
    reviewed = bench.commit_on_pr("feature.txt")
    _git(bench.work, "checkout", "-q", "-b", "side", bench.base_root)
    bench.commit_on_pr("side.txt")
    _git(bench.work, "checkout", "-q", "pr")
    _git(bench.work, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    head = _git(bench.work, "rev-parse", "HEAD")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "^2 is not on main" in result["_log"]


def test_a_merge_carrying_extra_content_is_reviewed(bench: Bench, script: str) -> None:
    """Shape alone cannot see this, which is why the tree is checked too.

    The head is a two-parent merge, its second parent is on the base branch, and nothing sits
    under it — it passes every shape test there is. Its tree carries a file neither side
    wrote, which is what "merge the base and edit while you are in there" looks like.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")

    _git(bench.work, "fetch", "-q", "origin", "main")
    _git(bench.work, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
    _write(bench.work, "backdoor.txt", "content no reviewer read\n")
    _git(bench.work, "add", "-A")
    _git(bench.work, "commit", "-q", "-m", "merge main into pr")
    head = _git(bench.work, "rev-parse", "HEAD")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "recreating the merge yields tree" in result["_log"]


def test_a_hand_resolved_conflict_is_reviewed(bench: Bench, script: str) -> None:
    """A resolution is a judgement call, and nothing has read this one.

    The merge cannot be recreated without conflicts, so there is no tree to compare against
    and the step fails closed onto the panel.
    """
    bench.commit_on_pr("shared.txt", "pr side\n")
    reviewed = _git(bench.work, "rev-parse", "HEAD")

    _git(bench.work, "checkout", "-q", "main")
    _write(bench.work, "shared.txt", "main side\n")
    _commit(bench.work, "main: shared")
    _git(bench.work, "push", "-q", "origin", "main")
    _git(bench.work, "checkout", "-q", "pr")

    _git(bench.work, "fetch", "-q", "origin", "main")
    subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", "origin/main"],
        cwd=bench.work,
        capture_output=True,
        text=True,
    )
    _write(bench.work, "shared.txt", "pr side\nmain side\n")
    _git(bench.work, "add", "-A")
    _git(bench.work, "commit", "-q", "-m", "merge main into pr")
    head = _git(bench.work, "rev-parse", "HEAD")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "could not cleanly recreate" in result["_log"]


def test_merge_that_drops_base_content_is_reviewed(bench: Bench, script: str) -> None:
    """`-s ours` produces a perfectly-shaped merge whose tree is not a merge at all."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    _git(bench.work, "fetch", "-q", "origin", "main")
    _git(bench.work, "merge", "-q", "-s", "ours", "-m", "merge main into pr", "origin/main")
    head = _git(bench.work, "rev-parse", "HEAD")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "recreating the merge yields tree" in result["_log"]


def test_force_review_outranks_a_pure_resync(bench: Bench, script: str) -> None:
    """PR #56's lesson: `/review` is the human override and must reach the panel."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed, force_review="true")
    assert result["inherit"] == "false"


def test_no_prior_cycle_sha_reviews_normally(bench: Bench, script: str) -> None:
    """First cycle on a PR: there is no verdict to inherit and no range to measure."""
    bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, "")
    assert result["inherit"] == "false"


def test_no_prior_verdict_reviews_normally(bench: Bench, script: str) -> None:
    """A recorded cycle whose verdict status is missing must not inherit an empty string."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    assert bench.run(script, head, reviewed)["inherit"] == "true"  # the verdict is the only variable

    empty = bench.run(script, head, reviewed, verdict="")
    assert empty["inherit"] == "false"
    assert "no prior verdict to inherit" in empty["_log"]


def test_a_plain_content_push_is_reviewed(bench: Bench, script: str) -> None:
    """The ordinary case, unchanged: a non-merge head was never inherited."""
    reviewed = bench.commit_on_pr("feature.txt")
    head = bench.commit_on_pr("more.txt")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"


def test_a_fixer_push_recorded_on_its_own_cycle_is_reviewed(bench: Bench, script: str) -> None:
    """The bug this anchor closes, in the shape observed on PR #162, run 31190337154.

    Cycle 1's panel read `reviewed`; the cold fixer then pushed a merge that resolved
    conflicts with the base *and* addressed the blockers, and `review-finalize.yml` stamped
    `review/cycle` on that push. So the next run's cycle-recorded SHA **is** the head being
    classified: the range `head..head` is empty, and `merge-tree` of a head against its own
    already-merged second parent trivially reproduces the head's tree. Everything inherits,
    and three files of fixes were re-stamped with the pre-fix NO-GO.

    The anchor is the only variable here — the second run below is the old behaviour,
    reproduced by pointing the anchor back at the cycle-recorded SHA — so this fails on a
    revert of the anchor change and on nothing else.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")

    _git(bench.work, "fetch", "-q", "origin", "main")
    _git(bench.work, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
    _write(bench.work, "blocker-fix.txt", "the fix the panel never read\n")
    _git(bench.work, "add", "-A")
    _git(bench.work, "commit", "-q", "-m", "merge: resolve conflicts + address reviewer findings")
    fixer_push = _git(bench.work, "rev-parse", "HEAD")

    result = bench.run(script, fixer_push, fixer_push, last_reviewed=reviewed)
    assert result["inherit"] == "false"
    assert result["inherited_verdict"] == ""
    assert "recreating the merge yields tree" in result["_log"]

    anchored_on_the_cycle_sha = bench.run(script, fixer_push, fixer_push, last_reviewed=fixer_push)
    assert anchored_on_the_cycle_sha["inherit"] == "true", "the anchor is what decides this"


def test_a_resync_stacked_on_a_fixer_push_is_reviewed(bench: Bench, script: str) -> None:
    """The same bug one commit further along, which the whole-range test alone cannot see.

    A clean base resync lands on top of the fixer's push. Every commit in the range is a
    merge whose merged-in parents are on the base branch, so test 1 passes — it is the tree
    recreation, measured from the commit the panel actually read, that still refuses.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("first.txt")
    _git(bench.work, "fetch", "-q", "origin", "main")
    _git(bench.work, "merge", "-q", "--no-commit", "--no-ff", "origin/main")
    _write(bench.work, "blocker-fix.txt", "the fix the panel never read\n")
    _git(bench.work, "add", "-A")
    _git(bench.work, "commit", "-q", "-m", "merge: resolve conflicts + address reviewer findings")
    fixer_push = _git(bench.work, "rev-parse", "HEAD")

    bench.advance_main("second.txt")
    head = bench.merge_main()

    result = bench.run(script, head, fixer_push, last_reviewed=reviewed)
    assert result["inherit"] == "false"
    assert "recreating the merge yields tree" in result["_log"]


def test_an_unchanged_reviewed_head_inherits(bench: Bench, script: str) -> None:
    """Re-entering on the very commit the verdict was published for reads nothing new.

    `review-entry.yml` fires on `reopened` and `ready_for_review` as well as `synchronize`,
    and `cleanup-claim` releases the dedup claim on every path, so gather can run again on a
    head whose verdict is already published. There is no range to walk and no merge to
    recreate; re-stamping the verdict that head already carries costs nothing, while
    reviewing would spend a cycle re-reading a commit the panel has read. Note the head here
    is not a merge commit at all — the shape tests below this branch would have refused it.
    """
    head = bench.commit_on_pr("feature.txt")

    result = bench.run(script, head, head, last_reviewed=head)
    assert result["inherit"] == "true"
    assert result["inherited_verdict"] == PRIOR_VERDICT
    assert "nothing has been pushed since" in result["_log"]


def test_an_unchanged_head_with_no_verdict_reviews_normally(bench: Bench, script: str) -> None:
    """The trivial-inherit branch is not a way around "there must be a verdict to inherit"."""
    head = bench.commit_on_pr("feature.txt")

    result = bench.run(script, head, head, last_reviewed=head, verdict="")
    assert result["inherit"] == "false"
    assert "no prior verdict to inherit" in result["_log"]


def test_a_missing_anchor_reviews_normally(bench: Bench, script: str) -> None:
    """A cycle recorded before this status existed says nothing about what was read.

    It could be the commit a panel read or the commit a fixer pushed, and those are the
    inherit decision and its inverse. Fail closed: one full read, after which the next
    finalize writes the anchor and the PR inherits normally again.
    """
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    assert bench.run(script, head, reviewed)["inherit"] == "true"  # the anchor is the variable

    missing = bench.run(script, head, reviewed, last_reviewed="")
    assert missing["inherit"] == "false"
    assert "carries no review/reviewed-sha anchor" in missing["_log"]


def test_a_malformed_anchor_reviews_normally(bench: Bench, script: str) -> None:
    """Anything that is not a full 40-hex object id is not an anchor either."""
    reviewed = bench.commit_on_pr("feature.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed, last_reviewed=reviewed[:7])
    assert result["inherit"] == "false"
    assert "carries no review/reviewed-sha anchor" in result["_log"]


def test_reviewed_sha_no_longer_in_history_is_reviewed(bench: Bench, script: str) -> None:
    """A force-push replaces the chain, so "everything since" names nothing measurable."""
    reviewed = bench.commit_on_pr("feature.txt")
    _git(bench.work, "reset", "-q", "--hard", bench.base_root)
    bench.commit_on_pr("rewritten.txt")
    bench.advance_main("other.txt")
    head = bench.merge_main()

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "is not an ancestor of" in result["_log"]


_DECISIONS_ATTRS = ".gitattributes"
_DECISIONS_FILE = "docs/decisions.md"
_DECISIONS_BASE = "# Decisions\n\n## Open items for a future round\n"


def _decisions_with(slug: str) -> str:
    return f"# Decisions\n\n## {slug} — scratch\n\nbody.\n\n## Open items for a future round\n"


def test_decisions_driver_resync_still_inherits(bench: Bench, script: str) -> None:
    """A docs/decisions.md merge the append-only driver resolved must recreate to the same
    tree, so the resync it exists for still inherits (D-decisions-merge-driver).

    `pr` and `main` each append a *different* decision before the tail marker -- exactly the
    shape that conflicts in a plain 3-way merge. review-fixer.yml's sync resolves it with the
    driver; this proves review-pipeline.yml's recreation, using the same driver from its own
    trusted-checkout registration, reproduces the identical tree rather than mismatching.
    """
    bench.seed_shared_files({_DECISIONS_ATTRS: "docs/decisions.md merge=decisions-append\n",
                              _DECISIONS_FILE: _DECISIONS_BASE})
    bench.install_trusted_driver_script()
    _git(bench.work, "config", "merge.decisions-append.driver",
         f"python3 {REPO_ROOT / 'scripts' / 'merge_decisions.py'} %O %A %B")

    reviewed = bench.commit_on_pr(_DECISIONS_FILE, _decisions_with("D-pr-side"))
    bench.advance_main(_DECISIONS_FILE, _decisions_with("D-main-side"))
    head = bench.merge_main()
    # The registration above stood in for review-fixer.yml resolving the merge, which happens
    # in a different checkout entirely. `pr-head` is a fresh `actions/checkout` and carries no
    # merge config of its own, so dropping it here is what makes the step's own registration
    # -- the thing under test -- the only one that can affect the recreation below.
    _git(bench.work, "config", "--unset", "merge.decisions-append.driver")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "true", result["_log"]
    assert result["inherited_verdict"] == PRIOR_VERDICT


def test_decisions_driver_resync_fails_closed_without_the_trusted_script(
    bench: Bench, script: str
) -> None:
    """Sanity check for the test above, and the regression this fix closes: if the trusted
    copy the registration points at is missing, recreation fails closed to a full review
    rather than matching by coincidence -- proving the registration is load-bearing, not
    redundant with something else that would have inherited anyway.
    """
    bench.seed_shared_files({_DECISIONS_ATTRS: "docs/decisions.md merge=decisions-append\n",
                              _DECISIONS_FILE: _DECISIONS_BASE})
    # Deliberately no install_trusted_driver_script(): $GITHUB_WORKSPACE/main-checkout does
    # not exist, so the step's own registration points at a script that cannot run.
    _git(bench.work, "config", "merge.decisions-append.driver",
         f"python3 {REPO_ROOT / 'scripts' / 'merge_decisions.py'} %O %A %B")

    reviewed = bench.commit_on_pr(_DECISIONS_FILE, _decisions_with("D-pr-side"))
    bench.advance_main(_DECISIONS_FILE, _decisions_with("D-main-side"))
    head = bench.merge_main()
    # The registration above stood in for review-fixer.yml resolving the merge, which happens
    # in a different checkout entirely. `pr-head` is a fresh `actions/checkout` and carries no
    # merge config of its own, so dropping it here is what makes the step's own registration
    # -- the thing under test -- the only one that can affect the recreation below.
    _git(bench.work, "config", "--unset", "merge.decisions-append.driver")

    result = bench.run(script, head, reviewed)
    assert result["inherit"] == "false"
    assert "could not cleanly recreate" in result["_log"]
