"""Front-loaded review depth (D-front-loaded-depth) — two independent critics per lens
on every draft, configurable, and none of the isolation properties spent to buy it.

Driven by the scriptable fake proxy, offline like the rest of the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeClient
from pydantic import ValidationError

from reasonable_answer import roles, triage
from reasonable_answer.config import Budgets, Config, ConfigError, ReviewConfig, Roster
from reasonable_answer.graph import Runtime, _critique, run
from reasonable_answer.llm import ModelCallError
from reasonable_answer.schemas import CritiqueOutput, LensResult, RawIssue, StructuralRef
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import LENSES, Category, Lens, Severity

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""

LENS_CATEGORY = {
    "logic": Category.OVERSTATED_CLAIM,
    "evidence": Category.UNCITED_CLAIM,
    "completeness": Category.OMITTED_COUNTERARGUMENT,
}


def lens_of(user: str) -> str:
    for lens in ("logic", "evidence", "completeness"):
        if f"YOUR DIMENSION: {lens}" in user:
            return lens
    raise AssertionError("no lens in prompt")


def issue(category: Category, severity: Severity = Severity.MAJOR) -> RawIssue:
    return RawIssue(
        category=category,
        severity=severity,
        locus=StructuralRef(section=1, paragraph=1),
        claim_span="A claim that is fully supported",
        rationale="no citation attached",
        instruction="cite a source or remove the claim",
    )


def clean(_alias, _user) -> CritiqueOutput:
    return CritiqueOutput(issues=[])


def make_config(tmp_path, roster: Roster, **review) -> Config:
    return Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=5, retry_backoff_seconds=0.0),
        review=ReviewConfig(**review) if review else ReviewConfig(),
        runs_dir=tmp_path / "runs",
    )


def events(cfg: Config, final: dict) -> list[dict]:
    path = Path(cfg.runs_dir) / final["run_id"] / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def critics_per_pass(cfg: Config, final: dict) -> list[dict[str, list[str]]]:
    """One entry per critique pass: lens -> the critic identities that read it.

    A pass ends at the `triage` event, which is the point every consumer of the
    reviews sees them together.
    """
    passes: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    for event in events(cfg, final):
        if event["kind"] == "critique":
            current.setdefault(event["lens"], []).append(event["critic"])
        elif event["kind"] == "triage":
            passes.append(current)
            current = {}
    return passes


# ------------------------------------------------- the default: two critics per lens


def test_every_lens_is_read_by_two_distinct_critics_on_the_first_pass(
    identities, roster, tmp_path
):
    """The whole point of D-front-loaded-depth: the second opinion is part of discovery,
    not a formality collected after a pass has already reported the draft clean."""
    cfg = make_config(tmp_path, roster)
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: REPORT)
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    first = critics_per_pass(cfg, final)[0]
    assert set(first) == {lens.value for lens in LENSES}
    for lens, critics in first.items():
        assert len(critics) == 2, (lens, critics)
        assert len(set(critics)) == 2, f"{lens} was read twice by one model: {critics}"


def test_a_clean_first_pass_is_already_strongly_cleared(identities, roster, tmp_path):
    """Depth 2 makes rule 8 unnecessary on the common path: the pass that reports the
    artifact clean has already collected both clean records."""
    cfg = make_config(tmp_path, roster)
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: REPORT)
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "accepted"
    triages = [e for e in events(cfg, final) if e["kind"] == "triage"]
    assert triages[0]["acceptance"] == "strong_met"
    assert all(count == 2 for count in triages[0]["cleared"].values()), triages[0]
    assert not [e for e in events(cfg, final) if e["kind"] == "control" and e["rule"] == 8]


def test_the_second_critics_finding_is_discovered_before_the_first_revision(
    identities, roster, tmp_path
):
    """`run-c4c0e64b4128` in miniature: the first critic on each lens sees nothing and
    the second sees a material defect. Under the old one-critic pass that defect could
    not surface until a clean pass had already been paid for and rule 8 fired."""
    def second_critic_finds_it(alias, user):
        # The head of every pool is the `<lens>-spec` specialist; the second slot is a
        # writer. Keyed on the alias rather than on call order, because a slate's calls
        # run concurrently.
        lens = lens_of(user)
        if alias == f"{lens}-spec":
            return CritiqueOutput(issues=[])
        return CritiqueOutput(issues=[issue(LENS_CATEGORY[lens])])

    cfg = make_config(tmp_path, roster)
    client = FakeClient(
        identities=identities, critique_fn=second_critic_finds_it, report_fn=lambda n: REPORT
    )
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    first_triage = next(e for e in events(cfg, final) if e["kind"] == "triage")
    assert first_triage["material"] == 3, "one material finding per lens, on pass one"
    # And nothing was accepted on the strength of the first critic's silence.
    assert final["terminal_status"] != "accepted"


# ------------------------------------------------------------------ configurability


def test_depth_one_restores_the_single_critic_pass(identities, roster, tmp_path):
    """`review.depth: 1` is the pre-D-front-loaded-depth behaviour, from configuration
    rather than from a checkout — so the two arms are comparable."""
    cfg = make_config(tmp_path, roster, depth=1)
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: REPORT)
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    first = critics_per_pass(cfg, final)[0]
    assert all(len(critics) == 1 for critics in first.values()), first
    # The second opinion is back to being rule 8's job.
    assert [e for e in events(cfg, final) if e["kind"] == "control" and e["rule"] == 8]


def test_depth_is_configurable_per_lens(tmp_path):
    roster = Roster(
        writers=["writer-a", "writer-b"],
        critics={lens.value: ["c1", "c2", "c3"] for lens in LENSES},
    )
    identities = {
        "writer-a": "vendor-a/model-a",
        "writer-b": "vendor-b/model-b",
        "c1": "vendor-c/one",
        "c2": "vendor-d/two",
        "c3": "vendor-e/three",
    }
    cfg = make_config(tmp_path, roster, depth=1, per_lens={"evidence": 3})
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: REPORT)
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    first = critics_per_pass(cfg, final)[0]
    assert len(first["evidence"]) == 3, first
    assert len(first["logic"]) == 1, first
    assert len(first["completeness"]) == 1, first


def test_an_unknown_lens_in_per_lens_fails_closed():
    with pytest.raises(ConfigError, match="unknown lenses"):
        ReviewConfig(per_lens={"vibes": 2})


def test_an_out_of_range_per_lens_depth_fails_closed():
    with pytest.raises(ConfigError, match="between 1 and 4"):
        ReviewConfig(per_lens={"logic": 9})


@pytest.mark.parametrize("depth", [0, 5])
def test_an_out_of_range_default_depth_fails_closed(depth):
    with pytest.raises(ValidationError):
        ReviewConfig(depth=depth)


# ---------------------------------------------------- eligibility is not spent for depth


def test_the_slate_never_contains_the_author(roster, identities):
    """Author exclusion is per slot, at resolved identity, however deep the slate."""
    author = identities["writer-a"]
    for lens in LENSES:
        slate = roles.critic_slate(roster, identities, lens, author, set(), depth=4)
        assert author not in {identities[a] for a in slate}
        assert len(slate) == len({identities[a] for a in slate})


def test_the_slate_never_repeats_one_model_behind_two_aliases():
    """RA-017 at depth: two aliases for one model are one reviewer, so a depth-2 slate
    over such a pool is one critic, not a model double-reviewing itself to strong."""
    r = Roster(
        writers=["w1", "w2"],
        critics={lens.value: ["a", "b"] for lens in LENSES},
    )
    ids = {"w1": "p/w1", "w2": "p/w2", "a": "p/same", "b": "p/same"}
    slate = roles.critic_slate(r, ids, Lens.LOGIC, "p/w1", set(), depth=2)
    assert slate == ["a"]


def test_the_slate_uses_distinct_model_families_for_independent_witnesses():
    roster = Roster(
        writers=["writer"],
        critics={lens.value: ["claude-a", "claude-b", "gemma"] for lens in LENSES},
    )
    identities = {
        "writer": "openrouter/z-ai/glm-5.2",
        "claude-a": "anthropic/claude-sonnet-4-5",
        "claude-b": "openrouter/anthropic/claude-opus-4.1",
        "gemma": "openrouter/google/gemma-4-31b-it",
    }

    slate = roles.critic_slate(
        roster, identities, Lens.LOGIC, identities["writer"], set(), depth=2
    )

    assert slate == ["claude-a", "gemma"]


def test_a_roster_limited_lens_still_runs_one_critic_and_converges(identities, tmp_path):
    """Depth is a ceiling, not a quota: a lens the roster can staff once is not an
    abort, it is the honest weaker guarantee rule 10 already issued."""
    roster = Roster(
        writers=["writer-a", "writer-b"],
        critics={
            "logic": ["logic-spec", "writer-a", "writer-b"],
            "evidence": ["evidence-spec", "writer-a", "writer-b"],
            # Only one model that is never the author.
            "completeness": ["completeness-spec"],
        },
    )
    cfg = make_config(tmp_path, roster)
    client = FakeClient(identities=identities, critique_fn=clean, report_fn=lambda n: REPORT)
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    assert final["terminal_status"] == "converged_unconfirmed"
    first = critics_per_pass(cfg, final)[0]
    assert len(first["completeness"]) == 1, first
    assert len(first["logic"]) == 2, first


# -------------------------------------------------------- fail-closed, at the right unit


def _runtime(cfg: Config, identities, critique_fn, tmp_path, name: str) -> Runtime:
    client = FakeClient(
        identities=identities, critique_fn=critique_fn, report_fn=lambda n: REPORT
    )
    return Runtime(
        config=cfg,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, name),
    )


def _state(identities) -> dict:
    return {
        "question": "Is it so?",
        "report": REPORT,
        "artifact_hash": "h" * 64,
        "author_identity": identities["writer-a"],
        "pending_lenses": ["logic"],
        "run_date": "2026-07-28",
    }


def test_one_failed_critic_does_not_discard_the_other_review(
    identities, roster, tmp_path
):
    """Fail-closed is about a review, not about a lens's whole slate: the failed call
    contributes nothing, and the completed one is not thrown away to re-ask."""
    def only_the_first_works(alias, _user):
        if alias == "logic-spec":
            return CritiqueOutput(issues=[issue(Category.OVERSTATED_CLAIM)])
        raise ModelCallError("provider exploded")

    cfg = make_config(tmp_path, roster)
    rt = _runtime(cfg, identities, only_the_first_works, tmp_path, "run-half")
    out = _critique(_state(identities), rt)

    results = [LensResult.model_validate(r) for r in out["lens_results"]["logic"]]
    assert [r.failed for r in results] == [False, True]
    # The lens is reviewed, so rules 2/3 do not fire...
    assert triage.unreviewed_lenses(results) == [Lens.EVIDENCE, Lens.COMPLETENESS]
    # ...and the failed review adds no issue and mints no clean record.
    _, totals = triage.tally(results)
    assert totals.major == 1
    assert triage.clean_records(results) == []


def test_depth_one_failed_confirmation_does_not_become_a_lens_failure(
    identities, roster, tmp_path
):
    def specialists_only(alias, _user):
        if alias.endswith("-spec"):
            return CritiqueOutput(issues=[])
        raise ModelCallError("confirmation provider exploded")

    cfg = make_config(tmp_path, roster, depth=1)
    client = FakeClient(
        identities=identities, critique_fn=specialists_only, report_fn=lambda n: REPORT
    )
    final = run(cfg, question="Is it so?", seed=REPORT, client=client)

    controls = [event for event in events(cfg, final) if event["kind"] == "control"]
    assert any(event["rule"] == 8 for event in controls)
    assert controls[-1]["rule"] == 11
    assert not any(event["rule"] in {2, 3} for event in controls)
    assert final["terminal_status"] == "exhausted_unresolved"


def test_a_lens_whose_every_critic_fails_is_still_an_incomplete_review(
    identities, roster, tmp_path
):
    """The rule 2 / rule 3 trigger is unchanged where it was ever load-bearing."""
    def always_fails(_alias, _user):
        raise ModelCallError("provider exploded")

    cfg = make_config(tmp_path, roster)
    rt = _runtime(cfg, identities, always_fails, tmp_path, "run-dead")
    out = _critique(_state(identities), rt)

    results = [LensResult.model_validate(r) for r in out["lens_results"]["logic"]]
    assert all(r.failed for r in results)
    assert Lens.LOGIC in triage.unreviewed_lenses(results)


#: A pool wide enough that a second pass still has fresh models to draw from, so what
#: it draws is the depth shortfall rather than the exhausted-pool fallback.
WIDE_IDENTITIES = {
    "writer-a": "vendor-a/model-a",
    "writer-b": "vendor-b/model-b",
    "c1": "vendor-c/one",
    "c2": "vendor-d/two",
    "c3": "vendor-e/three",
    "c4": "vendor-f/four",
}
WIDE_ROSTER = Roster(
    writers=["writer-a", "writer-b"],
    critics={lens.value: ["c1", "c2", "c3", "c4"] for lens in LENSES},
)


def test_a_top_up_asks_only_for_the_depth_the_lens_is_short_of(tmp_path):
    """A second pass over a lens that already holds one completed review draws one
    fresh critic, not another full slate."""
    def first_only(alias, _user):
        if alias == "c1":
            return CritiqueOutput(issues=[])
        raise ModelCallError("provider exploded")

    cfg = make_config(tmp_path, WIDE_ROSTER)
    rt = _runtime(cfg, WIDE_IDENTITIES, first_only, tmp_path, "run-topup")
    state = _state(WIDE_IDENTITIES)
    state |= _critique(state, rt)
    before = len(state["lens_results"]["logic"])
    state |= _critique(state, rt)

    added = state["lens_results"]["logic"][before:]
    assert [r["critic_alias"] for r in added] == ["c3"], added


def test_a_lens_that_lost_every_review_asks_for_the_full_depth_again(tmp_path):
    """The other side of the shortfall arithmetic: nothing completed, so the retry
    restores the whole depth rather than trickling one critic at a time."""
    def always_fails(_alias, _user):
        raise ModelCallError("provider exploded")

    cfg = make_config(tmp_path, WIDE_ROSTER)
    rt = _runtime(cfg, WIDE_IDENTITIES, always_fails, tmp_path, "run-retry")
    state = _state(WIDE_IDENTITIES)
    state |= _critique(state, rt)
    before = len(state["lens_results"]["logic"])
    state |= _critique(state, rt)

    added = state["lens_results"]["logic"][before:]
    assert [r["critic_alias"] for r in added] == ["c3", "c4"], added


# ------------------------------------------------------- one finding, however many report it


def test_two_critics_reporting_one_defect_count_it_once(roster):
    """Otherwise depth would inflate `totals`, inflate the stagnation signature, and
    make the view disagree with the defect list it summarizes."""
    shared = issue(Category.OVERSTATED_CLAIM)
    results = [
        LensResult(
            lens=Lens.LOGIC,
            artifact_hash="h" * 64,
            critic_alias=alias,
            critic_identity=f"p/{alias}",
            artifact_author_identity="p/author",
            issues=[shared],
        )
        for alias in ("one", "two")
    ]
    _, totals = triage.tally(results)
    assert totals.major == 1
    assert len(triage.to_defects(results)) == 1


def test_the_higher_severity_survives_a_collapse(roster):
    """A second reviewer may escalate a finding and may never soften it — the same
    direction the mechanical floor clamps in (RC-005)."""
    results = [
        LensResult(
            lens=Lens.LOGIC,
            artifact_hash="h" * 64,
            critic_alias=alias,
            critic_identity=f"p/{alias}",
            artifact_author_identity="p/author",
            issues=[issue(Category.OVERSTATED_CLAIM, severity)],
        )
        for alias, severity in (("one", Severity.MAJOR), ("two", Severity.BLOCKING))
    ]
    _, totals = triage.tally(results)
    assert (totals.blocking, totals.major) == (1, 0)
    assert triage.to_defects(results)[0].severity is Severity.BLOCKING
    # ...and the order the reviews arrived in cannot change that.
    _, reversed_totals = triage.tally(list(reversed(results)))
    assert (reversed_totals.blocking, reversed_totals.major) == (1, 0)


# ------------------------------------------------------------------------- isolation


def test_neither_critic_in_a_slate_can_see_the_other(identities, roster, tmp_path):
    """The two reviews of one lens are as blind to each other as the three lenses
    always were: same prompt, fresh context, no sight of the other's findings."""
    cfg = make_config(tmp_path, roster)
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[issue(LENS_CATEGORY[lens_of(u)])]),
        report_fn=lambda n: REPORT,
    )
    run(cfg, question="Is it so?", seed=REPORT, client=client)

    critiques = [c for c in client.calls if c.schema == "CritiqueOutput"]
    assert len(critiques) >= 6
    for call in critiques:
        assert "no citation attached" not in call.user, "a critique leaked into a critic"
        assert "cite a source or remove the claim" not in call.user
