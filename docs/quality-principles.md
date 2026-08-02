# Quality principles — the evidence register (D-quality-reviewer)

> **Status:** normative. This document is the register the `quality` CI reviewer audits
> pull requests against. The `invariant` reviewer asks whether code and spec moved
> *together*; the `quality` reviewer asks whether the position the spec now takes is still
> the position the published evidence supports. A change to a principle here without the
> procedure in [§4](#4-retiring-or-weakening-a-principle) is a graded violation. See D-quality-reviewer in
> [decisions.md](./decisions.md).

Evidence base last verified: 2026-07-28

That marker line is machine-checked: the `quality` reviewer compares it to the current UTC
date on every review and, past twelve months, files the refresh issue described in
[§3](#3-refreshing-the-evidence-base). Its format is exactly
`Evidence base last verified: YYYY-MM-DD` — change the wording and the check goes blind.

## 1. Why this register exists

This system's shape is not taste. Author exclusion, fresh-context critique, cross-family
rosters, mechanical severity floors, a deterministic controller, capped loops, and
fetched-text verification each track a specific empirical result (the [References
table](#5-references) below). The `invariant` reviewer cannot defend that grounding: a PR
that coherently updates code, spec, *and* `decisions.md` passes its row 12 by
construction — even if the new position stands on ground the literature has refuted. This
register gives that drift a reviewable surface, and the [refresh
procedure](#3-refreshing-the-evidence-base) gives the literature itself one.

## 2. The principles

A principle row is in play when a diff touches its surface. `QP<n>` ids are citable in
review findings the way `D<n>` ids are.

| # | Principle | Surface | Evidence |
|---|-----------|---------|----------|
| QP1 | **No LLM ordinal score enters a control decision.** Continue/stop/accept/merge decisions are deterministic functions of bounded categorical facts; severities are mechanical floors, clamped up only. | `controller.py`, `triage.py`, `taxonomy.py`, `schemas.py::OrchestratorView`; CI: `aggregate.mjs`, the judge stage | Grove et al. 2000 |
| QP2 | **Second witnesses are cross-family.** Agreement adds information only to the extent errors are decorrelated; a same-family model added as an extra witness adds correlated errors, not independence — and even cross-family agreement is partial evidence, never proof. | `config/roster.yaml`; `roles.py::critic_slate` selects at most one model per family and `roles.py::lens_statuses` counts clean families; `config.py::validate_roster_health` warns when a pool cannot supply two families | Kim et al. 2025; Li et al. 2025; Verga et al. 2024 |
| QP3 | **The CI panel itself stays family-diverse, and names what it runs.** Reviewer roles split across at least two agent families, and a role sits cross-family from the role it primarily deconflicts with. Every role pins its model: `agent:` fixes only the family, so an unpinned role follows a vendor default and the panel's composition stops being a reviewable property of this repository. | `agent:` and `model:` inputs in `review-pipeline.yml`, the `agent_model()` maps in `resolve-issue.yml` and `review-fixer.yml`; enforced by `tests/test_ci_model_pins.py` | Kim et al. 2025; Verga et al. 2024 |
| QP4 | **Author exclusion is evidence, not preference.** Models reliably fail to correct their own output and measurably favor it; the same claim relabeled as external input is corrected at far higher rates. A decision record cannot repeal a measurement. | `roles.py`, `config.py`; spec: [isolation.md](./isolation.md) | Huang et al. 2024; Panickssery et al. 2024; Chen, Su & Chiang 2026 |
| QP5 | **No critique or report prose crosses contexts as instruction.** Review happens in fresh, blind contexts; critic→writer traffic is bounded structural data; the blind orchestrator sees integers and enums. CI reviewers never read each other's findings. | `prompts.py`, `triage.py`, `schemas.py` (patch-scoped revision under `revision.mode`/D-scoped-revision narrows the *edit*, not the traffic — critic→writer input stays bounded structural data); CI: independent reviewer jobs | Song 2026; Sharma et al. 2023 |
| QP6 | **Refinement, not debate.** Models never argue in a shared transcript; the cycle is generate → independent critique → mechanical triage → regenerate. Visible peer judgments collapse independence. | `graph.py`, `prompts.py`; CI: panel-then-deterministic-judge topology | Smit et al. 2024; Lorenz et al. 2011 |
| QP7 | **Every loop is capped, and the cap is honored at every entry point.** More iterations do not monotonically improve output; refinement loops adapt to their evaluator. | `controller.py`, `config.py::Budgets` (the rule-13 rewrite valve is a new loop, bounded by `Budgets.rewrite_cap` and unreachable at/beyond the hard cap — D-scoped-revision); CI: `MAX_CYCLES`, the dispute budget, agent timeouts, and the inherited-verdict classifier that preserves a cycle only for a verified whole-range base resync (D-inherit-whole-range) | Huang et al. 2024; RG-001/RH-001 lineage in [decisions.md](./decisions.md) |
| QP8 | **Verdicts come from deterministic aggregation of structured findings, never an LLM grading prose.** Expert reviewer agreement is low even among humans; the remedy is structure and mechanical aggregation, not a smarter holistic judge. | `controller.py`, the blind orchestrator; `audition.py` (`judge` computes `fit`/`unfit` from aggregated `Metrics` against `AuditionThresholds`; `CacheEntry.matches`/`rubric_hash` decide which stored `Metrics` a verdict may be read from, and `cached_judgements`/`enforce_fitness` are the startup gate that trusts it — D-audition-rubric-identity); CI: the judge and the deterministic whole-range/tree-identity gate controlling whether its prior verdict may be inherited, both run from `main` (D-inherit-whole-range) | Grove et al. 2000; Beygelzimer et al. 2021; Verga et al. 2024 |
| QP9 | **Empirical claims in `docs/` carry citations that support them as stated.** A claim strengthened beyond its source, or left standing after its citation is removed, is drift. | every `docs/*.md`; the [References table](#5-references) | the register itself |
| QP10 | **Verification means fetched text, never parametric memory.** A model's recollection of a source is testimony from the component whose reliability is in question. | `dispute.py` mechanical adjudication, `search.verify_sources` and the opt-in `sources.*` reader tiers that widen what counts as fetched text (`config.SourcesConfig`, `fetch.SourceFetcher`, `graph._pdf_reading_enabled`, and the paid `resolve.extraction` renderer tier — `ResolutionTier.EXTRACTION` — whose markdown is the cited URL's own body and so carries no `body_source_url`); the `quality` prompt's own fetch rule | D-writer-disputes, D-retrieval-opt-in/D-source-verification, D-paid-tier-page in [decisions.md](./decisions.md) |
| QP11 | **Evidence-base freshness is checked mechanically and is never blocking.** See the marker line above and [§3](#3-refreshing-the-evidence-base). | this file | — |
| QP12 | **Principles-as-spec drift is blocking, in both directions.** Behavior governed by QP1–QP10 changing without this file and `decisions.md` moving too — or a principle here weakening with no new fetchable evidence in the diff — is the `quality` reviewer's row-12 analogue. See [§4](#4-retiring-or-weakening-a-principle). | this file + every surface above | — |

**Application — answer obligations (D-answer-obligations).** `incomplete_answer` is structured
completeness output with a mechanical `major` floor under QP1. Its writer and critic prompt
obligations change what each isolated role must assess, not what crosses contexts, so QP5's traffic
boundary is unchanged. The audition measures the added category through its existing structured
metrics, deterministic verdict, and rubric-identity invalidation under QP8; no LLM grades prose into
a control decision.

## 3. Refreshing the evidence base

The literature moves; this register must not silently rot. There is deliberately **no
scheduled job**: the `quality` reviewer is the scheduler. When the marker line above is
older than twelve months, every quality-selected PR receives one `non_blocking_notes[]`
entry and one `followup_issues[]` entry titled exactly:

> **Refresh the quality-principles evidence base**

with a body directing the assignee here. Staleness is the repository's debt, not any PR's
defect — the nag never blocks, and if nobody refreshes, enforcement simply continues
against the last-verified base with the nag persisting. Degradation is honest, not silent.

The refresh itself is a normal PR (a human, or the issue-resolution agent picking up the
filed issue) that:

1. fetches every URL in the [References table](#5-references) and confirms the paper still
   says what its row claims — a fetch that fails is a fetch that failed, not a retraction;
2. searches for *superseding* work on each row's specific claim — this is the one place
   broader literature search belongs, inside a reviewed PR rather than inside a per-PR
   review;
3. updates rows, citations, and any principle whose strength the new evidence changes
   (via [§4](#4-retiring-or-weakening-a-principle) if weakening);
4. bumps the marker date.

That PR goes through the full review panel — including the `quality` reviewer, which
fetch-verifies the changed claims like any others (QP9/QP10). A refresh agent's parametric
memory never becomes authority: only what survives fetch-verification lands.

## 4. Retiring or weakening a principle

No row here is immortal — the register loses its meaning the moment it cannot lose an
argument to better evidence. A PR may weaken or retire a principle by:

1. updating the row in this file and recording the change in `decisions.md`, **and**
2. citing new evidence **fetchable from a URL in the diff**, whose fetched text actually
   supports the change.

"The field has moved on," uncited, is the `qual-uncited-retreat` blocker. The `quality`
reviewer fetches the offered evidence and reads it before letting the retreat pass —
exactly the standard the pipeline holds report citations to.

## 5. References

Every URL below was fetched and checked against its row's claim on the date in the marker
line. Rows cite this table; the table is the boundary of what a `quality` finding may rest
on without fetching something new.

| Reference | Finding relied on | Backs |
|---|---|---|
| [Huang et al. 2024, "Large Language Models Cannot Self-Correct Reasoning Yet" (ICLR)](https://arxiv.org/abs/2310.01798) | Intrinsic self-correction without external feedback degrades reasoning performance. | QP4, QP7 |
| [Chen, Su & Chiang 2026, "The Self-Correction Illusion"](https://arxiv.org/abs/2606.05976) | Relabeling a model's own reasoning as external input raises explicit correction rates by 23–93 percentage points across seven model families. | QP4 |
| [Panickssery, Bowman & Feng 2024, "LLM Evaluators Recognize and Favor Their Own Generations" (NeurIPS)](https://arxiv.org/abs/2404.13076) | Self-recognition capability linearly predicts self-preference in LLM judges; the bias is causal. | QP4 |
| [Sharma et al. 2023, "Towards Understanding Sycophancy in Language Models" (ICLR 2024)](https://arxiv.org/abs/2310.13548) | Sycophancy is consistent across RLHF assistants and is triggered by stances visible in context. | QP5 |
| [Song 2026, "Cross-Context Review"](https://arxiv.org/abs/2603.12123) | Reviewing in a separate session with no access to the production conversation beats same-session self-review; repetition within a session does not. | QP5 |
| [Smit et al. 2024, "Should we be going MAD?" (ICML)](https://arxiv.org/abs/2311.17371) | Multi-agent debate does not reliably outperform simpler ensembling and is highly hyperparameter-sensitive. | QP6 |
| [Lorenz et al. 2011, "How social influence can undermine the wisdom of crowd effect" (PNAS)](https://doi.org/10.1073/pnas.1008636108) | Even mild social influence collapses judgment diversity while increasing confidence. | QP6 |
| [Kim et al. 2025, "Correlated Errors in Large Language Models" (ICML)](https://arxiv.org/abs/2506.07962) | When two capable models both err they frequently agree on the same wrong answer, even across providers; correlation grows with capability. | QP2, QP3 |
| [Li et al. 2025, "Preference Leakage: A Contamination Problem in LLM-as-a-judge"](https://arxiv.org/abs/2502.01534) | Judges favor related models — same family, same base, or models distilled from the judge. | QP2 |
| [Verga et al. 2024, "Replacing Judges with Juries" (PoLL)](https://arxiv.org/abs/2404.18796) | A panel of judges drawn from disjoint families outperforms a single large judge and reduces intra-model bias. | QP2, QP3, QP8 |
| [Grove et al. 2000, "Clinical versus mechanical prediction: a meta-analysis"](https://pubmed.ncbi.nlm.nih.gov/10752360/) | Across 136 studies, mechanical combination of information matches or beats holistic expert judgment. | QP1, QP8 |
| [Beygelzimer et al. 2021, "The NeurIPS 2021 Consistency Experiment"](https://arxiv.org/abs/2306.03262) | Parallel expert committees disagreed on 23% of accept/reject decisions; single-reviewer verdicts are noisy even among experts. | QP8 |
