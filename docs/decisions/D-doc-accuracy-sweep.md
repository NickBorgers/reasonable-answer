## D-doc-accuracy-sweep — a batch of verified documentation-accuracy corrections

**The finding.** A sweep of `docs/*.md`, `README.md`, `AGENTS.md`, `mkdocs.yml`, and one
`config/roster.yaml` comment turned up 21 places where the prose no longer matched the code it
describes — most from drift after later PRs (D-front-loaded-depth, D-repair-turn-context,
D-decision-slugs, D-completeness-pool-noise, and the schema/nesting shape `ControllerInput` had
already taken), a few pre-existing. Each item below was independently re-checked against the cited
source before being corrected; none of them is a behavior change — the code was already right, only
the description of it was not.

| # | Location | Was | Now |
|---|---|---|---|
| 1 | `docs/run-provenance.md` | image builds record `dirty: null` | image builds record `dirty: false`; `null` is only a `git` build whose `git status` failed |
| 2 | `docs/convergence.md` `ControllerInput` block | listed `roster_identities`/`clean_records` fields; flattened `polish_used`/`polish_cap` | matches `schemas.py`: nested `view: OrchestratorView`, pre-derived `lens_status: [LensStatus]`, documented `fatal_reason` |
| 3 | `docs/convergence.md` rule-1 row | attributed a lens-with-no-eligible-critic and repeated-malformed reviews to `fatal` | only writer-pool-empty and all-writer-attempts-failed are `fatal`; the other two surface as failed `LensResult`s through rules 2→3 |
| 4 | `docs/convergence.md` rule-8 row | "re-critique a toppable lens" (singular) | re-critiques *every* toppable lens per confirmation attempt |
| 5 | `docs/convergence.md` stagnation definition + rule-2 cell | "K consecutive ticks"; "partial counts never used" | counts every completed triage pass, including a rule-2 re-critique of the same draft; partial passes do update `prev_material`/`prev_signature`/`stagnation_count`/scoreboard, they just never reach a stop decision because rule 2 precedes rules 4–14 |
| 6 | `docs/convergence.md` severity-floor note | called `SEVERITY_FLOOR` "config-tunable" | it is a hardcoded constant (`taxonomy.py`) with no config surface |
| 7 | `docs/convergence.md` view-block comment | "`lens_cleared`: distinct non-author models" | distinct non-author model *families* (matches the prose a few paragraphs later) |
| 8 | `docs/convergence.md` clean-record reset | "any new generation IS a new `artifact_hash`" | the reset is unconditional on every generation, including byte-identical regeneration (RC-002) |
| 9 | `docs/isolation.md` arbiter (`Ano`) row | "never sees the lens" stated without qualification | added the caveat that `defect.category` is in the prompt and, for every category but the shared `stylistic` one, names exactly one lens |
| 10 | `README.md` source tree | omitted `build.py`, `reading.py`, `refine_audition.py`, `shutdown.py`, `support.py`, `textconv.py`, `resolve/` | added, each with a one-line role |
| 11 | `README.md` output tree | omitted `support/` and `refinements/`; said only `reports/`/`critiques/` are sensitive | added both dirs; corrected to all five `store.CONTENT_DIRS` entries |
| 12 | `README.md` web-interface section | "anyone signed in can open a run they hold the id for" | reading a run needs no sign-in at all; the run id alone is the credential (matches the auth section a few paragraphs below) |
| 13 | `docs/architecture.md` lens diagram | omitted `loaded_language`, `one_sided_sourcing`, `unexamined_presupposition`; didn't note `stylistic` spans all three lenses | added the missing categories and the `stylistic` note |
| 14 | `docs/index.md` | omitted `quality-principles.md`, `deployment-profile.md`, `run-provenance.md`, `model-evaluation.md`, `model-evaluation-record-2026-08-10.md` | added, in the page's existing style, matching `mkdocs.yml`'s nav grouping |
| 15 | `mkdocs.yml` comments (×2) | called `decisions.md` "990 lines" | ~6,000 lines |
| 16 | `docs/DESIGN.md` document map | "how each of the 20 findings was resolved" (round-1-only count) | reworded without a brittle count; names the `RA-`/`RB-`/`RC-`/`RG-` prefixes and "every round since" |
| 17 | `AGENTS.md` | claimed `test_reviewer_prompt_ranges.py` is the repo-wide slug guarantee; called the resolve-issue invariant list "the six invariants CI checks" | credited `test_citation_resolution.py` as the repo-wide guarantee (the other test covers 3 prompts); clarified the six are a subset of the eleven `invariant.md` audits (plus a twelfth drift row) |
| 18 | `docs/quality-principles.md` | called the evidence-base marker line "machine-checked" | it is checked by the `quality` CI reviewer (an LLM following `prompts/quality.md`), not a deterministic script |
| 19 | `docs/question-refinement.md` implementation map, item 8 | described bumping a numeric valid-ID allowlist in `invariant.md` as current practice | reworded as history from before D-decision-slugs, with a pointer to the current slug-membership mechanism |
| 20 | `config/roster.yaml` audition NOTE | said `gemma4` on evidence is "at position 3 and still unmeasured" | the pool is two entries, `gemma4` is at position 2 and measured (`marginal`); neither evidence critic is a writer, so author exclusion never shrinks this pool and strong `accepted` remains reachable |
| 21 | `docs/ssrf-egress-isolation.md` + `docs/deployment-profile.md` | the egress contract's "must reach the LLM proxy" and "must not reach... the tailnet" read as flatly contradictory once the proxy sits at a `.ts.net` address | stated the LLM proxy as the one deliberate, pinned exception to the tailnet-wide deny (a specific host allow, not a range carve-out), and mirrored a one-clause pointer to that exception in `deployment-profile.md`'s proxy sentence |

**Why this is a decision entry and not a silent fix.** AGENTS.md requires a `docs/decisions.md`
entry whenever a `docs/*.md` file changes, so that CI's docs-drift check (invariant row 12) has
something to diff against — even when, as here, every change moves prose toward the code rather
than away from it.

**What this deliberately does not do.** It does not touch any of: `docs/convergence.md`'s
earliest-vs-latest tie-break wording, `docs/isolation.md`'s fence-mitigation/repair-turn/dispute
passages, `docs/ci-pipeline.md`'s CI-mechanics lines, `docs/deployment-profile.md`'s redirect-hardening
sentence, `docs/quality-principles.md`'s principle-row table, or anything under `.github/` — other
PRs were in flight against those exact passages at the time of this sweep. It also does not verify
or change the production Squid configuration for item 21's pinned-host allow; the doc now states the
shape the exception must take, not that it has been implemented that way on the running proxy.

**Invariants.** None of the six is in reach. Nothing here touches a model call, prompt, critic
assignment, severity, or controller rule; every change is a correction of a description to match
behavior that was already there.
