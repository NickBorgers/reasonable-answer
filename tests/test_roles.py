"""Role assignment invariants — the ones that make `accepted` mean anything."""

from __future__ import annotations

import pytest

from reasonable_answer import roles
from reasonable_answer.config import ConfigError, Roster, _family, validate_roster_health
from reasonable_answer.schemas import CleanRecord
from reasonable_answer.taxonomy import LENSES, Lens


def test_a_model_never_critiques_its_own_draft(roster, identities):
    for lens in LENSES:
        eligible = roles.eligible_critics(roster, identities, lens, identities["writer-a"])
        assert "writer-a" not in eligible


def test_eligibility_is_deduplicated_by_resolved_identity(roster):
    """RA-017: two aliases for one underlying model are ONE reviewer, not two."""
    identities = {
        "writer-a": "vendor-a/model-a",
        "writer-b": "vendor-b/model-b",
        "logic-spec": "vendor-b/model-b",  # same model as writer-b, different alias
        "evidence-spec": "vendor-d/evidence",
        "completeness-spec": "vendor-e/completeness",
    }
    eligible = roles.eligible_critics(roster, identities, Lens.LOGIC, "vendor-a/model-a")
    assert {identities[a] for a in eligible} == {"vendor-b/model-b"}
    assert len(eligible) == 1


def test_next_writer_is_never_the_current_author(roster, identities):
    alias = roles.next_writer(roster, identities, identities["writer-a"], rotation=0)
    assert alias == "writer-b"
    alias = roles.next_writer(roster, identities, identities["writer-b"], rotation=0)
    assert alias == "writer-a"


def test_next_writer_fails_closed_with_a_single_writer(identities):
    solo = Roster(
        writers=["writer-a"],
        critics={lens.value: ["logic-spec", "evidence-spec"] for lens in LENSES},
    )
    with pytest.raises(roles.RosterExhausted):
        roles.next_writer(solo, identities, "vendor-a/model-a", rotation=0)


def test_pick_critic_prefers_a_model_that_has_not_reviewed_this_artifact(roster, identities):
    """This is what converts a weak clearance into a strong one."""
    first = roles.pick_critic(roster, identities, Lens.LOGIC, identities["writer-a"], set())
    second = roles.pick_critic(
        roster, identities, Lens.LOGIC, identities["writer-a"], {identities[first]}
    )
    assert identities[first] != identities[second]


def test_pick_critic_fails_closed_when_the_author_is_the_only_candidate(identities):
    narrow = Roster(
        writers=["writer-a"],
        critics={lens.value: ["writer-a"] for lens in LENSES},
    )
    with pytest.raises(roles.RosterExhausted):
        roles.pick_critic(narrow, identities, Lens.LOGIC, "vendor-a/model-a", set())


def test_assert_author_exclusion_catches_a_smuggled_self_review():
    with pytest.raises(roles.RosterExhausted):
        roles.assert_author_exclusion("same/model", "same/model", Lens.LOGIC)


def test_clean_records_for_another_hash_never_count(roster, identities):
    """RC-002: regeneration resets attestations; stale ones can't accept an artifact."""
    stale = [
        CleanRecord(
            artifact_hash="old" * 21 + "x",
            lens=lens,
            critic_identity=identities["logic-spec"],
            artifact_author_identity=identities["writer-a"],
        )
        for lens in LENSES
    ]
    status = roles.lens_statuses(
        roster, identities, identities["writer-a"], "new" * 21 + "y", stale, {}
    )
    assert all(s.cleared_count == 0 for s in status)


def test_roster_health_fails_closed_when_a_lens_has_no_non_author(identities):
    bad = Roster(
        writers=["writer-a"],
        critics={
            "logic": ["writer-a"],
            "evidence": ["evidence-spec"],
            "completeness": ["completeness-spec"],
        },
    )
    from reasonable_answer.config import Budgets, Config

    cfg = Config(roster=bad, budgets=Budgets())
    with pytest.raises(ConfigError, match="no eligible non-author"):
        validate_roster_health(cfg, identities)


