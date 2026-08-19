## D-completeness-pool-noise — a critic that invents work is removed from the lens it invents it on

**The measurement.** The public source record is
[issue #148](https://github.com/NickBorgers/reasonable-answer/issues/148), which records the
audition metrics, the 30-call manual-review protocol, its per-control result, and the two observed
failure signatures. The 2026-08-02 audition graded `mistral-large-3` **`unfit`** on completeness:
**2.61 material issues invented per sound control report**, the highest noise rate measured on any
(model, lens) pair and 2.6× the `max_control_material_rate` fail-closed line. Its sensitivity on
that same lens was **1.00** — it found every planted defect. The two numbers are not in tension,
they are the finding: this model reports *everything*, and on completeness most of what it reports
is not there.

A 30-call spot check confirmed the noise is the model's and not the corpus's, which is the reading
D-control-soundness exists to force us to rule out. Across **13 material issues filed on 5 of the 6
controls, zero were real defects**, and they fall into two reproducible signatures:

- **Hedge-blindness.** It reads explicitly hedged language as absolutist and then flags the
  absolutism it supplied. It flagged *"'Public health infrastructure' covers **at least** three
  distinct things"* as presupposing the three categories are exhaustive — the artifact's "at least"
  says the opposite, in the span the model itself quoted.
- **Inexhaustible-counterargument demand.** It files `omitted_counterargument` for an
  ever-further alternative mechanism on controls whose `## The strongest counterargument` section
  already engages the strongest objection at length — including cases where its own `related_span`
  quotes the passage stating the supposedly omitted point. This is not a bar a report can clear:
  every rebuttal admits one more mechanism, so a run reviewed by this critic on this lens stagnates
  rather than converging.

**Why the false-positive direction alone is disqualifying.** D-critic-audition already states that
both directions gate, and this is the case it was written for. The convergence loop is asymmetric
about the two errors: a missed defect costs one lens one round of evidence, while an invented
material issue blocks acceptance outright, spends a writer call and three-to-six critic calls
"fixing" prose that was correct, and pushes the run toward rule 13 `exhausted_unresolved` on a
report that was fine. Perfect sensitivity buys nothing back, because nothing downstream can tell an
invented material issue from a real one — the defect list is the same shape either way. A critic
whose findings must be discounted is not a cheap critic; it is a critic the harness cannot use.

**Decision.** `mistral-large-3` is removed from `roster.critics.completeness` in both shipped
rosters, and the remaining pool is ordered **fit-first**:

```yaml
completeness:
  - gemma4     # fit:      0.89 sensitivity, 0.17 invented per control
  - glm-5.2    # marginal: 0.72 invented per control
```

Ordering is load-bearing for the same reason D-critic-audition's position analysis was: whichever
slot a pass reaches first is the one whose *silence* the run acts on. Putting the measured-`fit`
model at position 1 also settles, for this lens only, the standing worry that `gemma4` is the same
checkpoint that flagged nothing across six completeness calls in `run-d5934276fafd`. That worry is
now answered by measurement rather than by hiding the model at position 3 — 0.89 sensitivity is a
model that reviews. On **evidence** `gemma4` is still at position 3 and still unmeasured, and the
roster comment there is unchanged.

**What survives the drop.** The pool keeps two model families (Google + Zhipu), so
`validate_roster_health` reports no `roster_limited` warning for completeness against any writer,
`critic_slate`'s one-model-per-family rule can still fill a depth-2 slate, and `lens_statuses` can
still count two distinct clean families — a strong `accepted` remains reachable on this lens. The
loss is bounded more tightly than the diff looks: `mistral-large-3` is a writer, so author
exclusion already emptied its completeness slot on every round it authored (R1 and every odd
round). On those rounds the pool *was* `{glm-5.2, gemma4}`; this change makes the even rounds match.

**What is genuinely lost, stated plainly.** Two things. First, mistral's Western corpus was this
lens's decorrelation argument against two CN-lab priors (QP2); the remaining pair is Google and
Zhipu, which is still cross-family and still cross-lab, but the pool is narrower than the other two
lenses. Second, at `review.depth: 2` a two-model pool has **no spare**, and the consequence is
worth spelling out rather than waving at, because it is not the gentle one. Suppose a depth-2
completeness pass where one critic returns a clean review and the other fails. `unreviewed_lenses`
is empty, so rule 2 does not re-ask; both identities are in `used_critics` (a failed review still
marks its critic used), so `unused_eligible` is 0 and the lens is **not toppable** — rule 8 has
nobody to call. And `roster_limited` is `eligible_count < 2` counted in *families*, which is
exactly 2 here, so rule 10's honest weaker guarantee does not apply either. The artifact is clean
with `cleared_count == 1`, and the run falls through to **rule 11, `exhausted_unresolved`**.

That is the pre-existing behaviour of a two-family lens, not a new rule — the same thing already
happened on completeness every round `mistral-large-3` authored — but before this change the even
rounds had a third family to top up with, and now no round does. Set against it: the third family
was the one filing 2.61 imaginary defects per sound report, which does not merely fail to confirm a
clean artifact, it *prevents* one. A lens staffed by two critics that can return an honest clean is
worth more than a lens staffed by three when the third manufactures work every round. Restoring
depth is the right end state, and the open item below says so — but the replacement has to be
measured first, because filling the slot on corpus-decorrelation reasoning alone is what produced
this.

**Deliberately not done.** `mistral-large-3` stays in the logic pool and in the writer pool. Its
logic verdict is `marginal`, not `unfit`, and the spot check attributed part of the logic-lens
noise *cohort-wide* — every critic on that lens — to defects in the control fixtures themselves,
which are being repaired separately; grading a model on a corpus known to be wrong is exactly the
mistake D-control-soundness names. The logic-lens roster call therefore waits for the post-fix
re-audition. Nothing here touches the audition thresholds, the fixtures, the grader, or any file
under `src/`: this decision is a roster edit, three tests pinning the composition it chose, and the
evidence for both — which is the whole point of having a measured eligibility term. No audition
cache is committed (`.ra-audition.json` is a property of the
deployment, not the repo), so the verdicts above are cited, not shipped — re-running `ra audition`
is what reproduces them.
