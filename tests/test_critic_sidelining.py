"""A critic whose calls keep failing is sidelined for the rest of the run
(D-failing-critic-sidelined).

The production shape (2026-09-04..09): `glm-5.3` failed 46 of its 66 critique calls, 39 of
them as three 300-second timeouts in a row, and was drawn again on every draft because the
only record of who had reviewed resets with each generation. Its failures never tripped
rule 2 — the lens's other critic completed — so every round paid fifteen minutes per lens
for a second witness that never arrived.

Driven by the scriptable fake proxy, offline like the rest of the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

from fakes import FakeClient

from reasonable_answer.config import Budgets, Config, ReviewConfig, Roster
from reasonable_answer.graph import (
    Runtime,
    _critic_roster,
    _critique,
    _run_fingerprint,
    run,
)
from reasonable_answer.llm import MalformedOutputError, ModelCallError
from reasonable_answer.schemas import CritiqueOutput, LensResult
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import LENSES

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""

IDENTITIES = {
    "writer-a": "vendor-a/model-a",
    "writer-b": "vendor-b/model-b",
    "c1": "vendor-c/one",
    "c2": "vendor-d/two",
    "c3": "vendor-e/three",
    "c4": "vendor-f/four",
}

#: Wide enough that sidelining `c1` leaves every lens two families deep.
WIDE = Roster(
    writers=["writer-a", "writer-b"],
    critics={lens.value: ["c1", "c2", "c3", "c4"] for lens in LENSES},
)


def _config(tmp_path, roster: Roster = WIDE, **review) -> Config:
    return Config(
        roster=roster,
        budgets=Budgets(min_ticks=2, hard_cap=5, retry_backoff_seconds=0.0),
        review=ReviewConfig(**review),
        runs_dir=tmp_path / "runs",
    )


def _runtime(cfg: Config, critique_fn, tmp_path, name: str = "run-side") -> Runtime:
    client = FakeClient(identities=IDENTITIES, critique_fn=critique_fn, report_fn=lambda n: REPORT)
    return Runtime(config=cfg, client=client, identities=IDENTITIES, store=RunStore(tmp_path, name))


def _fresh_draft(carry: dict | None = None) -> dict:
    """The state `_critique` sees on a new draft: per-artifact accumulators empty, the
    whole-run strike record carried over from the previous pass."""
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "artifact_hash": "h" * 64,
        "author_identity": IDENTITIES["writer-a"],
        "pending_lenses": [lens.value for lens in LENSES],
        "run_date": "2026-09-10",
    }
    if carry:
        state["critic_strikes"] = carry["critic_strikes"]
        state["sidelined_critics"] = carry["sidelined_critics"]
    return state


def _c1_times_out(alias, _user) -> CritiqueOutput:
    if alias == "c1":
        raise ModelCallError("c1: exhausted call retries (Request timed out.)", failure_class="timeout")
    return CritiqueOutput(issues=[])


def _events(store_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (store_dir / "events.jsonl").read_text().splitlines()]


# ------------------------------------------------------------------ counting


def test_a_critic_that_fails_two_passes_running_is_sidelined(tmp_path):
    rt = _runtime(_config(tmp_path), _c1_times_out, tmp_path)

    first = _critique(_fresh_draft(), rt)
    assert first["critic_strikes"]["c1"] == 1
    assert first["sidelined_critics"] == []

    second = _critique(_fresh_draft(first), rt)
    assert second["sidelined_critics"] == ["c1"]

    sidelined = [e for e in _events(rt.store.dir) if e["kind"] == "critic_sidelined"]
    assert sidelined == [
        {**sidelined[0], "critic": "vendor-c/one", "strikes": 2, "failure_class": "timeout"}
    ]


def test_a_sidelined_critic_is_never_drawn_again(tmp_path):
    rt = _runtime(_config(tmp_path), _c1_times_out, tmp_path)
    state = _fresh_draft()
    for _ in range(2):
        state = _fresh_draft(_critique(state, rt))
    asked_before = sum(1 for call in rt.client.calls if call.alias == "c1")

    third = _critique(state, rt)

    assert sum(1 for call in rt.client.calls if call.alias == "c1") == asked_before
    for lens in LENSES:
        drawn = [LensResult.model_validate(r).critic_alias for r in third["lens_results"][lens.value]]
        assert drawn == ["c2", "c3"], drawn


def test_one_bad_minute_across_three_lenses_is_one_strike(tmp_path):
    """Per pass, not per call: an alias serving every lens in one bad minute fails three
    calls, and that is one piece of evidence about it, not three."""
    rt = _runtime(_config(tmp_path), _c1_times_out, tmp_path)
    out = _critique(_fresh_draft(), rt)
    assert out["critic_strikes"]["c1"] == 1


def test_a_completed_review_clears_the_count(tmp_path):
    failing = {"now": True}

    def flaky_c1(alias, user):
        if alias == "c1" and failing["now"]:
            raise ModelCallError("c1: exhausted call retries (Request timed out.)", failure_class="timeout")
        return CritiqueOutput(issues=[])

    rt = _runtime(_config(tmp_path), flaky_c1, tmp_path)
    first = _critique(_fresh_draft(), rt)
    failing["now"] = False
    second = _critique(_fresh_draft(first), rt)
    failing["now"] = True
    third = _critique(_fresh_draft(second), rt)

    assert second["critic_strikes"]["c1"] == 0
    assert third["critic_strikes"]["c1"] == 1
    assert third["sidelined_critics"] == []


def test_a_schema_violation_is_not_a_strike(tmp_path):
    """A model that answered outside the schema has its own repair budget and its own
    audition verdict; it says nothing about whether the alias can answer a call."""
    def c1_malformed(alias, _user):
        if alias == "c1":
            raise MalformedOutputError("not json")
        return CritiqueOutput(issues=[])

    rt = _runtime(_config(tmp_path), c1_malformed, tmp_path)
    state = _fresh_draft()
    for _ in range(3):
        out = _critique(state, rt)
        state = _fresh_draft(out)

    assert out["critic_strikes"].get("c1", 0) == 0
    assert out["sidelined_critics"] == []
    failed = [LensResult.model_validate(r) for r in out["lens_results"]["logic"] if r["failed"]]
    assert [r.failure_class for r in failed] == ["schema_violation"]


def test_a_limit_of_zero_never_sidelines(tmp_path):
    rt = _runtime(_config(tmp_path, critic_strike_limit=0), _c1_times_out, tmp_path)
    state = _fresh_draft()
    for _ in range(4):
        out = _critique(state, rt)
        state = _fresh_draft(out)
    assert out["critic_strikes"]["c1"] == 4
    assert out["sidelined_critics"] == []


def test_the_critique_event_records_how_a_review_failed(tmp_path):
    rt = _runtime(_config(tmp_path), _c1_times_out, tmp_path)
    _critique(_fresh_draft(), rt)
    classes = {
        e["critic"]: e["failure_class"] for e in _events(rt.store.dir) if e["kind"] == "critique"
    }
    assert classes["vendor-c/one"] == "timeout"
    assert classes["vendor-d/two"] is None


# ------------------------------------------------------------------ the roster gate


#: `c1` is the only critic that can review `writer-a` on logic.
THIN = Roster(
    writers=["writer-a", "writer-b"],
    critics={"logic": ["c1", "writer-a"], "evidence": ["c2", "c3"], "completeness": ["c2", "c3"]},
)


def test_a_critic_the_roster_cannot_do_without_is_never_sidelined(tmp_path):
    """Sidelining asks `validate_roster_health`, exactly as a degraded startup does; a lens
    left without an eligible non-author is the failure fail-closed exists to refuse, and a
    slow critic is better than none."""
    rt = _runtime(_config(tmp_path, THIN), _c1_times_out, tmp_path)
    state = _fresh_draft()
    for _ in range(3):
        out = _critique(state, rt)
        state = _fresh_draft(out)

    assert out["critic_strikes"]["c1"] == 3
    assert out["sidelined_critics"] == []
    assert [r["critic_alias"] for r in out["lens_results"]["logic"]] == ["c1"]


def test_a_later_attempt_that_cannot_spare_the_critic_restores_it(tmp_path):
    """A resumed attempt may start under a roster startup degraded (D-degraded-roster). If
    that roster cannot staff a lens without the sidelined alias, the configured pools win."""
    rt = _runtime(_config(tmp_path, THIN), _c1_times_out, tmp_path)
    assert _critic_roster({"sidelined_critics": ["c1"]}, rt) is rt.config.roster


def test_the_strike_limit_is_not_part_of_the_run_identity(tmp_path):
    """`_run_fingerprint` hashes `budgets`; a knob there would make every paused run look
    like changed inputs at the deploy that shipped it."""
    assert _run_fingerprint(_config(tmp_path), "q", None) == _run_fingerprint(
        _config(tmp_path, critic_strike_limit=7), "q", None
    )


# ------------------------------------------------------------------ the verdict


#: Logic is two families deep only while `c1` is in it.
TWO_DEEP_LOGIC = Roster(
    writers=["writer-a", "writer-b"],
    critics={"logic": ["c1", "c2"], "evidence": ["c2", "c3"], "completeness": ["c2", "c3"]},
)


def test_a_lens_thinned_by_sidelining_can_reach_only_converged_unconfirmed(tmp_path):
    """Sidelining narrows the pool clearance is counted against, so a lens left with one
    family is `roster_limited` — the honest weaker verdict, never `accepted` — and the
    run stops asking a critic that will not answer instead of ending `exhausted_unresolved`
    waiting on it."""
    cfg = _config(tmp_path, TWO_DEEP_LOGIC)
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=_c1_times_out,
        report_fn=lambda n: f"{REPORT}\nRevision {n}.\n",
    )

    final = run(cfg, question="Is it so?", client=client)

    assert final["terminal_status"] == "converged_unconfirmed"
    kinds = [e["kind"] for e in _events(Path(final["run_dir"]))]
    assert kinds.count("critic_sidelined") == 1
    passes_asking_c1 = {call.user for call in client.calls if call.alias == "c1"}
    assert len(passes_asking_c1) <= 2
