"""How `LLMClient` retries a failing provider (D-provider-retry).

Three production runs aborted on 2026-07-29 because retrying cost nothing and therefore
bought nothing: `run-6f54b5a33e26` spent its entire call budget on three empty
completions between 06:01:28 and 06:01:33, which is three samples of one bad moment
rather than three chances. These tests pin the posture that replaced it — wait between
attempts, believe a provider that names its own delay, and do not spend the budget at
all on a failure the provider has already called final.

The wait is asserted, never served: `sleep` and `jitter` are injected, exactly as
`BraveSearch` injects its clock, so the suite stays instant and offline.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel

from reasonable_answer.config import Budgets, Config, ConfigError, ProxyConfig, Roster
from reasonable_answer.llm import (
    LLMClient,
    MalformedOutputError,
    ModelCallError,
    PermanentCallError,
)
from reasonable_answer.triage import LensValidationError, ViolationCode


def make_client(tmp_path, **budget_overrides) -> tuple[LLMClient, list[float]]:
    """A real client whose sleeps are recorded instead of taken.

    Jitter is pinned to 1.0 so the delays are exact; the randomness itself is asserted
    separately by `test_jitter_scales_the_delay`.
    """
    budgets = {
        "min_ticks": 1,
        "hard_cap": 3,
        "call_retries": 2,
        "retry_backoff_seconds": 2.0,
        "retry_backoff_max_seconds": 30.0,
    }
    budgets.update(budget_overrides)
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
        budgets=Budgets(**budgets),
        runs_dir=tmp_path / "runs",
    )
    slept: list[float] = []
    client = LLMClient(config, sleep=slept.append, jitter=lambda: 1.0)
    return client, slept


class _Boom(Exception):
    """A transport failure carrying no status — the shape of a timeout."""


def _raising(exc: Exception, *, succeed_on: int | None = None):
    """A `chat.completions.create` stand-in that raises until `succeed_on`."""
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if succeed_on is not None and calls["n"] >= succeed_on:
            return SimpleNamespace(
                model=kwargs["model"],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                choices=[SimpleNamespace(message={"role": "assistant", "content": "OK"})],
            )
        raise exc

    return create, calls


def _install(client: LLMClient, create) -> None:
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# ------------------------------------------------------------------ the wait


def test_retries_wait_and_the_wait_grows(tmp_path):
    client, slept = make_client(tmp_path)
    create, calls = _raising(_Boom("timed out"))
    _install(client, create)

    with pytest.raises(ModelCallError, match="exhausted call retries"):
        client.complete("writer-a", system="s", user="u")

    # Three attempts (call_retries=2), so two waits — never before the first.
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]


def test_the_wait_is_capped(tmp_path):
    """Doubling without a ceiling reaches minutes by the fifth attempt, which is longer
    than the caller's own rotation to a different model is worth waiting for."""
    client, slept = make_client(tmp_path, call_retries=5, retry_backoff_max_seconds=8.0)
    create, _ = _raising(_Boom("timed out"))
    _install(client, create)

    with pytest.raises(ModelCallError):
        client.complete("writer-a", system="s", user="u")

    assert slept == [2.0, 4.0, 8.0, 8.0, 8.0]


def test_a_successful_retry_stops_the_waiting(tmp_path):
    client, slept = make_client(tmp_path)
    create, calls = _raising(_Boom("blip"), succeed_on=2)
    _install(client, create)

    result = client.complete("writer-a", system="s", user="u")

    assert result.text == "OK"
    assert calls["n"] == 2
    assert slept == [2.0], "one wait, for the one retry actually taken"


def test_a_zero_base_disables_the_wait(tmp_path):
    """What the offline suite runs on: the failures it scripts are instant, so there is
    nothing to wait out."""
    client, slept = make_client(tmp_path, retry_backoff_seconds=0.0)
    create, _ = _raising(_Boom("timed out"))
    _install(client, create)

    with pytest.raises(ModelCallError):
        client.complete("writer-a", system="s", user="u")

    assert slept == []


def test_jitter_scales_the_delay(tmp_path):
    """Without it, every concurrent lens that fails together retries together — the
    thundering herd that put three requests into one bad five-second window."""
    client, slept = make_client(tmp_path)
    client._jitter = lambda: 0.5
    create, _ = _raising(_Boom("timed out"))
    _install(client, create)

    with pytest.raises(ModelCallError):
        client.complete("writer-a", system="s", user="u")

    assert slept == [1.0, 2.0]


