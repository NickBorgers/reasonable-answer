# Design decisions & adversarial-review log

## Key design decisions (from the design dialogue)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Alternating refine game.** A report is written by one model and critiqued only by models that did not write it; the next report is written by a different writer. *(Roster later generalized to a writer pool + per-lens critic pools by D14–D16.)* | Dissolves the corroboration-vs-specialization conflict; guarantees `critic ≠ producer`; convergence becomes temporal. |
| D2 | **Structured defect-list handoff**, not raw critiques. | Keeps principles #1 (artifact-first) and #6 (fresh context) fully intact while still telling the generator what to fix. |
| D3 | **Blind LLM orchestrator inside a deterministic controller.** | The user wants the AI to add judgment on the signal summary (its main value); the controller guarantees termination the LLM cannot. |
| D4 | **Observable-category taxonomy** (no intent tags). | A critic can't infer intent from text; `uncited_claim`/`contradicted_claim`/`fabricated_citation` are checkable. |
| D5 | **Report carries its own citations; no external retrieval in v1.** Uncited material claims are challenged. *(Amended by D17: retrieval is now implemented as an opt-in, off by default. With `search.enabled: false` this decision holds exactly as written.)* | Matches "the argument is sound" via in-artifact sourcing; output labeled *consensus-reviewed*, not fact-checked. |
| D6 | **Structural isolation boundary** for the orchestrator (`OrchestratorView` DTO only; superseded the earlier `SignalReport` name — see D11). | Makes blindness real, not a coding convention over shared state. |
| D7 | **Cross-model confirmation** before `accepted` (refined by D9/D14). | A single clean critique is one model's opinion; strong acceptance needs **two distinct non-author models** clean on the identical artifact (≥3-model roster). |
| D8 | **min_ticks floor.** | "The first tick should never be accepted." |

## Codex adversarial review — round 1 (verdict: CHANGES_REQUESTED, 20 findings)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RA-001 | crit | Blocking issues route to REVISE before the cap check → infinite loop; "guaranteed termination" false | **Fixed.** Controller checks `fatal` → `cap` **before** any revise; ordered stop-decision in [convergence.md](./convergence.md). |
| RA-002 | crit | Artifact-blindness is only a convention over shared state | **Fixed (D6).** Orchestrator invoked with a SignalReport DTO built outside nodes; noninterference test; redacted telemetry. |
| RA-003 | high | 2-model corroboration = brittle unanimity; 3 = silent majority | **Superseded by D1/D7.** No per-defect voting; agreement is temporal + whole-artifact cross-confirmation. |
| RA-004 | high | Orthogonal critics can't corroborate each other | **Superseded by D1.** Corroboration no longer required within a tick. |
| RA-005 | high | Lone blocking issue ignored as a nitpick (unsafe) | **Fixed.** Single critic per tick; **any** ≥ major issue forces another tick. Lone blocking is never ignored. |
| RA-006 | high | `dishonest` requires intent inference | **Fixed (D4).** Replaced with observable categories. |
| RA-007 | high | No handling of malformed/timeout/partial-critic failure | **Fixed.** Failure table in [architecture.md](./architecture.md); incomplete review never counts as clean. |
| RA-008 | high | Triage semantic dedup ill-defined; LLM triage = unblinded bias | **Fixed.** Triage is mechanical (tally structured findings), no LLM; canonical locus normalization; provenance kept in audit. |
| RA-009 | high | "Content-free" undefined; SignalReport could leak/covert-channel | **Fixed.** Closed schema (bounded enums/ints), metadata allowlist, noninterference test. |
| RA-010 | high | Prompt injection via seed/report/critique text | **Fixed.** Threat model in [isolation.md](./isolation.md): all such text untrusted; structured-output boundaries; validation; adversarial tests. |
| RA-011 | high | No evidence layer; models can agree on a plausible falsehood | **Scoped (D5), then addressed (D17 + D18).** In-artifact citations required; uncited claims challenged; output relabeled. Retrieval is no longer deferred: with `search.enabled: true` writers cite only URLs a live search returned, and with `search.verify_sources: true` the evidence lens reads those pages and can falsify `misrepresented_source` against them. Both off by default *in code*, so the D5 posture remains the default posture for a bare checkout; the shipped `config/roster.yaml` opts into retrieval only — verification stays off until a deployment provides the network-layer egress boundary (D22). The residual blind spot is narrower but real: verification shows a page exists and is compatible with the claim, not that the page is correct. |
| RA-012 | high | "Finalize" conflates accepted with known-unacceptable | **Fixed.** Four terminal statuses: `accepted` / `exhausted_unresolved` / `needs_human_review` / `aborted`. |
| RA-013 | med | Plateau/oscillation/best-scoring undefined | **Fixed.** Precise definitions in [convergence.md](./convergence.md). |
| RA-014 | med | No round-identity/reducer contract; replay can fake convergence | **Fixed.** Keys `(run_id, round, artifact_hash, models, lens, attempt)`; idempotent reducers; stale-hash rejection. |
| RA-015 | med | Single endpoint / no concurrency, timeout, capability checks | **Fixed.** Ops section: bounded concurrency, per-call timeout/retry, startup structured-output capability check, roster health check. |
| RA-016 | med | Audit trail may hold sensitive data; no retention/access policy | **Fixed.** Data classification, least-privilege perms, retention/deletion, redaction; note LiteLLM proxy logging. |
| RA-017 | med | "Distinct models" ≠ independent (aliases, fallback, same family) | **Fixed.** Enforce distinctness at resolved provider/model/version; no duplicate fallback; roster requirements generalized to per-lens eligibility by D16 (≥2 eligible non-author models per lens for strong acceptance); fail closed. |
| RA-018 | med | Input routing for question/seed combinations undefined | **Fixed.** Intake routing table + validation in [architecture.md](./architecture.md). |
| RA-019 | med | Only one isolation test mentioned | **Fixed.** Test matrix below. |
| RA-020 | low | Orchestrator/triage trust models inconsistent (agent vs pure logic) | **Fixed (D3).** Orchestrator = blind LLM inside a deterministic controller; triage = mechanical. |

## Operational requirements (RA-015 / RA-016 / RA-017)

- **Roster (role-structured, superseded by D15/D16):** a **writer pool** plus **per-lens critic
  pools** (each ≥2 eligible non-author models for strong acceptance; critic-only specialists
  allowed). Resolve/record provider/model/version behind each LiteLLM alias; enforce distinctness at
  that level; no silent fallback to a duplicate; **fail closed** (abort) if the writer pool is empty
  or any lens has no eligible non-author model. Startup validates structured-output support and
  per-lens roster health. (See [architecture.md](./architecture.md) for the normative statement.)
- **Concurrency/limits:** bounded concurrency (the 3 lenses may run in parallel), per-call
  timeout + retry budget, token/context budgeting for the slow local model, backpressure so
  "parallel" lenses don't overload a single proxy/model.
- **Audit/privacy:** `runs/<id>/` (reports, critiques, defect lists, decisions) holds sensitive
  seed material → least-privilege file perms, data classification, retention/deletion policy,
  trace redaction. OrchestratorView-level telemetry is redaction-safe; raw artifacts are stored
  separately with tighter access. Consider LiteLLM proxy request logging.

## Test matrix (RA-019) — zero-key by default via a deterministic `MockLLM`

