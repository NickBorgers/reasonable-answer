"""The web interface.

Design notes worth keeping in mind while reading:

* **No auth here on purpose.** The deployment posture is tailnet-only; Tailscale ACLs
  are the access control. Do not expose this to the internet without putting real
  authentication in front of it — anyone who can reach it can spend tokens and read
  the audit trail, which holds seed material.
* **Showing reports to a human does not weaken the isolation design.** Blindness is
  about what enters a *model's* context. This UI is a window onto the audit trail,
  which is the whole reason the pipeline keeps one.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from .. import ingest, shutdown
from ..config import Config, ConfigError
from .registry import Registry, RunSummary
from .render import render_index, render_report, render_run, render_run_progress
from .retention import RetentionSweeper
from .worker import QueueFull, RateLimited, RunWorker

#: Set by `tailscale serve` when it fronts the app; carries the calling node's user.
#: Present ⇒ rate-limit that identity; absent ⇒ fall back to one global bucket.
_IDENTITY_HEADERS = ("tailscale-user-login", "tailscale-user-name")

log = logging.getLogger(__name__)


def create_app(
    config: Config | None = None,
    worker: RunWorker | None = None,
    max_concurrent: int | None = None,
) -> FastAPI:
    config = config or Config.load(os.environ.get("RA_CONFIG"))
    _check_runs_dir_writable(config)
    concurrent = max_concurrent or int(os.environ.get("RA_MAX_CONCURRENT_RUNS", "1"))
    if (cap := os.environ.get("RA_MAX_RESUME_ATTEMPTS")):
        config = config.model_copy(update={"max_resume_attempts": int(cap)})
    worker = worker or RunWorker(config, max_concurrent=concurrent)
    registry = Registry(config.runs_dir)
    # A never-live run cannot be older than the retention window, but skipping the live
    # set anyway means an in-flight run can never have its drafts swept mid-run.
    sweeper = RetentionSweeper(
        config.runs_dir,
        config.retention_days,
        config.retention_sweep_interval_seconds,
        stop=shutdown.event(),
        skip=lambda: set(worker.active()),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Recovery lives here rather than in RunWorker.__init__ so that constructing a
        # worker stays inert — tests build one directly and should not have the previous
        # process's leftovers enqueued underneath them.
        worker.recover(registry)
        sweeper.start()
        yield
        # uvicorn installs its own SIGTERM handler inside `uvicorn.run()`, which would
        # overwrite anything we registered first, so the signal reaches us here instead:
        # uvicorn's handler sets should_exit, which unwinds into lifespan shutdown.
        shutdown.request_stop("lifespan")
        worker.shutdown()
        # The sweeper shares the stop flag, so it is already unwinding; join it on the
        # same grace budget rather than leaving a non-daemon thread behind.
        sweeper.join(timeout=shutdown.grace_seconds() * 0.5)

    app = FastAPI(title="reasonable-answer", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.config = config
    app.state.worker = worker
    app.state.registry = registry

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        runs = registry.list(active=worker.active())
        return render_index(runs, queue_depth=worker.queue_depth, config=config)

    # A seed reaches this handler as pasted text or as an http(s) URL, and never as a
    # filesystem path: no code path in the web layer may construct a `Path` from
    # request data. The CLI reads local files because its caller already has the shell.
    @app.post("/runs")
    def submit(
        request: Request,
        question: str = Form(...),
        seed: str = Form(""),
        seed_url: str = Form(""),
    ) -> RedirectResponse:
        _reject_cross_site(request)
        question = question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="a question is required")
        if len(question) > config.max_question_chars:
            raise HTTPException(
                status_code=400,
                detail=f"question exceeds {config.max_question_chars} characters",
            )

        seed_text = seed.strip() or None
        seed_url = seed_url.strip()
        if seed_text and seed_url:
            raise HTTPException(
                status_code=400, detail="provide a seed as text or as a URL, not both"
            )
        if seed_url and not config.seed.allow_url:
            raise HTTPException(status_code=400, detail="URL seeds are disabled")
        if seed_url and not seed_url.lower().startswith(("http://", "https://")):
            # Refused here, before an opener exists, so `file:///etc/passwd` never
            # reaches the fetch layer at all.
            raise HTTPException(status_code=400, detail="a seed URL must be http(s)")

        # Fetching blocks the request. `submit` is a plain `def`, so FastAPI runs it in
        # a threadpool and the event loop is unaffected — and a dead URL fails visibly
        # here instead of killing a worker thread a minute later.
        ingested = None
        if seed_url or seed_text:
            try:
                ingested = (
                    ingest.from_url(seed_url, config=config)
                    if seed_url
                    else ingest.from_text(seed_text or "")
                )
            except ingest.IngestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if len(ingested.markdown) > config.max_report_chars:
                raise HTTPException(
                    status_code=400, detail=f"seed exceeds {config.max_report_chars} characters"
                )

        try:
            run_id = worker.submit(
                question,
                ingested.markdown if ingested else None,
                identity=_identity(request),
                seed_format=ingested.format if ingested else None,
                seed_source=ingested.source if ingested else None,
                seed_warnings=ingested.warnings if ingested else (),
            )
        except RateLimited as exc:
            # A concrete Retry-After lets a well-behaved client back off precisely
            # instead of guessing; the ceil keeps it an integer count of seconds.
            raise HTTPException(
                status_code=429,
                detail="too many submissions; slow down",
                headers={"Retry-After": str(math.ceil(exc.retry_after))},
            ) from exc
        except QueueFull as exc:
            raise HTTPException(
                status_code=429, detail="the run queue is full; try again shortly"
            ) from exc
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str) -> str:
        summary = _require(registry, worker, run_id)
        return render_run(
            summary=summary,
            timeline=registry.timeline(run_id),
            report=registry.report(run_id),
            final=registry.final(run_id),
            lens_names=registry.lens_names(),
        )

    @app.post("/runs/{run_id}/resume")
    def resume(run_id: str, request: Request) -> RedirectResponse:
        _reject_cross_site(request)
        summary = _require(registry, worker, run_id)
        # `abandoned` is accepted on purpose: it means automatic recovery gave up, and a
        # human overriding that is the entire point of the escape hatch. A manual resume
        # is not counted against the attempt cap, so this always works.
        if summary.status not in ("interrupted", "abandoned"):
            raise HTTPException(status_code=409, detail=f"run is {summary.status}, not interrupted")
        # The seed is part of the run's identity (`graph._run_fingerprint`), so resuming
        # without it made every seeded run fail the fingerprint check and sit at
        # `interrupted` forever. `seed.md` holds the converted markdown that was
        # hashed, so reading it back reproduces the fingerprint exactly.
        worker.resume(run_id, summary.question, registry.seed(run_id))
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    # ------------------------------------------------------------- fragments

    @app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
    def progress(run_id: str) -> str:
        """The live region, re-rendered. Kept separate from the page so the SSE
        stream can push it without a reload."""
        summary = _require(registry, worker, run_id)
        return render_run_progress(
            summary=summary,
            timeline=registry.timeline(run_id),
            lens_names=registry.lens_names(),
        )

    @app.get("/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request) -> StreamingResponse:
        """Server-sent events, driven by polling the run's own event log.

        Polling a file looks crude next to a pub/sub channel, but the pipeline
        already writes every state change to `events.jsonl`, and a tick is minutes
        long — so a 1s poll is both simpler and entirely sufficient.
        """
        _require(registry, worker, run_id)

        async def events() -> Any:
            seen = 0
            while True:
                if await request.is_disconnected():
                    return
                # uvicorn drains connections *before* running lifespan shutdown, and this
                # generator otherwise only ends when the client leaves or the run
                # finishes. One forgotten browser tab would hold the whole grace period
                # before the worker was even told to stop.
                if shutdown.stop_requested():
                    return
                batch = list(registry.events(run_id, offset=seen))
                if batch:
                    seen += len(batch)
                    fragment = render_run_progress(
                        summary=registry.summary(run_id, worker.active()),
                        timeline=registry.timeline(run_id),
                        lens_names=registry.lens_names(),
                    )
                    yield _sse("progress", fragment)
                summary = registry.summary(run_id, worker.active())
                if not summary.is_live and registry.final(run_id) is not None:
                    yield _sse("done", summary.status)
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ----------------------------------------------------------------- assets

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report_page(run_id: str) -> str:
        summary = _require(registry, worker, run_id)
        report = registry.report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="this run has not produced a report yet")
        return render_report(summary, report, registry.final(run_id))

    @app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def report_markdown(run_id: str) -> str:
        _require(registry, worker, run_id)
        report = registry.report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="this run has not produced a report yet")
        return report

    @app.get("/runs/{run_id}/audit.json")
    def audit(run_id: str) -> dict[str, Any]:
        _require(registry, worker, run_id)
        return {
            "summary": registry.summary(run_id, worker.active()).__dict__,
            "final": registry.final(run_id),
            "events": list(registry.events(run_id)),
        }

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    return app


def _check_runs_dir_writable(config: Config) -> None:
    """Fail at startup, not on the first submission.

    A bind-mounted host directory arrives owned by root, while the container runs as
    an unprivileged uid — so the first thing a user ever does returns a 500 from deep
    inside the store. Named volumes inherit the image's ownership and are fine; bind
    mounts need chowning to the container uid.
    """
    runs = Path(config.runs_dir)
    try:
        runs.mkdir(parents=True, exist_ok=True)
        probe = runs / ".write-probe"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise ConfigError(
            f"runs directory {runs.resolve()} is not writable by uid {os.getuid()}: {exc}\n"
            f"If this is a bind mount, chown it on the host: "
            f"sudo chown -R {os.getuid()}:{os.getgid()} <host-path>\n"
            f"A named docker volume avoids this entirely."
        ) from exc


def _reject_cross_site(request: Request) -> None:
    """Refuse browser-driven cross-site POSTs — the CSRF guard for the two
    state-changing routes.

    A plain HTML form POST triggers no CORS preflight, so the tailnet-only posture and
    the CSP's `form-action 'self'` (which only constrains forms *this* app serves) do
    nothing to stop a foreign page from auto-submitting a run to a guessable MagicDNS
    host and burning a full 10–25-minute run. There is no cookie or session to lean on
    a SameSite attribute for, so the request context itself is the only signal.

    `Sec-Fetch-Site` is sent by every current browser and is authoritative when present:
    a form this app served reads `same-origin`, a sibling host under the same site reads
    `same-site`, and both `cross-site` and `none` are what a foreign page's auto-submit
    (or a POST with no browsing context) look like. When it is absent — an older browser
    or a non-browser caller such as curl or the test client — fall back to `Origin`, then
    `Referer`, compared against the host the client addressed. A browser always sends
    `Origin` on a cross-origin POST, so a request carrying none of these three headers is
    not a browser being tricked and is allowed through.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        if fetch_site not in ("same-origin", "same-site"):
            raise HTTPException(status_code=403, detail="cross-site request refused")
        return
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value is not None:
            if not _same_host(value, request):
                raise HTTPException(status_code=403, detail="cross-site request refused")
            return


