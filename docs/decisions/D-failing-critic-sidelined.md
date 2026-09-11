## D-failing-critic-sidelined — a critic whose calls keep failing stops being asked

**The finding.** Between 2026-09-04 and 09-09 production finished six runs; one was `accepted`.
The container logs and `audit.json` for all of them show one alias dominating the failures:
`glm-5.3` failed **46 of its 66** critique calls (glm-5.2, the alias it had replaced, failed 1 of
72; gemma4 2 of 115). 39 of those failures were `exhausted call retries (Request timed out.)` —
three consecutive 300-second timeouts, fifteen minutes per lens — and the rest were upstream 502
pages and empty completions. `run-f78e52f32b4a` took 8,057 seconds and ended
`exhausted_unresolved`; `glm-5.3` failed in every one of its eight rounds, and the logic and
evidence lenses never held more than one clearance. The separate version-currency audit
(`docs/model-evaluation-record-2026-09-08-glm-5.3.md`) reproduced the timeouts pinned to a single
high-uptime host, so this is the model on this call shape, not routing.

Two properties of the pipeline let one slow alias cost that much, and neither is about glm-5.3:

* **Nothing remembers a failing critic past one draft.** `used_critics` — the only record of who
  reviewed — is a per-artifact accumulator that `_generate` resets, so `critic_slate` drew the
  same first-in-pool alias on every new draft. The exhausted-pool rotation added for rule 2
  (D-provider-retry) only spreads retries *within* one artifact.
* **Its failures never reached a rule that routes around them.** Since D-front-loaded-depth, a
  lens is failed only when it holds no completed review. The other critic in the slate completed,
  so `lenses_failed` stayed 0, rule 2 never fired, and the shortfall landed silently on
  `cleared_count`, where it can only prevent acceptance.

**The decision.** The run keeps a whole-run strike count per critic alias. A critique pass is a
**strike** for an alias when every review it attempted in that pass failed *at the call layer*;
any completed review resets its count. When an alias reaches `review.critic_strike_limit`
(default 2) it is **sidelined**: removed from every critic pool for the rest of the run, recorded
as a `critic_sidelined` event (critic identity, strike count, failure class) and a WARNING.

* **Per pass, not per call.** An alias serving three lenses in one bad minute fails three calls;
  that is one observation of the alias, and counting it once also makes the result independent of
  the order the thread pool returned results in.
* **Only call-layer failures count.** `LensResult` now carries `failure_class` — the
  `ModelCallError` class (`timeout`, `http_502`, `empty_completion`, …), `schema_violation`, or
  `unstaffed`. A schema violation is excluded: the model answered, it has its own repair budget
  (D-repair-turn-context) and its own audition verdict. So is `http_402`, which is the account's
  failure rather than the alias's (D-credit-exhaustion-defers). The `critique` event records the
  class too, so a failing critic is countable from `audit.json` rather than reconstructed from
  container logs.
* **`validate_roster_health` gates it**, for the reason D-degraded-roster gave: it already is the
  definition of a viable roster. An alias whose removal would leave any lens without an eligible
  non-author for some writer is never sidelined — a slow critic is better than no critic, and a
  lens with none is what fail-closed exists to refuse. The same check runs again every time the
  narrowed roster is read, so a resumed attempt whose startup degraded the roster further falls
  back to the configured pools rather than to an unstaffable one.
* **Critic pools only.** Writers and the orchestrator are untouched: a critic slot timing out
  says nothing about the same alias drafting, and the writer walk already rotates past a failing
  author on its own budget.

**Why this cannot buy an acceptance.** `_critic_roster` is the single source for both drawing
slates *and* `roles.lens_statuses`. A sidelined alias therefore stops counting as eligible, and a
lens thinned to one model family is `roster_limited`: it can reach `weak_met` and
`converged_unconfirmed` (rule 10), never `strong_met` and `accepted`. That is exactly the verdict a
startup without that alias would have produced (D-degraded-roster), and it is more honest than the
previous outcome, where clearance counted an alias that would never answer and the run ended rule
11 `exhausted_unresolved` — "clean but unconfirmed" — waiting on it. Clean records the sidelined
alias earned earlier on the current artifact stop counting for the same reason, which can only
lower clearance.

**Why not cap timeout retries instead.** The first proposal was to retry a timeout once rather
than twice. The same logs argue against it: glm-5.3's third attempt after two timeouts rescued 0
of 39 calls, but gemma4's rescued 3 of 3. A per-call rule cannot tell those apart; a per-alias
count across passes can, and it removes the cost at its source — the alias being drawn again —
without taking a working retry away from a healthy model.

**Not part of the run's identity.** The knob lives under `review`, not `budgets`, because
`_run_fingerprint` hashes `budgets` and a new field there would change every paused run's
fingerprint at the deploy that shipped it, abandoning them through `ResumeMismatch`. Strikes and
the sidelined list are checkpointed state, so they persist across a resume; the only way to give
a sidelined alias another chance within a run is the roster-gate fallback above. Starting the run
again starts with an empty record.

**Invariants.** *Author exclusion*: unchanged — `eligible_critics` still drops the author at
resolved identity, now over a pool that can only be smaller. *Cross-model confirmation*: touched,
in the safe direction — eligibility shrinks, so `roster_limited` can only become true, never
false. *Fail-closed lenses*: unchanged — a failed review still contributes nothing, and the
roster gate refuses any narrowing that would leave a lens unstaffable. *Blind orchestrator*: the
controller sees `LensStatus` exactly as before; strikes, aliases and failure classes never enter
`OrchestratorView` or `ControllerInput`. *Termination*: no rule added, removed or reordered;
sidelining changes who is asked, never how many passes or generations the budgets allow.
