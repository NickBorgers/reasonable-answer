"""D-role-call-timeouts: the writer and the critic each get their own call timeout.

One `budgets.timeout_seconds` cut writer drafts that were still generating on a slow host,
and made every critique pass wait the full default on a critic that had run away to its
output cap. These tests pin the split: which calls each override reaches, that nothing
else moves, and that a config without the section behaves exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeClient
from pydantic import ValidationError

from reasonable_answer.config import CallTimeouts, Config
from reasonable_answer.graph import _run_fingerprint, run
from reasonable_answer.llm import per_call_timeout
from reasonable_answer.schemas import CritiqueOutput

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""

#: Every schema the graph asks for that is neither a writer draft nor a critic's own call.
OTHER_SCHEMAS = {"OrchestratorRecommendation", "WriterDisputes", "ArbiterVerdict", "SupportManifest"}


def _client(identities) -> FakeClient:
    return FakeClient(
        identities=identities,
        critique_fn=lambda *_: CritiqueOutput(issues=[]),
        report_fn=lambda _: REPORT,
    )


def test_the_section_defaults_to_the_client_timeout_for_both_roles(config):
    assert config.call_timeouts == CallTimeouts(writer_seconds=None, critic_seconds=None)


@pytest.mark.parametrize("field", ["writer_seconds", "critic_seconds"])
@pytest.mark.parametrize("value", [0, -1, 7201])
def test_a_timeout_outside_its_bounds_is_refused(field, value):
    with pytest.raises(ValidationError):
        CallTimeouts(**{field: value})


def test_changing_a_role_timeout_never_changes_a_runs_identity(config):
    """A paused run must resume across the deploy that tunes these (the reason they are
    not under `budgets`)."""
    tuned = config.model_copy(
        update={"call_timeouts": CallTimeouts(writer_seconds=900, critic_seconds=180)}
    )
    assert _run_fingerprint(tuned, "q?", None) == _run_fingerprint(config, "q?", None)


def test_per_call_timeout_omits_the_key_when_unset():
    assert per_call_timeout(None) == {}
    assert per_call_timeout(180.0) == {"timeout": 180.0}


def test_the_writer_and_critic_calls_carry_their_own_timeouts(identities, config):
    tuned = config.model_copy(
        update={"call_timeouts": CallTimeouts(writer_seconds=900, critic_seconds=180)}
    )
    client = _client(identities)

    run(tuned, question="Is it so?", client=client)

    writer = [c for c in client.calls if c.schema is None]
    critic = [c for c in client.calls if c.schema == "CritiqueOutput"]
    other = [c for c in client.calls if c.schema in OTHER_SCHEMAS]
    assert writer and critic
    assert {c.timeout for c in writer} == {900}
    assert {c.timeout for c in critic} == {180}
    assert all(c.timeout is None for c in other), "every other call keeps the client default"


def test_without_the_section_no_call_gets_a_timeout_override(identities, config):
    client = _client(identities)

    run(config, question="Is it so?", client=client)

    assert client.calls
    assert all(c.timeout is None for c in client.calls)


def test_the_deployment_roster_gives_writers_longer_and_critics_shorter_than_the_default():
    deployment = Config.load(Path("config/roster.yaml"))
    default = deployment.budgets.timeout_seconds
    assert deployment.call_timeouts.writer_seconds is not None
    assert deployment.call_timeouts.critic_seconds is not None
    assert deployment.call_timeouts.writer_seconds > default > deployment.call_timeouts.critic_seconds


def test_the_default_roster_keeps_one_timeout():
    assert Config.load(Path("config/roster.default.yaml")).call_timeouts == CallTimeouts()
