## D-dereferenced-schema — inline every `$ref` before a schema reaches a request or a prompt

**The finding.** `LLMClient.structured()` built its `response_format` (and its prompt-mode
instruction) straight from `schema.model_json_schema()`. Pydantic emits `$defs` + `$ref` for any
nested model or enum, and `CritiqueOutput` has both — `RawIssue`, `Category`, `Severity` and
`StructuralRef` all sit behind a `$ref` two or three levels deep. That is an interoperability risk
on a proxy path that substitutes tool calling for structured output: upstream
[LiteLLM issue #8898](https://github.com/BerriAI/litellm/issues/8898) documents an Anthropic request
being converted to a forced tool call whose otherwise-correct JSON is nested under varying envelope
keys. This application's strict `extra_forbidden` validation rejects any such envelope wholesale.
Inlining references makes the schema self-contained before it reaches either the proxy or a model,
rather than relying on every downstream structured-output path to interpret reference indirection
the same way.

`probe_structured_output` (D-probe-capability-evidence) pins an alias to `json_schema` using `_Probe`,
a trivial `{"ok": boolean}` schema with no nested model and therefore no `$ref`. It can establish that
the alias accepts that simple schema, but it cannot establish that the same path handles a real,
reference-bearing schema such as `CritiqueOutput`. That representativeness gap is tracked as an open
item below; this decision does not close it.

**The decision.** A module-level `_dereference(schema)` in `llm.py` inlines every `$ref` against
`$defs` and drops `$defs`, and `structured()` calls it once, immediately after
`schema.model_json_schema()`, before the result reaches either `_response_format`/`_strictify` or
`_schema_instruction` — so both the native request and the prompt-mode instruction see the same
self-contained form. A `prompt`-mode model previously had to follow `$ref` indirection by eye to
answer correctly; it no longer has to.

The helper is pure and total over dicts, lists and scalars, and preserves everything else about the
schema: `enum` lists, `minLength`/`maxLength`, `title`, nullable/optional unions expressed as
`anyOf`, and a `$ref` node's own sibling keys (pydantic emits a `description` override this way; the
override wins over the same key on the resolved target). `_strictify` is unchanged and runs on the
dereferenced result exactly as it ran on the raw one.

**The recursion guard.** No schema in this repository is recursive today — nothing self-references
through `$defs`, since a critique or defect graph never contains itself. A naive inliner is a
landmine for the day one does: `_dereference` tracks the `$defs` names currently being resolved on
the current path (not a global visited set, since two sibling fields legitimately sharing the same
`$defs` entry — every `RawIssue.locus` reusing `StructuralRef`, every list item reusing `RawIssue`
itself — is ordinary and must not trip it) and raises `ValueError` the moment a name recurs into
itself, naming the cycle. It fails loudly and immediately, not by hanging or by truncating at an
arbitrary depth. A `$ref` to an undefined `$defs` entry, or a `$ref` in a form other than
`#/$defs/<name>` (nothing pydantic emits does this, but the helper does not assume it), raises the
same way rather than passing through unresolved.

**What this deliberately does not do.** It does not touch the probe that can classify an alias as
capable without exercising reference handling — the gap above is a `probe_structured_output`
shortcoming rather than a `_dereference` one, and needs its own evidence (a probe schema shaped like
a real one, not `_Probe`) to fix without becoming a second, larger request on every startup. It does
not change `_strictify`'s
own `$defs`/`definitions` branch, which stays reachable and tested directly
(`tests/test_report_store_llm.py::test_strictify_closes_every_object`) against a raw, non-dereferenced
schema — nothing requires every caller of `_strictify` to have dereferenced first. For a downstream
path that already accepts `$ref`, the dereferenced schema is semantically identical to the one it
replaces; the test suite asserts that directly (a hand-rolled JSON-Schema-subset checker confirms a
payload validating against the original model still validates against the dereferenced schema,
across `json_schema`, `json_object` and `prompt` modes) rather than assuming it.

**Invariants.** None of the six is in reach. Author exclusion, the blind orchestrator, severity floors
and termination are untouched. Fail-closed lenses are unaffected — this changes what a schema looks
like on the wire, not when a lens is retried or abandoned. The untrusted-text boundary is untouched:
`_dereference` operates on a schema this application generated from its own pydantic models, never on
model-authored or provider-authored text.
