# Evaluating a candidate model for a roster slot

`ra audition` is the only thing in this repository that decides whether a model may hold a
critic slot ([DESIGN.md](./DESIGN.md), [architecture.md](./architecture.md), D-critic-audition).
This page is the operator procedure for using it on a candidate that is not yet in
`config/roster.yaml` — what order to do things in, what each failure mode actually means, and
the mistakes that cost a wasted measurement round when the 2026-08-10/11 logic-lens audition
first made them. It is procedure, not specification: nothing here is a safety invariant CI
enforces, and none of it changes behavior. It exists so the next candidate evaluation does not
have to rediscover three infrastructure bugs by hand.

## Why measurement, not a claim

A vendor benchmark score or a leaderboard ranking says nothing about whether a model can hold
*this* roster's critic contract — reading a report under strict epistemic isolation, staying
quiet on a sound control, and returning the closed `Issue[]` schema every call. `ra audition`
measures exactly that, against fixtures salted with a known defect (or none) and rubric-scored
mechanically (QP8: verdicts come from deterministic aggregation of structured findings, never an
LLM grading prose). A model's general capability is not the scarce property here — every
candidate measured in the 2026-08-10/11 logic-lens audition had **perfect recall**, 1.00
sensitivity on the `obvious` tier. What is scarce is precision on hedged prose: whether the model
manufactures a material issue out of a clause whose resolving qualifier sits in the next clause
over. A survey of vendor claims cannot surface that; only running the model against the corpus
this roster actually uses can.

## The procedure, in order

Do these steps in this order. Each step exists because skipping it, or doing it out of order, is
what produced one of the three infrastructure bugs below.

1. **Serve the alias.** The candidate needs a LiteLLM alias on the deployment's proxy before
   anything else is possible — `ra audition` and `ra doctor` both resolve through
   `/model/info` ([deployment-profile.md](./deployment-profile.md)).
2. **Screen its upstream hosts on the real critique path.** Before spending an audition's call
   budget, run [`scripts/host_probe.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/scripts/host_probe.py)
   against the alias. This is not optional when the alias resolves through a router that can pick
   more than one upstream (OpenRouter and similar): the router re-rolls the host per request, so
   a host that mangles the closed schema shows up as a fraction of calls failing, indistinguishable
   at first glance from the model itself being unreliable. Screen with the **real** critique call
   (`critique.critique_once` against real audition fixtures) — not a toy schema, and not a
   tool-calling loop. A toy schema can pass on a channel that still breaks the audition's actual
   schema shape, and critics never hold tools, so a tool-calling probe tests an affordance the
   critique path never uses. See "the three infrastructure bugs" below for what each of those
   substitutions actually cost.
3. **Pin the host(s) that work**, in the deployment's LiteLLM config (`provider.order`), before
   measuring. An audition run against an unpinned multi-host router is not a measurement of the
   model; it is a measurement of whichever hosts the router happened to route to that run.
4. **Add the candidate to a scratch roster.** `ra audition` has no candidate flag — `--alias` only
   *filters* the slots built from `audition.assignments`, which reads the roster's critic pool for
   each lens. A candidate must be added to the target lens's critic pool in a **scratch copy** of
   the roster file, never the committed one, with `$RA_CONFIG` pointed at the copy
   (`config.py`'s `Config.load` honors `$RA_CONFIG` first, ahead of the checkout's default
   search path).
5. **Run the audition**, filtered to the one lens and one alias under test:
   `ra audition --lens <lens> --alias <candidate>`. Read `schema_failures` in the output before any
   other metric (below). If a verdict is reused from cache when you expected a fresh measurement,
   pass `--force`.
6. **Interpret the verdict** against the thresholds and the instability caveat below, and decide
   fit-first placement.

## Read `schema_failures` before any judgement metric

`Metrics.schema_failure_rate` counts *any* failed lens call identically — a 429, a timeout, a
mangled envelope, and a genuine schema violation all land in the same bucket. `judge()` checks it
**before** every judgement gate ([`audition.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/src/reasonable_answer/audition.py)):
above `max_schema_failure_rate` (0.2) the slot is `unfit` and no sensitivity or noise number is
even computed. A verdict of `unfit` driven by schema failures is not a statement about the
model's judgment — it may be a statement about the proxy, the router, or a schema shape the
serving stack cannot deliver. Confirm which one *before* writing the model off. This is not a
hypothetical: it is exactly what happened to `claude-sonnet-5` below.

## Interpreting a verdict

`ra audition` grades `fit` / `marginal` / `unfit` (or `insufficient`, if too little of the corpus
graded) from two families of gate, checked in a fixed order — mechanical gates (schema failures,
fixture coverage) before judgement gates (sensitivity, invented-issue rate). Two gates are
threshold-proof by construction and worth naming because no amount of tuning admits past them: a
model that finds **zero** obvious planted defects is `unfit` regardless of everything else, and a
model that **never once** returns a clean review of a sound control is `unfit` regardless of
everything else. Everything between those two floors is a judgement call encoded in
`AuditionThresholds` — for the logic lens, the fail-closed ceiling on invented material issues per
sound control is `max_control_material_rate: 1.00`.

