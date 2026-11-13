"""A failed writer attempt hands its search results to the next attempt of the same
generation (D-failed-attempt-search-carry): result records only, fenced, capped, readable."""

from __future__ import annotations

import json

from fakes import FakeClient

from reasonable_answer import prompts, reading
from reasonable_answer.fetch import FetchedSource
from reasonable_answer.graph import Runtime, _carry, _generate
from reasonable_answer.llm import ModelCallError
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.search import QueryBudget, SearchResult
from reasonable_answer.store import RunStore

QUERY = "owner occupancy qualified census tracts"
URL = "https://example.org/census-working-paper"
SNIPPET = "Owner-occupancy rates in Qualified Census Tracts"


class _Searcher:
    def __init__(self):
        self.budget = QueryBudget(10)

    def search(self, query, count=None):
        self.budget.take()
        return [SearchResult(title="QCT paper", url=URL, description=SNIPPET)]


class _Fetcher:
    def __init__(self):
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        return FetchedSource(url=url, text="page body", status=200)

    def fetch_all(self, urls):
        return []


class _FailFirstClient(FakeClient):
    """The first generation searches, then fails the way a tool loop with no answer does."""

    def complete(self, alias, *, system, user, **kwargs):
        completion = super().complete(alias, system=system, user=user, **kwargs)
        if self.generations == 1:
            raise ModelCallError("no answer", failure_class="tool_loop_no_answer")
        return completion


def _runtime(tmp_path, identities, config, scripts, *, read=False):
    calls = {"n": 0}

    def script(alias):
        i = min(calls["n"], len(scripts) - 1)
        calls["n"] += 1
        return scripts[i]

    client = _FailFirstClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: "# Report\n\nBody.\n",
        tool_script=script,
    )
    fetcher = _Fetcher()
    reader = (
        reading.SourceReader(
            fetcher, budget=reading.ReadBudget(max_calls=5, max_chars=10_000), max_chars=6_000
        )
        if read
        else None
    )
    rt = Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-carry"),
        searcher=_Searcher(),
        reader=reader,
    )
    return rt, client, fetcher


def _writer_calls(client):
    return [c for c in client.calls if c.schema is None]


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


SEARCH = [("web_search", json.dumps({"query": QUERY}))]


def test_the_next_attempt_is_handed_the_failed_attempts_results_fenced(tmp_path, identities, config):
    rt, client, _ = _runtime(tmp_path, identities, config, [SEARCH, []])
    _generate({"question": "q?", "round": 0}, rt)

    first, second = _writer_calls(client)
    assert URL not in first.user
    assert URL in second.user and SNIPPET in second.user
    assert "SEARCH RESULTS ALREADY RETRIEVED" in second.user
    # Fenced exactly as a live result is, and the earlier model's query is not shown.
    assert prompts.UNTRUSTED_NOTE in second.user and prompts.DATA_FENCE in second.user
    assert QUERY not in second.user
    assert _events(rt, "generate")[-1]["carried_results"] == 1


def test_a_carried_url_is_readable_by_the_next_attempt(tmp_path, identities, config):
    read = [("read_source", json.dumps({"url": URL}))]
    rt, client, fetcher = _runtime(tmp_path, identities, config, [SEARCH, read], read=True)
    _generate({"question": "q?", "round": 0}, rt)

    assert fetcher.fetched == [URL], "the carried URL reached the reader, not a refusal"
    assert "not returned by a search" not in client.tool_results[-1]


def test_without_carrying_the_same_read_is_refused(tmp_path, identities, config):
    config.search.carry_max_results = 0
    read = [("read_source", json.dumps({"url": URL}))]
    rt, client, fetcher = _runtime(tmp_path, identities, config, [SEARCH, read], read=True)
    _generate({"question": "q?", "round": 0}, rt)

    assert fetcher.fetched == []
    assert "not returned by a search" in client.tool_results[-1]
    first, second = _writer_calls(client)
    assert URL not in second.user and second.user == first.user
    assert _events(rt, "generate")[-1]["carried_results"] == 0


def test_a_successful_first_attempt_carries_nothing(tmp_path, identities, config):
    rt, client, _ = _runtime(tmp_path, identities, config, [SEARCH])
    client.__class__ = FakeClient
    _generate({"question": "q?", "round": 0}, rt)
    assert len(_writer_calls(client)) == 1
    assert _events(rt, "generate")[-1]["carried_results"] == 0


def test_nothing_carries_into_the_next_round(tmp_path, identities, config):
    rt, client, _ = _runtime(tmp_path, identities, config, [SEARCH, [], []])
    _generate({"question": "q?", "round": 0}, rt)
    _generate({"question": "q?", "round": 1, "report": "# Report\n\nBody.\n"}, rt)
    assert URL not in _writer_calls(client)[-1].user


def test_the_carry_is_deduplicated_ordered_and_capped():
    def result(n):
        return SearchResult(title=f"T{n}", url=f"https://example.org/{n}", description="D")

    first = reading.ReadSession()
    first.record_results([result(1), result(2), result(1)])
    second = reading.ReadSession()
    second.record_results([result(2), result(3), result(4)])

    carried = _carry([], first, 3)
    assert [r.url for r in carried] == ["https://example.org/1", "https://example.org/2"]
    carried = _carry(carried, second, 3)
    assert [r.url for r in carried] == [f"https://example.org/{n}" for n in (1, 2, 3)]
    assert _carry(carried, second, 0) == []


def test_a_carried_session_keeps_offered_count_as_its_own_searches():
    carried = [SearchResult(title="T", url=URL, description="D")]
    session = reading.ReadSession(carried=carried)
    assert session.readable(URL) and not session.offered(URL)
    assert session.offered_count == 0 and session.carried_count == 1
