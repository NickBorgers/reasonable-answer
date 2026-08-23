## D-repair-turn-context — a critic is shown the field it got wrong, and asked for a patch

**The problem.** In `llm.structured`, each repair attempt was a fresh single-turn prompt — system, the
original user turn, the schema instruction, the error string. The rejected JSON was discarded. The
critic was told the rule it had broken and shown the source text, but never the field it actually
emitted, so it had to re-derive the entire review from a prompt materially identical to the one that
had just failed.

**The decision.** Two changes, and the second is what makes the first honest.

The repair turn carries the rejected field value back, fenced as data and attributed to the
validator — *"a candidate issue was rejected"*, never *"your previous response"* — together with the
validator's guidance and the source text the corrected field must be drawn from.

And what is asked for back is a **patch**, not the review again: `{issue_index, field, replacement}`
for the rejected field alone (`schemas.IssueRepairs`), merged into the retained `CritiqueOutput` by
`triage.apply_repairs` and revalidated whole. An earlier draft of this decision echoed the field but
still re-asked for the entire review, with an instruction to leave the other issues alone. That
instruction cannot be obeyed: those values are not in the critic's context, so the model must
regenerate them, and the `category_out_of_scope` regression above is exactly that happening. The
patch removes the mechanism rather than asking a model not to trigger it — everything a repair does
not name is carried across mechanically, with nothing in the path that could rewrite it.

**The patch channel is narrow, and the narrowness is enforced, not requested.** The schema admits
only the four fields `triage.validate_issue` can reject — never `severity`, `rationale` or
`instruction`, so the channel cannot become a way to rewrite a finding under cover of fixing a
quote. And `apply_repairs` is told which issue and which field the validator rejected, and **drops
every entry naming any other issue or field**, however well-formed. That guard exists because the
repair turn embeds report-derived text (the source excerpt, the rejected value), and report text is
untrusted (RA-010): an instruction smuggled into a report paragraph that talked the critic into
patching a *different* issue would otherwise ride a valid-looking entry through whole-output
revalidation, which rejects malformed patches but not well-formed changes to another issue. The
repair turn states the same scope to the critic — naming the index and field the entry must carry —
so the instruction and the enforcement agree. A replacement that will not parse is dropped rather
than guessed at.

**Every model-side string in the repair turn is fenced or content-free.** The rejected value and the
source excerpt are both neutralised (fence markers replaced) and placed inside the repository's
untrusted-data fences; an earlier draft fenced only the rejected value and let the source excerpt
travel inside the validator's guidance string, outside the markers. The two now ride separate
parameters (`repair_hint()` for validator-authored instruction, `repair_excerpt()` for report text),
so the boundary is typeful rather than a convention inside one string. What remains outside the
fences is validator-authored: the violation message, the structural loci list, and the quoting rule —
closed-vocabulary text no report content can reach.

The category guard is directional, and its direction was wrong in the first draft of this decision.
RC-005 permits escalation and forbids downgrades, and downward is the direction that can change a
*convergence outcome*: `stylistic` is excluded from the counts unconditionally, whatever severity it
carries, so relabelling into it makes a material finding vanish and a lens with nothing else
outstanding reads clean off the back of a repair. The guard now refuses a replacement that would take
a finding `counts_for_convergence` accepts and make it one it does not — asking that predicate
directly rather than comparing floors, because `clamp_to_floor` only ever raises, so a floor
comparison answered "unchanged" for every relabel it was meant to catch and guarded nothing.

**One budget, both halves.** `budgets.critic_repair_retries` is passed to the review call *and* to
the lens loop: the review call spends it on schema violations — malformed JSON, a missing field —
and the lens loop on patch rounds. What moved out of `client.structured` is lens validation, not the
budget, so isolation.md and convergence.md keep naming one number for a critic's repairs. An
intermediate draft dropped the argument from the review call, which silently rehomed a critic's
schema repairs onto the generic `repair_retries` — 1 against 2, a change no document described.

**Isolation, and the evidence it rests on.** This is the one bounded exception to the fresh-context
rule, recorded as such in the drift table, the critic's NEVER row and the repair bullet in
[isolation.md](../isolation.md), plus the rule-2 narrative in [convergence.md](../convergence.md).

QP12 §4 requires new evidence, fetchable from a URL in the diff, before a principle gives ground.
That evidence is **Gou et al. 2023 (CRITIC)**, added to the References table: a verify → correct →
verify loop in which a model revises its own output against external tool feedback consistently
improves it, concluding "the crucial importance of external feedback". That is the positive half of
the boundary Huang et al. 2024 draws from the other side, and it is the shape built here. Its limit
is stated in the table rather than left for a reader to find: it evaluates factuality, program
synthesis and toxicity, not schema repair.

One claim is explicitly withdrawn. An earlier draft cited Chen, Su & Chiang 2026 as support for
attributing the value to the validator rather than the critic. That study moves a byte-identical
claim between chat-template *roles*; this keeps the value in a user turn and changes only prose
attribution, so the reported gain is not inherited and is no longer claimed. The wording stands on
its own rationale — the text at that point *is* a candidate a check rejected — with no evidentiary
weight behind it.

What is claimed is a bounded same-task exception — **not** that relabeling restores independence.

**RA-016 is unchanged.** `rejected_text()` and `repair_excerpt()` are read only by the repair path,
which stays inside the run. They are deliberately absent from `diagnostics()` and from `str(exc)`,
because those reach stdout and `LensResult.failure_reason`, which live outside the 0700 run tree. A
log still gets only `fingerprint()`; the text goes back to the model that emitted it and nowhere
else.

**Invariants.** Fail-closed lens validation is unchanged: a rejection still fails the whole review
once the budget is spent, and no subset of issues is salvaged. Author exclusion, the blind
orchestrator, severity floors and termination are untouched. The untrusted-text boundary is
unchanged in kind and narrowed in two respects — every report-derived string in the repair turn is
now fenced and marker-scrubbed, and the patch channel is mechanically confined to the one field the
validator rejected.
