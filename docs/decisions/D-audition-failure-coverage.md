## D-audition-failure-coverage — a verdict covers every fixture it owed, or it is `unfit`

**The problem.** `audition.run_assignment` deliberately separates "cannot emit the schema" from
"looked and saw nothing": a failed lens increments `schema_failures` and skips grading, so it is
neither a miss nor a false positive. That separation is right — the two have different fixes, one
a prompt or output-mode problem and the other a reason to replace the model — and
`test_failed_lens_counts_as_schema_failure_not_as_silence` pins it. The accounting that followed
from it leaked.

Failed calls also vanished from every *denominator*. `planted_total`, `obvious_total` and
`control_runs` grew only on successful calls, so a fixture a model reliably broke on was deleted
from that model's own exam:

- An evidence assignment is 5 fixtures x 3 repetitions = 15 calls. A model that fails all three
  repetitions of one planted fixture — deterministically, because that artifact's content drives
  it out of schema — sits at exactly 3/15 = 20%, which the strict `schema_failure_rate >
  max_schema_failure_rate` gate admits.
- That fixture then contributes nothing to `planted_total` or `obvious_total`. Catching the rest
  and staying clean on the controls yields **`fit`**, confirmed by direct simulation.
- The noise direction censors more quietly still. `_ratio` returns 0.0 for a zero denominator,
  meaning "not measured", and `judge` gates the noise checks on `control_runs` — so a model that
  breaks on controls specifically switched those gates *off* rather than failing them, and its
  `control_material_rate` read clean.

The result was a verdict whose headline rates were computed over a corpus subset the model had
selected by failing, which is the exact inverse of the harness's fail-closed posture.

**Decision — coverage is tracked per fixture and gates before any rate.** `Metrics` gains
`fixtures_owed` (the size of `for_lens`, controls included) and `uncovered_fixtures` (the ids that
never produced one gradable review across every repetition). `judge` returns `unfit` when
`uncovered_fixtures` is non-empty, naming them. Coverage is checked *after* the schema gate and
*before* everything else, because coverage is what the remaining rates are over.

Per fixture, not per call, is the load-bearing part. "20% of calls failed" cannot distinguish a
model that stumbles once on each of five fixtures — a flake, correctly tolerated by the schema
threshold — from one that is deterministically broken on a single fixture and therefore never
measured on it at all. Only the second censors a denominator, and only a per-fixture count sees it.

**`unfit`, not `insufficient`.** Both were arguable and the choice follows the reasoning the schema
gate already uses one block earlier: a model asked `repetitions` times that returned nothing
gradable every time has produced a definite, reproducible failure, not an absence of evidence.
`insufficient` means "we did not ask enough" — `calls == 0`, or a corpus with nothing to grade —
and reporting a reproducible break as a gap in our own measurement would put the deficiency on the
harness. It also matters at the gate: `audition.enforce` blocks only on `unfit`, and a model that
cannot review one of the artifacts it will be handed in production is exactly what that gate is
for.

**The rule is uniform across planted fixtures and controls, and is not tunable.** The narrower
form considered was "any planted fixture, or *all* controls", on the theory that controls pool
into one rate and losing one of two only shrinks the sample. It was rejected: in both directions
the fact measured is the same one — this model reliably cannot produce a gradable review of this
artifact — and in production that means the report gets no review from that critic on that lens.
A uniform rule is also the one an operator can state without a footnote. There is no threshold,
for the same reason the zero-obvious rule has none (D-critic-audition): a rate no call contributed to
is not a lenient measurement, it is the absence of one.

**`schema_failure_rate` stays the separately reported cause, and stays `>`.** Failures are not
folded into misses; the schema gate still fires first, so a wholly broken model is still reported
as a mechanical problem rather than sent to an operator as "never graded 6 fixtures". The
20%-exactly boundary was reconsidered and deliberately left alone. `>` is what a field named
`max_…` should mean, and it is what `max_control_material_rate` already means, while
`min_obvious_sensitivity` admits its named bound from the other side — changing one of the three
would make the set read inconsistently. More to the point, the boundary was only load-bearing
because of the censoring: with coverage gated, a model sitting at exactly 20% because of three
scattered flakes is a model that was measured on everything, which is what the threshold is
calibrating for.

**`fixtures_owed` is a required field, so old cache entries are dropped.** A record that cannot
say what it owed cannot say whether it measured all of it. Defaulting it to 0 would have made the
coverage gate vacuous for every verdict written before this change, i.e. fail-open for the
`max_age_days` window on precisely the entries the finding is about. Required means
`Metrics.model_validate` rejects them and `load_cache` — which already treats any unreadable entry
as absent — degrades them to *not audited*. Same blast radius as D-control-soundness: the
`enforce` gate blocks only on a positive `unfit`, so this is safe to land with enforcement on, and
the correct reading of a pre-coverage verdict is "re-measure".

**Reporting.** `ra audition` gains a `cover` column (`covered/owed`, red when short) beside the
rates it qualifies, and `--json` carries both fields through the existing `model_dump`. The
`OrchestratorView` is untouched — audition metrics never enter the controller's context.

**What this does not do.** It does not change the fixtures, the thresholds, or the grader.
`refine_audition` has its own `Metrics` with the same shape of denominator and was left alone as
out of scope; whether it censors the same way is an open item below.
