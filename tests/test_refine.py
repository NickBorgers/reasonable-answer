"""`RefinementService` (D26, docs/question-refinement.md), driven entirely offline.

`StubClient` below is a minimal, local stand-in for `LLMClient` — `FakeClient` in
`tests/fakes.py` is built around the graph's schemas (`CritiqueOutput`,
`OrchestratorRecommendation`, ...) and doesn't know about `RefinementSuggestions`,
so a small purpose-built stub is clearer here than bending that one to fit.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from reasonable_answer.config import Config, ConfigError, RefineConfig, Roster
from reasonable_answer.schemas import RefinementSuggestion, RefinementSuggestions
from reasonable_answer.web import refine as refine_mod
from reasonable_answer.web.refine import RefinementService, _filter_suggestions

# --------------------------------------------------------------------------- stub


@dataclass
class StubClient:
    """`respond(alias, user) -> RefinementSuggestions` is called for every
    non-coalesced `structured()` invocation; raising propagates as a call failure,
    exactly like a real `ModelCallError`/`MalformedOutputError`/timeout would."""

    respond: Callable[[str, str], RefinementSuggestions]
    #: When set, `resolve_identities`/`probe_structured_output` raise `ConfigError`
    #: for any alias not in this set — simulates "the proxy does not serve it".
    known_aliases: set[str] | None = None
    calls: list[str] = field(default_factory=list)
    resolve_calls: list[list[str]] = field(default_factory=list)

    def resolve_identities(self, aliases: list[str]) -> dict[str, str]:
        self.resolve_calls.append(list(aliases))
        for alias in aliases:
            if self.known_aliases is not None and alias not in self.known_aliases:
                raise ConfigError(f"fail closed: alias '{alias}' is not served by the proxy")
        return {a: f"vendor/{a}" for a in aliases}

    def probe_structured_output(self, alias: str) -> str:
        if self.known_aliases is not None and alias not in self.known_aliases:
            raise ConfigError(f"fail closed: alias '{alias}' cannot produce structured output")
        return "json_schema"

    def structured(self, alias: str, *, system: str, user: str, schema: type, **kwargs: Any):
        assert schema is RefinementSuggestions
        self.calls.append(user)
        return self.respond(alias, user)


class FakeClock:
    """Monotonic-shaped, but fully caller-controlled — no sleeping in these tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTimer:
    """Stands in for `threading.Timer`: records scheduling and cancellation without
    ever actually firing on a background thread, so the orphan-linger tests are
    deterministic without sleeping past `orphan_linger_seconds`."""

    instances: list[FakeTimer] = []

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        """Test helper: run the scheduled callback as if the delay had elapsed."""
        self.function()


def one_suggestion(
    question: str = "Is it actually true, and what are the options?",
    transform: str = "check_the_premise_first",
    label: str = "check the premise first",
) -> RefinementSuggestions:
    return RefinementSuggestions(
        suggestions=[RefinementSuggestion(transform=transform, label=label, question=question)]
    )


def no_suggestions(_alias: str, _user: str) -> RefinementSuggestions:
    return RefinementSuggestions(suggestions=[])


def make_config(roster: Roster, **refine_overrides: Any) -> Config:
    return Config(roster=roster, refine=RefineConfig(**refine_overrides))


QUESTION = "Why is it illegal to relocate opossums in Texas?"


# ------------------------------------------------------------------------- config


def test_unknown_transform_name_raises_config_error():
    with pytest.raises(ConfigError):
        RefineConfig(enabled_transforms={"not_a_real_transform"})


def test_question_behind_the_question_excluded_from_default_transforms():
    assert "question_behind_the_question" not in RefineConfig().enabled_transforms


# ------------------------------------------------------------------------ startup


def test_start_raises_config_error_for_alias_the_proxy_does_not_serve(roster):
    config = make_config(roster, enabled=True)
    client = StubClient(respond=lambda _a, _u: one_suggestion(), known_aliases=set())  # serves nothing
    service = RefinementService(config, client=client)
    with pytest.raises(ConfigError):
        service.start()


def test_start_is_a_noop_when_disabled(roster):
    config = make_config(roster, enabled=False)
    client = StubClient(respond=lambda _a, _u: one_suggestion(), known_aliases=set())
    service = RefinementService(config, client=client)
    service.start()  # must not raise even though the alias is unknown to the stub
    assert client.resolve_calls == []


# -------------------------------------------------------------------------- basic


