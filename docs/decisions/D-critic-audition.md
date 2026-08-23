## D-critic-audition — critic eligibility becomes structural *and* demonstrated

Observed in `run-d5934276fafd`. Two critics returned zero issues on every call they made
across the whole run: `llama-4-scout` on 6 evidence calls, `gemma-4-31b-it` on 6
completeness calls — including on artifacts that `claude-haiku-4-5` and `gpt-5.4-mini`
subsequently found 6 and 10 material issues in. Both held first position on their lens,
so they were the default critic on every first-pass review. `validate_roster_health`
reported the roster healthy throughout, correctly: every structural property held.

This is a gap in the design's central claim, not an operational accident. "No eligible
reviewer can find a material defect" defined *eligible* purely structurally — non-author,
distinct resolved identity, distinct family. A model meeting all three and reporting
nothing satisfies the predicate while performing no review, and the run's counters,
statuses and label are identical to a genuinely clean one. Nothing downstream can
distinguish them, because the only evidence of a review is the absence of issues.

**Decision.** Eligibility gains a capability term, measured rather than asserted:
`src/reasonable_answer/audition.py` runs each rostered critic against fixtures with known
planted defects plus sound controls, and grades `fit` / `marginal` / `unfit` per
(resolved identity, lens).

Three sub-decisions worth recording, each with a rejected alternative.

**The grader is mechanical, never an LLM.** Category match plus a structural-locus window,
and nothing else. An LLM grader is precisely the component whose reliability is in
question here; using one would make the harness's trustworthiness depend on the property
the harness exists to measure. This is the same reason the controller is a pure function.

**Both directions gate.** Sensitivity alone is the wrong target: a critic that flags every
paragraph scores perfectly and is worse than useless, because it manufactures work each
round, drains the critique budget, drives `stagnation_count` to the limit, and terminates
the run `exhausted_unresolved` (rule 13) on a report that was fine. Control fixtures with
no planted defect measure that direction, and a high `control_material_rate` is `unfit`.

**Warn by default, enforce opt-in.** Fail-closed is the project's posture and the argument
for it is real — the soundness claim is void without capable reviewers. It was rejected as
a *default* because it couples every run to a cache whose freshness depends on a paid,
rate-limited proxy, and an operator blocked by an expired audition will disable the
harness outright, which is strictly worse than a loud warning. `audition.enforce: true`
turns an `unfit` assigned critic into a startup `ConfigError`.

**The gate blocks only on `unfit`, and only ever reads the cache.** `audition.enforce_fitness`
runs in `graph.build_runtime` beside `validate_roster_health`, before the structured-output
probes, so a roster with a measured-incapable critic never reaches the point of spending
tokens. `marginal`, `stale` and `not audited` stay warnings even with enforcement on: they
are absences of evidence, not evidence of incapacity, and blocking on them is precisely the
coupling to cache freshness the paragraph above rejects. A verdict about a model no longer
rostered is ignored, so swapping the unfit model out takes effect immediately. The gate
takes no `LLMClient` by signature — a test pins that — because it runs on every `ra run` and
every web boot, where growing a call would bill an audition per run and break a keyless boot.

**`audition.enabled` is deleted, not implemented.** It shipped `false`, was read by nothing,
and there was nothing for it to gate: `ra audition` is the only thing that measures, and
`ra doctor` and the gate above only read the cache it leaves behind. Auditioning is opt-in by
being its own command. A knob that cannot change behaviour is worse than no knob, because it
reads as a safety control — the same reason a blank audition cell in `ra doctor` is forbidden.
`AuditionConfig` is `extra="forbid"`, so a config still carrying `enabled:` now fails to load
rather than silently ignoring it — a loud break on a line that never did anything, which is the
right trade for a two-file repo but is the one thing to know before pulling this into a
deployment with a hand-edited roster. `config/roster.yaml` is updated;
`config/roster.default.yaml` carries no `audition` block and takes the code defaults.

One case is deliberately not tunable: a model scoring **zero** on `tier: obvious` fixtures
grades `unfit` under every threshold configuration. That is the observed signature above,
and a threshold that could permit it would defeat the purpose.

The harness is also position-aware, which matters for the current roster. `pick_critic`
prefers an identity that has not yet reviewed the artifact, so a model at pool index ≥2 is
unreachable on the first pass and is reached on the **rule 8 confirmation top-up**. A
silent critic there does not merely fail to catch things — it raises `cleared_count` to 2,
satisfies `strong_met`, and terminates the run `accepted`. #10 kept `gemma-4-31b-it` as
`gemma4` at exactly that position on two lenses.

### Deferred

- A held-out private fixture corpus. The shipped corpus is public and will reach training
  data, inflating sensitivity for reasons unrelated to capability. Mitigated for now by
  seeded slot substitution, which rotates surface forms while leaving each planted
  defect's structure intact; that raises the cost of memorization without removing it.
- Auditioning **writers** (citation validity, fix-task instruction-following) and the
  **orchestrator** (whose only authority is a cap-gated cosmetic polish, so a wrong answer
  costs one round). Different metrics, separate work.
- Corpus coverage. The initial corpus covers 5 of the 8 non-stylistic categories with one
  fixture each plus 2 controls. `omitted_counterargument` exposed a real limitation:
  omissions have no honest locus, handled by a per-defect `anywhere` flag rather than by
  pretending a filing choice is ground truth.
