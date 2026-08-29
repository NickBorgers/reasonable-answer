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

The other direction — *not* surviving — is here too. Every join in this package is
bounded, and ``exit_process`` is what makes leaving bounded as well, so that a process
which has decided to die actually does (D-failed-boot-exits).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import sys
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


def lingering_threads() -> list[str]:
    """Non-daemon threads, other than this one, still alive right now."""
    current = threading.current_thread()
    return sorted(
        t.name
        for t in threading.enumerate()
        if t is not current and t.is_alive() and not t.daemon
    )


def exit_process(code: int) -> None:
    """Leave the process with ``code``, whatever is still running (D-failed-boot-exits).

    The worker and sweeper threads are non-daemon on purpose — a daemon thread is
    truncated wherever it happens to be at interpreter exit, which is the mid-node kill
    the whole design avoids. The price is that CPython's exit joins them **without a
    deadline**, so any one of them that outlives its own bounded join silently converts
    ``sys.exit`` into hanging forever.

    That is not hypothetical. A fail-closed boot — the proxy unreachable, so
    ``RefinementService.start`` raises — logged uvicorn's ``Application startup failed.
    Exiting.`` and then sat up for twenty-two minutes with nothing bound to port 8080,
    healthcheck failing on every interval and ``RestartCount=0``, because one idle
    ``ra-worker-0`` was enough to hold the interpreter open. `restart: unless-stopped`
    cannot help a container that never exits, so recovery needed a human.

    Called at the end of ``ra serve`` on every path, and a no-op on the ordinary one:
    by then the lifespan has already joined everything on its grace budget, so there is
    nothing left and the caller exits normally with atexit handlers and buffers intact.
    The hard exit is reserved for the case where a thread ignored that budget, where
    waiting longer buys nothing that the bounded join did not already try for.
    """
    lingering = lingering_threads()
    if not lingering:
        return
    log.warning(
        "exiting with status %d while %s still running; not waiting for %s",
        code,
        ", ".join(lingering),
        "them" if len(lingering) > 1 else "it",
    )
    # `os._exit` runs no atexit handler and flushes nothing, so flush by hand first —
    # the warning above is the only record of why this process died abruptly.
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        # A closed or broken stream must not be the thing that stops the exit.
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


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