def test_disabled_config_returns_empty_offer_and_never_calls_client(roster):
    config = make_config(roster, enabled=False)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client)
    offer = service.suggest(QUESTION)
    assert offer.offer_id == ""
    assert offer.suggestions == ()
    assert client.calls == []


def test_blank_question_returns_empty_offer_without_calling_client(roster):
    config = make_config(roster, enabled=True)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client)
    offer = service.suggest("    ")
    assert offer.offer_id == ""
    assert client.calls == []


def test_happy_path_mints_offer_with_expected_json_shape(roster):
    config = make_config(roster, enabled=True)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client)
    offer = service.suggest(QUESTION)
    assert offer.offer_id != ""
    assert len(offer.offer_id) == 32
    assert offer.as_json() == {
        "offer_id": offer.offer_id,
        "suggestions": [
            {
                "transform": "check_the_premise_first",
                "label": "check the premise first",
                "question": "Is it actually true, and what are the options?",
            }
        ],
    }


def test_zero_suggestion_path_returns_empty_offer_and_mints_no_offer_record(roster):
    config = make_config(roster, enabled=True)
    client = StubClient(respond=no_suggestions)
    service = RefinementService(config, client=client)
    offer = service.suggest(QUESTION)
    assert offer.offer_id == ""
    assert offer.suggestions == ()
    assert len(service._offers) == 0  # noqa: SLF001 - white-box check


# ------------------------------------------------------------ deterministic rules


def _raw(transform: str = "check_the_premise_first", label: str = "a label", question: str = "Ok?"):
    # `model_construct` bypasses field validation (min/max length etc.) so we can
    # exercise the service's *own* deterministic checks in isolation, independent of
    # whether the schema would ever actually let such a value through.
    return RefinementSuggestion.model_construct(transform=transform, label=label, question=question)


def test_filter_drops_suggestion_missing_trailing_question_mark():
    raw = [_raw(question="This is not a question.")]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_over_length_label():
    raw = [_raw(label="x" * 41, question="Ok?")]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_over_length_question():
    raw = [_raw(question="Q" * 200 + "?")]  # 201 chars, over MAX_REFINE_QUESTION
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_entry_with_control_character_in_label():
    raw = [_raw(label="a label\nwith a newline", question="Ok?")]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_entry_with_control_character_in_question():
    raw = [_raw(question="Is\tthis ok?")]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_disabled_transform():
    raw = [_raw(transform="question_behind_the_question", question="Ok?")]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert out == ()


def test_filter_drops_duplicate_questions_case_and_whitespace_insensitive():
    raw = [
        _raw(question="Is it legal?"),
        _raw(question="is   it legal?  "),  # same after normalize+casefold
        _raw(question="Is it lawful?"),
    ]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=3
    )
    assert [s.question for s in out] == ["Is it legal?", "Is it lawful?"]


def test_filter_drops_suggestion_that_echoes_the_submitted_question():
    raw = [_raw(question="Is X legal in Texas?")]
    out = _filter_suggestions(
        raw,
        submitted_question="  is  x legal in texas?  ",
        enabled_transforms=["check_the_premise_first"],
        max_suggestions=3,
    )
    assert out == ()


def test_filter_truncates_to_max_suggestions():
    raw = [_raw(question=f"Question number {i}?") for i in range(5)]
    out = _filter_suggestions(
        raw, submitted_question="orig?", enabled_transforms=["check_the_premise_first"], max_suggestions=2
    )
    assert len(out) == 2
    assert [s.question for s in out] == ["Question number 0?", "Question number 1?"]


# ---------------------------------------------------------------------------- cache


