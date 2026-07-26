# Question refinement — pre-run reframing suggestions

> **Status:** Implemented; this is decision **D26** in [decisions.md](./decisions.md).
> The questions below are synthetic, each chosen to illustrate one framing
> category; no private run content appears in this spec.
> Revised 2026-07-25 after an adversarial design review (findings QR-001–017).

## What it is

A gentle, optional step between typing a question and starting a run. While the
user pauses after typing, the system quietly asks a small model whether the
question — as worded — encodes a framing that will fight the pipeline (a false
dichotomy, an unverified premise, an unscoped "net positive?" verdict). If, and
only if, it finds a materially better articulation of what the user appears to
be getting at, up to three suggestion chips appear under the textarea. Tapping
a chip swaps the question text (still editable). The "Start run" button is
never blocked, the original wording is never criticized on screen, and when the
question is already well-posed nothing appears at all.

The intent is not correction. It is the feeling that the tool understood what
you were really asking even though you didn't phrase it quite that way.

## Why: loaded questions cost downstream rounds

The pipeline already knows questions arrive loaded — [bias.md §4](./bias.md)
says "The question is untrusted input, not a premise," and the
`unexamined_presupposition` category (major severity floor) exists to catch
writers who swallow a loaded framing. But that machinery runs *downstream*, and
the common framings show what that costs (illustrative, synthetic questions):

| Framing | Illustrative question | What the framing costs downstream |
| --- | --- | --- |
| False either/or | "Does the mayor back the zoning plan or oppose it?" | Two labels for what is really a record or a spectrum. The report can burn its conclusion rejecting the dichotomy instead of answering, and end up needing human review. |
| Unverified premise + buried goal | "Why is it illegal to keep backyard chickens here?" | "Why is it illegal" assumes it is, and hides the practical goal (what the asker may lawfully do). The report can accept the premise with no citation and exhaust its rounds with that gap still blocking. |
| Unscoped "net X" verdict | "Was the new policy a net positive?" | "Net positive" demands a single scalar over an unspecified population, outcome set, and timeframe — unanswerable as scoped. |
| Value question as either/or | "Is it better to lead with data or with intuition?" | A value question with nothing for the evidence machinery to converge on; tends to exhaust unresolved. |
| Settled verification | "Did Apollo 11 land on the Moon?" | The literal question is settled; the draft's real energy goes to the adjacent question (why a contrary belief persists) — likely closer to what a genuine asker cares about. |

Runs take 10–25 minutes and burn a bounded confirmation budget. A ~3-second
suggestion that turns "Why is it illegal to keep backyard chickens here?" into
"Is it actually against the local rules to keep backyard chickens, and what are
my options if it is?" prevents a multi-round exhaustion. Refinement is the same
insight `unexamined_presupposition` encodes, moved upstream to the one party who
can cheaply act on it: the asker, before the run starts.

## UX flow

1. User types in the existing question textarea on `/`
   (`render_index`, `web/render.py:116-153`).
2. Inline JS debounces: after ~1.5 s of typing pause and ≥ 20 characters, it
   `fetch()`es the refinement route with the current text (same-origin; permitted
   by the existing CSP, `connect-src 'self'`). The route is `POST /refine`; the
   browser-facing URL the script actually emits is base-path aware —
   `RA_ROOT_PATH + "/refine"` (D29), which is the bare `/refine` root-origin
   identity only when `RA_ROOT_PATH` is unset. Each request aborts any in-flight
   predecessor (`AbortController`) and carries the exact text it was issued
   for; a response is applied only if that text still matches the textarea,
   so a slow response for stale text can never replace fresher chips.
3. Response is either empty (nothing renders — the common case for well-posed
   questions) or an **offer**: an opaque `offer_id` plus 1–3 suggestions. Each
   renders as a chip below the textarea: a short intent label plus the
   reframed question, e.g.
   > **check the premise first** — Is it actually against the local rules to keep backyard chickens, and what are my options if it is?
