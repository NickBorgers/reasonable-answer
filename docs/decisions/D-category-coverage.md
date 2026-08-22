## D-category-coverage — every material category needs a planted fixture, and coverage is now a test

**The problem.** The audition corpus covered every *lens* and was checked for exactly that
(`test_shipped_corpus_loads_and_covers_both_directions`). Per-lens coverage is not per-category
coverage, and the difference is load-bearing. `grade` scores the relaxed `same_lens` match and
`judge` gates on lens-level rates, so a critic that is wholly blind to one category still clears
every threshold on the strength of the categories its lens does cover. The lens reads as measured;
the blind spot is invisible in the report and in the cached verdict alike.

Three of the eleven non-stylistic categories had no planted fixture: `misrepresented_source`
(evidence, floors `major`), `overstated_claim` (logic, floors `major`), and `unclear_structure`
(completeness, floors `minor`). `misrepresented_source` is the sharpest case. The only instance
the corpus ever contained was an *accidental* one — the statistic `control-sound-01` attributed to
Tomes (1998), which D-control-soundness removed as a defect in a fixture declared sound — so the
category has never been legitimately measured on any model, in either direction. It is also the
category D-source-verification sharpens into a checkable fact once fetched pages are present, which
makes a critic's baseline competence at it worth knowing before that switch is turned on.

**Decision.** Every category whose mechanical severity floor is material — `major` or `blocking` —
carries at least one planted fixture, and `test_every_material_category_has_a_planted_fixture`
enforces it. Two fixtures are added to close the gap this decision found:

| fixture | lens | tier | the planted defect |
|---|---|---|---|
| `misrepresented-source-01` | evidence | obvious | A real, accurately listed source ([Durkin et al. 2022](https://doi.org/10.1037/dev0001301), *… through sixth grade*) is described in the body as reporting twelfth-grade outcomes it cannot contain. The paragraph before it cites the same paper correctly. |
| `overstated-claim-01` | logic | moderate | Randomized support establishing a small average effect is restated as a flat "prevents" with an individual-level expectation attached. The 2017 IPD meta-analysis reports aOR 0.88 (95% CI 0.81–0.96) and NNT 33 (95% CI 20–101) overall, with a markedly stronger effect in the daily-or-weekly dosing subgroup (aOR 0.81 vs. 0.97 for bolus dosing) and below 25 nmol/L baseline (aOR 0.30 vs. 0.75) ([Martineau et al. 2017, fetched via PMC5310969](https://pmc.ncbi.nlm.nih.gov/articles/PMC5310969/)); two later large trials report no reduction ([CORONAVIT, fetched via PMC9449358](https://pmc.ncbi.nlm.nih.gov/articles/PMC9449358/); [Brunvoll et al. 2022, fetched via PMC9449357](https://pmc.ncbi.nlm.nih.gov/articles/PMC9449357/)). |

Both are authored in `prompts.REPORT_SKELETON` shape — no `#` title, `## Conclusion` first, `[n]`
citations, a numbered `## Sources` — because a fixture that does not look like production input
measures the critic on a document shape it never sees.

**Every cited number is fetch-verified, per QP9/QP10.** The first draft cited a 2021 aggregate-data
update (Jolliffe et al., *Lancet Diabetes & Endocrinology*, reporting OR 0.92) alongside the 2017
IPD meta-analysis. Neither its publisher DOI nor its medRxiv preprint mirror returns fetchable full
text through the review pipeline's fetch boundary — both return a 403 or a bare landing page — so
the claim was unverifiable and the `quality` reviewer correctly blocked on it twice
(`qual-claim-unsupported-1`). It is removed rather than patched with a better link, because no
fetchable full text exists for it: `overstated-claim-01` now rests entirely on the 2017 IPD
meta-analysis (fetched via PMC5310969, which hosts the full text openly) plus the two null trials
(PMC9449358, PMC9449357), and the planted defect — S5.P2 flattening a hedged, subgroup-concentrated
finding into an unqualified "prevents" — needs only that one paper's aOR, NNT and two subgroup
splits to be licensed. `misrepresented-source-01`'s citation (Durkin et al. 2022) was already
fetch-verifiable and is unchanged.

**Why the rule stops at the material floor.** `_is_material` gates every hit in `grade`, so a
detection on a minor-floor category scores only when a critic volunteers an escalation above the
floor. Requiring a fixture for `unclear_structure` or `loaded_language` would therefore assert a
measurement the grader cannot reliably make: the fixture would be graded as a miss against critics
that found it and filed it honestly at `minor`. `loaded-language-01` already sits in the corpus and
already has this problem; **it is diagnostic, not a sensitivity measurement**, and the same holds
for any `unclear_structure` fixture. No fixture is added for `unclear_structure` here, and
[concepts.md](../concepts.md) now says which categories the corpus can and cannot score. Whether the
grader should credit a minor-floor detection at all is a separate question about scoring, not about
coverage, and it belongs to the issue that raised it rather than to this one.

**Why not simply require a fixture per category, floor be damned.** Because the resulting corpus
would encode a claim the harness cannot honour. A coverage rule whose satisfaction leaves a
category still unmeasured is worse than an acknowledged gap: it converts "we have not measured
this" into "we have", which is the same substitution D-control-soundness was written to undo.

**Tiering.** `obvious` gates a fail-closed verdict (`obvious_hits == 0` → `unfit`), so it is
claimed only where a competent critic must catch the defect. `misrepresented-source-01` earns it:
the tell is bibliographic and sits on the face of the Sources entry, the evidence lens brief names
this exact case, and the same source is cited correctly one paragraph earlier.
`overstated-claim-01` does not: it is a hedge-drop two sections after the numbers that constrain
it, `prevents` is ordinary shorthand for `reduces the risk of`, and a fast reader can wave it
through without being incompetent. Each fixture's manifest records that reasoning where the next
author will read it.

**Cache and blast radius.** New fixture directories change `corpus_hash`, so every cached verdict
stops matching and drops to *not audited* — never to `unfit`, and `audition.enforce` blocks only on
a positive `unfit`. Safe to land with enforcement on: it degrades to "re-measure", which is correct,
because the corpus now measures something it did not measure before.

**What this decision does not establish.** No model has been auditioned against either fixture —
that costs a paid proxy run. Two things are therefore unknown and should be read as open: whether
rostered evidence critics actually catch an on-its-face misrepresentation, and whether
`misrepresented-source-01` is honestly `obvious`. If a re-audition shows competent models missing it
while catching the other three evidence fixtures, the tier is wrong and should drop to `moderate`
rather than the roster being re-cut around it.

An adversarial review round caught an accidental second instance of this fixture's own category:
three loci attributed third-grade outcomes to source [1] (Puma et al. 2010), whose own follow-up
window is fetch-verified (ERIC ED507845) to end at 1st grade. A doctrine-compliant critic scoring
that would have been graded a miss on an `obvious` fixture for catching the wrong instance, or
credited once for catching both. Fixed by re-citing those loci to the 2012 Third Grade Follow-Up
report (a new source [9], ERIC ED539264, fetch-verified) rather than by deleting the claims — the
report is still allowed to say preschool's advantage fades by third grade, it now just cites the
paper that actually says that. Exactly one planted defect remains, at S4.P3.

**Invariants.** None in reach. Fixtures are test data: they enter no production path, and author
exclusion, the blind `OrchestratorView`, fail-closed lenses, the severity floors and controller
termination are all untouched. The audition already hands fixture text to a critic as untrusted
report content under the sentinel author `AUDITION_AUTHOR`, and these two fixtures use that path
unchanged.
