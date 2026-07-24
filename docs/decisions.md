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
| D24 | **Seed reports are converted to markdown at the edge; URL seeds are opt-in and off by default.** `--seed` and the web form accept PDF, `.docx`, HTML and `.txt`; `ingest` converts them before `graph.run` is called, which continues to require markdown. http(s) URL seeds exist behind `seed.allow_url`, default `false` (the D17/D18 posture): a URL seed makes the server fetch a caller-chosen URL and expose the body back through the run's report endpoints — on the unauthenticated web UI that is a read proxy into whatever the host can reach, and the egress boundary that makes it acceptable is a network-layer deployment concern outside this repo (docs/ssrf-egress-isolation.md). Turning it off hides the form field and rejects the parameter. A format that yields no headings is accepted with a warning, not rejected. | Markdown is not a preference here, it is load-bearing: `report.parse` builds the `[S<n>.P<m>]` loci critics must cite from `#` headings, and `extract_source_urls` reads only a markdown `## Sources` section, so an unconverted seed silently costs the evidence lens its fetch-backed checks. Converting at the **edge** rather than inside `_intake` keeps one artifact and one identity — `_run_fingerprint` and `artifact_hash` would otherwise hash different things (a URL vs. its converted text), letting a resume pass the fingerprint check while the checkpoint held different prose. It also keeps network I/O out of the graph, where every other fetch is injected through `Runtime` so tests stay offline. Accepting a heading-less seed reflects what the formats actually carry: PDF has no recoverable heading semantics without font heuristics, and refusing would block the most common real-world case to protect locus precision the source never had. PDF is the only format needing a dependency (`pypdf`, optional extra); `.docx` is a zip of XML and HTML is an `HTMLParser`, both standard library. |

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

The rate limiter is keyed by the Tailscale identity header when the app is fronted so the
header is present, and by a single global bucket otherwise. On the tailnet posture the
header is trustworthy; a caller reaching the app directly could forge it, but such a caller
could equally vary it to defeat any per-identity scheme, and the global fallback still
bounds that case. This is backpressure against bursts, not an auth boundary — the design
already states there is none here (Tailscale ACLs are the access control).

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
moves from "the fixer cannot do much" to "the fixed SHA earns its own review cycle with its
own reviewers" — which was always the real backstop, since the judge grades the reviewed
SHA, not the fixer's output.

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

## Open items for a future round

- Whether `misrepresented_source` can be meaningfully checked without fetching the source
  (v1 only checks on-its-face support); a later evidence layer (RA-011) would strengthen this.
- Calibration of `K` (plateau window), the hard cap, and defect-score weights against real runs.
