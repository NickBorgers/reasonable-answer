"""QP3 as an executable check: the review panel stays family-diverse and names what it runs.

Two properties used to rest entirely on a reviewer noticing, and one of them had already been
quietly false for the whole life of the pipeline.

*Family diversity* — three Codex roles, two Claude, with `quality` cross-family from `invariant`
(D-quality-reviewer) — was policed only by the `quality` reviewer reading `review-pipeline.yml`.
That is the reviewer whose own model family the property constrains, so a flip that made the
panel single-family had to be caught by a member of the newly-single family.

*Naming the model* was not enforced at all. `review-agent-run` exposed a `model:` input and no
workflow ever passed it, so every role ran whatever its CLI defaulted to and the codex roles ran
a `gpt-5.5` literal buried in a shell heredoc. The panel's composition could change with no diff
(D-ci-model-pinning). These tests make both properties fail at `pytest` time instead.

Fully offline: it reads workflow YAML and one shell script from this repo, and runs
``scripts/ci-agent-model.sh``, which touches no network, no git, and no token.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "review-pipeline.yml"
REVIEWER = REPO_ROOT / ".github" / "workflows" / "review-reviewer.yml"
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"
RUN_IN_CONTAINER = REPO_ROOT / ".github" / "actions" / "review-agent-run" / "run-in-container.sh"
AGENT_MODEL_SH = REPO_ROOT / "scripts" / "ci-agent-model.sh"

# Which family an alias belongs to, keyed by the agent that must run it. A pin naming the wrong
# family would resolve on the wrong proxy path and 404 inside the container, a long way from
# the edit that caused it.
_FAMILY_PREFIX = {"claude": "claude-", "codex": "gpt-"}


def _reviewer_jobs() -> dict[str, dict]:
    """Every job in review-pipeline.yml that delegates to the reviewer workflow, by role."""
    spec = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    jobs = {}
    for job in spec["jobs"].values():
        if not str(job.get("uses", "")).endswith("review-reviewer.yml"):
            continue
        params = job.get("with", {})
        jobs[params["role"]] = params
    return jobs


def test_the_panel_delegates_the_roles_we_think_it_does() -> None:
    """Guards every other test here: they are vacuous if the parse finds nothing."""
    assert set(_reviewer_jobs()) == {"invariant", "docs", "security", "test", "quality"}


@pytest.mark.parametrize("role", ["invariant", "docs", "security", "test", "quality"])
def test_every_reviewer_role_pins_both_an_agent_and_a_model(role: str) -> None:
    params = _reviewer_jobs()[role]
    assert params.get("agent"), f"role '{role}' names no agent"
    assert params.get("model"), (
        f"role '{role}' names no model; it would run whichever checkpoint its CLI currently "
        f"defaults to, which is not a reviewable property of this repo (D-ci-model-pinning)"
    )


@pytest.mark.parametrize("role", ["invariant", "docs", "security", "test", "quality"])
def test_each_pin_names_a_model_its_agent_can_actually_run(role: str) -> None:
    params = _reviewer_jobs()[role]
    agent, model = params["agent"], params["model"]
    assert agent in _FAMILY_PREFIX, f"role '{role}' names unknown agent '{agent}'"
    assert model.startswith(_FAMILY_PREFIX[agent]), (
        f"role '{role}' runs agent '{agent}' but pins '{model}', which belongs to the other "
        f"family; it would be requested from the wrong proxy path"
    )


def test_the_panel_spans_at_least_two_agent_families() -> None:
    """QP3, first half. Agreement between same-family reviewers is correlated, not independent."""
    families = {params["agent"] for params in _reviewer_jobs().values()}
    assert len(families) >= 2, f"review panel collapsed to a single agent family: {families}"


def test_quality_stays_cross_family_from_invariant() -> None:
    """QP3, second half, and D-quality-reviewer.

    The guard on the spec's *direction* must not share blind spots with the guard on the spec's
    *conformance* — the panel applying QP3 to itself.
    """
    jobs = _reviewer_jobs()
    assert jobs["quality"]["agent"] != jobs["invariant"]["agent"], (
        "quality and invariant are the pair that deconflicts most heavily; running them on one "
        "family means a blind spot in it clears both the design's direction and its conformance"
    )


def test_the_reviewer_workflow_requires_a_model() -> None:
    """A role that forgot `model:` must fail the call, not silently inherit a default."""
    spec = yaml.safe_load(REVIEWER.read_text(encoding="utf-8"))
    # `on:` parses as the boolean True under YAML 1.1, so it cannot be looked up by name.
    triggers = next(v for k, v in spec.items() if k is True or k == "on")
    model = triggers["workflow_call"]["inputs"]["model"]
    assert model["required"] is True


def test_no_model_literal_survives_in_the_container_script() -> None:
    """The `gpt-5.5` default lived here, invisible to anyone reading the pipeline.

    A model named in the shell script is one that cannot be seen beside the role it applies to,
    which is the whole failure D-ci-model-pinning fixes. The codex path must fail closed on an
    unset `AGENT_MODEL` rather than substituting one.
    """
    text = RUN_IN_CONTAINER.read_text(encoding="utf-8")
    stray = re.findall(r"\b(?:gpt|claude)-[0-9a-z.]+[0-9a-z-]*\b", text)
    assert not stray, f"model literal(s) {stray} back in run-in-container.sh"
    assert "AGENT_MODEL:?" in text, "codex path no longer fails closed on an unpinned model"


def test_every_caller_of_the_agent_composite_passes_a_model() -> None:
    """The four call sites, not just the five reviewer roles.

    The reviewer path is the one with per-role pins, but the resolver and both fixer paths
    reach the same composite, and a call site that omits `model:` now fails the job at
    runtime. Catching it here means catching it in the PR that wrote it rather than in the
    run that needed it.
    """
    missing = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in (spec.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if not str(step.get("uses", "")).endswith("actions/review-agent-run"):
                    continue
                if not (step.get("with") or {}).get("model"):
                    missing.append(f"{workflow.name}::{job_name}::{step.get('name')}")
    assert not missing, f"review-agent-run called with no model pinned: {missing}"


def test_the_shared_agent_model_map_answers_for_both_agents() -> None:
    """The resolver and fixer pick their agent at runtime, so their model comes from here."""
    for agent, prefix in _FAMILY_PREFIX.items():
        done = subprocess.run(
            ["bash", str(AGENT_MODEL_SH), agent],
            capture_output=True,
            text=True,
            check=True,
        )
        assert done.stdout.strip().startswith(prefix)


def test_the_shared_map_refuses_an_agent_it_does_not_know() -> None:
    """Fail closed: a typo must stop the job, not resolve to an empty model."""
    done = subprocess.run(
        ["bash", str(AGENT_MODEL_SH), "gemini"],
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert not done.stdout.strip()


def test_the_cold_fixer_pin_is_resolved_through_the_shared_map() -> None:
    """The cold path names its agent in one place and derives the model, rather than repeating it.

    Two copies of the pin is how the cold path ends up on a retired alias: the copy nobody
    edited keeps working until the day it is the one that runs.
    """
    text = FIXER.read_text(encoding="utf-8")
    assert "cold_model=${COLD_MODEL}" in text
    assert "model: ${{ steps.session.outputs.cold_model }}" in text
    stray = re.findall(r"model:\s*(?:gpt|claude)-[0-9a-z.-]+", text)
    assert not stray, f"cold fixer pins a model literal {stray} instead of resolving it"
