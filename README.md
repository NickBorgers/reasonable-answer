# reasonable-answer

Takes a question (and optionally a seed report) and produces a higher-quality report whose
argument is *sound* — where "sound" means **no eligible reviewer can find a material defect**,
not that anyone asserted it was good.

The design is in [docs/](./docs/); start with [docs/DESIGN.md](./docs/DESIGN.md). This README is
about running it.

## How it works, in one paragraph

Models take turns **writing** and **critiquing** a report, and a report is never critiqued — on any
dimension — by the model that wrote it. Three per-lens critics (logic / evidence / completeness)
each run in a fresh, authorship-blind context and emit issues against a closed schema. A mechanical
triage step clamps severities to category floors, turns the issues into depersonalized fix-tasks for
the next writer, and projects a content-free count summary for a **blind referee**. The referee — a
deterministic controller, assisted by an LLM whose only authority is a cosmetic-polish judgment —
decides continue / finalize / abort, and it never sees the report.

## Quick start

The repo is a devcontainer: clone, open, run the tests.

```bash
make test      # full offline suite — no network, no API keys, ~1s
make doctor    # resolve every roster alias against the LiteLLM proxy, report health
make run Q="Does a four-day work week increase productivity?"
```

With a seed artifact to improve:

```bash
make run Q="Is this analysis sound?" SEED=draft.md
```

Or directly:

```bash
uv run ra run -q "your question" --seed draft.md --config config/roster.yaml -v
uv run ra doctor
uv run ra purge <run_id> [--content-only]
uv run ra expired
```

## Configuration

Everything lives in [config/roster.yaml](./config/roster.yaml). The roster is **role-structured**:

```yaml
roster:
  writers: [claude-haiku-4-5, gpt-5.4-mini]   # models that author reports
  critics:
    logic:        [qwen3.7-max, gpt-5.4-mini, claude-haiku-4-5]
    evidence:     [llama-4-scout, ...]        # critic-only specialist: huge context
    completeness: [gemma4, ...]               # third family, decorrelated blind spots
```

Models are addressed as **LiteLLM proxy aliases**; the proxy is one OpenAI-compatible endpoint for
cloud and local models alike. At startup each alias is resolved to its underlying
`provider/model`, and **distinctness is enforced at that level** — two aliases pointing at one
model do not count as two independent reviewers.

Every lens wants **≥2 eligible non-author models**. `make doctor` tells you whether you have them:

| roster shape | strongest possible outcome |
|---|---|
| ≥2 eligible non-author models on every lens | `accepted` |
| some lens has only one | `converged_unconfirmed`, naming the under-reviewed dimension |
| some lens has none | fails closed at startup |

Point `proxy.base_url` at any OpenAI-compatible endpoint. If yours needs a key, set
`LITELLM_API_KEY` (or change `api_key_env`).

## What the terminal statuses mean

| status | meaning | exit code |
|---|---|---|
| `accepted` | every lens cleared by ≥2 distinct non-author models on the identical final artifact | 0 |
| `converged_unconfirmed` | every lens cleared, but ≥1 lens had only one eligible reviewer | 0 |
| `exhausted_unresolved` | cap/stagnation reached with only non-blocking issues, or clean-but-unconfirmed | 1 |
| `needs_human_review` | cap/stagnation/cycle reached with **blocking** issues outstanding | 1 |
| `aborted` | fatal: model unavailable, or a review could not be completed at all | 1 |

A known-unacceptable artifact is never labelled `accepted` or `converged_unconfirmed` — that is a
tested property, not a convention.

**What to expect in practice.** With a strict roster, `accepted` is uncommon: a second reviewer on
a lens usually finds something the first did not, and each rewrite gives the next round new text to
object to. Runs that reach the cap ship the *best-scoring* draft, not the last one, with the
outstanding defects listed in `final.json`. Raise `hard_cap`, or narrow the question, if you want
more convergence pressure.

**Known limitations (v1).** There is no external retrieval. The evidence lens challenges uncited
claims, on-its-face misrepresentation, and implausible citations *within* the artifact. Output is
labelled *consensus-reviewed with in-artifact sourcing* — not fact-checked.

A critic's quote fields (`claim_span`, `related_span`) are verified to be verbatim text from
the artifact, so a critic cannot smuggle invented text to the next writer that way. Its
`rationale` and `instruction` are still critic-authored prose; they are length-bounded, carry no
provenance, and reach the writer inside an explicit untrusted-data fence, but they are not
mechanically derived. Replacing them with generated text from the structured fields would close
that channel completely at some cost in fix quality.

## Output

Each run writes `runs/<run_id>/` (mode 0700):

```
final.md              the report that shipped
final.json            terminal status, clean records, outstanding defects, warnings
events.jsonl          every stage: startup, intake, generate, critique, triage, control
reports/              every draft, with its author
critiques/            every lens result, with provenance
signals/views.jsonl   what the blind orchestrator saw, per round
signals/decisions.jsonl  which rule fired, per round
```

`reports/` and `critiques/` hold the sensitive material; `ra purge <id> --content-only` drops them
and keeps the decision record.

## Speed is an anti-goal

The intended deployment is a slow local model. A run is many sequential model calls by design —
the three lenses parallelise, nothing else does. Resumability and the audit trail matter more than
latency.

## Development

```
src/reasonable_answer/
  taxonomy.py    categories, lenses, mechanical severity floors
  schemas.py     every boundary type; OrchestratorView is the isolation-critical one
  config.py      roster + budgets + fail-closed startup validation
  roles.py       who writes, who critiques, and the author-exclusion invariant
  llm.py         LiteLLM proxy client, identity resolution, structured-output ladder
  prompts.py     all prompts; untrusted data is fenced, roles never leak
  report.py      structural loci and artifact hashing
  triage.py      mechanical: floors, counts, defect list, clean records
  controller.py  the 14-rule ordered stop decision — pure, deterministic, total
  graph.py       the LangGraph loop
  store.py       audit trail and retention
```

The test suite is offline: a scriptable fake proxy drives the whole graph, so the loop's safety
properties (author exclusion, fail-closed lenses, termination, orchestrator blindness) are tested
without a network. `tests/test_controller.py` sweeps the controller's input space for totality and
for the property that no rule generates at or beyond the hard cap.
