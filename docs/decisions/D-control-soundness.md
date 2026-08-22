## D-control-soundness — a control fixture must be sound under *every* lens, not one

Observed on a live `ra audition` of the shipped roster (9 critic slots, 10 fixtures, 3
repetitions). Every audited critic on the `evidence` lens graded `unfit`, and two of three on
`completeness` did. Sensitivity was not the problem — `obvious_sensitivity` was ≈1.0 across the
board. Every `unfit` verdict came from `control_material_rate` alone: 4.17, 3.00 and 2.67
material issues invented per sound control on `evidence`, 2.83 and 1.67 on `completeness`,
against a `max_control_material_rate` of 1.0.

The critics were right and the corpus was wrong. Both controls carried real, material,
lens-relevant defects:

- `control-sound-01` asserted that hospital construction "follows a trend that predates 1918,
  driven by the shift of surgery and childbirth into institutional settings" with no citation
  anywhere in the report, and attributed a Rockefeller funding claim and a "studies exploiting
  variation in local pandemic severity" claim to nothing.
- `control-sound-02` attributed "the literature attributes much of the gap to construction
  experience" and "studies that model whole systems … generally find" to no source, and cited a
  contested cost question to two sources.
- `control-sound-01` also attributed a specific statistic — US cities with permanently staffed
  health departments "roughly doubled between 1917 and 1923" — to Tomes (1998), a claim the
  fixture author could not stand behind on review. That is `misrepresented_source` in a fixture
  declared sound.

`uncited_claim` and `one_sided_sourcing` both floor to `major`, so each of those is material by
`_is_material`. An evidence critic doing its job correctly scored 2-4 per control against a
threshold of 1.0. The lens was **structurally unpassable**, and the ordering of the observed
noise rates — evidence worst, completeness next, logic barely affected — tracks exactly how many
genuine targets the controls handed each lens. Logic was cleanest because the controls were in
fact written to be logically sound: the one lens whose contract they honoured.

**Root cause.** `FixtureSet.for_lens` hands every control to every lens, deliberately and
correctly — noise is a property of the model, not of a planted category, and a control scoped to
one lens leaves the other two unmeasured on the direction D-critic-audition exists to measure.
But nothing held the corpus to the other half of that bargain. Each control declared a single
`lens:`, and the soundness review — such as it was — went only as far as that declaration.
`test_every_lens_sees_all_controls` asserted that every lens *sees* every control; no test, and
no prose, asserted that a control is *clean* for the lens seeing it.

**Decision.** A control is sound under all three lenses or it is not a control. Both shipped
controls are rewritten to that contract: every empirical claim carries an in-text citation that
resolves to a `## Sources` entry, the question's ambiguous term is named and decomposed rather
than assumed, the case against the report's own answer gets its own section, and a contested
question is sourced across genuinely different kinds of publisher, with the strongest opposing
case stated on its own terms and, where the cited sourcing cannot settle it, left honestly
unresolved rather than resolved by fiat. Attributions the author cannot stand behind are removed
rather than softened.

*Correction (D-fixture-report-shape's merge-reconciliation pass).* The paragraph above originally
read "and across both sides of the live dispute" for `control-sound-02`. A later edit to that
fixture removed the source representing the opposing side of its cost-contingency question,
leaving one academic source (Lovering et al.) for the "costs are contingent, not intrinsic"
reading and none dedicated to the other; the report's own text was already honest about this —
"Nothing cited here resolves that transfer" — but the decision's prose and the fixture's manifest
both kept describing a two-sided academic sourcing that no longer shipped. Both are corrected here
to describe what the fixture actually does: state the strongest opposing case and decline to
resolve it, rather than sourcing both sides of it.

**A control now declares neither `lens` nor `tier`, and the loader refuses one that does.**
Both fields were read only on the planted path — `for_lens` ignores them, `_check_lens_ownership`
has no defects to check, `obvious_total` counts planted defects only — so `lens: evidence` on a
control asserted a scope nothing enforced while reading, to anyone auditing the corpus, as
though soundness under that lens had been established. Deleting the field is the honest form:
there is no lens a control belongs to. `Fixture.lens` becomes `Lens | None`, and a *planted*
fixture with no lens is now rejected too, since nothing would grade it.

**The soundness itself is not mechanically checkable, and the tests say so rather than
pretending otherwise.** A claim carrying no citation marker at all cannot be distinguished by
regex from prose that needs none — a definitional sentence legitimately cites nothing, and a
blanket "every paragraph cites something" rule would push fixture authors to bolt fake citations
onto definitions. What *is* checked is the resolvable part: in-text citations resolve to
`## Sources` entries, entries are all cited, and a control carries at least
`MIN_CONTROL_SOURCES` distinct sources so it cannot earn `one_sided_sourcing` honestly. The
omission and presupposition classes that drive the `completeness` rate have no mechanical form
at all; they rest on the soundness contract recorded in each control's manifest and on review.
A gate that covers part of a property should say which part. (D-control-defect-sweep extends that
contract: a control must also be internally consistent across sections, which is the same
unmechanisable half of the property showing up on the logic lens rather than the evidence one.)

**Rejected: raising `max_control_material_rate`.** It is the correct threshold. A critic
inventing more than one material issue per sound report manufactures work every round, drains
`critique_attempts`, drives `stagnation_count` to its limit and terminates a fine report
`exhausted_unresolved` (rule 13). Moving the line to accommodate a broken corpus would convert a
measurement of the corpus into a permanently weakened measurement of the models.

**Rejected: per-lens `sound_for:` scoping on controls.** It would have made the existing corpus
pass by narrowing what each control claims. With two controls and three lenses, at least one
lens would then have had no control at all — its noise direction unmeasured, which is the exact
failure D-critic-audition was written to close. Making the fixture honest is the fix; making the
measurement smaller is not.

**Cache and blast radius.** Editing the artifacts changes `corpus_hash`, so every cached verdict
stops matching in `cached_judgements` and drops to *not audited* — never to `unfit`. The
`audition.enforce` gate blocks only on a positive `unfit`, so this is safe to land even in a
deployment running with enforcement on: it degrades to "re-measure", which is the correct
reading, because a verdict from the old corpus is a claim about a measurement that no longer
exists.

**This is the half D-scoped-revision deferred.** That decision measured `mistral-large-2512` at
4.25 material issues per call on the completeness lens across 20 calls, never once clean, and
declined to re-roster on it: "that is the other half of this problem and belongs to `ra audition`,
not here." It belongs here, and the finding reads differently now. A completeness critic scoring
against a control that genuinely omits counterarguments is not necessarily noisy — some fraction
of that 4.25 was the corpus. How much, only a re-audition against the rewritten controls can say,
which is the point of fixing the corpus before drawing a roster conclusion from it.

**What this decision does not establish.** The rewritten controls have not been re-auditioned —
that costs a paid proxy run, and the fix stands or falls on the defects quoted above, which are
in the corpus and reviewable without spending a call. The expectation is that `evidence` and
`completeness` verdicts improve materially. If they do not, the residue is a real over-flagging
finding about those models and should be recorded as one.
