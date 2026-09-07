"""The `pipeline_error` verdict is wired to name its own cause.

A guard that refuses produces no reviewer artifact, so by the time the judge runs the
refusal has left no trace in anything the verdict can read. That is why the verdict used to
describe the empty directory it found — "no reviewer artifacts (reviews skipped?)" — and
sent every reader to the reviewers and the orchestrator, when the cause was normally a red
`PR Validation Required` that a guard had already read and logged one job upstream
(D-pipeline-error-names-its-cause).

The reason now travels: guard output -> reviewer workflow output -> the pipeline's judge
call -> the judge's environment. `judge.test.mjs` and `reviewer-guard.test.mjs` cover the
two ends. These pin the wire between them, because every one of those tests still passes if
the pipeline forgets to pass the value on — the feature would simply be inert in production.

Offline: reads workflow YAML from this repo. No network, no git, no token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "review-pipeline.yml"
REVIEWER = REPO_ROOT / ".github" / "workflows" / "review-reviewer.yml"
JUDGE = REPO_ROOT / ".github" / "workflows" / "review-judge.yml"

ROLES = ["invariant", "docs", "security", "test", "quality"]


def _spec(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_reviewer_workflow_publishes_its_guard_s_refusal() -> None:
    spec = _spec(REVIEWER)
    outputs = spec[True]["workflow_call"]["outputs"]
    assert "skip_reason" in outputs, (
        "the guard's reason must leave the reviewer workflow, or the judge has no way to "
        "learn why a role never ran"
    )
    assert "jobs.guard.outputs.reason" in outputs["skip_reason"]["value"]
    assert "reason" in spec["jobs"]["guard"]["outputs"]


def test_the_judge_call_collects_every_role_s_reason() -> None:
    """All five, not just the one whose absence is noticed first: the guards can refuse for
    different reasons, and a verdict naming only one of them is a new way to mislead."""
    judge_call = _spec(PIPELINE)["jobs"]["judge"]
    passed = judge_call["with"].get("guard_skip_reasons")
    assert passed, "the judge is called without the guards' reasons; the feature is inert"
    for role in ROLES:
        assert f"needs.review-{role}.outputs.skip_reason" in passed, (
            f"role '{role}' refusal would never reach the verdict"
        )


def test_the_judge_workflow_hands_them_to_the_judge_script() -> None:
    steps = _spec(JUDGE)["jobs"]["judge"]["steps"]
    env = next(s["env"] for s in steps if "env" in s and "REVIEWER_DIR" in s.get("env", {}))
    assert env.get("GUARD_SKIP_REASONS") == "${{ inputs.guard_skip_reasons }}", (
        "judge.mjs reads GUARD_SKIP_REASONS; without this the input is accepted and dropped"
    )


@pytest.mark.parametrize("role", ROLES)
def test_every_reviewer_job_can_report_a_reason(role: str) -> None:
    """Guards the collection test above: it is vacuous if a role is not a reviewer job."""
    job = _spec(PIPELINE)["jobs"][f"review-{role}"]
    assert str(job.get("uses", "")).endswith("review-reviewer.yml")


def test_the_comment_no_longer_guesses_at_the_cause() -> None:
    """The rendered blurb used to name "a reviewer or orchestration bug" as the usual cause.
    That guess is what cost the reading time: the true cause was one job upstream."""
    render = (REPO_ROOT / ".github" / "scripts" / "review" / "render-finalize-comment.sh").read_text()
    assert "usually a reviewer or orchestration bug" not in render
    assert "the reason below says what stopped the panel" in render
