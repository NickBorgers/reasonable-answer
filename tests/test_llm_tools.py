"""The real `LLMClient` tool-calling internals (D17 / RA-019).

`FakeClient` stands in for the whole client elsewhere, which means the agentic loop in
`complete()` and the capability probe in `probe_tool_calling()` never execute in the
suite. Both carry load-bearing guarantees:

* the loop **terminates** — a model that calls tools forever is still forced to answer,
  because the final round drops `tools`;
* the probe **detects a model that accepts `tools` and never calls one**, which is the
  exact failure the feature exists to prevent.

These tests stub the client one layer down (`_create`, or the OpenAI SDK object) so the
real control flow runs. Offline throughout.
"""

from __future__ import annotations

import pytest

from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.llm import (
    Completion,
    LLMClient,
    ModelCallError,
    _message_dict,
    _Reply,
    _tool_calls,
)


@pytest.fixture
def client(tmp_path) -> LLMClient:
    config = Config(
        proxy=ProxyConfig(),
        roster=Roster(
            writers=["writer-a"],
            critics={
                "logic": ["logic-spec"],
                "evidence": ["evidence-spec"],
                "completeness": ["completeness-spec"],
            },
        ),
        budgets=Budgets(min_ticks=1, hard_cap=3),
        runs_dir=tmp_path / "runs",
    )
    return LLMClient(config)


def _tool_message(call_id: str = "c1", query: str = "q"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "web_search", "arguments": f'{{"query": "{query}"}}'},
            }
        ],
    }


def _prose(text: str = "FINAL REPORT"):
    return {"role": "assistant", "content": text}


def _scripted(client: LLMClient, messages: list[dict], *, record: list | None = None):
    """Drive `_create` from a script, capturing the kwargs each round was called with."""
    seq = iter(messages)

    def fake_create(alias, kwargs):
        if record is not None:
            record.append(kwargs)
        return _Reply(
            message=next(seq), reported=alias, prompt_tokens=10, completion_tokens=5
        )

    client._create = fake_create  # type: ignore[method-assign]


# ---------------------------------------------------------------------- the loop


def test_tool_results_are_threaded_back_and_the_loop_terminates(client):
    rounds: list[dict] = []
    _scripted(client, [_tool_message("c1"), _tool_message("c2"), _prose()], record=rounds)

    seen: list[tuple[str, str]] = []

    def handler(name, arguments):
        seen.append((name, arguments))
        return f"RESULT {len(seen)}"

    result = client.complete(
        "writer-a",
        system="s",
        user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=handler,
    )

    assert result.text == "FINAL REPORT"
    assert result.tool_calls == 2
    assert [n for n, _ in seen] == ["web_search", "web_search"]

    # Round 3 carries the whole conversation: two assistant tool-call messages, each
    # followed by its tool result keyed to the right tool_call_id.
    final_messages = rounds[-1]["messages"]
    tool_msgs = [m for m in final_messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert [m["content"] for m in tool_msgs] == ["RESULT 1", "RESULT 2"]


def test_tokens_accumulate_across_rounds(client):
    _scripted(client, [_tool_message(), _prose()])
    result = client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=lambda n, a: "r",
    )
    # Two round-trips at 10/5 each — not just the last one's usage.
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 10


def test_a_model_that_never_stops_calling_tools_is_still_forced_to_answer(client):
    """The termination guarantee.

    Removing `tools` on the final round is the only instruction every provider in the
    roster honours identically, so it is what the loop relies on.
    """
    rounds: list[dict] = []
    # More tool calls than rounds allowed, then prose only when tools are gone.
    _scripted(client, [_tool_message() for _ in range(3)] + [_prose("FORCED")], record=rounds)

    result = client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=lambda n, a: "r",
        max_tool_rounds=3,
    )

    assert result.text == "FORCED"
    assert len(rounds) == 4
    assert all("tools" in r for r in rounds[:3]), "tools offered while rounds remain"
    assert "tools" not in rounds[-1], "the exhausted round must drop tools"


def test_a_model_that_answers_immediately_makes_no_tool_calls(client):
    _scripted(client, [_prose()])
    result = client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=lambda n, a: "r",
    )
    assert result.tool_calls == 0
    assert result.text == "FINAL REPORT"


def test_without_tools_the_call_is_a_plain_single_round(client):
    rounds: list[dict] = []
    _scripted(client, [_prose()], record=rounds)
    result = client.complete("writer-a", system="s", user="u")

    # Search-off must be byte-identical to the pre-retrieval path.
    assert len(rounds) == 1
    assert "tools" not in rounds[0]
    assert result.tool_calls == 0


def test_a_handler_is_required_to_activate_the_loop(client):
    rounds: list[dict] = []
    _scripted(client, [_prose()], record=rounds)
    client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
    )
    # tools without a handler would offer a capability nothing can service.
    assert "tools" not in rounds[0]


# -------------------------------------------------------------------- the probe


def test_probe_detects_a_model_that_calls_tools(client):
    _scripted(client, [_tool_message()])
    assert client.probe_tool_calling("writer-a") is True
    assert client.tool_capable("writer-a") is True


def test_probe_rejects_a_model_that_accepts_tools_and_never_calls_one(client):
    """The subtle half of failing closed.

    Such a writer still receives the '## Sources' instruction and fills it from memory,
    producing citations indistinguishable from retrieved ones.
    """
    _scripted(client, [_prose("I would search, but here is my answer.")])
    assert client.probe_tool_calling("writer-a") is False


def test_probe_treats_an_error_as_incapable(client):
    def boom(alias, kwargs):
        raise ModelCallError("proxy exploded")

    client._create = boom  # type: ignore[method-assign]
    assert client.probe_tool_calling("writer-a") is False


def test_probe_result_is_cached(client):
    calls: list[dict] = []
    _scripted(client, [_tool_message()], record=calls)

    assert client.probe_tool_calling("writer-a") is True
    assert client.probe_tool_calling("writer-a") is True
    # A second round-trip would exhaust the one-message script and raise StopIteration.
    assert len(calls) == 1


# ------------------------------------------------------------------- normalizing


def test_message_dict_keeps_content_as_an_explicit_null_with_tool_calls():
    """Several providers reject an assistant message that omits `content` entirely, so
    the key must survive even when the value is None."""

    class _SDKMessage:
        def model_dump(self, exclude_none=False):
            # exclude_none=True is what the SDK is asked for, so `content: None` is
            # already gone by the time normalization sees it.
            return {"role": "assistant", "tool_calls": [{"id": "c1"}]}

    out = _message_dict(_SDKMessage())
    assert "content" in out and out["content"] is None
    assert out["tool_calls"] == [{"id": "c1"}]


def test_message_dict_passes_through_a_plain_dict():
    out = _message_dict({"role": "assistant", "content": "hi"})
    assert out == {"role": "assistant", "content": "hi"}


def test_tool_calls_ignores_non_dict_entries():
    assert _tool_calls({"tool_calls": [{"id": "a"}, "garbage", None]}) == [{"id": "a"}]
    assert _tool_calls({"content": "no calls"}) == []


def test_completion_defaults_to_zero_tool_calls():
    # Anything constructing a Completion without the field must read as "did not search".
    assert Completion(text="t", model_reported="m", prompt_tokens=0,
                      completion_tokens=0).tool_calls == 0
