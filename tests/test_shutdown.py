"""Surviving a redeploy.

The process is continuously deployed: SIGTERM, a grace window, then SIGKILL, none of
it under its own control. These tests cover the two halves of surviving that — stopping
somewhere resumable, and picking the work back up on the next boot.
"""

from __future__ import annotations

import threading
import time

import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from reasonable_answer import shutdown
from reasonable_answer.graph import GracefulStop
from reasonable_answer.graph import run as run_graph
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore
from reasonable_answer.web.app import create_app
from reasonable_answer.web.registry import Registry
from reasonable_answer.web.worker import RunWorker

REPORT = """# Answer

A claim that is fully supported [1].

## Sources

[1] A real-looking source.
"""


@pytest.fixture
def fake_client(identities):
    return FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
    )


def _interrupted_run(config, run_id: str, question: str = "Was it interrupted?") -> RunStore:
    """A run dir that looks like the process died partway through it."""
    store = RunStore(config.runs_dir, run_id)
    store.question(question)
    store.event("queued", attempt=1, auto=False)
    store.event("intake", path="question")
    store.event("generate", author="writer-a", round=1)
    return store


# ------------------------------------------------------------ stopping cleanly


def test_a_stop_event_halts_the_graph_at_a_node_boundary(config, fake_client):
    """The run stops, says where, and leaves a checkpoint — it does not die mid-node."""
    stop = threading.Event()
    stop.set()

    with pytest.raises(GracefulStop) as raised:
        run_graph(config, question="Is it so?", run_id="run-halt", client=fake_client,
                  stop=stop)

    assert raised.value.run_id == "run-halt"
    events = list(Registry(config.runs_dir).events("run-halt"))
    assert events[-1]["kind"] == "pause"
    assert events[-1]["reason"] == "shutdown"
    assert (config.runs_dir / "run-halt" / "state.sqlite").exists()


def test_a_paused_run_finishes_when_it_is_resumed(config, fake_client):
    """The point of pausing rather than crashing: the next process completes the run."""
    stop = threading.Event()
    stop.set()
    with pytest.raises(GracefulStop):
        run_graph(config, question="Is it so?", run_id="run-cycle", client=fake_client, stop=stop)

    paused_at = len(list(Registry(config.runs_dir).events("run-cycle")))

    final = run_graph(config, question="Is it so?", run_id="run-cycle", client=fake_client,
                      stop=threading.Event())
    assert final["terminal_status"] in ("accepted", "converged_unconfirmed")

    kinds = [e["kind"] for e in Registry(config.runs_dir).events("run-cycle")]
    # `startup` is re-announced by every process; `resume` is the one that says the
    # graph continued from the checkpoint rather than starting the run over.
    assert "resume" in kinds[paused_at:]
    assert kinds.count("intake") == 1
    assert kinds.count("finalize") == 1


