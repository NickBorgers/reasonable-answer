## D-scoped-revision — a revision edits the paragraphs it was asked about, and stagnation buys one rewrite

**The problem, measured.** Six consecutive production runs finished with **zero `accepted` and zero
`converged_unconfirmed`**: four hit `hard_cap` with major issues (`exhausted_unresolved`), two reached
`needs_human_review`. The cause was not that fixes failed to land — `defects_applied` tracked the
material count round for round, so the writer was applying essentially every fix task it was given.
Across all 36 regenerations in those runs:

```
mean material before a regeneration: 5.39      mean after: 4.81
```

The count did not fall. Each revision retired about as many defects as it created. Broken out by
critique pass, the process is stationary from the second pass onward, and the completeness lens gets
*worse* the longer a run goes:

| pass | logic | evidence | completeness |
|---|---|---|---|
| 0 | 1.7 | 5.3 | 1.0 |
| 2 | 0.8 | 1.0 | 0.8 |
| 4 | 0.8 | 3.2 | 2.4 |
| 7 | 1.6 | 2.0 | 3.2 |

(mean issues per critic call, all six runs pooled)

The consequence is that acceptance was not merely rare, it was arithmetically out of reach. Observed
per-call clean rates were logic 0.40, evidence 0.20, completeness 0.14, so a single pass clears all
three lenses about 1.1% of the time — and `accepted` needs that to happen twice, once to reach
`material == 0` and again for the rule-8 confirmation top-up. `material` hit 0 twice in 45 triage
passes. Rule 8 fired once in six runs; the fresh critic returned 9 material issues and sent
`run-cabddb9a612b` back for seven more rounds.

These figures are drawn from the `audit.json` trail of that six-run production set — a private
operator's own run history, not part of this public repository — so, as in D-question-refinement, the
run IDs stand only as opaque handles and the measurements cannot be re-fetched from the diff (QP9).
They are the *motivation* for this decision, not its warrant. What the decision rests on and pins
publicly is the **mechanism**: the arithmetic below is checkable against the code, and the tests this
PR adds — `tests/test_revision_scope.py`, the rule-13 branches in `tests/test_controller.py`, and the
valve behaviour in `tests/test_graph.py` — make the changed behaviour checkable from the diff itself,
the same way D-source-verification pins its empirical claim rather than resting it on private run
audit material.

**The mechanism.** `prompts.writer_revision` ended *"Return the complete revised report in Markdown —
the whole document, not a diff,"* and `roles.next_writer` hands each revision to a **different** model.
So every round, a model that did not write the text regenerated ~1,800 words in order to repair ~5
paragraphs, and every passage the critics had just cleared was re-rendered by a model with different
priors. Fixing five paragraphs by re-rolling forty is a losing trade, and the numbers above are what
losing it looks like.

**Decision, part one: scope the edit.** `revision.mode: patch` (the default) tells the writer to change
only the paragraphs a fix task names in its locus, plus whatever a task's instruction explicitly
requires elsewhere, and to return every other paragraph **byte-identical**. The output shape is
unchanged — still the whole document, because the artifact hash is taken over the whole document and
every downstream reader wants a complete report. What changes is the licence to re-render text nobody
complained about. `revision.mode: rewrite` reproduces the previous prompt byte for byte, so the two are
A/B-comparable from configuration rather than from a checkout.

**Why this is not an echo chamber.** The objection to patching is that a blind spot planted early is
never challenged again. [isolation.md](../isolation.md) already answers it, and the answer is that
writer rotation was never carrying that load: principle #7 "is fundamentally about *not sharing a
context*, not about model identity," and model diversity is named there as "a second, independent
layer … each dimension blessed by ≥2 distinct non-author models." Decorrelation is assigned to the
**critic roster**. Writer rotation appears in none of the seven principles; its stated justification in
`config/roster.yaml` is availability (D-provider-retry). Three properties therefore hold unchanged, and
they are what make patching safe:

1. **Rotation stays.** `roles.next_writer` and `roles.writer_pool` are untouched — a different model
   patches every round, and no model ever patches its own last draft.
2. **Critics still read the whole document.** Nothing in the critique path changes. Untouched prose is
   not unreviewed prose: a rotating critic pool re-reads every paragraph on every tick.
3. **Clean records still reset on every generation.** RC-002 is absolute. There are no locus-scoped or
   carried-over attestations — that is precisely where a real echo chamber would form. The convergence
   gain comes from a lower defect birth rate, not from reusing stale clearance.

**Decision, part two: stagnation buys one rewrite.** Scoping the edit means a run can only ever
accrete, and an accreted document — eight rounds of "add a sentence acknowledging X" bolted onto one
line of drafting — is exactly what the completeness lens punishes. Controller rule 13 gains a generate
branch: when the signal has been stagnant for `K` ticks and `rewrites_used < budgets.rewrite_cap`
(default 1), the run spends one **whole-document rewrite by a fresh writer** and lets the next tick
judge it on its own signal; otherwise rule 13 is the terminal it always was. This also gives a dead
rule a job — rule 13 requires the per-category `{blocking, major}` multiset to be byte-identical for
`K` ticks, and real trajectories jitter (`7,6,2,3,5,6,8,8`), so it never fired in any of the six runs.
`stagnation_count` is reset when the rewrite is granted; without that the next tick immediately
re-fires rule 13 and spends the whole budget in consecutive ticks.

**Termination survives, unchanged in kind.** `rewrite_cap` is finite and strictly decrements; the
rewrite is a generation, so it advances `round` toward `hard_cap` like rules 4, 9 and 14; and rule 13
is unreachable at or beyond the cap, because the cap-gated rules 5 and 6 precede it in the table
whenever `material > 0`. No new cap gate is required. The rule number stays 13 — the table is still
1–14, and rule 13 already branched internally on `blocking > 0`.

**Measurement, not assertion.** `report.revision_scope` diffs the previous and revised drafts by
paragraph *content* (never by locus number — inserting a paragraph renumbers every locus after it, and
fix tasks routinely ask for one) and records `changed_paragraphs`, `in_scope`, `out_of_scope` and
`defect_loci_untouched` on the `generate` audit event. It is **warn-only**, matching D-refine-audition's
warn-only doctrine: rejecting a draft would burn one of three `writer_attempts`, and a model that
reflows whitespace is not a reason to lose a run. An enforcing tier is worth building only if these
numbers say the prompt does not hold. The check is silent for the three generations that legitimately
touch everything — the first draft, a rule-9 polish pass, and a rule-13 rewrite — so an absent field
means "not applicable" rather than "in scope".

**Known residual: framing lock-in.** One model's voice and framing now persist across a patch chain
instead of being re-rolled every round, and `loaded_language` floors at `minor` under D-social-bias
precisely so a noisy critic cannot force revisions on judgment-laden framing. So a framing bias that
survives its first review is not caught as material and will ride the chain. The rule-13 rewrite is the
mitigation, and it is a partial one: it fires on a stalled signal, not on framing. Recorded here rather
than papered over.

**Deliberately not done.** No change to what critics receive, to author exclusion, or to the blind
orchestrator. No severity-floor changes — `omitted_counterargument` and `unexamined_presupposition` at
`major` were 11 of the 21 outstanding defects across these runs and are worth revisiting separately.
No roster change: `mistral-large-2512` scored 4.25 material issues per call on the completeness lens
across 20 calls and was **never once clean**, against `audition.thresholds.max_control_material_rate`
of 1.0 — that is the other half of this problem and belongs to `ra audition`, not here. `hard_cap` is
not raised; the pass table above shows no improvement after pass 2, so more rounds are pure cost.
