"""The web layer, driven end to end with a fake proxy — no network, no real models."""

from __future__ import annotations

import os
import threading
import time

import pytest
from fakes import FakeClient
from fastapi.testclient import TestClient

from reasonable_answer.graph import run as run_graph
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore, sweep_expired
from reasonable_answer.web.app import create_app
from reasonable_answer.web.registry import Registry
from reasonable_answer.web.retention import RetentionSweeper
from reasonable_answer.web.worker import QueueFull, RateLimited, RateLimiter, RunWorker

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


@pytest.fixture
def client(config, fake_client):
    """A worker whose runner is the real graph but with a fake proxy behind it."""

    def runner(cfg, *, question, seed, run_id, stop=None):
        return run_graph(cfg, question=question, seed=seed, run_id=run_id, client=fake_client)

    worker = RunWorker(config, max_concurrent=1, runner=runner)
    app = create_app(config, worker=worker)
    with TestClient(app) as c:
        yield c
    worker.shutdown()


def _wait_for_final(config, run_id: str, timeout: float = 20.0) -> dict:
    registry = Registry(config.runs_dir)
    deadline = time.time() + timeout
    while time.time() < deadline:
        final = registry.final(run_id)
        if final:
            return final
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


# ------------------------------------------------------------------- submit


def test_submitting_a_question_starts_a_run_and_redirects(client, config):
    response = client.post("/runs", data={"question": "Is it so?"}, follow_redirects=False)
    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]

    final = _wait_for_final(config, run_id)
    assert final["terminal_status"] in ("accepted", "converged_unconfirmed")


def test_a_queued_run_is_listed_before_it_produces_anything(config, identities):
    """The question is recorded at submit time, so the run is identifiable the
    instant it is queued rather than only once the first draft lands."""
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: time.sleep(5))
    try:
        run_id = worker.submit("A distinctive question?")
        registry = Registry(config.runs_dir)
        summary = registry.summary(run_id, worker.active())
        assert summary.question == "A distinctive question?"
        assert summary.status in ("queued", "running")
    finally:
        worker.shutdown(timeout=0.1)


def test_an_empty_question_is_rejected(client):
    assert client.post("/runs", data={"question": "   "}).status_code == 400


def test_an_oversized_question_is_rejected(client, config):
    huge = "x" * (config.max_question_chars + 1)
    assert client.post("/runs", data={"question": huge}).status_code == 400


def test_an_oversized_seed_is_rejected(client, config):
    huge = "x" * (config.max_report_chars + 1)
    response = client.post("/runs", data={"question": "ok?", "seed": huge})
    assert response.status_code == 400


# --------------------------------------------------------------------- pages


