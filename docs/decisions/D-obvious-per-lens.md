## D-obvious-per-lens — every lens carries an obvious-tier fixture and a locus-anchored one, and "never clean" fails closed

**The problem.** Found by adversarial review of the audition harness after D-control-soundness, and
confirmed independently by simulation. Both fail-closed sensitivity gates in `audition.judge` are
keyed to planted defects on `tier: obvious` fixtures:

- the hardcoded one — `if metrics.obvious_total > 0 and metrics.obvious_hits == 0: → UNFIT`, the
  llama-4-scout signature, and
- the `min_obvious_sensitivity` threshold, itself guarded by `if metrics.obvious_total`.

The `completeness` lens had exactly two planted fixtures, `omitted-counterargument-01` and
`unexamined-presupposition-01`, and both were `tier: moderate`. So `obvious_total` was **zero on
every completeness assignment** and both gates were structurally dead. A critic that returned zero
issues on every call it ever made — the precise failure D-critic-audition was built to catch — scored:
obvious gates skipped, controls clean (it invents nothing because it says nothing), `lens_sensitivity`
0% against a warn threshold, verdict **MARGINAL**. `enforce_fitness` blocks only on `unfit`, so even
a deployment running `audition.enforce: true` would have started with a silent critic staffing one of
its three lenses.

A second hole ran through the same two fixtures from the other direction. Both set `anywhere: true`,
which skips the locus window entirely, so the completeness lens measured only *did the critic name a
category from my lens*, never *did it find the defect*. A degenerate critic that reflexively raises
one material `omitted_counterargument` on every artifact therefore scored 100% `lens_sensitivity` on
completeness — and exactly `1.00` on `control_material_rate`, which is not *greater than* the `1.0`
default, so the noise gate passed it too. Perfect sensitivity, MARGINAL verdict, no block. The
completeness rubric could fail neither a never-fire critic nor an always-fire one.

Neither hole was visible to a test. `test_shipped_corpus_loads_and_covers_both_directions` asserted
only that each lens has *a* planted fixture.

**Decision, part one: an obvious-tier fixture per lens, and a locus-anchored defect per lens.**
`omitted-counterargument-02` is added to the corpus: `lens: completeness`, `tier: obvious`,
`anywhere: false`, anchored at `S1.P1`. Its report recommends raising a rural interstate speed limit
and never mentions crash risk, injury or fatality anywhere — not a section, not a sentence, not a
source. That is the whole of the defect, and it is what makes the tier honest: the fixture is not
merely thin. It carries a real `## The strongest counterargument` section that engages the
reference-point objection on the merits, it names its own weakest argument as weak, and it decomposes
the measurement it rests on, so a critic cannot score by flagging "needs more detail". It is written
in production report shape (D-report-template) from the start rather than in the pre-template shape
the older fixtures still use.

Two corpus tests pin both properties per lens — `test_every_lens_has_an_obvious_tier_fixture` and
`test_every_lens_has_a_locus_anchored_planted_defect` — so neither can regress the next time fixtures
are edited. They are corpus assertions, not grader assertions: they fail at the fixture, which is
where the defect was.

**Why this fixture is anchored when the other two are not.** `anywhere` exists because an omission
often has several honest loci and grading against one of them measures agreement with the fixture
author's filing choice. This omission has a dominant locus. `S1.P1` is the only paragraph that states
the recommendation, and the only one that asserts what the strongest objection *is* — the claim the
absent objection most directly bears on, which is exactly what `prompts._CATEGORY_ANCHOR` instructs an
`omitted_counterargument` critic to quote (D-absence-anchor). Every other section is descriptive.

