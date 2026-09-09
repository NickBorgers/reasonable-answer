# glm-5.2 → glm-5.3 version-currency audit — 2026-09-08/09

This is the public source record for the `glm-5.3` version-bump comments in `config/roster.yaml`.
It records the operator's measurements without reproducing private model prompts or responses. It
is an observation record, not a shipped roster decision: the roster is unchanged by it. `glm-5.2`
is auditioned on all three lenses it holds a critic slot on (`logic`, `evidence`, `completeness`);
this record asks whether the newer `glm-5.3` snapshot could replace it, not whether a new family
is needed — that is the separate, still-open question `docs/decisions/D-logic-third-family.md`
and the open item in `docs/decisions.md` track.

## Measurement identity

- **Dates:** 2026-09-08 and 2026-09-09.
- **Lenses:** `logic`, `evidence`, `completeness` — every lens `glm-5.2` holds a slot on.
- **Corpus:** `tests/fixtures/audition` (now `src/reasonable_answer/fixtures/audition`,
  D-packaged-audition-corpus), corpus hash `9c248e1d249ad301` — the same corpus and hash as the
  2026-08-10/11 and 2026-09-06/07 records, so these numbers are directly comparable to those.
- **Repetitions:** shipped `audition.repetitions: 3`.
- **Thresholds:** shipped `max_schema_failure_rate: 0.2`, `max_control_material_rate: 1.00`,
  `warn_lens_sensitivity: 0.6`.
- **Source mode:** `sources=None`, the `AUDITION_SOURCE_MODE` `run_assignment` uses.

## Recorded slot results

| lens | control material rate | lens sensitivity | schema failure rate | verdict |
|---|---:|---:|---:|---|
| `logic` | not interpreted | not interpreted | 0.81 (34/42) | **unfit — void** |
| `evidence` | not interpreted | not interpreted | 0.94 (34/36) | **void — confounded, see below** |
| `completeness` | 0.46 (11/24) | 0.50 (6/12) | 0.00 (0/36) | marginal |

`completeness` is the one clean, interpretable result: 0 schema failures across 36 calls, 0.46
material issues invented per sound control — an improvement over `glm-5.2`'s own recorded 0.72 on
this lens — but lens sensitivity of 0.50 sits below the 0.6 warn line, and 3 of 6 `obvious`-tier
fixtures were missed. Net: precision improved, recall did not; `judge()` graded it `marginal`, the
same tier `glm-5.2` already holds on this lens.

## The logic-lens result, in detail

34 of 42 calls (81%) failed before a judgement metric could even be computed
(model-evaluation.md's "read `schema_failures` before any judgement metric" — `judge()`'s gate
order means the schema-failure floor is checked first and unconditionally). This was not a
handful of slow outliers: of the 42 recorded latencies, 26 clustered at 903-930 seconds — the
full 300-second `budgets.timeout_seconds` exhausted three times over (three call attempts per
critique) — against a handful of calls that returned in under 10 seconds. 10 of the 14 owed
fixtures produced zero gradable review across all three repetitions, `uncovered_fixtures`
(D-audition-failure-coverage) — including **all 8 control fixtures**, so the precision half of
the measurement (`control_material_rate`) was never computed at all; only 8 planted-defect
instances across the 4 fixtures that did produce a gradable review were available to grade, of
which 6 were caught strictly and all 3 `obvious`-tier instances were caught.

**This was screened, not just measured once and written off.** Following the procedure in
`docs/model-evaluation.md` (`scripts/host_probe.py`), a follow-up probe pinned `glm-5.3` to a
single upstream host — Cloudflare, 99.96% 1-day uptime and `structured_outputs: true` on
OpenRouter's own endpoint listing for this model, one of the stronger candidates among the 28
hosts OpenRouter lists for it — and ran the real critique call (`critique.critique_once` against
real audition fixtures, not a toy schema) twice. Both calls failed the same way: exhausted call
retries on repeated timeouts. A model failing identically on a single pinned, high-uptime host is
the signature the `nemotron-3-ultra` incident (`docs/model-evaluation.md`) says to rule out before
trusting an unpinned multi-host router's adverse result — here it rules the opposite way: this
looks like a genuine latency characteristic of `glm-5.3` on this call shape, not a bad-routing
artifact fixable by pinning `provider.order`.

## The evidence-lens attempt — confounded, not a result

The first `evidence` measurement (94% schema-failure rate, 34/36 calls) ran while the deployment's
OpenRouter account had exhausted its credit balance (`total_credits: 50` against
`total_usage: 50.07` on the account backing this measurement's proxy). Every failure in that batch
carried an OpenRouter `402 Payment Required` body ("This request requires more credits, or fewer
max_tokens"), not a timeout or a schema violation — a serving-account problem, not a model or
routing one, in the same spirit as the `claude-sonnet-5` schema-failure incident recorded in the
2026-08-10/11 record (there: a proxy envelope bug; here: an exhausted balance). The account was
topped up afterward and `logic` was cleanly re-measured (the result above), but `evidence` was not
re-run before this record was written — deliberately: given `logic`'s clean, host-pinned-confirmed
`unfit` result, a third multi-hour, multi-dollar measurement was not judged worth its cost to
settle a question `logic` alone already answers for the roster decision (below). `evidence` for
`glm-5.3` therefore remains genuinely unmeasured, not `unfit` and not `fit`.

## What this does and does not establish

`glm-5.3` is not a safe drop-in replacement for `glm-5.2` on `logic`, measured against this
corpus, at the shipped 300-second call timeout, as of these dates. That is a narrower claim than
"`glm-5.3` is a worse model" — it may simply need a materially longer `budgets.timeout_seconds`
than this roster ships, which was not tested (raising the timeout for one alias only was
considered and deliberately not attempted here, to avoid spending another multi-hour measurement
window chasing a parameter this record does not claim would fix it). It is not a `completeness`-
or `evidence`-lens verdict for `glm-5.3`: `completeness` has its own clean, separately-graded
result above, and `evidence` was never measured. It is not a claim about any other model's
latency on this call shape, and it is not a re-measurement of `glm-5.2` itself, whose existing
verdicts on all three lenses (2026-08-10/11, `docs/model-evaluation-record-2026-08-10.md`) are
unchanged and uncited here beyond the `completeness` comparison above.
