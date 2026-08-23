## D-control-defect-sweep — the noise metric measured the corpus too, and five control defects fall out of it

**The observation.** The first `ra audition` on the reshaped 17-fixture corpus (2026-08-02) flagged
several critics as noisy on the controls. A 30-call spot check that read every filed issue against
the artifact it was filed on found that a substantial share of the supposedly invented issues were
**real, material, lens-relevant defects in the controls** — D-control-soundness's failure mode a
second time, in a corpus that had been rewritten specifically to close it.

That is not a new kind of mistake, but it is a new piece of evidence about the harness: three of the
five defects below were found by the critics the controls were grading, on the run that was
supposed to be grading *them*. `control_material_rate` counts findings a control should not admit;
it cannot tell "the model invented this" from "the fixture author was wrong", and this round it was
measuring the second. The metric worked in both directions — it is a joint measurement of the model
and the corpus, and a spike in it is a hypothesis about either.

**The five defects.**

| fixture | locus | category the critics filed | what was wrong |
|---|---|---|---|
| `control-base-congestion-pricing-01` | S1.P1 | `overstated_claim` / `uncited_claim` | "none of the **four** reviewed here has been repealed" against three implementations — London [1], Stockholm [2], New York [3]. Both `## Key findings` and the evidence section list three, and `omitted-counterargument-01`, cut from this same base, still says three. |
| `control-base-remote-work-01` | S2.P1, S4.P1 | `misrepresented_source` | Bloom et al. (2015) decomposed backwards: "roughly two-thirds from more calls per minute and one-third from more hours worked". The paper reports 9 of the 13 points from more minutes worked per shift and 4 from more calls per minute. |
| `control-base-minimum-wage-01` | S4.P2 vs S3.P2 | `contradicted_claim` | S4.P2 said the bunching design "answers the comparison-group problem"; S3.P2 said that same design "rest[s] on contestable comparison groups". |
| `control-sound-02` | S5.P2 | `invalid_inference` | "Much of the distance between the two results is that discount-rate assumption" — a cross-study attribution licensed only by [2]'s own within-study sensitivity. |
| `control-sound-01` | S5.P2 | `contradicted_claim` | "the component on which the answer above is most confidently negative" against S3.P2's "hedged rather than negative" and S1.P1's "absence of studies, not absence of effect", with the reconciling distinction never stated. |

The Bloom correction is fetch-verified per QP9/QP10 rather than derived by negating the wrong
sentence: the published abstract reads "a 13% performance increase, of which 9% was from working
more minutes per shift (fewer breaks and sick days) and 4% from more calls per minute"
([QJE 130(1), 165–218](https://doi.org/10.1093/qje/qju032); [NBER w18871](https://www.nber.org/papers/w18871)).
The fixture also glossed the 9 points as "more hours worked", which implies longer shifts rather
than the fewer breaks and sick days the paper attributes them to; both sentences now say "more
minutes worked per shift".

**Decision.** All five are corrected in the fixtures, and the soundness contract in each manifest
records what was wrong and why the corrected sentence is what the sources carry. Two of the
corrections extend that contract past its previous scope: it required every empirical claim to
carry a resolving citation, and said nothing about whether two sections could assert opposite
things. **A control is internally consistent across sections, or it is not sound** — an artifact
that contradicts itself hands the logic lens a real finding, and D-control-soundness's rule ("sound
under every lens or it is not a control") already implies this. The two `contradicted_claim` defects
are the demonstration that the implication needed saying.

Each correction is the smallest edit that removes the defect and leaves the report's posture
intact. `control-sound-01` keeps its hedge and gains the sentence that makes the sanitation
confidence consistent with it; `control-base-minimum-wage-01` qualifies S4.P2 rather than deleting
S3.P2's caveat; `control-sound-02` says what [2] actually shows instead of dropping the paragraph.
No source is added or removed in any fixture, so the source counts `test_corpus_class_is_not_readable_off_source_count`
compares are unchanged, and the length band still clears `MAX_LENGTH_SPREAD`.

**Two planted fixtures are edited, and that is what keeps them measuring.** `fabricated-citation-01`
and `uncited-claim-01` share the corrected prose with their control bases — the paragraphs are
literally the same text, because each plant *is* its base with one paragraph mutated. Correcting
only the control would make the pair differ in two paragraphs instead of one, and the second
difference would be a feature separating the noise set from the sensitivity set, which is the
confound D-fixture-report-shape and D-conceptual-conflation exist to keep out. The mirrored edits
are byte-identical to the control's and touch no planted locus, no manifest `defects` block, no
threshold and no grader: the plant/control delta is exactly what it was, namely the planted defect.
`MINIMAL_PAIRS` does not yet assert one-paragraph minimality for these two pairs — their manifests
claim it in prose — so no test forced this; the diff was checked by hand and the differing-hunk
count is unchanged.

**Consequence for the cache.** Editing any fixture changes `corpus_hash`, and
`AuditionEntry.matches` therefore invalidates every cached verdict (D-audition-rubric-identity).
That is the intended behaviour and the reason the hash covers artifact bytes: the previous verdicts
were measured against a corpus that graded competent critics as inventors, so they are not verdicts
about the models. A re-audition follows this change, and the noise figures it produces are the
first ones that mean what `control_material_rate` says they mean.

**Deliberately not done.** No threshold moved. `max_control_material_rate` stays at 1.0: the fix for
a corpus that hands out real findings is to stop handing them out, not to raise the bar for how
many a control may hand out. No test is added — the two mechanically checkable properties here
(citation resolution, source counts) already have tests that pass, and the three that are not
mechanically checkable are exactly the ones D-control-soundness assigned to review and to the
manifest contracts, because no regex distinguishes a paragraph that contradicts another from one
that qualifies it. The audition structured-output probe gap and the completeness-pool roster
change are filed separately and are not touched here.
