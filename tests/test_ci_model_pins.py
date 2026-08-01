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

Fully offline: it reads workflow YAML and one shell script from this repo, and runs the
``agent_model()`` snippet extracted from that YAML under ``bash``. No network, no git, no token.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / ".github" / "workflows" / "review-pipeline.yml"
REVIEWER = REPO_ROOT / ".github" / "workflows" / "review-reviewer.yml"
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"
RUN_IN_CONTAINER = REPO_ROOT / ".github" / "actions" / "review-agent-run" / "run-in-container.sh"
RESOLVER = REPO_ROOT / ".github" / "workflows" / "resolve-issue.yml"

# The `agent_model()` shell function inlined in the resolver and the fixer. It is duplicated on
# purpose — see the comment at either site — so the check that keeps the copies honest lives
# here rather than in a shared file the pipeline could not read.
_AGENT_MODEL_CASE_RE = re.compile(r"^\s*(claude|codex)\)\s*echo\s*\"([^\"]+)\"", re.M)

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
    which is the whole failure D-ci-model-pinning fixes.

    Scope note: this asserts only that no alias is *written* here. That the script *refuses* an
    unpinned model is a behavioural claim, and is proved by running it —
    ``tests/test_run_in_container.py::test_an_unpinned_model_is_refused_on_the_codex_path`` and
    its claude twin — rather than by matching the guard's own source text here.
    """
    text = RUN_IN_CONTAINER.read_text(encoding="utf-8")
    stray = re.findall(r"\b(?:gpt|claude)-[0-9a-z.]+[0-9a-z-]*\b", text)
    assert not stray, f"model literal(s) {stray} back in run-in-container.sh"


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


def _inline_agent_model_map(workflow: Path) -> dict[str, str]:
    return dict(_AGENT_MODEL_CASE_RE.findall(workflow.read_text(encoding="utf-8")))


@pytest.mark.parametrize("workflow", [RESOLVER, FIXER], ids=lambda p: p.name)
def test_the_runtime_agent_map_answers_for_both_agents(workflow: Path) -> None:
    """The resolver and fixer pick their agent at runtime, so each carries the map inline."""
    mapping = _inline_agent_model_map(workflow)
    assert set(mapping) == set(_FAMILY_PREFIX), f"{workflow.name} maps {set(mapping)}"
    for agent, model in mapping.items():
        assert model.startswith(_FAMILY_PREFIX[agent]), (
            f"{workflow.name} maps agent '{agent}' to '{model}', which is the other family's"
        )


def test_the_two_inline_copies_of_the_map_agree() -> None:
    """The price of inlining, paid here.

    The map cannot live in a shared script: the review pipeline runs its own logic from main's
    checkout, so a helper file a PR adds does not exist for the run that reviews that PR — it
    dies at exit 127. Workflow YAML ships with the PR; a new file in the tree does not. So the
    copies are deliberate, and this is what stops them drifting apart.
    """
    assert _inline_agent_model_map(RESOLVER) == _inline_agent_model_map(FIXER)


@pytest.mark.parametrize("workflow", [RESOLVER, FIXER], ids=lambda p: p.name)
def test_the_runtime_map_refuses_an_agent_it_does_not_know(workflow: Path) -> None:
    """Fail closed: a typo must stop the job, not resolve to an empty model.

    Extracts the shell function as written and runs it, so this tests the deployed text rather
    than a restatement of it.
    """
    text = workflow.read_text(encoding="utf-8")
    body = re.search(r"agent_model\(\) \{.*?\n(\s*)\}\n", text, re.S)
    assert body, f"no agent_model() function found in {workflow.name}"
    # The function is indented inside a YAML block scalar; dedent so bash can parse it.
    snippet = textwrap.dedent(body.group(0))

    done = subprocess.run(
        ["bash", "-c", f"{snippet}\nagent_model gemini"],
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert not done.stdout.strip()

    ok = subprocess.run(
        ["bash", "-c", f"{snippet}\nagent_model codex"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ok.stdout.strip() == _inline_agent_model_map(workflow)["codex"]


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


def test_the_pipeline_never_shells_out_to_a_script_a_pr_could_add() -> None:
    """The regression that took the fixer down on the PR that introduced these pins.

    review-fixer.yml and review-reviewer.yml run the pipeline's own logic from *main's*
    checkout, deliberately, so that a PR cannot edit the pipeline reviewing it. The corollary
    is easy to miss: a `scripts/` helper added by a PR is absent for that PR's own review, and
    the step dies at exit 127 with no reviewer having said anything wrong. Anything these
    workflows need at runtime has to be in the workflow text itself, or already on main.
    """
    known_on_main = {"ci-session-store.sh"}
    for workflow in (FIXER, REVIEWER):
        called = set(re.findall(r"\./scripts/([a-z0-9-]+\.sh)", workflow.read_text("utf-8")))
        assert called <= known_on_main, (
            f"{workflow.name} calls {sorted(called - known_on_main)} from the main checkout; "
            f"if a PR adds that file, the PR's own review cannot see it"
        )
