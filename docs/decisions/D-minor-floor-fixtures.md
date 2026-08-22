## D-minor-floor-fixtures — an audition fixture plants only what the grader can credit

Found by adversarial review of the audition harness after D-control-soundness, and confirmed by a
second reviewer through direct simulation of `grade`.

**The problem.** `tests/fixtures/audition/loaded-language-01` planted a `loaded_language` defect.
Every detection credit in `audition.grade` requires `_is_material` — severity at or above `major`
after the floor clamp — because that is what triage counts and what a report would ever be revised
for. `loaded_language` floors at **minor**, deliberately and for a good reason (D-social-bias): it
is the most judgment-laden bias category, and a material floor would let one noisy critic force a
rewrite every round. So a critic that reported *the planted category, at the severity the taxonomy
assigns it* scored `strict = False` and `same_lens = False` — blind — and its `lens_sensitivity`
fell, which is a warn-level input to `MARGINAL`. The fixture measured willingness to escalate, or
to relabel as `overstated_claim`, not detection of the defect it declared. A rubric that penalizes
the doctrinal reading of a category is measuring the taxonomy, not the model.

Note what this is *not*: it is not an argument for raising the floor. The production pipeline
already accepts the consequence of a minor floor here — D-scoped-revision records framing lock-in
as a known residual precisely because a `loaded_language` finding is not material and does not
force a revision. A fixture whose planted defect no run would ever act on cannot be the thing that
grades a critic's fitness for runs.

**Decision.** The fixture is re-planted as `overstated_claim` and renamed
`tests/fixtures/audition/overstated-claim-02`. `-02` rather than `-01`: D-category-coverage landed
concurrently and independently claimed `overstated-claim-01` for its own fixture (a vitamin-D
report restated from a hedged, subgroup-concentrated effect into a flat "prevents"). Two
`overstated_claim` fixtures at `tier: moderate` is not redundant coverage — different domains and a
different defect shape, a counterfactual-certainty claim here against a hedge-drop there — so both
stand. The evaluative wording ("squandered", "a boondoggle of an outreach initiative") is
**removed** rather than left beside the new defect, and the "Spending and execution" section now
asserts a counterfactual certainty — the appropriation's size "was never the binding constraint",
"no increase in funding could have delivered the 300 beds on schedule" — that the two findings it
rests on do not establish. `docs/bias.md` §3 already routes exactly this case: framing that changes
the strength of a claim is `overstated_claim`, not `loaded_language`. Removing the wording rather
than keeping both is the point of the change: leaving it would have preserved a reading of the
locus on which a doctrine-compliant critic still scores zero. The artifact is also rewritten into
`prompts.REPORT_SKELETON` shape (`## Conclusion` first, `## Key findings`, `## The strongest
counterargument`, topical sections, `## Sources` last, no top-level `#` title) — the precedent
D-obvious-per-lens and D-category-coverage set for new fixtures in this corpus — rather than left in
the older `# Title` shape it previously used, so a critic is graded on the document shape production
actually hands it.

**The rule is mechanical, not a note.** `_check_planted_floor_is_material` refuses at load any
planted category whose floor is below `MATERIAL_FLOOR` — today `loaded_language`, `stylistic`,
`unclear_structure`. This is the same shape as `_check_control_manifest` under D-control-soundness:
the corpus property that review kept failing to hold becomes something the loader holds. It says
only that such a category is not measurable *by this grader*, whose single bar is materiality. This
supersedes D-category-coverage's characterization of `loaded-language-01` as "diagnostic, not a
sensitivity measurement": that fixture no longer exists, and nothing planting a minor-floor category
can exist in this corpus going forward — not diagnostic, unrepresentable.

**`severity_agreement` was not a rate.** The same code carried a second defect, and the
loaded-language case was the one that exposed it: `severity_agreements / strict_hits`, with
`severity_agrees` computed independently of material detection, so a non-material report of a
minor-floor category incremented the numerator while contributing nothing to the denominator and
the ratio could exceed 1.0. It is now derived from the same issue list that decides `strict`, so
the numerator is a subset of the denominator by construction. Second, agreement was equality with
the floor, which scored a **legal escalation** — `blocking` proposed on a major-floor category, the
one direction RC-005 permits — as *dis*agreement. It is now "at or above the floor": the metric
asks whether the clamp had to lift the critic's judgement, which is the question worth asking. The
name is kept over `exact_floor_rate` because the corrected definition is agreement-with-the-floor,
not equality-with-it. Nothing reads the metric — it is neither displayed by `ra doctor` nor gated
on — so this is a correctness fix ahead of a consumer, not a behaviour change.

**`RUBRIC_VERSION` bumped to 2 (D-audition-rubric-identity).** `grade`'s `severity_agrees`
computation is exactly the "strict / same-lens / severity-agreement matching rules" category that
decision names as requiring a hand bump. A cached `Metrics.severity_agreements` recorded under
version 1 could exceed `strict_hits` — the bug this decision fixes — so it must not be read as
though it meant the same thing as a version-2 count. The bump invalidates every stored entry via
`rubric_hash`, which is safe under `audition.enforce`: entries drop to *not audited*, never to a
false `unfit`.

**Coverage cost, stated plainly.** The corpus no longer covers `loaded_language`, and it is
**deliberately uncovered** by the gating corpus — this is the record of why. Covering it needs a
non-gating diagnostic channel — a correct-category, correct-locus, floor-severity report credited
in a `diagnostic_hits` metric excluded from `planted_total` — which was rejected here for two
reasons. It changes what `ra audition` reports and what `ra doctor` shows, so it is its own decision
with its own consumer; and D-critic-audition's argument against an inert `enabled` flag applies to
an inert metric too. It is an open item below.

**Rejected: keep the fixture and document that it measures escalation.** Weakest of the three
options considered. `lens_sensitivity` is not a diagnostic-only number — it is compared against
`warn_lens_sensitivity` and produces a `MARGINAL` verdict — so "documented" would mean a
doctrine-compliant critic is knowingly marked down in a gated metric.

**Cache and blast radius.** Editing the corpus changes `corpus_hash`, so every cached verdict stops
matching in `cached_judgements` and drops to *not audited* — never to `unfit`. Safe under
`audition.enforce`, which blocks only on a positive `unfit`, for the same reason D-control-soundness
was: a verdict from the old corpus is a claim about a measurement that no longer exists.