### Verdict instability near the threshold

A single-run verdict landing close to a threshold is not settled. The
[2026-08-10/11 operator record](./model-evaluation-record-2026-08-10.md) reports that
`claude-haiku-4-5` measured
**2.04** invented material issues per sound control on one run and **1.04** on a re-run of the
identical corpus, with low schema-failure rates both times — roughly a 2x run-to-run swing
against a `1.00` ceiling, at the default `audition.repetitions: 3` (24 control runs total for one
slot). Both runs graded `unfit`, and that directional conclusion is not in question — the gap to
`mistral-large-3`'s measured `0.08` is an order of magnitude, not a coin flip. But a verdict
resting on a single value between roughly **0.8 and 1.3** should not be read as a stable number
until it has been re-measured with a larger `repetitions`, or reported with a confidence interval.
Treat that band as "measure again before deciding," not as "this candidate is marginal."

### The control corpus is sound, not miscalibrated

When several candidates cluster just over the line, the instinct is to suspect the fixtures
rather than the models. Check that instinct against the evidence before acting on it, using
[`scripts/dump_control_issues.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/scripts/dump_control_issues.py),
which prints every material issue a critic filed against a sound control — `claim_span`,
`related_span`, and rationale — the same spot-check protocol D-minimax-retirement and
D-completeness-pool-noise used before retiring a critic. The
[operator record](./model-evaluation-record-2026-08-10.md#manual-control-issue-review) records the
method and outcome of reading 22 such issues from the 2026-08-10/11 audition: they are near-misses
in a specific, reproducible shape, where the critic flags a hedge whose resolving qualifier sits
in the adjacent clause, frequently quoted in the
critic's *own* `related_span`. Exactly one of the 22 was arguably a fair complaint. The decisive
argument here is structural, not a matter of reading each issue and forming an opinion:
`mistral-large-3` scores `0.08` on the **identical** corpus under the **same** rubric, so the
corpus cannot contain real defects that a competent critic would find. A cluster of marginal
scores is evidence about the candidates, not about the fixtures.

### Fit-first ordering

When a candidate clears the bar, where it lands in the pool is not arbitrary. Following the
`D-completeness-pool-noise`/`D-minimax-retirement` precedent, order a lens's pool **fit-first**:
the measured-`fit` model goes to position 1, `marginal` models fill in behind it. Ordering is
load-bearing because `review.depth` reads the pool front-to-back and whichever slot a pass reaches
first is the one whose *silence* the run acts on — a position-1 miss is the failure a converging
run cannot recover from as cheaply as a position-2 miss. When no candidate in a lens measures
`fit`, the higher-sensitivity `marginal` model takes position 1, for the same reason (see the
evidence-lens ordering in `config/roster.yaml`, both survivors marginal).

### Never relax a threshold to admit a candidate

`AGENTS.md` forbids weakening a test to make a change pass, and the same logic applies without
qualification to `AuditionThresholds`. A threshold exists to keep the harness usable — a critic
above `max_control_material_rate` manufactures work every round and a run staffed by it stagnates
instead of converging (D-completeness-pool-noise, D-minimax-retirement). Loosening the ceiling to
fit a candidate that measures just over it does not make the candidate more capable; it removes
the evidence that it isn't. If a candidate looks close, re-measure it (see the instability
caveat above) — do not move the goalposts.

## Measured logic-lens results (2026-08-10/11)

Seven candidates across five labs and both weight classes were auditioned against the logic lens
under the shipped fixture corpus and the shipped `max_control_material_rate: 1.00` ceiling. Rate
is mean invented material issues per sound control; lower is better. Corpus identity, call counts,
and the recorded metrics are in the
[2026-08-10/11 operator record](./model-evaluation-record-2026-08-10.md).

| model | rate | verdict |
|---|---|---|
| `mistral-large-3` | 0.08 | fit |
| `glm-5.2` | 0.75 | marginal |
| `minimax-m3` | 1.12 | unfit |
| `qwen3.5-397b-a17b` | 1.21 | unfit |
| `gpt-5.4-mini` | 1.22 | unfit |
| `claude-haiku-4-5` | 2.04, then 1.04 on re-run | unfit |
| `claude-sonnet-5` | void — 50% schema failures caused by a proxy bug | not measurable at the time |

Only `mistral-large-3` cleared the bar, and by a wide margin — the next-best candidate,
`glm-5.2`, invents nearly ten times as many material issues per control. Every failing candidate
with gradable judgement metrics had perfect recall, so the audition is not measuring whether
these models can find planted defects; it is measuring whether they can stay quiet on prose that
has none.

## The three infrastructure bugs

Each of these first presented as an adverse model-quality verdict. None of them was.

### 1. A multi-host router silently changed which upstream served the request

The [host-probe record](./model-evaluation-record-2026-08-10.md#upstream-host-probes) reports that
`nemotron-3-ultra` appeared to fail nearly every writer call. The alias routes through
OpenRouter, which re-rolls the upstream host per request rather than pinning one. Screened with
[`scripts/host_probe.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/scripts/host_probe.py)
against each candidate host individually: pinned to Venice, 4/4 clean; pinned to Together, 4/4
unparsed tool-call markup (the `_unparsed_tool_call` failure mode
[architecture.md](./architecture.md) and [deployment-profile.md](./deployment-profile.md)
describe — a proxy that does not parse a model's native tool-call syntax hands the raw markup
back as message content). Fixed by pinning `provider.order` in the deployment's LiteLLM config,
not in application code — `LLMClient` has no notion of upstream host, by design.

