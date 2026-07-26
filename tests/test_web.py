"""The web layer, driven end to end with a fake proxy — no network, no real models."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from contextlib import contextmanager

import pytest
from conftest import WEB_IDENTITY, web_client
from fakes import FakeClient

from reasonable_answer.graph import run as run_graph
from reasonable_answer.schemas import CritiqueOutput
from reasonable_answer.store import RunStore, sweep_expired
from reasonable_answer.web import assets
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

    def runner(cfg, *, question, seed, run_id, stop=None, **seed_provenance):
        return run_graph(
            cfg, question=question, seed=seed, run_id=run_id, client=fake_client, **seed_provenance
        )

    worker = RunWorker(config, max_concurrent=1, runner=runner)
    app = create_app(config, worker=worker)
    with web_client(app) as c:
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
        run_id = worker.submit("A distinctive question?", identity=WEB_IDENTITY)
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


# ----------------------------------------------------------------- CSRF guard


@pytest.mark.parametrize("fetch_site", ["cross-site", "none"])
def test_a_cross_site_submit_is_refused(client, fetch_site):
    """A form on an attacker's page auto-submitting a run carries a cross-site (or
    context-less) Sec-Fetch-Site, which every current browser sends. That request must
    never start a run."""
    response = client.post(
        "/runs",
        data={"question": "Burn my tokens?"},
        headers={"sec-fetch-site": fetch_site},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("fetch_site", ["same-origin", "same-site"])
def test_a_same_site_submit_is_allowed(client, config, fetch_site):
    """The app's own form reads same-origin; a sibling host under the same site reads
    same-site. Both are legitimate and must go through."""
    response = client.post(
        "/runs",
        data={"question": "From our own page?"},
        headers={"sec-fetch-site": fetch_site},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_cross_origin_submit_without_fetch_metadata_is_refused(client):
    """Older browsers omit Sec-Fetch-Site but still send Origin on a cross-origin POST;
    a mismatch against the addressed host is refused."""
    response = client.post(
        "/runs",
        data={"question": "Burn my tokens?"},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_a_mismatched_referer_without_fetch_metadata_is_refused(client):
    """With neither Sec-Fetch-Site nor Origin, a cross-host Referer is the last signal
    of a browser-driven cross-site POST."""
    response = client.post(
        "/runs",
        data={"question": "Burn my tokens?"},
        headers={"referer": "http://evil.example/page"},
    )
    assert response.status_code == 403


def test_a_matching_origin_without_fetch_metadata_is_allowed(client):
    """The fallback's allow arm: an older browser that omits Sec-Fetch-Site but sends
    an Origin matching the addressed host is a legitimate same-origin POST and must go
    through — this is the path that keeps such browsers working at all."""
    response = client.post(
        "/runs",
        data={"question": "From an older browser?"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_matching_referer_without_fetch_metadata_is_allowed(client):
    """Same allow arm one signal further down: no Sec-Fetch-Site, no Origin, but a
    Referer on the addressed host. Still a legitimate same-origin POST."""
    response = client.post(
        "/runs",
        data={"question": "From an older browser?"},
        headers={"referer": "http://testserver/"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_cross_site_resume_is_refused(client):
    """resume() is lower-risk than submit() but still state-changing, so it carries the
    same guard — and the guard fires before the run is even looked up."""
    response = client.post(
        "/runs/run-anything/resume",
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert response.status_code == 403


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
    store.owner(WEB_IDENTITY)
    store.event("intake", path="question")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app) as c:
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


def test_report_tables_are_wrapped_in_a_horizontal_scroller(config):
    """A model-written table is the one construct wider than any phone. It has to scroll
    inside its own box; without the wrapper it widens the whole document instead."""
    from reasonable_answer.web.markdown import to_html

    html = to_html("| a | b |\n| - | - |\n| 1 | 2 |\n")
    assert html.startswith('<div class="table-scroll"><table>')
    assert html.endswith("</table>\n</div>")
    # Only tables — the wrapper must not leak onto ordinary prose.
    assert "table-scroll" not in to_html("A paragraph, a [link](https://example.org).\n")


def test_the_runs_table_carries_the_labels_the_card_layout_needs(client, config):
    """Below 34rem the header row is hidden and each row becomes a card, so every cell
    that is not self-describing needs its own label. Deleting these attributes degrades
    the phone layout with nothing else failing, so pin them."""
    response = client.post("/runs", data={"question": "Labelled?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    page = client.get("/").text
    for label in ("rounds", "started", "id"):
        assert f'data-label="{label}"' in page


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
    store.owner(WEB_IDENTITY)
    store.event("intake", path="question")
    store.final(hostile, {"status": "accepted", "chosen_round": 1})

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app) as c:
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

    def slow_runner(cfg, *, question, seed, run_id, stop=None, **_):
        nonlocal peak
        running.append(run_id)
        peak = max(peak, len(running))
        time.sleep(0.3)
        running.remove(run_id)

    worker = RunWorker(config, max_concurrent=1, runner=slow_runner)
    try:
        for n in range(4):
            worker.submit(f"question {n}?", identity=WEB_IDENTITY)
        deadline = time.time() + 10
        while worker.active() and time.time() < deadline:
            time.sleep(0.05)
        assert peak == 1
    finally:
        worker.shutdown()


def test_a_crashing_run_leaves_the_worker_alive_and_the_run_resumable(config):
    def exploding(cfg, *, question, seed, run_id, stop=None, **_):
        raise RuntimeError("boom")

    worker = RunWorker(config, max_concurrent=1, runner=exploding)
    try:
        crashed = worker.submit("Crash?", identity=WEB_IDENTITY)
        deadline = time.time() + 5
        while worker.status(crashed) and time.time() < deadline:
            time.sleep(0.05)

        # the worker survived and still accepts work
        assert worker.submit("Next?", identity=WEB_IDENTITY)

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
    ran = threading.Event()

    def recording(cfg, *, question, seed, run_id, stop=None, **_):
        seen.append(seed)
        ran.set()

    worker = RunWorker(config, max_concurrent=1, runner=recording)
    app = create_app(config, worker=worker)
    try:
        store = RunStore(config.runs_dir, "run-seeded")
        store.question("Does the seed survive?", "# A seed report")
        store.owner(WEB_IDENTITY)
        store.event("intake", path="seed")

        with web_client(app) as c:
            assert c.post("/runs/run-seeded/resume", follow_redirects=False).status_code == 303

        # Wait on the runner's own signal, not a wall clock: a busy full-suite run
        # can leave a worker thread unscheduled for well over the old 5s budget. The
        # timeout is generous because the passing case returns the instant the run is
        # picked up. Splitting the two asserts keeps the failure legible — a lapsed
        # wait reads as "worker never ran", not as a lost seed.
        assert ran.wait(timeout=20), "worker never picked up the resumed run"
        assert seen == ["# A seed report"]
    finally:
        worker.shutdown()


def test_resuming_an_active_run_does_not_double_run(config):
    def slow(cfg, *, question, seed, run_id, stop=None, **_):
        time.sleep(0.5)

    worker = RunWorker(config, max_concurrent=1, runner=slow)
    try:
        run_id = worker.submit("Once?", identity=WEB_IDENTITY)
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
    store.owner(WEB_IDENTITY)
    store.event("intake", path="question")

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app) as c:
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

    def blocking(cfg, *, question, seed, run_id, stop=None, **_):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 2, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    try:
        r1 = worker.submit("occupies the worker?", identity=WEB_IDENTITY)
        _wait_running(worker)
        r2 = worker.submit("waiting one?", identity=WEB_IDENTITY)
        r3 = worker.submit("waiting two?", identity=WEB_IDENTITY)
        with pytest.raises(QueueFull):
            worker.submit("one too many?", identity=WEB_IDENTITY)
        dirs = {p.name for p in cfg.runs_dir.iterdir() if p.is_dir()}
        assert dirs == {r1, r2, r3}  # the rejected submission wrote nothing
    finally:
        gate.set()
        worker.shutdown()


def test_recover_and_resume_bypass_the_queue_cap(config):
    """The cap throttles *new* submissions; it must never turn away work already owed
    and on disk, or a backlog could wedge recovery after a restart."""
    gate = threading.Event()

    def blocking(cfg, *, question, seed, run_id, stop=None, **_):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 1, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    try:
        worker.submit("occupies?", identity=WEB_IDENTITY)
        _wait_running(worker)
        worker.submit("fills the one slot?", identity=WEB_IDENTITY)
        with pytest.raises(QueueFull):
            worker.submit("over the cap?", identity=WEB_IDENTITY)
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
        assert worker.submit("a?", identity=WEB_IDENTITY)
        assert worker.submit("b?", identity=WEB_IDENTITY)
        with pytest.raises(RateLimited):
            worker.submit("c?", identity=WEB_IDENTITY)  # allowance spent for this window
        ticks[0] += 61.0  # window rolls over
        assert worker.submit("d?", identity=WEB_IDENTITY)
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

    def blocking(cfg, *, question, seed, run_id, stop=None, **_):
        gate.wait(timeout=5)

    cfg = config.model_copy(update={"max_queue_depth": 1, "submit_rate_max": 0})
    worker = RunWorker(cfg, max_concurrent=1, runner=blocking)
    app = create_app(cfg, worker=worker)
    try:
        with web_client(app) as c:
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
        with web_client(app) as c:
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
        # No default Access header: the tailnet path is the one under test here.
        with web_client(app, identity=None) as c:
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
# ------------------------------------------------------------------- seed ingest


def test_url_seeds_are_refused_by_default(client):
    """The gate itself: `seed.allow_url` defaults to off, because a URL seed turns the
    unauthenticated web UI into a read proxy for whatever the host can reach. A
    deployment enables it only behind a network-layer egress boundary."""
    response = client.post(
        "/runs",
        data={"question": "Q?", "seed_url": "https://example.org/r"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_the_form_never_accepts_a_filesystem_path(client, config):
    """The web layer must not read local files on a request's say-so. There is no
    `seed_path` field, and a path in `seed_url` is refused by the scheme check."""
    config.seed.allow_url = True
    for value in ("/etc/passwd", "file:///etc/passwd", "../../secret.md"):
        response = client.post(
            "/runs", data={"question": "Q?", "seed_url": value}, follow_redirects=False
        )
        assert response.status_code == 400, value
        assert "http(s)" in response.json()["detail"]


def test_text_and_url_seeds_are_mutually_exclusive(client):
    response = client.post(
        "/runs",
        data={"question": "Q?", "seed": "# A", "seed_url": "https://example.org/a"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "not both" in response.json()["detail"]


def test_a_url_seed_is_fetched_converted_and_becomes_round_one(client, config, monkeypatch):
    from fakes import http_stub

    config.seed.allow_url = True
    page = "<h1>Draft</h1><p>An existing claim.</p><h2>Sources</h2><p>https://example.org/a</p>"
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: http_stub(page)
    )
    response = client.post(
        "/runs", data={"question": "Q?", "seed_url": "https://example.org/r"}, follow_redirects=False
    )
    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    # The store holds the converted markdown, not the HTML that was fetched.
    seed = Registry(config.runs_dir).seed(run_id)
    assert seed.startswith("# Draft")
    assert "<h1>" not in seed


def test_a_dead_seed_url_fails_at_submit_not_in_a_worker(client, config, monkeypatch):
    """Blocking the request is the point: the user learns immediately, instead of the
    run dying a minute later with nothing but a log line."""
    config.seed.allow_url = True

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    response = client.post(
        "/runs", data={"question": "Q?", "seed_url": "https://example.org/r"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert "could not fetch" in response.json()["detail"]


def test_pasted_html_is_converted_rather_than_shown_to_critics_raw(client, config):
    response = client.post(
        "/runs",
        data={"question": "Q?", "seed": "<h1>Pasted</h1><p>Body.</p>"},
        follow_redirects=False,
    )
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)
    assert Registry(config.runs_dir).seed(run_id).startswith("# Pasted")


def test_the_url_field_is_hidden_unless_url_seeds_are_enabled(config):
    from reasonable_answer.web.render import render_index

    # Off is the default posture, so the bare config must not render the field.
    assert 'name="seed_url"' not in render_index([], queue_depth=0, config=config)
    config.seed.allow_url = True
    assert 'name="seed_url"' in render_index([], queue_depth=0, config=config)


def test_resume_restores_the_seed(config, tmp_path):
    """A seeded run used to be unresumable from the web UI: `resume` dropped the seed,
    so `_run_fingerprint` computed a different identity, `ResumeMismatch` was raised,
    and the worker's generic handler swallowed it — leaving the run `interrupted`
    forever. The seed is read back from the store, where it sits already converted.
    """
    seen: dict = {}
    ran = threading.Event()

    def runner(cfg, *, question, seed, run_id, **_):
        seen["seed"] = seed
        ran.set()

    worker = RunWorker(config, max_concurrent=1, runner=runner)
    try:
        run_id = worker.submit("Q?", "# Seeded\n\nBody.", identity=WEB_IDENTITY)
        # Let the first run fully drain before resuming; a still-running run would
        # dedupe the resume rather than re-invoke the runner.
        deadline = time.time() + 5
        while worker.status(run_id) and time.time() < deadline:
            time.sleep(0.05)

        app = create_app(config, worker=worker)
        registry = Registry(config.runs_dir)
        assert registry.seed(run_id) == "# Seeded\n\nBody."

        seen.clear()
        ran.clear()
        worker.resume(run_id, "Q?", registry.seed(run_id))
        # Same generous, event-driven wait as the resume-seed test: distinguish a
        # worker that never ran from one that ran with the wrong seed.
        assert ran.wait(timeout=20), "worker never picked up the resumed run"
        assert seen["seed"] == "# Seeded\n\nBody.", "resume must carry the seed, or the run is stuck"
        assert app is not None
    finally:
        worker.shutdown()


# ------------------------------------------------- authentication & ownership


FRIEND = "friend@example.com"


@pytest.fixture
def owned(config):
    """An app plus a helper that plants a finished run owned by whoever you name."""
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)

    def plant(run_id: str, owner: str | None) -> str:
        store = RunStore(config.runs_dir, run_id)
        store.question(f"Whose run is {run_id}?")
        if owner is not None:
            store.owner(owner)
        store.event("intake", path="question")
        store.final("# a report", {"terminal_status": "accepted", "note": ""})
        return run_id

    try:
        yield app, plant
    finally:
        worker.shutdown(timeout=0.1)


def test_a_request_with_no_identity_is_refused(owned):
    """The middleware is the whole boundary: with no header and no dev identity there
    is nobody to serve, so nothing but the healthcheck answers."""
    app, plant = owned
    plant("run-mine", WEB_IDENTITY)
    with web_client(app, identity=None) as c:
        assert c.get("/").status_code == 403
        assert c.get("/runs/run-mine").status_code == 403
        assert c.get("/runs/run-mine/report.md").status_code == 403
        assert c.get("/runs/run-mine/audit.json").status_code == 403
        assert c.post("/runs", data={"question": "Anonymous?"}).status_code == 403


def test_the_healthcheck_answers_without_an_identity(owned):
    """The container healthcheck runs inside the container, where nothing has put a
    header on the request. Exempting it is what keeps the container from being
    restarted forever."""
    app, _ = owned
    with web_client(app, identity=None) as c:
        assert c.get("/healthz").status_code == 200


def test_the_access_header_wins_over_a_tailscale_one(owned):
    """Both are trusted the same amount; Access is how friends arrive, so it decides.
    Otherwise a tailnet-fronted deployment would file everyone's runs under whichever
    header happened to be checked first."""
    app, plant = owned
    plant("run-access", "access@example.com")
    with web_client(app, identity=None) as c:
        body = c.get(
            "/",
            headers={
                "Cf-Access-Authenticated-User-Email": "access@example.com",
                "Tailscale-User-Login": "tailnet@example.com",
            },
        ).text
    assert "run-access" in body
    assert "access@example.com" in body


def test_a_dev_identity_only_applies_when_no_header_is_present(config):
    """The local-development escape hatch. It must not override a real identity, or a
    machine that had it left on would file every friend's run under one owner."""
    cfg = config.model_copy(update={"auth": config.auth.model_copy(update={"dev_identity": "dev@localhost"})})
    worker = RunWorker(cfg, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(cfg, worker=worker)
    try:
        with web_client(app, identity=None) as c:
            assert "dev@localhost" in c.get("/").text
        with web_client(app, identity=FRIEND) as c:
            assert FRIEND in c.get("/").text
    finally:
        worker.shutdown(timeout=0.1)


def test_an_identity_header_that_is_not_one_is_ignored(owned):
    """Control characters and unbounded values would end up in `owner.txt` and in
    `audit.json`. Treating them as absent is the difference between a refused request
    and an ownership key nobody can ever match."""
    app, _ = owned
    with web_client(app, identity=None) as c:
        assert c.get("/", headers={"Tailscale-User-Login": "   "}).status_code == 403
        assert c.get("/", headers={"Tailscale-User-Login": "x" * 400}).status_code == 403


def test_the_index_shows_only_your_own_runs(owned):
    app, plant = owned
    plant("run-mine", WEB_IDENTITY)
    plant("run-theirs", FRIEND)

    with web_client(app) as c:
        mine = c.get("/").text
    with web_client(app, identity=FRIEND) as c:
        theirs = c.get("/").text

    assert "run-mine" in mine and "run-theirs" not in mine
    assert "run-theirs" in theirs and "run-mine" not in theirs


def test_a_submitted_run_is_owned_by_its_submitter(config):
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    try:
        run_id = worker.submit("Whose?", identity=FRIEND)
        assert (config.runs_dir / run_id / "owner.txt").read_text() == FRIEND
        assert Registry(config.runs_dir).summary(run_id).owner == FRIEND
    finally:
        worker.shutdown(timeout=0.1)


def test_the_access_email_is_lowercased(owned, config):
    """An identity that varies by case would split one person's runs across two owners
    (D30): `Viewer@Example.com` submitting a run and `viewer@example.com` returning for
    it must be the same person. `resolve_identity` lower-cases the Access email, so the
    run is filed under — and the owner-scoped index queried by — the lower-cased form
    whatever casing the header arrives in. Drop the `.lower()` and this run would be
    owned by `Viewer@Example.com` while the index is scoped to it verbatim, so a caller
    whose header casing differed by one letter would lose sight of their own run."""
    app, _ = owned
    with web_client(app, identity="Viewer@Example.com") as c:
        response = c.post("/runs", data={"question": "Whose casing?"}, follow_redirects=False)
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        # Written by the middleware-resolved viewer, so the header's casing is gone.
        assert (config.runs_dir / run_id / "owner.txt").read_text() == "viewer@example.com"
        assert Registry(config.runs_dir).summary(run_id).owner == "viewer@example.com"
        # The mixed-case caller still finds its own run in the owner-scoped index.
        assert run_id in c.get("/").text


def test_anyone_signed_in_can_read_a_run_they_hold_the_id_for(owned):
    """Sharing a link is the intended way to show someone a report, so a read is not
    owner-scoped — only the index is."""
    app, plant = owned
    plant("run-theirs", FRIEND)
    with web_client(app) as c:
        assert c.get("/runs/run-theirs").status_code == 200
        assert c.get("/runs/run-theirs/report").status_code == 200
        assert c.get("/runs/run-theirs/report.md").status_code == 200
        assert c.get("/runs/run-theirs/audit.json").status_code == 200
        assert c.get("/runs/run-theirs/progress").status_code == 200


def test_a_shared_run_says_who_submitted_it(owned):
    """A run reached by a link is otherwise unattributed. Your own runs need no byline."""
    app, plant = owned
    plant("run-theirs", FRIEND)
    plant("run-mine", WEB_IDENTITY)
    with web_client(app) as c:
        assert f"submitted by {FRIEND}" in c.get("/runs/run-theirs").text
        assert "submitted by" not in c.get("/runs/run-mine").text


def test_only_the_owner_can_resume_a_run(config, monkeypatch):
    """Reading costs nothing; resuming spends the owner's tokens for another 10-25
    minutes, so it stays with the person who started it."""
    # Boot recovery would otherwise pick the run up as the first client starts the
    # app's lifespan, leaving nothing interrupted for the owner to resume by hand.
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "0")
    store = RunStore(config.runs_dir, "run-stalled")
    store.question("Interrupted?")
    store.owner(FRIEND)
    store.event("queued", attempt=1, auto=False)
    store.event("generate", author="writer-a", round=1)

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app) as c:  # signed in as someone else
            assert c.post("/runs/run-stalled/resume", follow_redirects=False).status_code == 404
        with web_client(app, identity=FRIEND) as c:
            assert c.post("/runs/run-stalled/resume", follow_redirects=False).status_code == 303
    finally:
        worker.shutdown(timeout=0.1)


