## D-minimax-retirement — a critic unfit on every lens it holds leaves the roster

**The measurement.** The public source record is
[PR #162](https://github.com/NickBorgers/reasonable-answer/pull/162), whose body records the audit
dates, extraction modes, metrics, call counts, and per-issue spot-check result stated here. Three
audits agreed, and the trend worsened as the measurement got more honest. On 2026-08-02 (first run
on the reshaped corpus, prompt-mode extraction) `minimax-m3`
graded `unfit` on logic and `marginal` on evidence. After the control-defect sweep
(D-control-defect-sweep) and probe parity (D-audition-probe-parity) — so on corrected controls,
under the `json_schema` mode a run actually pins the model to — it graded `unfit` on evidence with
`marginal` logic. On 2026-08-07, against the 22-fixture corpus: **`unfit` on both.** Logic: 1.12
invented material issues per sound control, obvious-tier sensitivity 0.33. Evidence: 1.52
invented, sensitivity 0.58. Every other rostered critic improved or held as the corpus was
repaired; this one worsened.

**Why the noise is the model's.** The 30-call spot check behind D-control-defect-sweep read every
issue this model filed against the artifact it was filed on. Nine of its twelve material findings
on sound controls shared one mechanism: the critic flags a clause while its own quoted
`related_span` contains the adjacent qualifier — same sentence or paragraph — that resolves the
complaint. That is not a corpus defect and no fixture edit can fix it. A critic that cannot
return an honest clean does not converge a run: every invented material issue blocks acceptance
and burns a revision round, and at position 2 inside `review.depth: 2` it read every draft.

**The decision.** `minimax-m3` leaves both critic pools. It was critic-only, so it leaves the
roster. Both pools keep two families: logic is `[mistral-large-3, glm-5.2]` (Mistral + Zhipu),
evidence is `[glm-5.2, gemma4]` (Zhipu + Google), so `validate_roster_health`'s strong-`accepted`
requirement holds on every lens.

**Ordering, by the D-completeness-pool-noise rule — the pass acts on position 1's silence.**
Logic leads with its only measured `fit`, `mistral-large-3` (0.94 sensitivity, 0.08 invented per
control), with `marginal` `glm-5.2` (1.00 sensitivity, 0.75 invented) behind it. The cost is
stated rather than hidden: mistral also writes, so on rounds it authors, exclusion thins the
logic pool to `glm-5.2` alone. Evidence has no `fit` to lead with — both survivors are marginal —
so the higher-sensitivity model (`glm-5.2`, 0.92 against `gemma4`'s 0.50) takes position 1,
because a position-1 *miss* is the failure the run acts on when everything else is equal-noise.

**What this deliberately does not do.** It does not swap in a replacement candidate for the
evidence lens, which is now the roster's thinnest (two marginals, one of them at 0.50
sensitivity). Auditioning a candidate is a paid measurement against the full corpus and belongs
to its own decision; it is recorded as the open item below. It also does not touch thresholds:
every number above cleared or failed the existing gates without adjustment, which is what the
gates were rebuilt to do.
