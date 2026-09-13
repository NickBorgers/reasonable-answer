"""Finalize ships the *chosen* draft's defects, not the terminal round's (issue #93).

On any non-accepted terminal, `graph._finalize` ships the *selected* draft from any
round rather than the last one written — under either `review.selection` rule
(D-latest-unblocked-selection). The "Outstanding defects in this report" list an
export renders must then describe *that* draft — the shipped artifact — not the artifact
the loop happened to stop on. Otherwise the report is charged with defects raised against
text it never contained, the mirror image of the clean-record hazard `_reviewers` guards.

The fixture is the shape of production run `run-d3bb2e4d2d94`: chosen round 1, terminal
round 8, `exhausted_unresolved`.
"""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient

import reasonable_answer.graph as graph
from reasonable_answer import export
from reasonable_answer import report as report_mod

R1_TEXT = "# Answer\n\nThe round-one draft, shipped because it scored best [1].\n"
R8_TEXT = "# Answer\n\nThe round-eight draft the loop stopped on [1].\n"


def _defect(instruction: str) -> dict:
    return {
        "severity": "major",
        "category": "overstated_claim",
        "instruction": instruction,
    }


def _runtime(config, identities, run_id):
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: None,
        report_fn=lambda n: R8_TEXT,
    )
    return graph.Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=graph.RunStore(config.runs_dir, run_id),
    )


def _terminal_state(run_id):
    """A run that stopped on round 8 but whose selected draft is round 1.

    Round 8 carries a blocking issue and round 1 does not, so both selection rules
    choose round 1: `latest_unblocked` because round 1 is the only row at the minimum
    blocking count, `fewest_defects` because 100 beats 10. What this file is about —
    that the shipped draft's own defects are the ones persisted — is therefore asserted
    under both rules rather than only under whichever one is the default.
    """
    return {
        "run_id": run_id,
        "terminal_status": "exhausted_unresolved",
        "round": 8,
        "report": R8_TEXT,
        "defects": [_defect("Fix the round-eight claim.")],
        "scoreboard": [
            {
                "round": 1,
                "artifact_hash": report_mod.artifact_hash(R1_TEXT),
                "blocking": 0,
                "major": 1,
                "minor": 0,
                "report": R1_TEXT,
                "defects": [_defect("Fix the round-one claim.")],
            },
            {
                "round": 8,
                "artifact_hash": report_mod.artifact_hash(R8_TEXT),
                "blocking": 1,
                "major": 5,
                "minor": 0,
                "report": R8_TEXT,
                "defects": [_defect("Fix the round-eight claim.")],
            },
        ],
    }


@pytest.mark.parametrize("mode", ["latest_unblocked", "fewest_defects"])
def test_finalize_persists_the_shipped_drafts_own_defects(config, identities, mode):
    config.review.selection = mode
    graph._finalize(_terminal_state("run-93"), _runtime(config, identities, "run-93"))
    summary = json.loads((config.runs_dir / "run-93" / "final.json").read_text())

    # Round 1 shipped (fewest blocking, and best-scoring), round 8 is where the loop
    # stopped.
    assert summary["chosen_round"] == 1
    assert summary["artifact_hash"] == report_mod.artifact_hash(R1_TEXT)

    defects = summary["outstanding_defects"]
    assert [d["instruction"] for d in defects] == ["Fix the round-one claim."]
    # Every persisted defect is keyed to the shipped artifact, so the export can filter.
    assert all(d["artifact_hash"] == summary["artifact_hash"] for d in defects)


def test_the_export_renders_the_shipped_drafts_defects_and_only_those(config, identities):
    """Closing the loop through the export: what finalize keyed, the export renders —
    and the terminal round's defect never appears against the shipped text."""
    graph._finalize(_terminal_state("run-93b"), _runtime(config, identities, "run-93b"))
    final = json.loads((config.runs_dir / "run-93b" / "final.json").read_text())

    document = export.export_markdown("Q?", R1_TEXT, final, "run-93b")

    assert "Fix the round-one claim." in document
    assert "Fix the round-eight claim." not in document


def _equal_blocking_state(run_id):
    """The board that separates the two selection rules: no blocking issue anywhere,
    and the later round carrying more majors (the `1 → 7` swing D-claim-scoped-patch
    recorded, which run-1dd853cbbfd0 shows is critic identity, not prose)."""
    state = _terminal_state(run_id)
    state["scoreboard"][1]["blocking"] = 0
    return state


@pytest.mark.parametrize(
    ("mode", "expected_round", "expected_text"),
    [("latest_unblocked", 8, R8_TEXT), ("fewest_defects", 1, R1_TEXT)],
)
def test_selection_mode_decides_which_round_ships(
    config, identities, mode, expected_round, expected_text
):
    """D-latest-unblocked-selection: with blocking tied, the latest round ships under
    the default rule, and the previous rule still ships the lowest weighted score."""
    config.review.selection = mode
    run_id = f"run-93-{mode}"
    graph._finalize(_equal_blocking_state(run_id), _runtime(config, identities, run_id))
    summary = json.loads((config.runs_dir / run_id / "final.json").read_text())

    assert summary["chosen_round"] == expected_round
    assert summary["artifact_hash"] == report_mod.artifact_hash(expected_text)
    # The shipped draft's own defects travel with it under either rule.
    assert [d["instruction"] for d in summary["outstanding_defects"]] == [
        f"Fix the round-{'eight' if expected_round == 8 else 'one'} claim."
    ]


def test_the_default_selection_is_latest_unblocked(config):
    assert config.review.selection == "latest_unblocked"
