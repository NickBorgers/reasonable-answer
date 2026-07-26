"""The web interface.

Design notes worth keeping in mind while reading:

* **Every request carries an identity, and the app does not verify it.** It comes
  from a header set by Cloudflare Access or by `tailscale serve` (`identity.py`),
  which is only meaningful while the app's port is unreachable except through one of
  them. A caller who can reach the port directly can claim to be anyone (D30,
  docs/authentication.md).
* **Authentication is enforced by middleware, not by each route.** Every route but
  `/healthz` is behind it, including routes nobody has written yet — the failure mode
  of a per-route call is a new handler that forgets to make it.
* **Ownership is per run and scopes the index, not each read.** You see your own runs
  listed; anyone signed in who holds a run id can read that run. Sharing a link is
  the intended way to show someone a report. Runs with no owner — written before
  ownership existed, or by `ra run` without `--owner` — are served to nobody.
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

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)

from .. import ingest, shutdown
from ..config import Config, ConfigError
from ..llm import LLMClient
from . import assets as static_assets
from .identity import resolve_identity
from .refine import RefinementService
from .registry import Registry, RunSummary
from .render import (
    normalize_base_path,
    render_index,
    render_report,
    render_run,
    render_run_progress,
)
from .retention import RetentionSweeper
from .worker import QueueFull, RateLimited, RunWorker

#: The one route that must answer an anonymous caller: the container healthcheck runs
#: inside the container, with no proxy in front of it to attach an identity header.
#: It reports liveness only, and nothing about any run.
_UNAUTHENTICATED_PATHS = frozenset({"/healthz"})

log = logging.getLogger(__name__)


def create_app(
    config: Config | None = None,
    worker: RunWorker | None = None,
    max_concurrent: int | None = None,
    refiner: RefinementService | None = None,
) -> FastAPI:
    config = config or Config.load(os.environ.get("RA_CONFIG"))
    _check_runs_dir_writable(config)
    # The URL prefix the app is served under behind a stripping reverse proxy (e.g.
    # `RA_ROOT_PATH=/app` fronted by `location /app/ { proxy_pass http://ra:8080/; }`).
    # Empty by default, which leaves every emitted URL byte-identical to a root-origin
    # deployment. Resolved once here, like the static assets, and only ever prepended to
    # URLs the app *emits* — the proxy strips it from the path before the request lands, so
    # the routes below stay unprefixed. See D29.
    base_path = normalize_base_path(os.environ.get("RA_ROOT_PATH"))
    concurrent = max_concurrent or int(os.environ.get("RA_MAX_CONCURRENT_RUNS", "1"))
    if (cap := os.environ.get("RA_MAX_RESUME_ATTEMPTS")):
        config = config.model_copy(update={"max_resume_attempts": int(cap)})
    if (dev_identity := os.environ.get("RA_DEV_IDENTITY")):
        # An env var rather than only a roster key because the roster is mounted
        # read-only in the container: this is the local-development escape hatch, and
        # it has to be settable from the same place `make serve` already lives.
        config = config.model_copy(
            update={"auth": config.auth.model_copy(update={"dev_identity": dev_identity})}
        )
    if config.auth.dev_identity:
        # Warned about wherever it is set from, because it turns "no identity header"
        # from a refusal into a login — which is what you want on a laptop and never
        # what you want anywhere someone else can reach.
        log.warning(
            "auth.dev_identity is set: unauthenticated requests are treated as %s",
            config.auth.dev_identity,
        )
    worker = worker or RunWorker(config, max_concurrent=concurrent)
    refiner = refiner or RefinementService(
        config,
        # A refine-dedicated `LLMClient`, never shared with anything else:
        # `LLMClient.resolve_identities` *replaces* its whole identity map on every call
        # rather than merging into it (see `RefinementService.start`'s docstring), so a
        # shared instance would have the roster's resolved identities blanked out from
        # underneath it the moment `refiner.start()` ran. Built only when the feature is
        # enabled -- a disabled service never makes a network call, so there is nothing
        # to construct a client for.
        client=LLMClient(config) if config.refine.enabled else None,
    )
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
        # A bad or schema-incapable refine alias must fail here, at boot, not on some
        # user's first pause after typing (D26) -- so this runs before recovery, which is
        # itself allowed to enqueue real work.
        refiner.start()
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
        refiner.shutdown()
        # The sweeper shares the stop flag, so it is already unwinding; join it on the
        # same grace budget rather than leaving a non-daemon thread behind.
        sweeper.join(timeout=shutdown.grace_seconds() * 0.5)

    app = FastAPI(title="reasonable-answer", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.config = config
    app.state.worker = worker
    app.state.registry = registry
    app.state.refiner = refiner
    app.state.base_path = base_path
    # Read once here rather than per request: these files do not change while the process
    # runs, and resolving them at startup means a missing one is a 404 rather than a 500.
    # The base path shapes the URLs the manifest names and the worker precaches, so it is
    # baked in at the same point.
    app.state.assets = assets = static_assets.load(base_path)

    # ------------------------------------------------------------------- auth

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        """Resolve the caller once, and refuse the request if there is nobody there.

        A middleware rather than a `Depends` or a hand-written call per route,
        because those are opt-in and this must not be: the cost of forgetting is an
        open route onto other people's seed material. Every handler below can assume
        `request.state.viewer` is a real identity.

        `HTTPException` is not available here — it is raised past the exception
        middleware that would turn it into a response — so the refusal is returned
        directly. Nothing is logged about the failed attempt: the header is
        attacker-controlled, and a rejected request has no identity to attribute.
        """
        if request.url.path not in _UNAUTHENTICATED_PATHS:
            viewer = resolve_identity(request, config.auth)
            if viewer is None:
                return PlainTextResponse("authentication required", status_code=403)
            request.state.viewer = viewer
        return await call_next(request)

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> str:
        runs = registry.list(active=worker.active(), owner=request.state.viewer)
        return render_index(
            runs,
            queue_depth=worker.queue_depth,
            config=config,
            base_path=base_path,
            viewer=request.state.viewer,
        )

    # A seed reaches this handler as pasted text or as an http(s) URL, and never as a
    # filesystem path: no code path in the web layer may construct a `Path` from
    # request data. The CLI reads local files because its caller already has the shell.
    @app.post("/runs")
    def submit(
        request: Request,
        question: str = Form(...),
        seed: str = Form(""),
        seed_url: str = Form(""),
        refine_offer_id: str = Form(""),
        refine_selected: str = Form(""),
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

        # `refine_offer_id`/`refine_selected` are claims, not evidence (D26): resolved
        # against the server's own offer record, never trusted from the client. Skipped
        # outright when refinement is disabled -- with no offer ever minted while off,
        # any stray or forged pair would resolve to `unverified` anyway, but calling
        # `resolve()` regardless would still write a `refinement` event for a feature
        # that is supposed to leave no trace when it is off. A malformed claim here can
        # never fail the submission; the run proceeds normally either way.
        refinement = (
            refiner.resolve(refine_offer_id or None, refine_selected or None, question)
            if refiner.enabled
            else None
        )

        try:
            run_id = worker.submit(
                question,
                ingested.markdown if ingested else None,
                identity=request.state.viewer,
                seed_format=ingested.format if ingested else None,
                seed_source=ingested.source if ingested else None,
                seed_warnings=ingested.warnings if ingested else (),
                refinement=refinement,
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
        return RedirectResponse(url=f"{base_path}/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str, request: Request, response: Response) -> str:
        # A run page is a snapshot of something still moving. Installed as a standalone app
        # this leans on the HTTP cache and the back-forward cache far harder than a browser
        # tab does, and showing a finished run as still running is the one output this
        # interface must not produce. The service worker refuses to cache these too; this is
        # the same rule stated at the layer below it.
        response.headers["Cache-Control"] = "no-store"
        summary = _require(registry, worker, run_id)
        return render_run(
            summary=summary,
            timeline=registry.timeline(run_id),
            report=registry.report(run_id),
            final=registry.final(run_id),
            lens_names=registry.lens_names(),
            base_path=base_path,
            viewer=request.state.viewer,
        )

    @app.post("/runs/{run_id}/resume")
    def resume(run_id: str, request: Request) -> RedirectResponse:
        _reject_cross_site(request)
        summary = _require(registry, worker, run_id)
        # The one place ownership gates more than the index. Reading a run costs
        # nothing, but resuming one spends its owner's tokens for another 10–25
        # minutes, so it stays with the person who started it. 404 rather than 403,
        # matching `_require`: a stranger learns nothing about which ids are real.
        if summary.owner != request.state.viewer:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
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
        return RedirectResponse(url=f"{base_path}/runs/{run_id}", status_code=303)

    @app.post("/refine")
    def refine(request: Request, question: str = Form(...)) -> JSONResponse:
        # Same-origin enforcement first, uniform with every other state-changing route --
        # explaining an exception to this rule costs more than just applying it here too.
        _reject_cross_site(request)
        if not refiner.enabled:
            # The endpoint does not exist when the feature is off (D26).
            raise HTTPException(status_code=404, detail="refinement is disabled")

        question = question.strip()
        if len(question) > config.max_question_chars:
            raise HTTPException(
                status_code=400,
                detail=f"question exceeds {config.max_question_chars} characters",
            )

        retry_after = refiner.limiter.check_and_record(request.state.viewer)
        if retry_after > 0:
            # This endpoint's entire contract is "silence on any problem": a rate-limited
            # attempt degrades to the same empty result a timeout or a saturated
            # semaphore would produce, rather than a 429. That keeps the page's budget
            # and error handling uniform across every failure mode -- nothing to retry,
            # nothing to branch on -- for a feature that was never load-bearing.
            return JSONResponse(
                {"offer_id": "", "suggestions": []}, headers={"Cache-Control": "no-store"}
            )

        # Blocking LLM call; `refine` is a plain `def`, so FastAPI runs it in the
        # threadpool and the event loop is unaffected -- same as `submit` above.
        offer = refiner.suggest(question)
        return JSONResponse(offer.as_json(), headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- fragments

    @app.get("/runs/{run_id}/progress", response_class=HTMLResponse)
    def progress(run_id: str, response: Response) -> str:
        """The live region, re-rendered. Kept separate from the page so the SSE
        stream can push it without a reload."""
        response.headers["Cache-Control"] = "no-store"
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
        return render_report(summary, report, registry.final(run_id), base_path=base_path)

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

    # ------------------------------------------------------- installable-app assets
    #
    # Four hand-written routes rather than a `StaticFiles` mount. The reason is the rule
    # stated above `submit`: no code path here builds a `Path` out of request data. A mount
    # would; these do not — see `web/assets.py`, where the filenames are literals and a
    # request string is only ever a dictionary key.

    @app.get(static_assets.MANIFEST_PATH)
    def manifest() -> Response:
        return _asset_response(assets.manifest, "public, max-age=3600")

    @app.get(static_assets.OFFLINE_PATH, response_class=HTMLResponse)
    def offline() -> Response:
        return _asset_response(assets.offline, "public, max-age=3600")

    @app.get(static_assets.SERVICE_WORKER_PATH)
    def service_worker() -> Response:
        if not assets.service_worker:
            raise HTTPException(status_code=404, detail="no service worker")
        # Scoped to the app's own mount point so it controls every page the app serves; the
        # header says so explicitly, which costs nothing and survives the file being moved.
        # Under a base path this is `/app/` — the browser fetches `/app/sw.js` (which the
        # proxy strips to this route) and the worker claims `/app/`, not the origin root it
        # would otherwise escape to. `no-cache` means the browser revalidates on every
        # navigation, so a fix to this file reaches an installed app immediately.
        return Response(
            assets.service_worker,
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": f"{base_path}/"},
        )

    @app.get(static_assets.ICONS_PREFIX + "{name}")
    def icon(name: str) -> Response:
        # `name` indexes a fixed table; it is never joined onto a path. Traversal, encoded
        # separators and absolute paths are therefore misses like any other unknown name.
        return _asset_response(assets.icons.get(name), "public, max-age=604800")

    return app


def _asset_response(asset: static_assets.Asset | None, cache_control: str) -> Response:
    if asset is None:
        raise HTTPException(status_code=404, detail="no such asset")
    return Response(
        asset.body, media_type=asset.media_type, headers={"Cache-Control": cache_control}
    )


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

    A plain HTML form POST triggers no CORS preflight, and the CSP's `form-action 'self'`
    only constrains forms *this* app serves — neither stops a foreign page from
    auto-submitting a run to a guessable hostname and burning a full 10–25-minute run.
    This matters more since D30, not less: Cloudflare Access sets a `CF_Authorization`
    cookie, so such a POST now arrives *authenticated*, as a real user, and would create
    a run they own. The app sets no session cookie of its own to hang a SameSite
    attribute on, so the request context itself is the only signal.

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
    """The run, or 404 — the single gate every per-run route passes through.

    An owner-less run is a 404 and not a 403: there is nobody it could be served to,
    so from the web layer's side it does not exist. It takes no viewer argument
    because reading is not owner-scoped — the middleware has already established that
    *somebody* is asking, and that is the whole requirement. `resume` adds the
    ownership check it needs on top.
    """
    if not registry.exists(run_id) and worker.status(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    summary = registry.summary(run_id, worker.active())
    if summary.owner is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return summary


def _sse(event: str, data: str) -> str:
    """SSE frames are newline-delimited, so every payload line needs its own `data:`."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{body}\n\n"
