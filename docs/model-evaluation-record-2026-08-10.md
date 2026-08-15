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
