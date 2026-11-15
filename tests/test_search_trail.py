"""The writer search trail (D-search-trail): every `web_search` call's query, outcome and
returned URLs are recorded per round, writer and attempt — failed attempts included — in
the purgeable `searches/` directory, and never in events.jsonl or a log line."""

from __future__ import annotations

import json
import logging

from fakes import FakeClient

from reasonable_answer import search
from reasonable_answer.graph import Runtime, _generate
from reasonable_answer.llm import ModelCallError
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.search import QueryBudget, SearchRecord, SearchResult
from reasonable_answer.store import RunStore, purge, read_searches

QUERY = "owner occupancy qualified census tracts"
URL = "https://example.org/census-working-paper"


class _Searcher:
    def __init__(self, limit=10):
        self.budget = QueryBudget(limit)

    def search(self, query, count=None):
        if not self.budget.take():
            raise search.SearchBudgetExhausted("search budget exhausted for this run")
        return [SearchResult(title="Owner occupancy in QCTs", url=URL, description="D")]


class _FailFirstClient(FakeClient):
    """The first generation searches, then fails the way a tool loop with no answer does."""

    def complete(self, alias, *, system, user, **kwargs):
        completion = super().complete(alias, system=system, user=user, **kwargs)
        if self.generations == 1:
            raise ModelCallError("no answer", failure_class="tool_loop_no_answer")
        return completion


def _runtime(tmp_path, identities, config, client_cls=FakeClient, limit=10):
    client = client_cls(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: "# Report\n\nBody.\n",
        tool_script=[("web_search", json.dumps({"query": QUERY}))],
    )
    rt = Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-trail"),
        searcher=_Searcher(limit),
    )
    return rt, client


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


# ------------------------------------------------------------------- the tool handler


def test_the_handler_reports_every_query_with_its_outcome():
    seen: list[SearchRecord] = []
    handler = search.make_tool_handler(_Searcher(limit=1), on_search=seen.append)

    handler("web_search", json.dumps({"query": QUERY}))
    handler("web_search", json.dumps({"query": "second"}))

    assert [r.outcome for r in seen] == ["ok", "budget_exhausted"]
    assert seen[0].query == QUERY and [r.url for r in seen[0].results] == [URL]
    assert seen[1].query == "second" and seen[1].results == ()


def test_a_backend_failure_is_recorded_as_a_closed_outcome():
    class _Broken:
        budget = QueryBudget(10)

        def search(self, query, count=None):
            raise search.SearchError("brave search HTTP 500: boom")

    seen: list[SearchRecord] = []
    search.make_tool_handler(_Broken(), on_search=seen.append)("web_search", '{"query": "x"}')
    assert [(r.query, r.outcome) for r in seen] == [("x", "error")]


def test_a_malformed_call_reaches_no_trail():
    seen: list[SearchRecord] = []
    handler = search.make_tool_handler(_Searcher(), on_search=seen.append)
    handler("web_search", "not json")
    handler("web_search", '{"query": "  "}')
    assert seen == []


# ------------------------------------------------------------------- the generate node


def test_generate_records_query_and_urls_by_round_writer_and_attempt(tmp_path, identities, config):
    rt, client = _runtime(tmp_path, identities, config)
    _generate({"question": "q?", "round": 2}, rt)

    [record] = read_searches(tmp_path, "run-trail")
    assert record["round"] == 3
    assert record["attempt"] == 1
    assert record["alias"] == client.calls[-1].alias
    assert record["writer"] == identities[record["alias"]]
    assert record["query"] == QUERY
    assert record["outcome"] == "ok"
    assert record["results"] == [{"url": URL, "title": "Owner occupancy in QCTs"}]
    assert (rt.store.dir / "searches" / "r03.jsonl").exists()
    assert _events(rt, "generate")[-1]["queries"] == 1


def test_a_failed_attempt_leaves_its_queries_on_the_record(tmp_path, identities, config):
    rt, client = _runtime(tmp_path, identities, config, client_cls=_FailFirstClient)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert out["report"]

    records = read_searches(tmp_path, "run-trail")
    assert [r["attempt"] for r in records] == [1, 2]
    assert records[0]["alias"] == client.calls[0].alias
    assert records[1]["alias"] == client.calls[1].alias
    [failed] = _events(rt, "generate_failed")
    assert failed["failure_class"] == "tool_loop_no_answer" and failed["queries"] == 1
    assert _events(rt, "generate")[-1]["queries"] == 1


