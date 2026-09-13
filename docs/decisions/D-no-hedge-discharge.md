## D-no-hedge-discharge — a fix task is resolved by changing the claim, never by appending a qualifier to it

**The finding.** Across ten expert defect reviews of recent production runs, the same move appeared
in every run that failed to converge: the revision kept the flagged claim — its figure, scope, or
citation — and added a disclaimer. The sentence then no longer matched the filed finding even though
the unsupported claim survived. Aggregate observations included repeated caveats within a report,
high fix counts with little reduction in material defects, attribution language substituted for
support, absence claims made without searching, and time-sensitive answers based on stale evidence.

These aggregate observations come from the operator's private audit trail and contain no public
examples here. As in D-scoped-revision and D-claim-scoped-patch, they are the **motivation and not the
warrant** (QP9).

**The warrant is the mechanism, and it is checkable in the prompts.** `WRITER_PATCH_CLOSE` and
`writer_revision` said "resolve every fix task"; `critic_user` said an instruction "must allow
weakening the claim or adding an explicit caveat as an acceptable resolution". Nothing anywhere said
what weakening *means*. So the minimal edit that makes a flagged sentence stop matching its finding
— a qualifier — satisfied every rule in the system, and it is also the cheapest edit available. The
resolvability contract (D-run-date-grounding, D-conceptual-conflation) was written to stop critics
demanding documents the writer cannot get; it was read as a licence to keep the claim and hedge it.

**The decision.**

*Writer side.* A new `prompts.WRITER_RESOLUTION_STANDARD`, carried by every non-polish revision in
**both** `revision.mode`s, states that a task is resolved by changing the claim or its support and
never by appending a qualifier to a claim that is kept; that weakening means restricting the claim
to what the support establishes — a narrower population, a smaller magnitude, the cases actually
measured, one named source's finding — or removing it; that the four observed disclaimer forms do
not resolve anything and the defect will be filed again; that an evaluative qualifier never stands
in for a citation, a claim no source establishes being removed or labelled as the report's own
inference; and that a limitation is stated once where it applies, because D-claim-scoped-patch
carries a *fix* to every restatement and a caveat is not a fix. The goal sentence in
`writer_revision` names the same thing in one clause. It is shared rather than patch-only because
appending a qualifier is exactly as available under `rewrite`, so the D-scoped-revision A/B would
otherwise be comparing two arms that both have the defect; both closes are untouched, so the arms
still differ in exactly one thing. A polish pass has no fix tasks and does not carry it.

*Critic side.* The `instruction` bullet in `critic_user` keeps the resolvability contract verbatim in
substance — an instruction may still never demand a document the writer cannot obtain — and adds what
the guarantee costs: where the acceptable resolution is to weaken the claim, the instruction must say
what the weakened claim would be (the population, the magnitude, the source to attribute it to), and
an instruction whose cheapest compliant reading is "state that this is unverified" or "clarify that
this figure is the author's own calculation" must not be offered. Only that bullet moves — no other
line of `critic_user` is touched — so concurrent edits to the same function merge cleanly beside it.

*Search-gated.* Two rules join `WRITER_SEARCH_ADDENDUM`, gated on retrieval because both ask the
writer to go and look: an absence claim ("no source addresses this") is a claim about the literature
and is held to the same standard as any other; and where the newest evidence the report rests on is
more than a year older than the run date it already holds (D-run-date-grounding), on a question whose
answer moves, the writer searches for what changed and states how recent its evidence is.

*Measurement, warn-only.* `report.revision_scope` gains `additive_only`, on the `generate` event
beside `in_scope` / `restated` / `out_of_scope`: of the paragraphs a task named or whose previous
text restated a flagged claim, the ones whose revised text contains every word of the old text, in
order, and adds more (`report.only_added_words`). That is the exact signature of the move above. It
is a **subset** of `in_scope` plus `restated`, read as a rate against them, never a fourth bucket,
and nothing rejects a draft on it — the same sequencing D-scoped-revision and D-claim-scoped-patch
set, where the enforcing tier is only worth building if the numbers say the prompt did not hold.
Two properties are stated rather than hidden: attaching a citation to an uncited claim is a genuine
fix and is additive by construction, so it is counted (excluding it would require the measurement to
judge which additions are hedges, which is the judgement it deliberately does not make); and a
paragraph inserted *next to* an untouched flagged one is not counted, because there is no old text
it was added to. Comparison is on words, lower-cased with punctuation dropped, as `restates` does.

**Invariants.** None move, and in particular *untrusted text never reaches a generator as
instruction* is untouched: the writer's fix tasks are still validator-bounded `Defect` fields with
verbatim-anchored spans, and what changes is the prompt's definition of what discharging one of them
requires. Author exclusion, the blind orchestrator (no `ControllerInput` or `OrchestratorView` field
changes), fail-closed lens validation, severity floors and termination are all untouched: no
controller rule, floor, cap or budget is edited, and the measurement is a count on an audit event.

**The audition hash changes.** `critic_user` is inside the `prompt_hash` surface, so every cached
audition verdict is stale after this lands and `audition.enforce` reads *not audited* until an
operator re-runs `ra audition`. That is the documented consequence of any critic-prompt edit
(D-critic-audition), and it is warned, not failed. The writer prompts are not in that surface.

**Why not the alternatives.**

- *Reject an additive-only revision in the graph.* The enforcing tier is what D-scoped-revision
  deliberately deferred, and the same argument holds here with more force: a rejected draft costs one
  of three `writer_attempts`, and `additive_only` cannot yet distinguish a hedge from an added
  citation. Measure first.
- *Forbid the caveat escape in the critic contract outright.* That is the unsatisfiable-demand loop
  D-run-date-grounding was written to close — a critic that may not offer weakening will demand a
  document the writer cannot obtain. The escape is kept and given a meaning.
- *A new taxonomy category for a hedged claim.* It would need its own audition fixtures, floor and
  evidence, and the defect it names is already reportable: a claim whose hedge leaves it stronger
  than its support is `overstated_claim`, which is precisely what the runs above re-filed each round.
- *A mechanical rule that a defect cannot be re-filed against text that implemented its fix.* It
  would suppress exactly the re-filings that are correct here, which is the whole finding.

**Deliberately not done.** No enforcing tier. No critic-side staleness finding — the currency rule is
writer-side only, because a critic cannot search and a staleness demand it cannot bound is the
unsatisfiable-demand shape again. No change to `WRITER_SYSTEM`'s standards list or `LENS_BRIEF`. No
change to the report frame, the roster, any severity floor, or any budget. No live A/B in this PR:
`additive_only` against `in_scope` over the next runs on this build, read the way
[run-provenance.md](../run-provenance.md) prescribes, is the measurement.
