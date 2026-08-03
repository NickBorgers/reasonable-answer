# Design decisions & adversarial-review log

## Identifiers: decision slugs, and the old-number mapping

Every decision in this log is named by a **slug derived from its subject** — `D-source-verification`,
`D-decision-slugs` — not by a number from a shared counter. A slug is coined by the PR that writes the
decision and describes what the decision does, so two concurrently-open PRs cannot pick the same
identifier by construction and no PR needs to read another to choose one. Ordering is carried by
**position in this file**, not by the identifier: a new decision is appended as a `## D-<slug> — …`
section immediately before `## Open items for a future round`, the file's tail marker. Because slugs
have no order, a *range* of them is meaningless — cite a group by enumerating it (`D-per-lens-critics`
and `D-critic-only-specialists`), never as a span.

Decisions carry two surface forms: rows in the "Key design decisions" tables (`| D-<slug> | … |`) and
`## D-<slug> — …` prose sections. Either form is a definition, and `scripts/validate-decision-numbers.sh`
refuses a slug defined twice across **both** forms, so one decision cannot be silently defined in a
table and a section at once.

This scheme replaced a numeric one (see D-decision-slugs, which supersedes D-decision-gate). Historical
PR bodies, review comments and past reviewer artifacts cite the old `D<n>` numbers, so the mapping below
keeps them readable. **It is the only place an old numeric identifier is retained on purpose**; a bare
`D<n>` anywhere else in the tree is stale. Four numbers named two different decisions each — the reused
number the old gate could not see, and the plainest evidence the counter had failed — so those four are
disambiguated by subject below and split into two slugs apiece. The 43 old numbers therefore map onto 47
decisions.

| old id | new slug |
|---|---|
| D1 | `D-alternating-refine-game` |
| D2 | `D-structured-handoff` |
| D3 | `D-blind-orchestrator` |
| D4 | `D-observable-categories` |
| D5 | `D-in-artifact-citations` |
| D6 | `D-isolation-boundary` |
| D7 | `D-cross-model-confirmation` |
| D8 | `D-min-ticks-floor` |
| D9 | `D-two-clean-critiques` |
| D10 | `D-severity-floors` |
| D11 | `D-split-view-input` |
| D12 | `D-evidence-bearing-fields` |
| D13 | `D-context-window-unit` |
| D14 | `D-three-model-roster` |
| D15 | `D-per-lens-critics` |
| D16 | `D-critic-only-specialists` |
| D17 | `D-retrieval-opt-in` |
| D18 (open-weight roster) | `D-open-weight-roster` |
| D18 (source verification) | `D-source-verification` |
| D19 | `D-orchestrator-roster-entry` |
| D20 (redeploy durability) | `D-redeploy-survival` |
| D20 (critic audition) | `D-critic-audition` |
| D21 (proxy base_url override) | `D-proxy-base-url` |
| D21 (bounded submission) | `D-bounded-submission` |
| D22 | `D-run-date-grounding` |
| D23 | `D-fixer-grounded-judgment` |
| D24 (seed conversion) | `D-seed-conversion` |
| D24 (social-bias categories) | `D-social-bias` |
| D25 | `D-writer-disputes` |
| D26 | `D-question-refinement` |
| D27 | `D-installable-pwa` |
| D28 | `D-fixer-merges-not-rebases` |
| D29 | `D-base-path` |
| D30 | `D-verdict-attached` |
| D31 | `D-decision-gate` |
| D32 | `D-identity-header` |
| D33 | `D-refine-audition` |
| D34 | `D-unguarded-sync` |
| D35 | `D-id-as-credential` |
| D36 | `D-resume-timeout` |
| D37 | `D-quality-reviewer` |
| D38 | `D-notfound-fabrication` |
| D39 | `D-existence-vs-body` |
| D40 | `D-paid-tier-page` |
| D41 | `D-absence-anchor` |
| D42 | `D-provider-retry` |
| D43 | `D-stop-notification` |

## Key design decisions (from the design dialogue)

| # | Decision | Rationale |
|---|----------|-----------|
| D-alternating-refine-game | **Alternating refine game.** A report is written by one model and critiqued only by models that did not write it; the next report is written by a different writer. *(Roster later generalized to a writer pool + per-lens critic pools by D-three-model-roster, D-per-lens-critics and D-critic-only-specialists.)* | Dissolves the corroboration-vs-specialization conflict; guarantees `critic ≠ producer`; convergence becomes temporal. |
| D-structured-handoff | **Structured defect-list handoff**, not raw critiques. | Keeps principles #1 (artifact-first) and #6 (fresh context) fully intact while still telling the generator what to fix. |
| D-blind-orchestrator | **Blind LLM orchestrator inside a deterministic controller.** | The user wants the AI to add judgment on the signal summary (its main value); the controller guarantees termination the LLM cannot. |
| D-observable-categories | **Observable-category taxonomy** (no intent tags). | A critic can't infer intent from text; `uncited_claim`/`contradicted_claim`/`fabricated_citation` are checkable. |
| D-in-artifact-citations | **Report carries its own citations; no external retrieval in v1.** Uncited material claims are challenged. *(Amended by D-retrieval-opt-in: retrieval is now implemented as an opt-in, off by default. With `search.enabled: false` this decision holds exactly as written.)* | Matches "the argument is sound" via in-artifact sourcing; output labeled *consensus-reviewed*, not fact-checked. |
| D-isolation-boundary | **Structural isolation boundary** for the orchestrator (`OrchestratorView` DTO only; superseded the earlier `SignalReport` name — see D-split-view-input). | Makes blindness real, not a coding convention over shared state. |
| D-cross-model-confirmation | **Cross-model confirmation** before `accepted` (refined by D-two-clean-critiques/D-three-model-roster, then cross-family by D-front-loaded-depth/QP2). | A single clean critique is one model's opinion; strong acceptance now needs clean records from **two distinct non-author model families** on the identical artifact. |
| D-min-ticks-floor | **min_ticks floor.** | "The first tick should never be accepted." |

## Codex adversarial review — round 1 (verdict: CHANGES_REQUESTED, 20 findings)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RA-001 | crit | Blocking issues route to REVISE before the cap check → infinite loop; "guaranteed termination" false | **Fixed.** Controller checks `fatal` → `cap` **before** any revise; ordered stop-decision in [convergence.md](./convergence.md). |
| RA-002 | crit | Artifact-blindness is only a convention over shared state | **Fixed (D-isolation-boundary).** Orchestrator invoked with a SignalReport DTO built outside nodes; noninterference test; redacted telemetry. |
| RA-003 | high | 2-model corroboration = brittle unanimity; 3 = silent majority | **Superseded by D-alternating-refine-game/D-cross-model-confirmation.** No per-defect voting; agreement is temporal + whole-artifact cross-confirmation. |
| RA-004 | high | Orthogonal critics can't corroborate each other | **Superseded by D-alternating-refine-game.** Corroboration no longer required within a tick. |
| RA-005 | high | Lone blocking issue ignored as a nitpick (unsafe) | **Fixed.** Single critic per tick; **any** ≥ major issue forces another tick. Lone blocking is never ignored. |
| RA-006 | high | `dishonest` requires intent inference | **Fixed (D-observable-categories).** Replaced with observable categories. |
| RA-007 | high | No handling of malformed/timeout/partial-critic failure | **Fixed.** Failure table in [architecture.md](./architecture.md); incomplete review never counts as clean. |
| RA-008 | high | Triage semantic dedup ill-defined; LLM triage = unblinded bias | **Fixed.** Triage is mechanical (tally structured findings), no LLM; canonical locus normalization; provenance kept in audit. |
| RA-009 | high | "Content-free" undefined; SignalReport could leak/covert-channel | **Fixed.** Closed schema (bounded enums/ints), metadata allowlist, noninterference test. |
| RA-010 | high | Prompt injection via seed/report/critique text | **Fixed.** Threat model in [isolation.md](./isolation.md): all such text untrusted; structured-output boundaries; validation; adversarial tests. |
| RA-011 | high | No evidence layer; models can agree on a plausible falsehood | **Scoped (D-in-artifact-citations), then addressed (D-retrieval-opt-in + D-source-verification).** In-artifact citations required; uncited claims challenged; output relabeled. Retrieval is no longer deferred: with `search.enabled: true` writers cite only URLs a live search returned, and with `search.verify_sources: true` the evidence lens reads those pages and can falsify `misrepresented_source` against them. Both off by default *in code*, so the D-in-artifact-citations posture remains the default posture for a bare checkout; the shipped `config/roster.yaml` opts into retrieval only — verification stays off until a deployment provides the network-layer egress boundary (D-run-date-grounding). The residual blind spot is narrower but real: verification shows a page exists and is compatible with the claim, not that the page is correct. |
| RA-012 | high | "Finalize" conflates accepted with known-unacceptable | **Fixed.** Four terminal statuses: `accepted` / `exhausted_unresolved` / `needs_human_review` / `aborted`. |
| RA-013 | med | Plateau/oscillation/best-scoring undefined | **Fixed.** Precise definitions in [convergence.md](./convergence.md). |
| RA-014 | med | No round-identity/reducer contract; replay can fake convergence | **Fixed.** Keys `(run_id, round, artifact_hash, models, lens, attempt)`; idempotent reducers; stale-hash rejection. |
| RA-015 | med | Single endpoint / no concurrency, timeout, capability checks | **Fixed.** Ops section: bounded concurrency, per-call timeout/retry, startup structured-output capability check, roster health check. |
| RA-016 | med | Audit trail may hold sensitive data; no retention/access policy | **Fixed.** Data classification, least-privilege perms, retention/deletion, redaction; note LiteLLM proxy logging. |
| RA-017 | med | "Distinct models" ≠ independent (aliases, fallback, same family) | **Fixed.** Enforce distinctness at resolved provider/model/version and model family; no duplicate fallback; roster requirements generalized to per-lens eligibility by D-critic-only-specialists and D-front-loaded-depth (≥2 eligible non-author families per lens for strong acceptance); fail closed. |
| RA-018 | med | Input routing for question/seed combinations undefined | **Fixed.** Intake routing table + validation in [architecture.md](./architecture.md). |
| RA-019 | med | Only one isolation test mentioned | **Fixed.** Test matrix below. |
| RA-020 | low | Orchestrator/triage trust models inconsistent (agent vs pure logic) | **Fixed (D-blind-orchestrator).** Orchestrator = blind LLM inside a deterministic controller; triage = mechanical. |

## Operational requirements (RA-015 / RA-016 / RA-017)

- **Roster (role-structured, superseded by D-per-lens-critics/D-critic-only-specialists):** a **writer pool** plus **per-lens critic
  pools** (each ≥2 eligible non-author model families for strong acceptance; critic-only specialists
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
| Retrieval / web search (D-retrieval-opt-in) | offline-when-off (no `tools` offered, prompt byte-identical to the pre-retrieval path); startup fails closed on a missing credential **and** on a tool-incapable writer; `probe_tool_calling` returns False for a model that accepts `tools` and never calls one, and for a probe that raises; per-**run** query budget (not per-call) enforced under concurrency; budget exhaustion and fetch failure surfaced to the model as text, never as silence; results fenced as untrusted (RA-010); the agentic tool loop terminates — the exhausted round drops `tools` and forces prose — and `Completion.tool_calls` matches the number executed; the query string never reaches a log (RA-016) |
| Source verification (D-source-verification, D-notfound-fabrication) | citation URLs extracted from the `## Sources` section only (a URL mentioned in passing is not fetched); **only the evidence lens** receives page text — logic and completeness never do; each fetch carries a closed `SourceOutcome` (FULL_TEXT / NOT_FOUND / BLOCKED / UNREADABLE / EMPTY / ERROR) rendered to the critic as its own label (`NOT FOUND` / `BLOCKED` / `COULD NOT READ` …) rather than one flat "could not fetch", and the audit tally counts the enum, never the free-text `error` (RA-016); a cited URL that returns a definitive not-found (HTTP 404/410) yields a mechanical `fabricated_citation` at its `blocking` floor, independent of any critic (D-notfound-fabrication); every other failed fetch (403 and the other blocked codes, connection error/timeout, unreadable content type, empty body) yields **no** defect and keeps the on-its-face bar, never read as evidence of fabrication, each class pinned by a test; a twelve-of-twelve-404 fetch leaves the evidence lens **not** clean while a twelve-of-twelve-403 stays clean; truncation disclosed; a cited **PDF is read rather than refused** when `sources.enabled` **and** `sources.pdf.enabled` are both on — both switches required so enabling one tier never enables another (`test_a_cited_pdf_is_read_rather_than_refused`, `test_both_switches_are_required_to_read_pdfs`), fail-closed with a startup error when `pypdf` is absent (`test_pdf_reading_without_pypdf_refuses_to_start`), a truncated PDF refused not parsed (`test_a_truncated_pdf_is_refused_not_parsed`), a scanned/no-text-layer PDF reported `UNREADABLE` distinctly from `EMPTY` (`test_a_scanned_pdf_says_so_rather_than_looking_empty`), and the larger 25 MB PDF byte cap applying to PDFs only, never buying back HTML's 400 KB cap (`test_the_larger_pdf_cap_applies_only_to_pdfs`); with that tier off a PDF is still reported honestly as an unreadable content type; pages fetched once per run and cached across rounds; bounded by timeout, byte cap, redirect cap and http(s)-only; verification off ⇒ the evidence prompt is byte-identical to the D-retrieval-opt-in path |
| Seed ingest / format conversion (D-seed-conversion) | every converter meets the output contract (blank-line-separated blocks, headings alone on their line) so `report.parse` loci survive; PDF/`.docx`/HTML/`.txt` conversion each covered offline (urllib's opener stubbed — no network, no keys); one bounded http(s)-only egress point reused from `fetch.http_get`; `file:`/`ftp:`/`data:` schemes refused before any opener exists; the `.docx` zip-bomb guard (`seed.docx_max_uncompressed_bytes`) trips **before** decompression; truncation is fatal for binary formats and a warning for text; a heading-less format yields one section plus a warning, never a failure; URL seeds refused when `seed.allow_url` is off (the default) — the form field disappears and the parameter 400s; the web layer never constructs a `Path` from request data; converted markdown is byte-identical between what is hashed, stored and critiqued (resume fingerprint) |
| End-to-end | labeled fixtures where a known-flawed seed must reach `accepted` with the flaw fixed |

Real-proxy integration tests are **marker-gated**: they carry the `live` pytest marker declared in
`pyproject.toml`, and CI deselects them with `-m "not live"`. The proxy endpoint comes from
`proxy.base_url` in the roster — or, when set, the environment variable named by
`proxy.base_url_env` (`RA_PROXY_BASE_URL` by default; see D-proxy-base-url) — and its key from the environment
variable named by `proxy.api_key_env` (`LITELLM_API_KEY` by default). The full suite passes with no
keys and no network, honoring "clone → run tests."

## Additional decisions (from Codex round 2)

| # | Decision | Rationale |
|---|----------|-----------|
| D-two-clean-critiques | **Acceptance = two clean critiques by two distinct non-author witnesses.** *(Generalized to **per-lens** by D-per-lens-critics and to **cross-family** witnesses by D-front-loaded-depth/QP2; the 2-model consecutive-clean fallback was later removed.)* | A two-model "confirm the same artifact" would be the author reviewing its own draft (RB-001). The current family requirement preserves that exclusion and rejects correlated same-family checkpoints as a second witness. |
| D-severity-floors | **Mechanical, category-specific severity floors; fail-closed on invalid output.** Triage clamps severity up to the floor; unknown/invalid fields fail the whole lens. | Stops a critic gaming severity (RB-006) or an adversarial/invalid critique collapsing into a fake-clean empty result (RB-007). |
| D-split-view-input | **Split `OrchestratorView` (content-free, LLM-facing) from `ControllerInput` (identifiers, deterministic).** | The blind LLM must not see hashes/ids (correlation handles); the deterministic controller may. Makes noninterference testable (RB-004, RB-008). |
| D-evidence-bearing-fields | **Evidence-bearing defect fields** (`claim_span`, `related_span`, `citation_id`, `expected_support`, bounded `rationale`). | `{locus,category,severity,instruction}` can't convey which propositions contradict etc., so a blocking defect could survive (RB-005). Fields are bounded/untrusted/validated. |

## Codex adversarial review — round 2 (verdict: CHANGES_REQUESTED; 6 resolved / 14 partial / 0 unresolved + 10 new)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RB-001 | crit | Cross-model confirmation on a 2-model roster = the author reviewing its own draft | **Fixed (D-two-clean-critiques; generalized per-lens by D-per-lens-critics).** Acceptance requires clean reviews by distinct non-author models; the 2-model consecutive-clean idea was later removed in favor of per-lens `roster_limited` weak acceptance. |
| RB-002 | crit | At cap, a first clean critique could be labeled `accepted` without confirmation | **Fixed.** The cap never accepts a single clean review; clean-but-unconfirmed at cap → `exhausted_unresolved`, and per-lens top-up stays reachable at the cap (see RG-001). |
| RB-003 | high | Confirmation bypassed the critique→triage→controller path (undefined failure/budget/identity) | **Fixed.** Confirmation is an ordinary critique attempt, triaged and returned through the controller. |
| RB-004 | high | Controller's declared inputs insufficient for its deterministic decisions | **Fixed (D-split-view-input).** `ControllerInput` schema + exhaustive ordered decision table; LLM authority scoped to minor-polish. |
| RB-005 | high | `{locus,category,severity,instruction}` too lossy to fix blocking defects | **Fixed (D-evidence-bearing-fields).** Evidence-bearing bounded fields added. |
| RB-006 | high | Critic-selected severity lets a critic downgrade a material defect to `minor` | **Fixed (D-severity-floors).** Mechanical per-category floors; critic may only escalate. |
| RB-007 | high | "Unknown categories dropped" (isolation) vs "failed lens" (architecture) — dropping can fake-clean | **Fixed (D-severity-floors).** Unified fail-closed: unknown/invalid ⇒ whole lens fails; loci are bounded structural refs. |
| RB-008 | med | `SignalReport` carried hash/ids (correlation handle); noninterference test impossible as written | **Fixed (D-split-view-input).** `OrchestratorView` excludes ids/hash; noninterference defined over it. |
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
| D-context-window-unit | **The isolation unit is the context window, not the model.** Fresh, blind contexts defeat the *primary* bias (social/context drift) regardless of model; model diversity is a *secondary* layer that decorrelates blind spots. | The dominant threat (sycophancy, contextual drag, in-session self-review) is caused by *shared context*, not model identity — so principle #7 is fundamentally "not the same context." (User insight.) |
| D-three-model-roster | **Default roster = ≥3 distinct models.** *(Strong clearance was later tightened by D-front-loaded-depth/QP2 to two distinct non-author model families.)* | Two models cannot give the final artifact two non-author reviews when one authored it (RC-001); the later family rule also prevents same-family checkpoints from supplying the second witness. |

## Additional decisions (post-review design extension)

| # | Decision | Rationale |
|---|----------|-----------|
| D-per-lens-critics | **Per-lens critic models + per-lens acceptance.** Each lens gets its own critic pool, headed by the model best matched to that lens (evidence → the lowest-hallucination model, since `fabricated_citation`/`misrepresented_source` are attribution-fidelity failures); `CleanRecord` is keyed per-lens; strong `accepted` requires **each lens** strongly-cleared (≥2 distinct non-author models). | Matches the best model to each dimension and raises within-tick blind-spot decorrelation. A lens with only one eligible model honestly degrades that dimension to `converged_unconfirmed`, naming the under-reviewed lens. **Correction:** the evidence lens was originally headed by Llama 4 Scout for "huge context to scan citations". That rationale never held — `max_report_chars: 60_000` caps critic input at ~15k tokens, so context length was never the binding constraint. The lens wants attribution *fidelity*, not capacity. |
| D-critic-only-specialists | **Role-structured roster with critic-only specialists.** A writer pool plus per-lens critic pools; a model may be pinned as a lens reviewer that never authors. | Cleanly satisfies author-exclusion (author of Rₙ never critiques Rₙ on any lens). Its sharpest use is pinning the roster's *strongest* model as critic-only: as a writer it would be barred from reviewing its own drafts, losing the best reviewer on half of all rounds. `glm-5.2` is critic-only on all three lenses for exactly this reason. |
| D-retrieval-opt-in | **External retrieval, opt-in and off by default.** Amends D-in-artifact-citations and resolves RA-011's deferral. With `search.enabled: true` writers get a `web_search` tool (Brave API) and cite only URLs a search returned; startup fails closed on a missing credential **or** on a writer that cannot emit tool calls. With `search.enabled: false` (the default) D-in-artifact-citations holds unchanged and the suite stays offline. | RA-011's blind spot was that a diverse roster can agree on a plausible falsehood, and in-artifact sourcing cannot catch an invented citation. Retrieval makes citations *real*; it is opt-in because a credential is required and the default posture must remain "clone → run tests" with no keys. Failing closed on a tool-incapable writer is load-bearing: such a writer still emits a `## Sources` section, and nothing downstream distinguishes a remembered citation from a retrieved one. |
| D-open-weight-roster | **The roster is open-weight only, bounded by what the target box can load.** Every alias resolves to downloadable weights, and none exceeds ~450GB at 4-bit — the single-model ceiling on a shared ~768GB machine, with swapping between roles. | Two independent reasons. (1) `docs/DESIGN.md` commits to a local runtime; a roster containing models that cannot load there is not a dry run of it, it is a surprise deferred. (2) No role is locked to a vendor. Consequences: `deepseek-v4-pro` (~800GB) and `kimi-k3` (~1.4TB) are excluded by arithmetic, not preference; `qwen3.7-max` is excluded because Alibaba closed the 3.7 weights (the open Qwen line stops at 3.6); `nemotron-3-ultra` fits but was excluded by choice, which costs the evidence lens the only open model with an independent long-context score (RULER 0.947). Both writers report tool-call support, so D-retrieval-opt-in's fail-closed check passes if search is ever enabled. |
| D-orchestrator-roster-entry | **The orchestrator has its own roster entry**, optional, defaulting to `writers[0]`. It runs on the free local model. | It was hardcoded to `writers[0]`, so reordering the writer pool silently changed who refereed polish decisions — a coupling with no reason behind it. Its job is bounded ints in, one boolean out (`OrchestratorView`), so it needs neither reach nor a writer's capability, and D-retrieval-opt-in's tool-call requirement does not apply to it. Its blast radius is one skipped polish pass: `_orchestrate_call` swallows call and schema errors and returns `False`, and rule 9 is cap-gated, so the LLM can only ever *enable* polish. The alias joins `all_aliases` so startup resolves and probes it — without that, an identity mismatch would disable rule 9 permanently and silently. |
| D-redeploy-survival | **The checkpointer is the durability guarantee; the SIGTERM grace period is only an optimisation.** A redeploy stops the graph at the next *node* boundary, never mid-node and never "after the round". Boot re-enqueues whatever was owed. A run that makes no progress across N **consecutive** auto-resumes becomes `abandoned` — a registry-inferred lifecycle state that is terminal for the UI but is deliberately **never** written to `final.json`. | A run is 10–25 minutes, so no grace period can wait for one to finish; designing around that would make correctness depend on a number the platform owns and can change without telling us. Since LangGraph persists at every node boundary, a SIGKILL already costs at most the node in flight — so the grace window buys the chance to *land* that node rather than re-pay for it, and shortening it wastes work without risking corruption. The cap counts consecutive rather than total attempts so a restart storm cannot spend the budget on runs it never actually executed; any progress event resets it. `abandoned` avoids `final.json` because that file means the controller reached a verdict (D-evidence-bearing-fields/RA-012), and giving up is not a verdict — inventing one would let the audit trail claim a terminal status no rule ever fired. A human can always resume past it, so the cap bounds automation, not the run. |
| D-proxy-base-url | **`proxy.base_url` is overridable by an environment variable, named by `proxy.base_url_env` (default `RA_PROXY_BASE_URL`).** Precedence: env value > roster file value > built-in default. The roster's `base_url` becomes the *fallback*, not necessarily the effective value; the override is applied once in a `ProxyConfig` after-validator so every reader (`LLMClient`, `_fetch_model_info`) sees the resolved URL with no call-site change. Unset or empty env leaves the file value untouched. | Mirrors the existing `api_key_env` hook so the config surface stays consistent. Before this, `base_url` was readable only from the file, so a containerized deployment on a Docker bridge network — which cannot resolve the baked Tailscale MagicDNS URL and reaches the LiteLLM proxy by container DNS name (`http://litellm-proxy:4000/v1`) — had to mount a whole override `roster.yaml` just to change one line, shadowing every upstream roster change (model retunes, new critics, search defaults) and forcing a manual re-sync each time. Injecting one env var lets the baked roster stay authoritative for models, critics, search, and budgets. Kept backward-compatible: a roster with a plain `base_url:` and no env set behaves exactly as before. Applied in a validator rather than as an `api_key`-style lazy property because `base_url` is a plain field read across the codebase as an attribute, and a property cannot share its name; resolving at load also means nothing ever reads a URL the env was meant to override. No invariant is touched — this is a deployment-config affordance, not a change to isolation, author-exclusion, the orchestrator's blindness, or the controller. |
| D-run-date-grounding | **Critics and writers are grounded in the run's date, and the shipped roster opts into retrieval (D-retrieval-opt-in); source verification (D-source-verification) stays off everywhere by default.** A `run_date` (UTC) is captured once at intake, stored in graph state, and injected into every writer and critic prompt as trusted context outside the data fence. The code defaults for `search.enabled` and `search.verify_sources` stay `false`; `config/roster.yaml` flips on `search.enabled` only. The completeness brief and the critic `instruction` contract now require that every demanded fix be resolvable within the report itself (add the perspective, weaken the claim, or state the limitation) — a critic may not make a specific external document the only acceptable resolution. | Run `run-75eb136b9bfb` stagnated to `needs_human_review` with good output: the evidence lens, judging "on its face" plausibility from its training-data recency, flagged legitimate current-year citations (one dated the previous day) as future-dated `fabricated_citation` — a blocking defect with a severity floor the writer can never argue down and, without retrieval, never fix. Simultaneously the completeness lens demanded a specific budget-vote record the writer had no way to retrieve, while `writer_revision` (correctly) forbids inventing sources — an unsatisfiable demand. One date per run (not per call) keeps RB-010's byte-identical confirmation critiques across midnight; old checkpoints without `run_date` resume dateless, i.e. with the prior behavior. The date is excluded from the audition prompt-hash surface because it is run context, not lens semantics. Enabling search makes citation demands satisfiable, and retrieval-grounded citations carry real, current dates — closing the false-`fabricated_citation` loop even without verification. Verification would go further (URL resolves, page matches), but it fetches model-chosen URLs, and the egress boundary that makes that safe is a network-layer deployment concern deliberately not implemented in this repo (docs/ssrf-egress-isolation.md documents the pattern); the shipped roster therefore leaves `verify_sources: false`, to be enabled per-deployment behind such a boundary. Search itself is not that exposure: it talks only to the fixed public Brave endpoint. |

| D-source-verification | **Source verification for the evidence lens, opt-in and off by default.** With `search.verify_sources: true`, addressable cited pages are deduplicated and fetched up to `search.max_sources`, then handed to the **evidence lens only** as untrusted data; unaddressable and over-cap entries remain unchecked. `fabricated_citation` and `misrepresented_source` become checkable against the page instead of judgements about plausibility. A failed fetch is explicitly *not* evidence of fabrication — **except** a definitive not-found (HTTP 404/410), which D-notfound-fabrication later carves out as a mechanical `fabricated_citation` because that status proves the URL does not resolve rather than that it could not be read. Each fetch now carries a closed `SourceOutcome` (fetch.py) rather than one flat "could not fetch" string, so the evidence prompt names the failure class and only a not-found sharpens `fabricated_citation`; PR #96 added this vocabulary and, on top of it, an opt-in tier that **reads** a cited PDF instead of refusing it as an unreadable content type — gated by both `sources.enabled` and `sources.pdf.enabled` (`config.SourcesConfig`/`PdfSourceConfig`), fatal at startup if `pypdf` is missing, and off by default like verification itself. Not an SSRF boundary — egress is constrained at the network layer, not here. | D-retrieval-opt-in constrained where citations come from; it did not establish that a cited page supports the claim attached to it, because no critic could open one. Evidence-lens-only is an isolation requirement, not an optimization: logic and completeness cannot raise a citation category, so page text would widen what they see without widening what they may report. Off by default because fetching model-chosen URLs is exposure a deployment must opt into. |

## Codex adversarial review — round 3 (verdict: CHANGES_REQUESTED; 5 resolved / 4 partial / 1 unresolved + 6 new)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RC-001 | crit | Two-model "faithful regeneration" launders authorship → a model reviews its own content; the final artifact gets only one non-author review | **Fixed (D-context-window-unit, D-three-model-roster).** Reframed isolation unit; default ≥3 models with same-artifact `accepted`; honest weaker `converged_unconfirmed` tier for 2 models; "faithful regen" language removed. |
| RC-002 | high | Clean-review evidence not keyed to the accepted artifact; stale attestations could satisfy acceptance | **Fixed** (record now **per-lens**, D-per-lens-critics): immutable `CleanRecord{artifact_hash, lens, critic_identity, author_identity}`; any generation/polish resets the set; `strong_met` needs two distinct non-author records **per lens** for the exact current hash. |
| RC-003 | high | Ordered table wasn't the whole controller function (omitted lenses_failed, polish, cycle, thresholds) | **Fixed.** The single ordered table (now 14 rules after the per-lens reorder) includes lens-failure, polish (+counter/cap), and cycle rules; totality/termination argued explicitly. |
| RC-004 | high | Cap rules preceded the incomplete-review check → partial counts could be classified clean | **Fixed.** `lenses_failed > 0` is now rules 2–3, before any clean/material/cap conclusion; partial counts never satisfy a clean predicate; no retry budget ⇒ `aborted`. |
| RC-005 | high | `overstated_claim`/`omitted_counterargument` relied on critic-supplied materiality | **Fixed.** Both floored mechanically at `major`; the materiality-downgrade path is removed. |
| RC-006 | low | DESIGN.md/isolation.md still labeled v2 and referenced `SignalReport` | **Fixed.** All docs relabeled v3; normative `SignalReport` references replaced with `OrchestratorView`/`ControllerInput` (historical review-log mentions retained). |

## Codex adversarial review — round 8 (per-lens extension; verdict: CHANGES_REQUESTED, 0 crit / 2 high / 1 med / 1 low)

Rounds 4–7 drove the pre-extension design to 0 critical / 0 high / 0 medium (table verified total
and terminating; 3-model acceptance trace confirmed). Round 8 reviewed the D-per-lens-critics/D-critic-only-specialists per-lens
extension:

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RG-001 | high | At the cap, terminal rules fired before per-lens top-up could run | **Fixed.** Clean-artifact rules (7–11) are no longer `round`-gated; only `material>0` cap terminals (rules 5–6) are. Top-up (rule 8) stays reachable at the cap (it doesn't generate or advance `round`). |
| RG-002 | high | The "2-model consecutive-clean fallback" was referenced but never represented in state | **Fixed by removal.** `weak_met` is now purely the per-lens `roster_limited` case (current-hash-only); all consecutive-clean language deleted. |
| RG-003 | med | Tick/sequence/DESIGN diagrams still showed one critic for three lenses | **Fixed.** Diagrams relabeled to per-lens critics (each ≠ author); DESIGN core-loop reframed from "two-model ping-pong" to a role-structured alternating game. |
| RG-004 | low | Stale `lens_set` / rule-number / flat-roster wording in the review log | **Fixed.** RC-002 → per-lens `CleanRecord`; RB-002 de-numbered; D-two-clean-critiques annotated as superseded by D-per-lens-critics; roster contract restated as per-lens eligibility. |
| D-seed-conversion | **Seed reports are converted to markdown at the edge; URL seeds are opt-in and off by default.** `--seed` and the web form accept PDF, `.docx`, HTML and `.txt`; `ingest` converts them before `graph.run` is called, which continues to require markdown. http(s) URL seeds exist behind `seed.allow_url`, default `false` (the D-retrieval-opt-in/D-source-verification posture): a URL seed makes the server fetch a caller-chosen URL and expose the body back through the run's report endpoints — on the web UI that is a read proxy into whatever the host can reach, and the egress boundary that makes it acceptable is a network-layer deployment concern outside this repo (docs/ssrf-egress-isolation.md). *(Written when the UI was unauthenticated. D-identity-header identifies callers, which narrows who can submit a URL but not what the host can reach — the egress boundary remains the prerequisite.)* Turning it off hides the form field and rejects the parameter. A format that yields no headings is accepted with a warning, not rejected. | Markdown is not a preference here, it is load-bearing: `report.parse` builds the `[S<n>.P<m>]` loci critics must cite from `#` headings, and `extract_source_urls` reads only a markdown `## Sources` section, so an unconverted seed silently costs the evidence lens its fetch-backed checks. Converting at the **edge** rather than inside `_intake` keeps one artifact and one identity — `_run_fingerprint` and `artifact_hash` would otherwise hash different things (a URL vs. its converted text), letting a resume pass the fingerprint check while the checkpoint held different prose. It also keeps network I/O out of the graph, where every other fetch is injected through `Runtime` so tests stay offline. Accepting a heading-less seed reflects what the formats actually carry: PDF has no recoverable heading semantics without font heuristics, and refusing would block the most common real-world case to protect locus precision the source never had. PDF is the only format needing a dependency (`pypdf`, optional extra); `.docx` is a zip of XML and HTML is an `HTMLParser`, both standard library. |

## D-critic-audition — critic eligibility becomes structural *and* demonstrated

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
| RC-007 | med | Run submission is unbounded in both queue depth and disk footprint. `RunWorker.submit()` enqueued onto a `queue.Queue()` with no `maxsize` and no rate limit, and each submission immediately wrote a persistent run directory. Concurrency bounds token *spend* but not the number of queued runs, the memory they hold, or the run dirs they leave on disk; `recover()` re-enqueues them all on boot. A single burst — a script, or the companion CSRF vector — could create thousands of runs and directories, and `Registry.list()` reads every run dir on each `GET /`. | **Fixed (D-bounded-submission).** `submit()` refuses with HTTP 429 past `max_queue_depth`, and a fixed-window per-identity limiter (`submit_rate_max`/`submit_rate_window_seconds`) throttles bursts. Both checks precede any disk write, so a refusal costs nothing. The web server also runs an automatic content-only retention sweep so disk reclamation no longer waits on a manual `purge`. |

## D-bounded-submission — submission is bounded, and a refusal costs nothing

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
the Tailscale header when present and a single global bucket otherwise. D-identity-header superseded that
— every request now carries a resolved identity or is refused by the middleware before it
reaches `submit()`, so there is no shared global fallback bucket left.)* On the tailnet
posture the header is trustworthy; a caller reaching the app directly could forge it, but
such a caller could equally vary it to defeat any per-identity scheme. This is backpressure
against bursts, not itself the access boundary — that is D-identity-header's trusted-header gate, with
Tailscale ACLs / Cloudflare Access in front of it.

Retention gains an automatic **content-only** sweep on a timer (`purge --content-only`,
run for you), matching the documented posture — reports/critiques after N days, the
decision record for longer. Full-directory removal stays the explicit human `purge`, so the
audit trail of a run's convergence is never deleted by a background timer. Live runs are
skipped, so an in-flight run cannot lose its drafts mid-run.

This touches none of the isolation invariants: it is upstream of run creation and moves no
new data toward any model context. `OrchestratorView` and the controller are untouched.

## D-fixer-grounded-judgment — the cold review fixer exercises grounded judgment, not a mechanical checklist

*(D-run-date-grounding is allocated to run-scoped date grounding, landed separately.)*

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
fixer's output: the fixed SHA is not reviewed again (D-fixer-merges-not-rebases), so the pre-fix panel, the fixer's own
gates, and this verification run are the backstop.

## D-social-bias — social-bias categories on existing lenses, governed by docs/bias.md

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

## D-writer-disputes — writers may dispute fix-tasks; adjudication is mechanical-first, identity-blind, and fail-closed toward the finding

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
transition is byte-identical to a build without the feature, the D-retrieval-opt-in pattern):

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

## D-question-refinement — question refinement is offered at the edge, ambient and never blocking

**The problem.** The pipeline already knows questions arrive loaded: `unexamined_presupposition`
(D-social-bias, completeness lens, major floor) exists precisely to catch a writer who accepts a contested
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
was already nameable — `unexamined_presupposition` (D-social-bias) would tag some of these on sight — but
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
   suggestions) passes, the same deferred-audition pattern D-social-bias uses for its own cross-critic
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

