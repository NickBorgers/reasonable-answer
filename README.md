# reasonable-answer

[![PR Validation](https://github.com/NickBorgers/reasonable-answer/actions/workflows/pr-validation.yml/badge.svg)](https://github.com/NickBorgers/reasonable-answer/actions/workflows/pr-validation.yml)
[![Docker Release](https://github.com/NickBorgers/reasonable-answer/actions/workflows/docker-release.yml/badge.svg)](https://github.com/NickBorgers/reasonable-answer/actions/workflows/docker-release.yml)

Takes a question (and optionally a seed report) and produces a higher-quality report whose
argument is *sound* — where "sound" means **no eligible reviewer can find a material defect**,
not that anyone asserted it was good.

New here? Start with [docs/concepts.md](./docs/concepts.md) — the approachable tour of *why* the
system is shaped this way. The design specs are in [docs/](./docs/), hub at
[docs/DESIGN.md](./docs/DESIGN.md). This README is about running it.

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
make test      # full offline suite — no network, no API keys
make doctor    # resolve every roster alias against the LiteLLM proxy, report health
make audition  # measure whether each rostered critic can actually perform its lens
make serve     # web interface on http://127.0.0.1:8080
make run Q="Does a four-day work week increase productivity?"
```

With a seed artifact to improve. The seed does not have to be markdown — a PDF,
a Word document, a web page, `.txt` or `.html` is converted at ingest:

```bash
make run Q="Is this analysis sound?" SEED=draft.md
make run Q="Is this analysis sound?" SEED=q3-report.pdf
make run Q="Is this analysis sound?" SEED=https://example.org/whitepaper
```

Conversion is best-effort and aims at one thing: recovering the `#` headings that
critics cite loci against, and the `## Sources` list the evidence lens verifies. Where a
format carries no heading structure — a bare `.txt`, most PDFs — the seed is accepted
with a warning rather than rejected, and critics fall back to paragraph-level loci.
PDF support needs the optional extra: `uv sync --extra ingest`.

Or directly:

```bash
uv run ra run -q "your question" --seed draft.md --config config/roster.yaml -v
uv run ra run -q "your question" --seed https://example.org/report.pdf
uv run ra doctor
uv run ra audition
uv run ra purge <run_id> [--content-only]
uv run ra expired
```

## Web interface

`make serve`, or `ra serve --host 0.0.0.0 --port 8080` in a container. Submit a question, watch
the loop converge live, and browse every past run.

The run page streams the pipeline's own event log over server-sent events, so you see each round
as it happens — which model wrote the draft, which critic drew which lens, what each one found,
and which controller rule fired:

```
round 2   writer deepseek-v4-flash
  logic         glm-5.2          2 issues
  evidence      glm-5.2          clean
  completeness  mistral-large-3  clean
  1 major  ->  rule 14  generate  material issues remain
```

**There is no authentication.** The intended posture is tailnet-only, with Tailscale ACLs as the
access control; `ra serve` defaults to binding `127.0.0.1` for that reason. Anyone who can reach
the interface can spend tokens and read every stored run, including seed material. Do not put it
on a public interface without adding real auth in front of it.

Showing reports and critiques to a *human* does not weaken the isolation design — blindness is
about what enters a *model's* context. The UI is a window onto the audit trail, which is the
reason the pipeline keeps one.

## Docker

```bash
docker compose up -d
```

~236 MB, `python:3.12-slim`, runs as uid 10001. Three things it needs:

| | why |
|---|---|
| the host can reach the LiteLLM proxy | assumed, not configured here — on this network that means the host is on the tailnet |
| a volume at `/data/runs` | holds the audit trail *and* the SQLite checkpoints; resumability dies without it |
| `roster.yaml` at `/etc/ra/roster.yaml` | change models without rebuilding |

Use a **named volume** if you can. A bind-mounted host directory arrives owned by root while the
container runs unprivileged; the app detects this at startup and tells you what to chown rather
than failing on your first submission.

No database, no broker, no GPU, no model weights — all inference goes through the proxy.

## Configuration

Everything lives in [config/roster.yaml](./config/roster.yaml). The roster is **role-structured**:

```yaml
roster:
  writers: [mistral-large-3, deepseek-v4-flash]   # models that author reports
  orchestrator: gemma4-small                      # blind referee (optional; default writers[0])
  critics:
    logic:        [glm-5.2, minimax-m3, mistral-large-3]
    evidence:     [glm-5.2, minimax-m3, gemma4]
    completeness: [mistral-large-3, glm-5.2, gemma4]
```

Every entry is **open-weight** and small enough to load on the target local box (see
[docs/DESIGN.md](./docs/DESIGN.md) for the footprint table). `glm-5.2` is deliberately
*critic-only*: as a writer it would be barred from reviewing its own drafts, which would cost the
roster its best reviewer on half of all rounds.

The `orchestrator` decides only whether a cosmetic polish pass is worth running. It sees bounded
counts and returns one boolean, so it runs on the cheapest local model in the roster; if it fails,
the run simply skips polish.

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

That count is over distinct *identities*, not families — two checkpoints of the same base model
satisfy it while decorrelating very little. `make doctor` warns separately when a lens pool
collapses to a single family.

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

**Retrieval (optional, off by default).** Set `search.enabled: true` in the roster and writers get a
`web_search` tool backed by the Brave Search API, so the URLs in `## Sources` are ones a search
actually returned rather than ones the model remembered. Credential: `$BRAVE_SEARCH_API_KEY`, or a
gitignored `brave.token` for local work. Startup fails closed if the key is missing *or* if any
writer cannot emit tool calls — that writer would still be told to produce a `## Sources` section
and would fill it from memory, and no downstream check can tell a remembered citation from a
retrieved one. Each run carries a query budget (default 60) because the free tier is 2,000
queries/month; when it runs out the writer is told so explicitly rather than being handed silence.

**Source verification (optional, off by default).** Set `search.verify_sources: true` and the pages
the report cites are fetched and handed to the **evidence lens only**, as untrusted data. That turns
`fabricated_citation` and `misrepresented_source` from judgements about plausibility into checks
against the page. A failed fetch is explicitly *not* treated as evidence of fabrication — sites
block automated clients, paywall, and go offline. This fetches URLs a model chose, which is SSRF
exposure by construction; it is expected to be constrained at the network layer, not here.

**Known limitations.** Output is labelled *consensus-reviewed with in-artifact sourcing* by default,
*…with retrieved sourcing* when `search.enabled: true`, and *…with verified sourcing* when
`verify_sources` is also on. **None of the three is fact-checked.** Verification establishes that a
cited page exists and says something compatible with the claim — not that the page is correct, and
not that the roster chose good sources. With verification off, whether a source supports the claim
attached to it is unverified entirely. (See D5/D17/D18 in [decisions.md](docs/decisions.md) and the
evidence section of [convergence.md](docs/convergence.md).)

**Writer disputes (optional, off by default).** Set `disputes.enabled: true` and a writer that
believes a fix-task is factually wrong can dispute it with evidence instead of falsifying the
report to satisfy it. A citation dispute whose quote checks out against the cited page (with
`verify_sources` on) is upheld mechanically; anything else goes to a fresh-context arbiter model
that is neither the writer nor the critic that raised the finding, and that defaults to the
finding when uncertain. Upheld disputes suppress the re-raised finding for the rest of the run
(auditable in `events.jsonl`); everything else leaves the finding standing. See D25 in
[decisions.md](docs/decisions.md).

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
disputes/             every writer dispute, with its grounds (when enabled)
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
  dispute.py     writer disputes: mechanical adjudication, arbiter eligibility
  controller.py  the 14-rule ordered stop decision — pure, deterministic, total
  graph.py       the LangGraph loop
  store.py       audit trail and retention
```

The test suite is offline: a scriptable fake proxy drives the whole graph, so the loop's safety
properties (author exclusion, fail-closed lenses, termination, orchestrator blindness) are tested
without a network. `tests/test_controller.py` sweeps the controller's input space for totality and
for the property that no rule generates at or beyond the hard cap.

Lint with `uv run ruff check src/ tests/`.

## CI and agentic review

Every PR gets a secret-free validation run (ruff, the offline suite on 3.11 and 3.12, a lockfile
check, `actionlint`, and a container build with a health-check smoke test), plus an agent review by
three roles: **invariant**, **security**, and **test**. A deterministic judge aggregates their
structured verdicts and writes the merge gate; it runs from `main` with read-only permissions, so a
PR cannot modify the code that judges it. Nothing in the pipeline can push.

File an issue and an agent opens a PR for it; `/review` forces a fresh review
cycle on a PR.

The `invariant` reviewer is the one that earns its keep here: `docs/` is normative spec, so it
checks that a change preserves author exclusion, orchestrator blindness, fail-closed lenses,
severity floors, and termination — and blocks a change that alters one of those behaviors without
updating the spec and recording the decision.

- [docs/ci-pipeline.md](./docs/ci-pipeline.md) — what runs, and which properties are load-bearing
- [docs/ci-setup.md](./docs/ci-setup.md) — runner registration, secrets, branch protection