class _StopsAfter:
    """A stop flag that trips once some nodes have actually run.

    Setting the flag up front stops the graph at the first yield, before any node has
    executed — which never exercises the case that matters: work completed, checkpoint
    written, process leaving. This trips mid-run instead.
    """

    def __init__(self, nodes: int) -> None:
        self._remaining = nodes

    def is_set(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


def test_a_pause_after_real_work_keeps_that_work(config, fake_client):
    """The case the whole design turns on: nodes ran, we stopped, and the resume starts
    from where they left off rather than redoing them.
    """
    with pytest.raises(GracefulStop):
        run_graph(config, question="Is it so?", run_id="run-midway", client=fake_client,
                  stop=_StopsAfter(2))

    registry = Registry(config.runs_dir)
    kinds = [e["kind"] for e in registry.events("run-midway")]
    assert "intake" in kinds  # real work happened before the pause
    assert kinds[-1] == "pause"

    calls_before_resume = len(fake_client.calls)
    final = run_graph(config, question="Is it so?", run_id="run-midway", client=fake_client,
                      stop=threading.Event())

    assert final["terminal_status"] in ("accepted", "converged_unconfirmed")
    kinds = [e["kind"] for e in registry.events("run-midway")]
    assert kinds.count("intake") == 1, "intake re-ran: the checkpoint was lost"
    assert kinds.count("finalize") == 1
    assert len(fake_client.calls) > calls_before_resume  # it continued, not replayed


def test_a_run_with_no_stop_event_is_unaffected(config, fake_client):
    """The stop plumbing is optional everywhere; omitting it must change nothing."""
    final = run_graph(config, question="Is it so?", run_id="run-plain", client=fake_client)
    assert final["terminal_status"] in ("accepted", "converged_unconfirmed")


# ----------------------------------------------------------------- the worker


def test_shutdown_returns_while_a_job_is_still_running(config):
    """The old shutdown pushed a sentinel that a busy thread could not see for minutes,
    waited out its timeout, and abandoned the thread mid-node."""
    entered = threading.Event()

    def watches_the_flag(cfg, *, question, seed, run_id, stop=None):
        entered.set()
        stop.wait(timeout=10)  # stands in for a node that notices the boundary

    worker = RunWorker(config, max_concurrent=1, runner=watches_the_flag)
    worker.submit("Long one?")
    assert entered.wait(timeout=5)

    started = time.monotonic()
    worker.shutdown(timeout=5.0)
    assert time.monotonic() - started < 5.0
    assert not any(t.is_alive() for t in worker._threads)


def test_shutdown_leaves_queued_work_on_disk_for_the_next_process(config):
    """Anything still in the queue is owed, not lost: it is already an event on disk."""
    # Threads are non-daemon now, so a runner that outlives the assertions would leave a
    # live thread behind: it watches the stop flag instead of sleeping through it.
    def waits_for_the_flag(cfg, *, question, seed, run_id, stop=None):
        stop.wait(timeout=10)

    worker = RunWorker(config, max_concurrent=1, runner=waits_for_the_flag)
    try:
        worker.submit("First?")
        queued = worker.submit("Second?")
    finally:
        worker.shutdown(timeout=2.0)

    summary = Registry(config.runs_dir).summary(queued)
    assert summary.status == "queued"
    assert summary.question == "Second?"


def test_a_graceful_stop_is_not_logged_as_a_crash(config, caplog):
    def pauses(cfg, *, question, seed, run_id, stop=None):
        raise GracefulStop("paused", run_id)

    worker = RunWorker(config, max_concurrent=1, runner=pauses)
    try:
        run_id = worker.submit("Paused?")
        deadline = time.time() + 5
        while worker.status(run_id) and time.time() < deadline:
            time.sleep(0.05)
        assert "crashed" not in caplog.text
    finally:
        worker.shutdown(timeout=1.0)


# -------------------------------------------------------------------- recovery


def test_boot_recovery_re_enqueues_an_interrupted_run(config):
    seen: list[str] = []

    def recording(cfg, *, question, seed, run_id, stop=None):
        seen.append(run_id)

    _interrupted_run(config, "run-orphan")
    worker = RunWorker(config, max_concurrent=1, runner=recording)
    try:
        assert worker.recover(Registry(config.runs_dir)) == ["run-orphan"]
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen == ["run-orphan"]
    finally:
        worker.shutdown(timeout=1.0)


def test_boot_recovery_skips_runs_that_already_finished(config):
    store = RunStore(config.runs_dir, "run-done")
    store.question("Finished?")
    store.event("intake", path="question")
    store.final("# done", {"terminal_status": "accepted", "note": ""})

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        assert worker.recover(Registry(config.runs_dir)) == []
    finally:
        worker.shutdown(timeout=1.0)


def test_boot_recovery_can_be_switched_off(config, monkeypatch):
    """An operator watching a crash loop needs a way to stop feeding it."""
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "0")
    _interrupted_run(config, "run-parked")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        assert worker.recover(Registry(config.runs_dir)) == []
        assert Registry(config.runs_dir).summary("run-parked").status == "interrupted"
    finally:
        worker.shutdown(timeout=1.0)