## D-installable-pwa — installable on a phone, without letting anything cacheable be wrong

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
trusted header (D-identity-header). Three
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

## D-fixer-merges-not-rebases — the fixer syncs the branch and resolves conflicts; it merges, it never rebases

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
PAT, where a non-forced `git push HEAD:<ref>` is a much smaller thing to get wrong. A clean merge
whose tree matches a mechanical recreation lands on the merge-from-base inherit path, so that
resync costs no review cycle. A conflict resolution by the fixer or a human does not inherit: under
D-inherit-whole-range, the panel reads any merge whose tree cannot be recreated exactly.

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

## D-base-path — servable under a URL base path, without relaxing the same-origin posture

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
enabled (D-question-refinement) — the `fetch()` the inline refinement script issues to `/refine`. That last
one is a browser-origin URL like the rest and carries the prefix for the same reason; it was
missed when D-question-refinement and D-base-path landed in separate PRs and is corrected in PR #66.

**A stripping proxy, so the routes do not move.** The proxy removes `/app/` before the
request arrives, so the app still serves at `/runs`, `/sw.js`, `/manifest.webmanifest`. The
base path shapes only what the app *emits*, never what its router *matches*. This is the
ASGI `root_path` convention, but `FastAPI(root_path=…)` is deliberately **not** set: nothing
here reads `scope["root_path"]`, routing matches the already-stripped path, and URL
generation is explicit, so setting it would add a second, silent mechanism that could
disagree with the explicit one.

**Why an env and not `X-Forwarded-Prefix`.** The manifest and the service worker are
resolved to bytes once at startup (D-installable-pwa: "read once, at startup"), with the worker's cache
version hashed over the precached URLs. Reading the prefix from a per-request header would
force those to be rebuilt per request, or cached per distinct header value — turning a
static, hashed artifact into a request-varying one. A single startup value keeps D-installable-pwa's
"these files do not change while the process runs" true. One process serves one prefix;
that is the residual, and it matches the one-deployment-one-mount reality.

