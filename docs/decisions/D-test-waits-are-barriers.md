## D-test-waits-are-barriers — a test waits on a barrier, never on a stopwatch

**The problem.** `tests/test_web.py` drives real runs through a real `RunWorker`, on a background
thread, behind the fake proxy. To see a finished run it polled `registry.final()` until a 20-second
deadline and then failed. Locally that wait resolves in well under a second, which is what made the
budget look generous: it is roughly sixty times the observed cost.

It is still a race, because nothing bounds the other side of it. The run is only *typically* instant
here — the proxy is fake, but the graph, the queue, the store and the notification all really run,
under coverage, on a shared runner, alongside every other test in the process. A deadline that is
sixty times the median is not a safety margin against that; it is a bet on the tail.

The bet lost on PR #197, and the loss did not stop at one test:

1. `test_web.py` timed out waiting for one run. Everything else in the suite passed — 1668 to 1.
2. `Tests (3.11)` therefore failed, so `PR Validation Required` concluded `failure`.
3. Every reviewer's guard polls that gate and refuses to review a SHA that did not validate
   (`docs/ci-pipeline.md`, "The reviewer guard, and what it waits for"). All five refused.
4. The judge found no reviewer artifacts, and fail-closed to `NO-GO` / `pipeline_error`.

So a wall-clock race in one test spent a whole review round and published a NO-GO on a change no
reviewer had read. Each step after the first is correct and deliberately fail-closed — the guard
must refuse an unvalidated SHA, and the judge must not read an empty panel as consent. That is
exactly why the flake is worth removing at its source rather than absorbing anywhere downstream: the
pipeline is built to amplify a red gate, so a test that can go red without a defect behind it is
expensive in a way its own runtime does not show.

**The decision.** A test that waits for background work waits on that work's own completion signal.
In the web suite that signal already existed and was already in use for notifications: the worker's
`queue.join()`, which returns exactly when the job has been marked done — after the final record is
written. `_wait_for_final` now takes the handle the test already holds (its `TestClient` or the app),
reaches the worker and the `runs_dir` through `app.state`, joins, and then reads the record it came
for. A slow runner makes the test slower instead of red.

The remaining timeout is a **deadlock backstop, not a budget**. It is deliberately far larger than
any expected wait, because nothing is supposed to approach it: reaching it means the worker never
marked the job done, which is a defect and not a slow afternoon. Its failure prints the tail of the
run's own `events.jsonl`, so the next one is diagnosed from the assertion rather than from expired
CI logs.

**What this does not claim.** Waits that are *about* elapsed time stay stopwatches, because for them
the clock is the subject: `test_shutdown.py`'s deadlines assert that a stop is noticed within a
bound, and `test_llm_retry.py` is about the backoff itself. The rule is not "no timeouts in tests" —
it is that a test which only wants the work to be *done* must not encode a guess about how long
being done takes.

**Invariants.** None. This changes how the test suite waits, not what the system does: no module
that builds a model's context, scores a finding, or decides a stop is touched, and no test's
assertions were weakened — the same runs are driven to the same finished state and checked the same
way. The offline "clone → run tests" promise is unchanged; nothing here reaches the network.