def test_an_empty_completion_is_retried_on_the_same_budget_and_waits(tmp_path):
    """The exact failure of run-6f54b5a33e26: a 200 carrying neither prose nor a tool
    call, three times, with nothing between them."""
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
            choices=[SimpleNamespace(message={"role": "assistant", "content": "  "})],
        )

    client, slept = make_client(tmp_path)
    _install(client, create)

    with pytest.raises(ModelCallError, match="empty completion"):
        client.complete("writer-a", system="s", user="u")

    assert calls["n"] == 3
    assert slept == [2.0, 4.0]


# ------------------------------------------------------- provider-named delays


def _with_status(status: int, headers: dict | None = None) -> Exception:
    exc = _Boom("refused")
    exc.status_code = status
    exc.response = SimpleNamespace(status_code=status, headers=headers or {})
    return exc


def test_a_retry_after_header_beats_the_computed_delay(tmp_path):
    client, slept = make_client(tmp_path)
    create, _ = _raising(_with_status(429, {"retry-after": "7"}), succeed_on=2)
    _install(client, create)

    client.complete("writer-a", system="s", user="u")

    assert slept == [7.0]


def test_an_absurd_retry_after_is_capped(tmp_path):
    """A provider asking for ten minutes has effectively failed the call; the caller's
    own rotation to another model is the better move."""
    client, slept = make_client(tmp_path)
    create, _ = _raising(_with_status(429, {"retry-after": "600"}), succeed_on=2)
    _install(client, create)

    client.complete("writer-a", system="s", user="u")

    assert slept == [120.0]


def test_an_unparseable_retry_after_falls_back_to_the_computed_delay(tmp_path):
    """The HTTP-date form is legal and deliberately not honoured — comparing it needs a
    trusted clock, against a header already treated as advisory."""
    client, slept = make_client(tmp_path)
    create, _ = _raising(
        _with_status(429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), succeed_on=2
    )
    _install(client, create)

    client.complete("writer-a", system="s", user="u")

    assert slept == [2.0]


# ------------------------------------------------------------ permanent failures


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_a_permanent_failure_is_not_retried(tmp_path, status):
    """A malformed request, a rejected credential, or a model the proxy does not serve
    answers identically however many times it is sent. Retrying one burns the budget —
    and for a writer, the run's only eligible author — on a verdict already final."""
    client, slept = make_client(tmp_path)
    create, calls = _raising(_with_status(status))
    _install(client, create)

    with pytest.raises(PermanentCallError):
        client.complete("writer-a", system="s", user="u")

    assert calls["n"] == 1
    assert slept == []


def test_a_permanent_failure_is_still_a_model_call_error(tmp_path):
    """Every `except ModelCallError` in the graph predates this subclass and must keep
    catching it — the distinction is for `_create`, not for callers."""
    client, _ = make_client(tmp_path)
    create, _ = _raising(_with_status(400))
    _install(client, create)

    with pytest.raises(ModelCallError):
        client.complete("writer-a", system="s", user="u")


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_a_transient_status_is_retried(tmp_path, status):
    """The statuses backoff exists for. 408 and 429 are about the moment, not the
    request, and every 5xx is the server's problem to recover from."""
    client, _ = make_client(tmp_path)
    create, calls = _raising(_with_status(status))
    _install(client, create)

    with pytest.raises(ModelCallError, match="exhausted call retries"):
        client.complete("writer-a", system="s", user="u")

    assert calls["n"] == 3


# --------------------------------------------------------------- failure class


def test_an_empty_completion_names_its_class(tmp_path):
    """D-writer-failure-class: the token, not the message, is what a caller counts."""
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
            choices=[SimpleNamespace(message={"role": "assistant", "content": "  "})],
        )

    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    _install(client, create)

    with pytest.raises(ModelCallError) as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == "empty_completion"