### 2. A forced-tool-call fallback delivered the right payload under the wrong key

The [schema-failure incident record](./model-evaluation-record-2026-08-10.md#schema-failure-incidents)
reports that `claude-sonnet-5` graded `unfit` on a 50% schema-failure rate. The root cause was in
the proxy:
LiteLLM synthesized a forced tool call for Anthropic and unwrapped it buggily, delivering the
correct structured payload nested under a junk envelope key (`$PARAMETER_NAME`, `json_value`,
`parameters`, `$FUNCTION_NAME` were all observed) that strict validation rejected wholesale. The
verdict this produced was **void**, not adverse — the audition never exercised the model's
judgment on a single fixture, it exercised the proxy's envelope-unwrapping bug repeatedly. This
is exactly the case "read `schema_failures` first" above exists for: a 50% failure rate that looks
like a capability finding was actually a serving-path finding, and treating it as the former would
have retired a candidate that was never actually measured.

### 3. A dereferenced-vs-`$ref` schema shape tripped the same fallback for a narrower reason

Even after the deployment-side proxy behavior was addressed, any schema containing `$defs`/`$ref`
still downgraded to the same broken forced-tool-call path, because Anthropic's native structured
output rejects `$ref` outright and pydantic emits a `$ref` for every nested model and every enum —
which is most of this codebase's schemas. This is an application-side shape, not a serving
misconfiguration, and it is why bug 2's deployment-side fix alone did not fully resolve the
symptom for this model. It was fixed by dereferencing every schema before it is sent, so no
request this application makes carries a `$ref` regardless of what the proxy does with one
([PR #172](https://github.com/NickBorgers/reasonable-answer/pull/172)) — check
[deployment-profile.md](./deployment-profile.md) for whether that fix, and the matching proxy-side
capability flag, are actually live on the deployment you are measuring against before trusting a
`claude-sonnet-5` verdict.

**The general lesson these three share:** an adverse audition verdict and a broken serving path
produce the identical symptom — a bad number — from the operator's chair. Rule out the serving
path on the real critique call *before* believing an adverse verdict describes the model. The
abstract version of that advice is not persuasive on its own; these three incidents are the
concrete cost of skipping it.

## Procedural gotchas

- **`ra audition` has no candidate flag.** `--alias` only *filters* slots already built from the
  rostered pools (`audition.assignments`); a candidate must first be added to a pool via a
  scratch roster (see the procedure above).
- **`--alias` is not repeatable.** It is a single-valued CLI option; passing it twice silently
  measures only the last one, with no error. This cost a wasted audition round in the
  2026-08-10/11 evaluation.
- **Exit code 1 from `ra audition` means a slot graded `unfit`.** That is a result the command is
  supposed to produce, not a crash — do not treat it as tooling failure.
- **A cached verdict is reused** when corpus hash, prompt hash, rubric hash,
  `require_verbatim_spans`, `repetitions`, and `structured_output_mode` all match the entry
  already on disk. Pass `--force` to discard the cache and re-measure.
- **Screen hosts on the real critique path, not a toy schema or a tool-calling loop.** A toy
  schema can pass on a channel that still breaks the audition's actual schema shape. A
  tool-calling loop tests an affordance critics never use — critics never hold tools. This
  evaluation wrongly excluded a good host (Chutes) from a tool-loop survey, then re-measured it
  properly on the critique path, where it was 0/6 failures
  ([operator record](./model-evaluation-record-2026-08-10.md#upstream-host-probes)).
- **Do not run auditions concurrently, or alongside other proxy load.** A 429 raised inside a
  critique call becomes `LensResult(failed=True)`, indistinguishable from any other schema
  failure, and counts against the model exactly as a real failure would.
- **Never relax a threshold to admit a candidate.** See "never relax a threshold" above.

## The two scripts

Both are operator tools, run by hand against a live paid proxy — same posture as `ra audition`
itself. Neither is invoked by the application, by CI, or by any test; both need `RA_CONFIG`
pointed at a roster that resolves the alias under test, and both spend real proxy calls.

- [`scripts/host_probe.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/scripts/host_probe.py)
  answers "which upstream host(s) does this alias's router send requests to, and does each one
  survive the real critique schema?" Run it before pinning `provider.order` for any alias that
  resolves through a multi-host router.
- [`scripts/dump_control_issues.py`](https://github.com/NickBorgers/reasonable-answer/blob/main/scripts/dump_control_issues.py)
  answers "what did a critic actually say when it flagged a sound control?" Run it whenever a
  candidate's `control_material_rate` looks marginal and you need to decide whether the noise is
  the model's fault or the corpus's, before spending a `docs/decisions.md` entry on either
  conclusion.
