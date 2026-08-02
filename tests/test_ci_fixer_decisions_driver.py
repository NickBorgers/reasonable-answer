"""Offline checks for the fixer's trusted decisions merge-driver registrations."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXER = REPO_ROOT / ".github" / "workflows" / "review-fixer.yml"


def _step(step_id: str) -> dict:
    workflow = yaml.safe_load(FIXER.read_text(encoding="utf-8"))
    return next(step for step in workflow["jobs"]["fix"]["steps"] if step.get("id") == step_id)


def test_sync_registers_the_driver_from_the_trusted_main_checkout() -> None:
    run = _step("sync")["run"]

    assert "git config merge.decisions-append.driver" in run
    assert 'python3 ${GITHUB_WORKSPACE}/scripts/merge_decisions.py %O %A %B' in run
    assert "${PR_WORKSPACE}/scripts/merge_decisions.py" not in run


def test_cold_replay_repeats_the_trusted_driver_registration() -> None:
    run = _step("fallback")["run"]

    assert 'git -C "${PR_DIR}" config merge.decisions-append.driver' in run
    assert 'python3 ${GITHUB_WORKSPACE}/scripts/merge_decisions.py %O %A %B' in run
    assert "${PR_DIR}/scripts/merge_decisions.py" not in run