**The residual risk, recorded rather than hidden.** A critic that files the omission against the
`## The strongest counterargument` section instead scores a miss, and with `obvious_total` of 1 on
this lens that alone reads `unfit` — the shape of failure D-control-soundness was: a lens made
structurally unpassable by its own corpus. Two things bound it. The shipped posture is
`audition.enforce: false`, so the consequence is a warning naming the count ("found 0 of 1 obvious
planted defects"), which is inspectable. And the pre-registered remedy is to add a *second* obvious
completeness fixture — an `anywhere: true` one, which the coverage tests permit once an anchored
fixture exists — rather than to relax this one, because `obvious_total` of 2 turns a locus
disagreement into 50% obvious sensitivity (marginal) while still grading silence 0% (unfit). That
remedy is not taken pre-emptively: it costs a fixture's worth of paid calls on every audition to
insure against a miss no measurement has yet observed.

**Decision, part two: "never clean" fails closed, and the control-rate boundary does not move.**
The always-fire strategy lands on exactly `1.00`, and the obvious repair is to change
`control_material_rate > max_control_material_rate` to `>=`. That is **rejected**, twice over.

It leaves the hole open to configuration. The doctrine established for the silence direction — and
pinned by `test_silent_critic_is_unfit_under_every_threshold_setting` — is that the strategy which
most completely defeats the harness must not be reachable by tuning. Under `>=` the always-fire critic
is blocked only while `max_control_material_rate` happens to sit at `1.0`; an operator who loosens it
to `1.5`, which looks like an ordinary calibration, re-opens it exactly. And it silently re-tightens a
calibrated threshold for every deployment, changing the configured number's meaning from "more than
one invented issue per sound report" to "one or more", in a region the D-control-soundness data does
not speak to — the rates measured there were 1.67 to 4.17, none near the boundary. A knife-edge fix
for one arithmetic coincidence is not a fix for the class.

What is added instead is a second **hardcoded** gate, the mirror of the silence gate: `control_runs`
non-zero and `control_clean_runs == 0` is `unfit`, whatever the thresholds say. It states the property
that actually matters, which is not *how noisy* but *did this critic ever once let a sound report
through in this audition*. The gate is a conservative fail-closed policy against the demonstrated
always-fire strategy: zero clean results on the configured controls is sufficient to reject that
audition, without claiming the observations predict how the critic would behave on another sound
report. Ordering: the new gate sits *after* the `max_control_material_rate` check, so the existing and
more informative "invents N material issues per sound report" reason still wins wherever it applies,
and the new one speaks only where the rate gate is silent. `control_clean_rate` as a *threshold* was
considered and rejected for the same reason `>=` was: a tunable knob at the one point where the
harness must be untunable.

This is deliberately not a judgement about noise in degrees. A critic clean on some sound reports and
not others is `warn_control_material_rate`'s business and still grades `marginal`. The gate fires only
on *never*, across a base of two control artifacts times the configured repetitions — six evaluations
at the default, and only two when `repetitions: 1`. Those repeated observations are not treated as
independent samples or generalized beyond the audition corpus. The thinness of that base is a real
limitation and is already an open item below (a third control fixture).

**Cache and blast radius.** Adding a fixture changes `corpus_hash`, so every cached verdict stops
matching in `cached_judgements` and reads *not audited* — never `unfit`. `enforce_fitness` blocks only
on a positive `unfit`, so this lands safely even with enforcement on: it degrades to "re-measure",
which is correct, because a verdict from the old corpus is a claim about a measurement that no longer
covered the completeness lens's obvious tier at all.

**Invariants.** None touched. The audition harness sits outside the run graph — it never reaches the
controller, `OrchestratorView`, author exclusion (it pins the model under test on purpose, and uses the
`AUDITION_AUTHOR` sentinel), or the severity floors, which the grader reads but does not change. The
grader stays a pure function with no LLM in it. The new fixture is untrusted data that enters a critic
context through the same fenced `critic_user` path a real report does, and the fail-closed lens
contract is unchanged.

**What this does not establish.** The new fixture has not been auditioned against real models — that
costs a paid proxy run — so the `obvious` tier claim rests on reading the artifact, as every tier claim
in this corpus does. Nor has the never-clean gate ever fired on live data: every `unfit` verdict
recorded so far came from the rate gate, and this one exists to catch a strategy that was demonstrated
by simulation rather than observed in a roster.
