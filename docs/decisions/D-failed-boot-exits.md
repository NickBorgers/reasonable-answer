## D-failed-boot-exits — a boot that fails closed leaves the process, so the restart policy can retry it

**The finding.** Fail-closed startup did everything right except the last step. On 2026-08-23 the
host rebooted, `reasonable-answer` reached the LiteLLM proxy before it was serving, and
`RefinementService.start()` → `LLMClient.resolve_identities` → `_fetch_model_info` raised
`ConfigError: fail closed: cannot reach the LiteLLM proxy: HTTP Error 502: Bad Gateway`. Starlette
treated it as fatal, uvicorn logged `Application startup failed. Exiting.` — and the process did not
exit. The container stayed `Up` for twenty-two minutes with `RestartCount=0`, forty-four consecutive
healthcheck failures, and `ConnectionRefusedError` on every one of them, because nothing had ever
bound port 8080. The proxy was healthy again within a couple of minutes. Recovery took a human
typing `docker restart`.

The issue ([#192][issue]) read the gap as "something below the lifespan boundary swallows the
failure" and proposed `sys.exit(1)` after uvicorn returns. That diagnosis is wrong in a way worth
recording, because the obvious fix does nothing: uvicorn 0.51.0 defines startup failure as status 3
and [calls `sys.exit(STARTUP_FAILURE)` when `server.started` is false][uvicorn-startup]. `SystemExit`
was raised, and it was not caught. The process still did not die.

**It is the threads.** `create_app()` constructs `RunWorker` before the lifespan hook ever runs, and
`RunWorker.__init__` starts its drain threads immediately, **non-daemon by deliberate choice** — a
daemon thread is truncated wherever it happens to be at interpreter exit, which is precisely the
mid-node kill the checkpointer design exists to avoid. That choice is safe only under a premise the
module states in a comment: `shutdown()` is always called, and it is bounded.

A lifespan whose startup half raises breaks that premise. In Starlette 1.3.1 the lifespan context is
entered before startup is marked complete, and an exception from that entry goes directly to the
startup-failed branch rather than the successful shutdown path ([source][starlette-lifespan]).
`yield` never happens, so `worker.shutdown()` is never called — and CPython 3.14's exit-time thread
shutdown [joins each non-daemon thread with timeout `-1`][cpython-thread-shutdown]. One idle
`ra-worker-0` polling an empty queue every 500 ms is enough. `sys.exit(3)` unwinds the main thread
and then blocks forever, which is exactly what the logs showed: the fatal line printed, and nothing
after it.

This is the failure mode `restart: unless-stopped` cannot help with. Docker restarts a container
that *exits*; a hung one is indistinguishable from a working one at the process level, and the
healthcheck's only power is to mark it unhealthy. So the deployment's answer to a transient
dependency outage — wait a few seconds and boot again — was unreachable for the whole class of
outages that fail closed at boot.

**The decision.** Failing closed is a decision to *die*, and dying is not complete until the process
is gone. Two changes, at the two levels where the premise broke.

1. **A failed startup unwinds what it started** (`web/app.py`). The lifespan's startup half is
   wrapped, and on any exception it runs the same `stop_background()` teardown the SIGTERM path
   runs — `request_stop`, `worker.shutdown()`, `refiner.shutdown()`, `sweeper.join()` — before
   re-raising. That restores the module's own premise rather than working around it, and it costs
   nothing: the failure is in the first startup step, so the worker's queue is empty and its threads
   are idle. A teardown that itself raises is logged and swallowed, because the refusal is the news.
   This is also the fix that keeps the *ordinary* exit ordinary — atexit handlers, buffers, and
   `logging.shutdown()` all run as they always did.

2. **Leaving is bounded, like every join already is** (`shutdown.exit_process`, called from
   `cli.serve` on every path out of `uvicorn.run`). By the time uvicorn returns, every bounded join
   this process knows how to do has been done. A non-daemon thread still alive at that point ignored
   its budget, and CPython's unbounded wait buys nothing the budget did not already try for — so
   `exit_process` names the threads in a WARNING, flushes, and `os._exit`s with uvicorn's own status.
   With nothing stranded it returns and changes nothing, which is the common case.

Layer 1 alone would fix the incident. Layer 2 exists because layer 1 cannot cover every shape of the
same bug: `sweeper.start()` raising *after* `worker.recover()` has enqueued real work leaves threads
mid-run, where the bounded join is allowed to time out and log `worker(s) … did not stop`; a failure
inside `create_app()` itself never reaches the lifespan at all; and any future component that starts
a thread before the hook reintroduces the incident exactly. Layer 2 makes the process-level property
— *`ra serve` terminates* — hold independently of which component is at fault, which is the property
the platform actually depends on.

**Exit status is uvicorn's, not ours.** A fail-closed boot exits **3**, the `STARTUP_FAILURE` uvicorn
already chose; anything else that kills the boot exits 1. Only the *nonzero*-ness is load-bearing for
the restart policy, but inventing a status where a meaningful one already exists would make the two
disagree for no gain. The graceful SIGTERM path is untouched and still ends at 143 (uvicorn re-raises
the signal after draining), which is the supervisor-legible way to say "stopped because it was told
to" and is what distinguishes a deploy from a crash.

**This is not a retry, and deliberately so.** Nothing here waits for the proxy, backs off, or boots
degraded. The app trusts an identity header it does not verify (D-identity-header), so coming up
unauthenticated or half-configured is the one outcome worse than being down — the issue says so and
it is right. A supervisor already implements restart-with-backoff correctly, and it is the component
that can observe the whole container. The fix is to let it do its job, not to reimplement it inside
the process.

**Not the same thing as `deferred`.** D-deferred-not-abandoned covers a `StartupRefused` raised
*inside a run*, on a process that is already serving: the run is put back, the deployment stays up,
and nobody's backlog is abandoned over one outage. This covers a refusal raised *at boot*, where
there is no serving process to preserve and nothing to put back. The two are complementary — the
same proxy outage produces a deferral if it happens mid-life and an exit if it happens at boot — and
neither costs work: the boot teardown reuses `worker.shutdown()`, which leaves queued jobs on disk
for `recover()` (`test_a_failed_boot_leaves_queued_work_owed_rather_than_consumed`).

**Invariants.** None of the six pipeline-core invariants is in reach. Nothing here builds a model's
context, selects an author or a critic, scores a finding, or decides a stop; the modules touched are
the process entry point, the ASGI lifespan and the cooperative-shutdown helpers. The one *safety*
property in the area — a node is never truncated mid-flight — is preserved rather than traded away:
the worker threads stay non-daemon, the teardown is the same bounded `shutdown()` that already
existed, and the hard exit is reachable only after that budget has expired, which is a moment at
which the platform's SIGKILL was the incumbent outcome anyway.

**What is given up.** A thread that misses its budget now loses its remaining work with no further
warning beyond the logged WARNING naming it. The checkpointer bounds that to the node in flight,
which is the same bound the platform's SIGKILL already imposed, so nothing new is at risk — but the
process no longer waits indefinitely on the off-chance that a straggler finishes.

**Verification.** `tests/test_shutdown.py` covers both layers offline: the lifespan teardown (worker
threads and sweeper gone, refiner stopped, queued work still owed on disk), `exit_process` as a no-op
when nothing lingers, and — in a subprocess, the only place a process exit is observable — a stranded
non-daemon thread failing to veto the exit, plus the end-to-end regression itself: `ra serve` with a
refine service that cannot reach the proxy logs `Application startup failed` and is *gone*, nonzero,
well inside the timeout that used to expire.

[issue]: https://github.com/NickBorgers/reasonable-answer/issues/192
[uvicorn-startup]: https://github.com/Kludex/uvicorn/blob/e4d0b05eb8c6459b7ba27ad13a2c2f4f8d4ece50/uvicorn/main.py#L611-L629
[starlette-lifespan]: https://github.com/Kludex/starlette/blob/8ebffd0678570ddd5d5bb11c6f3c3c7fd4682ab9/starlette/routing.py#L629-L654
[cpython-thread-shutdown]: https://github.com/python/cpython/blob/323c59a5e348347be2ce2b7ea55fcb30bf68b2d3/Modules/_threadmodule.c#L2351-L2389