def _same_host(candidate_url: str, request: Request) -> bool:
    """True when `candidate_url`'s host[:port] matches the Host the client addressed.

    Comparison is on `netloc` only: scheme can legitimately differ behind a TLS-
    terminating proxy, but a mismatched host is exactly the cross-origin case we reject.
    """
    host = request.headers.get("host")
    if not host:
        return False
    return urlsplit(candidate_url).netloc == host


def _require(registry: Registry, worker: RunWorker, run_id: str) -> RunSummary:
    if not registry.exists(run_id) and worker.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return registry.summary(run_id, worker.active())


def _identity(request: Request) -> str:
    """The rate-limit key: the Tailscale identity when the app is fronted so that the
    header is present, otherwise one shared `global` bucket.

    These headers are only trustworthy behind `tailscale serve` on the tailnet posture
    the deployment assumes — anyone reaching the app directly could forge them, but such
    a caller could also just vary them to defeat any per-identity limit, so nothing is
    lost by trusting them here. The global fallback still bounds the forged-header case.
    """
    for header in _IDENTITY_HEADERS:
        value = request.headers.get(header)
        if value:
            return value.strip()
    return "global"


def _sse(event: str, data: str) -> str:
    """SSE frames are newline-delimited, so every payload line needs its own `data:`."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{body}\n\n"
