"""Automatic retention sweeping.

Retention used to depend entirely on someone running `ra purge` (or wiring a cron to
it). A web server that accepts submissions but never reclaims disk grows without bound,
so the server runs its own periodic sweep: every `retention_sweep_interval_seconds` it
content-purges runs past `retention_days`, exactly as `purge --content-only` would.

The sweep is *content-only* on purpose. It drops the bulk — reports, critiques, the
final draft — while keeping each run's decision record, which the privacy posture keeps
longer than the artifacts (see docs/architecture.md and docs/decisions.md RC-007).
Full-directory removal stays the explicit, human `ra purge` escape hatch.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from ..store import sweep_expired

log = logging.getLogger(__name__)


class RetentionSweeper:
    """A stop-aware background thread that content-purges expired runs on a timer."""

    def __init__(
        self,
        runs_dir: Path,
        retention_days: int,
        interval_seconds: float,
        stop: threading.Event,
        skip: Callable[[], set[str]] | None = None,
    ) -> None:
        self._runs_dir = runs_dir
        self._retention_days = retention_days
        self._interval = interval_seconds
        # Shared with the worker/shutdown path: the same flag that stops runs stops the
        # sweep, so a deploy does not have to wait out a sleeping timer.
        self._stop = stop
        # Live runs must never be swept, however old their id looks. Defaults to "none
        # live" for callers that have no worker to consult.
        self._skip = skip or (lambda: set())
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._interval <= 0:
            log.info("retention sweep disabled (interval <= 0); purge stays manual")
            return
        # Not a daemon, for the same reason the worker threads are not: it must unwind
        # cleanly rather than be truncated mid-rmtree at interpreter exit.
        self._thread = threading.Thread(target=self._loop, name="ra-retention", daemon=False)
        self._thread.start()

    def sweep_once(self) -> list[str]:
        """One pass. Exposed so a caller (and the tests) can drive it directly."""
        try:
            purged = sweep_expired(self._runs_dir, self._retention_days, skip=self._skip())
        except OSError:
            # A transient filesystem error must not kill the loop; the next tick retries.
            log.exception("retention sweep failed; will retry next interval")
            return []
        if purged:
            log.info("retention sweep content-purged %d run(s): %s", len(purged), ", ".join(purged))
        return purged

    def _loop(self) -> None:
        # Sweep once at startup so a backlog that accumulated while the server was down
        # is reclaimed immediately, then settle into the interval. `Event.wait` returns
        # early the instant a stop is requested, so the interval never delays shutdown.
        while True:
            self.sweep_once()
            if self._stop.wait(self._interval):
                return

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
