"""The job queue.

A run is 10–25 minutes of mostly-blocking model calls, so it cannot happen inside a
request. Submissions go on a queue; a small pool of threads drains it. The pool is
deliberately small: every concurrent run multiplies load on one LiteLLM proxy, and
the roster's local models are the bottleneck the whole design is built around.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from ..config import Config
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
    ) -> None:
        self._config = config
        self._runner = runner or run_graph
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._status: dict[str, str] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()

        for n in range(max(1, max_concurrent)):
            thread = threading.Thread(target=self._drain, name=f"ra-worker-{n}", daemon=True)
            thread.start()
            self._threads.append(thread)

    # ------------------------------------------------------------- submission

    def submit(self, question: str, seed: str | None = None) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        # Record the question up front so the run is identifiable in the list the
        # instant it is queued, before the graph has written anything.
        store = RunStore(self._config.runs_dir, run_id)
        store.question(question, seed)
        with self._lock:
            self._status[run_id] = "queued"
        self._queue.put(Job(run_id=run_id, question=question, seed=seed))
        log.info("queued %s", run_id)
        return run_id

    def resume(self, run_id: str, question: str, seed: str | None = None) -> str:
        with self._lock:
            if run_id in self._status:
                return run_id  # already queued or running; resuming again would double-run
            self._status[run_id] = "queued"
        self._queue.put(Job(run_id=run_id, question=question, seed=seed, resume=True))
        return run_id

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

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)

    # ----------------------------------------------------------------- worker

    def _drain(self) -> None:
        while not self._stopping.is_set():
            job = self._queue.get()
            if job is None:
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
                )
                log.info("%s finished in %.0fs", job.run_id, time.time() - started)
            except Exception:
                # The graph writes its own terminal state and audit trail; anything
                # escaping to here is a crash, and the registry will show the run as
                # `interrupted` — which is resumable, not lost.
                log.exception("%s crashed", job.run_id)
            finally:
                with self._lock:
                    self._status.pop(job.run_id, None)
                self._queue.task_done()