def test_a_run_that_never_progresses_is_abandoned_rather_than_retried_forever(config):
    """A deterministically-failing run would otherwise be picked up by every restart."""
    store = _interrupted_run(config, "run-doomed")
    for n in range(config.max_resume_attempts):
        store.event("queued", attempt=n + 1, auto=True)

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        assert worker.recover(Registry(config.runs_dir)) == []
        summary = Registry(config.runs_dir).summary("run-doomed")
        assert summary.status == "abandoned"
        assert "resume attempt cap" in summary.terminal_note
    finally:
        worker.shutdown(timeout=1.0)


def test_progress_resets_the_attempt_budget(config):
    """Counting consecutive failures, not total ones: a restart storm that never gets
    to run anything must not spend the budget a genuinely-progressing run needs."""
    store = _interrupted_run(config, "run-progressing")
    registry = Registry(config.runs_dir)

    for n in range(5):
        store.event("queued", attempt=n + 1, auto=True)
    assert registry.consecutive_auto_resumes("run-progressing") == 5

    store.event("control", rule=3, action="generate", round=2)
    assert registry.consecutive_auto_resumes("run-progressing") == 0


def test_a_human_can_resume_a_run_that_recovery_gave_up_on(config, monkeypatch):
    """The cap bounds automatic retries; it is not a verdict on the run."""
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "0")
    store = _interrupted_run(config, "run-revived")
    store.event("abandoned", reason="resume attempt cap", attempts=3)

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            assert c.post("/runs/run-revived/resume", follow_redirects=False).status_code == 303
    finally:
        worker.shutdown(timeout=1.0)


def test_an_abandoned_run_is_terminal_so_the_ui_stops_offering_recovery(config):
    from reasonable_answer.web.registry import TERMINAL_STATUSES

    assert "abandoned" in TERMINAL_STATUSES


def test_a_run_abandoned_at_the_cap_keeps_the_audit_trail_honest(config):
    """`final.json` means the controller reached a verdict. Giving up is not a verdict,
    so the run must not acquire one it never earned."""
    store = _interrupted_run(config, "run-honest")
    for n in range(config.max_resume_attempts):
        store.event("queued", attempt=n + 1, auto=True)

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        worker.recover(Registry(config.runs_dir))
        assert Registry(config.runs_dir).final("run-honest") is None
    finally:
        worker.shutdown(timeout=1.0)


def test_inputs_that_drifted_abandon_the_run_instead_of_looping(config):
    """A deploy that ships a new roster invalidates every in-flight fingerprint. The
    guard is right to refuse — but a refusal that stayed `interrupted` would be retried
    on every boot until it burned the whole cap."""
    from reasonable_answer.graph import ResumeMismatch

    def drifted(cfg, *, question, seed, run_id, stop=None):
        raise ResumeMismatch("roster changed")

    _interrupted_run(config, "run-drifted")
    worker = RunWorker(config, max_concurrent=1, runner=drifted)
    try:
        worker.recover(Registry(config.runs_dir))
        deadline = time.time() + 5
        while worker.status("run-drifted") and time.time() < deadline:
            time.sleep(0.05)
        assert Registry(config.runs_dir).summary("run-drifted").status == "abandoned"
    finally:
        worker.shutdown(timeout=1.0)


# ----------------------------------------------------------------- the budget


def test_the_grace_budget_comes_from_the_platform(monkeypatch):
    monkeypatch.setenv("RA_SHUTDOWN_GRACE_SECONDS", "45")
    assert shutdown.grace_seconds() == 45.0


