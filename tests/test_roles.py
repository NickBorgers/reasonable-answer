"""Role assignment invariants — the ones that make `accepted` mean anything."""

from __future__ import annotations

import pytest

from reasonable_answer import roles
from reasonable_answer.config import ConfigError, Roster, validate_roster_health
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
