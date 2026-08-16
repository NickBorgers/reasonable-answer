"""D-degraded-roster: an alias that cannot be probed costs the roster that alias, not
the run — but only when what is left can still staff the game.

The production incident these are written against (2026-08-15) is the shape to keep in
mind: five aliases answering normally, one behind an overloaded provider, and three
queued runs aborted before a token was spent.
"""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient

from reasonable_answer.config import Budgets, Config, ConfigError, ProxyConfig, Roster
from reasonable_answer.graph import _run_fingerprint, build_runtime
from reasonable_answer.schemas import CritiqueOutput

IDENTITIES = {
    "writer-a": "vendor-a/model-a",
    "writer-b": "vendor-b/model-b",
    "logic-spec": "vendor-c/logic",
    "evidence-spec": "vendor-d/evidence",
    "completeness-spec": "vendor-e/completeness",
    "referee": "vendor-f/referee",
}


def _client(**kwargs) -> FakeClient:
    return FakeClient(
        identities=IDENTITIES,
        critique_fn=lambda *_: CritiqueOutput(issues=[]),
        report_fn=lambda _: "",
        **kwargs,
    )


def _config(tmp_path, roster: Roster) -> Config:
    return Config(
        proxy=ProxyConfig(),
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=5, retry_backoff_seconds=0.0),
        runs_dir=tmp_path / "runs",
    )


def _full_roster(**overrides) -> Roster:
    fields = {
        "writers": ["writer-a", "writer-b"],
        "critics": {
            "logic": ["logic-spec", "writer-a", "writer-b"],
            "evidence": ["evidence-spec", "writer-a", "writer-b"],
            "completeness": ["completeness-spec", "writer-a", "writer-b"],
        },
    }
    fields.update(overrides)
    return Roster(**fields)


def _startup_event(rt) -> dict:
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return next(json.loads(line) for line in lines if json.loads(line)["kind"] == "startup")


# ------------------------------------------------------------------ degrading


def test_an_unreachable_writer_is_dropped_and_the_run_still_starts(tmp_path):
    """The 2026-08-15 case exactly: one writer behind an overloaded provider, every
    other alias healthy, and a roster that can still staff every lens without it."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.add("writer-b")

    rt = build_runtime(config, client=client)

    assert rt.config.roster.writers == ["writer-a"]
    assert all("writer-b" not in pool for pool in rt.config.roster.critics.values())
    assert "writer-b" not in rt.config.roster.all_aliases
    assert any("degraded roster" in w and "writer-b" in w for w in rt.warnings)


def test_the_configured_roster_is_left_alone(tmp_path):
    """Degrading builds a new config rather than mutating the caller's. The resume
    fingerprint is computed from the caller's, so mutating it would change the run's
    identity — see `test_a_degraded_attempt_does_not_change_the_run_identity`."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.add("writer-b")

    build_runtime(config, client=client)

    assert config.roster.writers == ["writer-a", "writer-b"]
    assert "writer-b" in config.roster.critics["logic"]


def test_a_degraded_attempt_does_not_change_the_run_identity(tmp_path):
    """`_run_fingerprint` must keep hashing the *configured* roster. Hashing what the
    attempt settled for would make the provider's recovery look like changed inputs, and
    trip `ResumeMismatch` at the one moment the run could have finished properly."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.add("writer-b")

    rt = build_runtime(config, client=client)

    configured = _run_fingerprint(config, "q", None)
    assert configured == _run_fingerprint(config, "q", None)
    # The guard is only meaningful if the two rosters genuinely hash differently — i.e.
    # if fingerprinting `rt.config` would have been an observable behaviour change.
    assert configured != _run_fingerprint(rt.config, "q", None)


def test_an_unreachable_orchestrator_falls_back_to_the_documented_default(tmp_path):
    """Its job is bounded ints in, one boolean out, so any probed alias can do it.
    Keeping the unreachable alias would disable rule 9 silently for the whole run."""
    config = _config(tmp_path, _full_roster(orchestrator="referee"))
    client = _client()
    client.unprobeable.add("referee")

    rt = build_runtime(config, client=client)

    assert rt.config.roster.orchestrator is None
    assert rt.config.roster.orchestrator_alias == "writer-a"
    assert rt.config.roster.writers == ["writer-a", "writer-b"]


def test_the_startup_event_names_the_aliases_that_were_unreachable(tmp_path):
    """Per attempt, not per run: a resumed run may have had a different roster each
    time, and this is the only record of which (docs/run-provenance.md)."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.add("writer-b")

    rt = build_runtime(config, client=client)

    assert _startup_event(rt)["unreachable_aliases"] == ["writer-b"]


def test_a_healthy_start_records_an_empty_unreachable_list(tmp_path):
    config = _config(tmp_path, _full_roster())

    rt = build_runtime(config, client=_client())

    assert _startup_event(rt)["unreachable_aliases"] == []
    assert rt.config.roster.writers == ["writer-a", "writer-b"]


# -------------------------------------------------------------- failing closed