def test_the_event_log_and_the_logs_carry_no_query_and_no_url(tmp_path, identities, config, caplog):
    caplog.set_level(logging.DEBUG)
    rt, _ = _runtime(tmp_path, identities, config, client_cls=_FailFirstClient)
    _generate({"question": "q?", "round": 0}, rt)

    events = (rt.store.dir / "events.jsonl").read_text()
    for needle in (QUERY, URL):
        assert needle not in events, "events.jsonl outlives a content purge (RA-016)"
        assert needle not in caplog.text, "no log line carries run content (RA-016)"
    signals = rt.store.dir / "signals"
    for path in signals.iterdir():
        assert QUERY not in path.read_text()


def test_search_off_writes_no_trail_and_counts_zero(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config)
    rt.searcher = None
    _generate({"question": "q?", "round": 0}, rt)
    assert read_searches(tmp_path, "run-trail") == []
    assert _events(rt, "generate")[-1]["queries"] == 0


# ------------------------------------------------------------------- retention


def test_a_content_purge_removes_the_trail_and_keeps_the_signal(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config)
    _generate({"question": "q?", "round": 0}, rt)
    assert read_searches(tmp_path, "run-trail")

    purge(tmp_path, "run-trail", content_only=True)

    assert read_searches(tmp_path, "run-trail") == []
    assert _events(rt, "generate")[-1]["queries"] == 1


def test_an_unreadable_line_is_skipped_not_raised(tmp_path):
    store = RunStore(tmp_path, "run-x")
    store.search(1, {"query": "a"})
    with (store.dir / "searches" / "r01.jsonl").open("a") as fh:
        fh.write('{"query": "partial')
    store.search(2, {"query": "b"})
    assert [r["query"] for r in read_searches(tmp_path, "run-x")] == ["a", "b"]


# ------------------------------------------------------------------- fail-open and held drafts


def test_a_trail_write_failure_never_fails_the_writer_call(
    tmp_path, identities, config, caplog, monkeypatch
):
    """D-search-trail residual (3): a full disk drops the trail line with a WARNING; the
    attempt completes, its `queries` count still includes the call, nothing aborts."""
    rt, _ = _runtime(tmp_path, identities, config)

    def full_disk(round_no, record):
        raise OSError("No space left on device")

    monkeypatch.setattr(rt.store, "search", full_disk)
    caplog.set_level(logging.WARNING)
    out = _generate({"question": "q?", "round": 0}, rt)

    assert out["report"] and not out.get("fatal")
    assert "search trail write failed: OSError" in caplog.text
    assert QUERY not in caplog.text
    assert _events(rt, "generate")[-1]["queries"] == 1
    assert _events(rt, "generate_failed") == []


def test_a_held_draft_that_ships_reports_its_own_query_count(tmp_path, identities, config):
    """The held-draft acceptance path (D-writer-must-read, D-writer-rotation-pool): attempt 1
    drafts without reading after two searches and is held; attempts 2 and 3 search three
    times and once and come back empty; the held draft ships. Its `generate` event must
    carry the held attempt's own count, not the last failed attempt's."""
    from test_read_before_citing import _runtime as _read_runtime

    rt, draft = _read_runtime(tmp_path, identities, config, read=True, verify=True)
    scripts = [
        [("web_search", '{"query": "a"}'), ("web_search", '{"query": "b"}')],
        [("web_search", '{"query": "c"}')] * 3,
        [("web_search", '{"query": "d"}')],
    ]
    calls = {"n": 0}

    def script(alias):
        i = min(calls["n"], len(scripts) - 1)
        calls["n"] += 1
        return scripts[i]

    rt.client.tool_script = script
    rt.client.report_fn = lambda n: draft if n == 1 else ""
    out = _generate({"question": "q?", "round": 0}, rt)

    assert out["report"] and not out.get("fatal")
    assert [e["queries"] for e in _events(rt, "generate_failed")] == [2, 3, 1]
    gen = _events(rt, "generate")[-1]
    assert gen["unread_accepted"] == 1
    assert gen["queries"] == 2
    assert [r["attempt"] for r in read_searches(tmp_path, "run-rbc")] == [1, 1, 2, 2, 2, 3]
