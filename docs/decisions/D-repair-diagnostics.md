## D-repair-diagnostics — a rejected critique says which class it failed, without quoting itself

**The problem.** When `triage._require_quote` rejects a span until the
`critic_repair_retries` budget runs out, the repair loop logs only `exc.__class__.__name__`
(RA-016 — see the audit/privacy bullet in [architecture.md](../architecture.md)), and
`critique_once` records only the **final** failure. So the logs cannot distinguish the two
hypotheses that motivate different follow-up investigations:

* the critic may **re-emit the same normalized rejected span** each attempt, which would be
  consistent with a repair turn that did not produce a different candidate;
* the critic may emit a **different normalized span** each attempt, which would be consistent with
  changing candidates without finding a valid anchor.

Nothing in a run's stored evidence separates them either: `LensResult.failure_reason` is the same
bounded sentence and does not include the rejected span.

**The decision.** A rejection carries bounded diagnostics, and the repair loop logs them.
`triage.ViolationCode` names the class (`category_out_of_scope`, `locus_absent`, `span_empty`,
`span_not_verbatim`); `LensValidationError` additionally carries the field, the locus, which issue
of how many failed (attached by `critique.critique_once`, which is the only caller that knows the
index), and `fingerprint()` — 8 hex from a keyed hash over the **normalized** rejected span. The key
is fresh for each `LLMClient.structured()` call and reused only across that call's repair attempts.
`llm._diagnostics_suffix` renders whatever a validator offers, duck-typed exactly as `repair_hint`
already is, because `triage` is LLM-free and must not import the client to say so.

The fingerprint makes that observable without quoting the rejected span: identical fingerprints
show that the normalized rejected span was identical across attempts, while different fingerprints
show that it changed. Those patterns support the two investigations above without establishing why
the critic produced them. The fingerprint folds what `_normalize` folds, so typographic retyping
does not look like a different candidate. The call-local key means the same hidden span does not
produce a reusable identifier in another structured call.

**What this deliberately does not do.** The rejected span itself never enters `str(exc)`. That is
not incidental: the message becomes `LensResult.failure_reason`, which is persisted into the
critique event and logged at WARNING, both outside the 0700 `runs/<id>/` tree. The span continues
to travel in `repair_hint()`, which stays inside the run. A test pins the message, the diagnostics
and the hint against the span text, so the property cannot regress into a leak.

This changes what a failure *reports*, never what the pipeline *decides*: validation, the repair
budget, fail-closed lens failure and every controller rule are untouched.

**Invariants.** None of the six is in reach. Fail-closed lenses is the nearest — a rejection still
fails the whole review once the budget is gone, and no subset of issues is salvaged. Untrusted text
still reaches no generator as instruction; the diagnostics travel to a log, not to a prompt.

**Amended by D-repair-turn-context:** lens validation no longer runs inside `LLMClient.structured`'s
repair loop — it moved to `critique._repair_until_valid`, which answers a rejection with a field
patch rather than a full re-ask. The diagnostics line this decision specifies is emitted there now;
`llm._diagnostics_suffix` remains for schema violations on the `structured` loop itself. The
guaranteed property — one fresh key per repair loop, reused across its attempts, never logged — is
unchanged and pinned by test.
