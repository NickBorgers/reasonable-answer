"""Finalize ships the *chosen* draft's defects, not the terminal round's (issue #93).

On any non-accepted terminal, `graph._finalize` ships the best-scoring draft from any
round rather than the last one written. The "Outstanding defects in this report" list an
export renders must then describe *that* draft — the shipped artifact — not the artifact
the loop happened to stop on. Otherwise the report is charged with defects raised against
text it never contained, the mirror image of the clean-record hazard `_reviewers` guards.

The fixture is the shape of production run `run-d3bb2e4d2d94`: chosen round 1, terminal
round 8, `exhausted_unresolved`.
"""

from __future__ import annotations

import json

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
    """A run that stopped on round 8 but whose best-scoring draft is round 1."""
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
                "blocking": 0,
                "major": 5,
                "minor": 0,
                "report": R8_TEXT,
                "defects": [_defect("Fix the round-eight claim.")],
            },
        ],
    }


def test_finalize_persists_the_shipped_drafts_own_defects(config, identities):
    graph._finalize(_terminal_state("run-93"), _runtime(config, identities, "run-93"))
    summary = json.loads((config.runs_dir / "run-93" / "final.json").read_text())

    # Round 1 shipped (best-scoring), round 8 is where the loop stopped.
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
