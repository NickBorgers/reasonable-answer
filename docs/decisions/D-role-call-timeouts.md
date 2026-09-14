## D-role-call-timeouts — writers and critics get their own call timeout

**The finding.** Every model call attempt shared `budgets.timeout_seconds`, although writer and
critic calls have different operational purposes. One shared value cannot be adjusted for one role
without also changing how long every other role may hold its retry loop. The operator's observations
that prompted this split live outside the repository and cannot be fetched by a reviewer. Under QP9
they motivate a configurable deployment option, not an empirical claim in this decision and not a
change to the code default.

**The decision.** A new top-level config section, `call_timeouts`, overrides the timeout for one
call attempt by role:

* `writer_seconds` bounds each attempt of the writer's draft call, including every tool round.
* `critic_seconds` bounds each attempt of the critique call, its repair turns, and the claim checks
  that run under the evidence critic's slot.
* Every other call keeps `budgets.timeout_seconds`. That covers the support manifest, dispute
  elicitation, the arbiter, the orchestrator, and startup probes. No role-specific operational
  case has been established here for changing those calls.
* `None`, the code default, keeps the client default. A config without the section behaves as
  before, and `config/roster.default.yaml` does not set it.

The deployment roster opts into `writer_seconds: 900` and `critic_seconds: 180`. These values are
an operator deployment posture, not a project-wide evidence claim; deployments without reviewable
operational evidence retain the shared timeout by leaving both fields unset. This follows the
existing `review.selection` pattern in D-latest-unblocked-selection: observations that the
repository cannot cite may motivate an opt-in roster choice but do not move the code default.

**How this interacts with D-failing-critic-sidelined.** A timed-out attempt is retried within the
call's budget. A strike needs every call the alias made in a pass to fail after its retries. A
shorter critic timeout can therefore cause a strike only when every bounded attempt fails; it does
not bypass or weaken the existing strike rule.

**Not part of the run's identity.** The section is not under `budgets`. `_run_fingerprint` hashes
`budgets`, so a field there would abandon every paused run at the deploy that shipped it.
Changing a role timeout changes how long an attempt may take, not which question is answered.

**Assumptions to re-check.** The deployment values need reviewable per-role call measurements before
they can become defaults or support comparative performance claims. A reverse proxy between the app
and LiteLLM may also impose a read timeout below the writer override; that limit must be verified in
the deployment rather than inferred here.

**Invariants.** *Termination*: unchanged — no rule, budget count, or cap moves. A call attempt can
now last up to 900s for a writer, and the attempt, retry, and writer-attempt counts still bound
it. *Fail-closed lenses*: unchanged — a critic call that exhausts its retries still fails its
lens. *Author exclusion, blind orchestrator, severity floors, untrusted text, cross-model
confirmation*: unchanged; this changes how long the client waits, not what any call sees or
decides.