def test_an_owner_less_run_is_served_to_nobody(owned):
    """Runs written before ownership existed, or by `ra run` without `--owner`. There
    is no identity to attribute them to, so the web layer declines to guess."""
    app, plant = owned
    plant("run-legacy", None)
    for identity in (WEB_IDENTITY, FRIEND):
        with web_client(app, identity=identity) as c:
            assert "run-legacy" not in c.get("/").text
            assert c.get("/runs/run-legacy").status_code == 404
            assert c.get("/runs/run-legacy/report.md").status_code == 404
            assert c.get("/runs/run-legacy/audit.json").status_code == 404
            assert c.post("/runs/run-legacy/resume", follow_redirects=False).status_code == 404


def test_recovery_still_picks_up_an_owner_less_run(config, monkeypatch):
    """Invisibility is a read concern. An interrupted run is work already owed, and
    whether anyone can currently see it has no bearing on whether it should finish."""
    monkeypatch.setenv("RA_RESUME_ON_BOOT", "1")
    store = RunStore(config.runs_dir, "run-orphan")
    store.question("Owed?")
    store.event("queued", attempt=1, auto=False)
    store.event("generate", author="writer-a", round=1)

    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: time.sleep(0.2))
    try:
        assert "run-orphan" in worker.recover(Registry(config.runs_dir))
    finally:
        worker.shutdown()


