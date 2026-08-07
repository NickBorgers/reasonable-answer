"""The real `LLMClient` tool-calling internals (D-retrieval-opt-in / RA-019).

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

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.llm import (
    Completion,
    LLMClient,
    MalformedOutputError,
    ModelCallError,
    _diagnostics_suffix,
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
        # No retry backoff: these tests are about the tool loop and the fail-closed
        # guards, not about the wait between attempts (test_llm_retry.py owns that),
        # and the scripted failures are instant so there is nothing to wait out.
        budgets=Budgets(min_ticks=1, hard_cap=3, retry_backoff_seconds=0.0),
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


def test_a_tool_call_on_the_exhausted_round_is_asked_again_in_words(client):
    """D-provider-retry. Dropping `tools` is an instruction, not an enforcement — a model may answer
    the final round with another tool call anyway. `_create` passes that message (one
    carrying tool calls is not an empty completion), so the loop used to return
    `text=""` as a *success*, and two production runs aborted reading it as "the writer
    produced nothing"."""
    rounds: list[dict] = []
    _scripted(
        client,
        [_tool_message(), _tool_message(), _prose("LATE REPORT")],
        record=rounds,
    )

    result = client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=lambda n, a: "r",
        max_tool_rounds=1,
    )

    assert result.text == "LATE REPORT"
    # The nudge round carries no tools and ends in a user turn asking for the answer.
    assert "tools" not in rounds[-1]
    last = rounds[-1]["messages"][-1]
    assert last["role"] == "user"
    assert "final answer" in last["content"]
    # The unanswered assistant message must NOT be replayed: it holds tool calls with
    # no matching `role: tool` replies, which several providers reject outright.
    assert not any(m.get("tool_calls") for m in rounds[-1]["messages"][-2:])


def test_a_loop_that_never_produces_prose_raises_rather_than_returning_nothing(client):
    """The backstop. One nudge, then the failure enters the caller's retry budget as a
    `ModelCallError` instead of surfacing as a successful empty report (D-provider-retry)."""
    _scripted(client, [_tool_message(), _tool_message(), _tool_message()])

    with pytest.raises(ModelCallError, match="ended without an answer"):
        client.complete(
            "writer-a", system="s", user="u",
            tools=[{"type": "function", "function": {"name": "web_search"}}],
            tool_handler=lambda n, a: "r",
            max_tool_rounds=1,
        )


def test_a_tool_whose_budget_is_spent_is_withdrawn_mid_loop(client):
    """D-provider-retry. The search handler answers "budget exhausted" as *text* by design, so a
    writer told nothing would read silence as "nothing exists" — but leaving the tool on
    offer let a determined model spend every remaining round asking again and arrive at
    the end with a tool call instead of a report."""
    rounds: list[dict] = []
    _scripted(client, [_tool_message(), _prose("WROTE IT")], record=rounds)

    budget_left = [True]

    def handler(name, arguments):
        budget_left[0] = False  # this call drained it
        return "budget exhausted"

    result = client.complete(
        "writer-a", system="s", user="u",
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handler=handler,
        max_tool_rounds=6,
        should_offer_tools=lambda: budget_left[0],
    )

    assert result.text == "WROTE IT"
    assert len(rounds) == 2, "no rounds wasted re-offering a tool that cannot work"
    assert "tools" in rounds[0]
    assert "tools" not in rounds[1]


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


# --------------------------------------------------------- the empty-completion retry


