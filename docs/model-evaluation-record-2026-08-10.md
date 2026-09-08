# Logic-lens audition operator record — 2026-08-10/11

This is the public source record for the empirical claims in
[model-evaluation.md](./model-evaluation.md). It records the operator's measurements and manual
inspection without reproducing private model prompts or responses. It is an observation record,
not a shipped roster decision and not a change to an audition threshold or default.

## Measurement identity

- **Dates:** 2026-08-10 and 2026-08-11.
- **Lens:** `logic`.
- **Corpus:** `tests/fixtures/audition`, loaded by `audition.load_fixtures`; corpus hash
  `9c248e1d249ad301`.
- **Fixture obligation:** 14 fixtures per slot: 6 planted logic fixtures and 8 sound controls.
- **Repetitions:** the shipped `audition.repetitions: 3`, except the `gpt-5.6-luna` slot, which was
  run at `repetitions: 6` — double the default, chosen deliberately to stabilise that estimate.
- **Calls:** 42 attempted critique calls per complete slot run, including 24 control calls; 84 and
  48 respectively for the doubled-repetitions `gpt-5.6-luna` run.
- **Threshold:** shipped `max_control_material_rate: 1.00`; rate is the mean number of material
  issues returned per successful sound-control review.
- **Source mode:** `sources=None`, the `AUDITION_SOURCE_MODE` used by `run_assignment`.

The corpus hash is the cache identity computed over every audition fixture, not a hand-written
label. The fixture and call counts follow directly from `FixtureSet.for_lens(Lens.LOGIC)` and the
configured repetitions.

## Recorded slot results

Each row is one complete 42-call slot run unless noted. The haiku row records two complete runs
against the same corpus. The `gpt-5.6-luna` row is one complete 84-call run at `repetitions: 6`.
`obvious sensitivity` is the recall gate for `tier: obvious` planted
defects. The sonnet run produced too many schema failures for judgement metrics to be meaningful.

The `vendor` column records the organisation that published each model's weights or serves it as
a hosted API. It is stated here so that any claim elsewhere about how many distinct vendors the
sample covers can be checked against a mapping rather than inferred from the alias strings.

| model | vendor | weights | control material rate | obvious sensitivity | schema failure rate | verdict |
|---|---|---|---:|---:|---:|---|
| `mistral-large-3` | Mistral AI | open | 0.08 | 1.00 | below gate | fit |
| `glm-5.2` | Z.ai (Zhipu) | open | 0.75 | 1.00 | below gate | marginal |
| `minimax-m3` | MiniMax | open | 1.12 | 1.00 | below gate | unfit |
| `qwen3.5-397b-a17b` | Alibaba | open | 1.21 | 1.00 | below gate | unfit |
| `gpt-5.4-mini` | OpenAI | closed | 1.22 | 1.00 | below gate | unfit |
| `gpt-5.6-luna` | OpenAI | closed | 1.88 | 1.00 | below gate | unfit |
| `claude-haiku-4-5`, run 1 | Anthropic | closed | 2.04 | 1.00 | below gate | unfit |
| `claude-haiku-4-5`, run 2 | Anthropic | closed | 1.04 | 1.00 | below gate | unfit |
| `claude-sonnet-5` | Anthropic | closed | not interpreted | not interpreted | 0.50 | void; not measurable |

Distinct vendors with an interpretable verdict: Mistral AI, Z.ai, MiniMax, Alibaba, OpenAI,
Anthropic — six. Of the seven distinct candidates with an interpretable verdict, four are
open-weight and three are closed. The table has eight interpretable runs because
`claude-haiku-4-5` was run twice.

The `gpt-5.6-luna` row carries its denominators here, because it is the only slot measured at
non-default repetitions: 84 attempted calls with 1 schema failure, well below the 0.2 gate, so the
channel was clean; 48 successful sound-control runs carrying 90 material issues, giving the 1.88
rate; and 33 of 35 successful planted-fixture reviews finding the plant, including 6 of 6 at the
`obvious` tier. It rests on more calls than any other slot here — 48 sound-control runs against the
24 a default run collects — but that is a larger sample, not a demonstrated stable one: no sampling
analysis or confidence interval was computed for any slot in this set. The instability caveat below
is therefore **not** established as inapplicable to it. The arithmetic matters, so it is written
down: the repeat-run swing observed on `claude-haiku-4-5` was 2.04 → 1.04, and a swing of that
proportion applied to 1.88 would cross the 1.00 ceiling. The `unfit` verdict is what was measured;
whether it would survive a re-measurement is untested, and it was not re-measured.

The two haiku runs are the basis for the open methodology item in
[decisions.md](./decisions.md): at 24 control calls per run, the point estimate moved from 2.04 to
1.04 without a corpus or repetitions change. Both values remained above the shipped ceiling.

## Manual control-issue review

The operator used `scripts/dump_control_issues.py` to print the `claim_span`, `related_span`, and
rationale for 22 material issues returned on sound controls, then read those fields against the
fixture text. The recorded classification was:

- 21 near-misses where the flagged hedge was resolved by an adjacent qualifier, commonly already
  present in the critic's own `related_span`;