**The CSP does not change, and that is the point.** Every URL the app *fetches from or
submits to* stays same-origin, so `connect-src 'self'` / `form-action 'self'` /
`base-uri 'none'` are exactly as D-installable-pwa pinned them, and that test stays green. (There is a
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

**What D-installable-pwa's three service-worker properties cost.** All three hold under a prefix. The
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
does touch is D-installable-pwa's, and it is generalized, not relaxed: "served from the root so its scope
is the whole origin" becomes "served from the mount point so its scope is the app," with the
root case as `base = ''`.

## D-verdict-attached — a report leaves the system with its verdict attached, or it does not leave

**The problem.** There was no export. `final.md` and `GET /runs/<id>/report.md` served the report
alone, and the deployment posture (tailnet-only, and — until D-identity-header — unauthenticated) means
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
report is unaffected by the record being added elsewhere. It is a *route*, not a button: offered
next to `Download .md` it advertised what reads like the same file, differing only by the review
record — the one thing this decision exists to keep attached — so the link was removed and the
route left in place for the tooling that wants bytes. All three export surfaces are offered from
`GET /runs/<id>/report`, the single page that renders a report (D-id-as-credential).

**Why these three and not a share link.** A hosted link is the obvious answer and the wrong one
here: it needs public exposure and an account for the recipient, which is well past the trusted-header
identity D-identity-header gives a handful of invited people, and past the posture D-run-date-grounding and the README take on.
Files need neither. PDF is generated by the browser rather
than by a server-side engine — the alternative costs a large dependency to reproduce a rendering
path every reader already has, and the print stylesheet is the same stylesheet as the screen, so
the printed page cannot drift from the page it was printed from.

> Superseded in part by **D-id-as-credential**, which serves every `GET` under `/runs/` without an identity, so a
> run page *is* now a share link that needs no account for the recipient. The export files stay for
> the reasons above — the review record travels attached, PDF needs no server-side engine, and a
> file reaches a recipient who cannot reach the host or outlives the retention sweep — but a link is
> no longer confined to the invited audience.

**Why the reviewer list is filtered by artifact hash.** A `CleanRecord` attests to one artifact
(RC-001/RC-002). Earlier drafts collect their own, and listing those in an export would credit a
critic with clearing text it never read — an overstatement of review coverage in the one artifact
that outlives the run directory. With **no** hash to key against, nobody is credited at all:
crediting everyone would be that same overstatement, arriving exactly when the record is least
trustworthy.

**Three states, not two: absent, unreadable, known.** A missing `final.json` means the controller
never reached a verdict. An *unreadable* one means a verdict may exist and cannot be recovered —
a different fact, and reading it as the first would make an export state `aborted`, a terminal
status no rule produced (the failure D-evidence-bearing-fields/RA-012 keeps `abandoned` out of `final.json` for). So
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

## D-decision-gate — decision numbers are checked for collision at the gate, not renamed after merge

*(Superseded by D-decision-slugs: the offline, secret-free duplicate gate survives; authoring-time numeric allocation and the "not renamed after merge" stance do not.)*

**The problem.** A decision number (`## D<n>`) is allocated by whoever writes the PR, against
the highest number on main at authoring time. The number is not just prose: it appears in
`config/`, `src/`, `tests/` and several docs, so it is effectively a shared identifier
allocated without a lock. Two PRs open at once each pick the same next-free number and collide
when both merge; worse, when a subagent notices the clash and *independently* renumbers, both
land on the same replacement. This happened three times, most visibly with #54 and #56 both
claiming D-verdict-attached (issue #71). Every collision costs a repo-wide rename.

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

## D-identity-header — the interface has users: a trusted identity header, and runs that belong to someone

Every prior version of this document says there is no authentication and that Tailscale ACLs are
the access control. That was true and deliberate for a single operator. Opening the interface to
friends makes it false in a way that matters: without a user concept, everyone who reaches the
app shares one index onto everyone's questions, seed material and audit trails.

**Decision.** Identity comes from a request header set by whatever fronts the app —
`Cf-Access-Authenticated-User-Email` from Cloudflare Access, or the `Tailscale-User-*` headers
D-bounded-submission already read — and every route but `/healthz` refuses a request that carries none. Runs
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
fine as D-bounded-submission's rate-limit key, where any *stable* string worked, but an ownership key must be
the *same* string the other door produces, and a display name is a different namespace from an
address. What normalization cannot fix is a tailnet whose identity provider reports a different
address than the Access policy lists — that is two people as far as this system can tell, and
the check is to sign in each way and compare the *signed in as* line.

**Enforcement is middleware, not a call per route.** `_reject_cross_site` is invoked by hand at
the top of each mutating handler, and that idiom is right for CSRF — it is a property of two
specific routes. Authentication is a property of the app, and the failure mode of an opt-in
check is a future route that forgets it. The middleware is the only fail-closed shape.

**`/healthz` stays the only exemption, including for D-installable-pwa's app shell.** The manifest, service
worker, offline page and icons are static files that hold nothing private, so exempting them
would have been defensible — and it is still declined, because an exemption list is a thing that
grows and every future entry is argued against a precedent rather than against this decision. The
price is paid in the `<head>` instead: a manifest is the one subresource a browser fetches with
credentials *omitted* by default, even same-origin, so the link carries
`crossorigin="use-credentials"`. Without it the fetch reaches Access with no `CF_Authorization`
cookie and is bounced at the edge — where an app-level exemption could not have helped anyway —
and the only symptom is that the app quietly stops being installable. The container smoke test
asserts both halves: `/` with no header is a 403, and the shell is there once a header is set.

> Superseded in part by **D-id-as-credential**, which serves every `GET` under `/runs/` without an identity. The
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

> **D-id-as-credential** kept this and dropped the "signed in": holding the id is the whole credential. Resume
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

**D-bounded-submission's rate limiter is unchanged in mechanism and stronger in effect.** Its key was already the
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

## D-refine-audition — refine prompts are auditioned with fixtures, and scope narrowing is a graded violation

D-question-refinement shipped its guardrails in two layers: three enforced mechanically, five as prompt policy
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
silence is the measured failure; for refinement, silence is the designed default (D-question-refinement), so a
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
`audition.enabled` flag D-critic-audition deleted.

**The mirror-pair skeleton exists, and it gates nothing.** The corpus carries one ideologically
mirrored pair (`pair: qbq-01`) for `question_behind_the_question`, skipped under the default
transform set and runnable via `ra audition-refine --transforms`. The harness reports the
pair's fire-rate asymmetry as a diagnostic number — the measurement D-social-bias deferred and D-question-refinement made
the condition for enabling that transform — but the enablement decision itself stays a human
one; no threshold on the asymmetry is wired into any verdict.

Residuals: the corpus is small (ten fixtures) and public, so slot rotation is doing real work
against memorization; `require_terms`/group stems are casefolded string containment with a
whole-word rule for short terms, which is deliberately dumb and will need corpus care as it
grows; and the harness measures the refine *model*, not the client-side JS, whose gaps remain
listed in question-refinement.md.

## D-unguarded-sync — the base-branch sync runs even when the panel was guarded off, and stays non-agentic when it does

**The problem.** The base-branch sync D-fixer-merges-not-rebases built to keep agent-authored PRs mergeable was
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
like every other D-fixer-merges-not-rebases sync. A conflicted PR at least now fails visibly, with `merge_state=blocked`
and the conflicting paths in the run log, instead of silently doing nothing.

**The sync-only successor is the one SHA the fixer does not claim.** The second thing review caught
was a contradiction between this decision and D-fixer-merges-not-rebases. Normally the fixer claims `review/pipeline` on
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
reviewable, on the same "reachable, not guaranteed" terms as any D-fixer-merges-not-rebases sync (above); what makes it safe
is that its content is the base branch, already reviewed on its way to main, plus a PR-side delta of
zero. Author
exclusion, the blind orchestrator, fail-closed lenses, severity floors, controller termination, and
the untrusted-text boundary all live in the Python review core and the convergence controller, none
of which this touches — this is CI gating in `review-pipeline.yml` and `review-fixer.yml`. The
untrusted-text boundary is in fact *tightened*: one path that could have fed unvetted conflict
contents to a generator no longer exists. The judge still fails closed on the sync-only cycle's
empty reviewer set (pre-existing behaviour when guards refuse), publishing a NO-GO on the pre-sync
SHA that the mergeable successor supersedes.

## D-id-as-credential — reading a run is public; holding the id is the credential

D-identity-header made every route but `/healthz` refuse a caller with no identity, which is the right default:
the failure mode of an opt-in check is a new route that forgets it, and seed material, questions
and audit trails are exactly what must not leak. But it also closed the one thing the interface
most wants to do — hand a finished report to someone who is not invited. Under D-identity-header sharing works
only *between signed-in callers* (reads are share-by-id, not owner-scoped); a link sent to anyone
outside the Access policy 403s at the app.

**Decision.** Every `GET` under `/runs/` is served without an identity. Every write stays gated
exactly as D-identity-header left it. Holding the run id *is* the credential for reading that run — which is
what D-identity-header already said, minus the sign-in.

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
owner (D-identity-header) and every route under `/runs/` passes through it, so this shares nothing that was
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
deployment — dev, the tailnet — emits byte-identical URLs to before (D-base-path is unchanged; this adds a
second base, it does not alter how either is joined).

**One page renders the report, and the run page points at it.** Making `/runs/<id>` public solved
the 403 but left two pages showing the same report: the run page rendered it in full *and* linked
to `/runs/<id>/report`, so whichever URL a person copied, the recipient got the report — plus, on
the run page, six buttons and a pipeline trail they have no use for. The report body now lives at
`/runs/<id>/report` only. The run page keeps what belongs to the *run* — the verdict, the
round-by-round trail (no longer folded, since there is nothing above it to outrank it),
`audit.json`, `Ask this again` — and a single `Read the report` link. Every way of *taking* the
report away (copy, `.md`, `.html`) moved to the page that renders it, where a reader who has
decided they want it is already standing, and `audit.json` is offered there too: `/report` is the
page that gets shared, and a verdict a recipient cannot check is not much of a claim.

**A status is a marker until it is labelled.** `exhausted unresolved` as a bare badge is a word in
a vocabulary the recipient of a shared link has never seen. Both pages now show `Run status`, the
badge, and the `STATUS_MEANING` sentence — the same words the export carries (D-verdict-attached), from the same
table, so the page and the file explain the verdict identically. The label is print-hidden along
with the badge; on paper the print header already states it.

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

**The forgery caveat is the same one D-identity-header already carries, no wider.** The tailnet path lets any peer
set the identity header and read or submit as anyone; that is the accepted risk whose fix is the
deferred JWT check below. This decision removes the identity *requirement* for reads that every
signed-in caller could already perform by holding the id, so the set of things an anonymous tailnet
peer can reach does not grow by anything they could not reach by claiming an identity.

**Isolation is untouched.** This is entirely in the web layer, upstream of nothing that enters a
model's context and downstream of every run. Author exclusion, the blind orchestrator
(`OrchestratorView`), fail-closed lenses, severity-floor clamping, controller termination and the
untrusted-text boundary all live in the Python review core and the convergence controller, none of
which this changes. Showing a report to an anonymous human is the same act D-identity-header already sanctioned
for a signed-in one; blindness is about what a *model* may read, and this moves no data toward any
model.

Deployment and the route table are documented in [authentication.md](./authentication.md).

## D-resume-timeout — a hung author-resume is contained at the container boundary, not by `continue-on-error`

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
- In **resume mode** the script is best-effort and never fails the job. Every contained outcome —
  a timeout, a crash, or a clean exit that produced no artifact — writes a `fixer-incomplete.sentinel`
  beside the output log, naming the reason, and exits 0. The sentinel is *positive* for all three
  rather than letting a missing `fixer-result.json` stand in for a crash, because an agent can write
  its result and *then* exit nonzero: the absence-based signal would read that crash as a fix and push
  work the agent never vouched for. Its stem comes from `OUTPUT_LOG_PATH`, not the role, so it always
  matches the log and result it sits beside (the composite builds those from `ARTIFACT_BASE`, which is
  allowed to differ from the role — a divergence would otherwise silently stop the fallback firing).
  In **any non-resume mode** (cold fixer, reviewers, resolver, author) there is no fallback, so a
  timeout or missing result stays fatal, exactly as before — the containment is gated on
  `AGENT_RESUME=1` and touches nothing else.
- The workflow adds a "Did the author-resume fixer produce a fix?" step that reads the sentinel /
  result off disk and sets `ok`. The fallback fires on `!cancelled() && … && ok != 'true'`. A status
  function is load-bearing: the "second-order trap" is that a condition with no status function has an
  implicit `success()` ANDed onto it, which — now that a contained timeout reports the step as
  `success` — would skip the fallback on exactly the hang it must catch. It is `!cancelled()` and not
  `always()` because the fallback step is not read-only: it resets the tree to the reviewed SHA and
  replays the base merge, and `always()` would run all of that while the job is being cancelled.
  `continue-on-error` is kept only as defense-in-depth for a failure in one of the composite's
  *other* steps.

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

## D-quality-reviewer — a `quality` CI reviewer guards the design's evidence base, which is itself dated and refreshable

**The problem.** The `invariant` reviewer audits conformance: code moving against the current
spec, or either side moving alone (its row 12). A PR that coherently updates code, the normative
doc, *and* `decisions.md` passes it by construction — which is correct for its job and is exactly
the gap. The design's load-bearing choices (author exclusion, fresh-context critique, cross-family
witnesses, mechanical floors and deterministic control, refinement over debate, capped loops,
fetched-text verification) each track a specific published result, and nothing in CI could block
the coherent, self-documented PR that walks the design onto ground that literature refutes — "let
an LLM score convergence 1–10, spec updated to match" would have sailed through the panel.

**Decision.** A fifth reviewer role, `quality`, audits *direction* against a new normative
register, [quality-principles.md](./quality-principles.md): twelve `QP` rows, each naming its
surface and the fetch-verified literature behind it. Conditionally selected (`src/`, `config/`,
every `docs/*.md`, and the review pipeline's own files); may abstain, since `invariant` and `docs`
remain the never-abstain backstop. It runs on **Codex** deliberately: it deconflicts most heavily
with `invariant` (Claude), and the guard on the spec's direction should not share a model family
with the guard on the spec's conformance — the panel applying QP3 to itself.

**Evidence discipline.** The reviewer's parametric memory of the literature is exactly the
testimony this repo distrusts, so a `quality` finding may rest only on the register's References
table (every URL fetch-verified on the marker date) or on text the reviewer fetches during the
review — and it may fetch only URLs appearing in the diff or in that table. No search engines, no
remembered papers. This is `search.verify_sources` applied to the pipeline's own epistemics.

**Freshness.** The register carries a machine-readable `Evidence base last verified: YYYY-MM-DD`
marker. The reviewer compares it to the run date on every review; past twelve months it files one
stably-titled follow-up issue and a non-blocking note — never a blocker, because staleness is the
repository's debt, not any PR's defect. The refresh is a normal PR (human, or the issue-resolution
agent) that re-fetches every reference, searches for superseding work *inside that reviewed PR*,
and bumps the marker — reviewed by the full panel including `quality` itself. Deliberately no
scheduled workflow: the reviewer is the scheduler, the nag persists until paid, and degradation is
honest rather than silent. A principle row may be weakened or retired only with new evidence
fetchable from a URL in the diff (`qual-uncited-retreat` otherwise) — the register must be able to
lose an argument to better evidence, or it is dogma with citations.

**Wiring.** Role enum in `reviewer-v1.json` *and* both id patterns in `fix-result-v1.json` (the
two-schema lesson from the `docs` role); classifier `want_quality`; static caller job plus the
three `needs:` lists and the `reviewed` OR-chain in `review-pipeline.yml`; `quality.md` added to
the prompt-ranges test; `quality-principles.md` added to `is_spec_critical`, the mkdocs nav, and
the DESIGN.md document map. Judge, aggregator, and fixer are untouched — they treat role names as
opaque strings, and needing to change them would have been a design smell.

## D-notfound-fabrication — a definitive not-found is `fabricated_citation`, settled mechanically; every other failed fetch is not

**The problem.** Source verification (D-source-verification) fetches the pages a report cites and hands them to the
evidence lens so `fabricated_citation` can mean *the URL does not resolve* rather than *implausible
on its face* (the convergence table). But the prompt rendered **every** failed fetch identically —
`COULD NOT FETCH: <error>` — and then told the critic, correctly for a 403/timeout/paywall,
*"never raise a defect on the basis of a failed fetch."* An HTTP **404** (and 410 Gone) is not
"could not read"; it is "does not exist" — the single status that proves the URL does not resolve.
Lumping it with the unreadable class laundered the one signal that establishes fabrication, so the
evidence lens was instructed to ignore exactly the fact that would flag it. `docs/convergence.md`
carried the same contradiction: its table said a non-resolving URL is `fabricated_citation` while
the paragraph below said a failed fetch is never evidence of fabrication.

The failure mode is concrete and reproducible: a writer that runs zero searches with retrieval
enabled fills `## Sources` from parametric memory, and if every one of those cited URLs returns HTTP
404, the old prompt led the evidence lens to raise **zero** issues on it, because it had been told
to. `fabricated_citation` floors at `blocking` (taxonomy), so suppressing it is what converts a
wholly-fabricated bibliography into a clean evidence lens and ships it. This diff pins the regression
publicly rather than resting the claim on private run audit material: `tests/test_fetch.py` replays a
twelve-of-twelve-404 fetch result and asserts the evidence lens does **not** come back clean, while a
twelve-of-twelve-403 run still does — so the empirical claim above is checkable from the diff itself
(QP9).

**Decision.** Split *unreachable* from *unreadable*. A cited URL that returns a definitive not-found
(HTTP 404 or 410) yields a `fabricated_citation` **mechanically** — raised in the fetch path
(`triage.mechanical_citation_issues`, called from `graph._critique_one`), where the fetch already
happens — so the finding is a fact the pipeline reports, not a judgement a critic model must elect
to make. This mirrors `dispute.adjudicate_mechanical` (a citation category a fetched page settles
without an arbiter) and QP10 (verification is fetched text, never parametric memory). The finding is
attached only to a **completed** review; a failed lens is discarded and re-critiqued (rule 2), and
because the per-run fetch cache is warm the finding is simply re-derived on the next attempt, so
nothing is lost and no failed-lens result is silently promoted to countable.

Every other failure class — 403, a connection error/timeout, an unreadable content type, an empty
body — is unchanged: no defect, surfaced honestly. The distinction rides on `FetchedSource.unresolvable`
against `NOT_FOUND_STATUSES = {404, 410}`. *(Reconciled by PR #96: `unresolvable` now reads
`outcome is SourceOutcome.NOT_FOUND` rather than re-deriving from `status`, so the definition cannot
drift from the closed vocabulary and a not-found established without an HTTP code — a registry that
has never heard of the identifier — reaches triage too; and the failure classes are now each surfaced
under their own `SourceOutcome` label rather than one flat "could not fetch".)*

**Preferred over prompt-only.** The issue offered a fallback (approach 2): stop laundering the
status in the prompt and let the critic raise it. Rejected as the primary route because
`fabricated_citation` floors at `blocking` and forces `needs_human_review` — too consequential to
leave to a critic model choosing to make it, when the fetch already proves it. The not-found
escalation is therefore the pipeline's job, not the model's — the critic still judges
misrepresentation and on-its-face plausibility and must not double-raise on a fetch failure.
*(At D-notfound-fabrication this was expressed as "the critic prompt is left unchanged: 'never raise a defect on the
basis of a failed fetch' stays correct." PR #96 reconciled the wording: because triage now mints the
finding, the evidence-lens prompt stops sharpening `fabricated_citation` toward the critic at all —
inviting the critic to raise it as well would double-report one defect, both copies at the blocking
floor — and instead tells the critic a not-found has already been recorded mechanically and must not
be raised again. The safety property D-notfound-fabrication established, that mechanical minting never depends on a
critic electing to act, is unchanged and is pinned by
`test_a_not_found_source_is_not_offered_to_the_critic_to_raise_again`; `misrepresented_source` still
sharpens, and only when a source's body actually arrived.)*

**Spec.** `docs/convergence.md` no longer contradicts itself: the not-found row of the verification
table is now explained as mechanical, and the "a failed fetch is never evidence of fabrication"
paragraph is scoped to the failure classes it was written for — the `run-75eb136b9bfb`
future-dated-citation failure mode it guards against (a *judgement* about date plausibility, D-run-date-grounding) is
untouched and must not return. The RA-019 test matrix row is updated and populated: 404/410 →
mechanical blocking finding; 403/timeout/unreadable/empty → no defect (each pinned); the
twelve-of-twelve-404 regression asserts the evidence lens does not come back clean.

**Invariants.** Fail-closed lenses, severity floors clamp-up-only, blind orchestrator, and author
exclusion are all preserved: the mechanical finding is a normal `fabricated_citation` at its
existing floor, its text reaches only the writer-facing `Defect` and the audit store (never
`OrchestratorView`, which stays counts-only), and it never touches who may critique what.

## D-existence-vs-body — a citation's *existence* is verifiable for free; its body usually is not, and the two must never be confused

**The problem.** D-source-verification fetches the URLs a report cites so the evidence lens can check them, and D-notfound-fabrication
mints `fabricated_citation` mechanically when one of those fetches returns a definitive not-found.
Both are sound, and both share a blind spot: **a direct fetch can fail for reasons that have
nothing to do with whether the source is real.** A paywalled journal or a newspaper that refuses an
automated client hands back a `blocked` — indistinguishable from a source nobody could check for
any other reason, and one HTTP status away from looking like a source nobody ever published.
Verification was therefore strongest exactly where a citation is easiest to fake (a fabricated blog
URL 404s) and weakest exactly where a source is refused rather than absent.

**The refusal, first.** There is no omni-passport service that hands over paywalled bodies, and the
ways of faking one are all off the table. This system does not spoof a browser user agent to defeat
a bot wall, does not solve CAPTCHAs, does not launder paywalled text through archive.org, and does
not replay a cookie jar or an institutional credential to impersonate a subscriber. `fetch.py` has
carried the reason since D-source-verification — *"pretending to be a browser to get around that would be the wrong
kind of clever"* — and that comment is doctrine, not decoration: a system whose whole claim is that
its citations are checkable cannot obtain them by circumventing the access controls of the people
who published them. Where a body is not lawfully readable, the honest answer is to say so.

**The asymmetry that IS the design.** Citation verification asks two questions, and they are not
equally expensive:

1. *Does this source exist, and is it what the report says it is?* — answerable for free from
   bibliographic registries, for any citation carrying a DOI, an arXiv id, a PMID or a PMCID.
2. *Does the source say what the report claims?* — needs the body, and often no lawful copy exists.

(1) is the cheapest and by far the largest win, because answering it is what stops a paywalled
source looking like a fabricated one. It must **not** be allowed to leak into (2). An abstract is a
summary the authors wrote; a claim's absence from an abstract is not evidence that the paper does
not make the claim. So `misrepresented_source` still sharpens only when some source's *body*
actually arrived, `prompts.fetched_sources_block` gains a third entry shape that announces
existence before anything else and labels an abstract as explicitly not the full text, and the
rules list forbids raising `misrepresented_source` against a source shown only as metadata.
`fabricated_citation` is likewise not sharpened toward the critic — D-notfound-fabrication mints it mechanically, and
a second copy would double-report one defect at its blocking floor.

**Decision.** A new `resolve/` package (a peer of `web/`) runs a two-rung ladder, and only when a
direct fetch yielded no body.

*Tier 0 — identifiers.* Extract a DOI / arXiv id / PMID / PMCID from the cited URL by regex alone
and ask the configured registries. With the default roster — Crossref and OpenAlex — that answers
*existence* for DOIs and PMIDs; arXiv ids and PMCIDs are covered when arXiv and Europe PMC are
added to `sources.identifiers.providers` (both ship in the open-access roster, so a free copy is
still sought for them there). A confirmed record yields the title, authors, year, venue and
abstract, and — the point — *existence*. It runs even when tier 1 succeeds, because the attributed
title is worth checking against the report whether or not a body arrived.

*Tier 1 — open access.* OpenAlex's `best_oa_location`, Unpaywall, Europe PMC, arXiv. When one names
a free copy, the direct/PDF path is re-entered **exactly once**, via an explicit depth argument
rather than a convention, and never recursively.

Every provider is a keyless GET through `fetch.http_get`, which stays the single egress point for
the whole codebase; `tests/test_resolve.py` asserts mechanically that no module under `resolve/`
imports `urllib.request`. CORE is deliberately excluded: it needs an API key, and a
credential-bearing request is a different security posture (header handling, no-redirect opener,
fail-closed startup validation) that this change does not take on.

`fetch` does **not** import `resolve` — the tiers need `search.QueryBudget`, which sits downstream
of `fetch`, so the dependency would close a cycle. The resolver is built in `graph._build_resolver`
and injected into `SourceFetcher`, exactly as `_build_searcher` builds the searcher: network
clients are assembled at startup, so the graph performs no I/O and the suite stays offline (D-seed-conversion).

**The new outcomes, and how conservative each is.** `METADATA_ONLY` means existence confirmed and
no body. `PAYWALLED` requires *both* a registry corroborating existence *and* a direct fetch that
was refused — it is never guessed from a status code, because HTTP 402 is vanishingly rare and a
real paywall usually answers 200 with a teaser. `BUDGET_EXHAUSTED` says a tier that could have
answered was out of per-run calls, so an operator does not read a column of `blocked` and blame the
sites; it deliberately never overwrites `NOT_FOUND`, since a run that exhausted its budget at
source five would otherwise silently stop reporting D-notfound-fabrication's finding for sources six through twelve —
turning a tier on must never weaken a defect the pipeline raises without it.

The one path that can *raise* a defect is gated hardest. An identifier no registry has heard of is
`NOT_FOUND`, which D-notfound-fabrication mints as a blocking `fabricated_citation`, so it requires all of: a
confidently-extracted identifier (a mangled one is an identifier no registry holds, which is why
`identifiers.py` prefers to return nothing); a denial from **every consulted provider that is
authoritative for that identifier kind** (Europe PMC answers DOI queries but is authoritative only
for PubMed ids — its silence about a physics DOI is a coverage boundary, not evidence about the
world); and a direct fetch that established nothing either (a not-found, or a host that did not
resolve). A 403, or a 200 that merely would not parse, keeps its own verdict — a live server
refusing a client says something about the client, and a served page proves the URL resolves.
Symmetrically, a registry that *confirms* the identifier outranks a 404 on the cited URL: the
citation names a real document, and a dead link is a dead link, not a fabrication.

**The dispute invariant under the new outcomes.** `dispute.adjudicate_mechanical` returns `True` or
`None` and never `False`. Two new hazards, both closed:

* an abstract must never uphold a dispute — free, via `.ok`, because `FetchedSource`'s first
  invariant forbids a non-`FULL_TEXT` outcome from carrying text at all, so `METADATA_ONLY` is not
  `ok` and there is nothing for a quote to match;
* an open-access mirror's body must never uphold a dispute about the cited URL. A preprint often
  differs materially from the version of record, and a quote present in arXiv v1 and absent from
  the published paper is a real failure mode — so a result whose `body_source_url` is set is
  inconclusive by construction, and the finding stands. The arbiter and the evidence critic are
  both told, in the prompt, when they are reading a mirror rather than the cited page.

**Off by default, budgeted per tier.** `sources.identifiers` and `sources.open_access` each carry
their own `enabled`, provider list, timeout and `max_calls_per_run` (reusing `search.QueryBudget`,
already generic and thread-safe), under the existing `sources.enabled` master switch. Two switches
per tier is the pattern `sources.pdf` established: enabling one tier must never turn on another.
Both are off in both shipped rosters, asserted in `tests/test_shipped_rosters.py`;
`config/roster.default.yaml` mentions them not at all, because its job is booting with no network
and no credential.

A contact email for Crossref and Unpaywall's polite pool is configurable via
`sources.contact_email_env` (default `RA_CONTACT_EMAIL`, resolved from the environment like
`ProxyConfig.api_key`). Its absence is a **warning**, not fatal, and the warning names what is lost
— demotion to the anonymous rate-limit pool, which is degraded service rather than a broken config.
Contrast a missing search credential, which is fatal because without it the feature cannot function
at all. Unpaywall is the single exception, refusing anonymous requests outright; it is dropped with
its own warning rather than failing the tier. `compose.yaml` passes the variable in explicitly,
because one the operator exports outside the container never reaches the process inside it, and
that failure would otherwise be silent.

**Cache monotonicity.** Three caches, three key spaces: the final `FetchedSource` by cited URL
(`SourceFetcher`'s own, and self-describing enough that a `METADATA_ONLY` entry — empty text,
non-`None` error — is already treated as a failure by every consumer written before this change);
`SourceMetadata` by normalised identifier, so two URLs naming one DOI share a single Crossref call;
and identifier → best open-access URL, where a stored `None` means *asked, and none exists* — the
distinction that stops a twelve-source report making twelve identical Unpaywall calls. All three
are monotone within a run: written once, never invalidated, never re-resolved. Every round
therefore judges the same evidence, and a provider that was flaky at round two does not become
authoritative at round six. The two caches inside the resolver share one lock, because critics run
at concurrency 3 and a second lock would only add an ordering to get wrong.

**Audit trail.** RA-016 sharpens rather than relaxes: provider *names* are a closed vocabulary and
are safe to log, provider request *URLs* are not — the polite-pool querystring carries the
operator's email, and the querystring is where the next vendor will put a token. The identifier is
no better, being derived from a URL private to the run. `resolve/base.py` owns that rule in its
docstring, and a test asserts that no request URL, contact email or identifier reaches a log
record. The `fetch_sources` event gains a tally of resolution tier across **all** sources, not just
the failures: `{"direct": 5, "open_access": 2, "identifier": 4}` is what tells an operator whether
a tier is earning its calls, and a source the open-access tier rescued is a success that leaves no
trace in the failure tally. `_failure_reasons` keeps tallying the closed outcome vocabulary and is
not regressed to free text.

**Egress posture unchanged.** `docs/ssrf-egress-isolation.md` describes a Squid gateway that denies
every private / loopback / link-local / tailnet destination and then `http_access allow all`, with
an explicit note that there is deliberately no domain allowlist because arbitrary public fetching is
the feature. These five fixed first-party hosts therefore need no new rule, and they are
categorically *narrower* than what D-source-verification already permits: model-chosen URLs. The bounds that do apply
are the ones already in `fetch.http_get` — timeout, byte cap, http(s)-only opener — plus a redirect
cap of zero for provider calls specifically, on `search.py`'s reasoning that an endpoint which is a
constant has no business being redirectable when its querystring carries something personal.

**What this does not claim.** A confirmed DOI shows that a source exists and that the report's
attributed title, authors, year and venue match a real record. It does not show the source is
correct, and it does not show the report characterises it fairly. That remains the residual blind
spot RA-011 names, now materially smaller: the class of citation that cannot be checked at all has
shrunk from "everything paywalled" to "everything paywalled and carrying no identifier".

## D-paid-tier-page — the paid tiers render a page, and never disguise who is asking for it

**The problem.** D-existence-vs-body made a citation's *existence* verifiable for free, which stops a paywalled
paper looking like a fabricated one. It did nothing for the other half: a body that never arrives
because the page needs JavaScript, or because a bot wall refuses an unknown HTTP client. Those are
not paywalls and they are not fabrications — they are a client capability gap, and the honest fix is
a better client.

**The decision.** Two more rungs on D-existence-vs-body's ladder, both off by default and both fail-closed at
startup. `sources.extraction` sends the cited URL to a rendering service (Firecrawl is the one
reference implementation; the registry is open) and takes back markdown. `sources.delivery` is a
config shape and a registry entry with **no provider behind it**.

**The line, which is the whole decision.** A rendering provider can be pointed at a page in two
registers: read it as a normal client, or disguise the client to defeat anti-bot defences —
residential IP rotation, fingerprint randomisation. On the provider integrated here, Firecrawl, the
second is a `proxy` mode, and `resolve/extraction.py` pins the request to `proxy: "basic"` while
naming `"stealth"` and `"auto"` in `FORBIDDEN_PROXY_MODES` as values it must never send — `"auto"`
because it starts basic and escalates into stealth silently when a site refuses. That is the
industrial form of the browser impersonation `fetch.py` has refused since D-source-verification and D-existence-vs-body records as
doctrine, bought by the page instead of coded by hand. **Rendering a page is not disguising who is
asking for it, and only the first is in scope.**

So `proxy` is pinned to `"basic"` in `resolve/extraction.py` and there is no configuration field
that can change it — absent, not defaulted-off, because a knob makes doctrine an operator
preference. `tests/test_extraction.py` asserts against the serialised request body rather than the
options constant, so a future code path assembling its own payload is caught too. The doctrine is
now something CI fails on rather than a comment somebody can delete. It is also, incidentally, one
credit per page instead of five; that is a coincidence and not the argument.

**What the paid tier does and does not buy.** It reads JavaScript-rendered pages and is not turned
away by the bot walls that refuse an unknown HTTP client — two reasons a cited body fails to arrive
that are neither a paywall nor a fabrication. It does **not** pass a hard paywall: a subscription
wall serves a teaser to a real browser too. Anyone reading "paid tier" as "universal access" is
wrong, and the module docstring, the roster comment and this record all say so, because that
misreading is the one most likely to be made.

**Why delivery ships empty.** A document-delivery provider would return a paywalled body under some
licensing terms, and whether those terms permit splicing a delivered document into a model's context
is a licensing question, not an engineering one — one this repository has not answered. Building a
speculative adapter against an API nobody here holds credentials for produces untested code that
will be wrong when someone finally needs it, so the seam exists with no provider behind it. That the
seam is *inert rather than half-built* is enforced, not merely asserted: a `SourcesConfig` validator
makes `sources.delivery.enabled: true` with `provider: ""` fatal at load, because a tier that can
name no provider can make no call.

**Ordering, and why extraction runs last.** By cost, not by likelihood. Extraction is the likelier
fix for a news citation and still runs after the free rungs, because a registry answer is worth
having even on a source whose body later arrives: it is what lets a critic check the *title* the
report attributes rather than only its prose. Extraction is skipped entirely against a definitive
not-found — there is nothing to render at a URL the server says is not there, and a success against
a soft-404 landing page would overwrite D-notfound-fabrication's mechanical `fabricated_citation`.

**A rendered body may settle a dispute; a mirror may not.** D-existence-vs-body refuses adjudication on anything
carrying `body_source_url`, because an open-access preprint is a *different document* from the
version of record. A rendered page is the cited URL itself read by a better client, so it carries no
such marker and stays usable. That distinction is the reason `ResolutionTier.EXTRACTION` exists
separately from `OPEN_ACCESS` rather than both being "we got the body somehow".

**Credentials.** `FIRECRAWL_API_KEY` and `CORE_API_KEY`, resolved through `search.resolve_token` —
environment first, gitignored file second — and both passed into the container explicitly in
`compose.yaml`. A tier enabled without its key refuses to start, unlike D-existence-vs-body's contact email, which
is a courtesy and only warns: a keyed provider with no key makes no successful call ever, so
starting would spend the tier's whole budget on 401s and report them as coverage. An enabled tier
naming no provider is fatal for the same reason a default would be wrong — a paid call must never go
to a vendor nobody chose.

CORE joins the open-access tier here rather than in D-existence-vs-body because it is keyed, and inherits this
fail-closed posture for that reason alone. It is deliberately absent from the default provider list,
so that enabling open access does not silently become "and also supply a CORE key or fail to boot".

**The call ceiling bounds a bug, not a bill.** Unset, `extraction.max_calls_per_run` derives from
`search.max_sources * budgets.hard_cap` — the most distinct URLs a run could ever cite, every
citation replaced every round. Derived rather than written down so raising `hard_cap` cannot
silently start starving the tier at the old number. It is generous on purpose: `SourceFetcher`
caches per URL for the whole run, so three critics re-verifying one `## Sources` list across eight
rounds cost one call per URL rather than twenty-four, and what remains to guard against is a fetch
loop that ignores that cache.

**Egress.** Unchanged, and narrower than what already passes. These are fixed first-party hosts, and
the credentialled POST goes through `fetch._request` — the same hardened opener as everything else —
with `allowed_hosts` naming only the provider's own host and a redirect cap of zero. Belt and
braces: `_BoundedRedirects` strips the credential on any redirect regardless, because a leaked key
is not a recoverable mistake.

**Invariants.** Untouched. Rendered text is third-party content entering a critic's context under
RA-010 and reaches only the evidence lens; provider names are a closed vocabulary safe for the audit
trail while their request URLs, which carry the key, are never logged (RA-016); the dispute channel
still returns only `True` or `None`; and no tier can raise a defect the pipeline would not otherwise
raise, only fail to suppress one.

## D-absence-anchor — a defect of absence anchors its `claim_span` to text that is present

**The problem.** `triage.validate_issue` requires `claim_span` to be a verbatim quote from the
paragraph the critic cited — the anchor that keeps a critic's findings to words the report already
contains, and one of the two things (with the RA-010 data fence) that stop critic text reaching a
writer as authority. The critic prompt stated that requirement in one flat line: *"`claim_span` must
be a short verbatim quote from that paragraph."*

That line is self-evident for every logic and evidence category, because those defects live *in*
text the report contains: the overstated wording, the uncited sentence, the claim a citation is
misdescribed as supporting. Quote the offending text and you are done.

The completeness categories are the opposite, in two distinct ways. Two are defects of *absence*:
`omitted_counterargument` is defined as "a material opposing view … is absent" and
`unexamined_presupposition` as adopting a presupposition "without stating or examining it" — both
material (a `major` floor). The third, `unclear_structure`, is neither absent nor material: it sits
at a `minor` floor and is a property of *arrangement* rather than of any one span. For all three,
"quote the offending text" has **no referent** — an absent view has no span to quote, and a
structural defect is a property of the passage as a whole rather than of a locatable phrase — so a
critic reaches for material that is not in the paragraph (the missing view, or a paraphrase of the
structural problem). It fails `_require_quote`, fails it again on both repair attempts (the hint
hands back the paragraph, which is the right text but not the missing answer the critic went looking
for), and fails the whole lens closed.

The failure is structural: it follows from the category shape, not from any one model's weakness, so
any critic asked only for "a verbatim quote" of an absent view has nothing valid to quote and fails
the same way. Each such failure surfaces as a `claim_span … is not a verbatim quote from the cited
paragraph` violation, and costs a controller re-critique out of the run's bounded `critique_attempts`
budget. That it is a gap in the contract rather than a weak model is why the fix is to the prompt and
not to the roster.

The in-call repair loop (`budgets.critic_repair_retries`) was the earlier response to the same
symptom; `tests/test_critique_repair.py` exercises it against exactly this violation, using
`omitted_counterargument` as its fixture. Repair stopped a *recoverable* slip from costing an
attempt. It cannot help a critic that does not know what the anchor is for, which is why the failure
survived it.

**The decision.** `prompts._CATEGORY_ANCHOR` gives every category an explicit statement of what
`claim_span` anchors to, rendered into the critic prompt for that lens's in-scope categories only —
the same closed scope the meanings table already follows. The prompt body states the general rule
once: *where the defect is something the report does NOT say, `claim_span` still quotes what it DOES
say — the passage the gap bites into.* Each of the three names the present text it anchors to: the
two absence categories point at the claim the missing element bears on, and `unclear_structure`
points at the opening words of the passage whose arrangement is the defect. The two whose missing
element is *content* redirect that content to a field which is not span-validated (`instruction` for
the omitted view, `rationale` for the presupposition), so the advice is not "drop the issue" by
implication.

`related_span` has carried per-category guidance in this prompt since it was written, for exactly
this reason. `claim_span` never did.

**Why this is not a weakening.** `triage.validate_issue`, `_require_quote` and `_normalize` are
byte-identical. The prompt tells a critic *where to find* a valid span; it does not enlarge the set
of spans that validate, and a span that is not really in the paragraph still fails the lens closed.
The change is strictly narrowing on the model side: each category now permits *less* than "some
quote from that paragraph". The alternative fix — relaxing `require_verbatim_spans` for the
completeness lens — was rejected, because span-anchoring is one of the six CI-audited invariants and
the completeness lens is precisely where an unanchored span would be most tempting to invent.

**What it does not fix.** A critic that raises a genuine omission and *still* cannot quote the
paragraph is unchanged: it fails the lens, as it should. This raises the ceiling on how often the
lens completes; it does not guarantee completion. Nor does it touch the decision table — a
completeness lens that completes instead of failing changes which rule the controller reaches only
by supplying the counts rule 2 would otherwise have discarded.

**Audition cache.** `audition.prompt_hash()` covers `prompts.critic_user`, so this change correctly
invalidates any cached audition and every critic reads `stale` until `ra audition` is re-run. That
is the intended behavior — the hash exists because editing a lens prompt changes what was measured.
With the shipped `audition.enforce: false` a stale cache warns rather than failing startup.

**Invariants.** Untouched. Untrusted text still never reaches a generator as instruction: spans stay
verbatim-anchored and validated, defects still cross to the writer only as fenced data (RA-010/D-evidence-bearing-fields),
the fail-closed lens contract (RB-007) is unchanged, severity floors are not involved, and the
controller's inputs and rule ordering are not touched.

## D-provider-retry — a transient provider failure costs minutes, never the run

**The finding.** A single flaky provider response — an empty completion, a transient error, or a
tool call arriving where prose was due — could abort a whole run as
`terminal=aborted … rule 1: every eligible writer failed`, having logged only `writer attempt 1/1`.
No model need be unavailable for this. Four separate weaknesses in the code compound, each
reproducible offline and pinned by a test added with this change, and together they turn one bad
sample into a terminal abort:

*The writer pool is one model deep on every revision round.* Author exclusion applies to writers,
not only critics: `roles.writer_pool` removes the previous author, so a two-writer roster leaves
exactly one eligible model from round two onward. `_generate` then computed
`attempts = min(len(pool), writer_attempts)`, which silently reduced a configured budget of 3 to
**1**. The fallback that exists precisely so "one dud model must not cost a run" was, on every
revision round, not there.

*Retries did not wait.* `LLMClient._create` looped with a bare `continue`. Nothing in the package
slept between attempts, so a three-deep call budget was spent in the time it takes to round-trip
three requests — three samples of one bad moment rather than three chances spread across it.

*An exhausted tool loop could return success with nothing in it.* The final round of `complete()`
drops `tools`, but a model may answer it with another tool call anyway. `_create` accepts such a
message — correctly, since a message carrying tool calls is not an empty completion — and the loop
returned `Completion(text="")`. `_generate` read that as "the writer produced an empty report", a
condition it never classified as a call failure, so it received **none** of the three call retries.
Because the call had "succeeded", this path logged nothing at all at the `llm` layer — it is
invisible from the logs, which is why it is pinned by a test rather than read from one.

*A tool with no budget left was still offered.* Once `search.query_budget` is gone the handler
returns "budget exhausted" as *text*, by design — a writer told nothing would read the silence as
"nothing exists". But the tool stayed on offer, so a determined writer could spend every remaining
round asking again and arrive at the exhausted round with a tool call instead of a report, feeding
the defect above.

**The decision.** Availability is a property of the pipeline, not of the roster's luck.

1. **Retries wait, exponentially, with jitter** — `min(base * 2^(n-1), cap)` scaled by
   `uniform(0.5, 1.0)`, from `budgets.retry_backoff_seconds` / `retry_backoff_max_seconds`. A
   server-supplied `Retry-After` beats the computed delay, bounded at 120s. The sleep and the jitter
   are injected into `LLMClient` exactly as `BraveSearch` injects its clock, so the offline suite
   asserts the wait without serving it.
2. **Permanent failures are not retried.** A 400/401/403/404/413/422 answers identically however
   many times it is sent; `PermanentCallError` (a `ModelCallError` subclass, so every existing
   `except` still catches it) raises immediately rather than burning the budget and the backoff on a
   verdict that was already final.
3. **A tool loop never returns empty text.** When the last round yields no prose, one further
   toolless round asks for the answer in words; if that is also empty the call raises
   `ModelCallError`. A writer call is ten-plus minutes and has usually spent its whole search budget
   by then, so one cheap round beats discarding it — and the failure now enters the retry budget
   instead of masquerading as a successful empty report.
4. **Writer attempts are a budget, not a walk over the pool.** `attempts = writer_attempts`, with
   the rotation wrapping. A one-model pool gets three spaced tries; a larger pool alternates.
5. **A tool whose budget is spent is withdrawn**, via `complete(should_offer_tools=...)`, which
   `_generate` binds to the search budget. The model is forced to write rather than to keep asking.
6. **A third writer**, `nemotron-3-ultra` (550B/A55B, ~275GB @4bit — inside the 450GB ceiling), so
   the eligible pool is two deep on revision rounds rather than one. `docs/DESIGN.md` previously
   recorded it as *excluded by choice*; the choice is reversed here, and the reason is availability.
   It is writer-only, so it shrinks no critic pool. Critic pools were **not** widened: they are
   already three deep per lens, and rule 2's rotation already absorbs a critic that times out
   repeatedly without aborting the run — the writer path, one-deep on revision rounds, was the only
   one that lacked that depth.

**Author exclusion is untouched, and this is the claim to check.** Wrapping the writer rotation
re-asks a model that already failed; it never re-asks the *previous author*, because
`writer_pool` removed them before `_generate` saw the list. The invariant is enforced at the
construction of the pool, not at the length of the walk over it, so lengthening the walk cannot
reach an excluded model. Nothing here touches critic selection, the blind orchestrator, the severity
floors, or termination: retries are bounded by `call_retries` and `writer_attempts`, the extra
toolless round is exactly one, and rule 1 still fires when every attempt fails.

**Observability.** The shipped log level was WARNING and the container's CMD is fixed, so
`--verbose` was unreachable: a deployment left at that default records no run starts, no controller
decisions, and no search results, which is why a failure of this shape has to be reconstructed from
code rather than read from a log. `$RA_LOG_LEVEL` now names a level, and `compose.yaml` sets `INFO`.
RA-016 holds at that level: `search.py` logs query *lengths* and never query text; the controller
whose decisions are now logged is blind to the report by construction; and `structured()`'s
schema-violation log names the exception class only, never the rejected value (see the `sec-audit-log`
guard in `llm.py`).

**Not fixed here, because they are not in this repository.** Two related failure modes are proxy
configuration rather than application code: silent fallback routing on the LiteLLM proxy — e.g.
`gemma4` served by `meta-llama/llama-4-scout` — which RA correctly fails closed on (RA-017) at the
cost of a burnt lens attempt, and DeepSeek tool-call syntax arriving unparsed as message content.
Both are written up in `docs/deployment-profile.md`.

## D-stop-notification — a run that stops says so, on a device that was not watching

**The problem.** A run is 10–25 minutes and the index makes it easy to start several. The only
mechanism that ever said a run had finished was `GET /runs/<id>/stream`, which pushes progress into
a page that is currently open and reloads it on `done`. Close the tab and nothing says anything;
background the installed app on a phone and iOS suspends it, so nothing *can*. The interface's own
affordance — queue several questions and go and do something else — was the one it could not
support, and the workaround was to come back and poll the index by hand.

**Decision.** Web Push, delivered through the service worker D-installable-pwa already ships, sent from the
worker thread the moment a run stops. Opt-in is per device, one tap, stored against the caller's
identity. Both a terminal completion and a stop-without-an-answer notify; a shutdown pause does not.

**Why Web Push and not the stream that already exists.** A local `Notification` fired from the SSE
`done` handler is thirty lines and no dependency, and it cannot do the job: it requires the page to
be open and foregrounded, which excludes every case worth notifying about. A suspended iOS PWA runs
no JavaScript at all. Only a server-sent push reaches a locked phone, and only a service worker can
receive one — which is why this decision is downstream of D-installable-pwa rather than independent of it. On iOS
push additionally exists *only* for a web app added to the Home Screen
([WebKit, 2023](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)), so the
install path D-installable-pwa built is the literal precondition; without it there is no iPhone notification to
have.

**The subscription endpoint is attacker-influenced, and that is the security core.** The browser
mints a URL and hands it to the server, which then POSTs to it — the same shape as the seed-URL
fetch that `ssrf-egress-isolation.md` exists for. `push.validate_endpoint` requires HTTPS, refuses
embedded credentials and an explicit port, and matches the host against an allowlist of push
services on *labels*: an exact match, or a dot-anchored suffix for a wildcard entry. A substring or
bare `endswith` test admits `evil-fcm.googleapis.com` and `fcm.googleapis.com.attacker.net`, both of
which are pinned as refusals. The check runs at subscribe time **and again before every send**, so
narrowing the allowlist takes effect on subscriptions already stored rather than only on new ones.

**The routes are top-level, and specifically not under `/runs/`.** D-id-as-credential opens every `GET` under
`/runs/` to anonymous callers, so that prefix is where reads of a finished run live. Subscribing is
the opposite: it attaches a device to an identity. `authenticate`'s method guard would refuse a
`POST` there anyway, but siting a write inside the public read prefix and relying on that guard
inverts the rule D-id-as-credential states — reads public, writes gated — into a coincidence. A test enumerates the
route table rather than these two handlers, so a future push route cannot drift into `/runs/`
either.

**The CSP is unchanged.** Subscribing is not a page fetch: the browser negotiates with the push
service out of band, and the only page-originated request is a POST to this origin, already covered
by `connect-src 'self'`. Nothing here needed a new directive, which is worth recording because D-installable-pwa
had to widen the policy and a reader may reasonably expect this to as well.

**The service worker's cache invariant survives by construction.** `push` and `notificationclick`
are additive and neither touches `caches`, so `cache.put` still appears in exactly one branch
reachable only for URLs in `ASSETS`. The `push` handler's payload parse is wrapped and always falls
back to a generic body, because Chrome's `userVisibleOnly` contract means a handler that throws gets
the browser's own "site updated in the background" notice — a vague notification is bad, that one is
worse.

**The contact address is an environment variable, and there is deliberately no roster field for
it.** [RFC 8292 §2.1](https://datatracker.ietf.org/doc/html/rfc8292#section-2.1) *recommends*
(`MAY` include the claim, `SHOULD` make its value a contact URI) a `sub` claim — a `mailto:` or
bare `https://host` a push service can use to reach the operator; the hard requirement is
`py_vapid`'s, whose `Vapid01._base_sign` raises `VapidException` when `sub` is absent or empty. So
an unset subject means every send raises before reaching the network. That exception would land in the notifier's best-effort
`except` and present as notifications that silently never arrive, so `push.enabled` with no subject
is a boot failure instead. It is an env var for the same reason `SourcesConfig.contact_email` is:
the value is somebody's personal address, the roster is committed to a public repository, and a
config field is an invitation to put it there. Only the *variable name* is configurable.

**The VAPID key is generated, not configured, and losing it is the sharp edge.** A keypair is
self-issued: there is no account anywhere to register it with, no Firebase project, no APNs
certificate. So making the operator produce one by hand adds a setup step that can be got wrong and
buys nothing, and the app mints it on first boot. The cost is state worth backing up — every
subscription is bound to the key it was minted under, so a lost key invalidates all of them at once,
silently, because a push to a stale subscription is simply refused and there is no channel left to
ask the device to re-subscribe. The generation path logs a warning saying exactly that.

**Both files, never a directory.** Subscriptions and the key live directly in `runs_dir`, which is
the mounted volume — anywhere else and they die with the container. They are files because
`Registry._run_dirs` skips anything without an `events.jsonl` *and* `store.expired_runs` filters on
`is_dir()` alone: a `push/` subdirectory would eventually be swept as an expired run, taking the key
and therefore every subscription with it. A file is invisible to both by construction, which is a
stronger guarantee than an exclusion rule a later refactor can drop.

**The send is inline on the worker thread, best-effort, and never fatal.** It runs from `_drain`'s
`finally` on a sentinel set by each branch, rather than from the branches themselves — `finally`
also runs on the `GracefulStop` `return`, and a shutdown pause resumes on the next boot, so
notifying about it is a false alarm. Leaving the sentinel unset is what suppresses that case, which
is harder to get wrong than remembering to omit a call from one branch in four. At
`RA_MAX_CONCURRENT_RUNS=1` a dead push service delays the next queued run by the timeout — seconds,
against a run measured in tens of minutes — which buys the absence of a second thread with its own
shutdown story. A `404` or `410` prunes the subscription; every other failure is a log line, because
the run is already finished and durable and a courtesy must not cost the result.

**The payload carries the question, which is a privacy decision.** Web Push bodies are encrypted
under the `aes128gcm` content coding to a key pair the user agent binds to the subscription
([RFC 8291](https://datatracker.ietf.org/doc/html/rfc8291)), so whichever push service relays the
message — Apple, Google, Mozilla or Microsoft — carries ciphertext it cannot
read. Truncated question text is what tells five concurrent runs apart on a lock screen, and without
it the notification says only that *something* finished. The deep link uses the reader-facing base
(D-id-as-credential), so it is the same URL every other run reference in the app emits; a finished run points at
the report, one that stopped without shipping an answer points at the run page.

**Permission is requested from a click and never on load.** iOS grants the prompt only in response
to direct user interaction
([WebKit, 2023](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)), and a
declined permission cannot be re-prompted — the only reset is deleting and reinstalling the
home-screen app — so an auto-prompt spends that single chance on a page view, before the person
knows what they are being asked. The
control also ships `hidden` and is revealed only after the script has established the browser can
deliver: an iOS Safari tab has `PushManager` and still cannot subscribe, so feature detection alone
would show a button that fails, and the standalone check turns that into an instruction instead.

> The last clause is superseded in part by **D-header-optin**, which moved this control into the
> layout shell. A header has no room for an inline sentence, so on iOS in a browser tab the control
> now stays hidden rather than rendering an instruction; the install affordance is the one the
> browser already offers. Everything else here — click-gated permission, `hidden` until the browser
> is known to deliver — stands.

**Off by default.** Like every feature needing egress or a secret. With `push.enabled: false` there
are no routes, no key on disk, and an index byte-identical to a build without any of this — the same
promise D-question-refinement makes for refinement, and asserted the same way.

**Invariants.** Untouched. This is entirely downstream of a finished run and moves no new data
toward any model context: nothing here reaches a critic, a writer or the arbiter. Ownership is read
from `owner.txt`, the single record D-identity-header established, so an owner-less run notifies nobody rather than
having an identity invented for it. Isolation, the dispute channel, the convergence controller and
the fail-closed lenses are not touched.

## D-decision-slugs — decisions are identified by a subject slug, not a shared counter

**The problem.** A decision identifier was a number allocated from a single sequence on `main`, so
choosing one meant knowing what every in-flight PR had already chosen. Two PRs opened against the same
base necessarily draw the same next-free number from that shared maximum, and the collision surfaces only
at merge — by construction, not by luck, because neither PR's merge ref contains the other's choice. The
only remedy the counter offered was to renumber, and a renumber is a push: it resets the review cycle and
spends a full panel run. That cost lands in the most expensive place a rename can, because the number is
echoed into the commit subject, the PR title and body, and across `config/`, `src/`, `tests/` and docs.

The counter had also failed silently in this very file. Four old numbers each named **two** different
decisions — the old→new mapping at the top of this file splits each into two slugs (for example
`D-open-weight-roster` and `D-source-verification`, which shared one number, and `D-redeploy-survival`
and `D-critic-audition`, which shared another). The gate could not catch it, because it matched only the
`## ` prose headings and never the table rows, so "is this identifier defined twice?" was a question it
could not answer for the table-form half of the decisions.

**The decision.** Identify each decision by a **slug derived from its subject** (`D-source-verification`),
coined by the authoring PR. Two concurrent PRs cannot collide, because a slug is chosen from the
decision's own content, not from a global maximum — neither PR needs to read the other. Ordering moves
into file position, and the append point is fixed: immediately before `## Open items for a future round`.
Every existing decision was renamed once, on purpose, and the old→new mapping is published at the top of
this file so historical citations stay resolvable. The duplicate gate survives but is rebuilt to read
**both** definition forms, closing the blind spot above; a companion test
(`tests/test_citation_resolution.py`) asserts that every decision-shaped citation across `docs/`, `src/`,
`tests/`, `config/` and the reviewer prompts names a slug this file actually defines, so a citation to a
decision that does not exist now fails CI — a property the numeric scheme never had.

**What D-decision-gate got right, and what changed.** D-decision-gate's premise was that a decision
identifier "is echoed across `config/`, `src/`, `tests/` and docs — so a collision costs a repo-wide
rename," and it chose to refuse collisions at an offline, secret-free gate rather than pay that rename.
The premise was correct and the gate is kept: a duplicate identifier is still refused at the same
secret-free PR job, now in both surface forms. What changed is the rest. D-decision-gate kept
authoring-time *numeric* allocation and explicitly declined to rename after merge, judging the rename too
expensive to pay. This decision pays it once, deliberately, to make the collision impossible by
construction instead of merely caught after the fact — trading a single bounded rename for the removal of
a recurring, unbounded one. Its "a gap in the sequence is legal" caveat is now moot: slugs have no
sequence, so there are no gaps to leave.

**Invariants.** None of the tabulated safety invariants is in reach — author exclusion, the blind
`OrchestratorView`, fail-closed lenses, severity floors, controller termination and the untrusted-text
boundary all live in the pipeline core and are untouched. This is repository governance: it constrains
how a *document* is identified, not what enters any model's context. The rename itself is mechanical —
each decision's body says exactly what it said before, under its new name.

## D-header-optin — the notification opt-in lives in the shell, and only until the device is subscribed

**The problem.** D-stop-notification shipped the opt-in as a control at the bottom of the index's
"Your runs" panel — below a table that grows without bound. Two things were wrong with that, and the
second is the one that matters.

It was hard to reach: with a dozen runs listed, the control sat below a screenful of rows, on the one
page a person visits least once they have started something. And it was *absent* exactly where it was
wanted. Starting a run redirects to `/runs/<id>`; the moment someone decides they want telling is the
moment they have just kicked off a 10-25 minute run and are about to put the phone down. On that page
there was no control at all, and no way to get to one without navigating back.

**Installed to a home screen there is no navigating back.** A standalone app has no address bar, no
reload button and no visible history - that is what `display: standalone` means. A control reachable
only from one page, on a surface with no chrome to reach it with, is a control that is not there.

**Decision.** The opt-in moves into the layout shell, beside `how this works`, so it is on every page
the app renders - index, run page, report. One mount point, one script, one state machine.

**It is shown only while this device has no subscription.** A toggle reading "Notifications on" would
be a permanent header element that never changes and never helps, and the header is the most expensive
real estate the page has. Once notifications are on there is nothing left to offer, so the steady
state is an empty header. Turning them *off* belongs to the OS, which owns the permission and exposes
it in Settings on every platform that implements Web Push; a page-level "off" switch would be a second
control that can disagree with the real one. What the script does keep is *reconciliation*: a
permission revoked out of band leaves a subscription the server would go on pushing to, so a `denied`
permission on load tells the server to forget that endpoint rather than waiting for the push service
to start answering `410`.

**It is emitted only for a signed-in caller, which is a rule about strangers and not about tidiness.**
Every `GET` under `/runs/` answers an anonymous reader (D-id-as-credential), so the run and report
pages are reached by people who were handed a link. They have no runs to be notified about, and
`POST /push/subscribe` is a gated write that would refuse them, so a control there is an invitation to
a 403. The key is withheld unless `request.state.viewer` is set.

**On iOS in a browser tab the control stays hidden rather than explaining itself.** `PushManager`
exists there and `subscribe()` rejects, so feature detection alone would show a button that fails.
D-stop-notification put an inline hint in its place; in a header there is no room for a sentence, and
the install affordance is one the browser already offers. Hidden is the honest state: the feature
genuinely is unavailable until the app is installed.

## D-self-refreshing-index — the runs list corrects itself, because an installed app cannot be reloaded by hand

**The problem.** The index is a snapshot. A run finishes, and the table goes on saying `running` until
something reloads the page. In a browser tab that is a non-issue - the reader hits reload. Installed
to a home screen it is a defect with no user-side fix: a standalone app has no address bar, no reload
button and no pull-to-refresh inside the page — the removed browser chrome D-installable-pwa and
D-header-optin already record as what `display: standalone` means. The one mechanism the index relied
on is the one the platform removes.

That is not cosmetic. D-installable-pwa states the rule this violates outright - *a finished run
displayed as still running is the one output this interface must not produce* - and spends its whole
service-worker design on preventing it in the cache. It was being produced anyway, one layer up, by a
page that had no way to correct itself.

**Decision.** The runs table refreshes itself. `render_index_rows` renders the `<tbody>` as a
fragment, `GET /runs-table` serves it, and the page swaps the element in place.

**The visibility handler is the load-bearing half, not the interval.** The realistic failure is not a
page left open in the foreground for five minutes; it is an app backgrounded for an hour and then
swiped back to. A suspended iOS PWA runs no JavaScript at all — the platform behavior D-stop-notification
already rests on — so an interval alone is frozen while backgrounded, resumes late on return
and shows stale rows for a beat first, precisely at the moment of maximum attention. Refreshing on
`visibilitychange` means the list is current *before* it is looked at. `pageshow` with `persisted`
covers the back-forward cache, which restores a page wholesale and runs no tick at all.

**The interval runs only while something is live, and stops itself.** `data-live` is computed on the
server from the same `is_live` the rows are rendered from and travels *on the fragment*, so the flag
and the rows it describes can never disagree - and the client never infers liveness by scraping status
text. When a refresh returns `data-live="0"` the loop ends, so an idle index left open on a phone
costs nothing.

**One renderer, so the two views cannot drift.** The page and the endpoint both call
`render_index_rows`. The fragment is the whole `<tbody>` rather than its rows so the swap is one node
and the refreshed flag arrives with the rows in a single step.

**`/runs-table`, not `/runs/table`.** `_PUBLIC_GET_PREFIX` is the string `"/runs/"` and every `GET`
beneath it is anonymous (D-id-as-credential). This is a per-viewer, owner-scoped list - the index's own
body - so it must be gated, and the sibling name is what keeps it outside the prefix by construction
rather than by a special case. `/runs/table` would have read as the natural name and would have
published one person's index to anyone holding any run id. A test asserts the path is not inside the
prefix.

**Unconditional, unlike refine and notifications.** This is not a feature to opt into; it is the
repair of a staleness the installed app cannot fix by hand. It needs no new opt-in, no external-service
credential and no outbound request. `/runs-table` stays authenticated and owner-scoped exactly like the
index whose body it is — identity-required in `authentication.md`, gated by construction outside the
public `/runs/` prefix — so it reveals nothing the index does not already show its own viewer.

## D-report-template — every report follows a conclusion-first frame, whoever writes it

**The problem.** Nothing pinned a report's shape. `WRITER_SYSTEM` asked for "clear section
headings" and the first-draft prompt ended at "Return the report in Markdown", so each model in
the writer pool imposed its own habits: the overall form of the output varied with which alias
happened to draft, and the reports read as layered analysis a reader had to excavate for the
answer. For a system whose point is to hand someone with a preconceived notion a clear,
defensible answer, burying the conclusion is a product defect, not a style preference.

**Decision.** A fixed frame with a free middle, stated once as `prompts.REPORT_SKELETON`:
`## Conclusion` first — a direct two-to-four-sentence cited answer that also names the strongest
opposing view — then `## Key findings`, then `## The strongest counterargument`, then topical
sections of the writer's choosing, then `## Sources`, byte-exact and last. Prompt-only: no new
mechanical gate, no new critic category. A skeleton violation is already the
`unclear_structure` lens's business.

**The frame lives in the writer's system prompt, not the first-draft prompt.** `writer_system()`
is the one composition point every writer call shares — first draft, every revision, the polish
pass. A frame stated only at the first draft decays: the revision prompts are template-unaware,
and a polish pass is otherwise free to restructure. And a seeded run never sees the first-draft
prompt at all — round 1 *is* the seed — so the system prompt is what steers a seeded run toward
the frame at its first revision. Seeded round-1 artifacts are deliberately not restructured at
intake.

**The counterargument is prominent by design, and the rationale is epistemic, not
persuasion-maximizing.** The intent of this system is to help people change their mind: a reader
arrives with a notion and should meet the strongest version of the other side early — inside the
conclusion itself, and in a dedicated section ahead of the topical detail — never as an
afterthought at the tail. The persuasion literature was checked before leaning on it, and it
carries less than the folklore says: refutational two-sided messages beat one-sided, which beat
non-refutational two-sided ([O'Keefe 1999](https://dokeefe.net/pub/OKeefe99AICA.pdf), k=107,
r≈.08 vs r≈−.05), but the classic claim that two-sidedness works best on *opposed* audiences did
not survive that meta-analysis, and the effects are small with prediction intervals spanning
zero. The modern result ([Xu & Petty 2022](https://doi.org/10.1177/0146167220988371),
[2024](https://doi.org/10.1177/01461672221128113)) is narrower and more useful: for entrenched
attitudes, a two-sided message increases
*openness*, mediated by the reader feeling their view was respectfully and strongly stated. So
the frame is justified by what it forces the pipeline to do — confront the strongest opposing
evidence where critics can see whether it was answered — with any persuasive benefit treated as
upside, not as the load-bearing claim.

**Two hazards the skeleton text guards against by name.** First, the strawman: presenting the
opposing case weakly nulls the openness effect entirely ([Xu & Petty 2022, study
2](https://doi.org/10.1177/0146167220988371)), and an LLM
asked for "the strongest counterargument" will happily manufacture a weak one — so the skeleton
requires the form proponents would accept, and prefers an honest "reasonable objections exist,
chiefly X" over a manufactured steelman. Second, the unanswered objection: raising a
counterargument without engaging it is *worse than one-sided* in O'Keefe's meta-analysis (the
non-refutational case), so the
skeleton forbids raising an objection and leaving it unanswered, and tells topical sections to
answer objections where they arise rather than deferring them all to the counterargument
section (a quarantined block is the weakest-supported arrangement; interweaving is the
dependable one).

**What this touches and what it does not.** `## Sources` stays byte-exact because
`fetch._SOURCES_HEADING` matches only a heading whose text is the word "sources" and
`triage._locate_url` assumes the section is last. No top-level `#` title, because
`export_markdown` already emits `# {question}` above the body. The `omitted_counterargument`
critic category is unchanged and should simply fire less: its target section is now structural.
Rendering the frame as a layered mobile reading experience is a separate decision.

## D-answer-card — the report page leads with the conclusion, and fails open to the page it replaced

**The problem.** The report page rendered the body as one blob below the page's own furniture: on a
375px viewport a reader got the question, the status badge and its meaning sentence, the back-link,
the run id, and a four-control share row — roughly a screen and a half — before the report's first
sentence. D-report-template makes every report open with a `## Conclusion` section; a page that
buries that section under chrome squanders exactly what the frame exists to deliver, and the narrow
viewport measured above is where that burial costs the most.

**Decision.** When the rendered body opens with a `## Conclusion` h2, `render_report` splits it at
its `<h2>` boundaries and reassembles the page conclusion-first: the conclusion as a distinct card
*above* the status/share furniture, the counterargument section boxed where it stands, the trailing
`## Sources` folded behind its entry count. Server-side string transform in `web/render.py`, CSS
only, no new JS, no CSP change.

**Fail open, because the structure is model-written.** The frame is a prompt, not a validator
(D-report-template), so the splitter trusts nothing: a body with preamble before the first heading, a
first heading that is not `Conclusion`, or a heading carrying inline markup gets the exact page the
route served before this existed — one plain article under the furniture. A pre-frame run, a seeded
artifact, and a writer that ignored the skeleton all degrade to the old page rather than to a broken
one. markdown-it with `html=False` emits bare `<h2>text</h2>` blocks joined by newlines, which is
what makes the split safe to do with a string operation; anything off that shape falls through.

**The counterargument is boxed, never folded.** An objection separated from its answer reads as
stronger than it is — the same reason D-report-template forbids raising an objection without engaging
it. So the counterargument gets visual prominence in place (a bordered box in the article flow), and
no disclosure control that could ever show the objection without the engagement.

**Sources fold on screen and duplicate for print.** The fold is the one collapse this page makes: a
reference list of long URLs is the least-read, most space-hungry section on a phone. A closed
`<details>` prints as nothing, and the print stylesheet exists precisely because a report that
reaches paper must keep its verdict and its evidence — so the fold is `screen-only` and a
`print-only` duplicate carries the full list onto paper — a CSS-only mechanism with no `beforeprint`
JS, consistent with this decision's no-new-JS, no-CSP-change constraint. `export.html` is untouched:
the transform lives in `web/render.py`, downstream of the shared markdown renderer, so the no-script
export keeps its single-article shape.

**Deliberately not done.** No sticky section-jump bar, no collapsed topical sections, no citation
drill-in yet — each builds on this sectionizer and each is its own decision; collapse-by-default in
particular is deferred to that separate decision rather than adopted here, precisely because folding
a section away from its answer is the kind of tradeoff this decision is careful about. And no
restructuring of the run page: it shows the pipeline, not the report.


## D-scoped-revision — a revision edits the paragraphs it was asked about, and stagnation buys one rewrite

**The problem, measured.** Six consecutive production runs finished with **zero `accepted` and zero
`converged_unconfirmed`**: four hit `hard_cap` with major issues (`exhausted_unresolved`), two reached
`needs_human_review`. The cause was not that fixes failed to land — `defects_applied` tracked the
material count round for round, so the writer was applying essentially every fix task it was given.
Across all 36 regenerations in those runs:

```
mean material before a regeneration: 5.39      mean after: 4.81
```

The count did not fall. Each revision retired about as many defects as it created. Broken out by
critique pass, the process is stationary from the second pass onward, and the completeness lens gets
*worse* the longer a run goes:

| pass | logic | evidence | completeness |
|---|---|---|---|
| 0 | 1.7 | 5.3 | 1.0 |
| 2 | 0.8 | 1.0 | 0.8 |
| 4 | 0.8 | 3.2 | 2.4 |
| 7 | 1.6 | 2.0 | 3.2 |

(mean issues per critic call, all six runs pooled)

The consequence is that acceptance was not merely rare, it was arithmetically out of reach. Observed
per-call clean rates were logic 0.40, evidence 0.20, completeness 0.14, so a single pass clears all
three lenses about 1.1% of the time — and `accepted` needs that to happen twice, once to reach
`material == 0` and again for the rule-8 confirmation top-up. `material` hit 0 twice in 45 triage
passes. Rule 8 fired once in six runs; the fresh critic returned 9 material issues and sent
`run-cabddb9a612b` back for seven more rounds.

These figures are drawn from the `audit.json` trail of that six-run production set — a private
operator's own run history, not part of this public repository — so, as in D-question-refinement, the
run IDs stand only as opaque handles and the measurements cannot be re-fetched from the diff (QP9).
They are the *motivation* for this decision, not its warrant. What the decision rests on and pins
publicly is the **mechanism**: the arithmetic below is checkable against the code, and the tests this
PR adds — `tests/test_revision_scope.py`, the rule-13 branches in `tests/test_controller.py`, and the
valve behaviour in `tests/test_graph.py` — make the changed behaviour checkable from the diff itself,
the same way D-source-verification pins its empirical claim rather than resting it on private run
audit material.

**The mechanism.** `prompts.writer_revision` ended *"Return the complete revised report in Markdown —
the whole document, not a diff,"* and `roles.next_writer` hands each revision to a **different** model.
So every round, a model that did not write the text regenerated ~1,800 words in order to repair ~5
paragraphs, and every passage the critics had just cleared was re-rendered by a model with different
priors. Fixing five paragraphs by re-rolling forty is a losing trade, and the numbers above are what
losing it looks like.

**Decision, part one: scope the edit.** `revision.mode: patch` (the default) tells the writer to change
only the paragraphs a fix task names in its locus, plus whatever a task's instruction explicitly
requires elsewhere, and to return every other paragraph **byte-identical**. The output shape is
unchanged — still the whole document, because the artifact hash is taken over the whole document and
every downstream reader wants a complete report. What changes is the licence to re-render text nobody
complained about. `revision.mode: rewrite` reproduces the previous prompt byte for byte, so the two are
A/B-comparable from configuration rather than from a checkout.

**Why this is not an echo chamber.** The objection to patching is that a blind spot planted early is
never challenged again. [isolation.md](./isolation.md) already answers it, and the answer is that
writer rotation was never carrying that load: principle #7 "is fundamentally about *not sharing a
context*, not about model identity," and model diversity is named there as "a second, independent
layer … each dimension blessed by ≥2 distinct non-author models." Decorrelation is assigned to the
**critic roster**. Writer rotation appears in none of the seven principles; its stated justification in
`config/roster.yaml` is availability (D-provider-retry). Three properties therefore hold unchanged, and
they are what make patching safe:

1. **Rotation stays.** `roles.next_writer` and `roles.writer_pool` are untouched — a different model
   patches every round, and no model ever patches its own last draft.
2. **Critics still read the whole document.** Nothing in the critique path changes. Untouched prose is
   not unreviewed prose: a rotating critic pool re-reads every paragraph on every tick.
3. **Clean records still reset on every generation.** RC-002 is absolute. There are no locus-scoped or
   carried-over attestations — that is precisely where a real echo chamber would form. The convergence
   gain comes from a lower defect birth rate, not from reusing stale clearance.

**Decision, part two: stagnation buys one rewrite.** Scoping the edit means a run can only ever
accrete, and an accreted document — eight rounds of "add a sentence acknowledging X" bolted onto one
line of drafting — is exactly what the completeness lens punishes. Controller rule 13 gains a generate
branch: when the signal has been stagnant for `K` ticks and `rewrites_used < budgets.rewrite_cap`
(default 1), the run spends one **whole-document rewrite by a fresh writer** and lets the next tick
judge it on its own signal; otherwise rule 13 is the terminal it always was. This also gives a dead
rule a job — rule 13 requires the per-category `{blocking, major}` multiset to be byte-identical for
`K` ticks, and real trajectories jitter (`7,6,2,3,5,6,8,8`), so it never fired in any of the six runs.
`stagnation_count` is reset when the rewrite is granted; without that the next tick immediately
re-fires rule 13 and spends the whole budget in consecutive ticks.

**Termination survives, unchanged in kind.** `rewrite_cap` is finite and strictly decrements; the
rewrite is a generation, so it advances `round` toward `hard_cap` like rules 4, 9 and 14; and rule 13
is unreachable at or beyond the cap, because the cap-gated rules 5 and 6 precede it in the table
whenever `material > 0`. No new cap gate is required. The rule number stays 13 — the table is still
1–14, and rule 13 already branched internally on `blocking > 0`.

**Measurement, not assertion.** `report.revision_scope` diffs the previous and revised drafts by
paragraph *content* (never by locus number — inserting a paragraph renumbers every locus after it, and
fix tasks routinely ask for one) and records `changed_paragraphs`, `in_scope`, `out_of_scope` and
`defect_loci_untouched` on the `generate` audit event. It is **warn-only**, matching D-refine-audition's
warn-only doctrine: rejecting a draft would burn one of three `writer_attempts`, and a model that
reflows whitespace is not a reason to lose a run. An enforcing tier is worth building only if these
numbers say the prompt does not hold. The check is silent for the three generations that legitimately
touch everything — the first draft, a rule-9 polish pass, and a rule-13 rewrite — so an absent field
means "not applicable" rather than "in scope".

**Known residual: framing lock-in.** One model's voice and framing now persist across a patch chain
instead of being re-rolled every round, and `loaded_language` floors at `minor` under D-social-bias
precisely so a noisy critic cannot force revisions on judgment-laden framing. So a framing bias that
survives its first review is not caught as material and will ride the chain. The rule-13 rewrite is the
mitigation, and it is a partial one: it fires on a stalled signal, not on framing. Recorded here rather
than papered over.

**Deliberately not done.** No change to what critics receive, to author exclusion, or to the blind
orchestrator. No severity-floor changes — `omitted_counterargument` and `unexamined_presupposition` at
`major` were 11 of the 21 outstanding defects across these runs and are worth revisiting separately.
No roster change: `mistral-large-2512` scored 4.25 material issues per call on the completeness lens
across 20 calls and was **never once clean**, against `audition.thresholds.max_control_material_rate`
of 1.0 — that is the other half of this problem and belongs to `ra audition`, not here. `hard_cap` is
not raised; the pass table above shows no improvement after pass 2, so more rounds are pure cost.


## D-control-soundness — a control fixture must be sound under *every* lens, not one

Observed on a live `ra audition` of the shipped roster (9 critic slots, 10 fixtures, 3
repetitions). Every audited critic on the `evidence` lens graded `unfit`, and two of three on
`completeness` did. Sensitivity was not the problem — `obvious_sensitivity` was ≈1.0 across the
board. Every `unfit` verdict came from `control_material_rate` alone: 4.17, 3.00 and 2.67
material issues invented per sound control on `evidence`, 2.83 and 1.67 on `completeness`,
against a `max_control_material_rate` of 1.0.

The critics were right and the corpus was wrong. Both controls carried real, material,
lens-relevant defects:

- `control-sound-01` asserted that hospital construction "follows a trend that predates 1918,
  driven by the shift of surgery and childbirth into institutional settings" with no citation
  anywhere in the report, and attributed a Rockefeller funding claim and a "studies exploiting
  variation in local pandemic severity" claim to nothing.
- `control-sound-02` attributed "the literature attributes much of the gap to construction
  experience" and "studies that model whole systems … generally find" to no source, and cited a
  contested cost question to two sources.
- `control-sound-01` also attributed a specific statistic — US cities with permanently staffed
  health departments "roughly doubled between 1917 and 1923" — to Tomes (1998), a claim the
  fixture author could not stand behind on review. That is `misrepresented_source` in a fixture
  declared sound.

`uncited_claim` and `one_sided_sourcing` both floor to `major`, so each of those is material by
`_is_material`. An evidence critic doing its job correctly scored 2-4 per control against a
threshold of 1.0. The lens was **structurally unpassable**, and the ordering of the observed
noise rates — evidence worst, completeness next, logic barely affected — tracks exactly how many
genuine targets the controls handed each lens. Logic was cleanest because the controls were in
fact written to be logically sound: the one lens whose contract they honoured.

**Root cause.** `FixtureSet.for_lens` hands every control to every lens, deliberately and
correctly — noise is a property of the model, not of a planted category, and a control scoped to
one lens leaves the other two unmeasured on the direction D-critic-audition exists to measure.
But nothing held the corpus to the other half of that bargain. Each control declared a single
`lens:`, and the soundness review — such as it was — went only as far as that declaration.
`test_every_lens_sees_all_controls` asserted that every lens *sees* every control; no test, and
no prose, asserted that a control is *clean* for the lens seeing it.

**Decision.** A control is sound under all three lenses or it is not a control. Both shipped
controls are rewritten to that contract: every empirical claim carries an in-text citation that
resolves to a `## Sources` entry, the question's ambiguous term is named and decomposed rather
than assumed, the case against the report's own answer gets its own section, and a contested
question is sourced across genuinely different kinds of publisher, with the strongest opposing
case stated on its own terms and, where the cited sourcing cannot settle it, left honestly
unresolved rather than resolved by fiat. Attributions the author cannot stand behind are removed
rather than softened.

*Correction (D-fixture-report-shape's merge-reconciliation pass).* The paragraph above originally
read "and across both sides of the live dispute" for `control-sound-02`. A later edit to that
fixture removed the source representing the opposing side of its cost-contingency question,
leaving one academic source (Lovering et al.) for the "costs are contingent, not intrinsic"
reading and none dedicated to the other; the report's own text was already honest about this —
"Nothing cited here resolves that transfer" — but the decision's prose and the fixture's manifest
both kept describing a two-sided academic sourcing that no longer shipped. Both are corrected here
to describe what the fixture actually does: state the strongest opposing case and decline to
resolve it, rather than sourcing both sides of it.

**A control now declares neither `lens` nor `tier`, and the loader refuses one that does.**
Both fields were read only on the planted path — `for_lens` ignores them, `_check_lens_ownership`
has no defects to check, `obvious_total` counts planted defects only — so `lens: evidence` on a
control asserted a scope nothing enforced while reading, to anyone auditing the corpus, as
though soundness under that lens had been established. Deleting the field is the honest form:
there is no lens a control belongs to. `Fixture.lens` becomes `Lens | None`, and a *planted*
fixture with no lens is now rejected too, since nothing would grade it.

**The soundness itself is not mechanically checkable, and the tests say so rather than
pretending otherwise.** A claim carrying no citation marker at all cannot be distinguished by
regex from prose that needs none — a definitional sentence legitimately cites nothing, and a
blanket "every paragraph cites something" rule would push fixture authors to bolt fake citations
onto definitions. What *is* checked is the resolvable part: in-text citations resolve to
`## Sources` entries, entries are all cited, and a control carries at least
`MIN_CONTROL_SOURCES` distinct sources so it cannot earn `one_sided_sourcing` honestly. The
omission and presupposition classes that drive the `completeness` rate have no mechanical form
at all; they rest on the soundness contract recorded in each control's manifest and on review.
A gate that covers part of a property should say which part. (D-control-defect-sweep extends that
contract: a control must also be internally consistent across sections, which is the same
unmechanisable half of the property showing up on the logic lens rather than the evidence one.)

**Rejected: raising `max_control_material_rate`.** It is the correct threshold. A critic
inventing more than one material issue per sound report manufactures work every round, drains
`critique_attempts`, drives `stagnation_count` to its limit and terminates a fine report
`exhausted_unresolved` (rule 13). Moving the line to accommodate a broken corpus would convert a
measurement of the corpus into a permanently weakened measurement of the models.

**Rejected: per-lens `sound_for:` scoping on controls.** It would have made the existing corpus
pass by narrowing what each control claims. With two controls and three lenses, at least one
lens would then have had no control at all — its noise direction unmeasured, which is the exact
failure D-critic-audition was written to close. Making the fixture honest is the fix; making the
measurement smaller is not.

**Cache and blast radius.** Editing the artifacts changes `corpus_hash`, so every cached verdict
stops matching in `cached_judgements` and drops to *not audited* — never to `unfit`. The
`audition.enforce` gate blocks only on a positive `unfit`, so this is safe to land even in a
deployment running with enforcement on: it degrades to "re-measure", which is the correct
reading, because a verdict from the old corpus is a claim about a measurement that no longer
exists.

**This is the half D-scoped-revision deferred.** That decision measured `mistral-large-2512` at
4.25 material issues per call on the completeness lens across 20 calls, never once clean, and
declined to re-roster on it: "that is the other half of this problem and belongs to `ra audition`,
not here." It belongs here, and the finding reads differently now. A completeness critic scoring
against a control that genuinely omits counterarguments is not necessarily noisy — some fraction
of that 4.25 was the corpus. How much, only a re-audition against the rewritten controls can say,
which is the point of fixing the corpus before drawing a roster conclusion from it.

**What this decision does not establish.** The rewritten controls have not been re-auditioned —
that costs a paid proxy run, and the fix stands or falls on the defects quoted above, which are
in the corpus and reviewable without spending a call. The expectation is that `evidence` and
`completeness` verdicts improve materially. If they do not, the residue is a real over-flagging
finding about those models and should be recorded as one.


## D-ci-model-pinning — every CI role names the model it runs, and the runtime-chosen agents default to Codex

**The problem.** Two separate ones, found together while looking for the pipeline's API cost.

The first is that no CI role named a model. `review-agent-run` had exposed a `model:` input since
it was written and **no workflow had ever passed it**, so the two Claude roles ran whatever the
Claude Code CLI currently defaults to, and the three Codex roles ran a `gpt-5.5` literal buried in
a heredoc in `run-in-container.sh`. QP3's stated surface was the `agent:` inputs — but `agent:`
fixes only a model *family*. Which checkpoint actually reviewed a PR was therefore not a property
of this repository at all: a vendor shipping a new CLI default silently re-composed the review
panel, with no diff, no decision, and nothing for the panel to review. A verdict has to be
attributable to something, and "whatever the CLI felt like" is not it.

The second is that the cost-shaped choices had never been made deliberately. Nothing in `docs/`
or `config/` argues about per-role API price anywhere; the economics discussed are cycles, tokens
per re-review, and third-party search quotas. So the assignment of the *expensive* family to a
role was, in four of five cases, undocumented — only `quality` had a stated reason.

**Decision — pin the model everywhere.** `model:` becomes required on `review-agent-run`, checked
at runtime rather than merely declared, since a composite action does not enforce `required: true`.
The `gpt-5.5` literal leaves `run-in-container.sh` entirely and the codex path fails closed on an
unset `AGENT_MODEL`. The five reviewer pins sit in `review-pipeline.yml` beside the `agent:` they
qualify, because that adjacency is what lets a reader check the panel's composition in one place.

| role | agent | model | why this tier |
|---|---|---|---|
| `invariant` | claude | `claude-opus-5` | never-abstain backstop on the six invariants and the merge gate; nothing downstream catches what it misses |
| `test` | claude | `claude-sonnet-5` | bounded, checklist-shaped work against the table in `test.md` |
| `docs` | codex | `gpt-5.6-luna` | the most mechanical role — prose against diff, decision entry present |
| `security` | codex | `gpt-5.6-sol` | guards the egress boundary, where a miss reaches production rather than the next cycle |
| `quality` | codex | `gpt-5.6-sol` | may not rely on remembered literature, so it must actually fetch and read cited sources |

This also pays off the gap noted above: `invariant`, `docs`, `security`, and `test` now carry a
stated reason for their family, which QP3 asks of a new role and which they had never had.

**What the "why this tier" column is, and is not.** It records the *shape of each role's task* —
how much of the repository it must hold, whether it must fetch and read sources, whether anything
downstream catches what it misses. Those are properties of this pipeline, checkable against the
prompts in `.github/scripts/review/prompts/`. It is **not** a measured claim that a given alias
is adequate for a given role, or that one costs less than another: no benchmark was run, and the
relative capability of these checkpoints is asserted by their vendors, not established here. The
tiering is a deliberate, revisable bet, and the thing that would falsify it is a role that starts
missing defects it used to catch — visible as blockers appearing only after a human review.

**Decision — Codex becomes the default author, and the cold fixer.** `CI_AGENT_DEFAULT` defaults
to `codex`, and the cold fixer is pinned to it. Resolving an issue end to end is the pipeline's
longest-running agent task by its configured budget — a 60-minute timeout against the reviewers'
30 — and it is the stage where family diversity is *not* at
stake: whatever writes a PR, all five reviewer roles still read it afterwards, and author
exclusion is enforced by context, not by vendor ([isolation.md](./isolation.md) — model identity
is the secondary boundary there, the context window the primary one). The cold fixer's existing
rationale already said the author's identity buys nothing when there is no session to resume, so
the pin was free to move. Because the agent is chosen at runtime in both stages, each resolves
the model through an `agent_model()` map written inline in its own workflow.

**Why that map is duplicated rather than extracted.** It was a shared script first, and the
fixer died at exit 127 on the very PR that introduced it. The reviewer and fixer jobs run the
pipeline's own logic from **main's** checkout — that is what stops a PR editing the pipeline
reviewing it — so a `scripts/` helper added by a PR does not exist for that PR's own review.
Workflow YAML ships with the PR; a new file in the tree does not. Two copies in the YAML is
therefore the only form that bootstraps, and `tests/test_ci_model_pins.py` asserts they stay
identical, doing offline the anti-drift job the shared file was there to do. It also refuses any
new `./scripts/*.sh` call from those two workflows, so the trap does not get re-set.

**What this is not.** It is not a move to a single-family panel. The split stays three Codex, two
Claude, with `quality` cross-family from `invariant` per D-quality-reviewer, and QP3 now has an
executable check (`tests/test_ci_model_pins.py`) rather than resting on reviewer attention alone.
QP3's surface column is *widened* to include `model:` — a strengthening, so §4 of the register,
which gates weakening a row on new fetchable evidence, does not apply.

**The cost basis, and its limits.** The motivating request was to cut CI API spend. No cost claim
is made here, because none can be supported: this repository has no per-role cost telemetry, no
spend was measured before or after, and vendor list prices are neither cited in the register nor
stable enough to state as fact in a normative document. So the tier assignments are a bet on task
shape, not a demonstrated saving, and the direction of the net change is genuinely unknown.

What *is* established, and is the actual justification for this decision, is the first half: the
pipeline now names the model each role runs, which it previously did not. That claim needs no
citation — it is a property of the diff. Measuring per-role spend, and revisiting these tiers
against evidence rather than against task shape, is an open item below.

**Known risk.** Making Codex the default author makes `codex exec resume --last` the fixer's
common path, and the cold-fixer fallback — which D-resume-timeout added — has to date never
actually fired in production. The timeout containment bounds a hang, but the path stays lightly
exercised, and this change puts more traffic on it.




## D-category-coverage — every material category needs a planted fixture, and coverage is now a test

**The problem.** The audition corpus covered every *lens* and was checked for exactly that
(`test_shipped_corpus_loads_and_covers_both_directions`). Per-lens coverage is not per-category
coverage, and the difference is load-bearing. `grade` scores the relaxed `same_lens` match and
`judge` gates on lens-level rates, so a critic that is wholly blind to one category still clears
every threshold on the strength of the categories its lens does cover. The lens reads as measured;
the blind spot is invisible in the report and in the cached verdict alike.

Three of the eleven non-stylistic categories had no planted fixture: `misrepresented_source`
(evidence, floors `major`), `overstated_claim` (logic, floors `major`), and `unclear_structure`
(completeness, floors `minor`). `misrepresented_source` is the sharpest case. The only instance
the corpus ever contained was an *accidental* one — the statistic `control-sound-01` attributed to
Tomes (1998), which D-control-soundness removed as a defect in a fixture declared sound — so the
category has never been legitimately measured on any model, in either direction. It is also the
category D-source-verification sharpens into a checkable fact once fetched pages are present, which
makes a critic's baseline competence at it worth knowing before that switch is turned on.

**Decision.** Every category whose mechanical severity floor is material — `major` or `blocking` —
carries at least one planted fixture, and `test_every_material_category_has_a_planted_fixture`
enforces it. Two fixtures are added to close the gap this decision found:

| fixture | lens | tier | the planted defect |
|---|---|---|---|
| `misrepresented-source-01` | evidence | obvious | A real, accurately listed source ([Durkin et al. 2022](https://doi.org/10.1037/dev0001301), *… through sixth grade*) is described in the body as reporting twelfth-grade outcomes it cannot contain. The paragraph before it cites the same paper correctly. |
| `overstated-claim-01` | logic | moderate | Randomized support establishing a small average effect is restated as a flat "prevents" with an individual-level expectation attached. The 2017 IPD meta-analysis reports aOR 0.88 (95% CI 0.81–0.96) and NNT 33 (95% CI 20–101) overall, with a markedly stronger effect in the daily-or-weekly dosing subgroup (aOR 0.81 vs. 0.97 for bolus dosing) and below 25 nmol/L baseline (aOR 0.30 vs. 0.75) ([Martineau et al. 2017, fetched via PMC5310969](https://pmc.ncbi.nlm.nih.gov/articles/PMC5310969/)); two later large trials report no reduction ([CORONAVIT, fetched via PMC9449358](https://pmc.ncbi.nlm.nih.gov/articles/PMC9449358/); [Brunvoll et al. 2022, fetched via PMC9449357](https://pmc.ncbi.nlm.nih.gov/articles/PMC9449357/)). |

Both are authored in `prompts.REPORT_SKELETON` shape — no `#` title, `## Conclusion` first, `[n]`
citations, a numbered `## Sources` — because a fixture that does not look like production input
measures the critic on a document shape it never sees.

**Every cited number is fetch-verified, per QP9/QP10.** The first draft cited a 2021 aggregate-data
update (Jolliffe et al., *Lancet Diabetes & Endocrinology*, reporting OR 0.92) alongside the 2017
IPD meta-analysis. Neither its publisher DOI nor its medRxiv preprint mirror returns fetchable full
text through the review pipeline's fetch boundary — both return a 403 or a bare landing page — so
the claim was unverifiable and the `quality` reviewer correctly blocked on it twice
(`qual-claim-unsupported-1`). It is removed rather than patched with a better link, because no
fetchable full text exists for it: `overstated-claim-01` now rests entirely on the 2017 IPD
meta-analysis (fetched via PMC5310969, which hosts the full text openly) plus the two null trials
(PMC9449358, PMC9449357), and the planted defect — S5.P2 flattening a hedged, subgroup-concentrated
finding into an unqualified "prevents" — needs only that one paper's aOR, NNT and two subgroup
splits to be licensed. `misrepresented-source-01`'s citation (Durkin et al. 2022) was already
fetch-verifiable and is unchanged.

**Why the rule stops at the material floor.** `_is_material` gates every hit in `grade`, so a
detection on a minor-floor category scores only when a critic volunteers an escalation above the
floor. Requiring a fixture for `unclear_structure` or `loaded_language` would therefore assert a
measurement the grader cannot reliably make: the fixture would be graded as a miss against critics
that found it and filed it honestly at `minor`. `loaded-language-01` already sits in the corpus and
already has this problem; **it is diagnostic, not a sensitivity measurement**, and the same holds
for any `unclear_structure` fixture. No fixture is added for `unclear_structure` here, and
[concepts.md](./concepts.md) now says which categories the corpus can and cannot score. Whether the
grader should credit a minor-floor detection at all is a separate question about scoring, not about
coverage, and it belongs to the issue that raised it rather than to this one.

**Why not simply require a fixture per category, floor be damned.** Because the resulting corpus
would encode a claim the harness cannot honour. A coverage rule whose satisfaction leaves a
category still unmeasured is worse than an acknowledged gap: it converts "we have not measured
this" into "we have", which is the same substitution D-control-soundness was written to undo.

**Tiering.** `obvious` gates a fail-closed verdict (`obvious_hits == 0` → `unfit`), so it is
claimed only where a competent critic must catch the defect. `misrepresented-source-01` earns it:
the tell is bibliographic and sits on the face of the Sources entry, the evidence lens brief names
this exact case, and the same source is cited correctly one paragraph earlier.
`overstated-claim-01` does not: it is a hedge-drop two sections after the numbers that constrain
it, `prevents` is ordinary shorthand for `reduces the risk of`, and a fast reader can wave it
through without being incompetent. Each fixture's manifest records that reasoning where the next
author will read it.

**Cache and blast radius.** New fixture directories change `corpus_hash`, so every cached verdict
stops matching and drops to *not audited* — never to `unfit`, and `audition.enforce` blocks only on
a positive `unfit`. Safe to land with enforcement on: it degrades to "re-measure", which is correct,
because the corpus now measures something it did not measure before.

**What this decision does not establish.** No model has been auditioned against either fixture —
that costs a paid proxy run. Two things are therefore unknown and should be read as open: whether
rostered evidence critics actually catch an on-its-face misrepresentation, and whether
`misrepresented-source-01` is honestly `obvious`. If a re-audition shows competent models missing it
while catching the other three evidence fixtures, the tier is wrong and should drop to `moderate`
rather than the roster being re-cut around it.

An adversarial review round caught an accidental second instance of this fixture's own category:
three loci attributed third-grade outcomes to source [1] (Puma et al. 2010), whose own follow-up
window is fetch-verified (ERIC ED507845) to end at 1st grade. A doctrine-compliant critic scoring
that would have been graded a miss on an `obvious` fixture for catching the wrong instance, or
credited once for catching both. Fixed by re-citing those loci to the 2012 Third Grade Follow-Up
report (a new source [9], ERIC ED539264, fetch-verified) rather than by deleting the claims — the
report is still allowed to say preschool's advantage fades by third grade, it now just cites the
paper that actually says that. Exactly one planted defect remains, at S4.P3.

**Invariants.** None in reach. Fixtures are test data: they enter no production path, and author
exclusion, the blind `OrchestratorView`, fail-closed lenses, the severity floors and controller
termination are all untouched. The audition already hands fixture text to a critic as untrusted
report content under the sentinel author `AUDITION_AUTHOR`, and these two fixtures use that path
unchanged.


## D-audition-stylistic-parity — the grader counts what triage counts, from one predicate

Found by adversarial review of the audition harness after D-control-soundness, and independently
confirmed by a second reviewer. `audition._is_material` claimed to mirror production —
"Severity after the mechanical floor clamp, which is what triage would count" — and did not.
It computed `max(severity, SEVERITY_FLOOR[category])` and stopped there. Production excludes
`stylistic` from convergence **unconditionally**, in four places: `to_defects` skips it, `tally`
skips it, `defect_provenance` skips it, and `clean_records` excludes it "even if the critic
escalated its severity".

Escalation is not hypothetical. `validate_issue` checks category scope, locus existence and
verbatim spans; it never checks severity, and RC-005 says clamps go up only — so a critic may
legally report a `stylistic` issue at `major`, and `stylistic` is in `LENS_CATEGORIES` for every
lens. Two measurements diverged from the thing they claim to measure:

- **Sensitivity was inflated.** `grade` credits a defect as found when any in-lens material issue
  lands in the locus window, and `obvious_hits` — the input to the fail-closed "found 0 of N
  obvious" gate — uses that relaxed form. A `stylistic` note filed at `major` on the planted
  paragraph scored as a detection. In a real run the same finding is discarded before the
  controller sees anything, and the planted defect sails through. The harness would have reported
  a critic as sighted on precisely the artifact it was blind to.
- **Noise was inflated.** `material_issue_count` counted the same finding on a control, feeding
  `control_material_rate` and its `unfit` gate, whose reason string reads "runs would stagnate
  rather than converge". A stylistic finding cannot stagnate a run: it is absent from the tally
  that `signal_signature` keys on and it never withholds a clean record. The gate would fail a
  usable critic for findings production throws away.

Both errors point the same way — toward believing the audition rather than the run.

**Decision.** The exclusion is not restated in the audition. `taxonomy.counts_for_convergence`
is now the single definition of "material issue": `stylistic` is out before severity is read,
everything else counts at its clamped severity. `audition._is_material` delegates to it, which
fixes `grade`'s `same_lens` and `material_issue_count` together, and `triage.clean_records` and
`triage.defect_provenance` were rewritten to call it — behavior unchanged, but the two consumers
now cannot disagree without one of them being edited on purpose.
`test_grader_materiality_agrees_with_triage_for_every_category` asserts the parity directly, over
every (lens, category, severity) a critic could legally report, rather than over the one case
the previous test happened to cover (`stylistic` at `minor`, where the old code was accidentally
right).

**Not changed.** Triage and the controller were already correct, and the fixture corpus is
untouched — `corpus_hash` deliberately does not move, because nothing about what is being
measured changed, only how a critic's answer is scored.

**Known gap: cached metrics predate this rule.** The audition cache is keyed on
`corpus_hash`, `prompt_hash` and `repetitions`, none of which this change touches, so entries
recorded before it survive and are still read by `ra doctor` and by the `audition.enforce`
startup gate. Those numbers were graded by the old predicate and can be wrong in both directions
— an inflated `obvious_hits`, an inflated `control_material_rate`. `ra audition --force` re-grades
a slot. Extending the cache key to cover the grader's own identity is a caching-semantics question
tracked separately and deliberately not solved here.


## D-audition-failure-coverage — a verdict covers every fixture it owed, or it is `unfit`

**The problem.** `audition.run_assignment` deliberately separates "cannot emit the schema" from
"looked and saw nothing": a failed lens increments `schema_failures` and skips grading, so it is
neither a miss nor a false positive. That separation is right — the two have different fixes, one
a prompt or output-mode problem and the other a reason to replace the model — and
`test_failed_lens_counts_as_schema_failure_not_as_silence` pins it. The accounting that followed
from it leaked.

Failed calls also vanished from every *denominator*. `planted_total`, `obvious_total` and
`control_runs` grew only on successful calls, so a fixture a model reliably broke on was deleted
from that model's own exam:

- An evidence assignment is 5 fixtures x 3 repetitions = 15 calls. A model that fails all three
  repetitions of one planted fixture — deterministically, because that artifact's content drives
  it out of schema — sits at exactly 3/15 = 20%, which the strict `schema_failure_rate >
  max_schema_failure_rate` gate admits.
- That fixture then contributes nothing to `planted_total` or `obvious_total`. Catching the rest
  and staying clean on the controls yields **`fit`**, confirmed by direct simulation.
- The noise direction censors more quietly still. `_ratio` returns 0.0 for a zero denominator,
  meaning "not measured", and `judge` gates the noise checks on `control_runs` — so a model that
  breaks on controls specifically switched those gates *off* rather than failing them, and its
  `control_material_rate` read clean.

The result was a verdict whose headline rates were computed over a corpus subset the model had
selected by failing, which is the exact inverse of the harness's fail-closed posture.

**Decision — coverage is tracked per fixture and gates before any rate.** `Metrics` gains
`fixtures_owed` (the size of `for_lens`, controls included) and `uncovered_fixtures` (the ids that
never produced one gradable review across every repetition). `judge` returns `unfit` when
`uncovered_fixtures` is non-empty, naming them. Coverage is checked *after* the schema gate and
*before* everything else, because coverage is what the remaining rates are over.

Per fixture, not per call, is the load-bearing part. "20% of calls failed" cannot distinguish a
model that stumbles once on each of five fixtures — a flake, correctly tolerated by the schema
threshold — from one that is deterministically broken on a single fixture and therefore never
measured on it at all. Only the second censors a denominator, and only a per-fixture count sees it.

**`unfit`, not `insufficient`.** Both were arguable and the choice follows the reasoning the schema
gate already uses one block earlier: a model asked `repetitions` times that returned nothing
gradable every time has produced a definite, reproducible failure, not an absence of evidence.
`insufficient` means "we did not ask enough" — `calls == 0`, or a corpus with nothing to grade —
and reporting a reproducible break as a gap in our own measurement would put the deficiency on the
harness. It also matters at the gate: `audition.enforce` blocks only on `unfit`, and a model that
cannot review one of the artifacts it will be handed in production is exactly what that gate is
for.

**The rule is uniform across planted fixtures and controls, and is not tunable.** The narrower
form considered was "any planted fixture, or *all* controls", on the theory that controls pool
into one rate and losing one of two only shrinks the sample. It was rejected: in both directions
the fact measured is the same one — this model reliably cannot produce a gradable review of this
artifact — and in production that means the report gets no review from that critic on that lens.
A uniform rule is also the one an operator can state without a footnote. There is no threshold,
for the same reason the zero-obvious rule has none (D-critic-audition): a rate no call contributed to
is not a lenient measurement, it is the absence of one.

**`schema_failure_rate` stays the separately reported cause, and stays `>`.** Failures are not
folded into misses; the schema gate still fires first, so a wholly broken model is still reported
as a mechanical problem rather than sent to an operator as "never graded 6 fixtures". The
20%-exactly boundary was reconsidered and deliberately left alone. `>` is what a field named
`max_…` should mean, and it is what `max_control_material_rate` already means, while
`min_obvious_sensitivity` admits its named bound from the other side — changing one of the three
would make the set read inconsistently. More to the point, the boundary was only load-bearing
because of the censoring: with coverage gated, a model sitting at exactly 20% because of three
scattered flakes is a model that was measured on everything, which is what the threshold is
calibrating for.

**`fixtures_owed` is a required field, so old cache entries are dropped.** A record that cannot
say what it owed cannot say whether it measured all of it. Defaulting it to 0 would have made the
coverage gate vacuous for every verdict written before this change, i.e. fail-open for the
`max_age_days` window on precisely the entries the finding is about. Required means
`Metrics.model_validate` rejects them and `load_cache` — which already treats any unreadable entry
as absent — degrades them to *not audited*. Same blast radius as D-control-soundness: the
`enforce` gate blocks only on a positive `unfit`, so this is safe to land with enforcement on, and
the correct reading of a pre-coverage verdict is "re-measure".

**Reporting.** `ra audition` gains a `cover` column (`covered/owed`, red when short) beside the
rates it qualifies, and `--json` carries both fields through the existing `model_dump`. The
`OrchestratorView` is untouched — audition metrics never enter the controller's context.

**What this does not do.** It does not change the fixtures, the thresholds, or the grader.
`refine_audition` has its own `Metrics` with the same shape of denominator and was left alone as
out of scope; whether it censors the same way is an open item below.

## D-audition-rubric-identity — a cached verdict names the grading rules that produced it

`CacheEntry.matches` keyed a stored audition result to `(corpus_hash, prompt_hash, repetitions)`.
That triple is the right *philosophy* — D-critic-audition's cache exists so a verdict is never
carried across a change in what the measurement means, and D-control-soundness relies on exactly
that when it says a corpus edit "degrades to re-measure" — but the triple was incomplete. Two
verdict-affecting inputs sat outside it, and both are read by `ra doctor` and by the
`audition.enforce` startup gate for up to `max_age_days` (30 by default).

**`require_verbatim_spans`.** The CLI already passes `config.require_verbatim_spans` into
`run_assignment`, and `triage.validate_issue` fails a whole lens closed on a quote that is not
verbatim. Flipping the flag changes what a critic is able to score at all, so a score measured
under one regime is not evidence about the other — in either direction. It is now a stored field
on the entry and a term in `matches()`.

**The grading rules.** `_is_material`, `_locus_matches`, `LOCUS_PARAGRAPH_TOLERANCE`,
`SEVERITY_FLOOR`, `SEVERITY_RANK`, `LENS_CATEGORIES` and `run_assignment`'s counter accounting
together decide what a call is worth. None was hashed, so a deployed change to any of them — a
severity floor moved, a category re-scoped to another lens, the locus window widened — left every
stored verdict trusted for a month though it had been produced by rules that no longer exist. A
new `rubric_hash()` is now a fourth term in `matches()`.

**How `rubric_hash` is built, and why it is half automatic and half by hand.** The issue offered
a choice: a hand-bumped constant, or a hash derived from the taxonomy tables. Both were taken,
mixed into one digest — the shape `refine_prompt_hash` already uses, where a hashed prompt surface
is combined with a hand-bumped `PROMPT_VERSION`.

The rules that are *data* are hashed from the tables directly (`LENS_CATEGORIES`, `SEVERITY_FLOOR`,
`SEVERITY_RANK`, `LOCUS_PARAGRAPH_TOLERANCE`). Deriving that part of the identity from its source
data removes the maintenance risk that a table edit in `taxonomy.py`, away from the grader, lands
without the separate manual version bump. The `Metrics` field set is hashed for the same reason: a
new counter defaults to 0 on every older entry, and a `judge` gate reading it would score a stale
entry as a measured zero rather than as an absence.

The rules that are *code* — `grade`, `_is_material`, `_locus_matches`, `run_assignment`'s
accounting — carry `RUBRIC_VERSION`, a constant with a comment listing what requires a bump.
**Rejected: hashing `inspect.getsource` over those functions.** It is automatic and cannot be
forgotten, which is the whole argument for it. It was rejected because `audition.py` is
deliberately comment-dense — the reasoning is the documentation — and an audition costs
|models| x |fixtures| x repetitions calls against a paid, rate-limited proxy. Billing a full
re-measurement of the roster for a typo fix in a docstring conflicts with the operational goal of
invalidating only when measurement semantics change. It also breaks under a source-less install.
The chosen trade is a manually maintained constant for code rules, with automatic hashing where
the rubric is already represented as data.

**Not covered, deliberately: `judge`'s gate order and `AuditionThresholds`.** Neither is stale-able.
The cache stores `Metrics`, not a verdict; `judge(entry.metrics, cfg.thresholds)` runs at read time
against live thresholds, so a gate reorder or a retuned threshold already takes effect on the next
read without any invalidation. Hashing them would force a paid re-measurement to obtain a verdict
the current code would compute for free from the data already stored.

**Backward compatibility degrades to *not audited*, never to a pass.** `rubric_hash` and
`require_verbatim_spans` are required fields with no defaults, so an entry written before they
existed fails `CacheEntry.model_validate`, and `load_cache` already drops what fails to validate.
A pre-rubric `.ra-audition.json` therefore reads as an empty cache: every slot shows *not audited*
in `ra doctor`, and the `enforce` gate — which blocks only on a positive `unfit` — passes. That is
the same direction D-control-soundness established for a corpus edit, and it is safe to land in a
deployment running with enforcement on. A default value would have been the unsafe choice: it
would have asserted a rubric the entry never recorded.

`cached_judgements` and `enforce_fitness` take `require_verbatim_spans` as a required argument
rather than defaulting it, because the flag lives on `Config` while those functions take
`AuditionConfig`, and both callers hold a `Config`. A default would let a caller silently compare
against the wrong regime — the defect this decision closes, reintroduced one layer up. The gate
still takes no `LLMClient`, and `test_the_gate_takes_no_client_and_so_can_never_spend` still pins
that.

**Deferred: storing per-call graded results so a rubric bump can regrade for free.** The cache
holds aggregated `Metrics`, so any invalidation — corpus, prompt, or now rubric — forces a full
paid re-measurement even when the raw calls would answer the new rules perfectly well. Storing raw
`LensResult`s per fixture per repetition would make a rubric bump a free regrade, and would make
the `judge`-reads-a-new-counter case free too. It was deferred rather than rejected: it changes the
cache from a small verdict record into a corpus of stored model output (roughly two orders of
magnitude larger, and containing critic prose about fixture text), which raises retention and
schema-migration questions this issue should not settle in passing. Recorded as an open item below.

## D-obvious-per-lens — every lens carries an obvious-tier fixture and a locus-anchored one, and "never clean" fails closed

**The problem.** Found by adversarial review of the audition harness after D-control-soundness, and
confirmed independently by simulation. Both fail-closed sensitivity gates in `audition.judge` are
keyed to planted defects on `tier: obvious` fixtures:

- the hardcoded one — `if metrics.obvious_total > 0 and metrics.obvious_hits == 0: → UNFIT`, the
  llama-4-scout signature, and
- the `min_obvious_sensitivity` threshold, itself guarded by `if metrics.obvious_total`.

The `completeness` lens had exactly two planted fixtures, `omitted-counterargument-01` and
`unexamined-presupposition-01`, and both were `tier: moderate`. So `obvious_total` was **zero on
every completeness assignment** and both gates were structurally dead. A critic that returned zero
issues on every call it ever made — the precise failure D-critic-audition was built to catch — scored:
obvious gates skipped, controls clean (it invents nothing because it says nothing), `lens_sensitivity`
0% against a warn threshold, verdict **MARGINAL**. `enforce_fitness` blocks only on `unfit`, so even
a deployment running `audition.enforce: true` would have started with a silent critic staffing one of
its three lenses.

A second hole ran through the same two fixtures from the other direction. Both set `anywhere: true`,
which skips the locus window entirely, so the completeness lens measured only *did the critic name a
category from my lens*, never *did it find the defect*. A degenerate critic that reflexively raises
one material `omitted_counterargument` on every artifact therefore scored 100% `lens_sensitivity` on
completeness — and exactly `1.00` on `control_material_rate`, which is not *greater than* the `1.0`
default, so the noise gate passed it too. Perfect sensitivity, MARGINAL verdict, no block. The
completeness rubric could fail neither a never-fire critic nor an always-fire one.

Neither hole was visible to a test. `test_shipped_corpus_loads_and_covers_both_directions` asserted
only that each lens has *a* planted fixture.

**Decision, part one: an obvious-tier fixture per lens, and a locus-anchored defect per lens.**
`omitted-counterargument-02` is added to the corpus: `lens: completeness`, `tier: obvious`,
`anywhere: false`, anchored at `S1.P1`. Its report recommends raising a rural interstate speed limit
and never mentions crash risk, injury or fatality anywhere — not a section, not a sentence, not a
source. That is the whole of the defect, and it is what makes the tier honest: the fixture is not
merely thin. It carries a real `## The strongest counterargument` section that engages the
reference-point objection on the merits, it names its own weakest argument as weak, and it decomposes
the measurement it rests on, so a critic cannot score by flagging "needs more detail". It is written
in production report shape (D-report-template) from the start rather than in the pre-template shape
the older fixtures still use.

Two corpus tests pin both properties per lens — `test_every_lens_has_an_obvious_tier_fixture` and
`test_every_lens_has_a_locus_anchored_planted_defect` — so neither can regress the next time fixtures
are edited. They are corpus assertions, not grader assertions: they fail at the fixture, which is
where the defect was.

**Why this fixture is anchored when the other two are not.** `anywhere` exists because an omission
often has several honest loci and grading against one of them measures agreement with the fixture
author's filing choice. This omission has a dominant locus. `S1.P1` is the only paragraph that states
the recommendation, and the only one that asserts what the strongest objection *is* — the claim the
absent objection most directly bears on, which is exactly what `prompts._CATEGORY_ANCHOR` instructs an
`omitted_counterargument` critic to quote (D-absence-anchor). Every other section is descriptive.

**The residual risk, recorded rather than hidden.** A critic that files the omission against the
`## The strongest counterargument` section instead scores a miss, and with `obvious_total` of 1 on
this lens that alone reads `unfit` — the shape of failure D-control-soundness was: a lens made
structurally unpassable by its own corpus. Two things bound it. The shipped posture is
`audition.enforce: false`, so the consequence is a warning naming the count ("found 0 of 1 obvious
planted defects"), which is inspectable. And the pre-registered remedy is to add a *second* obvious
completeness fixture — an `anywhere: true` one, which the coverage tests permit once an anchored
fixture exists — rather than to relax this one, because `obvious_total` of 2 turns a locus
disagreement into 50% obvious sensitivity (marginal) while still grading silence 0% (unfit). That
remedy is not taken pre-emptively: it costs a fixture's worth of paid calls on every audition to
insure against a miss no measurement has yet observed.

**Decision, part two: "never clean" fails closed, and the control-rate boundary does not move.**
The always-fire strategy lands on exactly `1.00`, and the obvious repair is to change
`control_material_rate > max_control_material_rate` to `>=`. That is **rejected**, twice over.

It leaves the hole open to configuration. The doctrine established for the silence direction — and
pinned by `test_silent_critic_is_unfit_under_every_threshold_setting` — is that the strategy which
most completely defeats the harness must not be reachable by tuning. Under `>=` the always-fire critic
is blocked only while `max_control_material_rate` happens to sit at `1.0`; an operator who loosens it
to `1.5`, which looks like an ordinary calibration, re-opens it exactly. And it silently re-tightens a
calibrated threshold for every deployment, changing the configured number's meaning from "more than
one invented issue per sound report" to "one or more", in a region the D-control-soundness data does
not speak to — the rates measured there were 1.67 to 4.17, none near the boundary. A knife-edge fix
for one arithmetic coincidence is not a fix for the class.

What is added instead is a second **hardcoded** gate, the mirror of the silence gate: `control_runs`
non-zero and `control_clean_runs == 0` is `unfit`, whatever the thresholds say. It states the property
that actually matters, which is not *how noisy* but *did this critic ever once let a sound report
through in this audition*. The gate is a conservative fail-closed policy against the demonstrated
always-fire strategy: zero clean results on the configured controls is sufficient to reject that
audition, without claiming the observations predict how the critic would behave on another sound
report. Ordering: the new gate sits *after* the `max_control_material_rate` check, so the existing and
more informative "invents N material issues per sound report" reason still wins wherever it applies,
and the new one speaks only where the rate gate is silent. `control_clean_rate` as a *threshold* was
considered and rejected for the same reason `>=` was: a tunable knob at the one point where the
harness must be untunable.

This is deliberately not a judgement about noise in degrees. A critic clean on some sound reports and
not others is `warn_control_material_rate`'s business and still grades `marginal`. The gate fires only
on *never*, across a base of two control artifacts times the configured repetitions — six evaluations
at the default, and only two when `repetitions: 1`. Those repeated observations are not treated as
independent samples or generalized beyond the audition corpus. The thinness of that base is a real
limitation and is already an open item below (a third control fixture).

**Cache and blast radius.** Adding a fixture changes `corpus_hash`, so every cached verdict stops
matching in `cached_judgements` and reads *not audited* — never `unfit`. `enforce_fitness` blocks only
on a positive `unfit`, so this lands safely even with enforcement on: it degrades to "re-measure",
which is correct, because a verdict from the old corpus is a claim about a measurement that no longer
covered the completeness lens's obvious tier at all.

**Invariants.** None touched. The audition harness sits outside the run graph — it never reaches the
controller, `OrchestratorView`, author exclusion (it pins the model under test on purpose, and uses the
`AUDITION_AUTHOR` sentinel), or the severity floors, which the grader reads but does not change. The
grader stays a pure function with no LLM in it. The new fixture is untrusted data that enters a critic
context through the same fenced `critic_user` path a real report does, and the fail-closed lens
contract is unchanged.

**What this does not establish.** The new fixture has not been auditioned against real models — that
costs a paid proxy run — so the `obvious` tier claim rests on reading the artifact, as every tier claim
in this corpus does. Nor has the never-clean gate ever fired on live data: every `unfit` verdict
recorded so far came from the rate gate, and this one exists to catch a strategy that was demonstrated
by simulation rather than observed in a roster.


## D-reviewer-confidence-field — the finding arrays admit the field the prompts make reviewers compute

**The problem.** The review pipeline was NO-GOing PRs that every reviewer who completed had
approved. A reviewer attached `confidence` to a `non_blocking_notes[]` entry; that array's items
set `additionalProperties: false` and did not admit the field; ajv rejected the *whole* artifact;
the judge then failed closed on a selected role that produced no valid artifact. On 2026-08-01
this hit six of the eight open resolver PRs (#123–#130) for roughly seven wasted review cycles,
sometimes burning both cycles of a single PR. It is intermittent per role invocation — the model
sometimes writes the number down and sometimes does not — so #124 and #125 passed the same
pipeline the same day.

The cause is a contradiction inside the reviewer contract, not a misbehaving model. Every reviewer
prompt states the **0.7 confidence ladder**: a finding the reviewer is less than 0.7 confident in
belongs in `non_blocking_notes[]` rather than `blocking_issues[]`. Deciding which array a finding
goes in is therefore done in terms of a number the reviewer has in hand — and `confidence` is
*required* on every `fix_suggestions[]` entry, so it is already in the output contract one array
over. The schema then forbade the field on exactly the arrays the prompt had it reasoning about.
A reviewer that recorded the number it was told to use lost every finding it had.

**Decision — admit `confidence` on both finding arrays, bounded `[0, 1]`, nullable on notes.**
Admitted rather than forbidden: the number is real signal a reader of the artifact wants, and
refusing it costs not one finding but all of them. Nullable on `non_blocking_notes[]` matches
`severity` and `source` there. The bound stays live, so `confidence: 1.5` is still a failure.

**Decision — the two finding arrays must admit the same fields, enforced mechanically.**
`blocking_issues[]` and `non_blocking_notes[]` describe the same findings at different confidence;
the ladder is literally "same finding, other array". This is the fourth instance of one class —
`id`, `decision_ref` (#29), `category` (#35), `confidence` (#75, then #131) — and each was fixed by
admitting the one field, which closes an instance, not the class. `reviewer-v1.json`'s own comment
says `severity` and `source` were admitted "pre-emptively to close the class"; that pre-emption
used the wrong frame. The leak-prone set is not *fields a blocker has* but **every field named
anywhere in a reviewer's output contract or prompt**, which is why it missed the one field required
on `fix_suggestions[]` and cited in all five prompts.

`.github/scripts/review/schema-parity.test.mjs` closes the class in the gate that already runs on
every change under `.github/scripts/review/**`:

- the two arrays' property sets must stay **equal** — admitting a field to one fails PR validation
  until it is on both;
- every descriptive field **required on `fix_suggestions[]`** must appear on both arrays — the rule
  that would have caught `confidence` before it shipped;
- neither array may switch to `additionalProperties: true`, which would make the parity assertion
  vacuous while silently accepting hallucinated fields;
- `confidence` keeps its `[0, 1]` bounds on both.

A deliberately one-sided field is still allowed: it goes in the test's `ASYMMETRIC` map with its
reason, which makes the exception a reviewed act rather than an oversight.

**What this is not.** The judge's fail-closed aggregation is untouched and is not being weakened.
A reviewer that fails must block the merge rather than drop out of the review set — that behaviour
was correct, and it is what made a self-inflicted schema bug visible instead of silently shrinking
the panel. What changes is only that a reviewer following its own prompt no longer produces an
invalid artifact. The prompts' confidence ladder is likewise unchanged; the schema is the side that
was wrong. Nor is this a general tolerance layer: unknown properties still fail closed, in the same
narrow spirit as the `maxLength` normalizer, which shortens over-long strings and deliberately
never drops an unknown field.

**Known limit.** Reviewer artifacts are validated against **main's** copy of the schema, so this
change does nothing for its own reviewers — the flake can still kill a role on the PR that fixes
it. That is what happened to the first attempt at this fix (PR #81, closed unmerged 2026-07-30,
NO-GO'd on cycle 1 by this exact bug class hitting its `invariant` reviewer). The limit is recorded
in [ci-pipeline.md](./ci-pipeline.md) rather than worked around, because the alternative — validating
against the PR's own schema — would let a PR relax the contract its own review is checked against.


## D-audition-source-mode — the audition measures the source-less floor, and the verdict says only that

`audition.run_assignment` calls `critique_once` with `sources=None`, on every fixture, for every
lens. The production deployment runs `verify_sources` always-on
([deployment-profile.md](./deployment-profile.md)), so its evidence critic reads a prompt this
harness never builds: `misrepresented_source` sharpened from *"the cited source plainly does not
support the claim"* into *"the fetched page does not contain the claim"* (`prompts.critic_user`),
the pages themselves in a `fetched_sources_block` with its three entry shapes (D-existence-vs-body),
and the standing instruction not to re-raise a definitive not-found that
`triage.mechanical_citation_issues` has already minted (D-notfound-fabrication). The verdicts are
named `fit` and `unfit`, which read as unconditional. They are not, and until now the gap was
recorded nowhere.

**Decision. The audition measures the capability floor a critic brings with no source access —
deliberately — and that scope is now stated in the code, carried in the cache identity, and
written here.** Four reasons, in the order they carry weight.

**The floor is real, not hypothetical — and it sits strictly below production's failed-fetch
case, not level with it.** `sources=None` matches exactly one production state: a report with no
citations to check at all. It does not match a paywalled, blocked or offline citation, because
fetching is best-effort by construction — sites block automated clients, paywall bodies, serve
formats the extractor cannot read, or go offline, and this system refuses the tricks that would
get around that (D-existence-vs-body) — but a failed fetch is still a *fetch attempt*, and
`fetched_sources_block` still renders an entry for it, telling the critic to judge that citation
*on its face*. A critic under `sources=None` never sees that instruction, or the fact that a
citation was attempted at all. A critic that cannot find a defect with no source scaffolding
whatsoever is therefore failing a strictly harder bar than any real evidence critic runs against
— which is why an `unfit` here is trustworthy evidence of a problem, even though a `fit` cannot
promise the model would also succeed once handed even a failed-fetch entry.

**One definition of "the prompt", across all three lenses.** The logic and completeness lenses
receive no sources under any configuration. A harness that fed a packet to evidence alone would be
taking two different measurements and printing both as `fit`, and the position-aware roster
warnings compare verdicts across lenses.

**Determinism.** `corpus_hash` keys every cached verdict to the exact bytes of the corpus. A
measurement whose inputs depended on what the network returned that day would be keyed to nothing,
would differ between machines, and would rot as cited URLs die — the same reason the whole test
suite is offline.

**The direction the gate actually uses survives the narrowing.** `audition.enforce` blocks only on
a positive `unfit`, and `unfit` here means the model found nothing obvious in text handed to it
directly, with no source scaffolding at all. In principle that is over-strict — a model could be
blind with nothing and sharp once handed even a failed-fetch entry — and that risk is accepted
because it fails toward re-rostering, which is this project's posture: a model that cannot pass
the harder bar is not thereby known to fail the easier one, but nothing here is claiming it does.

**What a `fit` verdict certifies.** That the model raises material, correctly-anchored, in-scope
findings against the artifact text alone, and does not invent them against a sound control, with
no fetched-source scaffolding of any kind in its context. For the evidence lens specifically, that
is a floor strictly below the on-its-face standard production actually runs when a citation is
attempted and its body does not arrive — that case still gets a `fetched_sources_block` entry
naming the failure, which this measurement never exercises.

**What it does not certify, and no threshold change would.** Three things, all of them real:

- **Use of fetched page text.** The sharpened `misrepresented_source` — the strongest check the
  production evidence lens has — is never exercised. (#118 covers the unfetched form of that
  category, which the corpus also lacks; the fetched form needs the packets below.)
- **The discipline of the fetched-sources block.** Not re-raising the `NOT FOUND` case triage has
  already recorded (a duplicate at the blocking floor), not reading `BLOCKED` as fabrication, not
  reading a metadata-only entry as a body. Each is a failure mode D-notfound-fabrication and
  D-existence-vs-body exist to prevent, and this harness can see none of them.
- **The noise direction with a page in context.** Sensitivity plausibly only improves when a critic
  is handed evidence. Over-flagging does not: a fetched page is more surface to over-read, and
  `control_material_rate` is measured without one.

**`fabricated-citation-01` stays `tier: obvious`, and is not measuring a superseded capability.**
Only an HTTP-definitive not-found is settled mechanically (D-notfound-fabrication); a fabricated
citation whose URL is blocked, paywalled, or resolves to an unrelated live page leaves the
judgment exactly where this fixture puts it — with the critic, on the face of the text. What the
fixture cannot measure is the duplicate case in the list above.

**`prompt_hash()` now describes what it covers.** Its docstring claimed "every prompt surface a
critic sees" while hashing `critic_user(lens, "q", "body", None)` and nothing else. The claim is
corrected rather than the coverage widened: the hash covers the surface the harness measures, plus
`AUDITION_SOURCE_MODE` as an explicit component of the identity.

*Rejected: hashing the sources-present surface too.* It would invalidate every cached verdict
whenever anyone edited a prompt fragment no measurement had ever used — discarding results that
remain exactly as true as the day they were recorded — and would advertise a coverage the corpus
does not have. The mode tag is what makes the narrower hash safe: a sources-present mode cannot
inherit these verdicts, because it will not key to them.

*Blast radius.* Introducing the tag changes the hash once, so every existing cached verdict stops
matching and reads *not audited* until `ra audition` is re-run — never `unfit`. The gate blocks
only on a positive `unfit`, so this is safe to land in a deployment running with enforcement on.

**Rejected: mirroring deployment by fetching the fixtures' own citations.** The planted citations
are fabricated by construction, so fetching them would measure how today's internet answers a
made-up URL — a 404 from a dead domain one week, a parked page the next — and the control
fixtures' real citations would rot on their own schedule. `ra audition` is a live command and may
spend proxy calls, but the corpus it grades against has to stay a fixed, hashable artifact that is
identical on every machine.

**Not done, deliberately: offline source packets.** Closing the gap for real needs no network — a
`sources.yaml` beside a fixture's `artifact.md`, deserialized into `FetchedSource` values covering
the outcomes that matter (a body that supports the claim, a body that does not, a `BLOCKED`, a
`NOT FOUND`, a metadata-only record), fed through the same `prompts.critic_user` call, and keyed in
the cache under a different `AUDITION_SOURCE_MODE`. It is a corpus change that belongs next to the
fixture work in #118 rather than bolted onto a scoping decision, so it is an open item below. The
mode tag is the seam it plugs into.


## D-minor-floor-fixtures — an audition fixture plants only what the grader can credit

Found by adversarial review of the audition harness after D-control-soundness, and confirmed by a
second reviewer through direct simulation of `grade`.

**The problem.** `tests/fixtures/audition/loaded-language-01` planted a `loaded_language` defect.
Every detection credit in `audition.grade` requires `_is_material` — severity at or above `major`
after the floor clamp — because that is what triage counts and what a report would ever be revised
for. `loaded_language` floors at **minor**, deliberately and for a good reason (D-social-bias): it
is the most judgment-laden bias category, and a material floor would let one noisy critic force a
rewrite every round. So a critic that reported *the planted category, at the severity the taxonomy
assigns it* scored `strict = False` and `same_lens = False` — blind — and its `lens_sensitivity`
fell, which is a warn-level input to `MARGINAL`. The fixture measured willingness to escalate, or
to relabel as `overstated_claim`, not detection of the defect it declared. A rubric that penalizes
the doctrinal reading of a category is measuring the taxonomy, not the model.

Note what this is *not*: it is not an argument for raising the floor. The production pipeline
already accepts the consequence of a minor floor here — D-scoped-revision records framing lock-in
as a known residual precisely because a `loaded_language` finding is not material and does not
force a revision. A fixture whose planted defect no run would ever act on cannot be the thing that
grades a critic's fitness for runs.

**Decision.** The fixture is re-planted as `overstated_claim` and renamed
`tests/fixtures/audition/overstated-claim-02`. `-02` rather than `-01`: D-category-coverage landed
concurrently and independently claimed `overstated-claim-01` for its own fixture (a vitamin-D
report restated from a hedged, subgroup-concentrated effect into a flat "prevents"). Two
`overstated_claim` fixtures at `tier: moderate` is not redundant coverage — different domains and a
different defect shape, a counterfactual-certainty claim here against a hedge-drop there — so both
stand. The evaluative wording ("squandered", "a boondoggle of an outreach initiative") is
**removed** rather than left beside the new defect, and the "Spending and execution" section now
asserts a counterfactual certainty — the appropriation's size "was never the binding constraint",
"no increase in funding could have delivered the 300 beds on schedule" — that the two findings it
rests on do not establish. `docs/bias.md` §3 already routes exactly this case: framing that changes
the strength of a claim is `overstated_claim`, not `loaded_language`. Removing the wording rather
than keeping both is the point of the change: leaving it would have preserved a reading of the
locus on which a doctrine-compliant critic still scores zero. The artifact is also rewritten into
`prompts.REPORT_SKELETON` shape (`## Conclusion` first, `## Key findings`, `## The strongest
counterargument`, topical sections, `## Sources` last, no top-level `#` title) — the precedent
D-obvious-per-lens and D-category-coverage set for new fixtures in this corpus — rather than left in
the older `# Title` shape it previously used, so a critic is graded on the document shape production
actually hands it.

**The rule is mechanical, not a note.** `_check_planted_floor_is_material` refuses at load any
planted category whose floor is below `MATERIAL_FLOOR` — today `loaded_language`, `stylistic`,
`unclear_structure`. This is the same shape as `_check_control_manifest` under D-control-soundness:
the corpus property that review kept failing to hold becomes something the loader holds. It says
only that such a category is not measurable *by this grader*, whose single bar is materiality. This
supersedes D-category-coverage's characterization of `loaded-language-01` as "diagnostic, not a
sensitivity measurement": that fixture no longer exists, and nothing planting a minor-floor category
can exist in this corpus going forward — not diagnostic, unrepresentable.

**`severity_agreement` was not a rate.** The same code carried a second defect, and the
loaded-language case was the one that exposed it: `severity_agreements / strict_hits`, with
`severity_agrees` computed independently of material detection, so a non-material report of a
minor-floor category incremented the numerator while contributing nothing to the denominator and
the ratio could exceed 1.0. It is now derived from the same issue list that decides `strict`, so
the numerator is a subset of the denominator by construction. Second, agreement was equality with
the floor, which scored a **legal escalation** — `blocking` proposed on a major-floor category, the
one direction RC-005 permits — as *dis*agreement. It is now "at or above the floor": the metric
asks whether the clamp had to lift the critic's judgement, which is the question worth asking. The
name is kept over `exact_floor_rate` because the corrected definition is agreement-with-the-floor,
not equality-with-it. Nothing reads the metric — it is neither displayed by `ra doctor` nor gated
on — so this is a correctness fix ahead of a consumer, not a behaviour change.

**`RUBRIC_VERSION` bumped to 2 (D-audition-rubric-identity).** `grade`'s `severity_agrees`
computation is exactly the "strict / same-lens / severity-agreement matching rules" category that
decision names as requiring a hand bump. A cached `Metrics.severity_agreements` recorded under
version 1 could exceed `strict_hits` — the bug this decision fixes — so it must not be read as
though it meant the same thing as a version-2 count. The bump invalidates every stored entry via
`rubric_hash`, which is safe under `audition.enforce`: entries drop to *not audited*, never to a
false `unfit`.

**Coverage cost, stated plainly.** The corpus no longer covers `loaded_language`, and it is
**deliberately uncovered** by the gating corpus — this is the record of why. Covering it needs a
non-gating diagnostic channel — a correct-category, correct-locus, floor-severity report credited
in a `diagnostic_hits` metric excluded from `planted_total` — which was rejected here for two
reasons. It changes what `ra audition` reports and what `ra doctor` shows, so it is its own decision
with its own consumer; and D-critic-audition's argument against an inert `enabled` flag applies to
an inert metric too. It is an open item below.

**Rejected: keep the fixture and document that it measures escalation.** Weakest of the three
options considered. `lens_sensitivity` is not a diagnostic-only number — it is compared against
`warn_lens_sensitivity` and produces a `MARGINAL` verdict — so "documented" would mean a
doctrine-compliant critic is knowingly marked down in a gated metric.

**Cache and blast radius.** Editing the corpus changes `corpus_hash`, so every cached verdict stops
matching in `cached_judgements` and drops to *not audited* — never to `unfit`. Safe under
`audition.enforce`, which blocks only on a positive `unfit`, for the same reason D-control-soundness
was: a verdict from the old corpus is a claim about a measurement that no longer exists.

## D-inherit-whole-range — a verdict is inherited by what the push contains, not by the shape of its head

Found by reading three pipeline runs on 2026-08-01/02 (#126, #127, #130) that all reported
"introduces no new content" over pushes that plainly did.

**The problem.** `review-pipeline.yml`'s merge-from-base short-circuit classified a push by the
head commit alone:

```bash
parents=$(git rev-list --parents -n 1 "$SHA" | wc -w)
if [ "$parents" -ge 3 ]; then
  if git merge-base --is-ancestor "${SHA}^2" "origin/${BASE_REF}"; then
    # ... inherit the prior verdict, run no reviewer
```

Nothing looked underneath that merge. The comment above it said the optimisation exists so "a
resync should not burn a cycle" — the right goal, tested with the wrong predicate: it asks *is
the head a resync commit*, not *is the push only a resync*. So a push of `content, content,
git merge origin/main` had the head of a pure resync and the body of a normal change, and the
pipeline took the inherit path: no reviewer role executed, and the prior verdict was re-stamped
onto content no model had read.

The three observed cases were all the annoying direction. #126 (2 content commits: the citation
fetchability fix answering a QP9 blocker, plus an unplanted-defect repair), #127 (a QP9 doctrine
correction across 3 files) and #130 (a `RUBRIC_VERSION` bump and fixture reshape) each had a
stale NO-GO re-published over the fixes that answered it, so the panel never read the answer and
the PR could not converge.

**The direction that had not happened yet is the reason this is a bug and not a cost defect.**
Push the content, then `git merge origin/main`, and a prior **GO** is re-stamped just as
readily. That is a merge-gate bypass available to any author, needing nothing but ordinary git
commands in an ordinary order, and leaving a run log that says the content was checked.

**Decision.** Inherit only when the *whole pushed range* is a base resync, established by two
tests that must both pass:

1. **Range shape.** Every commit reachable from the head but from neither `PRIOR_CYCLE_SHA` nor
   `origin/<base>` — `git rev-list "$PRIOR..$SHA" "^origin/$BASE"` — must be a merge whose every
   merged-in parent is already on the base branch. The `^origin/<base>` exclusion is what keeps a
   genuine resync from tripping this: the commits it carries across are reachable from the base
   branch, so they are never billed to the push. Without that exclusion the whole optimisation
   dies, because every base commit merged in reads as a non-merge commit in the range.
2. **Tree identity.** `git merge-tree --write-tree "$PRIOR_CYCLE_SHA" "${SHA}^2"` must produce
   exactly `${SHA}^{tree}`.

Two guards precede them: `PRIOR_CYCLE_SHA` must be non-empty (unchanged — a first cycle is always
read) and must still be an ancestor of the head, since after a force-push "everything since the
reviewed SHA" names nothing measurable.

**Why both, when the tree test is strictly stronger.** It is stronger, and it alone would be
sound. The range test is kept for two reasons that are about operating the pipeline rather than
about correctness. It names the offending commits in the run log — "`<sha>` is a content commit
under the head merge" — where a tree mismatch can only report two hashes, and diagnosing an
inherit that should have happened from two hashes is miserable. And it is pure plumbing available
on every git, so the cheap, legible test runs first and the strong one confirms it.

**Why the tree test is not optional.** Range shape is still a shape test. A merge that conflicts
is resolved by a human or by the fixer agent, and a resolution can put arbitrary content into a
commit whose every parent passes test 1 — the same bypass, one step further along. The tree test
is the only one that asks what the commit actually *contains*. It also incidentally rejects an
octopus merge whose third parent is off the base branch, which the old `^2`-only check never
looked at.

**It fails closed, and that costs something.** `--write-tree` needs git ≥ 2.38, and a re-created
merge that conflicts exits non-zero; both land on "review normally". The price is one spent cycle
on a PR that genuinely conflicted with its base, or on a runner with an old git. The alternative —
inheriting when we could not verify — is the defect this decision fixes, so the direction is not
a close call.

**This narrows D-fixer-merges-not-rebases.** `docs/ci-pipeline.md` used to say the fixer's sync
merge "lands on the merge-from-base inherit path like any other". That now holds for a **clean**
host merge and not for one the agent had to resolve: a resolution changes the tree, so the panel
reads it. The residual that decision names — a fixer-authored merge whose conflict resolutions
are wrong-but-clean reaching main unread — is closed for the inherit path specifically. It is
untouched on the fixer's normal path, where the fixer claims its own pushed SHA and no second
panel runs at all; that is the owner's intent and is not in scope here. The D-unguarded-sync
sync-only successor still inherits, correctly: that pass abandons on conflict, so what it pushes
is a clean merge of a reviewed tree with the base and nothing else.

**Guard rails preserved.** `/review` still outranks the whole path (PR #56) and is now checked
first. An empty `PRIOR_CYCLE_SHA` still reviews. A verified-pure resync still inherits, which is
the optimisation that made the anchor-conflict rebase churn across eight concurrent PRs
affordable.

**Tested by running the step, not by reading it.** `tests/test_ci_inherit_classifier.py` extracts
the `run:` block from `review-pipeline.yml` and drives it under `bash` over throwaway git
repositories, with a stub `gh` answering the one status query it makes — offline, no token. Seven
of its fourteen cases fail against the old classifier, including both directions of the bypass
and the hand-resolved conflict. A predicate this cheap to get wrong and this expensive to get
wrong is not one to leave to a reviewer noticing a diff in YAML.

## D-fixture-report-shape — audition fixtures are production-shaped, and four ship with the sound base they were mutated from

Found by adversarial review of the audition harness after D-control-soundness landed, and
independently confirmed by a second reviewer. Two defects in the corpus, related closely enough
that fixing one without the other would have been wasted work.

**The fixtures were a document class production writers are forbidden to emit.**
`test_run_assignment_uses_the_production_critic_prompt` pins the harness to the exact critic
prompt a run uses, so the measurement is only worth what the fixtures' resemblance to a real
artifact is worth. `prompts.REPORT_SKELETON` (D-report-template) mandates: no top-level `#` title —
"the report is the body only" — `## Conclusion` as the first section, `## Key findings`, `## The
strongest counterargument` engaged on the merits, inline `[1]` citations, and a numbered
`## Sources` section last with that byte-exact heading. All ten shipped artifacts opened with a
forbidden `#` title, none had a conclusion, key-findings or counterargument section, and every one
used author-year citations against a bulleted, unnumbered source list. The corpus was auditing
critics on a shape no writer in this system may produce, which moves three things at once: the
locus distribution (a `#` title makes the thesis S1.P1 and pushes every real section down one),
the organization cues the completeness lens explicitly judges, and citation mechanics — an
evidence critic looking for a dangling `[7]` had nothing of the kind to look at.

**Corpus class was readable off form alone.** The two controls ran 652-656 words with conspicuous
objection and decomposition sections and six dense sources each; the eight planted artifacts ran
239-357 words with thinner sourcing. Sensitivity and noise are measured on disjoint fixture sets,
so any feature separating those sets is a shortcut past the measurement: a model could score well
by being conservative on long, visibly balanced reports and aggressive on short ones, having
detected nothing. Length was the cheapest such feature and nothing was watching it.

**Decision — one rebuild, three parts.**

*Every artifact follows `REPORT_SKELETON`.* No title, `## Conclusion` first, `## Key findings`,
`## The strongest counterargument` stated in the form its proponents would accept and answered by
naming what would have to hold for the conclusion to flip, topical sections, numbered `## Sources`
last. `test_every_artifact_has_the_shape_production_writers_are_told_to_emit` checks the
mechanical part of that per fixture, so a fixture added later cannot quietly reintroduce the old
form. Every planted locus moved; the manifests carry the new coordinates and
`test_planted_loci_exist_in_their_artifact` still proves they exist.

*Matched pairs break the form confound.* Each of this decision's eight planted artifacts is one
minimal mutation applied to a sound base report — a paragraph appended, one citation swapped for a
fabricated one, one cited sentence replaced by an uncited one, one counterargument section pointed
at a weaker objection. Four of those bases ship as controls (`control-base-dust-bowl-01`,
`control-base-remote-work-01`, `control-base-minimum-wage-01`,
`control-base-congestion-pricing-01`), so for those four the sound and defective documents match
on length, structure, citation density and topic, and the only thing telling them apart is the
defect. The other four bases are not shipped: rewriting every citation in the `one_sided_sourcing`
fixture is not a minimal mutation, and the remaining three were held back to bound cost. Two
unpaired controls remain, so the corpus is not merely a set of near duplicates. (The corpus grew
further in the same merge window: `misrepresented-source-01` and `overstated-claim-01`
(D-category-coverage), `omitted-counterargument-02` (D-obvious-per-lens) and `overstated-claim-02`
(D-minor-floor-fixtures, replanted from `loaded-language-01`) are independent additions, not
matched-pair mutations of a shipped base — so of the corpus's eleven planted fixtures, four ship
with their sound base and seven do not; the pairing was never meant to reach every fixture, only to
break the confound without requiring it.) Corpus-wide the length spread is 1.348x — recomputed
against the full merged corpus, 787 words (`overstated-claim-02`) to 1,061
(`overstated-claim-01`) — against 2.74x before this decision, and
`test_corpus_class_is_not_readable_off_length` holds it under 1.5x with each class's median inside
the other class's range (control median 866 sits inside the planted range 787-1,061; planted
median 893 sits inside the control range 842-913). Every artifact, including the fixtures the
sibling decisions above added, carries exactly five sources, and
`test_corpus_class_is_not_readable_off_source_count` requires the observed source-count values to
be identical between controls and planted fixtures for every lens. A lens only ever sees its own
planted fixtures plus the controls, so a source-count gap that closes in aggregate can stay wide
open inside `for_lens` — as it did on `completeness`, where the planted pair carried three and four
sources against five and six for the controls, before this decision and the sibling fixtures that
merged alongside it converged every planted fixture on five.

*The control pool grows from two to six.* `control_material_rate` is a mean over
`controls x repetitions` runs compared against a threshold of 1.0. At two controls and the shipped
`repetitions: 3`, one residual soundness flaw in one control moved that mean by 0.5 — half the
distance to `unfit`, which is exactly how the pre-D-control-soundness corpus mis-graded every
evidence critic. Six controls bound one control's leverage at 0.167, and
`test_shipped_corpus_loads_and_covers_both_directions` now fails below four. This closes the "a
third control fixture" open item.

**The cost, stated rather than buried.** `for_lens` hands every control to every lens, so four new
controls raise the aggregate across the three model-lens assignments in a full audition — the
aggregate, not a per-model-lens-pair figure; a lens-by-lens breakdown follows below. Merged
alongside the sibling audition work landing in the same round (D-category-coverage adds
`misrepresented_source` and `overstated_claim` fixtures, D-obvious-per-lens adds the completeness
lens's obvious-tier fixture, D-minor-floor-fixtures replants `loaded-language-01` as
`overstated-claim-02`), the shipped corpus carries 11 planted fixtures and 6 controls — 17 total —
for an aggregate of 29 fixture-runs (10 evidence, 10 logic, 9 completeness) against 14 before any
of this round's audition work landed, roughly +107% on `ra audition`, not the +10% the old open
item estimated for one extra control alone. Isolating this decision's own contribution: four
additional controls add 12 fixture-runs by themselves (three per control, one per lens — that part
does not depend on how many planted fixtures ship); the remaining growth, from 14+12=26 to 29,
comes from the three net planted fixtures the sibling decisions above added to the same corpus.
That is the price of the measurement being worth anything, and it is paid per audition rather than
per run: nothing in the graph path calls the audition. Editing any fixture changes `corpus_hash`
and so invalidates every cached verdict, by design (`load_fixtures` hashes raw bytes before slot
substitution) — this rebuild invalidates the entire audition cache, and every rostered critic must
be re-auditioned before `ra doctor` says anything about it again.

**`test_control_citations_resolve_in_both_directions` is parametrized over every control, not a
hand-written pair,** and its regexes now read the numbered form: `[n]` in the body, `n.` in
`## Sources`. It additionally requires the entries to be numbered `1..n` contiguously, because an
inline marker that resolves to the wrong entry is worse than one that resolves to none. The
D-control-soundness caveat still applies unchanged: a claim carrying no citation marker at all
cannot be distinguished by regex from prose that needs none, so the soundness contract in each
control's manifest and review remain the only cover for that half.

**Rejected: shipping all eight bases as controls.** Ten controls would take a full audition to 41
fixture-runs — aggregate across the three model-lens assignments, the same unit as the 29 shipped
in the merged corpus, not a per-model-lens-pair figure — for a reduction in per-control leverage
from 0.167 to 0.1. **Rejected: keeping the old artifacts and bolting a conclusion
section onto each.** The defect is not a missing heading — author-year citations, bulleted sources
and a title-first structure are all load-bearing parts of the wrong shape, and a partial conversion
would have left the citation-mechanics half of the ecological-validity gap open while looking
fixed.

**Deliberately not done.** No grading-code change: the locus window, the severity floors, the
material-issue count and `judge` are untouched, and this decision is only about what the corpus
contains. No change to the number of planted fixtures or to any lens's coverage. No change to
`repetitions`, whose default remains 3.

## D-answer-obligations — every explicit question clause is material, and a substitute objection is not a counterargument

**Context.** The closed taxonomy could not name a fluent report that answers only one explicit
question clause or substitutes an adjacent, easier question. Such a report can satisfy the section
template and reach convergence even though a literal part of the user's question remains unanswered.

The audition corpus exposed a related specification mismatch. After D-fixture-report-shape,
`omitted-counterargument-01` deliberately contains a substantial `## The strongest counterargument`
section aimed at cordon-boundary effects while omitting the load-bearing distributional objection.
Its manifest correctly calls that the weakened substitute forbidden by the report template, but the
taxonomy still defined `omitted_counterargument` only as an opposing view being absent. The fixture
was testing a stronger and more useful rule than the production critic had been given.

**Decision.** Add `incomplete_answer` to the completeness lens with a mechanical `major` floor. It
means that an explicit, material part of the question is unanswered, or that the report answers an
adjacent question in its place. The writer must treat every explicit part as an answer obligation:
answer each in the conclusion and support each in the body. A question about change or comparison
requires the baseline and contrast needed to make the answer intelligible.

This category is deliberately literal. It does not license a critic to infer an unstated goal,
invent a "question behind the question," demand an optional angle, or choose arbitrary additional
depth. Those remain outside the report's obligations unless the question states them. A critic
anchors the issue to the partial conclusion or closest present passage and puts the missing explicit
obligation in `rationale`; span validation is unchanged.

Broaden `omitted_counterargument` without adding a second counterargument category. It now also
covers a purported opposing case that substitutes an easier adjacent objection and therefore does
not challenge a load-bearing conclusion. The critic anchors either to that weak substitute or to the
claim the absent view bears on, and puts the stronger missing case in `instruction`. A section heading
never earns completeness by itself.

**Measurement.** Add an obvious completeness fixture as a question-level matched pair. Its artifact
is byte-identical to the sound Dust Bowl control; only its question adds a second explicit obligation
about agricultural unionization, which the report never addresses. The pair therefore matches on
length, structure, citations, topic, and prose quality. The new fixture intentionally increases the
full-audition cost, and the corpus and prompt/rubric hashes invalidate old verdicts rather than
pretending the changed measurement is comparable.

**Invariants and limits.** Author exclusion, the blind orchestrator, fail-closed lens validation,
upward-only severity clamping, termination, and the untrusted-text boundary are unchanged. The new
category flows through the existing closed enum, per-lens allowlist, category-count map, and material
total. The QP1/QP5/QP8 application is recorded in `quality-principles.md`: the category has a
mechanical floor, changes no cross-context traffic, and is measured by the existing deterministic
audition aggregation and rubric-identity boundary. This decision does not add more critics per lens
(#135), conceptual-conflation checks (#136), writer-visible retrieved sources or claim-level
traceability (#137), or observed verification coverage in exported reports (#138); those are
separate changes with separate costs and failure modes.


## D-run-build-stamp — a run names the commit that produced it, or says it does not know

**The problem.** Runs recorded what they concluded and nothing about what produced them.
`final.json` carried `artifact_hash`, but that hashes the *report text*, not the code; nothing in
the store, the schemas, the events, the web API, the Dockerfile or CI recorded a commit, a version
or an image tag. `pyproject.toml` pinned `version = "0.1.0"` and no runtime code read it.

That made the most useful question about this system unanswerable from its own output. "We changed
how revisions are scoped and we are still not converging — which of these runs already had that
change?" could only be attacked by lining run timestamps up against `git log`, which is wrong
whenever a deploy lags a merge, whenever one PR carries two fixes, and whenever a run was resumed.
The system was accumulating exactly the evidence needed to evaluate its own changes, in a form that
could not be sorted by change.

**Decision.** Every run stamps `{"commit", "dirty", "source"}` — on each `queued` and `startup`
event, and in the `final.json` summary, which carries it into the `finalize` event and
`/runs/{id}/audit.json` for free. `build_identity()` resolves it once per process from, in order:
`RA_BUILD_SHA` baked into the image by CI (`source: "image"`, the production path and the only
authoritative one); the checkout the package sits in (`source: "git"`, covering `uv run`, the
devcontainer and tests, and the only source that can report `dirty`); or nothing
(`source: "unknown"`).

**`unknown` is recorded, not guessed.** The alternative — inferring a commit, or defaulting to
something plausible — produces a value indistinguishable from a measurement once written, and a
confidently wrong attribution is worse than a missing one. `ra doctor` reports the source and warns
on `unknown`, and the first run in the process logs a warning, so a deployment that has lost its
stamp is visible immediately rather than as a month of unattributable runs. For the same reason
there is no backfill of runs that predate this: they have no `build` key, and every display surface
omits the row rather than printing "unknown".

**Non-blank, not merely set.** `ENV RA_BUILD_SHA=$RA_BUILD_SHA` leaves the variable *always*
defined — empty on any `docker build` without the argument. Testing for presence would have
recorded `""` as an authoritative commit, which is the one failure mode this decision cannot
tolerate, so the check is for a non-blank value after stripping.

**Shelling out to git, in `src/`, for the first time.** Reading `.git/HEAD` directly would be
cheaper and dependency-free, but it cannot answer `dirty`, and `dirty` is the entire value of the
non-production path: a modified tree's commit is a starting point, not an identity. The call is
anchored to the package location rather than the cwd (or the app would report the HEAD of whatever
repository it was launched from), uses `--no-optional-locks` (the production rootfs is read-only
and `git status` would otherwise refresh the index), and treats a missing binary, a non-zero exit
and a timeout identically as "we do not know".

**`final.json` names one build, not all of them.** `_run_fingerprint` deliberately does not pin the
build, so a resumed run may cross a deploy — refusing to resume after an unrelated deploy would
discard work for no epistemic gain. The summary therefore names the build that *finalized* the run,
and the `startup` events are the full list. [run-provenance.md](./run-provenance.md) states this and
gives the query.

**Deliberately not done.** No `builds_seen` list in the summary: `_finalize` never reads
`events.jsonl` today, and adding I/O plus a failure mode to the terminal write path is not worth
data already recoverable from the events. No separate hash of the roster or the prompts — the
roster is tracked, so the commit covers it, and the `startup` event already records the resolved
identities and budgets, which is what actually varies between runs on the same commit. No
invariant, no controller or isolation surface touched: `OrchestratorView` forbids extras and is
built field by field, so a key in the store cannot reach it, and a test asserts the stamp never
appears in `signals/views.jsonl`.

## D-conceptual-conflation — a narrow `conceptual_conflation` logic category, and empirical anchors as an explicit widening of `overstated_claim`

**The problem.** The taxonomy had no name for a report that slides between materially distinct
propositions while keeping one label on them. The motivating report moved between formal policy
eligibility, differential implementation and utilization, and downstream cultural reinforcement;
between household form, access to suburban wealth-building, and representation in a promoted ideal;
and it grouped populations whose exclusion ran through different mechanisms. Every sentence was
individually defensible and every claim was cited, so `uncited_claim`, `misrepresented_source` and
`contradicted_claim` all had nothing to say. `invalid_inference` was the nearest fit and a poor one:
the inference is valid *given* the substitution, and a critic that files it there names the
conclusion rather than the step that produced it, so the fix task tells a writer to fix the wrong
sentence.

The same report made scale, prevalence and change claims through thematic assertion — sources that
described a phenomenon vividly and measured nothing about it. That defect also had no name, and it
is not the same defect: conflation is about *which* proposition is being supported, anchoring is
about *how much* the support licenses.

### Decision 1 — `conceptual_conflation`, logic lens, floor `major`

Two materially distinct concepts, mechanisms, units or populations are treated as interchangeable,
**and** the substitution carries a load-bearing inference or conclusion. Both halves are required.
The three distinctions named in the writer and critic rules — formal rule vs. mediated mechanism
vs. observed outcome; measured units vs. claimed population; heterogeneous mechanisms behind a
shared outcome — and the four exclusions (terminology preference, subgroup quotas, a distinction
that makes no difference because one mechanism or one body of evidence genuinely covers both, and
an aggregation the report draws and defends) are normative in
[convergence.md](./convergence.md#conceptual-conflation-and-anchors-for-empirical-scope-claims-d-conceptual-conflation).

**Why the logic lens rather than a fourth lens, or completeness.** The same reasoning D-social-bias
recorded: a new lens needs its own critic pool, its own double-clearance for strong acceptance, and
its own roster staffing, and this defect belongs to an existing dimension — it is a defect in how
the argument moves, which is what the logic lens reads. Completeness is the wrong home because the
defect is not an absence: the report says something, and what it says is a substitution.

**Floor `major`.** `invalid_inference`'s sibling and floored with it. The substitution is the step
the argument turns on, and only a material floor forces the revision. Not `blocking`: unlike a
contradiction, nothing in the report is thereby shown false, and the resolution — draw the
distinction, or restrict the claim to the concept the support covers — is always available inside
the report.

**`related_span` is verbatim-anchored** (added to `triage.IN_ARTIFACT_RELATED`), unlike the three
bias categories. Their related material is a *pattern* — a source cluster, the question's framing —
with no second span to quote. Both poles of a substitution are passages the report contains, which
is `invalid_inference`'s premise/conclusion shape. The field stays optional, so a single sentence
that fuses the two concepts with no second passage anywhere is still reportable, and this narrows
what a critic may forward to a writer rather than widening it.

### Decision 2 — a missing empirical anchor is `overstated_claim`, widened in the open

The issue asked for this to be settled explicitly rather than absorbed silently, and the three
candidates were weighed:

* **A new evidence category.** *Rejected.* The defect survives a perfect citation — a real source,
  accurately described, that characterizes a phenomenon and measures nothing about it — so it is
  not a sourcing failure, and the evidence lens would be raising something its own guarantee does
  not concern. Worse, an evidence category whose fix is "produce a figure" is unsatisfiable under
  `search.enabled: false`, where the writer has no retrieval at all, and would collide with the
  resolvability contract every critic instruction carries.
* **A report-mode (writer-side) requirement only.** *Rejected as sufficient, adopted as a
  component.* A standard no lens can raise is not detectable, and the whole design's claim is that
  no eligible reviewer can find a material defect — not that writers were told not to make one. The
  writer standards ship (below), and a critic can raise the failure.
* **`overstated_claim`, widened explicitly.** *Adopted.* A claim that turns on magnitude,
  prevalence, timing or change, resting on a thematic assertion rather than a concrete figure or a
  source that states it, *is* a claim stronger than its support. The widening is stated in
  `_CATEGORY_MEANING`, in the logic lens brief, in convergence.md and here — which is what
  distinguishes it from the drift the issue warned against. No floor change: `overstated_claim` was
  already `major`.

Two narrowings are part of the decision, not commentary: a claim about **kind, mechanism or
character** turns on none of the four and owes no anchor, and the instruction may never demand a
specific dataset or document as the only acceptable fix — qualifying the claim to what the support
establishes is always a complete resolution.

### Decision 3 — the floor ships with the measurement, because a minor floor here is unmeasurable

The issue asked to measure sensitivity and control noise **before** enabling a material floor. That
sequencing is not available in this harness, and saying so is part of the decision rather than a
deviation from it. `_check_planted_floor_is_material` (D-minor-floor-fixtures) refuses to load a
fixture planting a category floored below `major`, because every detection credit in `grade`
requires post-clamp materiality — a critic reporting a minor-floor category at its own floor scores
as blind. So a `conceptual_conflation` shipped at `minor` could not enter the corpus at all, and
would land in exactly the un-auditable state the tail of this file already records as an open item
for `loaded_language`: a category in production that the audition cannot see.

The measurement therefore ships **in the same change as the floor**, which is the closest faithful
reading of the request:

* `conceptual-conflation-01` (logic, moderate) plants the substitution, paired with
  `control-base-paid-leave-01` — the identical artifact with one paragraph rewritten to draw the
  same distinctions correctly. The pair is what separates "detects the conflation" from "distrusts
  reports about paid leave".
* `overstated-claim-03` (logic, moderate) plants the unanchored scope claim, paired with
  `control-base-open-source-01`, the **qualitative-evidence control**: a report built on interview
  and account evidence that makes claims about kind, declines the prevalence claim explicitly, and
  says why. A critic that demands a figure there is inventing a material issue.
* Both controls reach **every** lens (`for_lens`), so the noise direction is measured on all three,
  and `control_material_rate` / the never-clean gate are what would catch a widened
  `overstated_claim` reading as "quantify everything".

`prompt_hash` changes (the logic lens brief and two category meanings) and `rubric_hash` changes
(`LENS_CATEGORIES` and `SEVERITY_FLOOR` are hashed as data), so **every cached audition verdict is
invalidated by design** — operators re-run `ra audition` after upgrading, and `audition.enforce`
reads *not audited* until they do. The corpus grows from 17 fixtures to 21 (13 planted, 8 controls);
per-control leverage on `control_material_rate` falls from 0.167 to 0.125, and the aggregate rises
from 29 fixture-runs to 37 (12 evidence, 14 logic, 11 completeness). Two of the eight added runs are
this decision's planted fixtures on the logic lens; the other six are the two new controls, which
every lens owes. Corpus-class confounds hold:
the length spread stays 1.348x with each class's median inside the other's range, and every new
artifact carries exactly five sources like the rest.

`test_all_repetitions_failing_one_fixture_leaves_that_fixture_uncovered` is re-derived rather than
re-tuned: two more controls change the per-lens fixture count, so its `repetitions` moves to 5 and
its failure budget is computed from `max_schema_failure_rate` instead of hardcoded. Five is chosen
because `owed x repetitions / 5` is a whole number for *every* corpus size when `repetitions` is a
multiple of five, so the next fixture addition does not silently stop exercising the boundary; the
exact-rate assertion is what would catch it if it did.

**Writer standards** are symmetric with the critic categories, in `WRITER_SYSTEM`: keep a formal
rule, its implementing mechanism and the observed outcome as three separately supported claims;
keep a finding attached to the units it was measured on rather than restating it about a wider
group; name the difference, or narrow the claim, where a claim covers groups reaching one outcome
by different mechanisms; and give a figure or a source, or qualify the claim, where a claim turns
on magnitude, prevalence, timing or change — while not manufacturing a number for a claim about
kind.

**Known residual, accepted.** Same shape as D-social-bias's: the rules catch what they name. A
substitution between two things the critic does not recognize as distinct passes through, and the
`load-bearing` half of the trigger is a judgement no mechanical check can make. The exclusions bound
the false-positive direction and the controls measure it; nothing bounds the false-negative
direction except the critic's own reading, which is what the audition's sensitivity rate is for.

**Deliberately not done.** No new `docs/` page: the rules live in convergence.md, beside the
taxonomy table they govern, rather than in a `bias.md`-style file of their own. No change to the
controller, `OrchestratorView`, the decision table, or any floor other than the new category's own.
No `tier: obvious` fixture for either new plant — both shipped fixtures are moderate by
construction, and an obvious-tier fixture gates `judge` fail-closed, which is not a claim this
measurement has earned yet.
## D-front-loaded-depth — two independent critics read every draft, per lens, before it is revised

**The problem.** Strong acceptance requires two cross-family non-author clean records per lens
(RC-001/QP2), but only one critic per lens ever read a draft. The second was deferred to
controller **rule 8**, which fires only after a pass has already reported `material == 0`. So the
second opinion was never part of *discovery*: the run acted on the first review's silence before
asking the second witness. Front-loading the configured slate makes every selected witness's
findings available to the same triage pass, which is mechanically checkable against the code and
`tests/test_review_depth.py` without relying on private run history (QP9).

**Decision.** Review depth is configuration, and the production default is **2 eligible non-author
critics per lens on every generated artifact**.

```yaml
review:
  depth: 2                 # critics per lens per pass; 1 restores single-critic discovery
  per_lens: {evidence: 3}  # optional per-lens override
```

`roles.critic_slate` draws the whole slate for a lens at once, because drawing one model at a time
from the same "already used" set returns the same alias every time. It is a **ceiling, not a
quota**: the slate is taken from *fresh* eligible models only, so a lens the roster can staff once
runs one critic and reaches `converged_unconfirmed` through rule 10, exactly as before. Every
existing eligibility rule applies per slot — `eligible_critics` drops the author and deduplicates
by resolved provider/model; `critic_slate` admits at most one model from each family; and
`assert_author_exclusion` re-checks at the moment of the call. No slate can contain the author, the
same model twice, or two same-family checkpoints presented as independent however large `depth`
is. `lens_statuses` likewise counts distinct clean families, so same-family records cannot satisfy
QP2's second-witness requirement.

Each critic in a slate is a separate `critique_once` — the same production prompt, a fresh context,
no knowledge that another critic is reading the same artifact and no sight of what it found. The
two are as blind to each other as the three lenses always were (isolation.md).

**Rule 8 keeps its job and loses its shift.** It is still the only way an under-cleared *clean*
artifact reaches `strong_met`, and it is still bounded by `confirmation_attempts`. What changes is
that at depth 2 a clean pass normally arrives already strongly-cleared, so rule 8 becomes the
top-up for **incomplete depth** — a critic that failed, a pool that ran short — rather than the
normal discovery path. No rule was added, removed, renumbered or reordered; no `ControllerInput` or
`OrchestratorView` field changed. The termination argument in convergence.md is untouched: depth
multiplies the calls a critique pass makes, and every measure that bounds the loop counts passes,
generations and budgets, not calls.

**Fail-closed keeps its meaning, at the right unit.** One bad field still fails the *review* it
appeared in, whole, after the repair budget — nothing is salvaged, nothing is dropped. What is
re-scoped is `lenses_failed`, which now counts lenses with **no completed review of this artifact**
(`triage.unreviewed_lenses`) rather than lenses whose latest result failed. The readings coincide
on every depth-1 discovery pass, but differ on one pre-existing path: a rule-8 confirmation that
fails after the lens already holds a completed review. Previously the failed confirmation
overwrote that review, made `lenses_failed == 1`, sent the run through rule 2, and exhausted at
rule 3 (`aborted`). The completed review now remains in the list, so the controller returns to
rule 8 when another qualified witness remains or falls through to rule 10/11. The same distinction
appears within a depth-2 slate when one critic completes and another fails. Aborting a clean,
reviewed artifact because a confirmation provider failed is the wrong answer to a flaky provider;
the shortfall is still not forgiven, because it lands on family-counted `cleared_count` and cannot
satisfy `strong_met`.

**Counting distinct findings, not reports of them.** Two critics on one lens routinely land on the
same defect, and with `search.verify_sources` on, both evidence critics are handed the *same*
mechanical `fabricated_citation` for the same dead URL. `triage.distinct_issues` collapses on the
key `to_defects` already used — `(section, paragraph, category, claim_span)` — so `tally`, the
defect list and the stagnation signature all see one finding once. Where two critics disagree on
severity the **higher** survives, which is the direction the mechanical floor already clamps in
(RC-005): letting whichever review was stored first decide would give a second reviewer the power
to soften the first. At depth 1 this is a no-op — categories are partitioned by lens, so two lenses
cannot raise the same key.

**Auditioning follows the front-loading.** `audition.roster_warnings` was position-aware on the
premise that "position ≥ 2 is unreachable on the first pass"; at depth 2 that sentence is false for
position 2, so the threshold is now read from `review.depth_for(lens)`. A marginal or unfit model
inside the depth window gets its own warning saying it now runs on **every** draft, and one outside
it keeps the old rule-8 warning. `audition.enforce` is unchanged and already gates every assigned
slot regardless of position: a cached `unfit` verdict on any critic in a lens pool fails startup
closed before the graph runs. Re-auditioning the newly front-loaded slots against the
production-shaped corpus is an operator step (`ra audition`), not something this diff can perform —
it needs the paid proxy.

**Cost.** A pass makes `depth × |lenses|` critic calls instead of `|lenses|`. `budgets.max_concurrency`
is unchanged, so the instantaneous load on the proxy is the same and the extra depth is paid in
wall-clock. The expected trade is fewer generations, which are the expensive step: a round avoided
saves a writer call, three-to-six critic calls and an orchestrator call.

**Deliberately not done.** No change to the decision table, to `OrchestratorView`, to author
exclusion, to the severity floors, or to what any critic is shown. `review` is deliberately **not**
part of `_run_fingerprint`: depth is read fresh at each pass and every per-artifact accumulator
resets on generation, so changing it mid-run is safe, and adding it would cost every in-flight run
its checkpoint. `graph._lens_results` accepts the pre-existing one-result-per-lens state shape for
the same reason. No A/B harness: `depth: 1` reproduces the previous single-critic discovery pass
from configuration, while the intentional failed-confirmation divergence above is pinned
separately; comparing the arms on real questions remains an operator measurement.

## D-observed-source-coverage — the run reports what verification reached, not that verification was switched on

**The problem.** `graph._finalize` derived its sourcing label from a runtime boolean. With
`search.verify_sources: true` every run shipped as *consensus-reviewed with verified sourcing*,
whatever the fetches actually returned. The failure is structural: the label described enabled
configuration while `fetch_sources` counted only URLs already selected for fetching, so neither
recorded the bibliography's denominator or whether each entry was checked. The repository's
offline mixed-outcome fixture in `tests/test_source_coverage.py` pins the resulting distinction:
addressable, unaddressable, body-read, metadata-only, blocked, and not-found entries can coexist in
one artifact, while a boolean label cannot report any of those observed differences. That is the
failure mode the labelling discipline of D-in-artifact-citations, D-retrieval-opt-in and
D-source-verification exists to prevent.

**Decision.** Coverage is measured, keyed to an artifact, persisted, and rendered; the categorical
label is replaced by the measurement.

*Measured.* `fetch.coverage` reads the shipped draft's own `## Sources` section and tallies it in
**entries**, not in fetches: `cited`, `addressable` / `not_addressable`, `attempted` /
`not_attempted`, and a disposition for each attempt — `body_backed_entries`, `metadata_only`,
`blocked_or_unreadable`, `not_found`, `budget_exhausted`. `bodies_read` is deliberately outside that
entry partition: it counts distinct cited URLs whose body was read. `existence_confirmed` is derived
from body-backed entries and registry hits. `not_independently_checked` is derived only from
`not_addressable`, `not_attempted`, `blocked_or_unreadable`, and `budget_exhausted`; a definitive
`not_found` is an independent determination of absence, not an unchecked entry. Both derived values
are written from those counts so the summary line and the breakdown cannot disagree. Two entries
citing one URL are two things the report stands on, but the per-run fetch cache collapses them into
one call, so the record says two body-backed entries and one body read rather than calling one body
two.

*Keyed to an artifact.* The tally is taken in `_critique_one` where the evidence lens fetches, and
written into checkpointed state under the artifact's hash — never latest-wins. On a non-accepted
terminal `_finalize` ships the best-scoring draft, which need not be the last one written (issue
#93), so coverage keys the same way the outstanding-defect list does. A draft with no entry reads as
*not recorded*, which is neither zero coverage nor a pass.

*One record per artifact, however many critics read it.* D-front-loaded-depth gives each lens
`review.depth` critics per pass, so at the shipped default the evidence lens tallies the same
bibliography twice, concurrently, against a fetch cache that is monotone but last-write-wins
(`fetch.SourceFetcher.fetch`). Two critics that both miss the cache on the same URL can therefore
observe genuinely different outcomes, and no aggregate of the two exists to read back afterwards.
`graph._record_coverage` keeps the observation that **reached furthest** rather than whichever
thread happened to finish first. Its total ordering compares entries independently checked,
distinct bodies read, body-backed entries, registry confirmations, definitive absences and the
remaining disposition counts, with a canonical-record tie-breaker for future fields. Equal-reach
but different observations therefore cannot fall back to arrival order. Taking the maximum is not
the same as claiming both critics saw it: it is the honest reading of "what did verification reach
for this draft", which is a question about the run, not about a critic. A record update and its
`source_coverage` event execute under the same lock, so two callbacks cannot interleave and the
**last** event for an artifact is always the one `final.json` carries. This is deliberately not true
of `fetch_sources`, which still fires once per evidence critic and therefore double-counts a
depth-2 pass; it was never a coverage measurement, which is the gap this decision exists to close.

*Persisted and rendered.* `final.json` gains `source_coverage`; `export.Provenance` carries it; the
markdown export, the HTML export and the run page render the same breakdown from one definition, as
they already do for the defect list. Every row is a bounded non-negative integer derived from the
artifact's own text — no URL, no page text, no model identity — so the record is safe for the audit
trail on RA-016's terms, and `OrchestratorView` is untouched: the controller still sees none of it.

*The label.* With verification on, the label is now the observation —
`consensus-reviewed — source review: 15 cited; 3 addressable; 3 existence confirmed; 3 source bodies
read (backing 3 cited entries); 12 not independently checked`. Verification on with nothing recorded
says exactly that rather than falling back to the old wording, because an absent measurement must
not read as a passing one. The two non-verification labels are unchanged: neither ever claimed
verification, so neither was overstating anything — but their coverage is still measured and still
rendered, so a retrieval-only run now states in its export how much of its bibliography went
unchecked, which its label never could.

**What the numbers must not be read as saying.** Two misreadings are invited by the counts and
foreclosed in the rendering, which carries the caveat under every breakdown. An entry that was not
independently checked is *unverified*, not suspect. A `blocked` or `paywalled` entry was
*unreadable*, not absent — reading it as absence is precisely the inference D-notfound-fabrication
forbids. A definitive not-found is independently checked and establishes that a cited page does not
exist. The existence-vs-body doctrine of D-existence-vs-body survives intact in the columns:
`metadata_only` confirms existence and is counted separately from `body_backed_entries` and
`bodies_read`, because a registry record is not the source's text. Entry counts and distinct-body
counts are labelled separately everywhere they render.

**Where the measurement is deliberately conservative.** Entry splitting is a heuristic over
model-written markdown — list markers where the section has them, one entry per line where it does
not — and "addressable" means *carries an http(s) URL*, so a bare `doi:10.…` with no resolver URL
counts as not independently addressable. Text before the first list marker is treated as section
prose, so a URL there may be fetched without entering the bibliography denominator. All three
choices err toward reporting **less** coverage than was achieved, which is the only direction a
claim about verification is allowed to be wrong in. The counts are therefore reported as observed,
never as a completeness claim.

**Deliberately not done.** No controller change: coverage does not gate acceptance, does not enter
`OrchestratorView`, and mints no defect. No deduplication of `fetch_sources`, which fires once per
evidence critic and so reports a depth-2 pass twice — pre-existing, visible only in the audit trail,
and a change to an event this decision does not own. A bibliography that is entirely unaddressable is a fact the
export now states, not a blocking finding — turning it into one is a severity-floor decision with
its own failure modes and needs its own entry. No new fetching at finalize: the tally comes from
outcomes the evidence lens already produced, so `_finalize` still performs no I/O and a resumed run
reports the coverage its checkpoint carries. No change to `search.max_sources`, whose truncation is
now visible as `not_attempted` rather than fixed.

## D-audition-probe-parity — the audition measures a critic in the extraction regime a run would pin it to

**The problem.** `ra audition` built an `LLMClient` and went straight to `run_audition` without ever
calling `client.probe_structured_output`. Both of the other paths that use the client do probe:
`ra doctor` fills a whole column with the results, and `graph.build_runtime` probes every alias at
startup and logs the pinned mode. Unprobed, `LLMClient.structured` falls through to
`mode = mode or self.mode_for(alias)`, and `mode_for` answers the default `"prompt"` for any alias
it has never probed — the weakest rung of the extraction ladder.

So every audition call was made under prompt-mode extraction, for every model, whatever mode a run
would pin that model to. The harness certified critics in a regime production does not run them in.
A model reliable under `json_schema` but flaky under prompt extraction is graded on failures it will
never have in production; a model that is the reverse gets a pass it has not earned. Neither
direction was visible in the verdict, because the verdict did not record which regime produced it.
`schema_failures` is the counter most directly affected — it is a count *of* an extraction path —
and it feeds a hardcoded `unfit` gate, not a tunable threshold.

The gap was found by adversarial review of the 2026-08-02 audition run and its 30-call spot check,
which observed no schema failures attributable to it in the sampled pairs. So this is a fidelity
gap, not the cause of the noise findings that run reported. `ra audition-refine` had the identical
gap against `RefinementService.preflight`, which does probe before serving.

**The fix, part one: probe before measuring.** Both audition commands now probe every alias they
will call, before any measurement and before any cached verdict is read. The probe memoises, so the
harness's own calls cost nothing extra.

An alias that cannot be pinned to any mode **fails the command closed** (exit 2), rather than being
auditioned under the fallback. That is parity too: `build_runtime` refuses to start a run staffed by
such an alias, so measuring it under a mode no run would use would be this same defect in a new
place. The cost is that one unprobeable model blocks the whole command; `--alias` and `--lens` are
the escape, and the operator's fix — re-roster it — is the same either way.

**The fix, part two: the verdict names the regime.** `CacheEntry.structured_output_mode` is a
required field with no default, and a term in `matches()`. The precedent is
D-audition-rubric-identity: an entry that cannot say what regime produced it fails
`model_validate`, `load_cache` drops it, and the pre-probe cache reads as *not audited* — never as a
pass carried across a regime change. The same field, for the same reason, is on `RefineCacheEntry`.
`ra audition --json` and `ra audition-refine --json` both emit the mode, and the table prints it
beneath itself, so a report that does not name its regime cannot be mistaken for one taken in the
right regime.

**The decision the issue asked to be made explicitly: the mode is compared on the measuring path and
only reported on the free ones.** `matches()` takes `structured_output_mode` as a required keyword
that accepts `None` to mean *deliberately not compared*. `ra audition` and `ra audition-refine` pass
the probed mode and re-measure on a mismatch. `cached_judgements` — and through it `ra doctor`'s
table and the `audition.enforce` startup gate — and `refine_cached_judgement` pass `None`.

That asymmetry is the whole of this decision, and it is not the obvious symmetric answer, so:

- **Every other term in the identity is free to compute; this one is not.** The corpus hash, the
  prompt hash, the rubric hash and `require_verbatim_spans` come from disk, from code, or from
  config. The mode comes from probing a paid proxy. `cached_judgements` promises never to spend —
  `test_the_gate_takes_no_client_and_so_can_never_spend` pins it, because the gate runs on every
  `ra run` and every web boot, and a keyless checkout must still boot. Making the mode a term in the
  free read would mean either handing the gate a client (forbidden) or threading a probed map into
  it from `build_runtime`, which would move the probes *ahead* of the gate, so a roster with a known
  `unfit` critic would start spending before being refused.
- **A non-deterministic prober would silently disarm enforcement.** `config/roster.yaml` documents
  `minimax-m3` as probing non-deterministically across `json_schema`, `json_object` and `prompt`.
  If the free read dropped a mode-mismatched entry, that model's `unfit` verdict would stop blocking
  on most boots — not because anything was re-measured, but because the probe landed elsewhere. The
  gate blocks only on a *positive* `unfit`, so every invalidation is a step toward not blocking.
  Trading a real block for a mode-fidelity scruple is the wrong direction for a fail-closed gate.
- **Reading across the difference is only safe if the difference is visible.** So `mode_drift()`
  reports every slot whose cached verdict was measured under a mode the alias no longer probes to,
  naming both modes and the re-measure command. `ra doctor` prints it beside the other roster
  warnings — free there, because doctor probes every alias anyway. It takes the probed modes as
  data, never a client, and the no-client test now covers it too.

The net effect is that a verdict is *measured* under the production regime and *invalidated* the
moment a paying caller sees a different one, while a free reader keeps whatever measurement exists
and is told when it disagrees with today's probe.

**Cost.** `ra audition` now spends one probe call per distinct alias in the filtered slot set, even
when every verdict is cached, because the mode has to be known before the freshness check. That is
bounded by the roster size and is a rounding error against |models| × |fixtures| × repetitions.
Existing `.ra-audition.json` and refine cache files are dropped as unvalidatable, so the first
audition after this lands re-measures the roster — the same one-off cost D-audition-rubric-identity
accepted, and safe to land under enforcement for the same reason: it degrades to *not audited*.

**Deliberately not done.** The mode is not hashed into `prompt_hash()`. That hash is about the
prompt *surface* — what text a critic is shown — and D-audition-source-mode already fixed its
meaning to the source-less surface; folding a per-alias, probe-dependent value into a corpus-wide
hash would invalidate every model's verdict whenever any one model's probe moved. The mode is not
added to `Metrics` either: it is a condition the measurement was taken under, which is what
`CacheEntry` holds, and putting it there would change `rubric_hash`'s field set for a value that is
not a grading rule. `graph.build_runtime`'s ordering is unchanged — the cache-read gate still runs
before the probes. And nothing here changes what the audition *grades*: `prompt_hash`,
`rubric_hash`, the thresholds and the fixture corpus are untouched, so a verdict's meaning changes
only in that it now says which extraction path produced it.

## D-completeness-pool-noise — a critic that invents work is removed from the lens it invents it on

**The measurement.** The public source record is
[issue #148](https://github.com/NickBorgers/reasonable-answer/issues/148), which records the
audition metrics, the 30-call manual-review protocol, its per-control result, and the two observed
failure signatures. The 2026-08-02 audition graded `mistral-large-3` **`unfit`** on completeness:
**2.61 material issues invented per sound control report**, the highest noise rate measured on any
(model, lens) pair and 2.6× the `max_control_material_rate` fail-closed line. Its sensitivity on
that same lens was **1.00** — it found every planted defect. The two numbers are not in tension,
they are the finding: this model reports *everything*, and on completeness most of what it reports
is not there.

A 30-call spot check confirmed the noise is the model's and not the corpus's, which is the reading
D-control-soundness exists to force us to rule out. Across **13 material issues filed on 5 of the 6
controls, zero were real defects**, and they fall into two reproducible signatures:

- **Hedge-blindness.** It reads explicitly hedged language as absolutist and then flags the
  absolutism it supplied. It flagged *"'Public health infrastructure' covers **at least** three
  distinct things"* as presupposing the three categories are exhaustive — the artifact's "at least"
  says the opposite, in the span the model itself quoted.
- **Inexhaustible-counterargument demand.** It files `omitted_counterargument` for an
  ever-further alternative mechanism on controls whose `## The strongest counterargument` section
  already engages the strongest objection at length — including cases where its own `related_span`
  quotes the passage stating the supposedly omitted point. This is not a bar a report can clear:
  every rebuttal admits one more mechanism, so a run reviewed by this critic on this lens stagnates
  rather than converging.

**Why the false-positive direction alone is disqualifying.** D-critic-audition already states that
both directions gate, and this is the case it was written for. The convergence loop is asymmetric
about the two errors: a missed defect costs one lens one round of evidence, while an invented
material issue blocks acceptance outright, spends a writer call and three-to-six critic calls
"fixing" prose that was correct, and pushes the run toward rule 13 `exhausted_unresolved` on a
report that was fine. Perfect sensitivity buys nothing back, because nothing downstream can tell an
invented material issue from a real one — the defect list is the same shape either way. A critic
whose findings must be discounted is not a cheap critic; it is a critic the harness cannot use.

**Decision.** `mistral-large-3` is removed from `roster.critics.completeness` in both shipped
rosters, and the remaining pool is ordered **fit-first**:

```yaml
completeness:
  - gemma4     # fit:      0.89 sensitivity, 0.17 invented per control
  - glm-5.2    # marginal: 0.72 invented per control
```

Ordering is load-bearing for the same reason D-critic-audition's position analysis was: whichever
slot a pass reaches first is the one whose *silence* the run acts on. Putting the measured-`fit`
model at position 1 also settles, for this lens only, the standing worry that `gemma4` is the same
checkpoint that flagged nothing across six completeness calls in `run-d5934276fafd`. That worry is
now answered by measurement rather than by hiding the model at position 3 — 0.89 sensitivity is a
model that reviews. On **evidence** `gemma4` is still at position 3 and still unmeasured, and the
roster comment there is unchanged.

**What survives the drop.** The pool keeps two model families (Google + Zhipu), so
`validate_roster_health` reports no `roster_limited` warning for completeness against any writer,
`critic_slate`'s one-model-per-family rule can still fill a depth-2 slate, and `lens_statuses` can
still count two distinct clean families — a strong `accepted` remains reachable on this lens. The
loss is bounded more tightly than the diff looks: `mistral-large-3` is a writer, so author
exclusion already emptied its completeness slot on every round it authored (R1 and every odd
round). On those rounds the pool *was* `{glm-5.2, gemma4}`; this change makes the even rounds match.

**What is genuinely lost, stated plainly.** Two things. First, mistral's Western corpus was this
lens's decorrelation argument against two CN-lab priors (QP2); the remaining pair is Google and
Zhipu, which is still cross-family and still cross-lab, but the pool is narrower than the other two
lenses. Second, at `review.depth: 2` a two-model pool has **no spare**, and the consequence is
worth spelling out rather than waving at, because it is not the gentle one. Suppose a depth-2
completeness pass where one critic returns a clean review and the other fails. `unreviewed_lenses`
is empty, so rule 2 does not re-ask; both identities are in `used_critics` (a failed review still
marks its critic used), so `unused_eligible` is 0 and the lens is **not toppable** — rule 8 has
nobody to call. And `roster_limited` is `eligible_count < 2` counted in *families*, which is
exactly 2 here, so rule 10's honest weaker guarantee does not apply either. The artifact is clean
with `cleared_count == 1`, and the run falls through to **rule 11, `exhausted_unresolved`**.

That is the pre-existing behaviour of a two-family lens, not a new rule — the same thing already
happened on completeness every round `mistral-large-3` authored — but before this change the even
rounds had a third family to top up with, and now no round does. Set against it: the third family
was the one filing 2.61 imaginary defects per sound report, which does not merely fail to confirm a
clean artifact, it *prevents* one. A lens staffed by two critics that can return an honest clean is
worth more than a lens staffed by three when the third manufactures work every round. Restoring
depth is the right end state, and the open item below says so — but the replacement has to be
measured first, because filling the slot on corpus-decorrelation reasoning alone is what produced
this.

**Deliberately not done.** `mistral-large-3` stays in the logic pool and in the writer pool. Its
logic verdict is `marginal`, not `unfit`, and the spot check attributed part of the logic-lens
noise *cohort-wide* — every critic on that lens — to defects in the control fixtures themselves,
which are being repaired separately; grading a model on a corpus known to be wrong is exactly the
mistake D-control-soundness names. The logic-lens roster call therefore waits for the post-fix
re-audition. Nothing here touches the audition thresholds, the fixtures, the grader, or any file
under `src/`: this decision is a roster edit, three tests pinning the composition it chose, and the
evidence for both — which is the whole point of having a measured eligibility term. No audition
cache is committed (`.ra-audition.json` is a property of the
deployment, not the repo), so the verdicts above are cited, not shipped — re-running `ra audition`
is what reproduces them.
## D-control-defect-sweep — the noise metric measured the corpus too, and five control defects fall out of it

**The observation.** The first `ra audition` on the reshaped 17-fixture corpus (2026-08-02) flagged
several critics as noisy on the controls. A 30-call spot check that read every filed issue against
the artifact it was filed on found that a substantial share of the supposedly invented issues were
**real, material, lens-relevant defects in the controls** — D-control-soundness's failure mode a
second time, in a corpus that had been rewritten specifically to close it.

That is not a new kind of mistake, but it is a new piece of evidence about the harness: three of the
five defects below were found by the critics the controls were grading, on the run that was
supposed to be grading *them*. `control_material_rate` counts findings a control should not admit;
it cannot tell "the model invented this" from "the fixture author was wrong", and this round it was
measuring the second. The metric worked in both directions — it is a joint measurement of the model
and the corpus, and a spike in it is a hypothesis about either.

**The five defects.**

| fixture | locus | category the critics filed | what was wrong |
|---|---|---|---|
| `control-base-congestion-pricing-01` | S1.P1 | `overstated_claim` / `uncited_claim` | "none of the **four** reviewed here has been repealed" against three implementations — London [1], Stockholm [2], New York [3]. Both `## Key findings` and the evidence section list three, and `omitted-counterargument-01`, cut from this same base, still says three. |
| `control-base-remote-work-01` | S2.P1, S4.P1 | `misrepresented_source` | Bloom et al. (2015) decomposed backwards: "roughly two-thirds from more calls per minute and one-third from more hours worked". The paper reports 9 of the 13 points from more minutes worked per shift and 4 from more calls per minute. |
| `control-base-minimum-wage-01` | S4.P2 vs S3.P2 | `contradicted_claim` | S4.P2 said the bunching design "answers the comparison-group problem"; S3.P2 said that same design "rest[s] on contestable comparison groups". |
| `control-sound-02` | S5.P2 | `invalid_inference` | "Much of the distance between the two results is that discount-rate assumption" — a cross-study attribution licensed only by [2]'s own within-study sensitivity. |
| `control-sound-01` | S5.P2 | `contradicted_claim` | "the component on which the answer above is most confidently negative" against S3.P2's "hedged rather than negative" and S1.P1's "absence of studies, not absence of effect", with the reconciling distinction never stated. |

The Bloom correction is fetch-verified per QP9/QP10 rather than derived by negating the wrong
sentence: the published abstract reads "a 13% performance increase, of which 9% was from working
more minutes per shift (fewer breaks and sick days) and 4% from more calls per minute"
([QJE 130(1), 165–218](https://doi.org/10.1093/qje/qju032); [NBER w18871](https://www.nber.org/papers/w18871)).
The fixture also glossed the 9 points as "more hours worked", which implies longer shifts rather
than the fewer breaks and sick days the paper attributes them to; both sentences now say "more
minutes worked per shift".

**Decision.** All five are corrected in the fixtures, and the soundness contract in each manifest
records what was wrong and why the corrected sentence is what the sources carry. Two of the
corrections extend that contract past its previous scope: it required every empirical claim to
carry a resolving citation, and said nothing about whether two sections could assert opposite
things. **A control is internally consistent across sections, or it is not sound** — an artifact
that contradicts itself hands the logic lens a real finding, and D-control-soundness's rule ("sound
under every lens or it is not a control") already implies this. The two `contradicted_claim` defects
are the demonstration that the implication needed saying.

Each correction is the smallest edit that removes the defect and leaves the report's posture
intact. `control-sound-01` keeps its hedge and gains the sentence that makes the sanitation
confidence consistent with it; `control-base-minimum-wage-01` qualifies S4.P2 rather than deleting
S3.P2's caveat; `control-sound-02` says what [2] actually shows instead of dropping the paragraph.
No source is added or removed in any fixture, so the source counts `test_corpus_class_is_not_readable_off_source_count`
compares are unchanged, and the length band still clears `MAX_LENGTH_SPREAD`.

**Two planted fixtures are edited, and that is what keeps them measuring.** `fabricated-citation-01`
and `uncited-claim-01` share the corrected prose with their control bases — the paragraphs are
literally the same text, because each plant *is* its base with one paragraph mutated. Correcting
only the control would make the pair differ in two paragraphs instead of one, and the second
difference would be a feature separating the noise set from the sensitivity set, which is the
confound D-fixture-report-shape and D-conceptual-conflation exist to keep out. The mirrored edits
are byte-identical to the control's and touch no planted locus, no manifest `defects` block, no
threshold and no grader: the plant/control delta is exactly what it was, namely the planted defect.
`MINIMAL_PAIRS` does not yet assert one-paragraph minimality for these two pairs — their manifests
claim it in prose — so no test forced this; the diff was checked by hand and the differing-hunk
count is unchanged.

**Consequence for the cache.** Editing any fixture changes `corpus_hash`, and
`AuditionEntry.matches` therefore invalidates every cached verdict (D-audition-rubric-identity).
That is the intended behaviour and the reason the hash covers artifact bytes: the previous verdicts
were measured against a corpus that graded competent critics as inventors, so they are not verdicts
about the models. A re-audition follows this change, and the noise figures it produces are the
first ones that mean what `control_material_rate` says they mean.

**Deliberately not done.** No threshold moved. `max_control_material_rate` stays at 1.0: the fix for
a corpus that hands out real findings is to stop handing them out, not to raise the bar for how
many a control may hand out. No test is added — the two mechanically checkable properties here
(citation resolution, source counts) already have tests that pass, and the three that are not
mechanically checkable are exactly the ones D-control-soundness assigned to review and to the
manifest contracts, because no regex distinguishes a paragraph that contradicts another from one
that qualifies it. The audition structured-output probe gap and the completeness-pool roster
change are filed separately and are not touched here.

## D-decisions-merge-driver — a merge driver resolves the common append-only collision, not a file split

**The problem.** Every new decision is appended immediately before `## Open items for a future round`
(D-decision-slugs). Almost every PR here is agent-authored (docs/ci-pipeline.md's "Syncing with the
base branch": "Almost every PR here is agent-authored, so when the base moves there is no human in
the loop to resync"), and most add a decision. Two independent, non-conflicting PRs that both append
collide at that identical insertion point: a 3-way merge diffs
each side against the same base line and has no way to order two insertions anchored on it, so the
result is a genuine git conflict with no semantic disagreement behind it — the same shape every time,
regardless of which two decisions collided. The repository already carries dedicated machinery for
exactly this: `review-fixer.yml`'s "Sync with the base branch" step and `fixer.md`'s "Merge conflicts"
section exist because an agent hits this class of conflict routinely enough to need a documented,
gated resolution path rather than treating it as exceptional.

**The decision.** A repo-local git merge driver (`scripts/merge_decisions.py`, registered by
`.gitattributes` and `git config merge.decisions-append.driver`) special-cases the "both sides purely
appended sections before the tail marker" shape and merges it automatically. Anything else — an edit to
an existing section, an edit to the Open-items section itself, a genuine same-slug collision with
differing content, or any parse ambiguity — falls through to exactly what an unconfigured merge would
have done (`git merge-file`'s own diff3 merge, conflict markers and all). The driver is registered at
every place this repository actually runs a merge of this kind: `review-fixer.yml`'s two sync-merge call
sites, `review-pipeline.yml`'s merge-tree recreation step (D-inherit-whole-range), and
`.devcontainer/setup.sh` for a human resolving the same conflict locally.

The recognized shape is exact: appended text must be one or more complete `## D-<slug> — …` sections,
nothing else, with at least one blank line separating the last one from the tail marker. Any prose
that isn't itself a decision section, a stray non-decision heading, or a section running straight
into the marker with no blank line at all makes the driver decline, not merge something malformed.

**Registration is conditional, because a broken driver is worse than none.** "Falls through to what
an unconfigured merge would have done" is a property of the driver's *decisions*, not of its
*existence*: a driver whose command cannot start does not fall through to anything. Git marks the
path conflicted, leaves "ours" in the worktree with no conflict markers, and records the path as
merely `UU` in the index — and `review-fixer.yml`'s commit step runs `git add -A` before its marker
gate, which resolves that entry and leaves `git ls-files -u` and `git diff --check --cached` both
empty. The gate passes and the pipeline pushes a merge that dropped every base-side change to a
normative spec file, with no marker anywhere for a human or an agent to notice. The whole marker
gate assumes an unresolved conflict leaves markers behind, and a non-executing driver is the one
thing that breaks that assumption. So every site registers through
`scripts/register_decisions_driver.sh`, which first runs the exact command git will run against a
synthetic append (must merge it) and a synthetic same-section conflict (must decline it *and* leave
markers), and registers only if both hold — clearing any earlier registration if they do not. A
missing or broken driver therefore degrades to real plain-git behaviour rather than failing the job:
a stuck PR buys no safety here, and the conflict is then resolved exactly as it was before this
decision existed. The fixer's sync step carries the matching backstop, refusing to continue if a
path routed to the driver comes back conflicted with no markers in it.

Splitting the file into one-decision-per-file was considered and rejected: the single append-only log is
load-bearing in `scripts/validate-decision-numbers.sh` and `tests/test_decision_numbers.py`'s
whole-file duplicate scan, `tests/test_reviewer_prompt_ranges.py`'s membership check, several CI
reviewer prompts under `.github/scripts/review/prompts/` that cite the slug scheme against this one
file, `pr-validation.yml`'s path-filtered `decisions` job, and `mkdocs.yml`'s single top-level nav entry
(with its own comment explaining the file is deliberately not split). A split would touch all of that to
solve a problem a merge driver solves without touching any of it — and it would still need a variant of
this same driver, or a numbering scheme, to keep the resulting many-file index itself append-safe.

**Invariants.** None of the six tabulated pipeline-core safety invariants (author exclusion, blind
orchestrator, fail-closed lenses, severity floors, termination, untrusted text) is in reach — none
constrains how a model's context is built, and this changes none of them. It does narrow
D-inherit-whole-range's tree-identity gate: a `docs/decisions.md` merge this driver resolved now
recreates identically and can inherit a prior verdict, where before this decision any conflict in
that file forced a full review regardless of shape (see docs/ci-pipeline.md's "Cycle control" and
"Syncing with the base branch", and QP7/QP8 in quality-principles.md, all updated alongside this
entry). That narrowing is deliberate and bounded — the gate stays a pure, deterministic function of
git content, never an LLM judgment, and only the append-only shape is affected. It holds only because
every registration executes the driver from the trusted `main` checkout (`$GITHUB_WORKSPACE` in
review-fixer.yml, `$GITHUB_WORKSPACE/main-checkout` in review-pipeline.yml), never the PR checkout
under review: the inherit step is a verifier and must not run code the commit it is verifying
supplied, and the sync steps hold `WORKFLOW_PAT` and must not execute a contributor's edit to this
file before anything is reviewed. The driver's own default is fail-closed within that boundary: any
condition it cannot confirm true (marker missing, an edit inside the head, any slug named on both
sides) makes it abstain to the exact behavior git would have used unconfigured, so a conflict of any
other shape is unaffected and reviewed exactly as before.

## D-resume-stall-guard — a wedged author session is killed on silence, and never resumed twice

**The problem.** D-resume-timeout made a hung `author-resume` *survivable*: the cold fixer fires after
the resume attempt reaches its outer deadline. That still permits the resume attempt to consume its
full 25-minute budget before the cold agent's own 30-minute budget starts, and the pipeline has no
cross-run memory that a particular session previously produced no fix.

**Operational premise.** This decision treats a resumed agent that emits no stream-json output for a
bounded interval as stalled, not as evidence that every silent agent is irrecoverable. The premise is
deliberately narrower than an empirical claim about all hangs: it defines when the pipeline stops
waiting and falls back. `ci-session-store.sh validate` cannot make that decision because it proves
only that a non-empty transcript exists on disk, not that a new turn is making progress.

**The decision.** Two independent mechanisms, because they answer different questions — "is this
attempt going anywhere?" and "should this attempt happen at all?".

1. **An idle deadline, not a shorter one.** `run-in-container.sh` watches the output log beside a
   resumed agent and kills it when it stops growing. The obvious alternative — cutting the 25-minute
   budget to something short — was rejected: a resume that is genuinely working needs the same time a
   cold fixer gets, and any budget short enough to catch a wedge quickly is short enough to kill a
   working fix mid-edit. Idleness separates the two cleanly, and `--output-format=stream-json`
   (already required for D-resume-timeout's diagnosis) is what makes it observable.

   Two thresholds distinguish startup from an active turn. Before the first byte the CLI has not
   exposed progress, so the deadline is **3 minutes**. After output has started, the longer
   **10-minute** deadline leaves room for a tool call such as a test suite or dependency sync. These
   values are policy choices, not measurements of a universal failure threshold. The outer `timeout`
   stays unchanged as the backstop for a process that spins noisily rather than idling. The guard is
   gated on `AGENT_RESUME=1`: reviewers, the cold fixer, the resolver and the author have no fallback
   to hand off to, so an idle-kill there would turn a slow tool call into a failed job.

   The cause is recorded in a flag file *before* the kill, and read before the exit code. An outside
   kill surfaces as 143/137, and 137 is the same code the outer deadline's `--kill-after` escalation
   produces — the exit status cannot distinguish them, so it is not asked to. The flag then routes
   into the existing `fixer-incomplete.sentinel`, so the fallback keeps its single trigger.

2. **A quarantine, so a wedged session is paid for once.** When a resume produces no fix, the fixer
   uploads a marker artifact named `session-hung-<agent>-<run-id>`. Before the next resume it queries
   that name and, on a live hit, skips straight to cold. This closes the open item D-resume-timeout
   left behind (#85 defect 3).

   Artifacts are the store because this fact is keyed by *session*, not by commit. The pipeline's
   other cross-run state lives in per-SHA commit statuses (docs/ci-pipeline.md), which cannot express
   it: a session outlives every SHA it is asked about, and each cycle asks from a new one. Artifacts
   are already the transport for the sessions themselves, are repo-wide, and are queryable by name.
   The marker outlives the 7-day session artifact it describes; once the session is gone there is
   nothing to resume anyway, so a stale marker costs nothing, and expired markers are ignored.

   The check **fails open**. If the query errors, the worst case is the old behaviour — one resume
   attempt, now bounded by the guard above. Failing closed would silently and permanently delete the
   resume path on an unrelated API blip, and `author-resume` is the better fixer when it works.

**What this does not fix.** Reviewer-stage staggering is a runner-capacity question, not a
pipeline-logic question, and remains outside this decision.

**Invariants.** None of the six pipeline-core invariants is in reach. Author exclusion and the blind
orchestrator are properties of the report pipeline, not of CI; this changes neither what may enter a
generator's context nor who may review what. It does not touch the fixer's gates — schema validation,
the lint refusal, the marker gate, the remote-head check — and a quarantined session changes only
*which* fixer runs, never what a fixer is permitted to push. The safety-relevant direction is that
both mechanisms fail toward the **cold** path, which is the more conservative of the two: it works
from recorded intent rather than remembered intent and cannot claim `body_clarification`.

## Open items for a future round

- A third completeness critic, chosen by measurement (D-completeness-pool-noise). The pool is down
  to two families, which is enough for a strong `accepted` but leaves no spare for a rule 8 top-up:
  a clean pass where one of the two reviews fails now ends at rule 11 `exhausted_unresolved`.
  Restoring depth means auditioning a candidate on that lens first — the thing that went wrong here
  was a slot filled on corpus-decorrelation reasoning alone. Worth pairing with the question of
  whether a *non*-roster-limited lens that cannot be topped up deserves a gentler terminal status
  than a budget exhaustion; that is a controller change, so it is its own decision.
- A non-gating diagnostic channel for minor-floor categories in `ra audition`
  (D-minor-floor-fixtures), and a `loaded_language` fixture to put in it. The harness currently has
  exactly one bar — post-clamp materiality — so the three minor-floor categories cannot be
  auditioned at all, and the corpus is barred from planting them. A second channel that credits a
  correct-category, correct-locus report at its own floor, reports it, and gates nothing would close
  that gap; it needs a consumer in `ra doctor` before it is worth adding.
- Per-call graded results in the audition cache (D-audition-rubric-identity). Storing raw
  `LensResult`s alongside `Metrics` would turn every rubric bump into a free regrade instead of a
  paid re-measurement, which is the thing that makes invalidating a verdict expensive enough to
  argue about. It needs a retention answer (the cache becomes stored model output, not a verdict
  record) and a migration story for the entry schema, so it is its own decision.
- Per-role CI cost telemetry, and a revisit of the D-ci-model-pinning tiers against measured
  proxy spend. The tiers are currently a bet on task shape; nothing in this repository can yet
  say whether they cost more or less than what they replaced.
- Making the audition self-diagnosing about its own corpus (D-control-soundness). The bug that
  decision fixes was invisible to every gate for exactly as long as it existed, and was only
  caught by reading a failing result. The audition already holds the evidence: when a control
  draws material issues from a *majority of independent critics at the same locus*, the parsimonious
  reading is that the control has a defect, not that every model invented the same one. Reporting
  that as "fixture suspect" instead of grading the models down would make this class self-limiting.
  It needs a locus-clustering rule and a majority threshold, and it changes what `ra audition`
  reports, so it is its own decision.
- Whether the refine audition censors its denominators the way the critic audition did
  (D-audition-failure-coverage). `refine_audition.Metrics` counts `schema_failures` separately and
  builds its rates from successful calls only, which is the same shape; nobody has yet checked
  whether a refine model can deterministically break on one fixture and still grade `fit`. The fix
  would be the same three fields, but the argument for `unfit` there is weaker — refinement is
  warn-only and degrades to silence by design (D-refine-audition) — so it is its own decision.
- Deterministic offline **source packets** for the evidence fixtures, and a second audition mode
  that runs under them (D-audition-source-mode). Today every measurement is taken with
  `sources=None`, so the sharpened `misrepresented_source` and the whole `fetched_sources_block`
  discipline — don't duplicate a mechanical `NOT FOUND`, don't read `BLOCKED` as fabrication,
  don't read an abstract as a body — are uncertified for a critic production runs with
  `verify_sources` on. The packets need no network; they are fixture data. Cost is a corpus
  addition per evidence fixture, a mode component in the cache key and in what `ra doctor`
  displays, and roughly a doubling of evidence-lens calls when both modes are run. It lands
  naturally with the fixture work in #118.
- Whether `misrepresented_source` can be meaningfully checked without fetching the source
  (v1 only checks on-its-face support); a later evidence layer (RA-011) would strengthen this.
- Calibration of `K` (plateau window), the hard cap, and defect-score weights against real runs.
- Verifying `Cf-Access-Jwt-Assertion` against the team's JWKS with an `aud` check, replacing the
  trusted email header (D-identity-header). The prerequisite for exposing the service beyond a small invited
  group, or for closing the direct-to-origin forgery path the tailnet posture leaves open.
- Teaching `ci-session-store.sh validate` to recognise an unresumable session from its *contents*.
  D-resume-stall-guard closed the operational half of this (#85 defect 3): a wedge is now killed on
  silence in minutes, and a session that has already failed is quarantined by marker artifact rather
  than re-attempted. What remains is the cheaper detection — `validate` still proves only that a
  non-empty transcript exists, so the first hang on any given session is still paid for once. Whether
  a transcript can be told apart from one `claude --continue` wedges on, without loading it, is an
  open question; the current answer is to make the attempt cheap rather than to predict it.