def test_an_exhausted_budget_carries_the_cause_class_not_its_own(tmp_path):
    """What a reader needs from a run that spent its budget is which defect it spent it
    on. Three unparsed tool-call blocks and three timeouts are the same event today and
    want different fixes — one is a provider to re-pin, the other a moment to wait out.
    """
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[
                SimpleNamespace(
                    message={"role": "assistant", "content": "<｜tool▁calls▁begin｜>x<｜tool▁call▁end｜>"}
                )
            ],
        )

    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    _install(client, create)

    with pytest.raises(ModelCallError, match="exhausted call retries") as caught:
        client.complete("writer-a", system="s", user="u")

    assert calls["n"] == 3
    assert caught.value.failure_class == "unparsed_tool_markup"


def test_a_transport_failure_is_classified_by_status_never_by_message(tmp_path):
    """The same rule `_permanent` follows. A provider's wording is not an interface."""
    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    create, _ = _raising(_with_status(503))
    _install(client, create)

    with pytest.raises(ModelCallError) as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == "http_503"


def test_a_statusless_transport_failure_falls_back_rather_than_guessing(tmp_path):
    """A bare exception carrying nothing to read gets the generic token, not a class
    inferred from its text."""
    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    create, _ = _raising(_Boom("connection reset by peer"))
    _install(client, create)

    with pytest.raises(ModelCallError) as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == "call_failed"


@pytest.mark.parametrize(
    ("error_type", "failure_class"),
    [(APITimeoutError, "timeout"), (APIConnectionError, "connection")],
)
def test_an_sdk_transport_failure_is_classified_by_type(tmp_path, error_type, failure_class):
    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    create, _ = _raising(error_type(request=request))
    _install(client, create)

    with pytest.raises(ModelCallError) as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == failure_class


def test_an_identity_mismatch_names_its_class(tmp_path):
    def create(**kwargs):
        return SimpleNamespace(
            model="some-other-model",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[SimpleNamespace(message={"role": "assistant", "content": "OK"})],
        )

    client, _ = make_client(tmp_path)
    _install(client, create)

    with pytest.raises(ModelCallError, match="identity mismatch") as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == "identity_mismatch"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_a_permanent_failure_names_the_status_that_made_it_final(tmp_path, status):
    client, _ = make_client(tmp_path)
    create, _ = _raising(_with_status(status))
    _install(client, create)

    with pytest.raises(PermanentCallError) as caught:
        client.complete("writer-a", system="s", user="u")

    assert caught.value.failure_class == f"http_{status}"


# ------------------------------------- probe: capability vs availability (D-probe-capability-evidence)


def test_a_429_exhausting_the_budget_during_the_json_schema_probe_raises(tmp_path):
    """The live observation: `ra doctor` pinned `nemotron-3-ultra` to `json_object` on
    2026-08-11 after DeepInfra returned HTTP 429 three times during the `json_schema`
    probe. A 429 is an availability fact about the moment, not a capability fact about
    the alias — the probe must abort rather than silently fall through to a weaker
    mode."""
    client, _ = make_client(tmp_path, retry_backoff_seconds=0.0)
    create, calls = _raising(_with_status(429))
    _install(client, create)

    with pytest.raises(ConfigError, match="structured-output mode is unknown"):
        client.probe_structured_output("writer-a")

    assert calls["n"] == 3  # the call budget was spent before the probe gave up
    assert "writer-a" not in client._modes


def test_a_400_during_the_json_schema_probe_demotes_to_json_object(tmp_path):
    """`http_400` is how a provider says "I do not support this `response_format`" —
    genuine capability evidence, unlike a bare transient failure."""

    def create(**kwargs):
        response_format = kwargs.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            raise _with_status(400)
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[SimpleNamespace(message={"role": "assistant", "content": '{"ok": true}'})],
        )

    client, _ = make_client(tmp_path)
    _install(client, create)

    mode = client.probe_structured_output("writer-a")

    assert mode == "json_object"
    assert client._modes["writer-a"] == "json_object"


def test_a_malformed_output_demotes_to_the_next_mode(tmp_path):
    """The model answers, but not inside the closed schema — capability evidence."""

    def create(**kwargs):
        response_format = kwargs.get("response_format") or {}
        content = "not json" if response_format.get("type") == "json_schema" else '{"ok": true}'
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[SimpleNamespace(message={"role": "assistant", "content": content})],
        )

    client, _ = make_client(tmp_path)
    _install(client, create)

    mode = client.probe_structured_output("writer-a")

    assert mode == "json_object"


