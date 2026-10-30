"""A revision may not drop a read source without reading, and a large net loss of read
sources is a finding (D-no-unread-source-drop)."""

from __future__ import annotations

import json

import pytest
from test_graph import OPS_PREVIOUS
from test_read_before_citing import URL_A, URL_B, _runtime

from reasonable_answer import graph as graph_mod
from reasonable_answer import prompts
from reasonable_answer import report as report_mod
from reasonable_answer.config import ConfigError, ReviewConfig, RevisionConfig
from reasonable_answer.controller import select_shipped_index
from reasonable_answer.graph import _critique_one, _generate
from reasonable_answer.taxonomy import Category, Lens, Severity, clamp_to_floor
from reasonable_answer.triage import (
    dropped_read_sources,
    mechanical_source_loss_issues,
    read_cited_sources,
    source_loss_floor,
    validate_issue,
)

READ = [("web_search", '{"query": "q"}'), ("read_source", f'{{"url": "{URL_A}"}}')]
URL_C = "https://example.org/c"

#: The previous draft cites A and B; the revision keeps A and drops B.
PREVIOUS = (
    "## Findings\n\nThe effect was 4.2 percent [1]. A second effect was larger [2].\n\n"
    f"## Sources\n\n[1] Paper A. {URL_A}\n\n[2] Paper B. {URL_B}\n"
)
DROPPED = f"## Findings\n\nThe effect was 4.2 percent [1].\n\n## Sources\n\n[1] Paper A. {URL_A}\n"
BOTH_READ = {URL_A: "full_text", URL_B: "full_text"}


def _events(rt, kind):
    lines = (rt.store.dir / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == kind]


def _script(rt, per_call):
    """`per_call[i]` is the tool script of the i-th writer call (the last one repeats)."""
    calls = {"n": 0}

    def script(alias):
        i = min(calls["n"], len(per_call) - 1)
        calls["n"] += 1
        return per_call[i]

    rt.client.tool_script = script


def _writer_calls(rt):
    return [c for c in rt.client.calls if c.schema is None]


def _revise(tmp_path, identities, config, per_call, *, draft=DROPPED, **state):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    rt.client.report_fn = lambda _n: draft
    _script(rt, per_call)
    base = {"question": "q?", "round": 1, "report": PREVIOUS, "read_log": dict(BOTH_READ)}
    return rt, _generate({**base, **state}, rt)


# -- part 1: a drop needs a read ------------------------------------------------------------


def test_a_zero_read_revision_that_drops_a_read_source_is_retried_with_the_nudge(
    tmp_path, identities, config
):
    rt, out = _revise(tmp_path, identities, config, [[], READ])
    calls = _writer_calls(rt)
    assert len(calls) == 2
    assert calls[0].alias == calls[1].alias
    assert prompts.WRITER_DROP_NUDGE not in calls[0].user
    assert calls[1].user.endswith(prompts.WRITER_DROP_NUDGE)
    assert prompts.WRITER_UNREAD_NUDGE not in calls[1].user
    assert [e["failure_class"] for e in _events(rt, "generate_failed")] == ["unread_source_drop"]
    gen = _events(rt, "generate")[-1]
    assert gen["drop_retry"] == 1 and gen["drop_accepted"] == 0
    assert gen["unread_retry"] == 0 and gen["unread_accepted"] == 0
    assert out["author_alias"] == calls[1].alias


def test_a_revision_that_read_a_page_may_drop_a_source(tmp_path, identities, config):
    rt, _ = _revise(tmp_path, identities, config, [READ])
    assert len(_writer_calls(rt)) == 1
    assert not _events(rt, "generate_failed")
    gen = _events(rt, "generate")[-1]
    assert gen["drop_retry"] == 0 and gen["drop_accepted"] == 0


