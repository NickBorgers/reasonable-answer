## D-probe-capability-evidence — a probe that cannot complete does not get to say the alias is incapable

**The finding.** `LLMClient.probe_structured_output` walked `MODES = ("json_schema", "json_object",
"prompt")` strongest-first and caught bare `Exception` around each attempt: any failure at all —
schema violation, malformed JSON, a 429, a timeout, a 500 — read as "this alias does not support this
mode", and fell through to the next, weaker one. `probe_tool_calling` had the identical shape: any
exception from the probe call was read as "this alias cannot call a tool" and cached `False`.

Both probes conflate two different kinds of fact. Whether a model can produce `json_schema`-shaped
output, or emit a tool call, is a *capability* fact about the alias — stable across calls, worth
caching for the process. Whether a single request got a 429, timed out, or hit a 5xx is an
*availability* fact about that moment on the wire — exactly what `_create`'s own backoff already
exists to ride out, and already has, by the time either probe's `except` block runs. Treating the
second as the first is how `ra doctor` pinned `nemotron-3-ultra` to `json_object` on 2026-08-11,
after DeepInfra returned HTTP 429 three times during the `json_schema` probe: the model supports
`json_schema` fine, and the pin was wrong for the rest of the process. Because
`structured_output_mode` is part of the audition cache identity (D-audition-probe-parity), the same
mechanism can invalidate a cached audition verdict, or measure a critic under a weaker extraction
mode than it deserves, on nothing more than a bad moment during the probe. It also supplies a
plausible mechanism for a symptom D-writer-failure-class recorded without explaining: the roster
documented `minimax-m3` as probing non-deterministically across `json_schema`, `json_object` and
`prompt`. This decision does not claim that was the only cause — nobody measured minimax-m3's probe
failures at the time — only that transient-failure misclassification during a probe is a mechanism
that produces exactly that symptom.

**The decision.** Both probes now distinguish observed capability evidence from an incomplete call,
and only demote or mark incapable on the former:

- **Capability evidence** — `MalformedOutputError` (the model answered, but not inside the closed
  schema) demotes `probe_structured_output` to the next mode. A successful tool probe whose completion
  contains no tool call marks that alias tool-incapable. Both conclusions come from model output the
  probe actually observed.
- **Incomplete probe** — every call exception aborts by raising `ProbeIncomplete`, a `ConfigError`
  subtype distinct from the pre-existing "cannot produce parseable structured output" verdict. A
  broad status such as `http_400` or `http_422` says the request was rejected, but does not identify
  whether `response_format`, `tools`, or an unrelated field caused the rejection; it is therefore not
  capability evidence. Retryable failures have already spent `_create`'s call budget before reaching
  this boundary. The alias's capability remains unknown, not absent.

Classification reads the exception type and the `failure_class`/status code carried on it, never the
message text — the same rule `_permanent` and `_failure_class` already follow, for the same reason: a
provider's wording is not an interface.

**What this deliberately does not do.** It does not change the shape of either probe's ladder or its
caching. It does not retry the probe itself beyond what `_create`'s own budget already provides — a
probe that fails on availability grounds is meant to be re-run (a fresh `ra doctor` or a restarted
`build_runtime`), not looped inside the failing attempt. It does not re-measure or invalidate any
audition verdict already on disk; a verdict cached under a probed mode from before this fix is
unaffected until the alias is re-probed.

**Fail closed where it costs something; report where the job is diagnosis.** `ra audition` and
`ra audition-refine` are unchanged: an incomplete probe raises, uncaught, all the way to the same
`ConfigError` fail-closed path a
genuine incapability already used — right, because each is about to spend money on a run or a
measurement whose extraction regime would be silently wrong if the probe had degraded instead.
`ra doctor` is different in kind, not degree: it is the tool an operator reaches for *when the proxy
is misbehaving*, which is exactly the moment a probe is likely to hit a 429 or a timeout rather than
settle a capability question, and it spends nothing — it exists only to report. Before this decision
it had no `try/except` around either probe at all, so this fix would otherwise have turned every
transient failure doctor's own purpose exists to surface into an uncaught Python traceback, at
precisely the moment an operator needs the rest of the table. So `ra doctor` alone catches
`ProbeIncomplete` per alias, renders a marker distinct from a real mode and from a definite `NO`
("unreachable"), keeps printing every other alias and the roster-health warnings, and exits `2` —
distinct from a clean `0` and from the `1` a *definite* capability finding (a writer confirmed unable
to call a tool) still produces — so a scripted health check can tell "fully verified" apart from
"could not fully verify" apart from "found a real problem".

> Superseded in part by **D-degraded-roster**: `build_runtime` (`ra run`, `serve`) no longer lets
> `ProbeIncomplete` propagate. It collects the unprobeable aliases, drops them, and re-runs
> `validate_roster_health` on what remains, failing closed only when the survivors cannot staff the
> game. The rest of this decision is untouched: an alias whose mode is unknown is still never called
> under a guessed mode, and the definite `cannot produce parseable structured output` verdict still
> refuses to start.

**Invariants.** None of the six is in reach. Author exclusion, the blind orchestrator, severity
floors and termination are untouched. Fail-closed lenses are arguably strengthened, not weakened: a
capability probe that could not be completed now fails closed loudly instead of silently degrading,
everywhere except the one diagnostic command whose entire purpose is to report rather than spend or
gate. The untrusted-text boundary is untouched — classification reads exception types and status
codes, never provider-authored text, exactly as `_failure_class` already did.
