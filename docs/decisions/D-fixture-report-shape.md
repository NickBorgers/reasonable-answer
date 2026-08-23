## D-fixture-report-shape — audition fixtures are production-shaped, and four ship with the sound base they were mutated from

Found by adversarial review of the audition harness after D-control-soundness landed, and
independently confirmed by a second reviewer. Two defects in the corpus, related closely enough
that fixing one without the other would have been wasted work.

**The fixtures were a document class production writers are forbidden to emit.**
`test_run_assignment_uses_the_production_critic_prompt` pins the harness to the exact critic
prompt a run uses, so the measurement is only worth what the fixtures' resemblance to a real
artifact is worth. `prompts.REPORT_SKELETON` (D-report-template) mandates: no top-level `#` title —
"the report is the body only" — `## Conclusion` as the first section, `## Key findings`, `## The
strongest counterargument` engaged on the merits, inline `[1]` citations, and a numbered
`## Sources` section last with that byte-exact heading. All ten shipped artifacts opened with a
forbidden `#` title, none had a conclusion, key-findings or counterargument section, and every one
used author-year citations against a bulleted, unnumbered source list. The corpus was auditing
critics on a shape no writer in this system may produce, which moves three things at once: the
locus distribution (a `#` title makes the thesis S1.P1 and pushes every real section down one),
the organization cues the completeness lens explicitly judges, and citation mechanics — an
evidence critic looking for a dangling `[7]` had nothing of the kind to look at.

**Corpus class was readable off form alone.** The two controls ran 652-656 words with conspicuous
objection and decomposition sections and six dense sources each; the eight planted artifacts ran
239-357 words with thinner sourcing. Sensitivity and noise are measured on disjoint fixture sets,
so any feature separating those sets is a shortcut past the measurement: a model could score well
by being conservative on long, visibly balanced reports and aggressive on short ones, having
detected nothing. Length was the cheapest such feature and nothing was watching it.

**Decision — one rebuild, three parts.**

*Every artifact follows `REPORT_SKELETON`.* No title, `## Conclusion` first, `## Key findings`,
`## The strongest counterargument` stated in the form its proponents would accept and answered by
naming what would have to hold for the conclusion to flip, topical sections, numbered `## Sources`
last. `test_every_artifact_has_the_shape_production_writers_are_told_to_emit` checks the
mechanical part of that per fixture, so a fixture added later cannot quietly reintroduce the old
form. Every planted locus moved; the manifests carry the new coordinates and
`test_planted_loci_exist_in_their_artifact` still proves they exist.

*Matched pairs break the form confound.* Each of this decision's eight planted artifacts is one
minimal mutation applied to a sound base report — a paragraph appended, one citation swapped for a
fabricated one, one cited sentence replaced by an uncited one, one counterargument section pointed
at a weaker objection. Four of those bases ship as controls (`control-base-dust-bowl-01`,
`control-base-remote-work-01`, `control-base-minimum-wage-01`,
`control-base-congestion-pricing-01`), so for those four the sound and defective documents match
on length, structure, citation density and topic, and the only thing telling them apart is the
defect. The other four bases are not shipped: rewriting every citation in the `one_sided_sourcing`
fixture is not a minimal mutation, and the remaining three were held back to bound cost. Two
unpaired controls remain, so the corpus is not merely a set of near duplicates. (The corpus grew
further in the same merge window: `misrepresented-source-01` and `overstated-claim-01`
(D-category-coverage), `omitted-counterargument-02` (D-obvious-per-lens) and `overstated-claim-02`
(D-minor-floor-fixtures, replanted from `loaded-language-01`) are independent additions, not
matched-pair mutations of a shipped base — so of the corpus's eleven planted fixtures, four ship
with their sound base and seven do not; the pairing was never meant to reach every fixture, only to
break the confound without requiring it.) Corpus-wide the length spread is 1.348x — recomputed
against the full merged corpus, 787 words (`overstated-claim-02`) to 1,061
(`overstated-claim-01`) — against 2.74x before this decision, and
`test_corpus_class_is_not_readable_off_length` holds it under 1.5x with each class's median inside
the other class's range (control median 866 sits inside the planted range 787-1,061; planted
median 893 sits inside the control range 842-913). Every artifact, including the fixtures the
sibling decisions above added, carries exactly five sources, and
`test_corpus_class_is_not_readable_off_source_count` requires the observed source-count values to
be identical between controls and planted fixtures for every lens. A lens only ever sees its own
planted fixtures plus the controls, so a source-count gap that closes in aggregate can stay wide
open inside `for_lens` — as it did on `completeness`, where the planted pair carried three and four
sources against five and six for the controls, before this decision and the sibling fixtures that
merged alongside it converged every planted fixture on five.

