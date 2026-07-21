from __future__ import annotations

import pytest

from reasonable_answer import shutdown
from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.schemas import (
    ControllerInput,
    LensStatus,
    OrchestratorView,
    SeverityCounts,
)
from reasonable_answer.taxonomy import LENSES


@pytest.fixture(autouse=True)
def _clean_shutdown_flag():
    """The stop flag is a module global, so one test that shuts a worker down would
    otherwise leave every later run pausing immediately at its first node boundary."""
    shutdown.reset()
    yield
    shutdown.reset()


@pytest.fixture
def roster() -> Roster:
    return Roster(
        writers=["writer-a", "writer-b"],
        critics={
            "logic": ["logic-spec", "writer-a", "writer-b"],
            "evidence": ["evidence-spec", "writer-a", "writer-b"],
            "completeness": ["completeness-spec", "writer-a", "writer-b"],
        },
    )


@pytest.fixture
def identities() -> dict[str, str]:
    return {
        "writer-a": "vendor-a/model-a",
        "writer-b": "vendor-b/model-b",
        "logic-spec": "vendor-c/logic",
        "evidence-spec": "vendor-d/evidence",
        "completeness-spec": "vendor-e/completeness",
    }


@pytest.fixture
def config(roster: Roster, tmp_path) -> Config:
    return Config(
        proxy=ProxyConfig(),
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=5, polish_cap=1),
        runs_dir=tmp_path / "runs",
    )


def make_view(**overrides) -> OrchestratorView:
    base = dict(
        counts={},
        totals=SeverityCounts(),
        delta_material_vs_prev=0,
        lenses_failed=0,
        round=3,
        min_ticks=2,
        hard_cap=8,
        roster_size=5,
        lens_cleared={lens.value: 0 for lens in LENSES},
        acceptance="none",
        polish_used=0,
        polish_cap=1,
        stagnation_count=0,
        cycle_detected=False,
    )
    totals = overrides.pop("totals", None)
    if isinstance(totals, dict):
        totals = SeverityCounts(**totals)
    if totals is not None:
        base["totals"] = totals
    base.update(overrides)
    return OrchestratorView(**base)


def make_ci(view: OrchestratorView | None = None, *, lens_status=None, **overrides):
    view = view or make_view()
    if lens_status is None:
        lens_status = [
            LensStatus(lens=lens, cleared_count=0, eligible_count=3, unused_eligible=3)
            for lens in LENSES
        ]
    base = dict(
        view=view,
        fatal=False,
        run_id="run-test",
        artifact_hash="a" * 64,
        artifact_hash_history=["a" * 64],
        author_identity="vendor-a/model-a",
        lens_status=lens_status,
        critique_attempts_remaining=6,
        confirmation_attempts_remaining=6,
        polish_recommended=False,
        stagnation_limit=3,
        cycle_period=4,
    )
    base.update(overrides)
    return ControllerInput(**base)


def cleared(counts: dict[str, int], eligible: int = 3, unused: int = 3) -> list[LensStatus]:
    return [
        LensStatus(
            lens=lens,
            cleared_count=counts.get(lens.value, 0),
            eligible_count=eligible,
            unused_eligible=unused,
        )
        for lens in LENSES
    ]
