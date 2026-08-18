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
review findings the way `D-<slug>` ids are.

| # | Principle | Surface | Evidence |
|---|-----------|---------|----------|
| QP1 | **No LLM ordinal score enters a control decision.** Continue/stop/accept/merge decisions are deterministic functions of bounded categorical facts; severities are mechanical floors, clamped up only. | `controller.py`, `triage.py`, `taxonomy.py` (the `conceptual_conflation` major floor is a mechanical clamp-up-only floor — D-conceptual-conflation), `schemas.py::OrchestratorView`; CI: `aggregate.mjs`, the judge stage | Grove et al. 2000 |
| QP2 | **Second witnesses are cross-family.** Agreement adds information only to the extent errors are decorrelated; a same-family model added as an extra witness adds correlated errors, not independence — and even cross-family agreement is partial evidence, never proof. | `config/roster.yaml`; `roles.py::critic_slate` selects at most one model per family and `roles.py::lens_statuses` counts clean families; `config.py::validate_roster_health` warns when a pool cannot supply two families, and re-runs on the reduced roster when `graph.py::_degrade_roster` drops an unprobeable alias, so an outage can only ever *shrink* the eligible family set and never substitute a same-family witness for a missing one (D-degraded-roster) | Kim et al. 2025; Li et al. 2025; Verga et al. 2024 |
| QP3 | **The CI panel itself stays family-diverse, and names what it runs.** Reviewer roles split across at least two agent families, and a role sits cross-family from the role it primarily deconflicts with. Every role pins its model: `agent:` fixes only the family, so an unpinned role follows a vendor default and the panel's composition stops being a reviewable property of this repository. | `agent:` and `model:` inputs in `review-pipeline.yml`, the `agent_model()` maps in `resolve-issue.yml` and `review-fixer.yml`; enforced by `tests/test_ci_model_pins.py` | Kim et al. 2025; Verga et al. 2024 |
| QP4 | **Author exclusion is evidence, not preference.** Models reliably fail to correct their own output and measurably favor it; the same claim relabeled as external input is corrected at far higher rates. A decision record cannot repeal a measurement. | `roles.py`, `config.py`; `graph.py::_record_support` and `support.py` produce a same-author provenance record that is explicitly not an independent witness and cannot enter review or control; `graph.py::_adjudicate` excludes the disputing writer from arbitration and uses the prior draft's `defect_citation_scope`, so the writer's revision cannot supply its own adjudication evidence (D-dispute-evidence-prior-draft); spec: [isolation.md](./isolation.md) | Huang et al. 2024; Panickssery et al. 2024; Chen, Su & Chiang 2026 |
| QP5 | **No critique or report prose crosses contexts as instruction.** Review happens in fresh, blind contexts; critic→writer traffic is bounded structural data; the blind orchestrator sees integers and enums. CI reviewers never read each other's findings. | `prompts.py` (including the untrusted-data fences around writer-read pages and the separate support pass), `llm.py::structured` (schema-repair feedback is validator-attributed, fenced and marker-scrubbed; rejected input and model-authored extra-key locations are excluded — D-validator-error-hygiene), `triage.py`, `schemas.py` (patch-scoped revision under `revision.mode`/D-scoped-revision narrows the *edit*, not the traffic — critic→writer input stays bounded structural data); `graph.py::_triage` carries only the prior draft's bounded URL set in `defect_citation_scope` for later adjudication, never that draft's prose (D-dispute-evidence-prior-draft); CI: independent reviewer jobs | Song 2026; Sharma et al. 2023 |
| QP6 | **Refinement, not debate.** Models never argue in a shared transcript; the cycle is generate → independent critique → mechanical triage → regenerate. Visible peer judgments collapse independence. | `graph.py`, `prompts.py`; CI: panel-then-deterministic-judge topology | Smit et al. 2024; Lorenz et al. 2011 |
| QP7 | **Every loop is capped, and the cap is honored at every entry point.** More iterations do not monotonically improve output; refinement loops adapt to their evaluator. | `controller.py`, `config.py::Budgets` (the rule-13 rewrite valve is a new loop, bounded by `Budgets.rewrite_cap` and unreachable at/beyond the hard cap — D-scoped-revision); boot-time recovery spends **two** capped budgets, not one — `max_resume_attempts` bounds a run failing on its own inputs, and `max_deferred_attempts` bounds a run the *deployment* keeps refusing to start, counted by `registry.consecutive_deferrals` and terminating in the same `abandoned` event (D-deferred-not-abandoned; a deferral is cheap, which is an argument for a generous cap and not for an absent one, since an uncapped deferral loop would accumulate runs that never reach a terminal state for anyone to notice); CI: `MAX_CYCLES`, the dispute budget, agent timeouts, and the inherited-verdict classifier that preserves a cycle only for a verified no-new-content case: either a whole-range, tree-identical base resync or an unchanged head equal to its verified review anchor (D-inherit-whole-range, D-inherit-reviewed-anchor; the resync recreation registers the same regional merge driver the fixer's sync uses, so a driver-resolved resync still recreates identically when independently added decision sections and clean shared-region edits are recombined — D-decisions-merge-driver, D-decisions-merge-regions) | Huang et al. 2024; RG-001/RH-001 lineage in [decisions.md](./decisions.md) |
| QP8 | **Verdicts come from deterministic aggregation of structured findings, never an LLM grading prose.** Expert reviewer agreement is low even among humans; the remedy is structure and mechanical aggregation, not a smarter holistic judge. | `controller.py`, the blind orchestrator; `audition.py` (`judge` computes `fit`/`unfit` from aggregated `Metrics` against `AuditionThresholds`; `CacheEntry.matches`/`rubric_hash` decide which stored `Metrics` a verdict may be read from; measuring paths include `structured_output_mode` in that identity, while the no-spend `cached_judgements`/`enforce_fitness` startup gate deliberately reads across mode drift and reports it separately — D-audition-rubric-identity, D-audition-probe-parity); CI: the judge and the deterministic whole-range/tree-identity gate controlling whether its prior verdict may be inherited, both run from `main` (D-inherit-whole-range; the gate's `docs/decisions.md` recreation stays deterministic under the trusted regional merge driver, which uses `git merge-file` for shared regions and decides only the ordering of independently added decision sections — D-decisions-merge-driver, D-decisions-merge-regions) | Grove et al. 2000; Beygelzimer et al. 2021; Verga et al. 2024 |
| QP9 | **Empirical claims in `docs/` carry citations that support them as stated.** A claim strengthened beyond its source, or left standing after its citation is removed, is drift. | every `docs/*.md`; the [References table](#5-references) | the register itself |
| QP10 | **Verification means fetched text, never parametric memory.** A model's recollection of a source is testimony from the component whose reliability is in question. For dispute adjudication, fetched text counts only when its URL was cited by the draft the finding was raised against, not merely added by the disputing writer's revision. A fetched body may be recorded as body-backed coverage while its text is withheld from one critic context for efficacy; that state must be explicit, must not be presented as a failed fetch or registry-only corroboration, and cannot support a `misrepresented_source` judgement in that context. Retrieval bounds must be anti-pathological rather than spend caps and must refuse explicitly instead of silently truncating evidence into apparent absence. | `dispute.py` mechanical adjudication and `graph.py::_triage`/`_adjudicate`, where `defect_citation_scope` pins citation membership to the prior draft (D-dispute-evidence-prior-draft); `reading.SourceReader` and `support.check`; `search.verify_sources`, `search.read_sources`, `search.support_manifest`, `search.BraveSearch`/`search.MAX_RESPONSE_BYTES`, and the opt-in `sources.*` reader tiers that widen what counts as fetched text (`config.SourcesConfig`, `fetch.SourceFetcher`, `graph._pdf_reading_enabled`, and the paid `resolve.extraction` renderer tier — `ResolutionTier.EXTRACTION` — whose markdown is the cited URL's own body and so carries no `body_source_url`); `prompts.fetched_sources_block` distinguishes shown bodies from `FETCHED, TEXT WITHHELD`; the `quality` prompt's own fetch rule; and `fetch.coverage`, which records retrieval coverage independently of how much body text fits in one evidence context and treats deeper markers as annotations relative to the entry depth, anchored at the shallowest non-heading marker (D-observed-source-coverage, D-unbounded-evidence, D-bibliography-entry-nesting, D-brave-egress-hardening) | D-writer-disputes, D-dispute-evidence-prior-draft, D-retrieval-opt-in/D-source-verification, D-writer-source-reads, D-paid-tier-page, D-observed-source-coverage, D-unbounded-evidence, D-bibliography-entry-nesting, D-brave-egress-hardening in [decisions.md](./decisions.md) |
| QP11 | **Evidence-base freshness is checked mechanically and is never blocking.** See the marker line above and [§3](#3-refreshing-the-evidence-base). | this file | — |
| QP12 | **Principles-as-spec drift is blocking, in both directions.** Behavior governed by QP1–QP10 changing without this file and `decisions.md` moving too — or a principle here weakening with no new fetchable evidence in the diff — is the `quality` reviewer's row-12 analogue. See [§4](#4-retiring-or-weakening-a-principle). | this file + every surface above | — |

**Application — addressed blockers in finalize comments (D-addressed-blockers-visible).** Addressed
and unaddressed blocker ids are derived mechanically from the same structured reviewer and fixer
artifacts under QP8. The added verdict field changes how the finalize comment classifies findings for
display, not the GO/NO-GO boundary; no LLM prose or ordinal judgment enters either classification.

**Application — resumed-agent stall bounds (D-resume-stall-guard).** The resumed fixer remains under
the unchanged outer agent timeout, while a 3-minute first-output deadline and a 10-minute
between-output deadline add earlier bounds for silent attempts. Both idle deadlines route to the
cold fixer rather than opening another resume loop, and the session quarantine prevents repeated
attempts for the same recorded session. Together these preserve QP7's capped-loop requirement at
the author-resume entry point without shortening the total budget of an active attempt.

**Application — reviewer validation wait (D-validation-wait-budget).** The reviewer guard's inner
wait is capped at 40 polls with 15-second intervals, sleeps only between polls so every interval is
observed by a later check, and remains inside the guard job's 15-minute outer timeout. The inner cap,
outer timeout, and terminal poll ordering are one QP7-governed bounded loop: changing any of them
requires re-checking the others rather than treating the constants independently.

**Application — base-moved resync (D-base-moved-resync).** `sync-open-prs.yml` adds a call site for
the `docs/decisions.md` merge driver outside any review cycle, and two QP7-governed bounds hold it:
its wait for an in-flight review run is capped by a single deadline shared across the whole loop, not
one per PR, and the merge it pushes is authored as `AGENT_COMMIT_EMAIL` so `MAX_CYCLES` is not
silently reset by a machine merge that answered no blockers. The gate it feeds is unchanged — the
merge is deterministic git content on both sides of D-inherit-whole-range's tree-identity test, so
QP8's "never an LLM grading prose" holds at the new entry point too.

**Application — the inherit anchor (D-inherit-reviewed-anchor).** That same gate was measuring from
the wrong commit: `review/cycle` lands on the fixer's push, so the range walk and the tree
recreation compared a head with itself and inherited unconditionally. Anchoring both on
`review/verdict-anchor` — one status whose state is the verdict and whose description is the commit
that verdict is about — keeps QP8's determinism (still git plumbing and commit statuses, still no
model) while restoring what QP7 depends on for the cap to mean anything: a cycle is preserved only
when the verified anchor equals the unchanged head or the whole range is a tree-identical base resync,
and a fixer push that added something is read rather than re-stamped. Keeping both facts in one status
also prevents concurrent finalizers from manufacturing a verdict/anchor pair no run published
(D-atomic-verdict-anchor).

**Application — answer obligations (D-answer-obligations).** `incomplete_answer` is structured
completeness output with a mechanical `major` floor under QP1. Its writer and critic prompt
obligations change what each isolated role must assess, not what crosses contexts, so QP5's traffic
boundary is unchanged. The audition measures the added category through its existing structured
metrics, deterministic verdict, and rubric-identity invalidation under QP8; no LLM grades prose into
a control decision.

**Application — the in-call repair turn (D-repair-turn-context).** Handing a critic back the field
value it just submitted puts its own earlier output into its own context, which the drift table in
[isolation.md](./isolation.md) names as a **primary** vector and which QP4 is otherwise the strongest
argument against. Three things bound it, and the register's own evidence is what shapes the design:

* QP4 rests on Huang et al. 2024, whose result is that self-correction degrades **without external
  feedback**. A deterministic validator naming a field, a locus and a violated constraint is
  external feedback, so that citation does not settle this case either way.
* **Gou et al. 2023 (CRITIC)** is the new evidence this exception rests on, added to the References
  table under §4. It is the positive half of the same boundary: a verify → correct → verify loop in
  which a model revises its own output *against external tool feedback* consistently improves it,
  and the paper's own conclusion is "the crucial importance of external feedback". That is the shape
  implemented here — a mechanical validator's verdict, the rejected field, and a bounded correction.
  Its limit is stated rather than hidden: it evaluates factuality, program synthesis and toxicity,
  not schema or field-level repair, so it establishes the enabling condition and not this
  application.
* Panickssery et al. 2024 self-preference is bounded by the channel rather than by the prompt: the
  validator's verdict is mechanical, the critic has no way to overrule it, and the output remains a
  closed schema that still fails the lens closed once the budget is gone.

**What Chen, Su & Chiang 2026 does *not* support here.** An earlier draft of this note claimed that
study backed attributing the rejected value to the validator rather than to the critic. It does not.
It moves a byte-identical erroneous claim between actual chat-template **roles** — assistant thought,
user, tool, system memory — with the best role varying by domain. The repair turn keeps the value in
a user turn and changes only prose attribution, so the 23–93 point result is not inherited and is not
claimed. The attribution wording stands as a design choice with a plain rationale — at that point the
text *is* a candidate a check rejected — and carries no evidentiary weight.

QP5's traffic boundary is untouched — nothing new crosses to a *generator*, and the returned value
is the critic's own bounded field travelling back to the same call. QP6 is untouched: there is no
peer, no transcript and no debate. The claim being made is narrow and should stay narrow — that a
bounded same-task repair exception is warranted, not that relabeling restores independence.

**Application — measured completeness eligibility (D-completeness-pool-noise).** Removing an
auditioned-unfit critic from one lens leaves completeness with Google and Zhipu witnesses. That is
still cross-family under QP2 and can supply the two clean families required for strong acceptance,
but it is the minimum compliant pool and has no spare critic after a failed depth-2 review. The
fit-first order applies QP8's deterministic audition verdict to roster position; it does not treat
an LLM's prose assessment as a control decision.

**Application — critic retirement and ordering (D-minimax-retirement).** Removing a critic graded
unfit on both of its lenses leaves logic with Mistral and Zhipu witnesses and evidence with Zhipu and
Google witnesses, preserving QP2 family diversity. Author exclusion thins logic to one eligible
critic when `mistral-large-3` authored the report, so that lens cannot supply the two clean families
required for strong acceptance on those rounds. Fit-first ordering applies QP8's deterministic
audition verdicts and measured sensitivity to roster position; no LLM prose grades the roster.

**Application — writer failure classes (D-writer-failure-class).** Recording a `failure_class` on
each failed attempt makes repeated failure modes countable without interpreting provider prose.
The token is derived from an exception type and status code, so no LLM assessment enters it, and it
reaches the audit trail rather than any control decision under QP1.

**Application — probe capability evidence (D-probe-capability-evidence).** `structured_output_mode`
is a term in the audition cache identity under QP8's `CacheEntry.matches`/`rubric_hash` rule
(D-audition-probe-parity), so what `probe_structured_output` reports as an alias's mode is itself
part of a verdict's deterministic identity. Reading any call failure — whether a transient 429 or a
permanent 400 whose broad status does not identify the rejected field — as capability evidence would
let an unobserved cause choose which regime a cached verdict is keyed to. That is exactly the kind of
non-mechanical noise QP8 exists to keep out of aggregation. Capability verdicts now require observed
model behaviour; an unresolved call raises instead of asserting a mode it never actually measured.

**Application — writer source reads (D-writer-source-reads,
D-support-normalized-text).** Writer-read page bodies are production evidence, not critique: QP5
requires them to enter the writer context as fenced untrusted data, and no page instruction or
support-manifest prose crosses into a critic or later writer. The separate manifest call is made by
the report's author, so under QP4 it is a provenance assertion rather than an independent witness;
its entries and deterministic verdicts remain audit-only and cannot affect acceptance. Under QP10,
`supported` requires a non-empty normalized claim in the report and a non-empty normalized span in
the fetched body of the cited URL; parametric recollection and empty-string containment establish
nothing.

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
| [Gou et al. 2023, "CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing" (ICLR 2024)](https://arxiv.org/abs/2305.11738) | A verify → correct → verify loop in which the model revises its own output against **external tool feedback** consistently improves it across free-form QA, mathematical program synthesis and toxicity reduction; the paper's stated conclusion is "the crucial importance of external feedback in promoting the ongoing self-improvement of LLMs". It evaluates factuality, program synthesis and toxicity — **not** schema or field-level repair, so it establishes the enabling condition, not this application. | QP4 |
