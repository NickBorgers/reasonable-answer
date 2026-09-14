## D-model-call-timing — every model call attempt is timed in the audit trail

**The finding.** On 2026-09-14 the question "why do runs take so long?" could not be answered from
`audit.json`. The events carry timestamps, but no event records how long a model call took, which
host served it, or how many attempts it needed. The answer had to be pieced together from the gaps
between events and from LiteLLM's own request log in Loki, which keeps about two days.

Those short-retention operational sources were not a durable evidence record and cannot support a
documented timeout or routing conclusion under QP9. The repository-verifiable gap is narrower:
the critique and generate events show only the final result of a call, so attempts, their durations,
token counts, and any serving-provider metadata exposed by a response are absent from the run.

**The decision.** `LLMClient` reports every HTTP attempt to a call sink. The graph points the sink
at the run's event log, so each attempt becomes one `model_call` event with these fields:

| field | meaning |
|---|---|
| `purpose` | what the graph was doing: `writer`, `critic:<lens>`, `claim_check`, `support_manifest`, `dispute`, `arbiter`, `orchestrator` |
| `alias` | the roster alias called |
| `attempt` | 1-based, within one call's retry budget |
| `outcome` | `ok`, or the failure class the attempt ended on (`timeout`, `http_502`, `empty_completion`, …) |
| `seconds` | from sending the request to classifying the response, excluding backoff |
| `prompt_tokens`, `completion_tokens` | as the response reported them; 0 when there was no response |
| `provider` | the upstream host that served the request, or null |

* **Per attempt, not per call.** A call that timed out twice and then answered is three events.
  That is the only way the cost of a retry shows up afterwards.
* **Purpose travels in a context variable.** The label has to reach `_create` through `structured`,
  `complete`, and the tool loop. A parameter would add a field to every signature in between that
  only the audit trail reads. A context variable does not follow work into a thread pool, so the
  critique pass sets it inside the submitted function.
* **Provider is best effort.** When the response object exposes a top-level `provider` attribute,
  its value is recorded only when it matches a plain name pattern (letters, digits, space,
  `._()/-`, at most 80 characters). Any other or absent value is recorded as null.
* **The sink can never fail a call.** A sink that raises is logged at WARNING and ignored.
* **Startup probes are not recorded.** They run before the run store exists.

**What this deliberately does not change.** No timeout, retry budget, or routing changes. The
short-retention observations that motivated this instrumentation are not durable evidence for any
such policy change. This event provides the per-run record needed to evaluate a future change in
its own decision.

**Not part of the run's identity.** No configuration is added, so `_run_fingerprint` is unchanged
and paused runs resume across the deploy.

**Invariants.** *Isolation / untrusted text*: a record holds counts, a duration, a closed failure
token, an alias, a fixed purpose label, and a provider name restricted to a plain character set.
It holds no prompt, completion, or provider prose. The events are never shown to a model, and
nothing from them enters `OrchestratorView` or `ControllerInput`. *Author exclusion, blind
orchestrator, fail-closed lenses, severity floors, termination*: unchanged. The sink observes
calls. It changes no call, retry, or decision.