4. Tapping a chip replaces the textarea content and keeps focus there. On the
   first swap within an offer, the text the user had at that moment is
   captured as the **restore text** and prepended as a chip, so switching back
   is one tap. Manually editing the textarea clears the selection (the
   provenance fields reset to "no suggestion chosen") but leaves the chips
   visible. A new offer (from a later pause) replaces chips and restore text
   wholesale.
5. "Start run" works at every moment, with whatever text is in the box. If the
   refine call is slow, sheds load, or errors, no chips appear and nothing
   else changes.
6. On submit, the form carries two extra hidden fields: `refine_offer_id` and
   `refine_selected` (index, or empty for "typed/edited"). These are claims,
   not evidence: the server validates them against its own offer record
   (below) and copies provenance from that record, never from the client.
   Client-side, the page also enforces a refinement budget: at most 5
   **request attempts** per page load — the counter increments before
   dispatch and counts every outcome (non-empty, empty, failed, shed, or
   aborted after dispatch), so degraded responses cannot extend the budget —
   and after a completed request a new one is issued only when
   the text has changed by more than a trivial edit — Levenshtein distance
   ≥ 12 between the whitespace-normalized (trimmed, runs of whitespace
   collapsed) new text and the last requested text.

Why debounced-ambient rather than an interstitial after "Start run": an
interstitial makes every user pay a confirmation click to benefit the minority
of loaded questions, and it reads as a correction gate ("are you sure?").
Ambient chips appear only when the model has something genuinely better, cost
no clicks when ignored, and are what makes it feel like understanding rather
than review.

## The reframe taxonomy

