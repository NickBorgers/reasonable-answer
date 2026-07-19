"""The deterministic controller — the whole stop decision, in one ordered table.

This is a pure function of ``ControllerInput``. No LLM, no I/O, no clock. It is the
only thing that terminates the run, and it always terminates: see the measure
argument in docs/convergence.md.

Rules are evaluated in order; **first match wins**. Rule numbers here are the rule
numbers in the design doc, and the tests assert them by number.
"""

from __future__ import annotations

from .schemas import ControllerInput, Decision, LensStatus
from .taxonomy import Lens


def acceptance_state(lens_status: list[LensStatus], material: int) -> str:
    """`none` / `weak_met` / `strong_met`, derived from per-lens clearance."""
    if material > 0:
        return "none"
    if all(s.cleared_count >= 2 for s in lens_status):
        return "strong_met"
    if all(s.cleared_count >= 1 for s in lens_status) and all(
        s.roster_limited for s in lens_status if s.cleared_count < 2
    ):
        return "weak_met"
    return "none"


def decide(ci: ControllerInput) -> Decision:
    view = ci.view
    material = view.totals.blocking + view.totals.major
    toppable = [s for s in ci.lens_status if s.toppable]

    # 1 — fatal
    if ci.fatal:
        return Decision(
            rule=1,
            action="terminal",
            terminal_status="aborted",
            note=ci.fatal_reason or "fatal",
        )

    # 2 — incomplete review, budget remains: re-critique before any conclusion (RC-004)
    if view.lenses_failed > 0 and ci.critique_attempts_remaining > 0:
        return Decision(
            rule=2,
            action="recritique",
            recritique_lenses=_failed_lenses(ci),
            note="lens failure; partial counts discarded",
        )

    # 3 — incomplete review, no budget: cannot complete a review at all
    if view.lenses_failed > 0:
        return Decision(
            rule=3,
            action="terminal",
            terminal_status="aborted",
            note="lens failure with no critique budget remaining",
        )

    # 4 — floor on ticks: never accept a draft on its first critique
    if view.round < view.min_ticks:
        return Decision(rule=4, action="generate", note="below min_ticks")

    # 5/6 — the cap, with material issues outstanding
    if view.round >= view.hard_cap and view.totals.blocking > 0:
        return Decision(
            rule=5,
            action="terminal",
            terminal_status="needs_human_review",
            note="hard cap reached with blocking issues",
        )
    if view.round >= view.hard_cap and view.totals.major > 0:
        return Decision(
            rule=6,
            action="terminal",
            terminal_status="exhausted_unresolved",
            note="hard cap reached with major issues",
        )

    if material == 0:
        # 7 — every lens independently double-checked on this exact artifact
        if acceptance_state(ci.lens_status, material) == "strong_met":
            return Decision(
                rule=7,
                action="terminal",
                terminal_status="accepted",
                note="every lens strongly-cleared on the final artifact",
            )

        # 8 — top up per-lens clearance. Does NOT generate and does NOT advance
        #     `round`, so it stays reachable at the cap (RG-001).
        if toppable and ci.confirmation_attempts_remaining > 0:
            return Decision(
                rule=8,
                action="recritique",
                recritique_lenses=[s.lens for s in toppable],
                note="confirmation top-up by a fresh eligible non-author",
            )

        # 9 — the orchestrator LLM's only authority, and it is cap-gated (RH-001)
        if (
            view.round < view.hard_cap
            and view.totals.minor > 0
            and ci.polish_recommended
            and view.polish_used < view.polish_cap
        ):
            return Decision(rule=9, action="generate", polish=True, note="minor polish pass")

        # 10 — honest weaker guarantee: a lens the roster cannot double-check
        if acceptance_state(ci.lens_status, material) == "weak_met":
            limited = [s.lens.value for s in ci.lens_status if s.cleared_count < 2]
            return Decision(
                rule=10,
                action="terminal",
                terminal_status="converged_unconfirmed",
                note=f"roster-limited lens(es): {', '.join(limited)}",
            )

        # 11 — clean, but confirmation budget spent before clearance was reached
        return Decision(
            rule=11,
            action="terminal",
            terminal_status="exhausted_unresolved",
            note="clean but unconfirmed: confirmation budget exhausted",
        )

    # 12 — the loop is revisiting artifacts it has already produced
    if view.cycle_detected:
        return Decision(
            rule=12,
            action="terminal",
            terminal_status="needs_human_review",
            note="artifact cycle detected; freezing best-scoring version",
        )

    # 13 — the signal is stuck; more ticks will not move it
    if view.stagnation_count >= ci.stagnation_limit:
        return Decision(
            rule=13,
            action="terminal",
            terminal_status=(
                "needs_human_review" if view.totals.blocking > 0 else "exhausted_unresolved"
            ),
            note=f"signal stagnant for {view.stagnation_count} ticks",
        )

    # 14 — the ordinary case: material issues, so write the next report
    return Decision(rule=14, action="generate", note="material issues remain")


def _failed_lenses(ci: ControllerInput) -> list[Lens]:
    # The controller is told *how many* lenses failed via the view; which ones failed
    # is an operational identifier, carried alongside. The graph passes the concrete
    # list through `recritique_lenses`, so an empty list here means "all of them".
    return []


def detect_cycle(hash_history: list[str], period: int) -> bool:
    """A repeat within the last `period` artifacts means the loop is going in circles."""
    if len(hash_history) < 2:
        return False
    window = hash_history[-(period + 1) :]
    return len(set(window)) < len(window)


def best_scoring_index(
    scores: list[tuple[int, int, int]], w_b: int = 100, w_m: int = 10, w_n: int = 1
) -> int:
    """Minimal `w_b*blocking + w_m*major + w_n*minor`; ties go to the earliest round."""
    if not scores:
        return 0
    best = min(
        range(len(scores)),
        key=lambda i: (w_b * scores[i][0] + w_m * scores[i][1] + w_n * scores[i][2], i),
    )
    return best
