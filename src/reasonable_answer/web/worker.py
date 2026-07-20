"""The job queue.

A run is 10–25 minutes of mostly-blocking model calls, so it cannot happen inside a
request. Submissions go on a queue; a small pool of threads drains it. The pool is
deliberately small: every concurrent run multiplies load on one LiteLLM proxy, and
the roster's local models are the bottleneck the whole design is built around.

The queue itself is in memory, but it is not the record of what is owed. Every job is
written to its run's `events.jsonl` before it is enqueued, so a process that dies
holding a full queue loses nothing a restart cannot rebuild — see `recover()`. Disk
stays the only source of truth, exactly as `registry` describes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import shutdown
from ..config import Config
from ..graph import GracefulStop, ResumeMismatch
from ..graph import run as run_graph
from ..store import RunStore

log = logging.getLogger(__name__)


@dataclass
class Job:
    run_id: str
    question: str
    seed: str | None
    resume: bool = False


class RunWorker:
    """Bounded background execution with a queue and a live-status map."""

    def __init__(
        self,
        config: Config,
        max_concurrent: int = 1,
        runner: Callable[..., dict] | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        self._config = config
        self._runner = runner or run_graph
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._status: dict[str, str] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()
        # Injectable so tests never touch the process-wide flag.
        self._stop = stop if stop is not None else shutdown.event()

        for n in range(max(1, max_concurrent)):
            # Not daemons: a daemon thread is truncated wherever it happens to be at
            # interpreter exit, which is the mid-node kill this whole module exists to
            # avoid. Safe only because `shutdown()` is bounded — the platform's SIGKILL
            # remains the real backstop.
            thread = threading.Thread(target=self._drain, name=f"ra-worker-{n}", daemon=False)
            thread.start()
            self._threads.append(thread)

    # ------------------------------------------------------------- submission

    def submit(self, question: str, seed: str | None = None) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        # Record the question and the queue entry up front, so the run is both
        # identifiable and *recoverable* the instant it is queued — before the graph has
        # written anything. Dying between these writes and the `put` below is fine: the
        # next boot finds the run on disk and re-enqueues it. Dying before them leaves an
        # orphan question.txt, which is also fine — the caller never got its redirect, so
        # nothing was promised.
        store = RunStore(self._config.runs_dir, run_id)
        store.question(question, seed)
        store.event("queued", attempt=1, auto=False)
        with self._lock:
            self._status[run_id] = "queued"
        self._queue.put(Job(run_id=run_id, question=question, seed=seed))
        log.info("queued %s", run_id)
        return run_id

    def resume(
        self,
        run_id: str,
        question: str,
        seed: str | None = None,
        *,
        auto: bool = False,
        attempt: int = 1,
    ) -> str:
        with self._lock:
            if run_id in self._status:
                return run_id  # already queued or running; resuming again would double-run
            self._status[run_id] = "queued"
        RunStore(self._config.runs_dir, run_id).event("queued", attempt=attempt, auto=auto)
        self._queue.put(Job(run_id=run_id, question=question, seed=seed, resume=True))
        return run_id

    # --------------------------------------------------------------- recovery

    def recover(self, registry: Any) -> list[str]:
        """Re-enqueue everything that was owed when the last process went away.

        Called at startup. A deploy SIGTERMs mid-run, the graph pauses at a node
        boundary, and the container comes back — nobody should have to notice, let alone
        click resume. Queued-but-never-started runs come back the same way.

        Runs are replayed oldest-first: `registry.list()` sorts newest-first for the UI,
        and inverting that makes recovery FIFO, matching the order they were accepted in.
        """
        if not shutdown.resume_on_boot():
            log.warning("RA_RESUME_ON_BOOT is off; interrupted runs stay parked")
            return []

        cap = self._config.max_resume_attempts
        recovered: list[str] = []
        for summary in reversed(registry.list(active=self.active())):
            if summary.status not in ("queued", "interrupted"):
                continue
            store = RunStore(self._config.runs_dir, summary.run_id)
            attempt = registry.consecutive_auto_resumes(summary.run_id) + 1
            if attempt > cap:
                # Deliberately no final.json: that file means "the graph reached a
                # terminal status", and the audit trail must never claim a verdict the
                # controller never issued. The registry infers `abandoned` from here.
                log.warning("%s hit the resume cap (%d); abandoning it", summary.run_id, cap)
                store.event("abandoned", reason="resume attempt cap", attempts=attempt - 1)
                continue
            self.resume(
                summary.run_id,
                summary.question,
                registry.seed(summary.run_id),
                auto=True,
                attempt=attempt,
            )
            recovered.append(summary.run_id)

        if recovered:
            log.info("recovered %d interrupted run(s): %s", len(recovered), ", ".join(recovered))
        return recovered

    # ------------------------------------------------------------------ state

    def status(self, run_id: str) -> str | None:
        with self._lock:
            return self._status.get(run_id)

    def active(self) -> dict[str, str]:
        with self._lock:
            return dict(self._status)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def shutdown(self, timeout: float | None = None) -> None:
        """Stop accepting work and wait for in-flight runs to reach a node boundary.

        `timeout` is a budget for the *pool*, not for each thread: joining N threads at
        N seconds each is an N×N wait that silently outlives the platform's grace period.

        Jobs still sitting in the queue are left alone on purpose. They are already on
        disk as `queued` events, so `recover()` collects them after the restart.
        """
        budget = timeout if timeout is not None else shutdown.grace_seconds() * 0.5
        self._stopping.set()
        self._stop.set()
        for _ in self._threads:
            self._queue.put(None)  # wakes idle threads immediately rather than at the poll
        deadline = time.monotonic() + budget
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        still_running = [t.name for t in self._threads if t.is_alive()]
        if still_running:
            # Not a leak to chase: the checkpointer bounds the damage to the node in
            # flight, and the platform is about to SIGKILL us anyway.
            log.warning("worker(s) %s did not stop within %.0fs", ", ".join(still_running), budget)

    # ----------------------------------------------------------------- worker

    def _drain(self) -> None:
        while not self._stopping.is_set():
            try:
                # Polling rather than blocking forever: a thread parked in an unbounded
                # get() never sees the stop flag, so shutdown used to wait out its whole
                # timeout and then abandon the thread.
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None or self._stopping.is_set():
                if job is not None:
                    self._queue.task_done()
                return
            with self._lock:
                self._status[job.run_id] = "running"
            started = time.time()
            try:
                self._runner(
                    self._config,
                    question=job.question,
                    seed=job.seed,
                    run_id=job.run_id,
                    stop=self._stop,
                )
                log.info("%s finished in %.0fs", job.run_id, time.time() - started)
            except GracefulStop:
                # Expected during a deploy. The graph already wrote its `pause` event and
                # the checkpoint is durable, so the next boot resumes from here.
                log.info("%s paused for shutdown after %.0fs", job.run_id, time.time() - started)
                return
            except ResumeMismatch:
                # The run's inputs no longer match its checkpoint — most often because a
                # deploy shipped a new roster under it. The guard is right to refuse, but
                # a refusal that leaves the run `interrupted` would be retried on every
                # boot until it burned the whole attempt cap, so land it somewhere final.
                log.warning("%s cannot resume under changed inputs; abandoning it", job.run_id)
                RunStore(self._config.runs_dir, job.run_id).event(
                    "abandoned", reason="question, seed, roster or budgets changed since this run started"
                )
            except Exception:
                # The graph writes its own terminal state and audit trail; anything
                # escaping to here is a crash, and the registry will show the run as
                # `interrupted` — which is resumable, not lost.
                log.exception("%s crashed", job.run_id)
            finally:
                with self._lock:
                    self._status.pop(job.run_id, None)
                self._queue.task_done()
