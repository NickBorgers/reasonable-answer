"""The stop decision. Every rule in docs/convergence.md is asserted by number,
plus the two properties the design rests on: totality and termination."""

from __future__ import annotations

import itertools

import pytest

from conftest import cleared, make_ci, make_view
from reasonable_answer.controller import acceptance_state, decide, detect_cycle
from reasonable_answer.schemas import LensStatus, SeverityCounts
from reasonable_answer.taxonomy import LENSES


def test_rule_1_fatal_wins_over_everything():
    ci = make_ci(make_view(totals={"blocking": 9}), fatal=True, fatal_reason="writer pool empty")
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (1, "aborted")


def test_rule_2_failed_lens_recritiques_before_any_conclusion():
    # material == 0 and fully cleared would otherwise be `accepted` — the incomplete
    # review check must precede it (RC-004).
    ci = make_ci(
        make_view(lenses_failed=1),
        lens_status=cleared({l.value: 2 for l in LENSES}),
        critique_attempts_remaining=3,
    )
    d = decide(ci)
    assert (d.rule, d.action) == (2, "recritique")


def test_rule_3_failed_lens_without_budget_aborts():
    ci = make_ci(make_view(lenses_failed=1), critique_attempts_remaining=0)
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (3, "aborted")


def test_rule_4_min_ticks_blocks_early_acceptance():
    # A clean, fully-cleared artifact still cannot be accepted below min_ticks —
    # this is what stops a seed being rubber-stamped on its first critique.
    ci = make_ci(
        make_view(round=1, min_ticks=2),
        lens_status=cleared({l.value: 2 for l in LENSES}),
    )
    d = decide(ci)
    assert (d.rule, d.action) == (4, "generate")


def test_rule_5_cap_with_blocking_needs_human():
    ci = make_ci(make_view(round=8, hard_cap=8, totals={"blocking": 1}))
    assert decide(ci).terminal_status == "needs_human_review"


def test_rule_6_cap_with_major_is_exhausted():
    ci = make_ci(make_view(round=8, hard_cap=8, totals={"major": 2}))
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (6, "exhausted_unresolved")


def test_rule_7_strong_acceptance():
    ci = make_ci(lens_status=cleared({l.value: 2 for l in LENSES}))
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (7, "accepted")


def test_rule_8_top_up_does_not_generate_and_works_at_the_cap():
    # RG-001: the confirmation top-up must stay reachable at the hard cap, because
    # it neither generates nor advances `round`.
    ci = make_ci(
        make_view(round=8, hard_cap=8),
        lens_status=cleared({"logic": 2, "evidence": 2, "completeness": 1}),
    )
    d = decide(ci)
    assert (d.rule, d.action) == (8, "recritique")
    assert d.recritique_lenses and all(l.value == "completeness" for l in d.recritique_lenses)


def test_rule_9_polish_requires_the_orchestrator_and_is_cap_gated():
    view = make_view(round=3, hard_cap=8, totals={"minor": 4})
    status = cleared({l.value: 2 for l in LENSES}, unused=0)
    # strong_met wins first — rule 7 precedes 9 by design.
    assert decide(make_ci(view, lens_status=status, polish_recommended=True)).rule == 7

    # with a lens under-cleared but not toppable, polish becomes reachable
    status = cleared({"logic": 2, "evidence": 2, "completeness": 1}, eligible=1, unused=0)
    d = decide(make_ci(view, lens_status=status, polish_recommended=True))
    assert (d.rule, d.polish) == (9, True)

    # ...and the same state at the cap must NOT generate (RH-001)
    at_cap = make_view(round=8, hard_cap=8, totals={"minor": 4})
    d = decide(make_ci(at_cap, lens_status=status, polish_recommended=True))
    assert d.action != "generate"


def test_rule_9_never_fires_without_the_orchestrator():
    view = make_view(round=3, totals={"minor": 4})
    status = cleared({"logic": 2, "evidence": 2, "completeness": 1}, eligible=1, unused=0)
    d = decide(make_ci(view, lens_status=status, polish_recommended=False))
    assert d.rule != 9


