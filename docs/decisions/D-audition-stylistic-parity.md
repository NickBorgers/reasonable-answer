## D-audition-stylistic-parity — the grader counts what triage counts, from one predicate

Found by adversarial review of the audition harness after D-control-soundness, and independently
confirmed by a second reviewer. `audition._is_material` claimed to mirror production —
"Severity after the mechanical floor clamp, which is what triage would count" — and did not.
It computed `max(severity, SEVERITY_FLOOR[category])` and stopped there. Production excludes
`stylistic` from convergence **unconditionally**, in four places: `to_defects` skips it, `tally`
skips it, `defect_provenance` skips it, and `clean_records` excludes it "even if the critic
escalated its severity".

Escalation is not hypothetical. `validate_issue` checks category scope, locus existence and
verbatim spans; it never checks severity, and RC-005 says clamps go up only — so a critic may
legally report a `stylistic` issue at `major`, and `stylistic` is in `LENS_CATEGORIES` for every
lens. Two measurements diverged from the thing they claim to measure:

- **Sensitivity was inflated.** `grade` credits a defect as found when any in-lens material issue
  lands in the locus window, and `obvious_hits` — the input to the fail-closed "found 0 of N
  obvious" gate — uses that relaxed form. A `stylistic` note filed at `major` on the planted
  paragraph scored as a detection. In a real run the same finding is discarded before the
  controller sees anything, and the planted defect sails through. The harness would have reported
  a critic as sighted on precisely the artifact it was blind to.
- **Noise was inflated.** `material_issue_count` counted the same finding on a control, feeding
  `control_material_rate` and its `unfit` gate, whose reason string reads "runs would stagnate
  rather than converge". A stylistic finding cannot stagnate a run: it is absent from the tally
  that `signal_signature` keys on and it never withholds a clean record. The gate would fail a
  usable critic for findings production throws away.

Both errors point the same way — toward believing the audition rather than the run.

**Decision.** The exclusion is not restated in the audition. `taxonomy.counts_for_convergence`
is now the single definition of "material issue": `stylistic` is out before severity is read,
everything else counts at its clamped severity. `audition._is_material` delegates to it, which
fixes `grade`'s `same_lens` and `material_issue_count` together, and `triage.clean_records` and
`triage.defect_provenance` were rewritten to call it — behavior unchanged, but the two consumers
now cannot disagree without one of them being edited on purpose.
`test_grader_materiality_agrees_with_triage_for_every_category` asserts the parity directly, over
every (lens, category, severity) a critic could legally report, rather than over the one case
the previous test happened to cover (`stylistic` at `minor`, where the old code was accidentally
right).

**Not changed.** Triage and the controller were already correct, and the fixture corpus is
untouched — `corpus_hash` deliberately does not move, because nothing about what is being
measured changed, only how a critic's answer is scored.

**Known gap: cached metrics predate this rule.** The audition cache is keyed on
`corpus_hash`, `prompt_hash` and `repetitions`, none of which this change touches, so entries
recorded before it survive and are still read by `ra doctor` and by the `audition.enforce`
startup gate. Those numbers were graded by the old predicate and can be wrong in both directions
— an inflated `obvious_hits`, an inflated `control_material_rate`. `ra audition --force` re-grades
a slot. Extending the cache key to cover the grader's own identity is a caching-semantics question
tracked separately and deliberately not solved here.
