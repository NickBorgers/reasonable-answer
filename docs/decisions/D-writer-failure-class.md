## D-writer-failure-class — a failed writer attempt records what kind of failure it was

**The finding.** `generate_failed` recorded a free-text `reason` and nothing else. The string
embeds the alias and the provider's own words, so it is effectively unique per attempt and cannot
be grouped: the record can say *that* writers failed, never *how often* they failed a given way.
Its two sibling events already do better — `support_manifest_failed` and `dispute_pass_failed` both
carry `error_type`.

The gap also prevents a grounded diagnosis: repeated timeouts, status-bearing provider failures,
empty completions and malformed tool output collapse into unrelated sentences. Nothing in the run
record can distinguish those failure modes without parsing wording that this repository does not
treat as an interface.

**The decision.** `ModelCallError` carries a `failure_class`: a short, stable token naming how the
call failed, set at each raise site in `llm.py` and defaulting to `call_failed`. `graph._generate`
writes it onto every `generate_failed` event beside the existing `author` and `reason`. The classes
are `unparsed_tool_markup`, `tool_loop_no_answer`, `tool_loop_no_end`, `empty_completion`,
`identity_mismatch`, `http_<status>`, `timeout`, `connection`, `call_failed`, and — raised nowhere
in `llm.py`, because the call *succeeded* — `empty_report` for a model that answered with
whitespace.

Two properties are deliberate. Classification reads the exception type and the SDK's status code,
never the message text, for the same reason `_permanent` does: a provider's wording is not an
interface. And an exhausted retry budget reports its *cause's* class rather than a class of its
own, because what a reader needs is which defect the budget was spent on — three unparsed tool-call
blocks and three timeouts are the same event today but materially different observations. The
message still says the budget was exhausted.

**What this deliberately does not do.** It changes nothing the pipeline decides: the retry budgets,
the writer rotation, the fail-closed paths and every controller rule are byte-identical. It adds a
field to one event. In particular it does **not** retire `nemotron-3-ultra`, and it does not make
`probe_tool_calling` stricter — that probe is a one-shot ping while production runs a multi-round
loop. It does not change the roster, the deployment configuration or any probe; those require their
own evidence and decision. Reconsider them only when the per-class counts this decision creates
support a separate change.

**Invariants.** None of the six is in reach. Author exclusion, the blind orchestrator, fail-closed
lenses, severity floors, termination and the untrusted-text boundary are all untouched; the added
field is derived from an exception type, never from model-authored text, and it reaches the run
record rather than any prompt.