def test_a_first_draft_is_never_held_to_it(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    rt.client.report_fn = lambda _n: DROPPED
    _script(rt, [[]])
    _generate({"question": "q?", "round": 0, "read_log": dict(BOTH_READ)}, rt)
    assert len(_writer_calls(rt)) == 1
    assert _events(rt, "generate")[-1]["drop_retry"] == 0


def test_a_polish_pass_is_never_held_to_it(tmp_path, identities, config):
    rt, _ = _revise(tmp_path, identities, config, [[]], polish_next=True)
    assert len(_writer_calls(rt)) == 1
    assert not _events(rt, "generate_failed")


def test_a_retry_that_still_does_not_read_rotates_to_the_next_writer(tmp_path, identities, config):
    rt, out = _revise(tmp_path, identities, config, [[], [], READ])
    aliases = [c.alias for c in _writer_calls(rt)]
    assert aliases[0] == aliases[1] != aliases[2]
    assert out["author_alias"] == aliases[2]
    assert [e["failure_class"] for e in _events(rt, "generate_failed")] == ["unread_source_drop"] * 2
    assert _events(rt, "generate")[-1]["drop_accepted"] == 0


def test_no_writer_reading_ships_the_drop_with_a_warning_and_never_deadlocks(
    tmp_path, identities, config
):
    rt, out = _revise(tmp_path, identities, config, [[]])
    assert out["report"] == DROPPED.strip() and not out.get("fatal")
    gen = _events(rt, "generate")[-1]
    assert gen["drop_accepted"] == 1 and gen["drop_retry"] >= 1
    assert gen["unread_accepted"] == 0


def test_a_single_writer_pool_stops_after_its_one_nudged_retry(
    tmp_path, identities, config, monkeypatch
):
    monkeypatch.setattr(graph_mod.roles, "writer_pool", lambda roster: ["writer-a"])
    rt, out = _revise(tmp_path, identities, config, [[]])
    assert len(_writer_calls(rt)) == 2
    assert not out.get("fatal")
    assert _events(rt, "generate")[-1]["drop_accepted"] == 1


@pytest.mark.parametrize("outcome", ["not_found", "paywalled", None])
def test_dropping_a_source_nobody_read_in_full_is_free(tmp_path, identities, config, outcome):
    # A fabricated (404), unreadable or never-read citation: removing it is what its own
    # finding asks for, so the gate must not hold it back.
    log = {URL_A: "full_text"} | ({URL_B: outcome} if outcome else {})
    rt, _ = _revise(tmp_path, identities, config, [[]], read_log=log)
    assert len(_writer_calls(rt)) == 1
    assert not _events(rt, "generate_failed")


def test_a_revision_that_keeps_every_read_source_needs_no_read(tmp_path, identities, config):
    rt, _ = _revise(tmp_path, identities, config, [[]], draft=PREVIOUS)
    assert len(_writer_calls(rt)) == 1


def test_the_knob_off_restores_the_earlier_behaviour(tmp_path, identities, config):
    config.review.drop_requires_read = False
    rt, out = _revise(tmp_path, identities, config, [[]])
    assert len(_writer_calls(rt)) == 1
    gen = _events(rt, "generate")[-1]
    assert "drop_retry" not in gen and "drop_accepted" not in gen
    assert not _events(rt, "generate_failed")
    assert out["report"] == DROPPED.strip()


@pytest.mark.parametrize("read,verify", [(True, False), (False, True)])
def test_reading_or_verifying_off_leaves_the_gate_out(tmp_path, identities, config, read, verify):
    rt, _ = _runtime(tmp_path, identities, config, read=read, verify=verify)
    rt.client.report_fn = lambda _n: DROPPED
    _script(rt, [[]])
    _generate({"question": "q?", "round": 1, "report": PREVIOUS, "read_log": dict(BOTH_READ)}, rt)
    assert len(_writer_calls(rt)) == 1
    gen = _events(rt, "generate")[-1]
    assert "drop_retry" not in gen and "cited_read_peak" not in gen


def test_ops_mode_checks_the_spliced_draft(tmp_path, identities, config):
    config = config.model_copy(update={"revision": RevisionConfig(mode="ops")})
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    reply = "@@ replace S2.P2 tasks=T1\nThe freezing point paragraph is left unsourced.\n@@ end\n"
    rt.client.report_fn = lambda _n: reply
    _script(rt, [[], READ])
    log = {"https://example.org/boil": "full_text", "https://example.org/freeze": "full_text"}
    _generate({"question": "q?", "round": 1, "report": OPS_PREVIOUS, "read_log": log}, rt)
    assert len(_writer_calls(rt)) == 2
    assert _events(rt, "generate")[-1]["drop_retry"] == 1


def test_the_nudge_is_fixed_text_with_no_run_material():
    assert "{" not in prompts.WRITER_DROP_NUDGE
    assert "read_source" in prompts.WRITER_DROP_NUDGE


# -- counting -------------------------------------------------------------------------------


def test_only_cited_sources_read_in_full_text_count():
    log = {URL_A: "full_text", URL_B: "not_found"}
    assert read_cited_sources(PREVIOUS, None, log) == {URL_A}
    assert read_cited_sources(PREVIOUS, None, {}) == set()
    assert dropped_read_sources(PREVIOUS, DROPPED, BOTH_READ) == 1
    assert dropped_read_sources(PREVIOUS, DROPPED, log) == 0
    assert dropped_read_sources(None, DROPPED, BOTH_READ) == 0


def test_a_trailing_punctuation_variant_still_counts_as_read():
    assert read_cited_sources(PREVIOUS, None, {URL_A + ".": "full_text"}) == {URL_A}


# -- part 2: the net-loss finding -----------------------------------------------------------


def _report(n: int) -> str:
    """A report citing `n` distinct sources, all of them read."""
    body = " ".join(f"Finding number {i} holds [{i}]." for i in range(1, n + 1)) or "No finding."
    entries = "\n\n".join(f"[{i}] Paper {i}. https://example.org/p{i}" for i in range(1, n + 1))
    return f"## Findings\n\n{body}\n\n## Sources\n\n{entries}\n"


def _log(n: int) -> dict[str, str]:
    return {f"https://example.org/p{i}": "full_text" for i in range(1, n + 1)}


def _mint(current: int, peak: int | None, fraction: float = 0.5):
    text = _report(current)
    return mechanical_source_loss_issues(text, report_mod.parse(text), _log(8), peak, fraction=fraction)


@pytest.mark.parametrize(
    "peak,fraction,floor",
    [(6, 0.5, 4), (4, 0.5, 3), (3, 0.5, 2), (2, 0.5, 2), (1, 0.5, 1), (10, 0.3, 8), (5, 1.0, 1), (0, 0.5, 0)],
)
def test_the_floor_is_one_above_the_smallest_firing_loss(peak, fraction, floor):
    assert source_loss_floor(peak, fraction) == floor


def test_the_finding_fires_once_half_the_peak_is_lost_and_not_before():
    assert len(_mint(3, 6)) == 1
    assert _mint(4, 6) == []
    assert _mint(6, 6) == []
    assert len(_mint(0, 1)) == 1


def test_no_peak_mints_nothing():
    assert _mint(0, None) == []
    assert _mint(0, 0) == []


def test_the_finding_is_one_blocking_incomplete_answer_that_validates():
    text = _report(2)
    [issue] = mechanical_source_loss_issues(text, report_mod.parse(text), _log(8), 6)
    assert issue.category is Category.INCOMPLETE_ANSWER
    assert issue.severity is Severity.BLOCKING
    assert clamp_to_floor(issue.category, issue.severity) is Severity.BLOCKING
    validate_issue(Lens.COMPLETENESS, issue, report_mod.parse(text))
    assert "2 source(s)" in issue.rationale and "cited 6" in issue.rationale
    assert "at least 4" in issue.instruction


def test_the_finding_carries_counts_and_fixed_wording_only():
    text = _report(1)
    [issue] = mechanical_source_loss_issues(text, report_mod.parse(text), _log(8), 4)
    words = issue.rationale + issue.instruction
    assert "example.org" not in words and "Paper" not in words and "Finding number" not in words
    # Two different reports at the same counts produce the same rationale and instruction.
    other = _report(1).replace("Finding number 1 holds", "Another sentence entirely says")
    [twin] = mechanical_source_loss_issues(other, report_mod.parse(other), _log(8), 4)
    assert (twin.rationale, twin.instruction) == (issue.rationale, issue.instruction)


def test_an_unread_or_failed_source_does_not_count_toward_the_current_draft():
    text = _report(4)
    log = _log(2) | {"https://example.org/p3": "not_found"}
    assert len(mechanical_source_loss_issues(text, report_mod.parse(text), log, 4)) == 1


def test_latest_unblocked_ships_the_fuller_draft():
    # Round 1 cites six read sources with two majors; round 2 has shed half of them and with
    # them every major. Before this decision both rows had zero blocking and round 2 shipped.
    scores = [(0, 2, 0), (len(_mint(3, 6)), 0, 0)]
    assert select_shipped_index(scores, "latest_unblocked") == 0


def test_the_fraction_is_bounded():
    with pytest.raises((ConfigError, ValueError)):
        ReviewConfig(source_loss_fraction=0.0)
    with pytest.raises((ConfigError, ValueError)):
        ReviewConfig(source_loss_fraction=1.5)


# -- peak tracking --------------------------------------------------------------------------


def _first(tmp_path, identities, config, draft, read_log, **state):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    rt.client.report_fn = lambda _n: draft
    _script(rt, [READ])
    return rt, _generate({"question": "q?", "round": 0, "read_log": read_log, **state}, rt)


def test_generate_raises_the_peak_and_never_lowers_it(tmp_path, identities, config):
    rt, out = _first(tmp_path, identities, config, _report(3), _log(3))
    assert out["cited_read_peak"] == 3
    gen = _events(rt, "generate")[-1]
    assert gen["cited_read"] == 3 and gen["cited_read_peak"] == 3

    rt2, out2 = _first(tmp_path / "2", identities, config, _report(1), _log(3), cited_read_peak=5)
    assert out2["cited_read_peak"] == 5
    assert _events(rt2, "generate")[-1]["cited_read"] == 1


def test_a_state_from_before_the_field_restarts_the_peak_at_the_next_draft(tmp_path, identities, config):
    rt, out = _first(tmp_path, identities, config, _report(2), _log(2), report=_report(2), round=3)
    assert out["cited_read_peak"] == 2


def test_peak_tracks_across_a_run_of_drafts(tmp_path, identities, config):
    state: dict = {"read_log": _log(6)}
    peaks = []
    for i, n in enumerate([6, 5, 3, 4]):
        rt, out = _first(tmp_path / str(i), identities, config, _report(n), state["read_log"], **{
            k: v for k, v in state.items() if k != "read_log"
        })
        state["cited_read_peak"] = out["cited_read_peak"]
        peaks.append(out["cited_read_peak"])
    assert peaks == [6, 6, 6, 6]


def test_with_reading_off_no_peak_is_carried(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=False, verify=True)
    rt.client.report_fn = lambda _n: _report(2)
    out = _generate({"question": "q?", "round": 0}, rt)
    assert "cited_read_peak" not in out


# -- wiring into the completeness lens ------------------------------------------------------


def _review(rt, lens, text, *, peak, read_log=None):
    return _critique_one(
        rt,
        lens,
        f"{lens.value}-spec",
        "q?",
        text,
        "h" * 64,
        "vendor-a/model-a",
        1,
        read_log=_log(8) if read_log is None else read_log,
        cited_read_peak=peak,
    )


def test_the_completeness_lens_mints_it_and_no_other_lens_does(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    [issue] = _review(rt, Lens.COMPLETENESS, _report(2), peak=6).issues
    assert issue.category is Category.INCOMPLETE_ANSWER and issue.severity is Severity.BLOCKING
    assert _review(rt, Lens.COMPLETENESS, _report(5), peak=6).issues == []
    assert _review(rt, Lens.LOGIC, _report(2), peak=6).issues == []


def test_a_resumed_run_without_a_peak_mints_nothing(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    assert _review(rt, Lens.COMPLETENESS, _report(0), peak=None).issues == []


def test_a_spent_read_budget_suppresses_it(tmp_path, identities, config):
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    rt.reader.budget.take_chars(10_000)
    assert rt.reader.budget.exhausted
    assert _review(rt, Lens.COMPLETENESS, _report(2), peak=6).issues == []


def test_the_finding_knob_off_restores_the_earlier_behaviour(tmp_path, identities, config):
    config.review.source_loss_finding = False
    rt, _ = _runtime(tmp_path, identities, config, read=True, verify=True)
    assert _review(rt, Lens.COMPLETENESS, _report(2), peak=6).issues == []


@pytest.mark.parametrize("read,verify", [(True, False), (False, True)])
def test_reading_or_verifying_off_leaves_the_finding_out(tmp_path, identities, config, read, verify):
    rt, _ = _runtime(tmp_path, identities, config, read=read, verify=verify)
    assert _review(rt, Lens.COMPLETENESS, _report(2), peak=6).issues == []