*The control pool grows from two to six.* `control_material_rate` is a mean over
`controls x repetitions` runs compared against a threshold of 1.0. At two controls and the shipped
`repetitions: 3`, one residual soundness flaw in one control moved that mean by 0.5 — half the
distance to `unfit`, which is exactly how the pre-D-control-soundness corpus mis-graded every
evidence critic. Six controls bound one control's leverage at 0.167, and
`test_shipped_corpus_loads_and_covers_both_directions` now fails below four. This closes the "a
third control fixture" open item.

**The cost, stated rather than buried.** `for_lens` hands every control to every lens, so four new
controls raise the aggregate across the three model-lens assignments in a full audition — the
aggregate, not a per-model-lens-pair figure; a lens-by-lens breakdown follows below. Merged
alongside the sibling audition work landing in the same round (D-category-coverage adds
`misrepresented_source` and `overstated_claim` fixtures, D-obvious-per-lens adds the completeness
lens's obvious-tier fixture, D-minor-floor-fixtures replants `loaded-language-01` as
`overstated-claim-02`), the shipped corpus carries 11 planted fixtures and 6 controls — 17 total —
for an aggregate of 29 fixture-runs (10 evidence, 10 logic, 9 completeness) against 14 before any
of this round's audition work landed, roughly +107% on `ra audition`, not the +10% the old open
item estimated for one extra control alone. Isolating this decision's own contribution: four
additional controls add 12 fixture-runs by themselves (three per control, one per lens — that part
does not depend on how many planted fixtures ship); the remaining growth, from 14+12=26 to 29,
comes from the three net planted fixtures the sibling decisions above added to the same corpus.
That is the price of the measurement being worth anything, and it is paid per audition rather than
per run: nothing in the graph path calls the audition. Editing any fixture changes `corpus_hash`
and so invalidates every cached verdict, by design (`load_fixtures` hashes raw bytes before slot
substitution) — this rebuild invalidates the entire audition cache, and every rostered critic must
be re-auditioned before `ra doctor` says anything about it again.

**`test_control_citations_resolve_in_both_directions` is parametrized over every control, not a
hand-written pair,** and its regexes now read the numbered form: `[n]` in the body, `n.` in
`## Sources`. It additionally requires the entries to be numbered `1..n` contiguously, because an
inline marker that resolves to the wrong entry is worse than one that resolves to none. The
D-control-soundness caveat still applies unchanged: a claim carrying no citation marker at all
cannot be distinguished by regex from prose that needs none, so the soundness contract in each
control's manifest and review remain the only cover for that half.

**Rejected: shipping all eight bases as controls.** Ten controls would take a full audition to 41
fixture-runs — aggregate across the three model-lens assignments, the same unit as the 29 shipped
in the merged corpus, not a per-model-lens-pair figure — for a reduction in per-control leverage
from 0.167 to 0.1. **Rejected: keeping the old artifacts and bolting a conclusion
section onto each.** The defect is not a missing heading — author-year citations, bulleted sources
and a title-first structure are all load-bearing parts of the wrong shape, and a partial conversion
would have left the citation-mechanics half of the ecological-validity gap open while looking
fixed.

**Deliberately not done.** No grading-code change: the locus window, the severity floors, the
material-issue count and `judge` are untouched, and this decision is only about what the corpus
contains. No change to the number of planted fixtures or to any lens's coverage. No change to
`repetitions`, whose default remains 3.
