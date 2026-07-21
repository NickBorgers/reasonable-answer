"""Cooperative shutdown.

This process is continuously deployed: it gets a SIGTERM, some grace time, and then
a SIGKILL, all decided outside its own locus of control. A run is 10-25 minutes of
mostly-blocking model calls, so "just finish first" is not on the table.

The design leans on the checkpointer rather than on the grace period. LangGraph
persists state at every node boundary, so a hard kill costs at most the node that was
in flight — never the run. That makes the grace window an *optimisation*: it buys the
chance to land an in-flight node instead of re-paying for it after the restart. It is
best-effort by construction, and nothing here should be read as a guarantee.

Two knobs come from the platform, because the platform owns them:

* ``RA_SHUTDOWN_GRACE_SECONDS`` — how long SIGTERM-to-SIGKILL actually is. Every
  internal deadline derives from it, so retuning the platform retunes the process
  instead of silently inverting a hardcoded constant.
* ``RA_RESUME_ON_BOOT`` — set to 0 to stop the boot-time auto-resume, for an operator
  debugging a crash loop.
"""

from __future__ import annotations

import logging
import os
import signal
import threading

log = logging.getLogger(__name__)

DEFAULT_GRACE_SECONDS = 120.0

_STOP = threading.Event()


def event() -> threading.Event:
    """The process-wide stop flag.

    Prefer passing this explicitly into anything that watches it — a module global is
    convenient at the signal boundary and a liability everywhere else, tests most of all.
    """
    return _STOP


def stop_requested() -> bool:
    return _STOP.is_set()


def request_stop(reason: str) -> None:
    if not _STOP.is_set():
        log.info("shutdown requested (%s); finishing the current node", reason)
    _STOP.set()


def reset() -> None:
    """Tests only. A module-global Event otherwise leaks between them."""
    _STOP.clear()


def grace_seconds() -> float:
    """The SIGTERM-to-SIGKILL budget the platform gives us."""
    raw = os.environ.get("RA_SHUTDOWN_GRACE_SECONDS")
    if not raw:
        return DEFAULT_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning("RA_SHUTDOWN_GRACE_SECONDS=%r is not a number; using %s", raw, DEFAULT_GRACE_SECONDS)
        return DEFAULT_GRACE_SECONDS
    if value <= 0:
        log.warning("RA_SHUTDOWN_GRACE_SECONDS=%r is not positive; using %s", raw, DEFAULT_GRACE_SECONDS)
        return DEFAULT_GRACE_SECONDS
    return value


def resume_on_boot() -> bool:
    return os.environ.get("RA_RESUME_ON_BOOT", "1").strip().lower() not in ("0", "false", "no")


def install_handlers() -> None:
    """Handle SIGTERM/SIGINT ourselves.

    For ``ra run`` only. ``uvicorn.run()`` installs its own handlers and would overwrite
    these, so ``ra serve`` hooks the lifespan instead — see ``web/app.py``.

    This matters more than it looks in a container: ``ra`` is PID 1, and PID 1 has no
    default disposition for SIGTERM. Without a handler the signal is simply discarded
    and docker waits out the full grace period before SIGKILLing.
    """

    def handle(signum: int, _frame: object) -> None:
        request_stop(signal.Signals(signum).name)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle)
        except ValueError:  # pragma: no cover - not on the main thread
            log.debug("cannot install a %s handler off the main thread", sig)
