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


# --------------------------------------------------------------- seed ingest at the CLI


def test_a_url_seed_is_not_mangled_into_a_path(doctor_config, monkeypatch):
    """`--seed` is a `str`, not a `Path`, because `Path('https://a/b')` normalises to
    'https:/a/b' — a URL seed would be silently corrupted before it was ever fetched."""
    seen: list[str] = []

    def capture(raw, *, config):
        seen.append(raw)
        raise cli.ingest.IngestError("stop here; the URL is what this test is about")

    monkeypatch.setattr(cli.ingest, "from_seed_arg", capture)
    runner.invoke(
        cli.app,
        ["run", "-q", "Does it hold?", "--seed", "https://example.org/a/b", "-c", str(doctor_config)],
    )
    assert seen == ["https://example.org/a/b"]


def test_an_unusable_seed_fails_closed_with_a_readable_message(doctor_config, tmp_path):
    result = runner.invoke(
        cli.app,
        ["run", "-q", "Does it hold?", "--seed", str(tmp_path / "nope.pdf"), "-c", str(doctor_config)],
    )
    assert result.exit_code == 2
    assert "seed file not found" in result.output
    assert "Traceback" not in result.output


def test_a_seed_without_headings_warns_before_the_run_starts(doctor_config, tmp_path, monkeypatch):
    """The warning is worth printing up front: it tells the user their critics will
    only be able to cite [S0.Pn] loci, while there is still time to fix the source."""
    seed = tmp_path / "draft.txt"
    seed.write_text("Just prose, no headings at all.\n")
    monkeypatch.setattr(cli, "run_graph", lambda *a, **k: {"terminal_status": "accepted", "round": 2})

    result = runner.invoke(
        cli.app, ["run", "-q", "Does it hold?", "--seed", str(seed), "-c", str(doctor_config)]
    )
    assert "no headings" in result.output


def test_the_graph_receives_converted_markdown_not_the_original(doctor_config, tmp_path, monkeypatch):
    seed = tmp_path / "draft.html"
    seed.write_text("<h1>Title</h1><p>Body text.</p>")
    captured: dict = {}

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"terminal_status": "accepted", "round": 2}

    monkeypatch.setattr(cli, "run_graph", fake_run)
    runner.invoke(
        cli.app, ["run", "-q", "Does it hold?", "--seed", str(seed), "-c", str(doctor_config)]
    )

    assert captured["seed"] == "# Title\n\nBody text."
    assert captured["seed_format"] == "html"
    assert captured["seed_source"] == "file:draft.html"
