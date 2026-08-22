## D-fence-scrub-all-directions — every fenced block is marker-scrubbed, not only the critic-facing ones

**The finding.** D-repair-fence-scrubbing closed the gap between a repair prompt's stated boundary
and its implementation, but only on one side of the run: `prompts._neutralized` was called from
`critic_user`, `fetched_sources_block` and `critic_repair_turn` alone. Every writer-facing and
arbiter-facing fence interpolated the same class of untrusted text raw. `writer_revision` fenced the
question, the draft report and a serialized FIX TASKS list — the second of which carries a critic's
own `rationale`, `instruction` and `related_span`, free text by design — without scrubbing any of
them; the same pattern repeated in `writer_first_draft`, `writer_dispute`, `writer_support`,
`search_results_block`, `source_read_block` and `arbiter_user`. A defect whose `rationale` embedded
`DATA_END` verbatim closed the FIX TASKS fence early, and whatever followed sat outside the data
block next to a live instruction ("Apply them all."). The arbiter case was the most consequential:
its context fences a writer's own dispute `grounds` and `evidence_quote` — an interested party's
argument, by the prompt's own description — and `dispute_upheld=true` permanently suppresses the
finding those fields sit next to. A dispute that broke its fence would not need to argue the finding
wrong; it would only need to get its own free text read as the instruction that follows.

**The decision.** `_neutralized` is applied to every interpolation that lands inside a
`DATA_FENCE`/`DATA_END` block across the whole file, in both directions: `writer_revision` (question,
report, the FIX TASKS JSON), `writer_first_draft` and `writer_dispute` (question, report, FIX TASKS
JSON), `writer_support` (question, report, read source bodies), `search_results_block`,
`source_read_block`, `arbiter_user` (the finding JSON, the paragraph, the question, the challenge
JSON, the fetched evidence page) and `refine_user` (the question). This is not new scope for the
untrusted-text boundary — RA-010 already named the question, every report and every span as data —
it is closing the same implementation gap D-repair-fence-scrubbing closed, generalized to every fence
the file builds rather than to the one repair prompt that motivated it first.

`_neutralized` itself moved from the critics section to sit beside `DATA_FENCE`/`DATA_END`, since it
is now a file-wide primitive rather than a critic-repair one; its semantics are unchanged (replace
`DATA_END` with `[END-MARKER]`, `DATA_FENCE` with `[BEGIN-MARKER]`).

**Serialized JSON is scrubbed after `json.dumps`, not field-by-field before it.** The FIX TASKS list,
the arbiter's finding and its challenge are all built with `json.dumps` before they are fenced.
`DATA_FENCE` and `DATA_END` are plain ASCII sequences; `json.dumps` escapes quotes, backslashes and
control characters, not arbitrary substrings, so a marker embedded in a field's value survives
serialization byte-for-byte inside the resulting string. Scrubbing the serialized string finds every
occurrence in one pass, wherever it sits in the structure, and the replacement text
(`[END-MARKER]`/`[BEGIN-MARKER]`) contains no character `json.dumps` would need to escape — so the
result is still valid JSON. Scrubbing each field before serialization would need to be applied at
every call site that builds one of these dicts and re-applied at every future field added to them;
scrubbing the serialized string is one call, made once, that cannot be forgotten per field.

**Invariants.** Untrusted text never reaching a generator as instruction is what this decision
enforces uniformly rather than partially; the other five (author exclusion, blind orchestrator,
fail-closed lenses, severity floors, termination) are untouched — no schema, no severity mapping, no
controller rule changed.
