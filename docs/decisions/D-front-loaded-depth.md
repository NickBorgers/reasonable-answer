## D-front-loaded-depth — two independent critics read every draft, per lens, before it is revised

**The problem.** Strong acceptance requires two cross-family non-author clean records per lens
(RC-001/QP2), but only one critic per lens ever read a draft. The second was deferred to
controller **rule 8**, which fires only after a pass has already reported `material == 0`. So the
second opinion was never part of *discovery*: the run acted on the first review's silence before
asking the second witness. Front-loading the configured slate makes every selected witness's
findings available to the same triage pass, which is mechanically checkable against the code and
`tests/test_review_depth.py` without relying on private run history (QP9).

**Decision.** Review depth is configuration, and the production default is **2 eligible non-author
critics per lens on every generated artifact**.

```yaml
review:
  depth: 2                 # critics per lens per pass; 1 restores single-critic discovery
  per_lens: {evidence: 3}  # optional per-lens override
```

`roles.critic_slate` draws the whole slate for a lens at once, because drawing one model at a time
from the same "already used" set returns the same alias every time. It is a **ceiling, not a
quota**: the slate is taken from *fresh* eligible models only, so a lens the roster can staff once
runs one critic and reaches `converged_unconfirmed` through rule 10, exactly as before. Every
existing eligibility rule applies per slot — `eligible_critics` drops the author and deduplicates
by resolved provider/model; `critic_slate` admits at most one model from each family; and
`assert_author_exclusion` re-checks at the moment of the call. No slate can contain the author, the
same model twice, or two same-family checkpoints presented as independent however large `depth`
is. `lens_statuses` likewise counts distinct clean families, so same-family records cannot satisfy
QP2's second-witness requirement.

Each critic in a slate is a separate `critique_once` — the same production prompt, a fresh context,
no knowledge that another critic is reading the same artifact and no sight of what it found. The
two are as blind to each other as the three lenses always were (isolation.md).

**Rule 8 keeps its job and loses its shift.** It is still the only way an under-cleared *clean*
artifact reaches `strong_met`, and it is still bounded by `confirmation_attempts`. What changes is
that at depth 2 a clean pass normally arrives already strongly-cleared, so rule 8 becomes the
top-up for **incomplete depth** — a critic that failed, a pool that ran short — rather than the
normal discovery path. No rule was added, removed, renumbered or reordered; no `ControllerInput` or
`OrchestratorView` field changed. The termination argument in convergence.md is untouched: depth
multiplies the calls a critique pass makes, and every measure that bounds the loop counts passes,
generations and budgets, not calls.

**Fail-closed keeps its meaning, at the right unit.** One bad field still fails the *review* it
appeared in, whole, after the repair budget — nothing is salvaged, nothing is dropped. What is
re-scoped is `lenses_failed`, which now counts lenses with **no completed review of this artifact**
(`triage.unreviewed_lenses`) rather than lenses whose latest result failed. The readings coincide
on every depth-1 discovery pass, but differ on one pre-existing path: a rule-8 confirmation that
fails after the lens already holds a completed review. Previously the failed confirmation
overwrote that review, made `lenses_failed == 1`, sent the run through rule 2, and exhausted at
rule 3 (`aborted`). The completed review now remains in the list, so the controller returns to
rule 8 when another qualified witness remains or falls through to rule 10/11. The same distinction
appears within a depth-2 slate when one critic completes and another fails. Aborting a clean,
reviewed artifact because a confirmation provider failed is the wrong answer to a flaky provider;
the shortfall is still not forgiven, because it lands on family-counted `cleared_count` and cannot
satisfy `strong_met`.

**Counting distinct findings, not reports of them.** Two critics on one lens routinely land on the
same defect, and with `search.verify_sources` on, both evidence critics are handed the *same*
mechanical `fabricated_citation` for the same dead URL. `triage.distinct_issues` collapses on the
key `to_defects` already used — `(section, paragraph, category, claim_span)` — so `tally`, the
defect list and the stagnation signature all see one finding once. Where two critics disagree on
severity the **higher** survives, which is the direction the mechanical floor already clamps in
(RC-005): letting whichever review was stored first decide would give a second reviewer the power
to soften the first. At depth 1 this is a no-op — categories are partitioned by lens, so two lenses
cannot raise the same key.

**Auditioning follows the front-loading.** `audition.roster_warnings` was position-aware on the
premise that "position ≥ 2 is unreachable on the first pass"; at depth 2 that sentence is false for
position 2, so the threshold is now read from `review.depth_for(lens)`. A marginal or unfit model
inside the depth window gets its own warning saying it now runs on **every** draft, and one outside
it keeps the old rule-8 warning. `audition.enforce` is unchanged and already gates every assigned
slot regardless of position: a cached `unfit` verdict on any critic in a lens pool fails startup
closed before the graph runs. Re-auditioning the newly front-loaded slots against the
production-shaped corpus is an operator step (`ra audition`), not something this diff can perform —
it needs the paid proxy.

**Cost.** A pass makes `depth × |lenses|` critic calls instead of `|lenses|`. `budgets.max_concurrency`
is unchanged, so the instantaneous load on the proxy is the same and the extra depth is paid in
wall-clock. The expected trade is fewer generations, which are the expensive step: a round avoided
saves a writer call, three-to-six critic calls and an orchestrator call.

**Deliberately not done.** No change to the decision table, to `OrchestratorView`, to author
exclusion, to the severity floors, or to what any critic is shown. `review` is deliberately **not**
part of `_run_fingerprint`: depth is read fresh at each pass and every per-artifact accumulator
resets on generation, so changing it mid-run is safe, and adding it would cost every in-flight run
its checkpoint. `graph._lens_results` accepts the pre-existing one-result-per-lens state shape for
the same reason. No A/B harness: `depth: 1` reproduces the previous single-critic discovery pass
from configuration, while the intentional failed-confirmation divergence above is pinned
separately; comparing the arms on real questions remains an operator measurement.
