# Design decisions & adversarial-review log

This page is the **registry index**. It holds the identifier scheme, the old-number mapping, the
early design-dialogue tables, every adversarial-review finding table (`RA-*`, `RB-*`, `RC-*`,
`RG-*`), the RA-019 test matrix, and the open items. It does **not** hold the decision prose:
each decision is one file, `docs/decisions/D-<slug>.md`, named for the slug it defines
(D-decision-per-file).

## Identifiers: decision slugs, and the old-number mapping

Every decision in this log is named by a **slug derived from its subject** — `D-source-verification`,
`D-decision-slugs` — not by a number from a shared counter. A slug is coined by the PR that writes the
decision and describes what the decision does, so two concurrently-open PRs cannot pick the same
identifier by construction and no PR needs to read another to choose one. Because slugs have no
order, a *range* of them is meaningless — cite a group by enumerating it (`D-per-lens-critics` and
`D-critic-only-specialists`), never as a span.

**Where a decision lives.** A decision written as prose is its own file at
`docs/decisions/D-<slug>.md`, whose first line is the `## D-<slug> — …` heading. The filename **is**
the identifier, so a citation resolves to a path without searching: `D-writer-disputes` is
[docs/decisions/D-writer-disputes.md](./decisions/D-writer-disputes.md). Writing a decision therefore
adds a file and edits nothing, which is what makes two concurrently-open decision-bearing PRs
conflict-free by construction rather than merely collision-managed (D-decision-per-file, which
retires the shared insertion point D-decisions-merge-driver and D-decisions-merge-regions existed to
resolve).

**There is no ordering.** The predecessor scheme carried order by position in a single file. Nothing
carries it now, and nothing needs to: the doctrine above already says a slug implies no sequence.
When a reading order actually matters, `git log --diff-filter=A --format='%as %s' -- docs/decisions/`
recovers the real one — when each decision was made — which position in a hand-appended file only
approximated.

Decisions carry two surface forms: rows in the "Key design decisions" tables below (`| D-<slug> | … |`)
and `## D-<slug> — …` prose sections, one per file under `docs/decisions/`. Either form is a
definition, and `scripts/validate-decision-numbers.sh` refuses a slug defined twice across **both**
forms and across every file, so one decision cannot be silently defined in a table and a section at
once.

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
| Retrieval / web search (D-retrieval-opt-in) | offline-when-off (no `tools` offered, prompt byte-identical to the pre-retrieval path); startup fails closed on a missing credential **and** on a tool-incapable writer; `probe_tool_calling` returns False when a model accepts `tools` and completes without calling one, while any raised call failure leaves capability unknown and aborts the probe (D-probe-capability-evidence); per-**run** query budget (not per-call) enforced under concurrency; budget exhaustion and fetch failure surfaced to the model as text, never as silence; results fenced as untrusted (RA-010); the agentic tool loop terminates — the exhausted round drops `tools` and forces prose — and `Completion.tool_calls` matches the number executed; the query string never reaches a log (RA-016) |
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

## Security review — 2026-07 (web submission hardening)

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| RC-007 | med | Run submission is unbounded in both queue depth and disk footprint. `RunWorker.submit()` enqueued onto a `queue.Queue()` with no `maxsize` and no rate limit, and each submission immediately wrote a persistent run directory. Concurrency bounds token *spend* but not the number of queued runs, the memory they hold, or the run dirs they leave on disk; `recover()` re-enqueues them all on boot. A single burst — a script, or the companion CSRF vector — could create thousands of runs and directories, and `Registry.list()` reads every run dir on each `GET /`. | **Fixed (D-bounded-submission).** `submit()` refuses with HTTP 429 past `max_queue_depth`, and a fixed-window per-identity limiter (`submit_rate_max`/`submit_rate_window_seconds`) throttles bursts. Both checks precede any disk write, so a refusal costs nothing. The web server also runs an automatic content-only retention sweep so disk reclamation no longer waits on a manual `purge`. |

## Open items for a future round

- Making `probe_structured_output` measure against a schema shaped like a real one, not `_Probe`'s
  trivial `{"ok": boolean}` (D-dereferenced-schema). The probe's own schema has no nested model and
  therefore no `$ref`, so it cannot detect whether an alias's structured-output path handles the
  reference-bearing schemas this application actually sends, including `CritiqueOutput`.
  `_dereference` removes that application-side dependency, but the probe gap remains distinct: it
  does not exercise the schema feature whose capability it would be used to establish. Choosing a
  representative probe schema without turning every startup into a second `CritiqueOutput`-sized
  request needs its own evidence before it is a decision rather than a guess.
