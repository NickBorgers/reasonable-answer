"""Structural loci, the run store's privacy posture, and JSON extraction."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from reasonable_answer import report as report_mod
from reasonable_answer.config import Budgets, ConfigError
from reasonable_answer.llm import _extract_json, _strictify
from reasonable_answer.schemas import CritiqueOutput, StructuralRef
from reasonable_answer.store import RunStore, expired_runs, purge

REPORT = """Preamble before any heading.

# First

Para one.

Para two.

## Second

Only para.
"""


def test_loci_are_stable_and_addressable():
    structure = report_mod.parse(REPORT)
    assert structure.contains(StructuralRef(section=0, paragraph=1))  # preamble
    assert structure.contains(StructuralRef(section=1, paragraph=2))
    assert structure.contains(StructuralRef(section=2, paragraph=1))
    assert not structure.contains(StructuralRef(section=2, paragraph=2))


def test_rendered_report_shows_every_locus_a_critic_may_cite():
    rendered = report_mod.render_with_loci(REPORT)
    structure = report_mod.parse(REPORT)
    for para in structure.paragraphs:
        assert f"[S{para.section}.P{para.paragraph}]" in rendered


def test_heading_with_body_on_the_next_line_starts_at_paragraph_one():
    structure = report_mod.parse("# Title\n\nBody.\n")
    assert structure.contains(StructuralRef(section=1, paragraph=1))


def test_artifact_hash_is_byte_level():
    assert report_mod.artifact_hash("a") != report_mod.artifact_hash("a ")
    assert report_mod.artifact_hash(REPORT) == report_mod.artifact_hash(REPORT)


# ---------------------------------------------------------------------- store


def test_run_directory_and_files_are_private(tmp_path):
    store = RunStore(tmp_path, "run-x")
    store.event("startup")
    store.report(1, "h" * 64, "body", "vendor/model")
    assert store.dir.stat().st_mode & 0o777 == 0o700
    assert (store.dir / "events.jsonl").stat().st_mode & 0o777 == 0o600
    report_file = next((store.dir / "reports").iterdir())
    assert report_file.stat().st_mode & 0o777 == 0o600


def test_content_only_purge_keeps_the_decision_record(tmp_path):
    store = RunStore(tmp_path, "run-y")
    store.report(1, "h" * 64, "sensitive body", "vendor/model")
    store.critique("h" * 64, "logic", 1, CritiqueOutput(issues=[]))
    store.event("control", rule=7)
    store.final("final body", {"terminal_status": "accepted"})

    purge(tmp_path, "run-y", content_only=True)

    assert not list((store.dir / "reports").iterdir())
    assert not list((store.dir / "critiques").iterdir())
    assert not (store.dir / "final.md").exists()
    assert (store.dir / "events.jsonl").exists()
    assert json.loads((store.dir / "final.json").read_text())["terminal_status"] == "accepted"


def test_full_purge_removes_the_run(tmp_path):
    store = RunStore(tmp_path, "run-z")
    store.event("startup")
    purge(tmp_path, "run-z")
    assert not store.dir.exists()


def test_purge_of_an_unknown_run_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        purge(tmp_path, "nope")


def test_expired_runs_respects_the_retention_window(tmp_path):
    store = RunStore(tmp_path, "old-run")
    store.event("startup")
    os.utime(store.dir, (0, 0))
    assert expired_runs(tmp_path, retention_days=1) == ["old-run"]
    assert expired_runs(tmp_path, retention_days=100_000) == []


# ------------------------------------------------------------------ json path


@pytest.mark.parametrize(
    "raw",
    [
        '{"issues": []}',
        '```json\n{"issues": []}\n```',
        'Sure! Here you go:\n{"issues": []}\nHope that helps.',
        '```\n{"issues": []}\n```',
    ],
)
def test_json_extraction_survives_chatty_models(raw):
    assert _extract_json(raw) == {"issues": []}


def test_json_extraction_handles_braces_inside_strings():
    assert _extract_json('{"a": "not } the end", "b": 1}') == {"a": "not } the end", "b": 1}


@pytest.mark.parametrize("raw", ["", "no json here", "{unclosed"])
def test_json_extraction_fails_loudly(raw):
    with pytest.raises(ValueError):
        _extract_json(raw)


def test_strictify_closes_every_object():
    schema = _strictify(CritiqueOutput.model_json_schema())

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


# --------------------------------------------------------------------- config


def test_budgets_fail_closed_on_a_cap_that_cannot_be_hard():
    with pytest.raises(ConfigError, match="min_ticks < hard_cap"):
        Budgets(min_ticks=8, hard_cap=8)
    with pytest.raises(ConfigError, match="min_ticks < hard_cap"):
        Budgets(min_ticks=6, hard_cap=5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_ticks": 0},  # would make the floor meaningless
        {"max_concurrency": 0},  # crashes the executor at runtime
        {"stagnation_limit": -1},  # terminates on tick one
        {"critique_attempts": -1},  # silently reads as exhausted
        {"timeout_seconds": 0},
    ],
)
def test_out_of_range_budgets_are_rejected_at_load(kwargs):
    """Bad budgets must fail at startup, not surface as a crash or a silently
    different state machine mid-run."""
    from pydantic import ValidationError

    with pytest.raises((ValidationError, ConfigError)):
        Budgets(**kwargs)


def test_shipped_config_loads_and_is_healthy_in_shape():
    from reasonable_answer.config import Config

    cfg = Config.load(Path("config/roster.yaml"))
    assert cfg.roster.writers
    for lens_pool in cfg.roster.critics.values():
        assert len(lens_pool) >= 2
    # The shipped deployment opts into retrieval and source verification (the code
    # defaults stay False); pin it so the posture can't regress silently.
    assert cfg.search.enabled is True
    assert cfg.search.verify_sources is True