@pytest.mark.parametrize("value", ["", "soon", "-1", "0"])
def test_an_unusable_grace_budget_falls_back_rather_than_crashing(monkeypatch, value):
    """Getting this wrong at boot should not take the service down."""
    monkeypatch.setenv("RA_SHUTDOWN_GRACE_SECONDS", value)
    assert shutdown.grace_seconds() == shutdown.DEFAULT_GRACE_SECONDS


def test_requesting_a_stop_is_visible_and_idempotent():
    assert not shutdown.stop_requested()
    shutdown.request_stop("test")
    shutdown.request_stop("test again")
    assert shutdown.stop_requested()


def test_a_real_signal_reaches_the_stop_flag():
    """The wiring the whole feature hangs off. In a container `ra` is PID 1, which has
    no default SIGTERM disposition — without a handler the signal is simply discarded."""
    import signal

    original = signal.getsignal(signal.SIGTERM)
    try:
        shutdown.install_handlers()
        assert not shutdown.stop_requested()
        signal.raise_signal(signal.SIGTERM)
        assert shutdown.stop_requested()
    finally:
        signal.signal(signal.SIGTERM, original)


# --------------------------------------------------------------------- the CLI


def test_a_paused_cli_run_exits_130_and_says_how_to_resume(config, monkeypatch, tmp_path):
    """`ra run` is the other entry point that takes a SIGTERM, and a pause is not a
    failure — it needs an exit code that says so and a way back in."""
    import yaml
    from typer.testing import CliRunner

    from reasonable_answer import cli

    roster = tmp_path / "roster.yaml"
    roster.write_text(yaml.safe_dump({"roster": config.roster.model_dump()}))

    def pauses(cfg, **kwargs):
        raise GracefulStop("paused at generate", "run-cli")

    monkeypatch.setattr(cli, "run_graph", pauses)
    result = CliRunner().invoke(cli.app, ["run", "-q", "Is it so?", "-c", str(roster)])

    assert result.exit_code == 130
    assert "run-cli" in result.output  # the resume hint names the run


# --------------------------------------------------------------------- the UI


def test_the_event_stream_lets_go_when_the_process_is_stopping(config):
    """uvicorn drains connections *before* running lifespan shutdown, so this generator
    is what decides whether one forgotten browser tab holds the entire grace period."""
    _interrupted_run(config, "run-watched")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: time.sleep(30))
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            shutdown.request_stop("test")
            started = time.monotonic()
            with c.stream("GET", "/runs/run-watched/stream") as response:
                assert list(response.iter_lines()) == []  # closed rather than held open
            assert time.monotonic() - started < 5.0
    finally:
        worker.shutdown(timeout=1.0)


def test_resuming_a_run_that_is_not_interrupted_is_a_conflict(config, monkeypatch):
    """The statuses that accept a resume are an allowlist; everything else is a 409."""
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "0")
    store = RunStore(config.runs_dir, "run-finished")
    store.question("Already done?")
    store.event("intake", path="question")
    store.final("# done", {"terminal_status": "accepted", "note": ""})

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            assert c.post("/runs/run-finished/resume").status_code == 409
    finally:
        worker.shutdown(timeout=1.0)


def test_a_paused_run_reads_as_interrupted_with_a_reason(config):
    """`pause` and a crash are both resumable, but only one of them was deliberate."""
    store = _interrupted_run(config, "run-paused")
    store.event("pause", reason="shutdown", next=["critique"])

    summary = Registry(config.runs_dir).summary("run-paused")
    assert summary.status == "interrupted"
    assert "resumes automatically" in summary.terminal_note


def test_the_resume_cap_can_be_set_from_the_environment(config, monkeypatch):
    monkeypatch.setenv("RA_MAX_RESUME_ATTEMPTS", "7")
    app = create_app(config, worker=RunWorker(config, runner=lambda *a, **k: None))
    try:
        assert app.state.config.max_resume_attempts == 7
    finally:
        app.state.worker.shutdown(timeout=1.0)
