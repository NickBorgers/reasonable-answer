## D-validator-error-hygiene — the schema-repair re-ask is validator-attributed and content-free too

**The finding.** D-repair-turn-context's "RA-016 is unchanged" paragraph audits `rejected_text()` and
`repair_excerpt()` — the lens-repair half of a critic's repair budget — and is accurate about that
half. It does not audit the other half of the same budget: `LLMClient.structured`'s own schema-repair
loop, which runs before lens validation ever sees an output and handles the `CritiqueOutput`/
`IssueRepairs` level — malformed JSON, a missing field, an enum value out of range. There,
`last_err = str(exc)[:800]` took a pydantic `ValidationError`'s default rendering verbatim, and that
rendering echoes `input_value=…` — a fragment of the very field the validator rejected, which for a
critic's structured output is a quoted report span. The same string was re-prompted with "Your
previous response was rejected by the schema validator" — the critic-attribution wording
D-repair-turn-context specifically chose *not* to use for the lens half ("a candidate issue was
rejected", never "your previous response") — and, on repair exhaustion, was carried unchanged into
`MalformedOutputError`, which `critique.critique_once` truncates into `LensResult.failure_reason` and
logs at WARNING, both outside the 0700 `runs/<id>/` tree (RA-016). D-repair-diagnostic-keying had
already flagged the shape of this gap without closing it: "this decision does not generalize [keeping
the rejected span out of `str(exc)`] to arbitrary validator messages; RA-016 relies on the triage
error's message construction" — true only for `triage.LensValidationError`, never claimed for
pydantic's own `ValidationError`. A second, smaller leak shared the same branch: `_extract_json`'s
failure message quoted up to 200 characters of the unparseable response, which for a critic is
report-adjacent model output too.

**The decision.** `llm._sanitized_schema_error` replaces `str(exc)[:800]` on both exception types
`structured()`'s repair loop catches. For a pydantic `ValidationError` it renders
`errors(include_input=False, include_url=False)` — `loc`/`msg`/`type` only, with string locations
allowlisted against properties in the dereferenced schema and any other location replaced by
`(unrecognized-key)` — never the input value or a model-authored forbidden key. For everything else
reaching that branch (a plain `ValueError`), the message is used as-is,
which is now safe on both sides that currently reach it: `_extract_json` no longer quotes the response
it could not parse, and the `validate=` hook — unused by any caller today — is documented to describe
the failure the way `LensValidationError` does, not to quote the value that failed it. The re-ask now
reads "The schema validator rejected the output:" and fences and marker-scrubs the sanitized summary
with `prompts.DATA_FENCE`/`DATA_END`, matching `critic_repair_turn`'s house style and its
validator-attributed wording rather than "your previous response". `MalformedOutputError` therefore
carries only the sanitized summary, so every downstream consumer — `critique.critique_once`'s
`LensResult.failure_reason`, and the graph's support-manifest and dispute-pass warning logs — inherits
the fix without being touched itself; two of those call sites already carried a comment explaining why
they avoid `str(exc)` regardless of what it contains, updated to describe what the message now is
rather than repeat the old warning as if it still held.

**One budget, unchanged.** This does not touch `budgets.critic_repair_retries`, which half of a
critic's repair spends it on schema violations versus lens patches, or the raise-on-exhaustion
behaviour — only what the re-ask and the terminal error say.

**Invariants.** The untrusted-text boundary is strengthened: a pydantic error's `input_value` and a
model-authored forbidden-key `loc` were report-adjacent content capable of reaching a generator's
context under validator-attributed framing, and neither now does. Fail-closed lens validation, author
exclusion, the blind orchestrator, severity floors and termination are untouched.