def test_cache_hit_avoids_a_second_client_call(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    service.suggest(QUESTION)
    service.suggest(QUESTION)
    assert len(client.calls) == 1


def test_empty_results_are_cached(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True)
    client = StubClient(respond=no_suggestions)
    service = RefinementService(config, client=client, clock=clock)
    service.suggest(QUESTION)
    service.suggest(QUESTION)
    assert len(client.calls) == 1


def test_cache_entry_expires_and_is_recomputed(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, cache_ttl_seconds=60)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    service.suggest(QUESTION)
    clock.advance(61)
    service.suggest(QUESTION)
    assert len(client.calls) == 2


def test_cache_hit_still_mints_a_fresh_offer_id(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    first = service.suggest(QUESTION)
    second = service.suggest(QUESTION)
    assert len(client.calls) == 1
    assert first.offer_id != second.offer_id
    assert first.offer_id != "" and second.offer_id != ""


def test_cache_evicts_oldest_entry_at_cache_entries_capacity(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, cache_entries=16)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    keys = []
    for i in range(17):
        question = f"Question variant number {i} for cache eviction?"
        service.suggest(question)
        keys.append(refine_mod._normalize(question))
    assert len(service._cache) == 16  # noqa: SLF001
    first_key = (
        keys[0],
        refine_mod.PROMPT_VERSION,
        service._alias,  # noqa: SLF001
        config.refine.max_suggestions,
        service._enabled_transforms,  # noqa: SLF001
    )
    assert first_key not in service._cache  # noqa: SLF001


def test_coalesces_concurrent_identical_misses_into_one_client_call(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, concurrency=4)
    release = threading.Event()
    # Only the coalescing *owner* (exactly one of the four callers) ever reaches
    # `slow_respond` — the other three wait on its result instead of calling the
    # client at all. Two parties: the owner thread and this test thread.
    entered = threading.Barrier(2, timeout=5)

    def slow_respond(_alias: str, _user: str) -> RefinementSuggestions:
        entered.wait(timeout=5)
        release.wait(timeout=5)
        return one_suggestion()

    client = StubClient(respond=slow_respond)
    service = RefinementService(config, client=client, clock=clock)

    results: list = []

    def worker():
        results.append(service.suggest(QUESTION))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    entered.wait(timeout=5)
    release.set()
    for t in threads:
        t.join(timeout=5)

    assert len(client.calls) == 1
    assert len(results) == 4
    assert all(r.suggestions for r in results)
    # Coalesced waiters still each get their own offer id.
    assert len({r.offer_id for r in results}) == 4


# ------------------------------------------------------------------- concurrency


def test_semaphore_saturation_sheds_rather_than_blocking(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, concurrency=1)
    holding = threading.Event()
    release = threading.Event()

    def blocking_respond(_alias: str, _user: str) -> RefinementSuggestions:
        holding.set()
        release.wait(timeout=5)
        return one_suggestion()

    client = StubClient(respond=blocking_respond)
    service = RefinementService(config, client=client, clock=clock)

    t = threading.Thread(target=lambda: service.suggest(QUESTION))
    t.start()
    assert holding.wait(timeout=5)

    # A different key so this cannot coalesce with the in-flight call; the single
    # concurrency permit is held, so this must shed immediately rather than queue.
    shed = service.suggest("A completely different question entirely?")
    assert shed.offer_id == ""

    release.set()
    t.join(timeout=5)


# ----------------------------------------------------------------------- timeout


def test_timeout_yields_empty_offer_and_holds_the_permit_through_the_linger(roster, monkeypatch):
    monkeypatch.setattr(refine_mod, "_Timer", FakeTimer)
    FakeTimer.instances.clear()
    clock = FakeClock()
    config = make_config(roster, enabled=True, concurrency=1, orphan_linger_seconds=30)

    def timing_out(_alias: str, _user: str) -> RefinementSuggestions:
        raise TimeoutError("simulated provider timeout")

    client = StubClient(respond=timing_out)
    service = RefinementService(config, client=client, clock=clock)

    offer = service.suggest(QUESTION)
    assert offer.offer_id == ""
    assert len(client.calls) == 1

    # A timer was scheduled instead of releasing inline.
    assert len(FakeTimer.instances) == 1
    timer = FakeTimer.instances[0]
    assert timer.cancelled is False

    # The permit is still held: a fresh key must shed rather than call the client.
    shed = service.suggest("Another distinct question entirely, not cached?")
    assert shed.offer_id == ""
    assert len(client.calls) == 1  # the second attempt never reached the client

    # Firing the timer releases the permit, as it would after the real delay.
    timer.fire()
    client.respond = lambda _a, _u: one_suggestion()
    recovered = service.suggest("Yet another distinct question for recovery check?")
    assert recovered.offer_id != ""
    assert len(client.calls) == 2


def test_shutdown_cancels_pending_orphan_linger_timers(roster, monkeypatch):
    monkeypatch.setattr(refine_mod, "_Timer", FakeTimer)
    FakeTimer.instances.clear()
    clock = FakeClock()
    config = make_config(roster, enabled=True, concurrency=1, orphan_linger_seconds=30)

    def timing_out(_alias: str, _user: str) -> RefinementSuggestions:
        raise TimeoutError("simulated provider timeout")

    client = StubClient(respond=timing_out)
    service = RefinementService(config, client=client, clock=clock)
    service.suggest(QUESTION)

    assert len(FakeTimer.instances) == 1
    timer = FakeTimer.instances[0]
    assert timer.cancelled is False

    service.shutdown()

    assert timer.cancelled is True
    assert len(service._timers) == 0  # noqa: SLF001


def test_orphan_linger_zero_releases_the_permit_inline(roster, monkeypatch):
    monkeypatch.setattr(refine_mod, "_Timer", FakeTimer)
    FakeTimer.instances.clear()
    clock = FakeClock()
    config = make_config(roster, enabled=True, concurrency=1, orphan_linger_seconds=0)

    def timing_out(_alias: str, _user: str) -> RefinementSuggestions:
        raise TimeoutError("simulated provider timeout")

    client = StubClient(respond=timing_out)
    service = RefinementService(config, client=client, clock=clock)
    service.suggest(QUESTION)

    assert FakeTimer.instances == []  # no timer scheduled at all

    # Permit released immediately, so a distinct key can proceed right away.
    client.respond = lambda _a, _u: one_suggestion()
    recovered = service.suggest("A brand new question needing the permit right away?")
    assert recovered.offer_id != ""


# ----------------------------------------------------------------------- resolve


@pytest.fixture
def offered_service(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, offer_ttl_seconds=300)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    offer = service.suggest(QUESTION)
    return service, offer, clock


def test_resolve_verified_when_index_and_question_match(offered_service):
    service, offer, _clock = offered_service
    chosen = offer.suggestions[0]
    result = service.resolve(offer.offer_id, "0", chosen.question)
    assert result.provenance == "verified"
    assert result.offer_id == offer.offer_id
    assert result.transform == chosen.transform
    assert result.selected_index == 0
    assert result.question_at_offer == QUESTION
    assert result.suggestions == offer.suggestions
    assert result.original_sha256 is not None


def test_resolve_unverified_when_offer_expired(offered_service):
    service, offer, clock = offered_service
    clock.advance(10_000)
    result = service.resolve(offer.offer_id, "0", offer.suggestions[0].question)
    assert result.provenance == "unverified"
    assert result.offer_id == offer.offer_id
    assert result.suggestions == ()


def test_resolve_unverified_for_unknown_offer_id(offered_service):
    service, _offer, _clock = offered_service
    unknown = "z" * 32
    result = service.resolve(unknown, "0", "Some question?")
    assert result.provenance == "unverified"
    assert result.offer_id == unknown


def test_resolve_unverified_for_index_out_of_range(offered_service):
    service, offer, _clock = offered_service
    result = service.resolve(offer.offer_id, "5", offer.suggestions[0].question)
    assert result.provenance == "unverified"
    assert result.selected_index is None


def test_resolve_unverified_when_submitted_question_does_not_match_selection(offered_service):
    service, offer, _clock = offered_service
    result = service.resolve(offer.offer_id, "0", "A totally different question?")
    assert result.provenance == "unverified"
    assert result.transform is None


@pytest.mark.parametrize(
    "bogus_id",
    ["", "short", "a" * 33, "a" * 31, "not-url-safe-chars-!!!" + "a" * 10, None],
)
def test_resolve_unverified_for_malformed_or_oversized_offer_id(offered_service, bogus_id):
    service, _offer, _clock = offered_service
    result = service.resolve(bogus_id, "0", "Some question?")
    assert result.provenance == "unverified"
    assert result.offer_id == ""
    # The supplied garbage must never be persisted or echoed anywhere.
    if bogus_id:
        assert bogus_id not in str(result.content())
        assert bogus_id not in str(result.event_fields())


def test_resolve_returns_none_when_nothing_was_claimed(offered_service):
    service, _offer, _clock = offered_service
    assert service.resolve(None, None, "Some question?") is None
    assert service.resolve("", "", "Some question?") is None


def test_offer_map_evicts_oldest_at_offer_entries_capacity(roster):
    clock = FakeClock()
    config = make_config(roster, enabled=True, offer_entries=64)
    client = StubClient(respond=lambda _a, _u: one_suggestion())
    service = RefinementService(config, client=client, clock=clock)
    first_offer = service.suggest(QUESTION)
    for i in range(64):
        service.suggest(f"Filler question number {i} to force eviction?")
    assert len(service._offers) == 64  # noqa: SLF001
    assert first_offer.offer_id not in service._offers  # noqa: SLF001


def test_event_fields_carry_no_question_or_suggestion_text(offered_service):
    service, offer, _clock = offered_service
    chosen = offer.suggestions[0]
    result = service.resolve(offer.offer_id, "0", chosen.question)
    assert result.provenance == "verified"
    dumped = str(result.event_fields())
    assert chosen.question not in dumped
    assert chosen.label not in dumped
    assert QUESTION not in dumped