- Making `probe_tool_calling` measure the loop it licenses, not a one-shot `ping`
  (D-writer-failure-class). The probe offers a single trivial `ping` tool and asks only whether
  *any* tool call comes back. Production runs the multi-round agentic loop in `LLMClient.complete`
  — tool call, tool result fed back as fenced untrusted text, then prose — bounded by
  `search.max_tool_rounds`. So the probe licenses something strictly harder than it measures, which
  is the same defect class as the `probe_structured_output` gap above, with the same consequence: a
  capability the startup check has certified can still fail on the path that actually uses it. Not
  hypothetical. `nemotron-3-ultra` passed the probe and then failed the real writer loop, returning
  unparsed tool-call markup as message content (`_unparsed_tool_call`, the `unparsed_tool_markup`
  failure class); it read as a broken model and a roster retirement was proposed on that basis. The
  defect was upstream routing, not the model — the same alias was clean on one pinned OpenRouter
  host and failed every call on another
  ([operator record](./model-evaluation-record-2026-08-10.md#upstream-host-probes)) — and it was
  fixed deployment-side by pinning `provider.order`.
  That is also the tension, and it is why this is recorded rather than fixed: with `search.enabled`
  a tool-incapable writer fails startup closed, so a loop-shaped probe would fail a whole run
  closed on a model that is sound once its upstream is pinned — answering a routing problem with a
  roster ban. D-writer-failure-class declined to tighten the probe for that reason and that
  reasoning still holds. What a representative probe should exercise, and what it should cost every
  startup, needs its own evidence before it is a decision rather than a guess.
- A third **logic**-lens family — the search this item used to ask for has been run, and it
  returned nothing (D-writer-failure-class surfaced the survey; the gap itself is the fit-first cost
  stated in D-minimax-retirement). The 2026-08-10/11 audition measured eight candidates on the
  logic lens against the shipped fixture corpus and the shipped `max_control_material_rate: 1.00`:
  seven produced an interpretable verdict, spanning six vendors and both weight classes, and **none
  of them was a second `fit`**. `qwen/qwen3.5-397b-a17b` — the candidate this item nominated, and
  the one that fits every paper criterion at 397B/A17B, Apache 2.0, ~200GB at 4-bit and a genuinely
  new family — was added to the proxy, provider-pinned, and graded `unfit` at 1.21 invented material
  issues per sound control. The rates, the corpus identity and the one void run are in the
  [operator record](./model-evaluation-record-2026-08-10.md#recorded-slot-results); the procedure,
  including the three infrastructure bugs that cost a wasted round, is in
  [model-evaluation.md](./model-evaluation.md). Extend that record rather than restarting the
  survey.
  Two things the survey does support, stated no more strongly than it earns. First, seven candidates
  produced interpretable logic-lens verdicts and none of them was a second `fit`, so the
  `roster_limited` warning on every round `mistral-large-3` authors is not a gap that this search
  closed. That is a result about the seven models tried on the date they were tried; it is **not** a
  claim that the sample exhausts what is purchasable, and a later search may well find a candidate
  this one did not reach. Second, among those seven the failures were failures of *precision* rather
  than of detection: each had 1.00 lens sensitivity and perfect `obvious`-tier recall. That is a
  pattern worth knowing before the next attempt, not a reason to rule one out.

  Two other routes are worth recording alongside a further search, not in place of it: a materially
  different pool — weights that were not purchasable in 2026-08, or a self-hosted candidate outside
  the ~450GB ceiling — or a materially different approach to the lens itself, such as a rubric that
  scores precision on hedged prose differently. Each is its own decision.
  Still true from the original survey and worth keeping: `nvidia/nemotron-3-super-120b-a12b`
  (120B/A12B, open weights *and* training data, post-trained for tool calling, ~35GB at 4-bit) as a
  cheap tool-competent **writer**, in `nemotron`'s existing family; and `moonshotai/kimi-k2.6`, a
  genuinely new family excluded by the same ~450GB arithmetic as `kimi-k3`, at ~594GB INT4. Also
  unchanged: `llama-4-scout` returned 0 issues on all 6 evidence calls of run-d5934276fafd,
  `qwen3.7-max`'s weights are closed, and `deepseek-v4-pro` and `kimi-k3` are excluded by the
  arithmetic.
  These are **logic-lens verdicts only**. A critic's noise rate is lens-specific, and this roster is
  the proof: `mistral-large-3` is `fit` on logic and `unfit` on completeness
  (D-completeness-pool-noise). Nothing measured here transfers to the two items below, neither of
  which was measured in that session.

- Audition a replacement evidence-lens candidate (D-minimax-retirement). The lens now runs two
  `marginal` critics, and `gemma4`'s 0.50 sensitivity sits below the warn line; the roster needs a
  third family measured against the full corpus before `review.depth` can ever rise. Follow
  [model-evaluation.md](./model-evaluation.md) for the procedure. No evidence-lens measurement
  exists for any of the logic-lens candidates above, and their logic verdicts do not predict one.

- A third completeness critic, chosen by measurement (D-completeness-pool-noise). The pool is down
  to two families, which is enough for a strong `accepted` but leaves no spare for a rule 8 top-up:
  a clean pass where one of the two reviews fails now ends at rule 11 `exhausted_unresolved`.
  Restoring depth means auditioning a candidate on that lens first, per
  [model-evaluation.md](./model-evaluation.md) — the thing that went wrong here
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
- Verdict instability near the audition threshold
  ([operator record](./model-evaluation-record-2026-08-10.md)). The 2026-08-10/11
  logic-lens audition measured `claude-haiku-4-5` at 2.04 invented material issues per sound
  control on one run and 1.04 on a re-run of the identical corpus — roughly a 2x swing against the
  `max_control_material_rate: 1.00` ceiling, at the default `audition.repetitions: 3` (24 control
  runs). Both runs graded `unfit` and the directional conclusion (no candidate came close to
  `mistral-large-3`'s 0.08) is unaffected, because that gap is an order of magnitude — but a
  single-run verdict turning on a rate close enough to the ceiling that a swing of that magnitude
  would cross it is not settled. Two repeated values are not a sampling analysis and no confidence
  interval was computed, so no numeric band is claimed here. Raising `audition.repetitions` for
  such a candidate, or reporting an interval alongside the point estimate, should precede any
  roster decision that rests on it. This
  is recorded as an open item rather than a decision because no threshold or default changed here:
  doing either is a measurement-methodology question, not a documentation one.