def test_roster_health_warns_on_a_single_family_lens(roster):
    identities = {
        "writer-a": "anthropic/claude-a",
        "writer-b": "anthropic/claude-b",
        "logic-spec": "anthropic/claude-c",
        "evidence-spec": "openai/gpt",
        "completeness-spec": "google/gemma",
    }
    from reasonable_answer.config import Budgets, Config

    cfg = Config(roster=roster, budgets=Budgets())
    warnings = validate_roster_health(cfg, identities)
    assert any("weak independence" in w and "logic" in w for w in warnings)


# ------------------------------------------------------- orchestrator selection


def _orch_roster(orchestrator: str | None) -> Roster:
    return Roster(
        writers=["writer-a", "writer-b"],
        critics={lens.value: ["logic-spec", "writer-a", "writer-b"] for lens in LENSES},
        orchestrator=orchestrator,
    )


def test_orchestrator_defaults_to_the_first_writer():
    assert _orch_roster(None).orchestrator_alias == "writer-a"


def test_an_explicit_orchestrator_is_used_verbatim():
    assert _orch_roster("referee").orchestrator_alias == "referee"


def test_a_mistyped_orchestrator_key_is_rejected_rather_than_silently_defaulted():
    """extra='forbid' is what stops `orchestrater:` falling back to writers[0] unseen."""
    with pytest.raises(Exception, match="orchestrater"):
        Roster(
            writers=["writer-a"],
            critics={lens.value: ["logic-spec"] for lens in LENSES},
            orchestrater="referee",
        )


def test_all_aliases_carries_the_orchestrator_exactly_once():
    """It must be there or startup never resolves its identity nor probes its
    structured-output mode — both of which fail silently at call time (RA-015)."""
    assert _orch_roster("referee").all_aliases.count("referee") == 1
    # ...and no duplicate when it is just writers[0] again.
    assert _orch_roster(None).all_aliases.count("writer-a") == 1


def test_roster_health_fails_closed_on_an_unresolvable_orchestrator(identities):
    from reasonable_answer.config import Budgets, Config

    cfg = Config(roster=_orch_roster("never-registered"), budgets=Budgets())
    with pytest.raises(ConfigError, match="never-registered"):
        validate_roster_health(cfg, identities)


# ------------------------------------------------------------- family grouping


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("openrouter/google/gemma-4-31b-it", "gemma"),
        ("ollama_chat/gemma4:26b-a4b-it-q8_0", "gemma"),
        ("openrouter/z-ai/glm-5.2", "glm"),
        ("openrouter/mistralai/mistral-large-2512", "mistral"),
        ("openrouter/deepseek/deepseek-v4-flash", "deepseek"),
        ("openrouter/minimax/minimax-m3", "minimax"),
        ("anthropic/claude-haiku-4-5", "claude"),
    ],
)
def test_family_is_keyed_on_the_model_name_not_the_provider(identity, expected):
    assert _family(identity) == expected


def test_two_gemma_checkpoints_are_not_mistaken_for_independent_reviewers():
    """The bug this fixes: 'ollama_chat/...' has two path segments, so keying on the
    prefix returned the serving backend and made a Gemma pair look decorrelated."""
    roster = Roster(
        writers=["writer-a"],
        critics={lens.value: ["gemma-hosted", "gemma-local"] for lens in LENSES},
    )
    identities = {
        "writer-a": "openrouter/z-ai/glm-5.2",
        "gemma-hosted": "openrouter/google/gemma-4-31b-it",
        "gemma-local": "ollama_chat/gemma4:26b-a4b-it-q8_0",
    }
    from reasonable_answer.config import Budgets, Config

    cfg = Config(roster=roster, budgets=Budgets())
    warnings = validate_roster_health(cfg, identities)
    assert any("weak independence" in w for w in warnings)
