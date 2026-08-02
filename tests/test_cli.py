"""`ra doctor` and the audition commands — the pre-flight a roster edit is checked
against, offline."""

from __future__ import annotations

import pytest
import yaml
from fakes import FakeClient
from typer.testing import CliRunner

from reasonable_answer import cli, refine_audition
from reasonable_answer.build import UNKNOWN, BuildIdentity
from reasonable_answer.schemas import CritiqueOutput

runner = CliRunner()

IDENTITIES = {
    "writer-a": "vendor-a/model-a",
    "writer-b": "vendor-b/model-b",
    "logic-spec": "vendor-c/logic",
    "evidence-spec": "vendor-d/evidence",
    "completeness-spec": "vendor-e/completeness",
    "referee": "vendor-f/referee",
}


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
    monkeypatch.setattr(
        cli,
        "LLMClient",
        lambda _config: FakeClient(
            identities=IDENTITIES,
            critique_fn=lambda *_: CritiqueOutput(issues=[]),
            report_fn=lambda _: "",
        ),
    )
    return path


@pytest.fixture
def audition_client(doctor_config, monkeypatch):
    """`doctor_config`'s roster, with the fake client handed back so a test can see what
    the command probed and which mode it pinned. Two aliases answer a mode other than
    the fake's default, so a test can tell a pinned mode from a coincidence."""
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=lambda *_: CritiqueOutput(issues=[]),
        report_fn=lambda _: "",
        modes={"logic-spec": "json_object", "referee": "json_object"},
    )
    monkeypatch.setattr(cli, "LLMClient", lambda _config: client)
    return client


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


def test_doctor_warns_when_the_build_is_unknown(doctor_config, monkeypatch):
    monkeypatch.setattr(cli, "build_identity", lambda: UNKNOWN)

    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])

    assert result.exit_code == 0
    assert "build unknown" in result.stdout


def test_doctor_reports_the_known_build_source(doctor_config, monkeypatch):
    monkeypatch.setattr(
        cli,
        "build_identity",
        lambda: BuildIdentity(commit="a" * 40, dirty=False, source="image"),
    )

    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])

    assert result.exit_code == 0
    assert "build: aaaaaaaaaaaa (from image)" in result.stdout


def test_doctor_says_so_when_enforcement_is_on_with_nothing_measured(doctor_config):
    """`enforce: true` on an empty cache blocks nothing. Reporting that roster as simply
    healthy is how a setting comes to read as a safety control while being inert — the
    reason `audition.enabled` was deleted rather than wired up (D-critic-audition)."""
    data = yaml.safe_load(doctor_config.read_text())
    data["audition"] = {"enforce": True}
    doctor_config.write_text(yaml.safe_dump(data))

    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert result.exit_code == 0
    assert "cannot block anything" in result.stdout.replace("\n", " ")


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


def test_doctor_shows_refine_verdict_line_when_refinement_is_enabled(doctor_config, tmp_path):
    """An enabled refine channel must appear in doctor's output, and an unmeasured
    one must read as unmeasured — never as a pass (D-refine-audition)."""
    data = yaml.safe_load(doctor_config.read_text())
    data["refine"] = {"enabled": True}
    data["audition"] = {"refine": {"cache_path": str(tmp_path / "refine-cache.json")}}
    doctor_config.write_text(yaml.safe_dump(data))

    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert result.exit_code == 0
    out = result.stdout.replace("\n", " ")
    # The default refine alias is the orchestrator's.
    assert "refine ('referee')" in out
    assert "not audited" in out


def test_doctor_says_nothing_about_refine_when_disabled(doctor_config):
    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])
    assert result.exit_code == 0
    assert "refine (" not in result.stdout


def test_audition_refine_rejects_unknown_transforms(doctor_config):
    result = runner.invoke(
        cli.app,
        ["audition-refine", "--config", str(doctor_config), "--transforms", "bogus_transform"],
    )
    assert result.exit_code == 2
    assert "unknown transforms" in result.stdout