def test_a_content_purge_leaves_the_run_owned(config):
    """`owner.txt` sits outside CONTENT_DIRS on purpose: a retention sweep that took
    ownership with it would silently retire the run from its owner's index."""
    from reasonable_answer.store import purge

    store = RunStore(config.runs_dir, "run-aged")
    store.question("Old?")
    store.owner(WEB_IDENTITY)
    store.event("intake", path="question")
    store.final("# a report", {"terminal_status": "accepted", "note": ""})

    purge(config.runs_dir, "run-aged", content_only=True)

    assert Registry(config.runs_dir).summary("run-aged").owner == WEB_IDENTITY


def test_a_cli_run_is_invisible_unless_it_is_given_an_owner(config, fake_client):
    """`ra run` has no request to read an identity from, so ownership is opt-in via
    `--owner` — and without it the run stays a CLI artefact."""
    run_graph(config, question="Anonymous?", run_id="run-cli", client=fake_client)
    run_graph(config, question="Attributed?", run_id="run-cli-owned", client=fake_client, owner=FRIEND)

    registry = Registry(config.runs_dir)
    assert registry.summary("run-cli").owner is None
    assert registry.summary("run-cli-owned").owner == FRIEND


def test_the_same_person_is_one_owner_at_either_door(config):
    """Access and the tailnet are two doors onto one index. A run submitted through
    one must be listed through the other, or an operator who uses both silently sees
    half their runs — which is why every source is normalized identically."""
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app, identity=None) as c:
            posted = c.post(
                "/runs",
                data={"question": "Submitted over Access?"},
                headers={"Cf-Access-Authenticated-User-Email": "Nick@Example.COM"},
                follow_redirects=False,
            )
            assert posted.status_code == 303
            run_id = posted.headers["location"].rsplit("/", 1)[-1]

            # Same person, other door, and a case the header happened to carry.
            listed = c.get("/", headers={"Tailscale-User-Login": "nick@example.com"}).text
            assert run_id in listed
            shouty = c.get("/", headers={"Tailscale-User-Login": "NICK@EXAMPLE.COM"}).text
            assert run_id in shouty

        assert Registry(config.runs_dir).summary(run_id).owner == "nick@example.com"
    finally:
        worker.shutdown(timeout=0.1)


