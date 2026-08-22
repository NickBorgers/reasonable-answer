## D-audition-rubric-identity — a cached verdict names the grading rules that produced it

`CacheEntry.matches` keyed a stored audition result to `(corpus_hash, prompt_hash, repetitions)`.
That triple is the right *philosophy* — D-critic-audition's cache exists so a verdict is never
carried across a change in what the measurement means, and D-control-soundness relies on exactly
that when it says a corpus edit "degrades to re-measure" — but the triple was incomplete. Two
verdict-affecting inputs sat outside it, and both are read by `ra doctor` and by the
`audition.enforce` startup gate for up to `max_age_days` (30 by default).

**`require_verbatim_spans`.** The CLI already passes `config.require_verbatim_spans` into
`run_assignment`, and `triage.validate_issue` fails a whole lens closed on a quote that is not
verbatim. Flipping the flag changes what a critic is able to score at all, so a score measured
under one regime is not evidence about the other — in either direction. It is now a stored field
on the entry and a term in `matches()`.

**The grading rules.** `_is_material`, `_locus_matches`, `LOCUS_PARAGRAPH_TOLERANCE`,
`SEVERITY_FLOOR`, `SEVERITY_RANK`, `LENS_CATEGORIES` and `run_assignment`'s counter accounting
together decide what a call is worth. None was hashed, so a deployed change to any of them — a
severity floor moved, a category re-scoped to another lens, the locus window widened — left every
stored verdict trusted for a month though it had been produced by rules that no longer exist. A
new `rubric_hash()` is now a fourth term in `matches()`.

**How `rubric_hash` is built, and why it is half automatic and half by hand.** The issue offered
a choice: a hand-bumped constant, or a hash derived from the taxonomy tables. Both were taken,
mixed into one digest — the shape `refine_prompt_hash` already uses, where a hashed prompt surface
is combined with a hand-bumped `PROMPT_VERSION`.

The rules that are *data* are hashed from the tables directly (`LENS_CATEGORIES`, `SEVERITY_FLOOR`,
`SEVERITY_RANK`, `LOCUS_PARAGRAPH_TOLERANCE`). Deriving that part of the identity from its source
data removes the maintenance risk that a table edit in `taxonomy.py`, away from the grader, lands
without the separate manual version bump. The `Metrics` field set is hashed for the same reason: a
new counter defaults to 0 on every older entry, and a `judge` gate reading it would score a stale
entry as a measured zero rather than as an absence.

The rules that are *code* — `grade`, `_is_material`, `_locus_matches`, `run_assignment`'s
accounting — carry `RUBRIC_VERSION`, a constant with a comment listing what requires a bump.
**Rejected: hashing `inspect.getsource` over those functions.** It is automatic and cannot be
forgotten, which is the whole argument for it. It was rejected because `audition.py` is
deliberately comment-dense — the reasoning is the documentation — and an audition costs
|models| x |fixtures| x repetitions calls against a paid, rate-limited proxy. Billing a full
re-measurement of the roster for a typo fix in a docstring conflicts with the operational goal of
invalidating only when measurement semantics change. It also breaks under a source-less install.
The chosen trade is a manually maintained constant for code rules, with automatic hashing where
the rubric is already represented as data.

**Not covered, deliberately: `judge`'s gate order and `AuditionThresholds`.** Neither is stale-able.
The cache stores `Metrics`, not a verdict; `judge(entry.metrics, cfg.thresholds)` runs at read time
against live thresholds, so a gate reorder or a retuned threshold already takes effect on the next
read without any invalidation. Hashing them would force a paid re-measurement to obtain a verdict
the current code would compute for free from the data already stored.

**Backward compatibility degrades to *not audited*, never to a pass.** `rubric_hash` and
`require_verbatim_spans` are required fields with no defaults, so an entry written before they
existed fails `CacheEntry.model_validate`, and `load_cache` already drops what fails to validate.
A pre-rubric `.ra-audition.json` therefore reads as an empty cache: every slot shows *not audited*
in `ra doctor`, and the `enforce` gate — which blocks only on a positive `unfit` — passes. That is
the same direction D-control-soundness established for a corpus edit, and it is safe to land in a
deployment running with enforcement on. A default value would have been the unsafe choice: it
would have asserted a rubric the entry never recorded.

`cached_judgements` and `enforce_fitness` take `require_verbatim_spans` as a required argument
rather than defaulting it, because the flag lives on `Config` while those functions take
`AuditionConfig`, and both callers hold a `Config`. A default would let a caller silently compare
against the wrong regime — the defect this decision closes, reintroduced one layer up. The gate
still takes no `LLMClient`, and `test_the_gate_takes_no_client_and_so_can_never_spend` still pins
that.

**Deferred: storing per-call graded results so a rubric bump can regrade for free.** The cache
holds aggregated `Metrics`, so any invalidation — corpus, prompt, or now rubric — forces a full
paid re-measurement even when the raw calls would answer the new rules perfectly well. Storing raw
`LensResult`s per fixture per repetition would make a rubric bump a free regrade, and would make
the `judge`-reads-a-new-counter case free too. It was deferred rather than rejected: it changes the
cache from a small verdict record into a corpus of stored model output (roughly two orders of
magnitude larger, and containing critic prose about fixture text), which raises retention and
schema-migration questions this issue should not settle in passing. Recorded as an open item below.
