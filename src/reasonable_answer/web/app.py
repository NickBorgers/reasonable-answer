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
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from ..config import Config, ConfigError
from .registry import Registry, RunSummary
from .render import render_index, render_report, render_run, render_run_progress
from .worker import RunWorker

log = logging.getLogger(__name__)


def create_app(
    config: Config | None = None,
    worker: RunWorker | None = None,
    max_concurrent: int | None = None,
) -> FastAPI:
    config = config or Config.load(os.environ.get("RA_CONFIG"))
    _check_runs_dir_writable(config)
    concurrent = max_concurrent or int(os.environ.get("RA_MAX_CONCURRENT_RUNS", "1"))
    worker = worker or RunWorker(config, max_concurrent=concurrent)
    registry = Registry(config.runs_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        worker.shutdown()

    app = FastAPI(title="reasonable-answer", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.config = config
    app.state.worker = worker
    app.state.registry = registry

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        runs = registry.list(active=worker.active())
        return render_index(runs, queue_depth=worker.queue_depth, config=config)

    @app.post("/runs")
    def submit(question: str = Form(...), seed: str = Form("")) -> RedirectResponse:
        question = question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="a question is required")
        if len(question) > config.max_question_chars:
            raise HTTPException(
                status_code=400,
                detail=f"question exceeds {config.max_question_chars} characters",
            )
        seed_text = seed.strip() or None
        if seed_text and len(seed_text) > config.max_report_chars:
            raise HTTPException(
                status_code=400, detail=f"seed exceeds {config.max_report_chars} characters"
            )
        run_id = worker.submit(question, seed_text)
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
    def resume(run_id: str) -> RedirectResponse:
        summary = _require(registry, worker, run_id)
        if summary.status != "interrupted":
            raise HTTPException(status_code=409, detail=f"run is {summary.status}, not interrupted")
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


def _require(registry: Registry, worker: RunWorker, run_id: str) -> RunSummary:
    if not registry.exists(run_id) and worker.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return registry.summary(run_id, worker.active())


def _sse(event: str, data: str) -> str:
    """SSE frames are newline-delimited, so every payload line needs its own `data:`."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{body}\n\n"