def test_the_tailscale_display_name_is_not_an_identity(config):
    """`Tailscale-User-Name` carries a display name, a different namespace from the
    address Access reports. It was a fine rate-limit key under D21, where any stable
    string worked; as an ownership key it would file one person under two owners."""
    worker = RunWorker(config, max_concurrent=1, runner=lambda *a, **k: None)
    app = create_app(config, worker=worker)
    try:
        with web_client(app, identity=None) as c:
            assert c.get("/", headers={"Tailscale-User-Name": "Nick Borgers"}).status_code == 403
    finally:
        worker.shutdown(timeout=0.1)
# ------------------------------------------------------- installable-app assets


def test_the_manifest_names_only_icons_that_are_actually_served(client):
    """The swap-in-your-own-artwork path is only safe if this holds: rename or delete an
    icon and the manifest still promises it, and the install silently degrades."""
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")

    manifest = json.loads(response.text)
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["name"] and manifest["short_name"]

    sizes = {icon["sizes"] for icon in manifest["icons"]}
    # Chrome will not offer to install without both of these present.
    assert "192x192" in sizes and "512x512" in sizes
    # Android crops to the launcher's own shape; without a maskable entry it crops the
    # "any" icon instead and eats the artwork's corners.
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])

    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_every_icon_is_served_as_the_type_it_claims_to_be(client):
    for name, media_type in assets.ICON_TYPES.items():
        response = client.get(assets.ICONS_PREFIX + name)
        assert response.status_code == 200, name
        assert response.headers["content-type"].startswith(media_type), name
        if media_type == "image/png":
            assert response.content.startswith(b"\x89PNG\r\n\x1a\n"), name


