"""A writer reads before it drafts (D-writer-must-read)."""

from __future__ import annotations

import json

import pytest
from test_read_before_citing import URL_A, _runtime

from reasonable_answer import graph as graph_mod
from reasonable_answer import prompts
from reasonable_answer.graph import _generate

READ = [("web_search", '{"query": "q"}'), ("read_source", f'{{"url": "{URL_A}"}}')]


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


def _script(rt, per_call):
    """`per_call[i]` is the tool script of the i-th writer call (the last one repeats)."""
    calls = {"n": 0}

    def script(alias):
        i = min(calls["n"], len(per_call) - 1)
        calls["n"] += 1
        return per_call[i]

    rt.client.tool_script = script


def _writer_calls(rt):
    return [c for c in rt.client.calls if c.schema is None]


def _run(tmp_path, identities, config, per_call, **kw):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    _script(rt, per_call)
    return rt, _generate({"question": "q?", "round": 0, **kw}, rt)


def test_an_unread_draft_is_retried_once_with_the_nudge_and_a_reading_retry_is_accepted(
    tmp_path, identities, config
):
    rt, out = _run(tmp_path, identities, config, [[], READ])
    calls = _writer_calls(rt)
    assert len(calls) == 2
    assert calls[0].alias == calls[1].alias
    assert prompts.WRITER_UNREAD_NUDGE not in calls[0].user
    assert calls[1].user.endswith(prompts.WRITER_UNREAD_NUDGE)
    assert out["read_log"] == {URL_A: "full_text"}
    assert [e["failure_class"] for e in _events(rt, "generate_failed")] == ["unread_draft"]
    gen = _events(rt, "generate")[-1]
    assert gen["unread_retry"] == 1 and gen["unread_accepted"] == 0 and gen["cited_unread"] == 0


def test_a_retry_that_still_does_not_read_rotates_to_the_next_writer(tmp_path, identities, config):
    rt, out = _run(tmp_path, identities, config, [[], [], READ])
    aliases = [c.alias for c in _writer_calls(rt)]
    assert aliases[0] == aliases[1] != aliases[2]
    assert out["author_alias"] == aliases[2]
    assert [e["failure_class"] for e in _events(rt, "generate_failed")] == ["unread_draft"] * 2
    assert _events(rt, "generate")[-1]["unread_accepted"] == 0


def test_no_writer_reading_ships_the_draft_with_a_warning_and_never_deadlocks(
    tmp_path, identities, config
):
    rt, out = _run(tmp_path, identities, config, [[]])
    assert out["report"] and not out.get("fatal")
    gen = _events(rt, "generate")[-1]
    assert gen["unread_accepted"] == 1 and gen["unread_retry"] >= 1
    assert gen["cited_unread"] == 1


def test_a_single_writer_pool_stops_after_its_one_nudged_retry(tmp_path, identities, config, monkeypatch):
    monkeypatch.setattr(graph_mod.roles, "writer_pool", lambda roster: ["writer-a"])
    rt, out = _run(tmp_path, identities, config, [[]])
    assert len(_writer_calls(rt)) == 2
    assert not out.get("fatal")
    assert _events(rt, "generate")[-1]["unread_accepted"] == 1


def test_a_budget_of_one_attempt_accepts_the_unread_draft(tmp_path, identities, config):
    config.budgets.writer_attempts = 1
    rt, out = _run(tmp_path, identities, config, [[]])
    assert len(_writer_calls(rt)) == 1
    assert _events(rt, "generate")[-1]["unread_accepted"] == 1 and out["report"]


def test_a_draft_citing_only_already_read_entries_needs_no_new_read(tmp_path, identities, config):
    rt, _ = _run(tmp_path, identities, config, [[]], round=1, report="x", read_log={URL_A: "full_text"})
    assert len(_writer_calls(rt)) == 1
    gen = _events(rt, "generate")[-1]
    assert gen["unread_retry"] == 0 and gen["unread_accepted"] == 0
    assert not _events(rt, "generate_failed")


def test_a_page_whose_read_already_failed_does_not_trigger_the_retry(tmp_path, identities, config):
    rt, _ = _run(tmp_path, identities, config, [[]], round=1, report="x", read_log={URL_A: "paywalled"})
    assert len(_writer_calls(rt)) == 1


def test_the_knob_off_is_byte_identical(tmp_path, identities, config):
    config.search.require_writer_reads = False
    rt, _ = _run(tmp_path, identities, config, [[]])
    assert len(_writer_calls(rt)) == 1
    gen = _events(rt, "generate")[-1]
    assert "unread_retry" not in gen and "unread_accepted" not in gen
    assert not _events(rt, "generate_failed")


@pytest.mark.parametrize("read,verify", [(True, False), (False, True), (False, False)])
def test_reading_or_verifying_off_is_byte_identical(tmp_path, identities, config, read, verify):
    rt, _ = _runtime(tmp_path, identities, config, read=read, verify=verify)
    _script(rt, [[]])
    _generate({"question": "q?", "round": 0}, rt)
    assert len(_writer_calls(rt)) == 1
    assert "unread_retry" not in _events(rt, "generate")[-1]
    assert not _events(rt, "generate_failed")
