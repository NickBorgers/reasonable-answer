"""The web interface.

Design notes worth keeping in mind while reading:

* **Every request carries an identity, and the app does not verify it.** It comes
  from a header set by Cloudflare Access or by `tailscale serve` (`identity.py`),
  which is only meaningful while the app's port is unreachable except through one of
  them. A caller who can reach the port directly can claim to be anyone (D-identity-header,
  docs/authentication.md).
* **Authentication is enforced by middleware, not by each route.** Every route but
  `/healthz` and the GETs under `/runs/` is behind it, including routes nobody has
  written yet — the failure mode of a per-route call is a new handler that forgets to
  make it. The exemption is method-scoped, so a new *write* is gated by default and
  only a new *read* under `/runs/` inherits the public rule (D-id-as-credential).
* **Ownership is per run and scopes the index, not each read.** You see your own runs
  listed; anyone who holds a run id can read that run, signed in or not — holding the
  id is the credential. Sharing a link is the intended way to show someone a report.
  Runs with no owner — written before ownership existed, or by `ra run` without
  `--owner` — are served to nobody.
* **Nothing public names a person.** A shared link reaches strangers, so the owner's
  address is kept off every route under `/runs/`: no byline on the run page, no
  `owner` field in `audit.json` (D-id-as-credential).
* **Showing reports to a human does not weaken the isolation design.** Blindness is
  about what enters a *model's* context. This UI is a window onto the audit trail,
  which is the whole reason the pipeline keeps one.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
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

from .. import export, ingest, shutdown
from ..config import Config, ConfigError
from ..llm import LLMClient
from ..store import CorruptRun
from . import assets as static_assets
from . import push
from .identity import resolve_identity
from .refine import RefinementService
from .registry import Registry, RunSummary
from .render import (
    normalize_base_path,
    render_index,
    render_index_rows,
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

#: Reading a run needs no identity: holding the id is the credential (D-id-as-credential). Everything
#: under this prefix is a pure disk read of one run's own audit trail — no worker, no
#: graph, no token cost — so a person can share the URL they are looking at and have it
#: open for anyone.
#:
#: The rule is **method-scoped**, and that is what keeps it narrow: every route that
#: spends tokens or changes state is a POST, so `POST /runs` (submit — no trailing slash,
#: so it does not match), `POST /runs/{id}/resume` and `POST /runs/{id}/again` all fall
#: through to the identity check unchanged, as do `/` and the app-shell assets. Matched
#: against the same `request.url.path` the proxy has already stripped `RA_ROOT_PATH` from,
#: exactly as `_UNAUTHENTICATED_PATHS` is (D-base-path). An owner-less run still 404s via
#: `_require`, so nothing that was unreadable before becomes readable here.
#:
#: A prefix means a *future* GET under `/runs/` is public the day it is written.
#: `tests/test_web.py::test_public_run_get_routes_are_the_expected_set` enumerates the
#: route table and fails on a new one, so widening this is a deliberate edit.
_PUBLIC_GET_PREFIX = "/runs/"

log = logging.getLogger(__name__)


class _StreamLimit:
    """A ceiling on simultaneously open SSE connections.

    A counter rather than a semaphore because the answer to "full" is to refuse, not to
    wait: a caller parked on a semaphore is an open connection too, which is the thing
    being limited. Guarded by a lock because `release` runs from a generator's `finally`
    on whichever task is unwinding, not necessarily the one that acquired.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._open = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._open >= self._limit:
                return False
            self._open += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._open = max(0, self._open - 1)

    @property
    def open(self) -> int:
        with self._lock:
            return self._open


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
    # the routes below stay unprefixed. See D-base-path.
    base_path = normalize_base_path(os.environ.get("RA_ROOT_PATH"))
    # The prefix for the URLs a *reader* uses: the run page and everything hanging off it.
    # Split from `base_path` because the two live behind different doors (D-id-as-credential). The edge
    # gates `/app/` with Cloudflare Access and leaves `/runs/` open, so a run page emitted
    # under `/app` is a link only a signed-in person can open — which is the whole thing
    # this exists to fix. Setting `RA_PUBLIC_ROOT_PATH=/` puts every run URL at the origin
    # root, so the URL in the reader's address bar is already the one they can share.
    #
    # Unset means "the same door as everything else": it falls back to `base_path`, so a
    # deployment that has not opened a public path at its edge keeps today's behaviour
    # exactly, and dev and the tailnet (where both are empty) are unaffected.
    public_base = (
        normalize_base_path(os.environ.get("RA_PUBLIC_ROOT_PATH"))
        if os.environ.get("RA_PUBLIC_ROOT_PATH") is not None
        else base_path
    )
    concurrent = max_concurrent or int(os.environ.get("RA_MAX_CONCURRENT_RUNS", "1"))
    # How many progress streams may be open at once, across everybody. One reader needs
    # one; the number exists because the route is anonymous (D-id-as-credential) and an open connection
    # is the only cost a stranger can impose here.
    _streams = _StreamLimit(int(os.environ.get("RA_MAX_LIVE_STREAMS", "32")))
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
    # Notifications (D-stop-notification). Built before the worker because the worker holds the notifier:
    # the send happens on the worker thread the moment a run stops, which is the only place
    # that knows a run stopped without a browser having to be watching. `push_key` is the
    # public half, embedded in the index for `pushManager.subscribe`; empty when the feature
    # is off, which is what makes the page byte-identical to a build without it.
    push_store: push.PushStore | None = None
    notifier: push.Notifier | None = None
    push_key = ""
    if config.push.enabled:
        if not config.push.subject:
            # Fail at boot, not when the first run finishes. `py_vapid` refuses to sign
            # without a `sub` claim, and that exception would be raised inside `_deliver`'s
            # best-effort `except` — so the symptom would be notifications that silently
            # never arrive, which is the failure this check exists to convert into a
            # startup error.
            raise RuntimeError(
                f"push.enabled is true but ${config.push.subject_env} is unset; it must be "
                "a mailto: address or a bare https://host — the VAPID contact (RFC 8292). "
                "It is an env var rather than a roster key so a committed config never "
                "carries a personal address."
            )
        push_store = push.PushStore(
            config.runs_dir / push.SUBSCRIPTIONS_FILE,
            max_per_identity=config.push.max_subscriptions_per_identity,
        )
        pem, push_key = push.load_or_create_vapid(config.runs_dir / push.VAPID_FILE)
        notifier = push.Notifier(
            store=push_store,
            vapid_pem=pem,
            subject=config.push.subject,
            # Run URLs live on the reader-facing base (D-id-as-credential), so a notification opens the
            # same link every other run reference in the app uses.
            public_base=public_base,
            endpoint_hosts=config.push.endpoint_hosts,
            timeout_seconds=config.push.timeout_seconds,
        )
    worker = worker or RunWorker(config, max_concurrent=concurrent, notifier=notifier)
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
        # user's first pause after typing (D-question-refinement) -- so this runs before recovery, which is
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
    app.state.public_base = public_base
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
        `request.state.viewer` is a real identity — except the GETs under `/runs/`,
        which are public (D-id-as-credential) and where it may be None.

        `HTTPException` is not available here — it is raised past the exception
        middleware that would turn it into a response — so the refusal is returned
        directly. Nothing is logged about the failed attempt: the header is
        attacker-controlled, and a rejected request has no identity to attribute.
        """
        if request.url.path in _UNAUTHENTICATED_PATHS:
            return await call_next(request)
        if request.method == "GET" and request.url.path.startswith(_PUBLIC_GET_PREFIX):
            # Reading a run is public (D-id-as-credential). The identity is still resolved rather than
            # forced to None, because the same page is reachable through the gated door
            # too and a viewer we happen to know is not worth throwing away — but None is
            # an ordinary value here, not a refusal, so every handler under `/runs/` must
            # treat `request.state.viewer` as optional. None of them scope a read by it.
            request.state.viewer = resolve_identity(request, config.auth)
            return await call_next(request)
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
            public_base=public_base,
            viewer=request.state.viewer,
            vapid_key=push_key,
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

        # `refine_offer_id`/`refine_selected` are claims, not evidence (D-question-refinement): resolved
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

        run_id = _enqueue(
            question,
            ingested.markdown if ingested else None,
            identity=request.state.viewer,
            seed_format=ingested.format if ingested else None,
            seed_source=ingested.source if ingested else None,
            seed_warnings=ingested.warnings if ingested else (),
            refinement=refinement,
        )
        return RedirectResponse(url=f"{public_base}/runs/{run_id}", status_code=303)

    def _enqueue(question: str, seed: str | None, **kwargs: Any) -> str:
        """`worker.submit` with the two saturation refusals turned into 429s.

        Shared by `submit` and `again` so the second one cannot quietly skip a limit the
        first one enforces — the rate limiter is the only thing standing between one
        person and the token budget, and "ask this again" is a one-click way to spend it.
        """
        try:
            return worker.submit(question, seed, **kwargs)
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

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str, request: Request, response: Response) -> str:
        # A run page is a snapshot of something still moving. Installed as a standalone app
        # this leans on the HTTP cache and the back-forward cache far harder than a browser
        # tab does, and showing a finished run as still running is the one output this
        # interface must not produce. The service worker refuses to cache these too; this is
        # the same rule stated at the layer below it.
        response.headers["Cache-Control"] = "no-store"
        summary = _require(registry, worker, run_id)
        # The body is not rendered here — only whether there is one, which decides the
        # link to the report and whether there is a verdict to state.
        report = registry.report(run_id)
        _, prov = _provenance(registry, summary, run_id)
        record = export.provenance_html(prov) if report else ""
        return render_run(
            summary=summary,
            timeline=registry.timeline(run_id),
            report=report,
            lens_names=registry.lens_names(),
            record=record,
            base_path=base_path,
            public_base=public_base,
            # Only for a signed-in caller. This page is anonymous by design (D-id-as-credential), and a
            # stranger reading a shared run has no runs to be notified about and could not
            # subscribe anyway — the route is a gated write.
            vapid_key=push_key if request.state.viewer else "",
        )

    @app.post("/runs/{run_id}/again")
    def again(run_id: str, request: Request) -> RedirectResponse:
        """Start a fresh run of this run's question.

        The recovery path for a run that stopped without a verdict. It replaces the
        resume button, which could not survive the run page becoming public: with no
        identity on `/runs/<id>` the page cannot tell the owner from a stranger, and a
        resume offered to everyone is an invitation to a 404. Asking again needs no such
        knowledge — anyone signed in may spend their own tokens on their own new run,
        which they could equally do by retyping the question on the index.

        Nothing is read from the request but the run id: the question and the seed come
        off disk, so no client-supplied text reaches a model context and the seed does
        not have to ride into the DOM in a hidden field to get back here.

        A *new* run, not a continuation — so it is owned by whoever asked, counts against
        their rate limit, and leaves the original run exactly as it is.
        """
        _reject_cross_site(request)
        summary = _require(registry, worker, run_id)
        # Only once it has stopped. Re-asking a live question is a duplicate of work
        # already in flight, and the 409 says so rather than silently spending twice.
        if summary.is_live:
            raise HTTPException(status_code=409, detail=f"run is {summary.status}; it has not stopped")
        # `seed.md` holds the markdown the original run was given, already converted --
        # the same bytes `resume` reads back, so the new run starts from the same seed
        # without re-fetching a URL that may no longer resolve.
        seed = registry.seed(run_id)
        new_id = _enqueue(
            summary.question,
            seed,
            identity=request.state.viewer,
            seed_format="markdown" if seed else None,
            seed_source=f"run:{run_id}" if seed else None,
        )
        return RedirectResponse(url=f"{public_base}/runs/{new_id}", status_code=303)

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
        return RedirectResponse(url=f"{public_base}/runs/{run_id}", status_code=303)

    @app.post("/refine")
    def refine(request: Request, question: str = Form(...)) -> JSONResponse:
        # Same-origin enforcement first, uniform with every other state-changing route --
        # explaining an exception to this rule costs more than just applying it here too.
        _reject_cross_site(request)
        if not refiner.enabled:
            # The endpoint does not exist when the feature is off (D-question-refinement).
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

    # ---------------------------------------------------------- notifications

    # Registered only when the feature is on, so a deployment with `push.enabled: false`
    # grows no new surface at all rather than two routes that always refuse.
    #
    # Top level, deliberately *not* under `/runs/` — subscribing attaches a device to an
    # identity, and D-id-as-credential opens `/runs/` to anonymous readers. A subscribe endpoint there
    # would let anyone holding a run id register their own phone against this app; the
    # method guard in `authenticate` would still refuse a `POST`, but siting a write route
    # inside the public read prefix and relying on that is the wrong side of the rule.
    if config.push.enabled and push_store is not None:

        @app.post("/push/subscribe")
        async def push_subscribe(request: Request) -> JSONResponse:
            _reject_cross_site(request)
            store = push_store
            assert store is not None  # narrowed by the enclosing `if`
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="expected a JSON body") from None
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="expected a JSON object")
            keys = body.get("keys")
            if not isinstance(keys, dict):
                raise HTTPException(status_code=400, detail="missing subscription keys")
            endpoint = body.get("endpoint")
            p256dh = keys.get("p256dh")
            auth = keys.get("auth")
            if not all(isinstance(v, str) and v for v in (endpoint, p256dh, auth)):
                raise HTTPException(status_code=400, detail="incomplete subscription")
            # The SSRF boundary. This string is chosen by the browser and the server will
            # POST to it, so it is checked here and again before every send.
            try:
                push.validate_endpoint(endpoint, config.push.endpoint_hosts)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            store.add(
                request.state.viewer, endpoint, p256dh, auth, now=time.time()
            )
            return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

        @app.post("/push/unsubscribe")
        async def push_unsubscribe(request: Request) -> JSONResponse:
            _reject_cross_site(request)
            store = push_store
            assert store is not None
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="expected a JSON body") from None
            endpoint = body.get("endpoint") if isinstance(body, dict) else None
            if not isinstance(endpoint, str) or not endpoint:
                raise HTTPException(status_code=400, detail="missing endpoint")
            # Scoped to the caller: an endpoint may only be forgotten by the identity it is
            # stored under, so holding somebody else's endpoint string does not let you turn
            # their notifications off.
            store.remove(request.state.viewer, endpoint)
            return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------- fragments

    @app.get("/runs-table", response_class=HTMLResponse)
    def runs_table(request: Request, response: Response) -> str:
        """The index's runs table, re-rendered (D-self-refreshing-index).

        Owner-scoped exactly like the index it belongs to, and gated exactly like it: the
        path is deliberately `/runs-table` and not `/runs/table`, because everything under
        `/runs/` answers an anonymous caller (D-id-as-credential) and this is a per-viewer list. The
        trailing-slash detail matters — `_PUBLIC_GET_PREFIX` is the string `"/runs/"`, so a
        sibling name cannot fall inside it by accident.
        """
        response.headers["Cache-Control"] = "no-store"
        runs = registry.list(active=worker.active(), owner=request.state.viewer)
        return render_index_rows(runs, public_base)

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

        Anonymous since D-id-as-credential, which changes what an open connection costs: the ceiling
        used to be the number of people who could sign in, and is now the number of
        people who hold a run id. Nothing here writes — `Registry` has no write path at
        all, and the only worker call is `active()`, a lock-guarded dict copy — so the
        exposure is connections held open, not state changed or tokens spent. Two things
        bound it: `_streams` caps how many run at once, and the loop now exits as soon as
        the run is not live.
        """
        _require(registry, worker, run_id)
        if not _streams.acquire():
            # 503 rather than a queue: a stream is a live view of something already
            # visible on the page, and a reader who is turned away can reload.
            raise HTTPException(
                status_code=503,
                detail="too many live connections; the page still refreshes on reload",
                headers={"Retry-After": "5"},
            )

        async def events() -> Any:
            seen = 0
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    # uvicorn drains connections *before* running lifespan shutdown, and
                    # this generator otherwise only ends when the client leaves or the run
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
                    # Not live is the whole exit condition. It used to also require a
                    # `final.json`, which meant a run that stopped *without* one — a crash,
                    # or `abandoned` — polled this file every second for as long as the tab
                    # stayed open, and nothing would ever end it. Those are exactly the
                    # states the page most needs to repaint into.
                    if not summary.is_live:
                        yield _sse("done", summary.status)
                        return
                    await asyncio.sleep(1.0)
            finally:
                _streams.release()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ----------------------------------------------------------------- assets

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    def report_page(run_id: str, request: Request) -> str:
        summary = _require(registry, worker, run_id)
        report = registry.report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="this run has not produced a report yet")
        # The page a reader prints is the page a recipient downloads: same review
        # record, same stylesheet, so `Save as PDF` and `Download .html` agree. That
        # makes this page durable too, so it gets the same honesty about an unreadable
        # record — it says so rather than printing a status nothing supports.
        final, prov = _provenance(registry, summary, run_id)
        # Copy markdown puts the export document — report + review record — on the
        # clipboard, the same bytes `export.md`/`Download .md` serve (D-verdict-attached). An unreadable
        # record cannot be exported as a file (the route 409s), but the page still
        # renders, so the copy mirrors what the page shows: the record as unreadable.
        return render_report(
            summary,
            report,
            final,
            record=export.provenance_html(prov),
            print_header=export.print_header_html(prov),
            copy_markdown=export.export_markdown(
                summary.question,
                report,
                final,
                run_id,
                unreadable=prov.status == export.UNREADABLE_RECORD,
            ),
            base_path=base_path,
            public_base=public_base,
            vapid_key=push_key if request.state.viewer else "",
        )

    @app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def report_markdown(run_id: str) -> str:
        """The shipped artifact, byte for byte. Anything that hashes or diffs a report
        wants this route, not `export.md`."""
        _require(registry, worker, run_id)
        report = registry.report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="this run has not produced a report yet")
        return report

    @app.get("/runs/{run_id}/export.md")
    def export_md(run_id: str) -> PlainTextResponse:
        summary, report, final = _exportable(registry, worker, run_id)
        return PlainTextResponse(
            export.export_markdown(summary.question, report, final, run_id),
            media_type="text/markdown; charset=utf-8",
            headers=_attachment(export.export_filename(summary.question, run_id, "md")),
        )

    @app.get("/runs/{run_id}/export.html")
    def export_html(run_id: str) -> HTMLResponse:
        summary, report, final = _exportable(registry, worker, run_id)
        return HTMLResponse(
            export.export_html(summary.question, report, final, run_id),
            headers=_attachment(export.export_filename(summary.question, run_id, "html")),
        )

    @app.get("/runs/{run_id}/audit.json")
    def audit(run_id: str) -> dict[str, Any]:
        """The whole audit trail: summary, verdict, every event.

        Public since D-id-as-credential, which is the point — the trail is the reason the pipeline
        keeps one, and a shared link that cannot be checked is not much of a claim.
        `owner` is the one field held back: it is an email address, it is not evidence
        about the run, and a link shared with a stranger should not hand them the
        sender's address. Nothing else here names a person.
        """
        _require(registry, worker, run_id)
        summary = dict(registry.summary(run_id, worker.active()).__dict__)
        summary.pop("owner", None)
        return {
            "summary": summary,
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
    This matters more since D-identity-header, not less: Cloudflare Access sets a `CF_Authorization`
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


def _exportable(
    registry: Registry, worker: RunWorker, run_id: str
) -> tuple[RunSummary, str, dict[str, Any] | None]:
    summary = _require(registry, worker, run_id)
    report = registry.report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="this run has not produced a report yet")
    try:
        final = registry.final_strict(run_id)
    except CorruptRun as exc:
        # Refusing beats shipping a file that states a verdict the record cannot
        # support. 409 rather than 500: nothing failed here, the run is in a state
        # that has no honest export. The exception names the file it could not parse,
        # which is for the operator reading logs, not for the response body.
        log.warning("run %s has an unreadable record: %s", run_id, exc)
        raise HTTPException(
            status_code=409,
            detail="this run's record cannot be read, so no export can state its verdict",
        ) from exc
    return summary, report, final


def _provenance(
    registry: Registry, summary: RunSummary, run_id: str
) -> tuple[dict[str, Any] | None, export.Provenance]:
    """The review record for a page. Pages survive an unreadable record — they show it
    as unknown — where an export refuses outright."""
    try:
        final = registry.final_strict(run_id)
    except CorruptRun:
        return None, export.provenance(summary.question, None, run_id, unreadable=True)
    return final, export.provenance(summary.question, final, run_id)


def _attachment(filename: str) -> dict[str, str]:
    """`export_filename` restricts its output to `[a-z0-9.-]`, so no quoting or
    RFC 5987 encoding is needed — and no request-derived text can reach the header
    unfiltered."""
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _sse(event: str, data: str) -> str:
    """SSE frames are newline-delimited, so every payload line needs its own `data:`."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event}\n{body}\n\n"
