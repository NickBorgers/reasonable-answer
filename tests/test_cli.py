"""`ra doctor` — the pre-flight a roster edit is checked against, offline."""

from __future__ import annotations

import pytest
import yaml
from fakes import FakeClient
from typer.testing import CliRunner

from reasonable_answer import cli
from reasonable_answer.schemas import CritiqueOutput

runner = CliRunner()


@pytest.fixture
def doctor_config(tmp_path, monkeypatch):
    """A roster whose orchestrator is nobody's writer and nobody's critic — the case
    where the roles column would render empty if doctor did not know about it."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "roster": {
                    "writers": ["writer-a", "writer-b"],
                    "orchestrator": "referee",
                    "critics": {
                        "logic": ["logic-spec", "writer-a", "writer-b"],
                        "evidence": ["evidence-spec", "writer-a", "writer-b"],
                        "completeness": ["completeness-spec", "writer-a", "writer-b"],
                    },
                },
                "runs_dir": str(tmp_path / "runs"),
            }
        )
    )
    identities = {
        "writer-a": "vendor-a/model-a",
        "writer-b": "vendor-b/model-b",
        "logic-spec": "vendor-c/logic",
        "evidence-spec": "vendor-d/evidence",
        "completeness-spec": "vendor-e/completeness",
        "referee": "vendor-f/referee",
    }
    monkeypatch.setattr(
        cli,
        "LLMClient",
        lambda _config: FakeClient(
            identities=identities,
            critique_fn=lambda *_: CritiqueOutput(issues=[]),
            report_fn=lambda _: "",
        ),
    )
    return path


def test_doctor_labels_the_orchestrator_role(doctor_config):
    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert result.exit_code == 0
    # rich wraps the table, so assert on the cell text rather than a whole row.
    assert "orchestrator" in result.stdout


def test_doctor_reports_every_alias_including_the_orchestrator(doctor_config):
    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert result.exit_code == 0
    for alias in ("writer-a", "writer-b", "logic-spec", "referee"):
        assert alias in result.stdout


def test_doctor_reports_a_healthy_roster(doctor_config):
    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert "roster healthy" in result.stdout


def test_doctor_warns_rather_than_claiming_health_when_a_lens_is_thin(tmp_path, monkeypatch):
    """One eligible non-author critic is legal but degrades acceptance, so doctor has
    to say so — a silent pass here would misrepresent what `accepted` will mean."""
    path = tmp_path / "roster.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "roster": {
                    "writers": ["writer-a"],
                    "critics": {
                        "logic": ["writer-a", "logic-spec"],
                        "evidence": ["writer-a", "logic-spec"],
                        "completeness": ["writer-a", "logic-spec"],
                    },
                },
                "runs_dir": str(tmp_path / "runs"),
            }
        )
    )
    monkeypatch.setattr(
        cli,
        "LLMClient",
        lambda _config: FakeClient(
            identities={"writer-a": "vendor-a/model-a", "logic-spec": "vendor-c/logic"},
            critique_fn=lambda *_: CritiqueOutput(issues=[]),
            report_fn=lambda _: "",
        ),
    )
    result = runner.invoke(cli.app, ["doctor", "--config", str(path)])
    assert result.exit_code == 0
    assert "roster_limited" in result.stdout
    assert "roster healthy" not in result.stdout
