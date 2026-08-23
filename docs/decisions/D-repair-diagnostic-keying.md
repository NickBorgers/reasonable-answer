## D-repair-diagnostic-keying — span correlation is limited to one repair loop

**The finding.** An unkeyed truncated SHA-256 was content-derived even though it did not quote the
content. Anyone able to read container stdout could hash candidate phrases offline, test whether a
private report span had been rejected, and correlate the same hidden span across calls or runs.
That exceeded RA-016's least-privilege boundary for stdout.

**The decision.** `LLMClient.structured()` generates a fresh 32-byte secret before its repair loop.
`LensValidationError.fingerprint()` uses keyed BLAKE2s over the normalized rejected span and emits
only 8 hex characters. The same key is used for every attempt in that one structured call, so
identical candidates remain observable as re-rolls there; a new call gets a new key, so the value
cannot serve as a stable guessed-text verifier or cross-call correlation identifier. The key and
the rejected span are never logged.

The final content-free `LensValidationError` message still becomes `LensResult.failure_reason` and
is logged at WARNING after repair exhaustion. This decision does not generalize that property to
arbitrary validator messages; RA-016 relies on the triage error's message construction keeping the
rejected span out of that path.

**Invariants.** Validation, fail-closed lens handling, repair budgets, prompts and controller rules
are unchanged. This tightens only the audit-privacy scope of the diagnostic identifier.

**Amended by D-repair-turn-context:** the lens-rejection repair loop now lives in
`critique._repair_until_valid`, which mints its own fresh 32-byte key per loop; `structured()`'s own
key still exists for schema violations. The property this decision guarantees is preserved either
place a repair loop runs.