def _sdk_response(content, alias="writer-a"):
    """The shape `_create` reads off the SDK: choices[0].message, usage, model."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message={"role": "assistant", "content": content})],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        model=alias,
    )


def _sdk_scripted(client: LLMClient, contents: list, record: list | None = None):
    seq = iter(contents)

    def create(**kwargs):
        if record is not None:
            record.append(kwargs)
        return _sdk_response(next(seq))

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_an_empty_completion_is_retried_rather_than_returned(client):
    """A 200 with no content is a failed call that forgot to say so.

    Returned as success it reaches the caller as a verdict about the *draft*, which is
    what aborted run-4d350e1d27a8: one empty body from a small model killed the run.
    """
    calls: list[dict] = []
    _sdk_scripted(client, ["", "   ", "REAL REPORT"], record=calls)

    result = client.complete("writer-a", system="s", user="u")

    assert result.text == "REAL REPORT"
    assert len(calls) == 3  # both empties were retried, not believed


def test_empty_completions_still_fail_closed_once_the_budget_is_gone(client):
    _sdk_scripted(client, ["", "", ""])  # call_retries=2 -> 3 attempts, all empty
    with pytest.raises(ModelCallError, match="empty completion"):
        client.complete("writer-a", system="s", user="u")


def test_a_tool_call_with_no_prose_is_not_treated_as_empty(client):
    """An assistant turn that only calls a tool legitimately carries no content."""
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=_tool_message())],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            model="writer-a",
        )

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    assert client.probe_tool_calling("writer-a") is True
    assert len(calls) == 1  # not retried as an empty answer


# ------------------------------------------------------------------ the timeout kwarg


class _Echo(BaseModel):
    value: str


def test_timeout_is_forwarded_to_the_real_sdk_call(client):
    """`complete(..., timeout=...)` is documented as passed straight through to the
    OpenAI SDK's per-request `timeout` kwarg (`web.refine.RefinementService`'s
    client-occupancy bound and orphan-linger depend on that being true). Every other
    test in this suite stubs `_create`/`structured` a layer above the SDK call, so
    this is the one place `_invoke_create`'s and `_create`'s `if timeout is not None`
    arms actually run against something that looks like the real `create(**kwargs)`."""
    calls: list[dict] = []
    _sdk_scripted(client, ["REAL REPORT"], record=calls)

    client.complete("writer-a", system="s", user="u", timeout=12.5)

    assert calls[0]["timeout"] == 12.5


def test_timeout_is_forwarded_through_structured_to_the_real_sdk_call(client):
    """`structured()` reaches the SDK via `complete()` -> `_invoke_create` ->
    `_create`; this pins the passthrough survives that whole chain, not just the
    `complete()` entry point."""
    calls: list[dict] = []
    _sdk_scripted(client, ['{"value": "ok"}'], record=calls)

    result = client.structured(
        "writer-a", system="s", user="u", schema=_Echo, timeout=3.0
    )

    assert result.value == "ok"
    assert calls[0]["timeout"] == 3.0


def test_timeout_is_absent_from_the_sdk_call_when_not_given(client):
    """`_invoke_create`'s docstring promises `None` and "omitted" mean the same thing
    to `_create` — verify the SDK kwargs never carry a `timeout` key at all when no
    caller-supplied deadline exists, rather than merely carrying `timeout=None`."""
    calls: list[dict] = []
    _sdk_scripted(client, ["REAL REPORT"], record=calls)

    client.complete("writer-a", system="s", user="u")

    assert "timeout" not in calls[0]


# ------------------------------------------------- unparsed tool-call markup as content


#: What DeepSeek emitted, verbatim in shape, when the proxy failed to parse its tool
#: call: fullwidth-bar markup arriving as `content` with `tool_calls` empty. It read as
#: a successful completion and became run-5d4b1d9cb08b's shipped answer.
_DSML_GARBAGE = (
    '<｜DSML｜tool_calls> <｜DSML｜invoke name="web_search"> '
    '<｜DSML｜parameter name="query" string="true">Ken Paxton securities fraud case '
    "dismissed June 2025</｜DSML｜parameter> </｜DSML｜invoke> </｜DSML｜tool_calls>"
)


def test_tool_call_markup_returned_as_prose_is_retried_not_believed(client):
    """A 200 whose "content" is an unparsed tool call is a failed call wearing prose's
    clothes: `tool_calls` is empty, so nothing downstream can tell it apart from an
    answer."""
    calls: list[dict] = []
    _sdk_scripted(client, [_DSML_GARBAGE, "REAL REPORT"], record=calls)

    result = client.complete("writer-a", system="s", user="u")

    assert result.text == "REAL REPORT"
    assert len(calls) == 2


def test_tool_call_markup_fails_closed_once_the_budget_is_gone(client):
    _sdk_scripted(client, [_DSML_GARBAGE] * 3)  # call_retries=2 -> 3 attempts
    with pytest.raises(ModelCallError, match="unparsed tool-call markup"):
        client.complete("writer-a", system="s", user="u")


def test_prose_that_merely_mentions_tool_call_syntax_is_left_alone(client):
    """A report *about* tool calling quotes the token once in thousands of characters.
    Failing that call would make the roster unable to answer a whole class of question."""
    prose = (
        "Models signal a tool invocation with a `<tool_call>` block. " + "Filler text. " * 60
    )
    _sdk_scripted(client, [prose])

    assert client.complete("writer-a", system="s", user="u").text == prose.strip()


# ----------------------------------------------------- the post-schema validate hook


def test_a_validator_rejection_is_repaired_with_its_hint(client):
    """`structured(validate=...)` repairs on the same loop as a schema violation, and
    carries the error's `repair_hint()` into the re-ask. This is the mechanism the
    critique path relies on; without the hint the second attempt is a blind re-roll."""

    class _Rejected(ValueError):
        def repair_hint(self) -> str:
            return "COPY THIS EXACTLY: the cited paragraph"

    seen: list[dict] = []
    _sdk_scripted(client, ['{"value": "bad"}', '{"value": "good"}'], record=seen)

    def validate(parsed: _Echo) -> None:
        if parsed.value != "good":
            raise _Rejected("value is not verbatim")

    result = client.structured(
        "writer-a", system="s", user="u", schema=_Echo, repair_retries=1, validate=validate
    )

    assert result.value == "good"
    repair_prompt = seen[1]["messages"][-1]["content"]
    assert "value is not verbatim" in repair_prompt
    assert "COPY THIS EXACTLY" in repair_prompt


def test_a_validators_diagnostics_reach_the_log_and_nothing_else_does():
    """Duck-typed like `repair_hint`, for the same reason: `triage` is LLM-free and must
    not import this module. A validator with nothing to say leaves the line unchanged."""

    class _Rejected(ValueError):
        def diagnostics(self) -> dict[str, str]:
            return {"code": "span_not_verbatim", "locus": "S5.P1", "span": "1a2b3c4d"}

    suffix = _diagnostics_suffix(_Rejected("rejected"))

    assert suffix == " [code=span_not_verbatim locus=S5.P1 span=1a2b3c4d]"
    # A plain pydantic failure offers none, and must not change the line at all.
    assert _diagnostics_suffix(ValueError("boom")) == ""


def test_a_validator_that_never_passes_fails_closed(client):
    _sdk_scripted(client, ['{"value": "bad"}'] * 3)

    def validate(_parsed: _Echo) -> None:
        raise ValueError("still wrong")

    with pytest.raises(MalformedOutputError, match="still wrong"):
        client.structured(
            "writer-a", system="s", user="u", schema=_Echo, repair_retries=2, validate=validate
        )
