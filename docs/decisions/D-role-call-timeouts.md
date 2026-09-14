## D-role-call-timeouts — writers and critics get their own call timeout

**The finding.** Every model call attempt shared one timeout, `budgets.timeout_seconds` (300s in
the deployment roster). On 2026-09-14 three days of LiteLLM request records (2026-09-11 to 09-14,
Loki) showed that one value fits neither role. The records are useful here for a specific reason:
the upstream keeps generating after our client hangs up. So each record holds the time a call
*would* have taken with no client timeout.

* **Writers were cut off while still working.** deepseek-v4-flash draft rounds that wrote more than
  16k tokens took up to 1,150s. On one upstream host the model ran at a median 66 tok/s, and on
  another at 207 tok/s. 36 of its calls finished upstream after our client had given up at 300s.
  Each of those was billed, thrown away, and asked for again.
* **Slow critic calls were runaways.** In the run windows, 45 gemma4 attempts ran past 180s. All
  45 either stopped at an output-token cap (8,192 or 16,384 tokens) or failed. No healthy gemma4
  call took longer than 180s. A critique pass waits for its slowest critic, so each runaway held
  the whole pass for the full 300s.
* **The other critics are fast.** glm-5.2 and critic-sized deepseek-v4-flash calls have a p95
  near 80–90s. Only 8 of 875 of their attempts took longer than 180s.

A replay of those durations against candidate timeouts (three attempts per call, each attempt an
independent draw from the model's recorded durations, a capped output counted as failed) gave:

| calls | timeout | mean wait | p95 wait | call fails every attempt |
|---|---|---|---|---|
| writer-sized deepseek-v4-flash | 300s | 244s | 723s | 2.5% |
| | 900s | 250s | 721s | 0% |
| gemma4 critic | 300s | 38s | 302s | 0.11% |
| | 180s | 26s | 183s | 0.11% |
| glm-5.2, critic-sized deepseek-v4-flash | 120s–1200s | 27–29s | 80–89s | 0% |

**The decision.** A new top-level config section, `call_timeouts`, overrides the timeout for one
call attempt by role:

* `writer_seconds` bounds each attempt of the writer's draft call, including every tool round.
* `critic_seconds` bounds each attempt of the critique call, its repair turns, and the claim checks
  that run under the evidence critic's slot.
* Every other call keeps `budgets.timeout_seconds`. That covers the support manifest, dispute
  elicitation, the arbiter, the orchestrator, and startup probes. The data above does not cover
  those calls separately, so they are not changed.
* `None`, the code default, keeps the client default. A config without the section behaves as
  before, and `config/roster.default.yaml` does not set it.

The deployment roster sets `writer_seconds: 900` and `critic_seconds: 180`.

* **900s for writers** is the lowest value at which no replayed writer call failed every attempt.
  It sits above the slowest recorded call on the slow host (1,150s occurred once and is covered by
  the retry). Mean wait does not change, because retries often land on a faster host. What
  changes is that a draft is no longer lost after three cut-off attempts, and a call that is
  going to finish is not billed twice.
* **180s for critics**, not 120s. At 120s gemma4's mean wait falls further (20s against 26s), but
  the timeout cuts 20 healthy glm-5.2 and deepseek-v4-flash attempts instead of 8. A cut attempt is
  retried, so either value costs almost nothing in failures. 180s keeps nearly all the gain with
  fewer wasted attempts.

**How this interacts with D-failing-critic-sidelined.** A timed-out attempt is retried within the
call's budget. A strike needs every call the alias made in a pass to fail after its retries. The
replay puts a critic call failing all three attempts at 0.11% or lower at 180s, so a shorter
critic timeout does not measurably raise the strike rate.

**Not part of the run's identity.** The section is not under `budgets`. `_run_fingerprint` hashes
`budgets`, so a field there would abandon every paused run at the deploy that shipped it.
Changing a role timeout changes how long an attempt may take, not which question is answered.

**Assumptions to re-check.** The replay treats attempts as independent. That fits OpenRouter
spreading retries across hosts. It would not hold if one prompt makes a model run away on every
attempt. Writer-sized and critic-sized deepseek-v4-flash calls were told apart by output size,
not by role. The per-attempt `model_call` audit event proposed separately (PR #216) records the
role directly and is how the effect of this change should be measured. A reverse proxy between the app
and LiteLLM could also have its own read timeout below 900s. Over the three days it returned no
504s, but no call was allowed to run past 300s, so that limit is unobserved.

**Invariants.** *Termination*: unchanged — no rule, budget count, or cap moves. A call attempt can
now last up to 900s for a writer, and the attempt, retry, and writer-attempt counts still bound
it. *Fail-closed lenses*: unchanged — a critic call that exhausts its retries still fails its
lens. *Author exclusion, blind orchestrator, severity floors, untrusted text, cross-model
confirmation*: unchanged; this changes how long the client waits, not what any call sees or
decides.
