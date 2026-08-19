## D-refine-audition — refine prompts are auditioned with fixtures, and scope narrowing is a graded violation

D-question-refinement shipped its guardrails in two layers: three enforced mechanically, five as prompt policy
whose adherence was "tested statistically with fixtures" — except no fixtures existed, and the
known-gaps section of [question-refinement.md](../question-refinement.md) said so. The gap
stopped being hypothetical in production: "Is fluoride in tap water a net positive for public
health in the United States?" drew a `name_the_outcome` chip reading "What is the impact of
fluoride in tap water on dental health outcomes for children in the United States?" — the
stated domain (*public health*) silently narrowed to one component (*children's dental
health*). Nothing in the guardrails forbade it: the subject survived, no verdict was embedded,
no valence flipped. And for exactly the audience this question serves — someone whose doubts
about fluoridation are not about teeth — a dental-only reframe reads as steering toward the
sub-question with the most convenient answer.

**Decision, part one: the prompt forbids down-scoping, and the sanctioned move is
enumeration.** A new guardrail ("Preserve the scope") requires every rewrite to cover
everything the original covered; the `name_the_outcome` description now instructs the model to
make a scalar verdict measurable by *enumerating* the stated domain's component outcomes —
never by selecting one. Its trigger was also corrected: the old wording fired only on questions
with "no population, outcome, or timeframe named", which the fluoride question did not match
(it named both a population and a domain), so the transform was firing outside its own stated
trigger. `web.refine.PROMPT_VERSION` is bumped so no cached suggestion outlives the old prompt.

**Decision, part two: the refine surface gets the audition treatment.** Prompt policy that is
never measured is indistinguishable from prompt policy that is ignored — the same argument that
built `audition.py` after run-d5934276fafd. `refine_audition.py` runs a fixture corpus
(`tests/fixtures/refine/`) through the production prompt, schema, and deterministic filter,
grades the surviving suggestions with a **pure, mechanical grader** — never an LLM, for the
reason `audition.py` states: the grader must not depend on the property being measured — and
caches a verdict per (identity, enabled-transform set) that `ra audition-refine` writes and
`ra doctor` reads.

**The scope check is synonym groups, not exact strings.** A suggestion on a scope-carrying
fixture passes by retaining a breadth surface form ("public health") or by hitting at least
`min_groups` of the fixture's enumeration groups — dental, skeletal, neurological — where a
group counts once however many of its stems appear, so enumerating synonyms for one component
is still narrowing. Exact-string grading was rejected as brittle in both directions; an LLM
grader was rejected on principle. The residual brittleness (a legitimate phrasing the groups
miss) is bounded by three things: silence always passes, rates are measured over repetitions
rather than single shots, and anything speculative belongs in `tier: subtle`, which never
gates.

**The gating asymmetry is inverted from the critic audition, deliberately.** For a critic,
silence is the measured failure; for refinement, silence is the designed default (D-question-refinement), so a
low fire rate only warns while an obvious-tier violation gates. On `tier: obvious` fixtures the
tolerance is zero — the fluoride fixture is obvious precisely because it is the pinned
regression, and a model that narrows even once when silence was freely available is doing the
one thing the guardrails exist to prevent. Violations outside that pinned class do *not* make
the verdict `unfit`: the aggregate non-obvious violation rate (above `warn_violation_rate`)
only marginalizes, matching `judge_refine()`, which reserves `unfit` for obvious-tier
violations, schema failure, and control noise. Noise gates too, but only past the hard bound:
chips manufactured for the well-posed controls above `max_control_suggestion_rate` are `unfit`,
same as a critic that invents defects, while a lighter rate above `warn_control_suggestion_rate`
only warns.

**Enforcement is warn-only, and that is not a gap.** Refinement already degrades to silence on
every failure; its fitness must never gate serving runs, and blocking startup over a
chip-suggester's verdict would invert the feature's own doctrine. Under `audition.enforce` an
`unfit` refine verdict is a loud warning at service start and in `ra doctor` — never a refusal.
Auto-disabling refinement on an `unfit` verdict was rejected: config-behavior coupling where a
stale cache file can silently turn a feature off is the same shape as the inert
`audition.enabled` flag D-critic-audition deleted.

**The mirror-pair skeleton exists, and it gates nothing.** The corpus carries one ideologically
mirrored pair (`pair: qbq-01`) for `question_behind_the_question`, skipped under the default
transform set and runnable via `ra audition-refine --transforms`. The harness reports the
pair's fire-rate asymmetry as a diagnostic number — the measurement D-social-bias deferred and D-question-refinement made
the condition for enabling that transform — but the enablement decision itself stays a human
one; no threshold on the asymmetry is wired into any verdict.

Residuals: the corpus is small (ten fixtures) and public, so slot rotation is doing real work
against memorization; `require_terms`/group stems are casefolded string containment with a
whole-word rule for short terms, which is deliberately dumb and will need corpus care as it
grows; and the harness measures the refine *model*, not the client-side JS, whose gaps remain
listed in question-refinement.md.