def test_a_401_during_the_probe_raises_rather_than_demoting(tmp_path):
    """A rejected credential says nothing about `response_format` support and must not
    be read as capability evidence."""
    client, _ = make_client(tmp_path)
    create, calls = _raising(_with_status(401))
    _install(client, create)

    with pytest.raises(ConfigError, match="structured-output mode is unknown"):
        client.probe_structured_output("writer-a")

    assert calls["n"] == 1  # permanent failures are never retried
    assert "writer-a" not in client._modes


def test_a_genuinely_incapable_alias_still_fails_closed_after_all_three_modes(tmp_path):
    """The pre-existing fail-closed path survives: when every mode is genuinely
    rejected on capability grounds, the probe still exhausts the ladder and raises the
    'cannot produce parseable structured output' `ConfigError` — not the new
    'could not be completed' one, which means something different."""
    client, _ = make_client(tmp_path)
    create, calls = _raising(_with_status(400))
    _install(client, create)

    with pytest.raises(ConfigError, match="cannot produce parseable structured output"):
        client.probe_structured_output("writer-a")

    assert calls["n"] == 3  # one attempt per mode, each rejected on capability grounds


# ------------------------------------------------------ audit privacy (RA-016)


class _Verdict(BaseModel):
    """A trivial closed schema for exercising `structured()`'s repair loop."""

    verdict: str


def _returning(content: str):
    """A `create` stand-in that always returns `content` as the assistant message."""

    def create(**kwargs):
        return SimpleNamespace(
            model=kwargs["model"],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[SimpleNamespace(message={"role": "assistant", "content": content})],
        )

    return create


def test_a_schema_violation_never_logs_the_rejected_content_at_info(tmp_path, caplog):
    """RA-016: `structured()`'s repair loop must not copy report-derived validation
    input into ordinary logs. `RA_LOG_LEVEL=INFO` is the container default (D-provider-retry), so a
    validator that quotes a private `claim_span` back in its error must not reach INFO.

    The report content is synthetic, but it stands in for exactly what a real `triage`
    validator embeds in its message — the span it could not find in the paragraph."""
    secret = "PRIVATE-CLAIM-SPAN-fluoridation-reduces-decay-by-25pct"
    client, _ = make_client(tmp_path)
    _install(client, _returning('{"verdict": "ok"}'))

    def validate(_parsed):
        raise ValueError(f"claim_span {secret!r} is not verbatim in the cited paragraph")

    with caplog.at_level(logging.INFO), pytest.raises(MalformedOutputError):
        client.structured(
            "writer-a",
            system="s",
            user="u",
            schema=_Verdict,
            repair_retries=0,
            validate=validate,
        )

    # The repair path ran and logged at INFO...
    assert "schema violation" in caplog.text
    assert "ValueError" in caplog.text
    # ...but the content the validator quoted never left the run.
    assert secret not in caplog.text


def test_span_fingerprints_are_stable_only_within_one_repair_loop(
    tmp_path, caplog, monkeypatch
):
    """The log can identify a re-roll within one call without exporting a stable
    verifier for guessed report text or a correlation identifier across calls."""
    secret = "PRIVATE-CLAIM-SPAN-fluoridation-reduces-decay-by-25pct"
    keys = iter([b"a" * 32, b"b" * 32])
    monkeypatch.setattr("reasonable_answer.llm.secrets.token_bytes", lambda _size: next(keys))
    client, _ = make_client(tmp_path)
    _install(client, _returning('{"verdict": "ok"}'))

    def validate(_parsed):
        raise LensValidationError(
            "claim_span is not a verbatim quote",
            code=ViolationCode.SPAN_NOT_VERBATIM,
            field="claim_span",
            rejected=secret,
        )

    with caplog.at_level(logging.INFO):
        for _ in range(2):
            with pytest.raises(MalformedOutputError):
                client.structured(
                    "writer-a",
                    system="s",
                    user="u",
                    schema=_Verdict,
                    repair_retries=1,
                    validate=validate,
                )

    fingerprints = re.findall(r"span=([0-9a-f]{8})", caplog.text)
    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[2] == fingerprints[3]
    assert fingerprints[0] != fingerprints[2]
    assert secret not in caplog.text
