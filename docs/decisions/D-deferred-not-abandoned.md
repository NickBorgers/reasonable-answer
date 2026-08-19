## D-deferred-not-abandoned — a provider outage is not evidence against the run it stopped

**The finding.** `WorkerPool._drain` caught every exception out of `run_graph` as one case: log
`crashed`, leave the run `interrupted`, let the next boot re-enqueue it. `interrupted` is resumable,
and `max_resume_attempts` (3) bounds how many consecutive auto-resumes a run may make without
progressing before it is `abandoned` — the bound D-redeploy-survival introduced so that a run failing
*deterministically* cannot be retried forever.

A startup-validation failure is caught by that bound while being the one thing it was not written
for. Nothing in `build_runtime` reads the question or seed, so a refusal there is not evidence about
one run's inputs and applies equally to every queued run under the same deployment. Counting such a
refusal against each run's budget can therefore abandon the backlog without any run reaching graph
execution. `tests/test_shutdown.py` pins that accounting boundary separately from intake failures,
which do depend on a run's own inputs and still spend the cap.

**The decision.** `build_runtime` re-raises every fail-closed refusal as `StartupRefused`, and
`_drain` catches that separately from a crash. It writes a `deferred` event naming the reason, leaves
the run `interrupted` so the next boot still picks it up, and sends no owner notification — for the
same reason `GracefulStop` sends none: nothing has happened to the run that its owner can act on.
`consecutive_auto_resumes` then treats `deferred` as cancelling the attempt it belongs to.

**The line is structural, not a list of causes.** `StartupRefused` exists because catching plain
`ConfigError` in the worker would have been wrong, and quietly so: intake raises `ConfigError` too, for
a question over `max_question_chars`, a seed over `max_report_chars`, or a missing question. Those
depend on the run's own inputs, fail identically on every retry, and are precisely what the resume cap
is for — deferring them would retry a permanently-bad run forever. What separates the two is not the
kind of problem but where it was found: nothing `build_runtime` does reads the question or the seed,
so whatever it refuses it refuses for every queued run alike. Wrapping that one function is therefore
the whole rule, and it needs no taxonomy of causes to stay correct as new startup checks are added.
`StartupRefused` subtypes `ConfigError`, so every caller that already caught the base type — the CLI's
fail-closed exit among them — is unaffected.

Cancelling, rather than resetting to zero as a progress event does, is what keeps the cap honest
across a mixed history: three genuine crashes still abandon a run even if an outage happened to fall
between two of them. The registry reports the state distinctly — "the model roster was unreachable; it
retries automatically" rather than the generic resumable-crash note — because the cause is outside the
run and outside the user's reach, and a run parked by someone else's rate limit should not read like
one that died.

**Two budgets, both capped, because they answer different questions.** The first draft of this
decision made deferrals *unbounded*, on the reasoning that a deferral is nearly free — one startup
validation, seconds, zero tokens, once per container start — while discarding work owed because a
provider had a bad hour is not recoverable. The cheapness holds; the conclusion did not. QP7 requires
every loop to be capped, and an uncapped one has a failure mode the argument missed: a permanently
misconfigured deployment would accumulate runs that defer on every boot and never reach a terminal
state, so nothing ever asks anyone for help. A silent backlog is worse than a terminal one precisely
because it never surfaces. Weakening QP7 would also have needed new fetchable evidence under QP12,
and there is none — the right answer was not a retreat but a second cap.

So recovery now spends two separate budgets. `max_resume_attempts` (3) asks *is this run broken*, and
three failures answer it. `max_deferred_attempts` (20) asks *is the deployment still coming back*,
where an honest answer takes far longer than three restarts. A run that exhausts the second is
`abandoned` in exactly the same shape as one that exhausts the first: an event, never a `final.json`,
and a human can resume past it. Twenty deliberately gives deployment recovery more boot cycles than
the three-attempt run-failure budget while still terminating.

**What the audit trail may say about it.** The `deferred` event records `StartupRefused.code`, a
closed token (`startup_refused` / `roster_unreachable`), never `str(exc)`. The message is written to
the container log instead, which is reachable only by someone who can already read the deployment.
The distinction is load-bearing rather than tidy: `StartupRefused` wraps every `ConfigError`
`build_runtime` can raise, including proxy-reachability failures whose text carries the proxy base
URL and the upstream provider's own wording — and `/runs/<run_id>/audit.json` serves every event
verbatim to anyone holding a run id (D-id-as-credential). Persisting the message would have made a
run id a key to deployment state it has no business naming. A closed vocabulary also means a reader
of the audit trail can enumerate the possibilities, which free text never allows.

**What this deliberately does not do.** It does not retry inside the attempt, back off, or wait for a
provider — the deferral ends the attempt immediately and the next boot is the retry. It does not
change `ResumeMismatch`, which still abandons: that failure *is* about the run's inputs. It does not
distinguish a transient outage from a permanently misconfigured roster at the moment of failure; both
defer, and it is the deferral cap, not a classification, that eventually tells them apart. And it is
a property of `RunWorker._drain` only: a direct `ra run` has no registry lifecycle to move, so the
CLI catches the same `ConfigError` it always did, prints the full diagnostic, and exits `2` — the
operator is standing right there, which is the case the deferral exists to cover when nobody is.

**Invariants.** None of the six is in reach. This changes what a worker does with an exception and
how a registry counts attempts; no model call, prompt, critic assignment, severity, or controller rule
is touched.
