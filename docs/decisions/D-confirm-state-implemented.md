## D-confirm-state-implemented — the confirm-state label RB-010 describes now exists in code, not only in the docs

**The finding.** `docs/convergence.md`, `docs/isolation.md`, `docs/architecture.md`, and RB-010 in this
file all describe `confirm_state` as a controller-side label, stamped after a rule-8 confirmation
critique returns, that distinguishes a confirming review from a discovering one in the audit record.
`LensResult.confirm_state: bool = False` has existed since RB-010 was recorded, but nothing in `graph.py`
or `controller.py` ever set it to `True`. Every persisted critique — rule-2 discovery and rule-8
confirmation alike — carried `confirm_state=False`, so the audit trail could not answer the question
the field exists to answer: which of a lens's clean records came from the pass that first reported the
artifact clean, and which came from the top-up rule 8 collected afterward.

**What was not broken.** The safety property RB-010 actually names — a critic cannot infer it is
confirming and flip to a biased binary verdict — held regardless, because it rests on prompt
byte-identity, not on the field. Nothing ever threaded `confirm_state` (or any equivalent signal) into
a prompt, so a confirming critique was always indistinguishable from a discovering one *to the model*.
`tests/test_isolation.py::test_confirmation_critique_is_byte_identical_to_a_normal_one` already covered
that half and needed no change. What was missing was the half the docs also promise: that the audit
record itself, read after the run, can tell the two apart.

**The decision.** `State` gains a `confirming: bool` field, set by `_control` in the same branch that
already sets `pending_lenses` for a `recritique` action: `True` only when `decision.rule == 8`, `False`
for `decision.rule == 2`, and reset to `False` at intake and on every fresh generation (the same two
places `pending_lenses` is reset to the full lens list). `_critique` reads `state["confirming"]` once,
after building every prompt from `pending_lenses`, and stamps `confirm_state=True` via
`LensResult.model_copy(update=...)` on each result `work()` returns for that pass — including a
result built from an unstaffed slot, so a rule-8 pass that could not find an eligible critic is still
labeled as one. The copy happens strictly after the model call returns (or, for an unstaffed slot,
after the roster-exhaustion path decides no call will be made), so the label is order-of-operations
incapable of reaching the prompt that produced the result it is attached to.

No acceptance or controller logic reads `confirm_state`: `acceptance_state`, `roles.lens_statuses`, and
`toppable` computation are unchanged and keyed on clean records and `used_critics` exactly as before.
This is a labeling fix to the audit record, not a behavior change to convergence.

**Verification.** `tests/test_review_depth.py::test_rule_8_confirmation_is_labeled_confirm_state_and_never_reaches_the_prompt`
drives a real rule-8 top-up (`review.depth: 1` against a two-eligible-critic roster, the same
configuration `test_depth_one_restores_the_single_critic_pass` uses) and asserts both halves: every
lens's discovery `LensResult` persists `confirm_state=False` and its confirmation top-up persists
`confirm_state=True`, and every critique call on a given lens — discovery and confirmation alike —
used the byte-identical prompt.

**Invariants.** None of the six. Author exclusion, the blind orchestrator, fail-closed lens validation,
severity floors, termination, and untrusted text reaching a generator are all unaffected — this closes
a gap between the audit record and its own documentation, not a live isolation or convergence property.
