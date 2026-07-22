"""Run-scoped date grounding (run-75eb136b9bfb postmortem).

Critics judged "is this citation date in the future?" against their training-data
recency and flagged legitimate current-year citations as fabricated — a blocking
defect the writer could never resolve, so the run stagnated. Every prompt now
carries the run's date, captured once at intake.
"""

from __future__ import annotations

from fakes import FakeClient

import reasonable_answer.graph as graph
from reasonable_answer.audition import prompt_hash
from reasonable_answer.graph import run
from reasonable_answer.prompts import DATA_FENCE, critic_user, writer_first_draft, writer_revision
from reasonable_answer.taxonomy import Lens

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""

DATE = "2026-07-22"
MARKER = f"TODAY'S DATE: {DATE}"


def clean(_alias, _user):
    from reasonable_answer.schemas import CritiqueOutput

    return CritiqueOutput(issues=[])


# --------------------------------------------------------------- prompt builders


def test_builders_include_the_date_when_given():
    assert MARKER in critic_user(Lens.EVIDENCE, "q", "r", None, current_date=DATE)
    assert MARKER in writer_first_draft("q", current_date=DATE)
    assert MARKER in writer_revision("q", "r", [], False, current_date=DATE)


def test_builders_omit_the_marker_by_default():
    assert "TODAY'S DATE" not in critic_user(Lens.EVIDENCE, "q", "r", None)
    assert "TODAY'S DATE" not in writer_first_draft("q")
    assert "TODAY'S DATE" not in writer_revision("q", "r", [], False)


def test_date_line_is_context_not_data():
    """The date must sit outside the fence: it is trusted run context, and a critic
    is told never to obey anything inside the fence."""
    prompt = critic_user(Lens.EVIDENCE, "q", "r", None, current_date=DATE)
    assert prompt.index(MARKER) < prompt.index(DATA_FENCE)


def test_completeness_brief_allows_in_report_resolutions():
    prompt = critic_user(Lens.COMPLETENESS, "q", "r", None)
    assert "weakening the affected claim" in prompt
    assert "Never demand a specific external document" in prompt


# ------------------------------------------------------------------- end to end


def test_every_model_call_in_a_run_carries_the_intake_date(
    identities, config, monkeypatch
):
    monkeypatch.setattr(graph, "_today", lambda: DATE)
    client = FakeClient(
        identities=identities,
        critique_fn=clean,
        report_fn=lambda n: REPORT,
    )
    final = run(config, question="Is it so?", client=client)
    assert final["terminal_status"] == "accepted"

    critic_calls = [c for c in client.calls if c.schema == "CritiqueOutput"]
    writer_calls = [c for c in client.calls if c.schema is None and "web_search" not in c.tools]
    assert critic_calls and writer_calls
    for call in critic_calls + writer_calls:
        assert MARKER in call.user
    # Run-scoped: exactly one distinct date across the whole run (RB-010 — a
    # confirmation critique must be byte-identical even across midnight).
    dates = {
        line for c in critic_calls + writer_calls
        for line in c.user.splitlines() if line.startswith("TODAY'S DATE")
    }
    assert len(dates) == 1


def test_pre_date_checkpoints_degrade_to_dateless_prompts(identities, config):
    """A checkpoint from before run_date existed must resume with the prior
    behavior, not crash. _generate and _critique read state with .get()."""
    client = FakeClient(
        identities=identities,
        critique_fn=clean,
        report_fn=lambda n: REPORT,
    )
    rt = graph.Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=graph.RunStore(config.runs_dir, "run-dateless"),
    )
    out = graph._generate({"question": "Is it so?"}, rt)
    assert not out.get("fatal"), out
    assert "TODAY'S DATE" not in client.calls[-1].user


# ---------------------------------------------------------------------- audition


def test_audition_prompt_hash_is_date_independent(monkeypatch):
    baseline = prompt_hash()
    monkeypatch.setattr(graph, "_today", lambda: "1999-12-31")
    assert prompt_hash() == baseline