def test_losing_every_writer_fails_closed(tmp_path):
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.update({"writer-a", "writer-b"})

    with pytest.raises(ConfigError, match="no writer is reachable"):
        build_runtime(config, client=client)


def test_emptying_a_critic_pool_fails_closed(tmp_path):
    """The failure names the lens the outage cost, not a field-length error."""
    roster = _full_roster(
        critics={
            "logic": ["logic-spec"],
            "evidence": ["evidence-spec", "writer-a", "writer-b"],
            "completeness": ["completeness-spec", "writer-a", "writer-b"],
        }
    )
    config = _config(tmp_path, roster)
    client = _client()
    client.unprobeable.add("logic-spec")

    with pytest.raises(ConfigError, match="no critic is reachable for logic"):
        build_runtime(config, client=client)


def test_a_lens_left_with_only_its_author_fails_closed(tmp_path):
    """The case a pool-size check would miss: the pool is not empty, it just contains
    nobody who is allowed to review the writer about to author. `validate_roster_health`
    is the gate precisely so this stays covered without a second rule."""
    roster = Roster(
        writers=["writer-a"],
        critics={
            "logic": ["logic-spec", "writer-a"],
            "evidence": ["evidence-spec", "writer-a"],
            "completeness": ["completeness-spec", "writer-a"],
        },
    )
    config = _config(tmp_path, roster)
    client = _client()
    client.unprobeable.add("logic-spec")

    with pytest.raises(ConfigError, match="no eligible non-author critic"):
        build_runtime(config, client=client)


def test_a_definite_capability_failure_is_not_degraded_away(tmp_path):
    """D-probe-capability-evidence's distinction survives: an alias that completed every
    attempt without producing parseable output is a measured fact about the model, not
    about the moment, and still refuses to start."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.structured_incapable.add("writer-b")

    with pytest.raises(ConfigError, match="cannot produce structured output"):
        build_runtime(config, client=client)


def test_a_reachable_explicit_orchestrator_is_left_alone(tmp_path):
    """The other branch of the orchestrator ternary: dropping some *other* alias must
    not quietly reassign a referee that was answering fine."""
    config = _config(tmp_path, _full_roster(orchestrator="referee"))
    client = _client()
    client.unprobeable.add("writer-b")

    rt = build_runtime(config, client=client)

    assert rt.config.roster.orchestrator == "referee"
    assert rt.config.roster.orchestrator_alias == "referee"


def _searching_config(tmp_path) -> Config:
    return Config(
        proxy=ProxyConfig(),
        roster=_full_roster(),
        budgets=Budgets(min_ticks=2, hard_cap=5, retry_backoff_seconds=0.0),
        runs_dir=tmp_path / "runs",
        search={"enabled": True},
    )


def test_a_non_probe_startup_refusal_is_typed_the_same_way(tmp_path, monkeypatch):
    """The wrapper's scope is every fail-closed refusal `build_runtime` can make, not
    only the probe path. A tool-incapable writer under D-retrieval-opt-in has nothing to
    do with a provider outage, and still defers rather than crashing — it is equally a
    fact about the deployment. It carries the unclassified code, not the roster one."""
    from reasonable_answer.graph import StartupRefused

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-token")
    client = _client()
    client.tool_capable["writer-a"] = False

    with pytest.raises(StartupRefused) as caught:
        build_runtime(_searching_config(tmp_path), client=client)
    assert caught.value.code == "startup_refused"
    assert "cannot emit tool calls" in str(caught.value)


def test_a_missing_search_credential_defers_rather_than_crashing(tmp_path, monkeypatch):
    """`SearchConfigError` is not a `ConfigError`, but it is raised from the same place
    for the same reason and would otherwise have escaped the wrapper as a bare crash."""
    from reasonable_answer.graph import StartupRefused

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    with pytest.raises(StartupRefused) as caught:
        build_runtime(_searching_config(tmp_path), client=_client())
    assert caught.value.code == "startup_refused"


def test_startup_refusals_are_typed_so_a_worker_can_tell_them_from_a_bad_run(tmp_path):
    """`build_runtime` never reads the question or the seed, so whatever it refuses it
    refuses for every queued run alike. `StartupRefused` is how the worker knows that
    without pattern-matching on messages (D-deferred-not-abandoned), and it subtypes
    `ConfigError` so every existing fail-closed caller is unaffected."""
    from reasonable_answer.graph import StartupRefused

    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.update({"writer-a", "writer-b"})

    with pytest.raises(StartupRefused) as caught:
        build_runtime(config, client=client)
    assert isinstance(caught.value, ConfigError)


def test_every_alias_is_probed_before_anything_is_decided(tmp_path):
    """Giving up on the first unreachable alias cannot know whether the rest are healthy,
    and which ones are is exactly what says whether the run may go on."""
    config = _config(tmp_path, _full_roster())
    client = _client()
    client.unprobeable.add("writer-a")

    build_runtime(config, client=client)

    assert set(client.probes) == set(IDENTITIES) - {"writer-a", "referee"}
