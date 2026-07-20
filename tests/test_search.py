"""Web search: credential resolution, budget, result fencing, and fail-closed startup.

Everything here is offline. The one place a real HTTP call would happen is stubbed at
`urllib.request.urlopen`, so the suite keeps its "no network, no API keys" property.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from reasonable_answer import prompts, search
from reasonable_answer.config import Config, ProxyConfig, SearchConfig
from reasonable_answer.search import (
    BraveSearch,
    QueryBudget,
    SearchConfigError,
    SearchError,
    SearchResult,
)

# --------------------------------------------------------------------- credential


def test_env_var_wins_over_token_file(tmp_path, monkeypatch):
    token_file = tmp_path / "brave.token"
    token_file.write_text("from-file")
    monkeypatch.setenv("TEST_BRAVE_KEY", "from-env")

    # Deliberate: a prod image that happens to ship a stale token file must still use
    # the environment, or a rotated key silently fails to take effect.
    assert search.resolve_token("TEST_BRAVE_KEY", token_file) == "from-env"


def test_token_file_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_BRAVE_KEY", raising=False)
    token_file = tmp_path / "brave.token"
    token_file.write_text("  from-file\n")
    assert search.resolve_token("TEST_BRAVE_KEY", token_file) == "from-file"


def test_missing_credential_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_BRAVE_KEY", raising=False)
    with pytest.raises(SearchConfigError, match="no credential"):
        search.resolve_token("TEST_BRAVE_KEY", tmp_path / "absent.token")


def test_empty_token_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_BRAVE_KEY", raising=False)
    token_file = tmp_path / "brave.token"
    token_file.write_text("   \n")
    with pytest.raises(SearchConfigError, match="is empty"):
        search.resolve_token("TEST_BRAVE_KEY", token_file)


def test_blank_env_var_falls_through_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_BRAVE_KEY", "   ")
    token_file = tmp_path / "brave.token"
    token_file.write_text("from-file")
    assert search.resolve_token("TEST_BRAVE_KEY", token_file) == "from-file"


# ------------------------------------------------------------------------- budget


def test_budget_stops_at_the_limit():
    budget = QueryBudget(2)
    assert budget.take() and budget.take()
    assert not budget.take()
    assert budget.exhausted and budget.used == 2


def test_budget_is_thread_safe():
    import threading

    budget = QueryBudget(50)
    granted = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if budget.take():
                with lock:
                    granted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Never over-grants: the whole point is that concurrent writers cannot collectively
    # spend more than the run was budgeted.
    assert len(granted) == 50


def test_exhausted_budget_is_reported_not_silent():
    client = BraveSearch("tok", budget=QueryBudget(0), min_interval=0)
    with pytest.raises(SearchError, match="budget exhausted"):
        client.search("anything")


# ------------------------------------------------------------------------ parsing


def _stub_response(payload: dict):
    class _Resp(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode())


def test_search_parses_results_and_strips_markup(monkeypatch):
    payload = {
        "web": {
            "results": [
                {
                    "title": "The <strong>CAP</strong> theorem",
                    "url": "https://example.org/cap",
                    "description": "A  <strong>proof</strong>\nsketch.",
                    "age": "2019-03-01",
                },
                {"title": "no url", "description": "dropped"},
            ]
        }
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _stub_response(payload)
    )
    client = BraveSearch("tok", budget=QueryBudget(5), min_interval=0)
    results = client.search("cap theorem")

    assert len(results) == 1, "an entry with no URL is not citable, so it is dropped"
    assert results[0].title == "The CAP theorem"
    assert results[0].description == "A proof sketch."
    assert results[0].url == "https://example.org/cap"


def test_search_sends_the_token_as_a_header_not_a_query_param(monkeypatch):
    seen = {}

    def capture(req, *a, **k):
        seen["headers"] = dict(req.headers)
        seen["url"] = req.full_url
        return _stub_response({"web": {"results": []}})

    monkeypatch.setattr("urllib.request.urlopen", capture)
    BraveSearch("secret-key", budget=QueryBudget(5), min_interval=0).search("q")

    # urllib title-cases header names.
    assert seen["headers"]["X-subscription-token"] == "secret-key"
    assert "secret-key" not in seen["url"], "the key must never land in a URL or a log"


def test_http_error_becomes_search_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    client = BraveSearch("tok", budget=QueryBudget(5), min_interval=0)
    with pytest.raises(SearchError, match="429"):
        client.search("q")


def test_empty_query_costs_no_budget():
    client = BraveSearch("tok", budget=QueryBudget(5), min_interval=0)
    with pytest.raises(SearchError, match="empty query"):
        client.search("   ")
    assert client.budget.used == 0


# ------------------------------------------------------------------ tool handler


def _handler(results=None, error=None):
    class _Client:
        budget = QueryBudget(10)

        def search(self, query, count=None):
            if error:
                raise error
            return results or []

    return search.make_tool_handler(_Client())


def test_results_reach_the_model_fenced_as_untrusted_data():
    handler = _handler(
        [SearchResult(title="T", url="https://example.org/a", description="D")]
    )
    out = handler("web_search", json.dumps({"query": "x"}))

    # Search results are the most untrusted text in the system: arbitrary web pages
    # entering a writer's context. They get the same fence as every other input.
    assert prompts.UNTRUSTED_NOTE in out
    assert prompts.DATA_FENCE in out and prompts.DATA_END in out
    assert "https://example.org/a" in out


def test_a_failed_search_is_told_to_the_model_rather_than_raised():
    handler = _handler(error=SearchError("budget exhausted for this run"))
    out = handler("web_search", json.dumps({"query": "x"}))

    # The alternative — returning nothing — reads to the model as "no such source
    # exists", which is exactly the state that produces invented citations.
    assert "SEARCH FAILED" in out
    assert "budget exhausted" in out
    assert "Do not invent sources" in out


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("web_search", "not json at all"),
        ("web_search", json.dumps({"query": "  "})),
        ("some_other_tool", json.dumps({"query": "x"})),
    ],
)
def test_malformed_tool_calls_degrade_to_a_message(name, arguments):
    out = _handler([])("web_search" if name == "web_search" else name, arguments)
    assert "SEARCH FAILED" in out


def test_no_results_is_stated_explicitly():
    out = _handler([])("web_search", json.dumps({"query": "x"}))
    assert "(no results)" in out


# ----------------------------------------------------------------- prompt wiring


def test_writer_system_only_mentions_the_tool_when_it_has_one():
    assert "web_search" not in prompts.writer_system(False)
    assert "web_search" in prompts.writer_system(True)
    # The no-invented-sources standard survives in both modes.
    assert "never invent a source" in prompts.writer_system(False)
    assert "Do not reconstruct a URL from memory" in prompts.writer_system(True)


# --------------------------------------------------------------- fail-closed startup


def _config(tmp_path, **search_kwargs) -> Config:
    from reasonable_answer.config import Budgets, Roster

    return Config(
        proxy=ProxyConfig(),
        roster=Roster(
            writers=["writer-a", "writer-b"],
            critics={
                "logic": ["logic-spec", "writer-a"],
                "evidence": ["evidence-spec", "writer-a"],
                "completeness": ["completeness-spec", "writer-a"],
            },
        ),
        budgets=Budgets(min_ticks=2, hard_cap=5),
        runs_dir=tmp_path / "runs",
        search=SearchConfig(**search_kwargs),
    )


def test_search_is_off_by_default(tmp_path):
    assert _config(tmp_path).search.enabled is False


def test_enabled_search_without_a_credential_refuses_to_start(tmp_path, monkeypatch):
    from reasonable_answer.graph import _build_searcher

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    config = _config(
        tmp_path, enabled=True, token_file=str(tmp_path / "absent.token")
    )
    with pytest.raises(SearchConfigError):
        _build_searcher(config, client=object())


def test_a_writer_that_cannot_call_tools_refuses_to_start(tmp_path, monkeypatch):
    """The subtle half of failing closed.

    A model that accepts `tools` and never calls one still gets the '## Sources'
    instruction, so it returns a report whose citations came from memory but look
    identical to retrieved ones. Nothing downstream can distinguish them, so the run
    must not begin.
    """
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _build_searcher

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "tok")
    config = _config(tmp_path, enabled=True)

    class _Client:
        def probe_tool_calling(self, alias):
            return alias != "writer-b"

    with pytest.raises(ConfigError, match="writer-b"):
        _build_searcher(config, _Client())


def test_capable_writers_get_a_searcher(tmp_path, monkeypatch):
    from reasonable_answer.graph import _build_searcher

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "tok")
    config = _config(tmp_path, enabled=True, query_budget=7)

    class _Client:
        def probe_tool_calling(self, alias):
            return True

    searcher = _build_searcher(config, _Client())
    assert isinstance(searcher, BraveSearch)
    assert searcher.budget.limit == 7


def test_disabled_search_builds_no_searcher(tmp_path):
    from reasonable_answer.graph import _build_searcher

    assert _build_searcher(_config(tmp_path), client=object()) is None


# ------------------------------------------------------------------- generate node


def _runtime(tmp_path, identities, config, searcher=None):
    from fakes import FakeClient

    from reasonable_answer.graph import Runtime
    from reasonable_answer.schemas import CritiqueOutput
    from reasonable_answer.store import RunStore

    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: "# Report\n\nBody.\n",
    )
    return Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-search"),
        searcher=searcher,
    ), client


def test_generate_offers_no_tool_when_search_is_off(tmp_path, identities, config):
    from reasonable_answer.graph import _generate

    rt, client = _runtime(tmp_path, identities, config)
    _generate({"question": "q?", "round": 0}, rt)

    assert client.calls[-1].tools == [], "search off must be byte-identical to before"
    assert "web_search" not in client.calls[-1].system


def test_generate_hands_the_writer_the_search_tool(tmp_path, identities, config):
    from reasonable_answer.graph import _generate

    class _Searcher:
        budget = QueryBudget(10)

        def search(self, query, count=None):
            return [SearchResult(title="T", url="https://example.org/x", description="D")]

    rt, client = _runtime(tmp_path, identities, config, searcher=_Searcher())
    _generate({"question": "q?", "round": 0}, rt)

    call = client.calls[-1]
    assert call.tools == ["web_search"]
    assert "web_search" in call.system
    # And the result the writer would see is fenced, not raw.
    assert prompts.UNTRUSTED_NOTE in client.tool_results[0]
    assert "https://example.org/x" in client.tool_results[0]
