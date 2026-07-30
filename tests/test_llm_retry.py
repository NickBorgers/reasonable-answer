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
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.llm import (
    LLMClient,
    MalformedOutputError,
    ModelCallError,
    PermanentCallError,
)


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