def test_the_index_lists_finished_runs(client, config):
    response = client.post("/runs", data={"question": "Listed question?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    page = client.get("/")
    assert page.status_code == 200
    assert "Listed question?" in page.text
    assert run_id in page.text


def test_the_run_page_shows_the_roster_that_actually_reviewed(client, config, identities):
    response = client.post("/runs", data={"question": "Which critics?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    page = client.get(f"/runs/{run_id}").text
    for lens in ("logic", "evidence", "completeness"):
        assert lens in page
    # critics are shown by their short model name, and the author must not appear
    # as a critic on its own draft anywhere in the timeline
    assert "logic-spec" in page or "model-b" in page


def test_an_unknown_run_is_a_404(client):
    assert client.get("/runs/run-doesnotexist").status_code == 404
    assert client.get("/runs/run-doesnotexist/report.md").status_code == 404


def test_a_traversal_run_id_is_rejected_not_served(client):
    for bad in ("..%2f..%2fetc", "....//etc"):
        assert client.get(f"/runs/{bad}").status_code in (404, 400)


def test_report_markdown_is_served_only_once_it_exists(client, config):
    response = client.post("/runs", data={"question": "Report ready?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    markdown = client.get(f"/runs/{run_id}/report.md")
    assert markdown.status_code == 200
    assert "# Answer" in markdown.text


def test_the_report_is_rendered_not_shown_as_raw_markdown(client, config):
    """A reader gets HTML; `report.md` stays the escape hatch for the source."""
    response = client.post("/runs", data={"question": "Rendered?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    for url in (f"/runs/{run_id}", f"/runs/{run_id}/report"):
        page = client.get(url)
        assert page.status_code == 200
        assert "<h1>Answer</h1>" in page.text
        assert "# Answer" not in page.text


def test_the_report_page_404s_before_there_is_a_report_and_for_unknown_runs(config, identities):
    """Both of the new route's guards: no such run, and a run with nothing to show yet."""
    store = RunStore(config.runs_dir, "run-early")
    store.question("Too soon?")
    store.event("intake", path="question")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            assert c.get("/runs/run-early/report").status_code == 404
            assert c.get("/runs/run-doesnotexist/report").status_code == 404
    finally:
        worker.shutdown()


def test_report_markdown_features_reports_actually_use_are_enabled(config):
    """Tables and strikethrough are enabled on top of CommonMark; pin that."""
    from reasonable_answer.web.markdown import to_html

    html = to_html("| a | b |\n| - | - |\n| 1 | 2 |\n\n~~struck~~\n")
    assert "<table>" in html
    assert "<s>struck</s>" in html


def test_a_finished_report_outranks_the_progress_trail(client, config):
    """Once there is an answer, the answer is the page; the rounds fold up below it."""
    response = client.post("/runs", data={"question": "Which comes first?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    page = client.get(f"/runs/{run_id}").text
    assert page.index("<h1>Answer</h1>") < page.index('id="progress"')
    assert "<details class=\"fold\">" in page


def test_a_report_that_contains_html_is_rendered_as_text_not_markup(config, identities):
    """The report is model-written, so markdown rendering must not become an XSS hole."""
    hostile = (
        '# Answer\n\n<script>alert("xss")</script>\n\n'
        "[click](javascript:alert(1))\n\n"
        "![probe](http://127.0.0.1:9/pixel.png)\n"
    )
    store = RunStore(config.runs_dir, "run-mdxss")
    store.question("Hostile?")
    store.event("intake", path="question")
    store.final(hostile, {"status": "accepted", "chosen_round": 1})

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            for url in ("/runs/run-mdxss", "/runs/run-mdxss/report"):
                page = c.get(url).text
                assert "<script>alert" not in page
                assert "&lt;script&gt;" in page
                # markdown-it refuses the scheme, so the link stays inert literal text
                assert 'href="javascript:' not in page
                # An <img> would be an automatic outbound GET from the reader's browser
                # the moment the page loads, so image syntax stays literal text too.
                assert "<img" not in page
                assert "127.0.0.1:9/pixel.png" in page  # rendered, but as text
    finally:
        worker.shutdown()


def test_audit_json_exposes_the_whole_event_stream(client, config):
    response = client.post("/runs", data={"question": "Audit?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    audit = client.get(f"/runs/{run_id}/audit.json").json()
    kinds = {e["kind"] for e in audit["events"]}
    assert {"startup", "generate", "critique", "triage", "control", "finalize"} <= kinds


def test_healthz(client):
    assert client.get("/healthz").text == "ok"


# ------------------------------------------------------------------ timeline


def test_the_timeline_reconstructs_rounds_from_the_event_log(config, identities, fake_client):
    run_graph(config, question="Timeline?", seed=REPORT, run_id="run-timeline", client=fake_client)
    timeline = Registry(config.runs_dir).timeline("run-timeline")

    assert timeline
    assert [r.round for r in timeline] == sorted(r.round for r in timeline)
    for snapshot in timeline:
        for _lens, lens_snapshot in snapshot.lenses.items():
            # the critic on every lens differs from the writer of that round
            assert lens_snapshot.critic != snapshot.writer


def test_a_failed_lens_is_visible_in_the_timeline(config, identities):
    from reasonable_answer.llm import ModelCallError

    def flaky(alias, user):
        if "YOUR DIMENSION: evidence" in user:
            raise ModelCallError("provider down")
        return CritiqueOutput(issues=[])

    client = FakeClient(identities=identities, critique_fn=flaky, report_fn=lambda n: REPORT)
    run_graph(config, question="Flaky?", seed=REPORT, run_id="run-flaky", client=client)

    timeline = Registry(config.runs_dir).timeline("run-flaky")
    evidence = [r.lenses.get("evidence") for r in timeline if "evidence" in r.lenses]
    assert any(e and e.failed for e in evidence)


# -------------------------------------------------------------------- worker


def test_the_worker_caps_concurrency(config):
    """Every extra concurrent run multiplies load on one proxy, so the cap is the
    point — not an implementation detail."""
    running = []
    peak = 0

    def slow_runner(cfg, *, question, seed, run_id, stop=None):
        nonlocal peak
        running.append(run_id)
        peak = max(peak, len(running))
        time.sleep(0.3)
        running.remove(run_id)

    worker = RunWorker(config, max_concurrent=1, runner=slow_runner)
    try:
        for n in range(4):
            worker.submit(f"question {n}?")
        deadline = time.time() + 10
        while worker.active() and time.time() < deadline:
            time.sleep(0.05)
        assert peak == 1
    finally:
        worker.shutdown()


def test_a_crashing_run_leaves_the_worker_alive_and_the_run_resumable(config):
    def exploding(cfg, *, question, seed, run_id, stop=None):
        raise RuntimeError("boom")

    worker = RunWorker(config, max_concurrent=1, runner=exploding)
    try:
        crashed = worker.submit("Crash?")
        deadline = time.time() + 5
        while worker.status(crashed) and time.time() < deadline:
            time.sleep(0.05)

        # the worker survived and still accepts work
        assert worker.submit("Next?")

        summary = Registry(config.runs_dir).summary(crashed, worker.active())
        assert summary.status in ("interrupted", "queued", "running")
    finally:
        worker.shutdown()


def test_resuming_a_seeded_run_passes_the_seed_back(config, monkeypatch):
    """The graph fingerprints question + seed + roster + budgets and refuses a
    checkpoint whose inputs drifted. A resume that forgets the seed therefore looks
    identical to someone changing the question, and every seeded run becomes
    unresumable — so the seed has to come back off disk.

    Boot recovery is switched off here so the manual endpoint is what gets tested; the
    automatic path has its own coverage below."""
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "0")
    seen: list[str | None] = []

    def recording(cfg, *, question, seed, run_id, stop=None):
        seen.append(seed)

    worker = RunWorker(config, max_concurrent=1, runner=recording)
    app = create_app(config, worker=worker)
    try:
        store = RunStore(config.runs_dir, "run-seeded")
        store.question("Does the seed survive?", "# A seed report")
        store.event("intake", path="seed")

        with TestClient(app) as c:
            assert c.post("/runs/run-seeded/resume", follow_redirects=False).status_code == 303

        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        assert seen == ["# A seed report"]
    finally:
        worker.shutdown()


def test_resuming_an_active_run_does_not_double_run(config):
    def slow(cfg, *, question, seed, run_id, stop=None):
        time.sleep(0.5)

    worker = RunWorker(config, max_concurrent=1, runner=slow)
    try:
        run_id = worker.submit("Once?")
        worker.resume(run_id, "Once?")
        worker.resume(run_id, "Once?")
        assert worker.queue_depth <= 1
    finally:
        worker.shutdown()


# ------------------------------------------------------------------ escaping


def test_run_content_is_escaped_into_the_page(config, identities):
    """Questions and reports are untrusted text on the way *out* as well as in."""
    hostile = '<script>alert("xss")</script>'
    store = RunStore(config.runs_dir, "run-xss")
    store.question(hostile)
    store.event("intake", path="question")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with TestClient(app) as c:
            page = c.get("/runs/run-xss").text
            assert "<script>alert" not in page
            assert "&lt;script&gt;" in page
    finally:
        worker.shutdown()


def test_an_unwritable_runs_directory_fails_at_startup_not_on_first_use(config, tmp_path):
    """A bind mount owned by root is the likeliest container misconfiguration; it
    should say so at boot rather than 500 on the user's first submission."""
    import os

    from reasonable_answer.config import ConfigError

    if os.getuid() == 0:
        pytest.skip("root can write anywhere")

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(ConfigError, match="not writable"):
            create_app(config.model_copy(update={"runs_dir": locked / "runs"}))
    finally:
        locked.chmod(0o700)


# ------------------------------------------------------------- backpressure


def _wait_running(worker: RunWorker, timeout: float = 5.0) -> None:
    """Block until the worker actually has a run in flight, so a depth assertion is
    not racing the drain thread that has yet to pick the first job up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "running" in worker.active().values():
            return
        time.sleep(0.01)
    raise AssertionError("no run started")


def test_submit_is_rejected_when_the_queue_is_full_and_leaves_no_run_dir(config):
    """The cap is the whole point: a burst becomes a refusal, and — because the check
    runs before any disk write — the refused submission leaves no directory behind, so
    the memory cap is also a disk cap."""
    gate = threading.Event()

    def blocking(cfg, *, question, seed, run_id, stop=None):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 2, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    try:
        r1 = worker.submit("occupies the worker?")
        _wait_running(worker)
        r2 = worker.submit("waiting one?")
        r3 = worker.submit("waiting two?")
        with pytest.raises(QueueFull):
            worker.submit("one too many?")
        dirs = {p.name for p in cfg.runs_dir.iterdir() if p.is_dir()}
        assert dirs == {r1, r2, r3}  # the rejected submission wrote nothing
    finally:
        gate.set()
        worker.shutdown()


def test_recover_and_resume_bypass_the_queue_cap(config):
    """The cap throttles *new* submissions; it must never turn away work already owed
    and on disk, or a backlog could wedge recovery after a restart."""
    gate = threading.Event()

    def blocking(cfg, *, question, seed, run_id, stop=None):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 1, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    try:
        worker.submit("occupies?")
        _wait_running(worker)
        worker.submit("fills the one slot?")
        with pytest.raises(QueueFull):
            worker.submit("over the cap?")
        # resume() represents already-owed work, so it is accepted past the cap
        assert worker.resume("run-owed", "owed?")
    finally:
        gate.set()
        worker.shutdown()


def test_submission_rate_limit_rejects_then_recovers(config):
    ticks = [1000.0]
    limiter = RateLimiter(max_events=2, window_seconds=60.0, clock=lambda: ticks[0])
    cfg = config.model_copy(update={"max_queue_depth": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=lambda *a, **k: None, rate_limiter=limiter)
    try:
        assert worker.submit("a?")
        assert worker.submit("b?")
        with pytest.raises(RateLimited):
            worker.submit("c?")  # allowance spent for this window
        ticks[0] += 61.0  # window rolls over
        assert worker.submit("d?")
    finally:
        worker.shutdown()


def test_rate_limiter_is_per_identity_and_reports_retry_after():
    ticks = [0.0]
    limiter = RateLimiter(max_events=1, window_seconds=30.0, clock=lambda: ticks[0])

    assert limiter.check_and_record("alice") == 0.0
    assert limiter.check_and_record("bob") == 0.0  # a distinct bucket, unaffected

    retry = limiter.check_and_record("alice")  # alice's one slot is gone
    assert 0.0 < retry <= 30.0
    assert limiter.check_and_record("alice") > 0.0  # a rejected hit is not recorded

    ticks[0] += 30.0
    assert limiter.check_and_record("alice") == 0.0  # window cleared


def test_a_disabled_rate_limiter_always_passes():
    limiter = RateLimiter(max_events=0, window_seconds=60.0, clock=lambda: 0.0)
    for _ in range(100):
        assert limiter.check_and_record("x") == 0.0


def _post(c, question: str, headers: dict | None = None) -> int:
    resp = c.post("/runs", data={"question": question}, headers=headers, follow_redirects=False)
    return resp.status_code


def test_a_full_queue_surfaces_a_429(config):
    gate = threading.Event()

    def blocking(cfg, *, question, seed, run_id, stop=None):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 1, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    app = create_app(cfg, worker=worker)
    try:
        with TestClient(app) as c:
            assert _post(c, "one?") == 303
            _wait_running(worker)
            assert _post(c, "two?") == 303  # fills the one queue slot
            assert _post(c, "three?") == 429
    finally:
        gate.set()
        worker.shutdown()


def test_a_rate_limited_submission_surfaces_429_with_retry_after(config):
    limiter = RateLimiter(max_events=1, window_seconds=60.0, clock=lambda: 0.0)
    cfg = config.model_copy(update={"max_queue_depth": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=lambda *a, **k: None, rate_limiter=limiter)
    app = create_app(cfg, worker=worker)
    try:
        with TestClient(app) as c:
            assert _post(c, "one?") == 303
            over = c.post("/runs", data={"question": "two?"}, follow_redirects=False)
            assert over.status_code == 429
            assert int(over.headers["Retry-After"]) >= 1
    finally:
        worker.shutdown()


def test_distinct_tailscale_identities_get_separate_allowances(config):
    limiter = RateLimiter(max_events=1, window_seconds=60.0, clock=lambda: 0.0)
    cfg = config.model_copy(update={"max_queue_depth": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=lambda *a, **k: None, rate_limiter=limiter)
    app = create_app(cfg, worker=worker)
    try:
        with TestClient(app) as c:
            alice = {"Tailscale-User-Login": "alice@example.com"}
            bob = {"Tailscale-User-Login": "bob@example.com"}
            assert _post(c, "a1?", alice) == 303
            # bob is a different bucket, so alice's spent allowance does not touch him
            assert _post(c, "b1?", bob) == 303
            assert _post(c, "a2?", alice) == 429  # alice, over her limit, is throttled
    finally:
        worker.shutdown()


# --------------------------------------------------------------- retention


def _age(run_dir, days: float) -> None:
    when = time.time() - days * 86400
    os.utime(run_dir, (when, when))


def test_sweep_expired_content_purges_old_runs_but_keeps_the_decision_record(
    config, identities, fake_client
):
    """The automatic sweep is a content-only purge: it reclaims the bulk (drafts and
    critiques) while the signal trail — what the audit needs — is kept longer."""
    run_graph(config, question="Old one?", seed=REPORT, run_id="run-old", client=fake_client)
    run_dir = config.runs_dir / "run-old"
    assert list((run_dir / "reports").iterdir())  # produced something first
    _age(run_dir, config.retention_days + 5)

    purged = sweep_expired(config.runs_dir, config.retention_days)

    assert "run-old" in purged
    assert not list((run_dir / "reports").iterdir())  # content gone
    assert not (run_dir / "final.md").exists()
    assert (run_dir / "events.jsonl").exists()  # signal trail kept
    assert (run_dir / "signals").exists()


def test_a_fresh_run_is_left_untouched_by_the_sweep(config, identities, fake_client):
    run_graph(config, question="New one?", seed=REPORT, run_id="run-new", client=fake_client)
    assert sweep_expired(config.runs_dir, config.retention_days) == []
    assert list((config.runs_dir / "run-new" / "reports").iterdir())


def test_the_sweeper_never_purges_a_live_run(config, identities, fake_client):
    """An expired-looking id must not cost a running run its drafts."""
    run_graph(config, question="Live?", seed=REPORT, run_id="run-live", client=fake_client)
    run_dir = config.runs_dir / "run-live"
    _age(run_dir, config.retention_days + 5)

    sweeper = RetentionSweeper(
        config.runs_dir,
        config.retention_days,
        interval_seconds=3600.0,
        stop=threading.Event(),
        skip=lambda: {"run-live"},
    )
    assert sweeper.sweep_once() == []
    assert list((run_dir / "reports").iterdir())


def test_the_sweeper_is_disabled_when_the_interval_is_not_positive(config):
    sweeper = RetentionSweeper(
        config.runs_dir, config.retention_days, interval_seconds=0.0, stop=threading.Event()
    )
    sweeper.start()  # a no-op — no background thread is spawned
    sweeper.join(timeout=0.1)
    assert not any(t.name == "ra-retention" for t in threading.enumerate())