# ------------------------------------------- the audition's structured-output regime


def _with_audition_cache(config_path, tmp_path, **extra):
    data = yaml.safe_load(config_path.read_text())
    data["audition"] = {"cache_path": str(tmp_path / "audition-cache.json"), **extra}
    config_path.write_text(yaml.safe_dump(data))
    return tmp_path / "audition-cache.json"


def _stub_run_audition(monkeypatch, seen):
    """Stand in for the measuring pass, recording the mode each slot was measured under.

    The point of the audition is what the calls look like, and `mode_for` is what
    decides that — so a stub that records it is measuring exactly the property
    D-audition-probe-parity is about, without spending a corpus.
    """

    def fake(client, roster, identities, fixtures, cfg, require_verbatim_spans=True, only=None):
        for slot in only:
            seen[slot.alias] = client.mode_for(slot.alias)
        return tuple(
            cli.audition_mod.Metrics(
                alias=s.alias, identity=s.identity, lens=s.lens, fixtures_owed=0
            )
            for s in only
        )

    monkeypatch.setattr(cli.audition_mod, "run_audition", fake)


def test_audition_pins_the_production_structured_output_mode_before_measuring(
    audition_client, doctor_config, tmp_path, monkeypatch
):
    """D-audition-probe-parity. Unprobed, every audition call resolves `mode_for` to the
    default prompt mode, so the harness certifies a critic in an extraction regime no run
    ever uses for it — a model reliable under its pinned mode is penalised for prompt-mode
    failures it will never have in production, and the reverse can hide a real one.
    """
    cache_path = _with_audition_cache(doctor_config, tmp_path)
    seen: dict[str, str] = {}
    _stub_run_audition(monkeypatch, seen)

    result = runner.invoke(cli.app, ["audition", "--config", str(doctor_config)])

    assert result.exit_code == 0, result.stdout
    # Every critic slot was probed, and measured under what the probe pinned.
    assert set(audition_client.probes) >= set(seen)
    assert seen["logic-spec"] == "json_object"
    assert seen["writer-a"] == "json_schema"
    assert "prompt" not in set(seen.values())

    # And the verdict records the regime it was taken in, rather than leaving a reader
    # to assume one.
    cache = cli.audition_mod.load_cache(cache_path)
    modes = {e.metrics.alias: e.structured_output_mode for e in cache.values()}
    assert modes["logic-spec"] == "json_object"
    assert modes["writer-a"] == "json_schema"


def test_audition_re_measures_a_verdict_taken_under_another_mode(
    audition_client, doctor_config, tmp_path, monkeypatch
):
    """The measuring path is the one place a mode mismatch can be answered by spending
    rather than by reporting, so it re-measures instead of reusing (D-audition-probe-parity).
    """
    cache_path = _with_audition_cache(doctor_config, tmp_path)
    seen: dict[str, str] = {}
    _stub_run_audition(monkeypatch, seen)
    runner.invoke(cli.app, ["audition", "--config", str(doctor_config)])
    assert seen  # measured once, cache now populated

    # A second run reuses everything...
    seen.clear()
    result = runner.invoke(cli.app, ["audition", "--config", str(doctor_config)])
    assert result.exit_code == 0, result.stdout
    assert seen == {}

    # ...until the alias probes somewhere else, which is a documented possibility for at
    # least one rostered model (`config/roster.yaml`'s minimax-m3 note).
    audition_client.modes["logic-spec"] = "json_schema"
    audition_client.probes.clear()
    result = runner.invoke(cli.app, ["audition", "--config", str(doctor_config)])

    assert result.exit_code == 0, result.stdout
    assert set(seen) == {"logic-spec"}
    assert seen["logic-spec"] == "json_schema"
    cache = cli.audition_mod.load_cache(cache_path)
    modes = {e.metrics.alias: e.structured_output_mode for e in cache.values()}
    assert modes["logic-spec"] == "json_schema"