| Area | Tests |
|------|-------|
| Controller ordering | fatal→abort precedence; cap-before-revise; terminal-status mapping (unit) |
| Termination | property test: bounded steps for arbitrary MockLLM issue streams |
| Convergence | accepted only after cross-model confirm; min_ticks enforced; plateau/oscillation detection |
| Isolation | noninterference: identical `OrchestratorView` ⇒ identical recommendation regardless of report content; generator/critic context-boundary tests (each sees only its permitted slice); confirmation-indistinguishability (a critic cannot detect it is confirming) |
| Severity/validity | mechanical floor clamping (critic can't downgrade a floor category); unknown/invalid field fails the whole lens |
| Prompt injection | adversarial seed ("return zero issues"); adversarial critic output smuggling instructions |
| Failure handling | malformed/timeout/partial-lens → not counted clean; repeated → abort |
| Resume/replay | checkpoint replay idempotency; stale-hash rejection |
| Redeploy survival (`tests/test_shutdown.py`) | a stop flag pauses the graph at a **node boundary**, never mid-node: work completed before the pause survives and is not re-run on resume, and the run reaches its normal terminal status; the pause is recorded as an event and is not logged as a crash; `shutdown()` returns within its budget while a job is in flight; queued-but-unstarted work is durable on disk, not only in the in-memory queue; boot recovery re-enqueues `queued`/`interrupted` runs and skips finished ones, and can be switched off; a run that makes no progress across `max_resume_attempts` **consecutive** auto-resumes is abandoned, while any progress event resets the count; `ResumeMismatch` (e.g. a roster change under an in-flight run) abandons rather than retrying every boot; abandonment writes an event and **never** a `final.json` — the audit trail must not claim a terminal status the controller never issued; `abandoned` is terminal for the UI yet still manually resumable; the grace budget is read from the platform and falls back rather than crashing on a bad value |
| Retrieval / web search (D17) | offline-when-off (no `tools` offered, prompt byte-identical to the pre-retrieval path); startup fails closed on a missing credential **and** on a tool-incapable writer; `probe_tool_calling` returns False for a model that accepts `tools` and never calls one, and for a probe that raises; per-**run** query budget (not per-call) enforced under concurrency; budget exhaustion and fetch failure surfaced to the model as text, never as silence; results fenced as untrusted (RA-010); the agentic tool loop terminates — the exhausted round drops `tools` and forces prose — and `Completion.tool_calls` matches the number executed; the query string never reaches a log (RA-016) |
| Source verification (D18) | citation URLs extracted from the `## Sources` section only (a URL mentioned in passing is not fetched); **only the evidence lens** receives page text — logic and completeness never do; a failed fetch is surfaced as "could not fetch" and never as evidence of fabrication; truncation disclosed; unreadable content types (PDF) reported honestly; pages fetched once per run and cached across rounds; bounded by timeout, byte cap, redirect cap and http(s)-only; verification off ⇒ the evidence prompt is byte-identical to the D17 path |
| Seed ingest / format conversion (D24) | every converter meets the output contract (blank-line-separated blocks, headings alone on their line) so `report.parse` loci survive; PDF/`.docx`/HTML/`.txt` conversion each covered offline (urllib's opener stubbed — no network, no keys); one bounded http(s)-only egress point reused from `fetch.http_get`; `file:`/`ftp:`/`data:` schemes refused before any opener exists; the `.docx` zip-bomb guard (`seed.docx_max_uncompressed_bytes`) trips **before** decompression; truncation is fatal for binary formats and a warning for text; a heading-less format yields one section plus a warning, never a failure; URL seeds refused when `seed.allow_url` is off (the default) — the form field disappears and the parameter 400s; the web layer never constructs a `Path` from request data; converted markdown is byte-identical between what is hashed, stored and critiqued (resume fingerprint) |
| End-to-end | labeled fixtures where a known-flawed seed must reach `accepted` with the flaw fixed |

Real-proxy integration tests are **marker-gated**: they carry the `live` pytest marker declared in
`pyproject.toml`, and CI deselects them with `-m "not live"`. The proxy endpoint comes from
`proxy.base_url` in the roster — or, when set, the environment variable named by
`proxy.base_url_env` (`RA_PROXY_BASE_URL` by default; see D21) — and its key from the environment
variable named by `proxy.api_key_env` (`LITELLM_API_KEY` by default). The full suite passes with no
keys and no network, honoring "clone → run tests."

## Additional decisions (from Codex round 2)

| # | Decision | Rationale |
|---|----------|-----------|
| D9 | **Acceptance = two clean critiques by two distinct non-author models.** *(Generalized to **per-lens** by D15; the 2-model consecutive-clean fallback was later **removed** — weak acceptance is now the per-lens `roster_limited` case, current-hash-only.)* | A two-model "confirm the same artifact" would be the author reviewing its own draft (RB-001). Preserves #7 and is honest about roster limits. |
| D10 | **Mechanical, category-specific severity floors; fail-closed on invalid output.** Triage clamps severity up to the floor; unknown/invalid fields fail the whole lens. | Stops a critic gaming severity (RB-006) or an adversarial/invalid critique collapsing into a fake-clean empty result (RB-007). |
| D11 | **Split `OrchestratorView` (content-free, LLM-facing) from `ControllerInput` (identifiers, deterministic).** | The blind LLM must not see hashes/ids (correlation handles); the deterministic controller may. Makes noninterference testable (RB-004, RB-008). |
| D12 | **Evidence-bearing defect fields** (`claim_span`, `related_span`, `citation_id`, `expected_support`, bounded `rationale`). | `{locus,category,severity,instruction}` can't convey which propositions contradict etc., so a blocking defect could survive (RB-005). Fields are bounded/untrusted/validated. |

## Codex adversarial review — round 2 (verdict: CHANGES_REQUESTED; 6 resolved / 14 partial / 0 unresolved + 10 new)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RB-001 | crit | Cross-model confirmation on a 2-model roster = the author reviewing its own draft | **Fixed (D9; generalized per-lens by D15).** Acceptance requires clean reviews by distinct non-author models; the 2-model consecutive-clean idea was later removed in favor of per-lens `roster_limited` weak acceptance. |
| RB-002 | crit | At cap, a first clean critique could be labeled `accepted` without confirmation | **Fixed.** The cap never accepts a single clean review; clean-but-unconfirmed at cap → `exhausted_unresolved`, and per-lens top-up stays reachable at the cap (see RG-001). |
| RB-003 | high | Confirmation bypassed the critique→triage→controller path (undefined failure/budget/identity) | **Fixed.** Confirmation is an ordinary critique attempt, triaged and returned through the controller. |
| RB-004 | high | Controller's declared inputs insufficient for its deterministic decisions | **Fixed (D11).** `ControllerInput` schema + exhaustive ordered decision table; LLM authority scoped to minor-polish. |
| RB-005 | high | `{locus,category,severity,instruction}` too lossy to fix blocking defects | **Fixed (D12).** Evidence-bearing bounded fields added. |
| RB-006 | high | Critic-selected severity lets a critic downgrade a material defect to `minor` | **Fixed (D10).** Mechanical per-category floors; critic may only escalate. |
| RB-007 | high | "Unknown categories dropped" (isolation) vs "failed lens" (architecture) — dropping can fake-clean | **Fixed (D10).** Unified fail-closed: unknown/invalid ⇒ whole lens fails; loci are bounded structural refs. |
| RB-008 | med | `SignalReport` carried hash/ids (correlation handle); noninterference test impossible as written | **Fixed (D11).** `OrchestratorView` excludes ids/hash; noninterference defined over it. |
| RB-009 | med | Plateau/oscillation as dotted branches; `==0` vs `≈0`; count-multiset "semantic" oscillation misnamed | **Fixed.** Exhaustive ordered table; exact predicates; renamed to `signal-stagnation`. |
| RB-010 | med | Confirmation could be gamed if the critic infers it is confirming | **Fixed.** Identical interface/prompt; `confirm_state` labeled post-hoc, invisible to the model; no cache reuse. |

**Round-1 partials shored up in v3:** RA-002/RA-020 → RB-004; RA-005 → RB-006/RB-007;
RA-008 → RB-005 + locus normalization; RA-012 → RB-002; RA-013 → RB-009; RA-016 → concrete
retention/deletion/LiteLLM-logging defaults; RA-017 → family-independence warning; RA-018 →
seed-only requires an explicit question in v1; RA-019 → added generator/critic context-boundary
and confirmation-indistinguishability tests.

## Additional decisions (from Codex round 3)

| # | Decision | Rationale |
|---|----------|-----------|
| D13 | **The isolation unit is the context window, not the model.** Fresh, blind contexts defeat the *primary* bias (social/context drift) regardless of model; model diversity is a *secondary* layer that decorrelates blind spots. | The dominant threat (sycophancy, contextual drag, in-session self-review) is caused by *shared context*, not model identity — so principle #7 is fundamentally "not the same context." (User insight.) |
| D14 | **Default roster = ≥3 distinct models.** Strong `accepted` = two distinct non-author models clean on the identical final artifact. 2-model rosters can only reach `converged_unconfirmed`. | Two models cannot give the final artifact two independent non-author reviews (RC-001); a third model closes it and adds blind-spot decorrelation. User confirmed 3 models is easy. |

## Additional decisions (post-review design extension)

| # | Decision | Rationale |
|---|----------|-----------|
| D15 | **Per-lens critic models + per-lens acceptance.** Each lens gets its own critic pool, headed by the model best matched to that lens (evidence → the lowest-hallucination model, since `fabricated_citation`/`misrepresented_source` are attribution-fidelity failures); `CleanRecord` is keyed per-lens; strong `accepted` requires **each lens** strongly-cleared (≥2 distinct non-author models). | Matches the best model to each dimension and raises within-tick blind-spot decorrelation. A lens with only one eligible model honestly degrades that dimension to `converged_unconfirmed`, naming the under-reviewed lens. **Correction:** the evidence lens was originally headed by Llama 4 Scout for "huge context to scan citations". That rationale never held — `max_report_chars: 60_000` caps critic input at ~15k tokens, so context length was never the binding constraint. The lens wants attribution *fidelity*, not capacity. |
| D16 | **Role-structured roster with critic-only specialists.** A writer pool plus per-lens critic pools; a model may be pinned as a lens reviewer that never authors. | Cleanly satisfies author-exclusion (author of Rₙ never critiques Rₙ on any lens). Its sharpest use is pinning the roster's *strongest* model as critic-only: as a writer it would be barred from reviewing its own drafts, losing the best reviewer on half of all rounds. `glm-5.2` is critic-only on all three lenses for exactly this reason. |
| D17 | **External retrieval, opt-in and off by default.** Amends D5 and resolves RA-011's deferral. With `search.enabled: true` writers get a `web_search` tool (Brave API) and cite only URLs a search returned; startup fails closed on a missing credential **or** on a writer that cannot emit tool calls. With `search.enabled: false` (the default) D5 holds unchanged and the suite stays offline. | RA-011's blind spot was that a diverse roster can agree on a plausible falsehood, and in-artifact sourcing cannot catch an invented citation. Retrieval makes citations *real*; it is opt-in because a credential is required and the default posture must remain "clone → run tests" with no keys. Failing closed on a tool-incapable writer is load-bearing: such a writer still emits a `## Sources` section, and nothing downstream distinguishes a remembered citation from a retrieved one. |
| D18 | **The roster is open-weight only, bounded by what the target box can load.** Every alias resolves to downloadable weights, and none exceeds ~450GB at 4-bit — the single-model ceiling on a shared ~768GB machine, with swapping between roles. | Two independent reasons. (1) `docs/DESIGN.md` commits to a local runtime; a roster containing models that cannot load there is not a dry run of it, it is a surprise deferred. (2) No role is locked to a vendor. Consequences: `deepseek-v4-pro` (~800GB) and `kimi-k3` (~1.4TB) are excluded by arithmetic, not preference; `qwen3.7-max` is excluded because Alibaba closed the 3.7 weights (the open Qwen line stops at 3.6); `nemotron-3-ultra` fits but was excluded by choice, which costs the evidence lens the only open model with an independent long-context score (RULER 0.947). Both writers report tool-call support, so D17's fail-closed check passes if search is ever enabled. |
| D19 | **The orchestrator has its own roster entry**, optional, defaulting to `writers[0]`. It runs on the free local model. | It was hardcoded to `writers[0]`, so reordering the writer pool silently changed who refereed polish decisions — a coupling with no reason behind it. Its job is bounded ints in, one boolean out (`OrchestratorView`), so it needs neither reach nor a writer's capability, and D17's tool-call requirement does not apply to it. Its blast radius is one skipped polish pass: `_orchestrate_call` swallows call and schema errors and returns `False`, and rule 9 is cap-gated, so the LLM can only ever *enable* polish. The alias joins `all_aliases` so startup resolves and probes it — without that, an identity mismatch would disable rule 9 permanently and silently. |
| D20 | **The checkpointer is the durability guarantee; the SIGTERM grace period is only an optimisation.** A redeploy stops the graph at the next *node* boundary, never mid-node and never "after the round". Boot re-enqueues whatever was owed. A run that makes no progress across N **consecutive** auto-resumes becomes `abandoned` — a registry-inferred lifecycle state that is terminal for the UI but is deliberately **never** written to `final.json`. | A run is 10–25 minutes, so no grace period can wait for one to finish; designing around that would make correctness depend on a number the platform owns and can change without telling us. Since LangGraph persists at every node boundary, a SIGKILL already costs at most the node in flight — so the grace window buys the chance to *land* that node rather than re-pay for it, and shortening it wastes work without risking corruption. The cap counts consecutive rather than total attempts so a restart storm cannot spend the budget on runs it never actually executed; any progress event resets it. `abandoned` avoids `final.json` because that file means the controller reached a verdict (D12/RA-012), and giving up is not a verdict — inventing one would let the audit trail claim a terminal status no rule ever fired. A human can always resume past it, so the cap bounds automation, not the run. |
| D21 | **`proxy.base_url` is overridable by an environment variable, named by `proxy.base_url_env` (default `RA_PROXY_BASE_URL`).** Precedence: env value > roster file value > built-in default. The roster's `base_url` becomes the *fallback*, not necessarily the effective value; the override is applied once in a `ProxyConfig` after-validator so every reader (`LLMClient`, `_fetch_model_info`) sees the resolved URL with no call-site change. Unset or empty env leaves the file value untouched. | Mirrors the existing `api_key_env` hook so the config surface stays consistent. Before this, `base_url` was readable only from the file, so a containerized deployment on a Docker bridge network — which cannot resolve the baked Tailscale MagicDNS URL and reaches the LiteLLM proxy by container DNS name (`http://litellm-proxy:4000/v1`) — had to mount a whole override `roster.yaml` just to change one line, shadowing every upstream roster change (model retunes, new critics, search defaults) and forcing a manual re-sync each time. Injecting one env var lets the baked roster stay authoritative for models, critics, search, and budgets. Kept backward-compatible: a roster with a plain `base_url:` and no env set behaves exactly as before. Applied in a validator rather than as an `api_key`-style lazy property because `base_url` is a plain field read across the codebase as an attribute, and a property cannot share its name; resolving at load also means nothing ever reads a URL the env was meant to override. No invariant is touched — this is a deployment-config affordance, not a change to isolation, author-exclusion, the orchestrator's blindness, or the controller. |
| D22 | **Critics and writers are grounded in the run's date, and the shipped roster opts into retrieval (D17); source verification (D18) stays off everywhere by default.** A `run_date` (UTC) is captured once at intake, stored in graph state, and injected into every writer and critic prompt as trusted context outside the data fence. The code defaults for `search.enabled` and `search.verify_sources` stay `false`; `config/roster.yaml` flips on `search.enabled` only. The completeness brief and the critic `instruction` contract now require that every demanded fix be resolvable within the report itself (add the perspective, weaken the claim, or state the limitation) — a critic may not make a specific external document the only acceptable resolution. | Run `run-75eb136b9bfb` stagnated to `needs_human_review` with good output: the evidence lens, judging "on its face" plausibility from its training-data recency, flagged legitimate current-year citations (one dated the previous day) as future-dated `fabricated_citation` — a blocking defect with a severity floor the writer can never argue down and, without retrieval, never fix. Simultaneously the completeness lens demanded a specific budget-vote record the writer had no way to retrieve, while `writer_revision` (correctly) forbids inventing sources — an unsatisfiable demand. One date per run (not per call) keeps RB-010's byte-identical confirmation critiques across midnight; old checkpoints without `run_date` resume dateless, i.e. with the prior behavior. The date is excluded from the audition prompt-hash surface because it is run context, not lens semantics. Enabling search makes citation demands satisfiable, and retrieval-grounded citations carry real, current dates — closing the false-`fabricated_citation` loop even without verification. Verification would go further (URL resolves, page matches), but it fetches model-chosen URLs, and the egress boundary that makes that safe is a network-layer deployment concern deliberately not implemented in this repo (docs/ssrf-egress-isolation.md documents the pattern); the shipped roster therefore leaves `verify_sources: false`, to be enabled per-deployment behind such a boundary. Search itself is not that exposure: it talks only to the fixed public Brave endpoint. |

| D18 | **Source verification for the evidence lens, opt-in and off by default.** With `search.verify_sources: true` the pages a report cites are fetched and handed to the **evidence lens only**, as untrusted data. `fabricated_citation` and `misrepresented_source` become checkable against the page instead of judgements about plausibility. A failed fetch is explicitly *not* evidence of fabrication. Not an SSRF boundary — egress is constrained at the network layer, not here. | D17 constrained where citations come from; it did not establish that a cited page supports the claim attached to it, because no critic could open one. Evidence-lens-only is an isolation requirement, not an optimization: logic and completeness cannot raise a citation category, so page text would widen what they see without widening what they may report. Off by default because fetching model-chosen URLs is exposure a deployment must opt into. |

## Codex adversarial review — round 3 (verdict: CHANGES_REQUESTED; 5 resolved / 4 partial / 1 unresolved + 6 new)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RC-001 | crit | Two-model "faithful regeneration" launders authorship → a model reviews its own content; the final artifact gets only one non-author review | **Fixed (D13, D14).** Reframed isolation unit; default ≥3 models with same-artifact `accepted`; honest weaker `converged_unconfirmed` tier for 2 models; "faithful regen" language removed. |
| RC-002 | high | Clean-review evidence not keyed to the accepted artifact; stale attestations could satisfy acceptance | **Fixed** (record now **per-lens**, D15): immutable `CleanRecord{artifact_hash, lens, critic_identity, author_identity}`; any generation/polish resets the set; `strong_met` needs two distinct non-author records **per lens** for the exact current hash. |
| RC-003 | high | Ordered table wasn't the whole controller function (omitted lenses_failed, polish, cycle, thresholds) | **Fixed.** The single ordered table (now 14 rules after the per-lens reorder) includes lens-failure, polish (+counter/cap), and cycle rules; totality/termination argued explicitly. |
| RC-004 | high | Cap rules preceded the incomplete-review check → partial counts could be classified clean | **Fixed.** `lenses_failed > 0` is now rules 2–3, before any clean/material/cap conclusion; partial counts never satisfy a clean predicate; no retry budget ⇒ `aborted`. |
| RC-005 | high | `overstated_claim`/`omitted_counterargument` relied on critic-supplied materiality | **Fixed.** Both floored mechanically at `major`; the materiality-downgrade path is removed. |
| RC-006 | low | DESIGN.md/isolation.md still labeled v2 and referenced `SignalReport` | **Fixed.** All docs relabeled v3; normative `SignalReport` references replaced with `OrchestratorView`/`ControllerInput` (historical review-log mentions retained). |

## Codex adversarial review — round 8 (per-lens extension; verdict: CHANGES_REQUESTED, 0 crit / 2 high / 1 med / 1 low)

Rounds 4–7 drove the pre-extension design to 0 critical / 0 high / 0 medium (table verified total
and terminating; 3-model acceptance trace confirmed). Round 8 reviewed the D15/D16 per-lens
extension:

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RG-001 | high | At the cap, terminal rules fired before per-lens top-up could run | **Fixed.** Clean-artifact rules (7–11) are no longer `round`-gated; only `material>0` cap terminals (rules 5–6) are. Top-up (rule 8) stays reachable at the cap (it doesn't generate or advance `round`). |
| RG-002 | high | The "2-model consecutive-clean fallback" was referenced but never represented in state | **Fixed by removal.** `weak_met` is now purely the per-lens `roster_limited` case (current-hash-only); all consecutive-clean language deleted. |
| RG-003 | med | Tick/sequence/DESIGN diagrams still showed one critic for three lenses | **Fixed.** Diagrams relabeled to per-lens critics (each ≠ author); DESIGN core-loop reframed from "two-model ping-pong" to a role-structured alternating game. |
| RG-004 | low | Stale `lens_set` / rule-number / flat-roster wording in the review log | **Fixed.** RC-002 → per-lens `CleanRecord`; RB-002 de-numbered; D9 annotated as superseded by D15; roster contract restated as per-lens eligibility. |
| D24 | **Seed reports are converted to markdown at the edge; URL seeds are opt-in and off by default.** `--seed` and the web form accept PDF, `.docx`, HTML and `.txt`; `ingest` converts them before `graph.run` is called, which continues to require markdown. http(s) URL seeds exist behind `seed.allow_url`, default `false` (the D17/D18 posture): a URL seed makes the server fetch a caller-chosen URL and expose the body back through the run's report endpoints — on the web UI that is a read proxy into whatever the host can reach, and the egress boundary that makes it acceptable is a network-layer deployment concern outside this repo (docs/ssrf-egress-isolation.md). *(Written when the UI was unauthenticated. D32 identifies callers, which narrows who can submit a URL but not what the host can reach — the egress boundary remains the prerequisite.)* Turning it off hides the form field and rejects the parameter. A format that yields no headings is accepted with a warning, not rejected. | Markdown is not a preference here, it is load-bearing: `report.parse` builds the `[S<n>.P<m>]` loci critics must cite from `#` headings, and `extract_source_urls` reads only a markdown `## Sources` section, so an unconverted seed silently costs the evidence lens its fetch-backed checks. Converting at the **edge** rather than inside `_intake` keeps one artifact and one identity — `_run_fingerprint` and `artifact_hash` would otherwise hash different things (a URL vs. its converted text), letting a resume pass the fingerprint check while the checkpoint held different prose. It also keeps network I/O out of the graph, where every other fetch is injected through `Runtime` so tests stay offline. Accepting a heading-less seed reflects what the formats actually carry: PDF has no recoverable heading semantics without font heuristics, and refusing would block the most common real-world case to protect locus precision the source never had. PDF is the only format needing a dependency (`pypdf`, optional extra); `.docx` is a zip of XML and HTML is an `HTMLParser`, both standard library. |

## D20 — critic eligibility becomes structural *and* demonstrated

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

## Security review — 2026-07 (web submission hardening)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RC-007 | med | Run submission is unbounded in both queue depth and disk footprint. `RunWorker.submit()` enqueued onto a `queue.Queue()` with no `maxsize` and no rate limit, and each submission immediately wrote a persistent run directory. Concurrency bounds token *spend* but not the number of queued runs, the memory they hold, or the run dirs they leave on disk; `recover()` re-enqueues them all on boot. A single burst — a script, or the companion CSRF vector — could create thousands of runs and directories, and `Registry.list()` reads every run dir on each `GET /`. | **Fixed (D21).** `submit()` refuses with HTTP 429 past `max_queue_depth`, and a fixed-window per-identity limiter (`submit_rate_max`/`submit_rate_window_seconds`) throttles bursts. Both checks precede any disk write, so a refusal costs nothing. The web server also runs an automatic content-only retention sweep so disk reclamation no longer waits on a manual `purge`. |

## D21 — submission is bounded, and a refusal costs nothing

The soundness machinery all sits *downstream* of a run existing. Nothing upstream limited
how many runs could be created: the queue was a `queue.Queue()` with no `maxsize`, no
per-caller rate limit gated submission, and `submit()` wrote `question.txt` plus a `queued`
event before enqueuing. Bounded concurrency (default 1) kept token *spend* in check, so the
gap was invisible in normal use — but a burst could still pin unbounded memory (the queue),
unbounded disk (one run dir per submission, purged only by a manual CLI step), and make the
home page progressively slower (`Registry.list()` stats and reads `events.jsonl` for every
dir on each `GET /`).

**Decision.** Backpressure at submission, with two sub-decisions worth recording.

**A refused submission must leave nothing behind.** The depth and rate checks run *before*
the run id is minted and before any file is written. A cap that rejected only after writing
`question.txt` would move the growth from memory onto disk rather than stopping it — the
disk half of the finding would survive the fix. So the order is load-bearing: check, then
write, never the reverse.

**The bounds apply to `submit()` only, never to `resume()` or `recover()`.** Those replay
work already owed and already on disk (D-"surviving a redeploy"): the queue is not the
record of what is owed. Rate-limiting or depth-rejecting recovery would let a backlog wedge
the restart path — precisely the runs the checkpointer exists to protect. Depth is also
checked before the rate limit is *recorded*, so a caller turned away by a full queue does
not also burn its own per-identity allowance on the attempt.

The rate limiter is keyed by the caller's resolved identity — Cloudflare Access email
first, then the Tailscale header, then `auth.dev_identity` — the same identity the auth
middleware enforces. *(Written when the UI was unauthenticated: the limiter then keyed on
the Tailscale header when present and a single global bucket otherwise. D32 superseded that
— every request now carries a resolved identity or is refused by the middleware before it
reaches `submit()`, so there is no shared global fallback bucket left.)* On the tailnet
posture the header is trustworthy; a caller reaching the app directly could forge it, but
such a caller could equally vary it to defeat any per-identity scheme. This is backpressure
against bursts, not itself the access boundary — that is D32's trusted-header gate, with
Tailscale ACLs / Cloudflare Access in front of it.

Retention gains an automatic **content-only** sweep on a timer (`purge --content-only`,
run for you), matching the documented posture — reports/critiques after N days, the
decision record for longer. Full-directory removal stays the explicit human `purge`, so the
audit trail of a run's convergence is never deleted by a background timer. Live runs are
skipped, so an in-flight run cannot lose its drafts mid-run.

This touches none of the isolation invariants: it is upstream of run creation and moves no
new data toward any model context. `OrchestratorView` and the controller are untouched.

## D23 — the cold review fixer exercises grounded judgment, not a mechanical checklist

*(D22 is allocated to run-scoped date grounding, landed separately.)*

The cold fixer's original gate was mechanical by design: a fix had to name a file and line,
be fully determined by the blocker's own description, stay inside reviewer-named files, and
stay under a line cap — and the reconstructed-intent record could only make it skip, never
apply. That posture was borrowed from the reference pipeline's earliest fixer and priced
every judgment call as unaffordable for an agent without the author's reasoning.

In practice it made the fixer nearly useless on exactly the blockers that stall a PR. On
PR #40, cycle 2 skipped both open blockers: one asked compose to adopt an egress-isolation
pattern **already documented in `docs/ssrf-egress-isolation.md`**, the other asked for a
test pinning a new branch, with a whole neighbouring test file to mirror. Neither fix
required the author's private reasoning — both were sitting in the repository — but both
failed the checklist, the cycle cap tripped, and the PR went to `needs-human-review` with
work an agent could have done.

**Decision.** The mechanical gate is replaced by a grounding requirement, adopted from the
current hide-my-list fixer posture: the cold fixer decides like an engineer, and may apply
any fix it can anchor in (1) the repository's existing content and structure, (2) the PR's
reconstructed intent, (3) the reviewer's finding, connected by (4) its own engineering
judgment — with no line cap and no reviewer-named-files-only rule. Each `addressed[].how`
must state the grounding. What it may not do is **invent**: a fix requiring a design
decision the repository has not made, an architectural redesign, or a change the context
record shows to be deliberate is skipped with a reason, exactly as before.

What does *not* change, because the risk it bounds is unchanged: scope stays limited to
reviewer findings (judgment governs *how* a finding closes, never *whether* to do unraised
work); the context record still cannot widen scope and is still untrusted text; a cold
fixer still cannot claim `body_clarification` (schema-enforced — recorded intent is not the
author's own); the docs-coupling rule for invariant-touching fixes still applies; and the
verification run before exit matters *more* under a wider reach, not less. The safety story
is not "the fixer cannot do much" but that the judge grades the pre-fix reviewed SHA, not the
fixer's output: the fixed SHA is not reviewed again (D28), so the pre-fix panel, the fixer's own
gates, and this verification run are the backstop.

## D24 — social-bias categories on existing lenses, governed by docs/bias.md

The user intent this system serves includes *avoiding social biases with rules defined in the
repo as documentation* — and until this decision, no such rules existed: every "bias" the docs
addressed was sycophancy (fixed by fresh contexts) or model blind spots (fixed by roster
diversity). Neither layer touches bias that is **correlated across the whole roster** — loaded
framing, one-sided source selection, and presuppositions inherited from the question — because
every capable model shares training-corpus and cultural priors, and a sample run
(run-75eb136b9bfb, a politically loaded question) leaned on a single-viewpoint outlet cluster
with **no lens able to raise the objection**.

**Decision.** Three observable-text categories on the *existing* lenses, governed by a new
normative doc, [bias.md](./bias.md):

| category | lens | floor |
|---|---|---|
| `one_sided_sourcing` | evidence | major |
| `loaded_language` | logic | minor (escalation permitted) |
| `unexamined_presupposition` | completeness | major |

Plus symmetric writer-side standards in `WRITER_SYSTEM` (neutral language, surface contested
presuppositions, multi-cluster sourcing on contested questions).

**Why categories, not a fourth lens.** A `social_bias` lens would need its own critic pool,
double-clearance for strong acceptance, and roster staffing — diluting per-lens confirmation and
raising the roster bar for every deployment — while each of the three defects already belongs
naturally to an existing dimension (sourcing → evidence, framing-verdicts → logic, omitted
examination → completeness). If audition fixtures later show the categories underperform inside
shared lenses, a dedicated lens is the recorded upgrade path.

**Floors** (argument in [bias.md](./bias.md)): `one_sided_sourcing` major — observable from the
`## Sources` list, undermines the evidence guarantee the way `uncited_claim` does, and only a
material floor forces revision; not blocking, because unlike a fabricated citation every source
is real. `loaded_language` minor — the most judgment-laden category; a material floor would hand
one noisy critic a per-round forced-revision lever, while the clamp-up-only rule (RC-005) still
lets a critic *propose* major for pervasive framing and have it stick. `unexamined_presupposition`
major — `omitted_counterargument`'s sibling and always resolvable within the report.

**Deliberately excluded from `IN_ARTIFACT_RELATED`:** the three categories' `related_span`
describes a pattern (a source cluster, the question's framing), not a second quotable span —
the same rationale as the citation categories.

**Deferred:** a cross-critic bias-correlation audition report ("do this roster's critics lean
the same way?"). It needs paired mirror fixtures with directional ground truth over multiple
repetitions — a new fixture design, not a new aggregation — and lands only when that corpus
exists. Known residual, accepted: rules catch what they name; a bias the rulebook does not
describe passes through.

**Operational note:** these categories change `critic_user` for all three lenses, so
`audition.prompt_hash()` changes and every cached audition verdict is invalidated by design —
operators re-run `ra audition` after upgrading.

## D25 — writers may dispute fix-tasks; adjudication is mechanical-first, identity-blind, and fail-closed toward the finding

**The problem.** Critics were structurally unaccountable. A critic false positive is
indistinguishable from a real defect everywhere downstream: triage counts it, severity floors
escalate it (`fabricated_citation` → blocking), the blind orchestrator sees only counts, and the
next writer's only moves are to comply or to stall the run into stagnation. Sample run
`run-75eb136b9bfb` terminated `needs_human_review` after six hours on exactly this: critics with
stale knowledge flagged real recent events as "future-dated fabrications," and the resulting
fix-tasks (*"correct the date to a factual historical date"*) would have made a compliant writer
**falsify true facts** to satisfy a wrong critic. The whole design audits writers three ways per
round and audits critic *positives* not at all.

**The mechanism** (opt-in, `disputes.enabled: false` by default — with it off, every prompt and
transition is byte-identical to a build without the feature, the D17 pattern):

1. **Elicitation.** After a non-polish revision, one *separate* structured call to the same
   writer collects `WriterDisputes`: per dispute a `task_index`, bounded `grounds`, and optional
   `evidence_url` + `evidence_quote`. Any failure degrades to "no disputes", never fatal.
2. **Adjudication**, in a new `adjudicate` node on the one-way generate → critique edge,
   mechanical-first: a citation-category dispute whose `evidence_url` the report **already
   cites**, whose fetch succeeds, and whose `evidence_quote` appears verbatim (triage
   normalization) in the page text is **upheld with no model judgment**. Everything else goes to
   an **arbiter**: a fresh-context model whose resolved identity is neither the disputing writer
   nor any critic that raised the finding (raiser identities come from audit-side
   `defect_provenance` state and are consumed by eligibility code only — never a prompt). The
   arbiter sees the depersonalized finding, the one paragraph it points at, the question, the
   fenced dispute (labelled an interested party's argument), and the fetched page when the
   evidence URL is one the report cites. It never sees the report body, an identity, the lens,
   or the round, and its tie-break is explicit: **uncertainty resolves in favor of the finding**.
3. **The adjudicated-facts registry**, in checkpointed state, keyed `(category, normalized
   claim_span)` — the triage dedup key minus locus, since paragraphs shift between revisions
   while a verbatim span does not. Each key is ruled once per run. `upheld` records **suppress**
   matching re-raised issues at the top of triage — before `tally`, `clean_records`,
   `to_defects` and the stagnation signature, so counts, clearance and fix-tasks stay consistent
   by construction; every suppression is an audit event. `overruled` records mark the returning
   defect `adjudicated: true` — a bare boolean telling the next writer the task was independently
   reviewed and stands; re-disputes of it are refused. Every other outcome (`no_eligible_arbiter`,
   `arbiter_failed`, `budget_exhausted`, `duplicate`, `invalid`) is a dismissal: **nothing is
   ever suppressed without an explicit upheld verdict.**

**Controller: untouched.** No new `ControllerInput`/`OrchestratorView` field, no new rule. See
the termination note in [convergence.md](./convergence.md): the node adds no cycle, the budget
strictly decreases, suppression only removes counts, and a writer that refuses an overruled task
falls to the existing cycle/stagnation terminals.

**Alternatives rejected.**
- *Extending `structured()` with the tool loop* so one call could revise and dispute: the
  revision path stays byte-identical this way, the dispute call needs no tools (adjudication does
  the fetching), and `response_format`+`tools` together is the combination small rostered models
  fail unpredictably.
- *A neutral arbiter tie-break*: suppression permanently silences a signal for the run, so it
  must be earned; the clear-cut false positives that motivated the feature are exactly the ones
  the mechanical path settles without any model's judgment.
- *Fail-closed startup when some (writer, critic) pair has no possible arbiter*: dismissal
  already fails safe to the status quo ante, so an uncoverable pair costs a privilege, not a
  safety property — it is a startup *warning*, not a `ConfigError`.

**Isolation accounting** (the seven principles): the two honest tensions are principle 1 — the
dispute `grounds` and the finding's `rationale` are reasoning prose entering the arbiter, which
an appeal cannot avoid; bounded because the arbiter is a **terminal consumer** whose only output
is a closed two-field schema, whose `reason` goes to the audit store only, and whose sole
writer-facing residue is one boolean — and principle 5 — adjudication is the system's only
agonistic structure, so it is **one-shot by construction**: no rebuttal, no iteration, a
default-to-the-finding tie-break, and a once-per-key registry that forbids re-litigation.
Principles 2, 3, 4, 6, 7 hold outright (blind parties, no identities in any prompt, one scoped
question, fresh contexts, arbiter ≠ disputer ≠ raiser at resolved identity). One edge accepted:
a disputed span may survive from the disputing writer's own draft two ticks back — the writer is
a party, not a judge, so its stake is expected.

**Known residuals, accepted and recorded:** a later *genuine* defect matching an upheld key is
suppressed for the rest of the run (logged, per-run scope); a hostile *cited* page could carry
text that mechanically upholds a false dispute (bounded by the already-cited requirement, the
once-per-key rule, and the audit trail); with `verify_sources` off there is no mechanical path
and every citation dispute rides on the arbiter; the dispute config deliberately does not join
the resume fingerprint, so toggling it mid-run changes only whether the *privilege* exists going
forward.

## D26 — question refinement is offered at the edge, ambient and never blocking

**The problem.** The pipeline already knows questions arrive loaded: `unexamined_presupposition`
(D24, completeness lens, major floor) exists precisely to catch a writer who accepts a contested
framing as settled. But that machinery fires only after a run is already underway, and the
production run history shows what waiting until then costs. Six runs motivated this decision, and
they fall into four shapes. Two posed a **false either/or** — a political "does X back A or
support B?" and a values question of the "is it better to be honest or nice?" kind — and both
spent their conclusions rejecting the frame rather than answering: 7 rounds to
`needs_human_review`, and `exhausted_unresolved`. One carried an **unverified premise** ("why is
it illegal to do X in Y?") and let it stand uncited while the asker's real, buried question — the
lawful alternative — went unaddressed; 8 rounds, `exhausted_unresolved`. Two asked for a **"net
positive or negative?" scalar verdict** over an unscoped population, outcome set, and timeframe,
which is unanswerable as asked. One asked a **settled verification question** when the report's
real energy went to the adjacent and more interesting question of why the belief persists.

The questions themselves are paraphrased here rather than quoted, and the run IDs left out: they
are a private operator's own queries, and this repository is public. In every case the category
was already nameable — `unexamined_presupposition` (D24) would tag some of these on sight — but
the finding lands 10–25 minutes and several critique rounds after the one party who could cheaply
reframe the question, the asker, has already walked away from the keyboard. The
fix that costs nothing is upstream: catch the same framing before the run starts, while the asker
is still there to accept, ignore, or edit it.

**The mechanism** (opt-in, `refine.enabled: false` by default):

1. **A closed enum of six transforms**, not free-form rewriting: split-the-either/or,
   check-the-premise-first, name-the-outcome-you-care-about, surface-the-real-goal,
   ask-what's-answerable, and ask-the-question-behind-the-question. Every transform preserves the
   user's subject; the model may change *how* the question is posed, never *what* it is about.
   The sixth — the only transform that lets the model infer an unstated concern rather than
   rephrase what is already on the page — ships **disabled** and is enabled only after a
   paired-fixture audition (mirror questions posed from opposing framings must yield mirror
   suggestions) passes, the same deferred-audition pattern D24 uses for its own cross-critic
   bias-correlation check.
2. **Edge-only placement.** Refinement lives entirely in `web/refine.py`, never inside the graph.
   `_intake` (RA-018) is unchanged; the graph still receives exactly one question and never knows
   refinement existed. This follows the seed-ingestion precedent (PR #25): edge-side
   transformation that is audited but never routes. Keeping it out of `_intake` also keeps the
   resume fingerprint honest — the question the graph fingerprints never depends on a model call.
3. **Server-side offer records are the provenance authority.** Client-submitted
   `refine_offer_id`/`refine_selected` are *claims*, never evidence. They are verified only when
   the offer exists server-side, the index is valid, and the submitted question exactly equals
   that suggestion's stored text. A forged or stale claim degrades to an `unverified` mark; it
   can never fabricate an audit trail, because the record it would have to fake was never written
   from client input in the first place.
4. **Two layers of guarantee.** Enforced: schema validation plus deterministic per-entry checks
   (transform in the enabled set, length caps, ends with `?`, no control characters, no
   duplicates, count bound) — any failure degrades silently to zero suggestions. Prompt policy:
   no meta-commentary, no steering, subject preservation, silence-as-default, one transform per
   suggestion — fixture-tested, never assumed, not enforced.
5. **Retention follows content, not code path.** Full refinement content — question at offer,
   suggestions offered, chosen text — is written to `runs/<id>/refinements/refinement.json`,
   folded into `CONTENT_DIRS` so the existing directory-level content purge removes it alongside
   reports and critiques. `events.jsonl`, which survives purges, gets only hashes and enum
   fields: `{offer_id, transform, selected_index, question_sha256, original_sha256, provenance}`.
   `question.txt` continues to hold exactly the question that ran, per the resume-fingerprint
   rule.

**Alternatives rejected.**
- *An interstitial confirmation step after "Start run"*: makes every user pay a click to benefit
  a minority of loaded questions, and reads as a correction gate ("are you sure?") rather than
  the felt experience of being understood.
- *Refinement inside the graph, at `_intake`*: would make the question the graph fingerprints
  depend on a model call, breaking resume, and would put the reframing decision in the pipeline's
  hands rather than the asker's — the opposite of the intent.
- *Trusting client-submitted provenance*: a `refine_offer_id`/`refine_selected` pair from the
  client is exactly the kind of untrusted input RA-010 already treats every model-adjacent field
  as; recording it as fact would let a forged or replayed claim write a false audit trail.
- *A distinct alias name as an isolation claim*: a different alias name alone proves nothing
  about backend isolation, and can even add contention rather than remove it, via model swapping
  onto the same underlying resource pool.

**Isolation accounting** (the seven principles): refinement is not part of the alternating game —
no writer, no critic, no orchestrator role is added or touched — so principles 2–7 hold by
absence of contact: no new social context, no authorship question, no new lens, no new step
inside the alternating loop, no new context accumulation, no critic-writer coupling. Principle 1
is the one with an honest tension to name: refinement puts a model's words in front of the human
*before* a run exists at all, a channel the pipeline otherwise does not have — there is no
analogous point today where an artifact-first handoff rule even applies, since nothing has been
produced yet. It is bounded the same way every other model-facing channel in this system is
bounded: the transform enum forbids free-form rewriting, the subject is preserved (guardrail 6),
the original wording is always one tap away and always wins ties (never auto-replaced, never
required), and every offered suggestion is written to `refinements/refinement.json`, auditable
per run exactly like a report or critique.

**Known residuals, accepted and recorded:** refinement shares the LiteLLM proxy with runs, so the
honest guarantee is a **small fixed ceiling on live client calls** (`refine.concurrency`), shed
rather than queued when saturated — not zero contention with run traffic, which is a deployment
property (an alias routed to a dedicated backend/resource pool), not something an alias name or
client-side control can provide on its own. A timed-out call's orphan-linger window
(`orphan_linger_seconds`) is best-effort damping on orphan accumulation, not a guarantee — a
stalled backend can outlive any window, since the design does not assume verified
disconnect-cancellation from the underlying proxy/backend. The suggester could itself introduce
spin — a taste for one phrasing over another — mitigated by the closed transform enum, the
prompt-policy guardrails, the highest-risk transform (`question_behind_the_question`) shipping
disabled, and per-run auditability via `refinement.json`, but not eliminated as a possibility. An
expired or evicted offer record downgrades an honest, unforged selection to `unverified`
provenance — indistinguishable, downstream, from a forged one; the cost is a slightly less
informative audit trail, never a false one. Enabling refinement also makes the proxy a **boot**
dependency of the web server rather than only a run dependency — the refine alias joins startup
identity resolution and structured-output probing, so an unreachable proxy stops the UI coming up
at all. That is the intended fail-closed trade (a schema-incapable alias must not first surface on
a user's pause), and it is why the roster baked into the image and the wheel is
`config/roster.default.yaml`, which leaves refinement off so the image still boots with no network
and no credential; `config/roster.yaml`, mounted over it by `compose.yaml`, is where this
deployment opts in.

## D27 — installable on a phone, without letting anything cacheable be wrong

**The problem.** The web interface was a page, not an app: no manifest, no icons, no way to keep
it on a home screen. Making it installable is mostly additive, but two parts of it are not, and
both touch a documented posture rather than just adding a route.

**The CSP had to change**, from

```
default-src 'none'; img-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'
```

to

```
default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; form-action 'self'; base-uri 'none'
```

`img-src 'none'` blocked the entire icon set: browsers enforce `img-src` on favicon and
manifest-icon fetches, so there is no version of this feature that keeps it. The property that
directive was protecting is nevertheless unchanged. It exists so that model-written report text
cannot trigger an outbound GET from the reader's browser on a tailnet — and that ban does not
live in the CSP. It lives one layer earlier, in `web/markdown.py`, which disables the `image`
rule outright and sets `html=False`, so a report *cannot express* an image in the first place.
The CSP was belt to that braces. What `'self'` newly permits is same-origin images the
*application* names in its own template, from an origin the reader's browser is already loaded
from. Off-origin fetches remain impossible. The renderer-level ban is now the load-bearing one,
so it carries its own assertion in `tests/test_web.py`; if images are ever re-enabled there, this
decision has to be revisited in the same change.

`manifest-src 'self'` and `worker-src 'self'` are additions rather than relaxations — both were
blocked by `default-src 'none'`, and `script-src 'unsafe-inline'` does not cover them, because
`'unsafe-inline'` permits inline blocks and not URLs. `script-src` gains `'self'` because Safari
has historically resolved worker scripts through `script-src`/`child-src` rather than
`worker-src`; adding a host-source does not disable `'unsafe-inline'`, which is dropped only in
the presence of a nonce or a hash. The whole policy is now pinned by an exact-match test, so
widening it further fails a test rather than passing quietly.

**A service worker is the first persistent client-side execution surface this project ships** —
code that keeps running after the tab closes, on an interface whose only authentication is a
trusted header (D32). Three
properties bound it:

1. **Its cache is an inclusion allowlist, not an exclusion list.** It precaches the icons, the
   manifest and one static offline page. `cache.put` appears in exactly one branch, reachable
   only for URLs in that fixed list. A run page, the `/runs/<id>/progress` fragment and the
   `/runs/<id>/stream` event log therefore cannot be cached *by construction* — not by a pattern
   that a later URL change could slip past. The stream is additionally never passed to
   `respondWith`, so the worker never sits between the browser and an open `text/event-stream`.
   `/runs/<id>` and `/runs/<id>/progress` also carry `Cache-Control: no-store`, because an
   installed standalone app leans on the HTTP cache and the back-forward cache far harder than a
   tab does and the rule has to hold at both layers. A finished run displayed as still running is
   the one output this interface must not produce.
2. **The cache key is a hash of the asset bytes**, not the package version — which has never been
   bumped and would therefore never invalidate anything. Replacing a placeholder icon changes the
   served `sw.js`, which is what makes every installed client fetch a new worker and drop the old
   cache. That is what makes "swap in your own artwork" a two-step operation rather than a
   support question.
3. **Registration is guarded by `isSecureContext`.** Reached over plain HTTP on a tailnet address
   the guard returns before anything can throw, and the page is byte-identical to a build without
   the feature. Installation is available only behind `tailscale serve`'s HTTPS, which is the
   intended posture anyway.

To withdraw the worker later, ship a `sw.js` whose body is `self.registration.unregister()`. Do
not simply delete the route: a worker already installed on a device outlives the deploy that
removed its source.

**Static files are served by an explicit URL→filename allowlist**, not a `StaticFiles` mount.
The mount would resolve a request string against a directory, which contradicts the rule recorded
at `web/app.py` that no code path in the web layer constructs a `Path` from request data. Here
the request string is only ever a dictionary key and every filesystem path is the static
directory joined with a literal, so traversal attempts are ordinary misses. It is also the only
way to set `Service-Worker-Allowed`, and it avoids depending on `mimetypes` knowing
`.webmanifest` — which a bare `python:3.12-slim` does not, and a manifest served as
`application/octet-stream` is ignored silently.

**Known residual:** the manifest's `background_color` is the light palette's. A manifest has no
media-query mechanism and the OS caches it at install time, so the Android splash frame is light
even in dark mode. Serving the manifest dynamically would fix one frame at the cost of making it
non-static; not worth it.

## D28 — the fixer syncs the branch and resolves conflicts; it merges, it never rebases

**The problem.** Almost every PR in this repository is agent-authored, and no agent goes back to
a PR it already opened. When `main` moves, the branch drifts, and there is nobody in the loop to
resync it — the PR sits until a human notices and rebases by hand. The previous position was that
this was deliberate: `docs/ci-pipeline.md` listed "no agent-driven merge-conflict resolution"
under *Deliberately not built*, on the grounds that an agent choosing at conflict markers produces
exactly the unreviewable change this pipeline exists to catch. That reasoning is not wrong about
the risk; it is wrong about the alternative, which in practice is not a careful human resolution
but an indefinitely stale PR.

**The mechanism.** Before the fixer's agent runs, the host attempts
`git merge --no-commit --no-ff origin/<base>` in the PR workspace. `none` (already current) is a
no-op; `clean` is committed by the host and needs no agent at all; `conflicts` leaves the markers
in the working tree, writes the conflicted paths to a file, and the agent resolves them as
ordinary file edits before it touches any reviewer blocker. The host then seals the merge, or
aborts it.

**Merge, not rebase**, for three reasons that all point the same way. A rebase rewrites the SHAs,
and `reviewed_sha` is the key that dedup, the cycle counter, and every artifact name hang from. It
would break the `input_sha == reviewed_sha` gate, since the rebased tree no longer descends from
the SHA the reviewers read. And it needs a force-push from the one job holding a write-capable
PAT, where a non-forced `git push HEAD:<ref>` is a much smaller thing to get wrong. A merge commit
also lands on the existing merge-from-base inherit path, so a resync costs no review cycle.

**The gate that makes it safe to leave a conflict unresolved.** Both fixer prompts tell the agent
to prefer the base branch's structural change and re-apply the PR's intent on top, and — the part
that matters — to leave a marker in place when the two genuinely cannot be reconciled. The host
checks for unmerged index entries *and* for markers in staged content, because a file staged with
its markers intact has neither an unmerged entry nor a resolution. Either one aborts the merge,
labels the PR `needs-human-review`, and comments the unresolved paths. So the honest failure is
cheap and visible, which is what makes "do not guess" a real instruction rather than a wish.

**What is not defended.** A resolution that is syntactically clean and semantically wrong passes
every gate here — `ruff` sees Python, and nothing reads the merge for meaning. The owner's
confirmed intent (see the correction below) is that fixer output — a conflict resolution included
— reaches main without a further review cycle. The protection is the pre-fix panel plus the
fixer's own gates: schema validation against `fix-result-v1.json`, `ruff` at the version pinned in
main's lockfile, the marker gate (no unmerged index entry, no conflict marker in the staged
content), and the remote-head-equality check. None of those reads a merge for meaning, so the
residual is real and it is accepted, not defended against: a wrong-but-clean conflict resolution,
or any other wrong-but-clean fixer output, can reach main unread.

**Correction, from PR #49, and a second correction on top of it.** The paragraph above originally
said the merge commit "arrives on the inherit path", and treated that as an accepted cost. On PR
#49 it was worse than described: reviewers cleared the pre-fix tree, the judge issued a GO, the
fixer pushed a merge carrying four conflict resolutions and two blocker fixes, gather saw
merge-from-base and skipped all four reviewers, and that GO was re-stamped onto a tree nobody had
read. Auto-merge fired three seconds later; 2105 lines landed on main unreviewed.

PR #65's response (`docs/ci-pipeline.md`, `review-pipeline.yml`'s inherit check) was to have gather
refuse to inherit any commit authored as `AGENT_COMMIT_EMAIL`, on the theory that "the fixed SHA
earns its own cycle with its own reviewers" was a property worth restoring by rule rather than
letting it hold by the accident of fixer commits having one parent. That was an agent's invention,
not a design decision the repo owner had made, and it inverted the intent this design was ported
from: the owner has since confirmed that fixer output — including a fixer-authored merge — was
always meant to reach main on the strength of the pre-fix panel and the fixer's own gates, without
a second cycle reading it. The per-author inherit check has been removed accordingly (see the
fixer's claim on its own post-push SHA in `review-fixer.yml`'s Push step, which is what actually
stops a second panel from running).

What PR #49 got wrong that is still worth fixing on its own merits: the merge-gate status must land
on the SHA the fixer actually produced (`post_fix_sha`), never the pre-fix `reviewed_sha` — a gate
written on a SHA that is no longer the PR's head protects nothing. `review-finalize.yml` now takes
`post_fix_sha` as an explicit input and writes `review/cycle`, `review/verdict`, and the merge gate
on it.

## D29 — servable under a URL base path, without relaxing the same-origin posture

**The problem.** RA was a root-origin app: every URL it emitted was root-absolute
(`/static/*`, `/manifest.webmanifest`, `href="/runs/<id>"`, `action="/runs"`, the brand
`/`, the `/runs/<id>/stream` event source) and the service worker registered at `/sw.js`
with scope `/`. A reverse proxy that relocates the app under a stripped prefix — the
Cloudflare Access shape, `location /app/ { proxy_pass http://ra:8080/; }`, where the bare
`/` has to be a real public page and the auth-gated app lives deeper — therefore could not
hold the app under that prefix. Every link and the live stream escaped back to the origin
root, past the Access policy scoped to `/app`.

**The mechanism.** One env, `RA_ROOT_PATH`, normalized once at startup by
`normalize_base_path` to `''` or `'/seg[/seg…]'` (no trailing slash), and joined to every
emitted URL as `base + "/…"`. **The empty string is the join identity** — `"" + "/runs"` is
`/runs` — so an unset env leaves every byte of every page identical to the root-origin
build, which is what lets the whole existing web test suite stand unchanged and pins the
"no prefix" case as a real assertion rather than an accident. The prefix is prepended in
exactly the places a browser resolves against the origin: the server-rendered links and
form actions, the `303` `Location` after a submit or resume, the manifest's `id`,
`start_url`, `scope` and icon `src`s, the worker's precache list, its `OFFLINE` fallback and
its registration scope, the `Service-Worker-Allowed` header, and — when refinement is
enabled (D26) — the `fetch()` the inline refinement script issues to `/refine`. That last
one is a browser-origin URL like the rest and carries the prefix for the same reason; it was
missed when D26 and D29 landed in separate PRs and is corrected in PR #66.

**A stripping proxy, so the routes do not move.** The proxy removes `/app/` before the
request arrives, so the app still serves at `/runs`, `/sw.js`, `/manifest.webmanifest`. The
base path shapes only what the app *emits*, never what its router *matches*. This is the
ASGI `root_path` convention, but `FastAPI(root_path=…)` is deliberately **not** set: nothing
here reads `scope["root_path"]`, routing matches the already-stripped path, and URL
generation is explicit, so setting it would add a second, silent mechanism that could
disagree with the explicit one.

**Why an env and not `X-Forwarded-Prefix`.** The manifest and the service worker are
resolved to bytes once at startup (D27: "read once, at startup"), with the worker's cache
version hashed over the precached URLs. Reading the prefix from a per-request header would
force those to be rebuilt per request, or cached per distinct header value — turning a
static, hashed artifact into a request-varying one. A single startup value keeps D27's
"these files do not change while the process runs" true. One process serves one prefix;
that is the residual, and it matches the one-deployment-one-mount reality.

**The CSP does not change, and that is the point.** Every URL the app *fetches from or
submits to* stays same-origin, so `connect-src 'self'` / `form-action 'self'` /
`base-uri 'none'` are exactly as D27 pinned them, and that test stays green. (There is a
single off-origin URL the app emits — the static "how this works" navigation link to the
published docs site, added later. It is an anchor `href`, not a subresource fetch or a form
submit, so no CSP directive here governs it; it carries `rel="noreferrer"` so following it
from a per-run page hands no run id to that host. The same-origin guarantee this section
makes is therefore about the URLs the browser *resolves against the origin* — links back
into the app, redirects, streams, the manifest and the worker — not this one outbound
navigation link.) `base-uri 'none'` also forecloses the obvious shortcut — a
single `<base href="/app/">` tag — so each URL is prefixed individually instead. The prefix
is the application naming its own same-origin paths, which is what `'self'` already permits;
it opens nothing.

**What D27's three service-worker properties cost.** All three hold under a prefix. The
cache is still an inclusion allowlist: the precache list is the same fixed set of icons,
manifest and offline page, now carrying the prefix, and still contains no run URL — the
`no /runs in the worker` assertion is unchanged. The cache key is still a hash of the asset
bytes, now with the prefixed URL as each entry's name, so two deployments at different
prefixes key their caches distinctly, which is correct. Registration is still
`isSecureContext`-guarded. `Service-Worker-Allowed` becomes `/app/` because a worker served
(as the browser sees it) from `/app/sw.js` must be allowed to claim `/app/`.

**Invariants.** None of the pipeline invariants are in reach: this is URL generation in the
web layer, which is a window onto the audit trail and touches no model context, no
`OrchestratorView`, no author-exclusion, no controller rule. The web-layer posture this
does touch is D27's, and it is generalized, not relaxed: "served from the root so its scope
is the whole origin" becomes "served from the mount point so its scope is the app," with the
root case as `base = ''`.

## D30 — a report leaves the system with its verdict attached, or it does not leave

**The problem.** There was no export. `final.md` and `GET /runs/<id>/report.md` served the report
alone, and the deployment posture (tailnet-only, and — until D32 — unauthenticated) means
sharing a result is
handing over a *file* — the recipient has no run page, no badge, no event log. As prose, an
`accepted` report and a `needs_human_review` report shipped with blocking defects outstanding are
indistinguishable. An export that carried only the text would make the system's one distinction
invisible at exactly the moment it travels furthest, and would do it in the format most likely to
be forwarded on.

**The decision.** Every export is *report + review record*, and the review record is mechanically
derived from `final.json` — terminal status and what it means, the sourcing label, round count and
which round shipped, run id and artifact hash, the reviewers whose clean records key to **that**
hash, outstanding defects, warnings. Three surfaces, one renderer (`export.py`):

| surface | what it is for |
|---|---|
| `GET /runs/<id>/export.md`, `ra export <id>` | paste into a document or a message; the copy button on the report page copies the same text |
| `GET /runs/<id>/export.html`, `ra export <id> -f html` | one self-contained file for someone who cannot reach the tailnet |
| the print stylesheet | `Save as PDF` from a browser, which is where a shared report usually ends up |

`report.md` is unchanged and stays the raw shipped artifact, so anything that hashes or diffs a
report is unaffected by the record being added elsewhere.

**Why these three and not a share link.** A hosted link is the obvious answer and the wrong one
here: it needs public exposure and an account for the recipient, which is well past the trusted-header
identity D32 gives a handful of invited people, and past the posture D22 and the README take on.
Files need neither. PDF is generated by the browser rather
than by a server-side engine — the alternative costs a large dependency to reproduce a rendering
path every reader already has, and the print stylesheet is the same stylesheet as the screen, so
the printed page cannot drift from the page it was printed from.

**Why the reviewer list is filtered by artifact hash.** A `CleanRecord` attests to one artifact
(RC-001/RC-002). Earlier drafts collect their own, and listing those in an export would credit a
critic with clearing text it never read — an overstatement of review coverage in the one artifact
that outlives the run directory. With **no** hash to key against, nobody is credited at all:
crediting everyone would be that same overstatement, arriving exactly when the record is least
trustworthy.

**Three states, not two: absent, unreadable, known.** A missing `final.json` means the controller
never reached a verdict. An *unreadable* one means a verdict may exist and cannot be recovered —
a different fact, and reading it as the first would make an export state `aborted`, a terminal
status no rule produced (the failure D12/RA-012 keeps `abandoned` out of `final.json` for). So
`store.load_final` raises `CorruptRun` rather than returning `None`, `Registry.final_strict` asks
that question where `Registry.final` stays lenient for the pages that already depend on it, and
the export routes **refuse** with 409 rather than shipping a file whose verdict line is invented.
The report page renders instead of refusing — but it is what gets printed to PDF, so it shows the
verdict as `unreadable record` and claims no defects.

**What is not sanitised, and why that is a documented limit rather than a bug.** Defect
descriptions, warnings, the reviewer identities and the sourcing label are model-authored or
proxy-derived and reach the export with newline flattening only — enough that none of them can
*start a markdown block* and forge a second review record, not enough to stop inline emphasis or a
link rendering inside the record with the record's apparent authority. Full per-field escaping
would not close that gap: the report body is deliberately rendered as markdown, so a writer can
already put a convincing fake record in the body itself. The defence against a forged record is
not escaping — it is the artifact hash and run id, which tie a document back to a run directory
holding the real one.

**Consequences and residue.** The copy control puts raw markdown in an off-screen `<textarea>`
rather than a JS string, because report text is model-written and `execCommand('copy')` — the only
clipboard path available on plain http, which is not a secure context — must select a rendered
element. The exported HTML fetches nothing (no font, no stylesheet, no image), which preserves the
property `web/markdown.py` disables images for: opening a report never emits a request on the
reader's behalf, and an exported file travels to people who have no idea what it is. `export.py`
imports nothing from `web/` at module scope so `ra export -f md` works on a core install; the HTML
path borrows the renderer and stylesheet at call time and is the only part needing the `web` extra.
Defect *prose* in the record is critic-authored and carries no provenance — unchanged from what
the run page already showed, but it now leaves the host. A `purge --content-only` removes
`final.md`, so exporting is the thing that outlives retention; the CLI says so rather than
reporting a corrupt run.

## D31 — decision numbers are checked for collision at the gate, not renamed after merge

**The problem.** A decision number (`## D<n>`) is allocated by whoever writes the PR, against
the highest number on main at authoring time. The number is not just prose: it appears in
`config/`, `src/`, `tests/` and several docs, so it is effectively a shared identifier
allocated without a lock. Two PRs open at once each pick the same next-free number and collide
when both merge; worse, when a subagent notices the clash and *independently* renumbers, both
land on the same replacement. This happened three times, most visibly with #54 and #56 both
claiming D30 (issue #71). Every collision costs a repo-wide rename.

**The decision.** Keep authoring-time allocation — it is simple, and the number wants to be
chosen while the decision is being written, not minted by machinery at merge — but refuse the
collision at the gate. `scripts/validate-decision-numbers.sh` fails when any `## D<n>` is
defined twice in `decisions.md`, and runs as a required `Decision Numbers` job in
`pr-validation.yml`. The alternative fix idea — allocate the number at merge time — was
rejected: it would have to rewrite the number across `config/`, `src/`, `tests/` and docs in a
merge-time job with write access, which is exactly the kind of branch-writing, credentialed
step the PR gate is built to avoid.

**Why a duplicate on the PR is a collision on main.** On a `pull_request` event GitHub checks
out the *merge ref* — the PR already merged into its base branch — so the file the check reads
is the file that will exist on main once the PR lands. A duplicate there is a collision that
would otherwise reach main, caught before it does. Two simultaneously-open PRs that both add
`D<n>` do not collide against each other's unmerged branches; the first to merge advances main,
and the second's merge ref then carries two `D<n>` and fails. That is why the reviewer should
keep branch protection's "require branches to be up to date before merging" on — it forces the
second PR's check to re-run against the advanced main before it can merge.

**Why its own job, and why it stays pure.** The `tests` job is path-filtered to Python, so a
docs-only PR that adds a colliding section would skip it entirely; the collision check is a
separate path-filtered job (`docs/decisions.md` or the script itself) so that case is covered.
The script reads one file and touches nothing else — no git, no network, no token — so it fits
the secret-free posture of the PR gate and is exercised offline by
`tests/test_decision_numbers.py`, which also asserts the shipped log is collision-free.

**Invariants.** None of the pipeline invariants are in reach: author exclusion, the blind
`OrchestratorView`, fail-closed lenses, severity floors and controller termination are all
untouched. This is repository governance in CI — it constrains how a *document* is numbered,
not what enters any model's context.

**A gap in the sequence is legal; a duplicate is not.** The check refuses a number defined
twice and says nothing about numbers left unused, which is the right asymmetry: renumbering
*your own* unmerged PR out of a collision is cheap, while renaming a merged `D<n>` across
`config/`, `src/`, `tests/` and docs is the expensive thing this exists to prevent. So when two
open PRs pick the same number, one simply moves up and leaves a hole until the other lands. A
reader who finds a missing `D<n>` in this file is looking at a number allocated to a PR still
in flight, not at a deleted decision — decisions are never deleted, only superseded in place.

## D32 — the interface has users: a trusted identity header, and runs that belong to someone

Every prior version of this document says there is no authentication and that Tailscale ACLs are
the access control. That was true and deliberate for a single operator. Opening the interface to
friends makes it false in a way that matters: without a user concept, everyone who reaches the
app shares one index onto everyone's questions, seed material and audit trails.

**Decision.** Identity comes from a request header set by whatever fronts the app —
`Cf-Access-Authenticated-User-Email` from Cloudflare Access, or the `Tailscale-User-*` headers
D21 already read — and every route but `/healthz` refuses a request that carries none. Runs
record their submitter in `owner.txt`. The index is owner-scoped.

**The header is trusted, not verified — and that is the accepted risk.** Cloudflare Access also
sends a signed `Cf-Access-Jwt-Assertion` that could be checked against the team's JWKS with an
`aud` claim, which is the real boundary. It is not implemented here. Cloudflare strips and
rewrites `Cf-Access-*` on everything it proxies, so *through the tunnel* the email header is
authoritative; the exposure is that the tailnet path is deliberately kept open, so any tailnet
peer that can reach the port can set the header to any value and read or submit as that person.
At the scale this serves — a handful of invited people, on a tailnet the operator controls —
that is a trade taken knowingly, and revisiting it is the stated condition for exposing the
service more broadly. All of it is confined to `web/identity.py:resolve_identity`, so verifying
the JWT is a change to one function.

**Access is preferred over Tailscale, and every source is normalized identically.** Both
headers are trusted equally; Access is checked first because it is how friends arrive. The
operator reaches the app by both doors, so the same person must resolve to one identity either
way — every source is lower-cased, and a value that is blank, over 320 characters, or carries
control characters is treated as absent rather than truncated into an ownership key its own
submitter could never match. Only `Tailscale-User-Login` is read; `Tailscale-User-Name` was
fine as D21's rate-limit key, where any *stable* string worked, but an ownership key must be
the *same* string the other door produces, and a display name is a different namespace from an
address. What normalization cannot fix is a tailnet whose identity provider reports a different
address than the Access policy lists — that is two people as far as this system can tell, and
the check is to sign in each way and compare the *signed in as* line.

**Enforcement is middleware, not a call per route.** `_reject_cross_site` is invoked by hand at
the top of each mutating handler, and that idiom is right for CSRF — it is a property of two
specific routes. Authentication is a property of the app, and the failure mode of an opt-in
check is a future route that forgets it. The middleware is the only fail-closed shape.

**`/healthz` stays the only exemption, including for D27's app shell.** The manifest, service
worker, offline page and icons are static files that hold nothing private, so exempting them
would have been defensible — and it is still declined, because an exemption list is a thing that
grows and every future entry is argued against a precedent rather than against this decision. The
price is paid in the `<head>` instead: a manifest is the one subresource a browser fetches with
credentials *omitted* by default, even same-origin, so the link carries
`crossorigin="use-credentials"`. Without it the fetch reaches Access with no `CF_Authorization`
cookie and is bounced at the edge — where an app-level exemption could not have helped anyway —
and the only symptom is that the app quietly stops being installable. The container smoke test
asserts both halves: `/` with no header is a 403, and the shell is there once a header is set.

> Superseded in part by **D35**, which serves every `GET` under `/runs/` without an identity. The
> reasoning above is why that is a method-scoped rule with a route-table test rather than a second
> entry in `_UNAUTHENTICATED_PATHS` — which still holds `/healthz` alone. The app shell stays gated
> exactly as argued here.

**`auth.dev_identity` is the single knob, and its unset state is the safe one.** Set (via the
roster or `$RA_DEV_IDENTITY`), it supplies an identity to requests with no header, which is what
local development needs; unset, such a request is refused. A boolean `require_auth` alongside it
would have been two settings that can disagree, and the disagreeing combination fails open.

**Ownership scopes the index; it does not scope reads.** You see your own runs listed. Anyone
signed in who holds a run id can read that run — sharing a link is the intended way to show
someone a report, with export/publish to follow. Resume is the one exception: reading costs
nothing, but resuming spends the owner's tokens for another 10–25 minutes, so it stays with the
person who started it.

> **D35** kept this and dropped the "signed in": holding the id is the whole credential. Resume
> stays owner-only but loses its button, since a page served without an identity cannot tell an
> owner from a stranger.

**A run with no owner is served to nobody.** Runs written before this decision, and CLI runs
started without `ra run --owner`, have no identity to attribute and none can be invented for
them. They are 404 over HTTP — not listed, not readable, not resumable by hand — while remaining
untouched on disk and through the CLI. There is deliberately no backfill: guessing an owner is
how a stranger's run ends up in someone's index. Boot recovery is unaffected, because an
interrupted run is work already owed and whether anyone can currently *see* it has no bearing on
whether it should finish. `owner.txt` sits outside `CONTENT_DIRS` so that a retention sweep
cannot silently retire a run from its owner's index.

**D21's rate limiter is unchanged in mechanism and stronger in effect.** Its key was already the
identity header; the difference is that there is no longer a shared `global` bucket to spill
into, because an unauthenticated request never reaches the queue. The CSRF guard also matters
more than it did: Access sets a `CF_Authorization` cookie, so a cross-site form POST would now
ride an authenticated session, and `Sec-Fetch-Site` is what refuses it.

**Isolation is untouched.** This is entirely upstream of run creation and moves no new data
toward any model context. `owner` deliberately stays out of `_run_fingerprint`: the fingerprint
guards against a run resuming under changed *inputs*, and attributing a run must never cost it
its checkpoint. The `seed.allow_url` rationale changes slightly — authentication narrows *who*
can make the server fetch a URL, but not what the host can reach, so the egress boundary in
[ssrf-egress-isolation.md](./ssrf-egress-isolation.md) remains the prerequisite it was.

Deployment is documented in [authentication.md](./authentication.md).

## D33 — refine prompts are auditioned with fixtures, and scope narrowing is a graded violation

D26 shipped its guardrails in two layers: three enforced mechanically, five as prompt policy
whose adherence was "tested statistically with fixtures" — except no fixtures existed, and the
known-gaps section of [question-refinement.md](./question-refinement.md) said so. The gap
stopped being hypothetical in production: "Is fluoride in tap water a net positive for public
health in the United States?" drew a `name_the_outcome` chip reading "What is the impact of
fluoride in tap water on dental health outcomes for children in the United States?" — the
stated domain (*public health*) silently narrowed to one component (*children's dental
health*). Nothing in the guardrails forbade it: the subject survived, no verdict was embedded,
no valence flipped. And for exactly the audience this question serves — someone whose doubts
about fluoridation are not about teeth — a dental-only reframe reads as steering toward the
sub-question with the most convenient answer.

**Decision, part one: the prompt forbids down-scoping, and the sanctioned move is
enumeration.** A new guardrail ("Preserve the scope") requires every rewrite to cover
everything the original covered; the `name_the_outcome` description now instructs the model to
make a scalar verdict measurable by *enumerating* the stated domain's component outcomes —
never by selecting one. Its trigger was also corrected: the old wording fired only on questions
with "no population, outcome, or timeframe named", which the fluoride question did not match
(it named both a population and a domain), so the transform was firing outside its own stated
trigger. `web.refine.PROMPT_VERSION` is bumped so no cached suggestion outlives the old prompt.

**Decision, part two: the refine surface gets the audition treatment.** Prompt policy that is
never measured is indistinguishable from prompt policy that is ignored — the same argument that
built `audition.py` after run-d5934276fafd. `refine_audition.py` runs a fixture corpus
(`tests/fixtures/refine/`) through the production prompt, schema, and deterministic filter,
grades the surviving suggestions with a **pure, mechanical grader** — never an LLM, for the
reason `audition.py` states: the grader must not depend on the property being measured — and
caches a verdict per (identity, enabled-transform set) that `ra audition-refine` writes and
`ra doctor` reads.

**The scope check is synonym groups, not exact strings.** A suggestion on a scope-carrying
fixture passes by retaining a breadth surface form ("public health") or by hitting at least
`min_groups` of the fixture's enumeration groups — dental, skeletal, neurological — where a
group counts once however many of its stems appear, so enumerating synonyms for one component
is still narrowing. Exact-string grading was rejected as brittle in both directions; an LLM
grader was rejected on principle. The residual brittleness (a legitimate phrasing the groups
miss) is bounded by three things: silence always passes, rates are measured over repetitions
rather than single shots, and anything speculative belongs in `tier: subtle`, which never
gates.

**The gating asymmetry is inverted from the critic audition, deliberately.** For a critic,
silence is the measured failure; for refinement, silence is the designed default (D26), so a
low fire rate only warns while an obvious-tier violation gates. On `tier: obvious` fixtures the
tolerance is zero — the fluoride fixture is obvious precisely because it is the pinned
regression, and a model that narrows even once when silence was freely available is doing the
one thing the guardrails exist to prevent. Violations outside that pinned class do *not* make
the verdict `unfit`: the aggregate non-obvious violation rate (above `warn_violation_rate`)
only marginalizes, matching `judge_refine()`, which reserves `unfit` for obvious-tier
violations, schema failure, and control noise. Noise gates too, but only past the hard bound:
chips manufactured for the well-posed controls above `max_control_suggestion_rate` are `unfit`,
same as a critic that invents defects, while a lighter rate above `warn_control_suggestion_rate`
only warns.

**Enforcement is warn-only, and that is not a gap.** Refinement already degrades to silence on
every failure; its fitness must never gate serving runs, and blocking startup over a
chip-suggester's verdict would invert the feature's own doctrine. Under `audition.enforce` an
`unfit` refine verdict is a loud warning at service start and in `ra doctor` — never a refusal.
Auto-disabling refinement on an `unfit` verdict was rejected: config-behavior coupling where a
stale cache file can silently turn a feature off is the same shape as the inert
`audition.enabled` flag D20 deleted.

**The mirror-pair skeleton exists, and it gates nothing.** The corpus carries one ideologically
mirrored pair (`pair: qbq-01`) for `question_behind_the_question`, skipped under the default
transform set and runnable via `ra audition-refine --transforms`. The harness reports the
pair's fire-rate asymmetry as a diagnostic number — the measurement D24 deferred and D26 made
the condition for enabling that transform — but the enablement decision itself stays a human
one; no threshold on the asymmetry is wired into any verdict.

Residuals: the corpus is small (ten fixtures) and public, so slot rotation is doing real work
against memorization; `require_terms`/group stems are casefolded string containment with a
whole-word rule for short terms, which is deliberately dumb and will need corpus care as it
grows; and the harness measures the refine *model*, not the client-side JS, whose gaps remain
listed in question-refinement.md.

## D34 — the base-branch sync runs even when the panel was guarded off, and stays non-agentic when it does

**The problem.** The base-branch sync D28 built to keep agent-authored PRs mergeable was
unreachable in a family of cases it exists for. `fix` was gated on `record-cycle` succeeding, and
`record-cycle` only writes when at least one reviewer's guard cleared. Every reviewer's guard
requires a completed, successful `PR Validation Required` check on the reviewed SHA. So whenever
that check is absent or red while the base has moved underneath the branch, every guard refuses,
the panel is skipped, `record-cycle` writes nothing, and the one stage that could resync never
runs.

The sharpest form is a PR that already *conflicts* with its base. GitHub cannot compute a merge
ref for it, so it fires no `pull_request` event, so `pr-validation.yml` never runs, so the check
never appears, and the deadlock is self-sustaining:

```
conflicted PR → no merge ref → no pull_request event → no PR Validation
  → every reviewer guard refuses → no reviewers → record-cycle skipped → no fixer
  → the conflict is never resolved
```

`/review` reaches the graph through `issue_comment`, which fires regardless of merge state, but
with the panel guarded off the run did nothing but re-publish a fail-closed NO-GO. PRs #54 and #56
both sat in this state and had to be unstuck by a human merging `main` in by hand.

**Rejected: fixing it at PR Validation.** #67 added a `push` trigger to `pr-validation.yml` — a
push needs no merge ref. But that workflow's concurrency group keys on `head.ref`/`head.sha` with
`cancel-in-progress: true`, so a push and its paired `pull_request` event land in the same group
and one *cancels* the other. Both publish a check named `PR Validation Required`, and a cancelled
required check is not a success, so the PR could show that required check as failed when nothing
had failed. It was reverted in #68. The other options weighed in #68 — separate concurrency groups
(doubles CI on every push), a second check name the guard also accepts (widens what "validated"
means), or relaxing the guard to proceed when validation is *absent* (loosens the gate that keeps
reviewers off unvalidated code) — each pay a cost on the healthy path to fix the stuck one.

**The decision.** Fix it at the fixer's gate instead, which is where the maintainer's analysis in
#68 landed: *let the fix job run without the guard's PR-Validation precondition when the only work
is a base-branch sync — a sync consumes no reviewer findings, so the precondition buys nothing
there.* `gather` now computes `needs_sync` (is the reviewed SHA behind `origin/<base>`, by the same
`merge-base --is-ancestor` test the fixer's sync uses), and `fix` has a second, disjoint way in:

- **normal path** — `record-cycle` succeeded (a reviewer ran) *and* `fix_allowed`. Blockers plus,
  if the base moved, a sync in the same commit. Unchanged.
- **sync-only path** — `record-cycle` was *skipped* (every guard refused) *and* `needs_sync`. The
  fixer is called with `sync_only: true`.

**`sync_only` makes the pass non-agentic, and that is the whole safety argument.** The first draft
of this change simply dropped the `record-cycle` precondition and reasoned that with no reviewer
artifacts the fixer would count zero blockers and "do nothing but the merge". That was wrong, and
the security lens caught it on review: `review-fixer.yml`'s work gate sets `agent=true` when
blockers are non-zero **or the base merge conflicts**. A conflicting merge is exactly the state the
motivating case produces, so the sync-only path would have reached `review-agent-run` — the
write-capable fixer, on the self-hosted runner, with host networking and pipeline credentials —
carrying a tree, a PR body, and conflict contents that are all contributor-controlled and that *no
reviewer has read and PR Validation may never have checked*. On the normal path a cleared reviewer
guard is what licenses handing the agent that material; on this path there is no such clearance.

So the pass is reduced to what needs no judgement at all: a clean host-side merge, committed and
pushed, or nothing. `sync_only` forces `agent=false` unconditionally — not as a consequence of the
blocker count happening to be zero — and a merge that conflicts is *abandoned* (`git merge --abort`,
`merge_state=blocked`, nothing pushed) rather than left as markers for an agent to resolve. The
abort matters twice: it keeps the agent out, and it means no later step can mistake a half-merged
tree for something pushable.

**What this does and does not fix.** It does not rescue a PR that already conflicts with its base;
that still takes a human merge, as #54 and #56 got. Automating it would mean an unreviewed,
unvalidated tree driving a credentialed agent, which is a worse trade than a human doing one merge.
What it does fix is the strictly larger non-conflicting case: any behind-the-base PR whose panel was
guarded off — validation red, the branch moved mid-run, an untrusted author — now gets its sync,
becomes mergeable, earns its `pull_request` event, gets validated, and is reachable by its own panel
like every other D28 sync. A conflicted PR at least now fails visibly, with `merge_state=blocked`
and the conflicting paths in the run log, instead of silently doing nothing.

**The sync-only successor is the one SHA the fixer does not claim.** The second thing review caught
was a contradiction between this decision and D28. Normally the fixer claims `review/pipeline` on
the SHA it pushes so that dedup swallows the `synchronize` event and no second panel re-reads the
fix — licensed by "the pre-fix panel plus the fixer's own gates *are* the review". A sync-only pass
has no pre-fix panel to point at; it runs because every guard refused and nothing was read. Claiming
there would have produced a successor with no verdict, no event left to earn one, and therefore no
route through the merge gate: the deadlock this path exists to break, moved one commit forward. So
`sync_only` suppresses the claim. Every other push still makes it, and the rule is unchanged — the
exception falls out of the same justification.

What this buys is worth stating exactly, since the first draft of this decision overstated it twice.
The successor is a merge-from-base, so where a prior verdict exists in its chain the inherit
short-circuit may re-stamp it rather than open a panel; that is fail-closed (a stale NO-GO, never a
stale GO) and `/review` overrides it. The guarantee is therefore narrower than "it will be
reviewed": the successor is mergeable, validated, and *reachable* by a panel, where before it was
none of the three.

**Why the sync-only path drops `fix_allowed` but keeps `cap_exhausted`.** `fix_allowed` bars a
blocker-fix on the last permitted cycle because that fix would never be reviewed (its cycle is
capped). A sync addresses no blockers, and its pushed SHA becomes reviewable the moment it is mergeable,
so the reason does not apply — and the stuck PRs (#54, #56) were at a cycle where `fix_allowed` was
already false, so honouring it would have left the deadlock intact. `cap_exhausted` is still
honoured on both paths: a genuinely exhausted PR takes the terminal cap-exhausted NO-GO and waits
for a human, rather than being kept alive indefinitely by resyncs.

**Why this does not weaken the loop bound.** `MAX_CYCLES` bounds the *agent fix loop*
(review → fix → push → review). The sync-only path writes no `review/cycle` (that is
`record-cycle`'s job, and it was skipped), so it consumes no cycle — consistent with "a run that
reviewed nothing does not consume a cycle". It cannot advance the fix loop because it runs no agent
and addresses no blockers, and it cannot run away: once merged, the SHA contains the base,
`needs_sync` reads false, and no further sync fires until the base moves again — one sync per base
movement, which is external and legitimate.

An earlier draft of this decision claimed here that "a fixer-authored commit is never inherited", so
the merged SHA would earn its own panel rather than re-stamp a stale verdict. That rule no longer
exists: the inherit short-circuit in `review-pipeline.yml` is purely topological — a merge whose
second parent is already on the base branch, with a prior verdict to copy — and checks no author.
The sentence also contradicted this decision's own residual two paragraphs down, which says the
opposite and is the accurate one. It is removed rather than repaired, because nothing in this
decision needs it: the successor's protection is that it arrives *unclaimed and unstamped*, which is
what makes a panel reachable at all. Whether one opens automatically is the inherit rule's business,
and `/review` is the documented override when it does not.

That property is not free, and review caught it going unbacked. `review-finalize.yml` stamps
`review/cycle` and `review/verdict` on `post_fix_sha` — sound everywhere else, because the fixer's
claim guarantees no other run will ever write them for that SHA. Suppressing the claim breaks that
guarantee, so a sync-only successor would have arrived at its own panel already stamped with this
run's cycle, consuming the cycle this decision says it does not and — at cycle 2 — reaching that
panel already capped. So a sync-only push is not passed as `post_fix_sha` at all: the statuses stay
on the pre-sync SHA, which the mergeable successor supersedes, and the successor is left clean for
the run that will actually read it. The three pieces only work together — suppress the claim, skip
the stamp, leave the cycle unwritten — and each one alone would have reintroduced the deadlock in a
different place.

**Invariants.** No blocker-fixing code lands unreviewed: the sync-only path pushes a clean merge and
nothing else — no blocker fix rides it, so there is no unreviewed *fix* to land. The merge itself is
reviewable, on the same "reachable, not guaranteed" terms as any D28 sync (above); what makes it safe
is that its content is the base branch, already reviewed on its way to main, plus a PR-side delta of
zero. Author
exclusion, the blind orchestrator, fail-closed lenses, severity floors, controller termination, and
the untrusted-text boundary all live in the Python review core and the convergence controller, none
of which this touches — this is CI gating in `review-pipeline.yml` and `review-fixer.yml`. The
untrusted-text boundary is in fact *tightened*: one path that could have fed unvetted conflict
contents to a generator no longer exists. The judge still fails closed on the sync-only cycle's
empty reviewer set (pre-existing behaviour when guards refuse), publishing a NO-GO on the pre-sync
SHA that the mergeable successor supersedes.

## D35 — reading a run is public; holding the id is the credential

D32 made every route but `/healthz` refuse a caller with no identity, which is the right default:
the failure mode of an opt-in check is a new route that forgets it, and seed material, questions
and audit trails are exactly what must not leak. But it also closed the one thing the interface
most wants to do — hand a finished report to someone who is not invited. Under D32 sharing works
only *between signed-in callers* (reads are share-by-id, not owner-scoped); a link sent to anyone
outside the Access policy 403s at the app.

**Decision.** Every `GET` under `/runs/` is served without an identity. Every write stays gated
exactly as D32 left it. Holding the run id *is* the credential for reading that run — which is
what D32 already said, minus the sign-in.

**Why the line is read/write and not one filename.** The first shape of this change made a single
route public, `GET /runs/<id>/export.html`, on the argument that the export is the safest possible
artifact. It is — but it does not do the job. That route is served
`Content-Disposition: attachment`, so the "public link" *downloads a file* instead of rendering a
page; and the URL a person is actually looking at after a run is `/runs/<id>`, which stayed 403.
The result was a share affordance that required copying a *second*, different URL out of a button
— exactly the friction the change existed to remove. A person shares the URL in their address bar.
If that URL does not work for the recipient, nothing else about the design matters.

So the exemption is the read surface, whole: the run page, the report page, `progress`, `stream`,
`report.md`, `export.md`, `export.html`, `audit.json`. "Reads of a run are public, writes are
gated" is a rule a person can hold in their head and apply to a route that does not exist yet,
which a list of blessed paths is not.

**What keeps it narrow is the method, not the path.** Every route that spends tokens or changes
state is a `POST`: `POST /runs` (submit — no trailing slash, so it does not match the prefix),
`/runs/<id>/resume`, `/runs/<id>/again`, `/refine`. All still hit `resolve_identity` and 403
without a header, as do `/` — a per-viewer list, which needs a viewer — and the app-shell assets.
A `POST` to a public *read* path is refused before it reaches routing.

A prefix does mean a future `GET` under `/runs/` is public the day it is written. That is a real
cost, accepted with a guard rather than argued away:
`tests/test_web.py::test_public_run_get_routes_are_the_expected_set` enumerates the route table and
fails when the set changes, so widening the public surface is a deliberate edit with a test to
update, not a side effect of adding a handler.

**An owner-less run stays a 404, anonymously as much as before.** `_require` 404s a run with no
owner (D32) and every route under `/runs/` passes through it, so this shares nothing that was
unshareable: legacy and `ra run`-without-`--owner` runs remain served to nobody. `viewer` may now
be `None` on these handlers, and none of them scope a read by it — the identity is still resolved
when a header happens to be present, because the same pages are reachable through the gated door
too, but `None` is an ordinary value here rather than a refusal.

**The URL in the address bar has to be the shareable one, so the app emits two bases.** The edge
gates `/app/` with Cloudflare Access and leaves `/runs/` open. A run page emitted under
`/app/runs/<id>` is therefore a link only a signed-in person can open, no matter what the
middleware allows. `RA_ROOT_PATH` keeps the gated surface — the index, form actions, the app shell;
`RA_PUBLIC_ROOT_PATH` carries the reader-facing surface — the run page, everything linked from it,
the SSE stream, and the `303` a submission lands on. Setting `RA_PUBLIC_ROOT_PATH=/` in production
puts every run URL at the origin root. Unset, it falls back to `RA_ROOT_PATH`, so a single-door
deployment — dev, the tailnet — emits byte-identical URLs to before (D29 is unchanged; this adds a
second base, it does not alter how either is joined).

**Nothing public names a person.** A shared link reaches strangers, so the owner's address is kept
off every public route. The run page's byline ("submitted by …") is gone: it existed to attribute a
run reached by a link *within* the org, and on a page anyone can open it publishes the sender's
address to whoever received the link. `audit.json` drops its `summary.owner` field for the same
reason — an email address is not evidence about a run. Nothing else under `/runs/` carries an
identity: ownership lives in `owner.txt`, never in the event log.

**Resume becomes "Ask this again".** The resume button was shown only to a run's owner, a
distinction a page with no identity cannot draw, and a resume offered to everyone is an invitation
to a 404. Rather than reintroduce an authenticated twin of the run page to host one button, the
page now offers `POST /runs/<id>/again` on any stopped run: a *new* run of the same question,
owned by whoever clicks, rate-limited as their own submission, leaving the original untouched. It
needs no knowledge of who is reading, because it grants nothing a reader could not get by retyping
the question on the index. An anonymous reader who presses it meets the identity gate, which is the
honest failure. The question and the seed are read off disk from the run id — nothing is taken from
the request — so no client-supplied text reaches a model context and the seed does not have to ride
into the DOM in a hidden field. `POST /runs/<id>/resume` remains as a route for the operator; it
just no longer has a button.

This is a deliberate trade: a run that crashed at minute 18 is now re-run from scratch rather than
resumed from its checkpoint, at full token cost. It is narrow. Automatic recovery already handles
the common interruptions (deploy, restart) with no human involved (`worker.recover`), a run
`abandoned` by a roster change *cannot* be resumed anyway (`ResumeMismatch` is why it is abandoned),
and what remains is a crash that leaves the process alive. Paying tokens in that case is cheaper
than an owner-aware second surface.

**The progress stream is public, and that is a resource question, not a data one.** Nothing on this
path writes: `Registry` has no write method at all, and the only worker call is `active()`, a
lock-guarded dict copy. It spends no tokens — it polls the run's own `events.jsonl` once a second.
What changes is that an open connection is now something a stranger can start, so two bounds were
added with it. `RA_MAX_LIVE_STREAMS` (default 32) caps how many run at once and answers `503` past
that rather than queueing, since a parked caller is an open connection too. And the loop now exits
on `not is_live` alone: it previously also required a `final.json`, which meant a run that stopped
*without* one — a crash, or `abandoned` — polled forever, and those are exactly the states the page
most needs to repaint into.

**The forgery caveat is the same one D32 already carries, no wider.** The tailnet path lets any peer
set the identity header and read or submit as anyone; that is the accepted risk whose fix is the
deferred JWT check below. This decision removes the identity *requirement* for reads that every
signed-in caller could already perform by holding the id, so the set of things an anonymous tailnet
peer can reach does not grow by anything they could not reach by claiming an identity.

**Isolation is untouched.** This is entirely in the web layer, upstream of nothing that enters a
model's context and downstream of every run. Author exclusion, the blind orchestrator
(`OrchestratorView`), fail-closed lenses, severity-floor clamping, controller termination and the
untrusted-text boundary all live in the Python review core and the convergence controller, none of
which this changes. Showing a report to an anonymous human is the same act D32 already sanctioned
for a signed-in one; blindness is about what a *model* may read, and this moves no data toward any
model.

Deployment and the route table are documented in [authentication.md](./authentication.md).

## D36 — a hung author-resume is contained at the container boundary, not by `continue-on-error`

**The problem.** The fixer runs in two modes (docs/ci-pipeline.md): `author-resume`, which resumes
the agent that wrote the PR, and `cold`, a fresh agent. `author-resume` was designed as best-effort —
a resumed session that stalls should fall through to the cold fixer rather than fail the PR — and the
mechanism was `continue-on-error: ${{ ... == 'author-resume' }}` on the "Run the fixer" step, with a
"Fall back to a cold fixer" step gated on `steps.run.outcome != 'success'`. It never worked. Three
consecutive `Review pipeline — entry` runs resumed the same wedged session (`run-30224800178`), each
died at exactly +25m00s on the coreutils `timeout` (exit 124), and each aborted the *job* — the
fallback's `if:` was never reached, so the cold fixer that is the pipeline's only designed second
attempt has **never fired**. The uploaded artifact was 148 bytes: `claude --continue` hung before its
first token. Two mechanical defects underneath (issue #85): an expression-valued `continue-on-error`
on a step that `uses:` a **composite action** does not keep the composite's inner-step failure from
failing the job; and `run-in-container.sh`, under `set -euo pipefail`, died on the `timeout`'s 124
before it could tell a deadline from a crash, so no caller could distinguish the two.

**Why it mattered beyond the red X.** `record-cycle` runs before `fix` and gates it, and `MAX_CYCLES`
is 2, so only cycle 1 ever gets a fixer. A hung resume that fails the job consumes the PR's single fix
attempt and produces nothing; on an agent-authored branch the next push lands at cycle 2 where
`fix_allowed=false`, and the blockers return with no fixer left. Human pushes resetting the cycle had
been masking this.

**The decision.** Containment moves to the boundary that actually fails — `run-in-container.sh` — and
the fallback trigger moves from a step *outcome* to an on-disk *signal*.

- The script captures `timeout`'s own status via `PIPESTATUS[0]` (not `tee`'s) under a scoped
  `set +e`, and adds `--kill-after` so a CLI that ignores SIGTERM is still killed at the deadline
  (surfacing as 137, handled alongside 124). A duration override seam (`AGENT_TIMEOUT`) exists purely
  so the offline test can use a 2-second deadline.
- In **resume mode** the script is best-effort and never fails the job: a timeout writes a
  `fixer-timeout.sentinel` beside the output log and exits 0; a crash or a clean-but-empty run also
  exits 0 (no sentinel — the missing `fixer-result.json` is the signal). In **any non-resume mode**
  (cold fixer, reviewers, resolver, author) there is no fallback, so a timeout or missing result stays
  fatal, exactly as before — the containment is gated on `AGENT_RESUME=1` and touches nothing else.
- The workflow adds a "Did the author-resume fixer produce a fix?" step that reads the sentinel /
  result off disk and sets `ok`. The fallback now fires on `always() && … && ok != 'true'`. `always()`
  is load-bearing: the "second-order trap" is that a condition with no status function has an implicit
  `success()` ANDed onto it, which — now that a contained timeout reports the step as `success` —
  would skip the fallback on exactly the hang it must catch. `continue-on-error` is kept only as
  defense-in-depth for a failure in one of the composite's *other* steps.

**What this does not do.** It does not stop the pipeline re-resuming a session that has already hung
(defect 3 in #85): `validate` still cannot tell a loadable session from one `--continue` wedges on, and
no cheap cross-run memory exists on the ephemeral, plural homelab runners to record "this run-id hung."
That is deliberately out of scope, and its practical harm is now small — with the fallback firing, a
hung resume no longer fails the run or burns the cycle's fix attempt; it costs one bounded resume
attempt before the cold fixer succeeds in the same cycle. Left as an open item below.

**Invariants.** None of the review-core invariants are touched — author exclusion, the blind
orchestrator, fail-closed lenses, severity floors, controller termination, and the untrusted-text
boundary all live in the Python core, and this is CI gating in `run-in-container.sh` and
`review-fixer.yml`. The fixer's own pre-push gates (docs/ci-pipeline.md: artifact validates against
main's schema, `input_sha`/`cycle`/`mode` match, ruff, marker and race gates) are unchanged, so a cold
fix produced by the now-reachable fallback clears exactly the same bar it always had to.

## Open items for a future round

- Whether `misrepresented_source` can be meaningfully checked without fetching the source
  (v1 only checks on-its-face support); a later evidence layer (RA-011) would strengthen this.
- Calibration of `K` (plateau window), the hard cap, and defect-score weights against real runs.
- Verifying `Cf-Access-Jwt-Assertion` against the team's JWKS with an `aud` check, replacing the
  trusted email header (D32). The prerequisite for exposing the service beyond a small invited
  group, or for closing the direct-to-origin forgery path the tailnet posture leaves open.
- Detecting an *unresumable* author session before spending a resume attempt on it (D36, #85
  defect 3). `validate` proves only that a non-empty transcript exists; it cannot tell a session
  `claude --continue` loads from one it wedges on, and there is no cross-run memory on the
  ephemeral runners to record that a given `run-id` has already hung. A durable signal (a marker
  committed to the PR, or a per-`run-id` "hung" note surviving between runs) would let the fixer
  skip straight to cold instead of re-paying the bounded resume timeout each cycle.
