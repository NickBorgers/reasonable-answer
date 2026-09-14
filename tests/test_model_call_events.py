"""D-model-call-timing: every HTTP attempt against the proxy is a `model_call` event.

The 2026-09-14 run-time investigation had to infer per-call latency from the gaps between
audit events and from a LiteLLM log that Loki keeps for two days. These tests pin what
replaced that: one record per attempt carrying purpose, outcome, duration, token counts
and serving provider — and nothing that is content.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
from fakes import FakeClient
from openai import APITimeoutError

from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.graph import run
from reasonable_answer.llm import CallRecord, LLMClient, call_purpose, current_call_purpose
from reasonable_answer.schemas import CritiqueOutput

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""


def _client(tmp_path, clock_values: list[float]) -> tuple[LLMClient, list[CallRecord]]:
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
        budgets=Budgets(min_ticks=1, hard_cap=3, call_retries=2, retry_backoff_seconds=2.0),
        runs_dir=tmp_path / "runs",
    )
    ticks = iter(clock_values)
    client = LLMClient(config, sleep=lambda _: None, jitter=lambda: 1.0, clock=lambda: next(ticks))
    records: list[CallRecord] = []
    client.set_call_sink(records.append)
    return client, records


def _reply(content: str = "OK", *, prompt: int = 11, completion: int = 7, **extra):
    return SimpleNamespace(
        model="writer-a",
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        choices=[SimpleNamespace(message={"role": "assistant", "content": content})],
        **extra,
    )


def _install(client: LLMClient, *outcomes) -> None:
    """Each call to `create` takes the next outcome: an exception is raised, anything else
    is returned."""
    queue = list(outcomes)

    def create(**_kwargs):
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _timeout() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "http://proxy/v1/chat/completions"))


# ------------------------------------------------------------------ the record


def test_a_successful_call_is_one_record_with_its_provider_and_duration(tmp_path):
    client, records = _client(tmp_path, [100.0, 104.25])
    _install(client, _reply(provider="DeepInfra"))

    with call_purpose("writer"):
        client.complete("writer-a", system="s", user="u")

    assert records == [
        CallRecord(
            purpose="writer",
            alias="writer-a",
            attempt=1,
            outcome="ok",
            seconds=4.25,
            prompt_tokens=11,
            completion_tokens=7,
            provider="DeepInfra",
        )
    ]


def test_a_timeout_then_an_answer_is_two_records(tmp_path):
    """The shape the investigation could not see: the call "succeeded", and 300 seconds of
    it were a timeout that only a per-attempt record shows."""
    client, records = _client(tmp_path, [0.0, 300.0, 302.0, 305.0])
    _install(client, _timeout(), _reply())

    client.complete("writer-a", system="s", user="u")

    assert [(r.attempt, r.outcome, r.seconds) for r in records] == [
        (1, "timeout", 300.0),
        (2, "ok", 3.0),
    ]
    assert records[0].completion_tokens == 0 and records[0].provider is None


def test_an_empty_completion_keeps_the_tokens_it_spent(tmp_path):
    """A runaway generation that ends empty still cost its tokens; the record says how many."""
    client, records = _client(tmp_path, [0.0, 600.0, 602.0, 604.0])
    _install(client, _reply("", completion=16384, provider="DeepInfra"), _reply())

    client.complete("writer-a", system="s", user="u")

    assert records[0].outcome == "empty_completion"
    assert records[0].completion_tokens == 16384
    assert records[0].provider == "DeepInfra"
    assert records[1].outcome == "ok"


def test_every_record_is_metadata_only(tmp_path):
    client, records = _client(tmp_path, [0.0, 1.0])
    _install(client, _reply("a report body that must never reach the audit trail"))

    client.complete("writer-a", system="secret system", user="secret user")

    event = json.dumps(records[0].as_event())
    assert "report body" not in event and "secret" not in event
    assert set(records[0].as_event()) == {
        "purpose", "alias", "attempt", "outcome", "seconds",
        "prompt_tokens", "completion_tokens", "provider",
    }


def test_a_provider_value_that_is_not_a_name_is_dropped(tmp_path):
    for value in ["x" * 81, "Deep\nInfra", "<b>host</b>", 42, None]:
        client, records = _client(tmp_path, [0.0, 1.0])
        _install(client, _reply(provider=value))
        client.complete("writer-a", system="s", user="u")
        assert records[0].provider is None, value


def test_a_response_without_a_provider_field_records_none(tmp_path):
    client, records = _client(tmp_path, [0.0, 1.0])
    _install(client, _reply())

    client.complete("writer-a", system="s", user="u")

    assert records[0].provider is None


def test_a_failing_sink_never_fails_the_call(tmp_path, caplog):
    client, _ = _client(tmp_path, [0.0, 1.0])
    _install(client, _reply())

    def broken(_record):
        raise RuntimeError("disk full")

    client.set_call_sink(broken)
    with caplog.at_level(logging.WARNING, logger="reasonable_answer.llm"):
        result = client.complete("writer-a", system="s", user="u")

    assert result.text == "OK"
    assert "call sink failed" in caplog.text


# ------------------------------------------------------------------ the label


def test_labels_nest_and_unwind():
    assert current_call_purpose() is None
    with call_purpose("critic:evidence"):
        with call_purpose("claim_check"):
            assert current_call_purpose() == "claim_check"
        assert current_call_purpose() == "critic:evidence"
    assert current_call_purpose() is None


def test_a_label_set_around_a_pool_does_not_reach_its_threads():
    """Why `_critique` sets the label inside the submitted function: set around
    `pool.map`, every critic call would be recorded with no purpose."""
    with call_purpose("critic:logic"), ThreadPoolExecutor(max_workers=1) as pool:
        seen = list(pool.map(lambda _: current_call_purpose(), [0]))
    assert seen == [None]


# ------------------------------------------------------------------ the graph


def _events(config: Config, final: dict) -> list[dict]:
    path = Path(config.runs_dir) / final["run_id"] / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _lens_of(user: str) -> str:
    return next(lens for lens in ("logic", "evidence", "completeness") if f"YOUR DIMENSION: {lens}" in user)


def test_the_graph_points_the_sink_at_the_run_log(identities, config):
    client = FakeClient(
        identities=identities,
        critique_fn=lambda *_: CritiqueOutput(issues=[]),
        report_fn=lambda _: REPORT,
    )
    final = run(config, question="Is it so?", seed=REPORT, client=client)

    assert client.call_sink is not None
    record = CallRecord("critic:logic", "logic-spec", 2, "timeout", 300.0, 0, 0, None)
    client.call_sink(record)

    calls = [e for e in _events(config, final) if e["kind"] == "model_call"]
    assert len(calls) == 1
    assert {k: v for k, v in calls[0].items() if k not in ("ts", "kind")} == record.as_event()


def test_every_graph_call_site_carries_its_purpose(identities, config):
    client = FakeClient(
        identities=identities,
        critique_fn=lambda *_: CritiqueOutput(issues=[]),
        report_fn=lambda _: REPORT,
    )
    run(config, question="Is it so?", client=client)

    by_schema = {
        None: "writer",
        "OrchestratorRecommendation": "orchestrator",
        "WriterDisputes": "dispute",
        "ArbiterVerdict": "arbiter",
        "SupportManifest": "support_manifest",
        "ClaimVerdict": "claim_check",
    }
    purposes = set()
    for call in client.calls:
        purposes.add(call.purpose)
        if call.schema == "CritiqueOutput":
            assert call.purpose == f"critic:{_lens_of(call.user)}"
        elif call.schema == "IssueRepairs":
            assert call.purpose is not None and call.purpose.startswith("critic:")
        else:
            assert call.purpose == by_schema[call.schema], call.schema
    assert {"writer", "critic:logic", "critic:evidence", "critic:completeness"} <= purposes