def test_doctor_reports_a_verdict_measured_under_a_different_mode(
    audition_client, doctor_config, tmp_path, monkeypatch
):
    """The free read keeps the verdict (dropping it would disarm the `enforce` gate every
    time a non-deterministic prober landed elsewhere), so doctor has to say the two
    disagree or the divergence is invisible (D-audition-probe-parity)."""
    _with_audition_cache(doctor_config, tmp_path)
    _stub_run_audition(monkeypatch, {})
    runner.invoke(cli.app, ["audition", "--config", str(doctor_config)])

    audition_client.modes["logic-spec"] = "json_schema"
    audition_client.probes.clear()
    result = runner.invoke(cli.app, ["doctor", "--config", str(doctor_config)])

    assert result.exit_code == 0
    out = result.stdout.replace("\n", " ")
    assert "auditioned under structured-output mode" in out
    assert "json_object" in out and "json_schema" in out


def test_audition_refine_also_measures_under_the_pinned_mode(
    audition_client, doctor_config, tmp_path, monkeypatch
):
    """Same gap, same fix, on the refine corpus: `RefinementService.preflight` pins the
    strongest supported mode before serving, so the audition has to measure under it."""
    data = yaml.safe_load(doctor_config.read_text())
    data["refine"] = {"enabled": True}
    data["audition"] = {"refine": {"cache_path": str(tmp_path / "refine-cache.json")}}
    doctor_config.write_text(yaml.safe_dump(data))

    seen: dict[str, str] = {}

    def fake_run(client, alias, identity, enabled, fixtures, cfg):
        seen[alias] = client.mode_for(alias)
        return refine_audition.RefineMetrics(
            alias=alias, identity=identity, transforms=tuple(sorted(enabled))
        )

    monkeypatch.setattr(refine_audition, "run_refine_audition", fake_run)

    result = runner.invoke(cli.app, ["audition-refine", "--config", str(doctor_config)])

    assert result.exit_code == 0, result.stdout
    assert seen == {"referee": "json_object"}
    cache = refine_audition.load_refine_cache(tmp_path / "refine-cache.json")
    assert {e.structured_output_mode for e in cache.values()} == {"json_object"}


# ------------------------------------------------------------------- logging


def test_the_log_level_can_be_named_by_the_environment(monkeypatch):
    """D-provider-retry. The container's CMD is fixed, so `--verbose` is unreachable in production —
    and a night of aborted runs left only WARNING lines to diagnose them from."""
    monkeypatch.setenv(cli.LOG_LEVEL_ENV, "info")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: captured.update(kw))

    cli._setup_logging(verbose=False)

    assert captured["level"] == cli.logging.INFO


def test_the_environment_can_also_ask_for_less_than_verbose(monkeypatch):
    """`--verbose` can only ever raise verbosity, so honouring the variable is the only
    way a deployment that passes the flag can still ask for less."""
    monkeypatch.setenv(cli.LOG_LEVEL_ENV, "ERROR")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: captured.update(kw))

    cli._setup_logging(verbose=True)

    assert captured["level"] == cli.logging.ERROR


def test_an_unknown_log_level_warns_and_keeps_the_flag_default(monkeypatch, capsys):
    """A logging preference must not be able to fail a run."""
    monkeypatch.setenv(cli.LOG_LEVEL_ENV, "CHATTY")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: captured.update(kw))

    cli._setup_logging(verbose=False)

    assert captured["level"] == cli.logging.WARNING
    assert "CHATTY" in capsys.readouterr().err


def test_no_variable_leaves_the_historical_behaviour(monkeypatch):
    monkeypatch.delenv(cli.LOG_LEVEL_ENV, raising=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: captured.update(kw))

    cli._setup_logging(verbose=False)
    assert captured["level"] == cli.logging.WARNING
    cli._setup_logging(verbose=True)
    assert captured["level"] == cli.logging.INFO