@pytest.mark.parametrize(
    "name",
    ["nope.png", "../app.py", "..%2fapp.py", "%2e%2e%2fapp.py", "/etc/passwd"],
)
def test_an_unknown_or_traversing_asset_name_is_a_miss_not_a_read(client, name):
    """The name is a key into a fixed table, never a path segment, so none of these are a
    special case — they are all just names that are not in the table."""
    response = client.get(assets.ICONS_PREFIX + name)
    assert response.status_code != 200
    assert b"def create_app" not in response.content


def test_the_service_worker_is_served_at_root_scope(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    # Without root scope the worker controls only its own directory, and an app installed
    # from "/" would never be under its control.
    assert response.headers["service-worker-allowed"] == "/"
    # Revalidated on every navigation, so a fix reaches an installed app immediately.
    assert "no-cache" in response.headers["cache-control"]


def test_the_service_worker_cannot_cache_anything_about_a_run(client):
    """The load-bearing property of the whole feature. A cached run page would show a
    finished run as still running, which is the one output this interface must not
    produce. It is prevented structurally: the worker's precache list is an inclusion
    allowlist, and no run URL appears anywhere in its source."""
    source = client.get("/sw.js").text

    declared = json.loads(re.search(r"var ASSETS = (\[.*?\]);", source, re.S).group(1))
    assert declared, "the precache list must not be templated away to nothing"
    assert declared == client.app.state.assets.precache
    assert not [path for path in declared if path.startswith("/runs")]

    # Comments in that file discuss run URLs at length — the claim is about what the code
    # can reach, so the prose has to come off before asserting on it.
    code = "\n".join(line.split("//")[0] for line in source.splitlines())
    for forbidden in ("/runs", "stream", "progress", "audit.json", "report.md"):
        assert forbidden not in code, f"the worker's code must never name {forbidden}"
    # Exactly one write into a cache, and it sits behind the ASSETS membership test.
    assert code.count(".put(") == 1


def test_the_cache_version_tracks_the_asset_bytes(client):
    """Replacing an icon has to invalidate the old one on every installed client with no
    version bump and nothing to clear by hand, so the key is the bytes themselves."""
    first = assets.cache_version({"/a.png": b"one", "/b.png": b"two"})
    assert first == assets.cache_version({"/b.png": b"two", "/a.png": b"one"})
    assert first != assets.cache_version({"/a.png": b"one", "/b.png": b"CHANGED"})
    # A rename changes the URL the worker precaches, so it has to change the key too.
    assert first != assets.cache_version({"/a.png": b"one", "/c.png": b"two"})

    assert client.app.state.assets.version in client.get("/sw.js").text


def test_the_offline_page_stands_alone(client):
    """It is shown precisely when the server is unreachable, so a link into a run would be
    a link to a page that cannot load."""
    response = client.get("/offline.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/runs" not in response.text


def test_a_run_page_is_never_cacheable(client, config):
    """The same rule as the service worker's, restated at the HTTP layer — an installed
    standalone app leans on the browser cache and the back-forward cache much harder than
    a tab does."""
    response = client.post("/runs", data={"question": "Stale?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    assert client.get(f"/runs/{run_id}").headers["cache-control"] == "no-store"
    assert client.get(f"/runs/{run_id}/progress").headers["cache-control"] == "no-store"


def test_the_head_advertises_the_installable_app(client):
    page = client.get("/").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">' in page
    # iOS reads this and ignores the manifest's icons entirely.
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page
    assert '<meta name="theme-color" content="#fbfaf8" media="(prefers-color-scheme: light)">' in page
    assert '<meta name="theme-color" content="#16181a" media="(prefers-color-scheme: dark)">' in page
    # Lets the page reach under the notch, which the stylesheet pays back with safe-area
    # padding. Without it the safe-area insets all resolve to zero.
    assert "viewport-fit=cover" in page


def test_the_csp_admits_the_manifest_and_the_worker_and_nothing_off_origin(client, config):
    """Pinned as an exact literal on purpose. Widening this policy is a decision recorded
    in docs/decisions.md (D27), not a tidy-up — so it should not be possible to widen it
    without a test turning red and asking why."""
    expected = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; "
        "worker-src 'self'; form-action 'self'; base-uri 'none'"
    )
    response = client.post("/runs", data={"question": "Policy?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]
    _wait_for_final(config, run_id)

    for url in ("/", f"/runs/{run_id}", f"/runs/{run_id}/report"):
        page = client.get(url).text
        assert f'content="{expected}"' in page, url


def test_service_worker_registration_is_guarded_and_cannot_break_the_live_script(client, config):
    """Two things at once. The guard is what keeps a plain-http tailnet address silent
    instead of throwing a SecurityError; the semicolon is what stops `})()` followed by
    `(function` from parsing as a call and taking the live stream down with it."""
    response = client.post("/runs", data={"question": "Both scripts?"}, follow_redirects=False)
    run_id = response.headers["location"].rsplit("/", 1)[-1]

    live_page = client.get(f"/runs/{run_id}").text
    assert "isSecureContext" in live_page
    assert "navigator.serviceWorker.register('/sw.js'" in live_page

    script = live_page.split("<script>")[1].split("</script>")[0]
    if "EventSource" in script:  # the run was still live when the page was rendered
        # Two IIFEs share one <script>, and the first has to close with a semicolon:
        # without it, `})()` followed by `(function` parses as a single call expression.
        assert re.search(r"\}\)\(\);\s*\(function", script), script

    _wait_for_final(config, run_id)
    # The registration is on every page, live or not — an installed app is entered from
    # whichever page the user last had open.
    assert "isSecureContext" in client.get("/").text
    assert "isSecureContext" in client.get(f"/runs/{run_id}/report").text


# ------------------------------------------------------ base path (reverse-proxy prefix)
#
# The deployment model is a *stripping* proxy: `location /app/ { proxy_pass .../; }` removes
# `/app/` before the request reaches the app, so the app serves at its normal stripped
# routes. The TestClient stands in for that proxy by requesting those stripped paths — the
# thing under test is that every URL the app *emits* carries the prefix and nothing escapes
# back to the origin root, past the Access policy scoped to the prefix. See D29.

BASE = "/app"

#: Every attribute a browser resolves as a navigable/subresource URL. The event stream is
#: `data-stream`, read by the inline live script and handed to `EventSource`.
_URL_ATTRS = re.compile(r'(?:href|src|action|data-stream)="(/[^"]*)"')


def _absolute_urls(html: str) -> list[str]:
    """Every root-absolute URL the page emits, from the URL-bearing attributes and from the
    service-worker `register(...)` call the attribute scan cannot see."""
    urls = _URL_ATTRS.findall(html)
    urls += re.findall(r"register\('(/[^']*)'", html)
    urls += re.findall(r"scope:\s*'(/[^']*)'", html)
    return urls


@contextmanager
def _prefixed_client(config, fake_client, base_path=BASE):
    """A client whose app is built with `RA_ROOT_PATH` set, driven by the same fake graph
    the default `client` fixture uses."""

    def runner(cfg, *, question, seed, run_id, stop=None, **seed_provenance):
        return run_graph(
            cfg, question=question, seed=seed, run_id=run_id, client=fake_client, **seed_provenance
        )

    previous = os.environ.get("RA_ROOT_PATH")
    os.environ["RA_ROOT_PATH"] = base_path
    worker = RunWorker(config, max_concurrent=1, runner=runner)
    try:
        app = create_app(config, worker=worker)
        # Signed in as the default viewer: the auth middleware (D30) refuses every
        # route but /healthz, and these tests assert URL-prefixing behaviour that is
        # only reachable past that gate.
        with web_client(app) as c:
            yield c
    finally:
        worker.shutdown()
        if previous is None:
            os.environ.pop("RA_ROOT_PATH", None)
        else:
            os.environ["RA_ROOT_PATH"] = previous


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("/", ""),
        ("/app", "/app"),
        ("/app/", "/app"),
        ("  /app/  ", "/app"),
        ("app", "/app"),  # a missing leading slash anchors at the origin, not a sibling
        ("/app/v2/", "/app/v2"),
    ],
)
def test_normalize_base_path_collapses_to_the_join_identity(raw, expected):
    from reasonable_answer.web.render import normalize_base_path

    assert normalize_base_path(raw) == expected


def test_an_unset_prefix_leaves_every_url_at_the_root(client):
    """The empty base is the join identity: with no `RA_ROOT_PATH`, every emitted URL is
    byte-identical to before this feature existed."""
    for url in _absolute_urls(client.get("/").text):
        assert not url.startswith(f"{BASE}/"), url
    assert '<link rel="manifest" href="/manifest.webmanifest">' in client.get("/").text


def test_the_index_stays_entirely_under_the_prefix(config, fake_client):
    with _prefixed_client(config, fake_client) as c:
        page = c.get("/").text
    urls = _absolute_urls(page)
    assert urls, "the page emits URLs; the scan found none, so it is not asserting anything"
    for url in urls:
        # `/app` itself (the brand link is `/app/`) and everything below it are in; anything
        # else is a link that escapes the Access-scoped prefix back to the origin root.
        assert url == BASE or url.startswith(f"{BASE}/"), url
    assert f'action="{BASE}/runs"' in page
    assert f'href="{BASE}/manifest.webmanifest"' in page
    assert f"register('{BASE}/sw.js', {{ scope: '{BASE}/' }})" in page


def test_the_manifest_is_served_under_the_prefix(config, fake_client):
    with _prefixed_client(config, fake_client) as c:
        manifest = json.loads(c.get("/manifest.webmanifest").text)
        # start_url, scope and id are what scope the installed app; unprefixed, the app
        # launches at the origin root and its scope no longer contains its own pages.
        assert manifest["start_url"] == f"{BASE}/"
        assert manifest["scope"] == f"{BASE}/"
        assert manifest["id"] == f"{BASE}/"
        for icon in manifest["icons"]:
            assert icon["src"].startswith(f"{BASE}/static/icons/"), icon["src"]
            # The proxy strips the prefix, so the stripped path is what the app serves.
            assert c.get(icon["src"][len(BASE):]).status_code == 200, icon["src"]


def test_the_service_worker_registers_and_precaches_under_the_prefix(config, fake_client):
    with _prefixed_client(config, fake_client) as c:
        response = c.get("/sw.js")
        # Without the prefixed scope the worker would claim the origin root, not `/app/`.
        assert response.headers["service-worker-allowed"] == f"{BASE}/"
        source = response.text
        declared = json.loads(re.search(r"var ASSETS = (\[.*?\]);", source, re.S).group(1))
        assert declared == c.app.state.assets.precache
        assert declared, "the precache list must not be templated away to nothing"
        for path in declared:
            assert path.startswith(f"{BASE}/"), path
        # OFFLINE has to match its precached entry or the navigate fallback misses the cache.
        offline = re.search(r"var OFFLINE = '([^']*)'", source).group(1)
        assert offline == f"{BASE}/offline.html"
        assert offline in declared
        # The structural no-run-caching property is unchanged under a prefix.
        assert not [p for p in declared if "/runs" in p]


def test_a_submit_redirect_carries_the_prefix(config, fake_client):
    """A `303` to an unprefixed `/runs/<id>` would bounce the browser straight out of the
    Access-scoped prefix on the very first submission."""
    with _prefixed_client(config, fake_client) as c:
        response = c.post("/runs", data={"question": "Under a prefix?"}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(f"{BASE}/runs/")


def test_the_run_page_stays_entirely_under_the_prefix(config, fake_client):
    with _prefixed_client(config, fake_client) as c:
        response = c.post("/runs", data={"question": "A prefixed run?"}, follow_redirects=False)
        run_id = response.headers["location"].rsplit("/", 1)[-1]
        _wait_for_final(config, run_id)

        for path in (f"/runs/{run_id}", f"/runs/{run_id}/report"):
            page = c.get(path).text
            for url in _absolute_urls(page):
                assert url == BASE or url.startswith(f"{BASE}/"), (path, url)
        run_page = c.get(f"/runs/{run_id}").text
        # The live stream is the `connect-src 'self'` target; it has to stay under the prefix
        # too or the EventSource escapes it.
        assert f'data-stream="{BASE}/runs/{run_id}/stream"' in run_page
