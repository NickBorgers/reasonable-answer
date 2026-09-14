## D-model-call-timing — every model call attempt is timed in the audit trail

**The finding.** On 2026-09-14 the question "why do runs take so long?" could not be answered from
`audit.json`. The events carry timestamps, but no event records how long a model call took, which
host served it, or how many attempts it needed. The answer had to be pieced together from the gaps
between events and from LiteLLM's own request log in Loki, which keeps about two days.

Those sources told a story the audit trail could not:

* Twelve runs on builds 60cef84–90ef9d7 spent about 81% of their wall time waiting in the run
  queue. Active time was 38–96 minutes.
* Inside a run, writing and critique were about two thirds of the time. Timeouts cost up to a
  quarter. Each timeout blocked for the full `budgets.timeout_seconds` (300s) before its retry.
* Most "timeouts" were not stalls. 49 of gemma4's 51 slow calls ran to their output-token cap.
  36 deepseek-v4-flash calls succeeded upstream after our client had already given up at 300s.
  deepseek-v4-flash ran at a median 207 tok/s on one host and 66 tok/s on another.

None of that could be read from a run's own record. The critique and generate events show only the
final result of a call. The retries and the time they cost are invisible.

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
* **Provider is best effort.** OpenRouter returns a top-level `provider` field. LiteLLM copies
  unknown top-level keys onto its response, and the OpenAI SDK keeps them as extra attributes. The
  value is recorded only when it matches a plain name pattern (letters, digits, space, `._()/-`, at
  most 80 characters). Any other value is recorded as null. Backends that do not send it also get
  null.
* **The sink can never fail a call.** A sink that raises is logged at WARNING and ignored.
* **Startup probes are not recorded.** They run before the run store exists.

**What this deliberately does not change.** No timeout, retry budget, or routing changes. The same
data does suggest one. The LiteLLM log records how long each call would have taken with no client
timeout, because the upstream keeps generating after the client hangs up. Replaying those durations
against candidate timeouts, with three attempts per call:

* Writer-sized deepseek-v4-flash calls fail all three attempts about 2.5% of the time at 300s, and
  about 0% at 900s, at the same mean wall time.
* gemma4 critic calls wait a mean 38s (p95 302s) at 300s, and a mean 20s (p95 124s) at 120s, with
  no change in how often a call fails.
* glm-5.2 and critic-sized deepseek-v4-flash calls are unaffected either way. A slow attempt is
  retried; it is not a strike toward D-failing-critic-sidelined, which counts only a pass in which
  every call from the alias failed after its retries.

So a longer writer timeout and a shorter critic timeout look like a gain. That change belongs in its
own decision, and this event is how its effect gets measured.

**Not part of the run's identity.** No configuration is added, so `_run_fingerprint` is unchanged
and paused runs resume across the deploy.

**Invariants.** *Isolation / untrusted text*: a record holds counts, a duration, a closed failure
token, an alias, a fixed purpose label, and a provider name restricted to a plain character set.
It holds no prompt, completion, or provider prose. The events are never shown to a model, and
nothing from them enters `OrchestratorView` or `ControllerInput`. *Author exclusion, blind
orchestrator, fail-closed lenses, severity floors, termination*: unchanged. The sink observes
calls. It changes no call, retry, or decision.