def test_rule_10_roster_limited_lens_degrades_to_converged_unconfirmed():
    status = [
        LensStatus(lens=LENSES[0], cleared_count=2, eligible_count=3, unused_eligible=1),
        LensStatus(lens=LENSES[1], cleared_count=2, eligible_count=3, unused_eligible=1),
        LensStatus(lens=LENSES[2], cleared_count=1, eligible_count=1, unused_eligible=0),
    ]
    d = decide(make_ci(lens_status=status))
    assert (d.rule, d.terminal_status) == (10, "converged_unconfirmed")
    assert "completeness" in d.note


def test_rule_11_clean_but_unconfirmed_when_budget_is_spent():
    status = cleared({"logic": 2, "evidence": 2, "completeness": 1}, eligible=3, unused=1)
    d = decide(make_ci(lens_status=status, confirmation_attempts_remaining=0))
    assert (d.rule, d.terminal_status) == (11, "exhausted_unresolved")


def test_rule_12_cycle_detected():
    ci = make_ci(make_view(totals={"major": 1}, cycle_detected=True))
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (12, "needs_human_review")


@pytest.mark.parametrize(
    "totals,expected",
    [({"blocking": 1}, "needs_human_review"), ({"major": 1}, "exhausted_unresolved")],
)
def test_rule_13_stagnation(totals, expected):
    ci = make_ci(make_view(totals=totals, stagnation_count=3), stagnation_limit=3)
    d = decide(ci)
    assert (d.rule, d.terminal_status) == (13, expected)


def test_rule_14_ordinary_continue():
    d = decide(make_ci(make_view(totals={"major": 2})))
    assert (d.rule, d.action) == (14, "generate")


def test_no_rule_generates_at_or_beyond_the_cap():
    """RI-001 — the hard cap is genuinely hard. Sweep the whole input space."""
    for blocking, major, minor in itertools.product((0, 1), (0, 1), (0, 3)):
        for polish, cleared_n, eligible in itertools.product((True, False), (0, 1, 2), (1, 3)):
            view = make_view(
                round=8,
                hard_cap=8,
                min_ticks=2,
                totals={"blocking": blocking, "major": major, "minor": minor},
            )
            ci = make_ci(
                view,
                lens_status=cleared({l.value: cleared_n for l in LENSES}, eligible=eligible,
                                    unused=eligible - cleared_n if eligible > cleared_n else 0),
                polish_recommended=polish,
            )
            assert decide(ci).action != "generate", (blocking, major, minor, polish, cleared_n)


def test_decision_is_total():
    """First-match semantics must select exactly one rule for every reachable state."""
    for fatal, failed, budget in itertools.product((True, False), (0, 1), (0, 3)):
        for rnd, blocking, major, minor in itertools.product((1, 3, 8), (0, 2), (0, 2), (0, 2)):
            for cleared_n, eligible, stag, cycle in itertools.product(
                (0, 1, 2), (1, 3), (0, 3), (True, False)
            ):
                view = make_view(
                    round=rnd,
                    lenses_failed=failed,
                    totals={"blocking": blocking, "major": major, "minor": minor},
                    stagnation_count=stag,
                    cycle_detected=cycle,
                )
                ci = make_ci(
                    view,
                    fatal=fatal,
                    critique_attempts_remaining=budget,
                    lens_status=cleared({l.value: cleared_n for l in LENSES}, eligible=eligible),
                )
                d = decide(ci)
                assert 1 <= d.rule <= 14
                assert d.action in ("generate", "recritique", "terminal")
                if d.action == "terminal":
                    assert d.terminal_status is not None


def test_known_unacceptable_artifact_is_never_accepted():
    """The central safety property: material issues can never yield an accept."""
    for blocking, major in ((1, 0), (0, 1), (2, 3)):
        for cleared_n in (0, 1, 2):
            ci = make_ci(
                make_view(totals={"blocking": blocking, "major": major}),
                lens_status=cleared({l.value: cleared_n for l in LENSES}),
            )
            assert decide(ci).terminal_status not in ("accepted", "converged_unconfirmed")


def test_acceptance_state_requires_zero_material():
    status = cleared({l.value: 2 for l in LENSES})
    assert acceptance_state(status, material=0) == "strong_met"
    assert acceptance_state(status, material=1) == "none"


def test_detect_cycle():
    assert not detect_cycle(["a", "b", "c"], period=4)
    assert detect_cycle(["a", "b", "a"], period=4)
    assert not detect_cycle(["a", "b", "c", "d", "e", "a"], period=2)
