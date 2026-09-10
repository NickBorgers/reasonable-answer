"""A provider account that cannot pay defers the run instead of aborting it
(D-credit-exhaustion-defers).

The production shape (2026-09-09): `run-81212fcbf68f` reached round 5, OpenRouter began
answering 402 for every alias on the account, each writer spent three attempts inside five
seconds, and rule 1 recorded `aborted` — a verdict about a report nobody had judged
unfixable, over a balance someone topped up two hours later.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fakes import FakeClient

from reasonable_answer.graph import (
    DEFERRAL_CODES,
    REFUSAL_CODES,
    ProviderAccountExhausted,
    Runtime,
    _critique,
    run,
)
from reasonable_answer.llm import ProviderAccountError
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.web.registry import Registry
from reasonable_answer.web.worker import RunWorker

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""


def _refusal(alias: str = "writer-a") -> ProviderAccountError:
    return ProviderAccountError(
        f"{alias}: Error code: 402 - This request would exceed your available credits"
    )


class RefusingWriterClient(FakeClient):
    """Writers raise `error` while it is set; critics answer clean."""

    error: Exception | None = None

    def complete(self, alias, *, system, user, **kwargs):
        if self.error is not None and "YOUR DIMENSION" not in user:
            raise self.error
        return super().complete(alias, system=system, user=user, **kwargs)


def _events(run_dir) -> list[dict]:
    path = Path(run_dir) / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _clean(_alias, _user) -> CritiqueOutput:
    return CritiqueOutput(issues=[])


# ------------------------------------------------------------------ writers


def test_a_writer_walk_the_account_refused_defers_instead_of_aborting(identities, config):
    client = RefusingWriterClient(identities=identities, critique_fn=_clean, report_fn=lambda n: REPORT)
    client.error = _refusal()

    with pytest.raises(ProviderAccountExhausted) as caught:
        run(config, question="Is it so?", run_id="run-broke", client=client)

    assert caught.value.code == "provider_account"
    events = _events(Path(config.runs_dir) / "run-broke")
    kinds = [e["kind"] for e in events]
    # Every attempt is still on the record, with the class that makes it countable...
    assert {e["failure_class"] for e in events if e["kind"] == "generate_failed"} == {"http_402"}
    # ...and nothing claims the controller reached a verdict.
    assert "control" not in kinds
    assert "finalize" not in kinds
    assert not (Path(config.runs_dir) / "run-broke" / "final.json").exists()


def test_a_deferred_run_finishes_once_the_account_can_pay(identities, config):
    client = RefusingWriterClient(identities=identities, critique_fn=_clean, report_fn=lambda n: REPORT)
    client.error = _refusal()
    with pytest.raises(ProviderAccountExhausted):
        run(config, question="Is it so?", run_id="run-topped-up", client=client)

    client.error = None
    final = run(config, question="Is it so?", run_id="run-topped-up", client=client,
                stop=threading.Event())

    assert final["terminal_status"] in ("accepted", "converged_unconfirmed")
    kinds = [e["kind"] for e in _events(final["run_dir"])]
    assert "resume" in kinds
    assert kinds.count("intake") == 1


# ------------------------------------------------------------------ critics


def test_a_critique_pass_the_account_refused_defers_before_recording_anything(identities, config):
    """Without this a refusing account fails a lens in seconds, rule 2 re-asks a model on the
    same account, and rule 3 aborts twelve attempts later. The pass is re-run whole on resume,
    so none of it may reach the audit trail first."""
    def refused(alias, _user):
        raise _refusal(alias)

    client = FakeClient(identities=identities, critique_fn=refused, report_fn=lambda n: REPORT)

    with pytest.raises(ProviderAccountExhausted):
        run(config, question="Is it so?", seed=REPORT, run_id="run-broke-critic", client=client)

    kinds = [e["kind"] for e in _events(Path(config.runs_dir) / "run-broke-critic")]
    assert "critique" not in kinds
    assert "control" not in kinds


def test_a_lens_another_critic_reviewed_does_not_defer(identities, config, tmp_path):
    """At depth 2 the other critic may bill a different account; that lens is as reviewed as
    any other depth shortfall, and the run goes on."""
    def only_logic_spec_refused(alias, _user):
        if alias == "logic-spec":
            raise _refusal(alias)
        return CritiqueOutput(issues=[])

    client = FakeClient(identities=identities, critique_fn=only_logic_spec_refused,
                        report_fn=lambda n: REPORT)
    rt = Runtime(config=config, client=client, identities=identities,
                 store=RunStore(tmp_path, "run-half-broke"))
    state = {
        "question": "Is it so?",
        "report": REPORT,
        "artifact_hash": "h" * 64,
        "author_identity": "external/seed",
        "pending_lenses": ["logic"],
        "run_date": "2026-09-10",
    }

    out = _critique(state, rt)

    results = out["lens_results"]["logic"]
    assert [r["failure_class"] for r in results if r["failed"]] == ["http_402"]
    assert any(not r["failed"] for r in results)
    # An account refusal is not evidence about the alias.
    assert out["critic_strikes"].get("logic-spec", 0) == 0


# ------------------------------------------------------------------ the worker


def _queued_run(config, run_id: str) -> None:
    store = RunStore(config.runs_dir, run_id)
    store.question("Was it interrupted?")
    store.owner("viewer@example.com")
    store.event("queued", attempt=1, auto=False)
    store.event("intake", path="question")
    store.event("generate", author="writer-a", round=1)


def _broke(cfg, *, question, seed, run_id, stop=None, **_):
    raise ProviderAccountExhausted(
        f"run '{run_id}': the provider account refused the only critics of logic "
        "(openrouter says: This request would exceed your available credits)",
        run_id,
    )


def _drain(worker, run_id: str) -> None:
    deadline = time.time() + 5
    while worker.status(run_id) and time.time() < deadline:
        time.sleep(0.05)


def test_the_worker_defers_an_account_refusal_with_a_closed_code(config):
    _queued_run(config, "run-deferred-402")
    worker = RunWorker(config, max_concurrent=1, runner=_broke)
    try:
        worker.recover(Registry(config.runs_dir))
        _drain(worker, "run-deferred-402")

        registry = Registry(config.runs_dir)
        summary = registry.summary("run-deferred-402")
        assert summary.status == "interrupted"
        assert "out of credit" in summary.terminal_note
        deferred = [e for e in registry.events("run-deferred-402") if e["kind"] == "deferred"]
        assert [e["reason"] for e in deferred] == ["provider_account"]
        # The provider's wording stays in the container log (D-id-as-credential).
        assert not any("credits" in json.dumps(e) for e in deferred)
        # It cancels its own resume attempt, like a startup deferral.
        assert registry.consecutive_auto_resumes("run-deferred-402") == 0
        assert registry.consecutive_deferrals("run-deferred-402") == 1
    finally:
        worker.shutdown(timeout=1.0)


def test_the_deferral_vocabulary_is_closed_and_extends_the_startup_one():
    assert set(REFUSAL_CODES) < set(DEFERRAL_CODES)
    assert ProviderAccountExhausted.code in DEFERRAL_CODES
