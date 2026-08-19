## D-degraded-roster — an unreachable alias costs the roster that alias, not the run

**The finding.** `build_runtime` probed every entry in `roster.all_aliases` in a bare loop, and any
`ProbeIncomplete` aborted the whole startup. Every alias was therefore a single point of failure for
every run, regardless of what it did in the roster or how many healthy models stood beside it.

The startup flow makes the consequence deterministic: `build_runtime` used to stop before graph
execution whenever any one alias raised `ProbeIncomplete`, even when removing that alias left the
configured writers and every critic pool viable. `tests/test_degraded_roster.py` pins both sides of
that boundary: an unreachable alias is removed when `validate_roster_health` still passes, while a
reduced roster that cannot staff the game still fails closed.

The precedent for the right shape already existed one decision away. D-probe-capability-evidence gave
`ra doctor` exactly this treatment — catch `ProbeIncomplete` per alias, mark it unreachable, keep
going — and deliberately left `build_runtime` raising, on the reasoning that doctor spends nothing
while a run is about to spend money under a silently wrong extraction regime. That reasoning is still
correct about *the alias that failed to probe*, and this decision does not touch it: an alias whose
mode is unknown is never called under a guessed mode. What the earlier decision did not separate is
the alias from the roster. Refusing to call a model you could not probe is fail-closed; refusing to
run at all when five other probed models can staff the entire game is not a safety property, it is an
outage.

**The decision.** The probe loop collects unreachable aliases instead of raising on the first, then
`_degrade_roster` removes them and re-runs `validate_roster_health` on what is left. If the reduced
roster passes, the run proceeds under it, with the drop recorded as a warning and in the `startup`
event's `unreachable_aliases`. If it does not, startup fails closed exactly as before, with a message
naming both the unreachable aliases and what their absence emptied.

`validate_roster_health` is the gate on purpose, rather than a new count of what is "enough roster".
It is already the definition of a viable roster and it is already fail-closed, so a lens left with no
eligible non-author critic still refuses to start — including the subtle case a pool-size check would
miss, where a pool is non-empty but contains only the writer who is about to author. `_roster_without`
checks the two structural emptinesses ahead of it (no reachable writer, no reachable critic for some
lens) only so the failure reads as the outage it is instead of a pydantic field-length error.

**Why silence is not what makes this safe.** A degraded roster cannot buy an `accepted` it did not
earn when the drop leaves a lens roster-limited, and this required no new code:
`LensStatus.roster_limited` is computed per round from the critics that actually turn out to be
eligible, so a lens thinned below two reaches `weak_met` and the run terminates
`converged_unconfirmed` rather than `accepted`. If every lens still has at least two eligible critics,
the reduced roster may still earn `accepted`. Cross-model confirmation (D-cross-model-confirmation,
D-two-clean-critiques) is enforced by the same machinery it always was, while each attempt's
`startup` event records which aliases were unreachable.

**The degraded roster is not part of the run's identity.** `_run_fingerprint` keeps hashing the
*configured* roster, from `config` and never from `rt.config`. Hashing what an attempt settled for
would make the recovery look like changed inputs: a run would resume happily for as long as the outage
lasted and then hit `ResumeMismatch` (D-redeploy-survival) at the exact moment the provider came back
and it could have finished properly. Degradation is per attempt, which is why `unreachable_aliases`
is stamped on each `startup` event rather than on the run — a resumed run may legitimately have had
different rosters on different attempts, and docs/run-provenance.md's per-attempt reading of the
startup events is what shows it.

**An unreachable orchestrator is a special case, and the cheapest one.** Clearing the field selects
the documented default of `writers[0]` (D-orchestrator-roster-entry). Keeping the unreachable alias
would instead leave `_orchestrate_call` swallowing a failure every round, disabling rule 9 silently
for the whole run — the permanent invisible loss that decision exists to prevent. The orchestrator's
job is bounded ints in and one boolean out, so any probed alias can do it, and rule 9 is cap-gated:
the substitution can only ever *enable* a polish pass, never force one.

**What this deliberately does not do.** It does not degrade on `probe_tool_calling` — a search-enabled
roster with a tool-incapable writer still fails closed per D-retrieval-opt-in, and the writer-facing
half of that check now sees the reduced roster anyway. It does not catch the definite capability
verdict: the `ConfigError` for "cannot produce parseable structured output" is a measured fact about
the alias, not about the moment, and still refuses to start. It does not retry the probe, add a
backoff, or wait for a provider to recover; a deployment that is fully unreachable fails closed here
and is handled by D-deferred-not-abandoned instead.

**Invariants.** None of the six is in reach. Author exclusion is enforced by `roles.eligible_critics`
at resolved identity, unchanged and now over a smaller pool. The orchestrator stays blind — which
alias referees does not touch what `OrchestratorView` carries. Severity floors, termination and the
untrusted-text boundary are untouched. Fail-closed lenses are unaffected in the sense the invariant
means it (an invalid critic field still fails the whole lens); the startup-time roster check remains
fail-closed, and is now asked the question it was always the right function to answer.