- 1 issue that was arguably a fair complaint.

This manual review was diagnostic only. It did not alter the deterministic audition metrics or
the verdicts in the table.

## Upstream host probes

These probes used the real critique schema rather than a toy schema or a tool-calling loop.

| alias or model | pinned host | calls | recorded outcome |
|---|---|---:|---|
| `nemotron-3-ultra` | Venice | 4 | 4 clean |
| `nemotron-3-ultra` | Together | 4 | 4 `_unparsed_tool_call` failures |
| critique-path recheck | Chutes | 6 | 0 failures |

The nemotron comparison is why the procedure requires screening every upstream host before
trusting an adverse result from a multi-host router. The Chutes recheck records the correction of
an earlier tool-loop survey that did not exercise the critic path.

## Schema-failure incidents

The `claude-sonnet-5` slot run recorded a 0.50 schema-failure rate. Inspection attributed those
failures to two serving-path problems rather than to the model's logic judgement:

1. the proxy's forced-tool-call fallback returned the structured payload under inconsistent
   envelope keys, which strict validation correctly rejected; and
2. schemas containing `$defs`/`$ref` were routed through that fallback because the native path
   rejected the reference-bearing shape.

The application-side schema-shape correction is recorded in
[PR #172](https://github.com/NickBorgers/reasonable-answer/pull/172). Deployment state remains a
separate prerequisite, so the recorded sonnet verdict is void rather than a model-quality result.

## Follow-up measurement — 2026-09-06/07 (D-logic-third-family)

This is an extension of the record above, not a restart of the survey — same corpus, same lens,
per the instruction in [model-evaluation.md](./model-evaluation.md). Where the 2026-08-10/11
survey looked for a genuinely new fourth family and found none `fit`, this round instead
auditioned candidates already present or already excluded elsewhere in the roster's own
reasoning: `gemma4` (this roster's evidence/completeness critic), and two logic-lens candidates
the original open item had already named as "still true" — `qwen3.8-27b` (successor to the
closed-weight `qwen3.7-max` the open item excluded) and `nemotron-3-super-120b-a12b` (the item's
own nominated cheap tool-competent candidate, not previously audited on this lens).

### Measurement identity

Identical to the identity stated above, restated because it is what makes this an extension and
not a new survey: corpus hash `9c248e1d249ad301`, lens `logic`, `repetitions: 3`, 42 attempted
calls per complete slot (24 control calls), `sources=None`, threshold
`max_control_material_rate: 1.00`, `max_schema_failure_rate: 0.2`.

### Recorded slot results

| model | vendor | weights | control material rate | obvious sensitivity | schema failure rate | verdict |
|---|---|---|---:|---:|---:|---|
| `gemma4` | Google | open | 0.00 (0/24) | 1.00 (3/3) | 0.00 (0/42) | fit |
| `qwen3.8-27b` | Alibaba | open | 1.62 (39/24) | 1.00 (3/3) | 0.00 (0/42) | unfit |
| `nemotron-3-super-120b-a12b` | NVIDIA | open | not interpreted | not interpreted | 0.36 (15/42) | void; not measurable |

All three vendors are open-weight. `gemma4` and `qwen3.8-27b` both cleared the schema-failure gate
cleanly (below 0.2) and both found every `obvious`-tier planted defect (3/3) — consistent with the
2026-08-10/11 finding that recall on this tier is not the scarce property. They diverge entirely
on precision: `gemma4` returned zero material issues across 24 sound-control reviews, while
`qwen3.8-27b` invented 39 across the same 24, a 1.62 rate against the 1.00 ceiling — closer to the
noise profile of the 2026-08-10/11 survey's `unfit` candidates than to `mistral-large-3`'s 0.08.

`nemotron-3-super-120b-a12b` failed the schema gate before any judgement metric was computed: 15
of 42 calls (0.36) did not return a gradable review, and one owed fixture
(`conceptual-conflation-01`) produced no gradable review across any of its 3 repetitions
(`uncovered_fixtures`), which independently forces `unfit` regardless of the rate
(D-audition-failure-coverage). Per-call latencies for this slot ran unusually high, several
clustering near the 900s `budgets.timeout_seconds` ceiling — consistent with upstream congestion
on this alias rather than a judgement failure, and consistent with the same alias's writer sibling
(`nemotron-3-ultra`) recording `unreachable`/rate-limited failures via the deployment's `ra doctor`
around the same dates. This verdict is a statement about serving reliability on the dates
measured, not about judgment quality — the same distinction the sonnet schema-failure incident
above draws, though the fix in that case was an application bug and no comparable root cause was
investigated here.

### What this does not establish

Three candidates is not the eight of the original survey, and this round did not re-attempt a
new-vendor search — it deliberately measured reuse and near-miss candidates instead, for the
reasons D-logic-third-family states. It is not evidence that a fourth, genuinely new family
remains unreachable; it answers a narrower question (does closing the immediate `roster_limited`
gap require finding one) in the negative for now. It is also not an evidence-lens or
completeness-lens measurement for any of the three models: `gemma4`'s `fit` verdict here is
logic-lens-specific, exactly as the original record's closing caveat states for its own
candidates.