The model is instructed to propose a suggestion only when one of these bounded
transforms applies, and to say which one (the chip's intent label). Every
transform preserves the user's subject and target — it never changes *what* the
question is about, only *how it is posed*.

| Transform | Trigger | Illustrative example |
| --- | --- | --- |
| **Split the either/or** | Question offers exactly two labels for something that is a record or a spectrum | Mayor/zoning → "What is the mayor's actual record on the zoning plan — where have they supported, amended, or opposed it?" |
| **Check the premise first** | Question presupposes a contested or unverified fact | Chickens → "Is it actually against the local rules to keep backyard chickens, and what are my options if it is?" |
| **Name the outcome you care about** | "Net positive/negative", "better/worse" with no population, outcome, or timeframe | Four-day week → "What were the effects of a four-day work week on output and on employee retention?" |
| **Surface the real goal** | A practical need is buried inside a factual framing | Chickens (second half) → the options clause above |
| **Ask what's answerable** | Pure value question with no factual core | Data-vs-intuition → "What does research say about when data-driven and intuition-driven decisions each perform better?" |
| **Ask the question behind the question** | The literal question is settled; the live question is adjacent | Apollo 11 → offer *both*: keep the verification question, and add "Why does the belief that the Moon landing was faked persist?" |

The last row is the model of gentleness for the whole feature: when the
literal question is answerable, it stays available untouched; the suggestion
sits beside it, not over it. It is also the transform with the strongest
steering surface — it authorizes the model to infer an unstated concern — so
it carries extra constraints: the adjacent question must itself be factual and
answerable, must not presume causes or attribute beliefs to populations beyond
what is verifiable, and may only ever appear *in addition to* the unchanged
literal question. This transform ships **disabled** and is enabled only after
a paired-fixture audition passes: mirror questions posed from opposing
framings must yield mirror suggestions (the same deferred methodology as
D24's bias-correlation audition).

## Mechanism

Follows the D25 pattern: opt-in config flag, byte-identical behavior when off,
and the seed-ingestion precedent (PR #25) of edge-side transformation that is
audited but never routes — refinement lives entirely at the web edge, never
inside the graph.

- **Config** (`config.py`): a `RefineConfig` model with `extra="forbid"` and
  bounded fields — `enabled: bool = False`, `alias: str` (default: the
  orchestrator alias), `max_suggestions: int` (1–3), `timeout_seconds: float`
  (0.5–15, default 5), `cache_entries: int` (16–4096, default 256),
  `cache_ttl_seconds: int` (60–86400, default 900), `rate_max: int` (1–100,
  default 10) / `rate_window_seconds: int` (1–3600, default 60) as separate
  refinement limits, `concurrency: int` (1–4, default 2),
  `offer_entries: int` (64–8192, default 512) / `offer_ttl_seconds: int`
  (300–7200, default 1800), `orphan_linger_seconds: int` (0–300, default
  30), and
  `enabled_transforms: set[str]` (defaults to all except
  `question_behind_the_question`). Excluded from the resume fingerprint.
  When `enabled`, the effective alias participates in startup identity
  resolution and structured-output capability probing exactly like roster
  aliases (`llm.resolve_identities`) — a bad or schema-incapable alias fails
  at startup, not on the first user request.
- **Service** (`web/refine.py`, new): a `RefinementService` owning the LLM
  call, validation, cache, offer records, rate limiter, and concurrency
  semaphore, constructed with injected `LLMClient`, clock, and config. The
  route stays thin: request validation in, JSON out. The service gets
  explicit startup/shutdown hooks alongside the worker's.
- **Prompt** (`prompts.py`): `refine_system(enabled_transforms)` +
  `refine_user(question)`. The
  question is fenced with `DATA_FENCE`/`UNTRUSTED_NOTE` exactly like every
  other model-facing input (RA-010). The system prompt encodes the taxonomy
  above, the prompt-policy guardrails below, and an explicit instruction that
  returning zero suggestions is the correct output for a well-posed question.
  It is composed from the *enabled* subset only, so a disabled transform is
  never even described to the model.
- **Schema and validation** (`schemas.py`): `RefinementSuggestions` — list of
  `{transform: <enum of the six>, label: str≤40, question: str≤200}` —
  validated via `LLMClient.structured` (`llm.py:318-359`), called with a
  small `max_tokens` (~700), at most **one** repair retry, and — because the
  client is synchronous — a **provider-level request timeout**
  (`timeout_seconds` passed through to the underlying OpenAI/LiteLLM request),
  not merely a wrapper deadline. What that timeout guarantees is **client
  occupancy**: the connection is closed and the concurrency permit is held
  until the underlying call actually ends, never released early on HTTP-side
  degradation. Whether the upstream backend stops generating on disconnect is
  a LiteLLM/backend property the design does not assume, and without verified
  cancellation no client-side mechanism can *guarantee* a ceiling on
  aggregate backend work. The guarantees are therefore layered honestly:
  `concurrency` is a hard ceiling on live client calls; a timed-out call's
  permit is additionally held for an orphan-linger window
  (`orphan_linger_seconds`, 0–300, default 30) as a **best-effort** damper on
  orphan accumulation — most ~700-token generations finish well inside it,
  but a stalled backend can outlive any window; and `max_tokens` bounds each
  orphaned generation's output. Deployments that need a strict aggregate
  guarantee get it by isolation, not lingering: point `refine.alias` at a
  dedicated backend/resource pool (linger may then be set to 0 if that
  backend's disconnect-cancellation is verified). Malformed or timed-out
  output is treated as an empty result. On top of schema validation the service applies deterministic
  checks per entry — transform is in `enabled_transforms`, length caps,
  ends with `?`, no control characters, no duplicates, count ≤
  `max_suggestions` — and silently drops entries that fail.
- **Route** (`web/app.py`): `POST /refine`. Same-origin enforcement via
  `_reject_cross_site` (keeping the CSRF check uniform is cheaper than
  explaining an exception). Applies the same `max_question_chars` validation
  as `POST /runs`. Rate limiting reuses the existing `_identity(request)`
  semantics and `RateLimiter` class with the refinement-specific limits —
  identity-keyed like `/runs`, not IP-keyed. A concurrency semaphore
  (`refine.concurrency`) bounds simultaneous LLM calls; when it is saturated
  or the deadline cannot be met, the endpoint returns an empty result
  immediately (shed load, never queue). Refinement shares the LiteLLM proxy
  with runs, so its resource claim is stated honestly: not "zero impact" but
  a **small fixed ceiling on live client calls** — at most `concurrency`
  in-flight completions of ≤ ~700 tokens each, never queued. Deployments that
  want strictly zero contention with run traffic should point `refine.alias`
  at an alias **routed to a dedicated backend/resource pool** — a different
  alias name alone proves nothing about isolation and can even add contention
  via model swapping; any finer-grained prioritization between refinement and
  run traffic is the proxy's scheduling concern, not this service's.
- **Cache**: a bounded, thread-safe TTL cache inside the service, keyed by
  (normalized question text, prompt/schema version, effective alias,
  `max_suggestions`, `enabled_transforms`). Validated successes are cached
  **including empty results**; failures are not. Identical in-flight misses
  are coalesced so simultaneous requests for the same text cost one
  completion. Serving a cache hit still mints a fresh offer record.
- **Offer records**: every non-empty response is recorded server-side before
  it is returned, keyed by `offer_id` in an in-memory map: `_OfferRecord`
  holds `{question_at_offer, suggestions, expires_at}` (`web/refine.py`) —
  there is no `created_at` field; the record tracks its own expiry directly,
  and `offer_id` itself is the map key rather than a field inside the record.
  The map is bounded by `offer_entries` with TTL `offer_ttl_seconds` and LRU
  eviction when full. `offer_id` is `secrets.token_urlsafe(24)` — a fixed
  32-character URL-safe token. At submit, the claimed id is validated against
  that exact format and length **before** any lookup; a malformed value
  short-circuits to a constant `unverified` status and the supplied bytes are
  never written to `events.jsonl` or anywhere else.
  At submit, `refine_offer_id`/`refine_selected` are looked up: provenance is
  marked `verified` only when the offer exists, the index is valid, **and the
  submitted question (after the same trim applied by `POST /runs`) is exactly
  equal to that suggestion's text** — a valid offer id alone proves nothing
  about the question that actually ran. On any mismatch, or if the offer is
  missing (expired, evicted, restart), the run proceeds normally, the event
  records an `unverified` claim, and no chosen-suggestion content is recorded
  as applied. Client-submitted text is never trusted as provenance.
- **Record and retention** (`web/worker.py`, `store.py`): full refinement
  content — question at offer, suggestions offered, chosen text — is written
  to `runs/<run_id>/refinements/refinement.json`, with `refinements` added to
  `CONTENT_DIRS` so the existing directory-level `purge()` removes it
  alongside reports and critiques (the purge mechanism only handles
  directories, so a root-level file would silently escape it). This only
  happens when `RefinementService.resolve()` actually returns a
  `Refinement`. `resolve()` returns `None` — and `RunWorker.submit()` then
  writes **neither** `refinement.json` **nor** a `refinement` event, not even
  one with a placeholder status — when nothing was claimed at submit time (no
  `refine_offer_id` and no `refine_selected`). When `resolve()` does return a
  `Refinement`, `events.jsonl` (which survives purges) gets only non-content
  signal: `{offer_id, transform, selected_index, question_sha256,
  original_sha256, provenance: verified|unverified}`. There is no `none`
  provenance value; "nothing was claimed" is signaled by the *absence* of a
  `refinement` event for the run, never by a value carried inside one.
  `question.txt` continues to hold exactly the question that ran, per the
  resume-fingerprint rule.
- **Rendering** (`web/render.py`): chips container + debounce/fetch/swap JS
  added to the index page. Inline script is CSP-compatible
  (`script-src 'unsafe-inline'`). All model-derived values (labels,
  questions) are inserted with `createElement`/`textContent` only —
  `innerHTML` is prohibited for any model- or user-derived string, since the
  CSP's `'unsafe-inline'` would make DOM XSS exploitable. No framework,
  matching the hand-written HTML convention. With `refine.enabled = false`
  the rendered index page is byte-identical to today's.

## Guardrails: gentle, not corrective

Two distinct layers. **Enforced** means deterministic server-side validation;
**prompt policy** means best-effort instructions whose adherence is tested
statistically with fixtures, not assumed.

Enforced:

1. **Bounded output.** ≤ 200 characters per suggestion, phrased as a question
   (ends with `?`), a valid transform label from the enabled set, no control
   characters, no duplicates, at most `max_suggestions` chips.
2. **The original always wins ties.** Never auto-replace, never block "Start
   run", never require a choice. After any swap, the restore text (what the
   user had at first swap) remains available as a chip; manual edits clear
   the selection provenance.
3. **Silence on failure.** Any validation, timeout, or capacity problem
   degrades to zero suggestions.

Prompt policy (mapped onto [bias.md §6](./bias.md)'s "what critics must NOT
do", applied to suggestions; fixture-tested per transform):

4. **No meta-commentary on screen.** Chips never say "your question is
   loaded/biased." The label names the *move* ("check the premise first"),
   never the flaw.
5. **No steering.** A suggestion may not embed a verdict, flip the question's
   valence, or demand both-sides framing. It opens the question; it does not
   answer it.
6. **Preserve the subject.** The user's entities and topic survive every
   transform. "What is the mayor's record on the zoning plan…" — never "Why do
   people mischaracterize politicians' records?"
7. **Silence is the default.** Zero suggestions is a first-class, expected
   output. Showing chips for every question destroys the magic and turns the
   feature into a nag.
8. **One transform per suggestion.**

## Failure modes and costs

- Refine endpoint slow, saturated, or erroring → no chips, and the run
  *control flow* is untouched. At the resource level the guarantee is a small
  fixed ceiling on live client calls, not zero: at most `concurrency` short
  completions in flight, shed (never queued) beyond that. Zero contention is
  a deployment property — an alias routed to a dedicated backend/resource
  pool — not something any alias name or client-side control can provide.
- Cost: bounded on both sides. Client: ≤ 5 request attempts per page load
  (counted at dispatch, regardless of outcome), meaningful edit distance
  required between requests. Server: identity-keyed rate
  limits, TTL cache (including cached empties), coalesced in-flight misses,
  ~700-token completions on the orchestrator-class alias. Negligible next to
  a 10–25-minute run, and strongly positive whenever it averts a
  framing-driven exhaustion.
- Prompt-injection surface: the question is already treated as untrusted
  everywhere downstream; the refine prompt fences it identically. Model
  output is schema-validated, deterministically filtered, and rendered via
  `textContent` only. Worst case is a bad suggestion the user must actively
  tap, with the restore chip one tap away.
- Forged provenance: submit-time refinement fields are claims validated
  against server-side offer records; an attacker (or a confused client) can
  at most cause an `unverified` provenance mark, never a fabricated audit
  trail.
- Bias surface: the suggester could itself introduce spin. Mitigations: the
  transform enum (no free-form rewriting rationale), prompt-policy guardrails
  5–6, the highest-risk transform disabled until its paired-fixture audition
  passes, and `refinement.json` making every offered suggestion auditable
  per run.

## Non-goals

- Not a triage gate: no question is refused or held for refinement.
- Not inside the graph: `_intake` (RA-018) is unchanged; the graph still
  receives exactly one question and never knows refinement existed.
- Not a rewrite of the critic-side machinery: `unexamined_presupposition`
  stays as the downstream backstop for whatever framing survives to a run.

## Implementation map

The design above is normative; this section maps it to where it landed, not to remaining work — except item 9's tests, which are split into what landed and what is still intended coverage, since test surface is the one place this PR's actual state and the original design diverge.

1. `config.py`: `RefineConfig` (`extra="forbid"`, bounded fields, default
   off) + prod config enablement; startup alias resolution/probing when
   enabled.
2. `web/refine.py`: `RefinementService` (LLM call, validation, TTL cache,
   offer records, limiter, semaphore) with injected dependencies.
3. `prompts.py`: `refine_system(enabled_transforms)`, `refine_user` (fenced).
4. `schemas.py`: `RefinementSuggestions`.
5. `web/app.py`: `POST /refine` (+ `_reject_cross_site`, validation, shed
   behavior); extend `POST /runs` to validate offer claims.
6. `web/render.py`: chips UI + debounce/abort/swap JS (`textContent`-only
   DOM construction).
7. `web/worker.py` / `store.py`: `refinement.json` writer (purgeable) +
   non-content `refinement` event in `events.jsonl`.
8. Docs: `## D26` section in `decisions.md` (problem / mechanism /
   alternatives rejected / isolation accounting / known residuals), the
   valid-ID allowlist in `.github/scripts/review/prompts/invariant.md` bumped
   to `D1`–`D26`, this file registered in `DESIGN.md`'s Document map, and a
   cross-reference from `bias.md §4`.
9. Tests — what actually landed in this PR (`tests/test_refine.py`,
   `tests/test_refine_web.py`, `tests/test_report_store_llm.py`):
   - Service unit tests: schema + deterministic validation (each rule),
     zero-suggestion path, cache hit/expiry/eviction, coalesced concurrent
     identical misses, timeout and semaphore-saturation shedding (permit held
     until the underlying client call terminates, then through the
     orphan-linger window on timeout — asserting client-call termination and
     permit lifetime, not upstream cancellation, which is only tested via
     backend observation in deployments that claim it), offer expiry/restart
     and a non-matching selection → `unverified` provenance,
     nothing-claimed → `resolve()` returns `None`, disabled transform
     filtered, offer-map LRU eviction at `offer_entries`.
   - Route tests: CSRF rejection, `/refine` 404s when disabled, rate-limit
     degrading to an empty 200, oversized question rejected, forged/expired/
     malformed offer claims on `POST /runs` never persisting the raw claimed
     bytes, no-refinement-fields writes no refinement record, resuming a run
     does not rewrite an existing refinement record.
   - Rendering tests: `refine.enabled = false` produces byte-identical index
     HTML; the enabled index carries the chips container, hidden fields, and
     inline script; hostile suggestion text is proven inert two ways — the
     `/refine` JSON response round-trips it as an inert string (server-side
     half), and static assertions on the emitted `REFINE_JS` string confirm
     it never uses `innerHTML` and only builds chip text via `textContent`
     (client-side half). These are string/JSON-level checks against server
     output, not a browser/DOM execution harness.
   - Store tests: `refinement.json` purged by the content purge;
     `events.jsonl` entry contains hashes and enum fields, no question or
     suggestion text.
   - Startup test: enabled config with an unknown/schema-incapable alias
     fails at boot.

   Not covered by this PR — listed here as intended coverage, not landed:
   - **Edit-distance gate.** The normalized-Levenshtein threshold that gates
     re-request dispatch lives entirely in client-side JS (`web/render.py`);
     no test exercises insertion/deletion/substitution/short-edit behavior
     against it.
   - **A real rendering/JS execution harness**, as opposed to the static
     string checks above: the stale-response race (a slow, superseded
     response must not clobber newer chips), edit-after-selection clearing
     provenance, and submit-while-refinement-pending are all unexercised.
   - **Prompt fixtures**: each enabled transform against the production
     questions above, plus paired ideological mirror fixtures gating
     `question_behind_the_question`, are design intent for a later pass —
     none exist yet.
