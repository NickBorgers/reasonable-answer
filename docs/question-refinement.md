# Question refinement — pre-run reframing suggestions

> **Status:** Proposed design, grounded in the production runs at
> https://reasonable-answer.featherback-mermaid.ts.net/ as of 2026-07-24.
> Not yet implemented. On implementation this becomes decision **D26** in
> [decisions.md](./decisions.md) (bump the invariant allowlist accordingly).

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

## Why: the production runs demonstrate the gap

The pipeline already knows questions arrive loaded — [bias.md §4](./bias.md)
says "The question is untrusted input, not a premise," and the
`unexamined_presupposition` category (major severity floor) exists to catch
writers who swallow a loaded framing. But that machinery runs *downstream*, and
the run history shows what that costs:

| Run | Question as asked | What the framing cost |
| --- | --- | --- |
| `run-75eb136b9bfb` | "Does Talarico back the police or support defunding them?" | False either/or. The report had to spend its conclusion rejecting the dichotomy ("not captured by a simple 'back the police' or 'support defunding' label"); 7 rounds, ended **needs human review**. |
| `run-40de6a7cdbf9` | "Why is it illegal to move an opossum in tx? …" | Unverified premise ("why is it illegal" assumes it is) plus a buried practical goal (the user wants lawful removal options). The report accepted the premise without a statute citation; 8 rounds, **exhausted unresolved** with the missing citation still a blocking defect. |
| `run-3e184fb11a36` | "Was closing schools in the US a net positive during COVID-19?" | "Net positive" demands a single scalar verdict over an unspecified population, outcome set, and timeframe — unanswerable as scoped. |
| `run-5af587189b89` | "Does fluoride in municipal drinking water have net negative impact on the health of ame…" | Same "net X" scalar-verdict shape. |
| `run-85c88f8c6ba4` | "Is it better to be honest or nice?" | Value question posed as either/or; the pipeline's evidence machinery has nothing to converge on. **Exhausted unresolved.** |
| `run-4d350e1d27a8` | "Did Donald Trump win the 2020 presidential election?" | Settled verification question; the draft's real energy went to *why the belief persists* — likely closer to what a genuine asker cares about. |

Runs take 10–25 minutes and burn a bounded confirmation budget. A ~3-second
suggestion that turns the opossum question into "Is it actually illegal to
relocate opossums in Texas, and what are my lawful options for dealing with
them?" prevents an 8-round exhaustion. Refinement is the same insight
`unexamined_presupposition` encodes, moved upstream to the one party who can
cheaply act on it: the asker, before the run starts.

## UX flow

1. User types in the existing question textarea on `/`
   (`render_index`, `web/render.py:116-153`).
2. Inline JS debounces: after ~1.5 s of typing pause and ≥ 20 characters, it
   `fetch()`es `POST /refine` with the current text (same-origin; permitted by
   the existing CSP, `connect-src 'self'`).
3. Response is either empty (nothing renders — the common case for well-posed
   questions) or 1–3 suggestions. Each renders as a chip below the textarea:
   a short intent label plus the reframed question, e.g.
   > **check the premise first** — Is it actually illegal to relocate opossums in Texas, and if so what are my lawful options?
4. Tapping a chip replaces the textarea content and keeps focus there; the user
   can edit further. Chips stay visible so they can switch back (the original
   wording is retained as the first chip once any swap happens).
5. "Start run" works at every moment, with whatever text is in the box. If the
   refine call is slow or errors, no chips appear and nothing else changes.
6. On submit, the form carries two extra hidden fields: the question as
   originally typed and the id of the chosen suggestion (if any), so the run
   record shows what refinement did.

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

| Transform | Trigger | Production example |
| --- | --- | --- |
| **Split the either/or** | Question offers exactly two labels for something that is a record or a spectrum | Talarico → "What is Talarico's actual record on police funding, reform, and accountability?" |
| **Check the premise first** | Question presupposes a contested or unverified fact | Opossum → "Is it actually illegal to relocate opossums in Texas, and if so what are my lawful options?" |
| **Name the outcome you care about** | "Net positive/negative", "better/worse" with no population, outcome, or timeframe | School closures → "What were the effects of US COVID-19 school closures on learning outcomes and on transmission?" |
| **Surface the real goal** | A practical need is buried inside a factual framing | Opossum (second half) → the lawful-options clause above |
| **Ask what's answerable** | Pure value question with no factual core | Honest-or-nice → "What does research say about how honesty and tact affect trust in relationships?" |
| **Ask the question behind the question** | The literal question is settled; the live question is adjacent | Trump 2020 → offer *both*: keep the verification question, and add "Why do many Americans believe the 2020 election was stolen?" |

The last row is the model of gentleness for the whole feature: when the
literal question is answerable, it stays available untouched; the suggestion
sits beside it, not over it.

## Mechanism

Follows the D25 pattern: opt-in config flag, byte-identical behavior when off,
and the seed-ingestion precedent (PR #25) of edge-side transformation that is
audited but never routes — refinement lives entirely at the web edge, never
inside the graph.

- **Config** (`config.py`): `refine.enabled` (default `false`), `refine.alias`
  (default: the orchestrator alias — already the designated lightweight
  judgment model), `refine.max_suggestions` (default 3). Excluded from the
  resume fingerprint.
- **Prompt** (`prompts.py`): `REFINE_SYSTEM` + `refine_user(question)`. The
  question is fenced with `DATA_FENCE`/`UNTRUSTED_NOTE` exactly like every
  other model-facing input (RA-010). The system prompt encodes the taxonomy
  above, the guardrails below, and an explicit instruction that returning zero
  suggestions is the correct output for a well-posed question.
- **Schema** (`schemas.py`): `RefinementSuggestions` — list of
  `{transform: <enum of the six>, label: str≤40, question: str≤200}` —
  validated via `LLMClient.structured` (`llm.py:318-359`), which already
  handles the capability ladder and bounded repair retries.
- **Route** (`web/app.py`): `POST /refine`. Same-origin enforcement via
  `_reject_cross_site` (it is a state-less POST, but keeping the CSRF check
  uniform is cheaper than explaining an exception). Applies the same
  `max_question_chars` validation as `POST /runs`. Returns JSON. Server-side:
  a small LRU cache keyed on the question hash (~15 min) so retyping and
  multiple pauses don't multiply LLM calls, and a per-IP rate limit reusing
  the worker backpressure conventions.
- **Rendering** (`web/render.py`): chips container + debounce/fetch/swap JS
  added to the index page. Inline script is CSP-compatible
  (`script-src 'unsafe-inline'`). No framework, matching the hand-written
  HTML convention.
- **Record** (`web/worker.py`, `store.py`): `submit()` gains
  `original_question`/`refinement_choice` passthrough; store a
  `refinement` event in `events.jsonl` with
  `{original, chosen, transform, suggestions_offered}`. Safe there — it is the
  user's own text, already persisted verbatim in `question.txt` (which, per
  the resume-fingerprint rule, always holds the question that actually ran).

## Guardrails: gentle, not corrective

These are the product constraints; the first four map directly onto
[bias.md §6](./bias.md)'s "what critics must NOT do", applied to suggestions:

1. **No meta-commentary on screen.** Chips never say "your question is
   loaded/biased." The label names the *move* ("check the premise first"),
   never the flaw.
2. **No steering.** A suggestion may not embed a verdict, flip the question's
   valence, or demand both-sides framing. It opens the question; it does not
   answer it.
3. **Preserve the subject.** The user's entities and topic survive every
   transform. "What is Talarico's record…" — never "Why do people
   mischaracterize politicians' records?"
4. **Silence is the default.** Zero suggestions is a first-class, expected
   output. Showing chips for every question destroys the magic and turns the
   feature into a nag.
5. **The original always wins ties.** Never auto-replace, never block "Start
   run", never require a choice. After any swap, the original wording remains
   available as a chip.
6. **Bounded rewrites.** ≤ 200 characters, phrased as a question, at most one
   transform per suggestion, at most three suggestions.

## Failure modes and costs

- Refine endpoint slow/erroring → no chips, run flow untouched. The feature can
  only add value, never subtract availability.
- Cost: one small structured completion per distinct question typed (cached),
  on the orchestrator-class alias — negligible next to a 10–25-minute run, and
  strongly positive whenever it averts a framing-driven exhaustion.
- Prompt-injection surface: the question is already treated as untrusted
  everywhere downstream; the refine prompt fences it identically. Worst case
  is a bad suggestion the user must actively tap, with the original one
  keystroke away.
- Bias surface: the suggester could itself introduce spin. Mitigations: the
  transform enum (no free-form rewriting rationale), guardrails 2–3 in the
  system prompt, and the refinement event record making every offered
  suggestion auditable per run. A future paired-fixture audition (mirror
  questions from opposing framings should yield mirror suggestions) is the
  same deferred idea as D24's bias-correlation audition.

## Non-goals

- Not a triage gate: no question is refused or held for refinement.
- Not inside the graph: `_intake` (RA-018) is unchanged; the graph still
  receives exactly one question and never knows refinement existed.
- Not a rewrite of the critic-side machinery: `unexamined_presupposition`
  stays as the downstream backstop for whatever framing survives to a run.

## Implementation checklist

1. `config.py` flag block (default off) + prod config enablement.
2. `prompts.py`: `REFINE_SYSTEM`, `refine_user` (fenced).
3. `schemas.py`: `RefinementSuggestions`.
4. `web/app.py`: `POST /refine` (+ `_reject_cross_site`, validation, cache,
   rate limit); extend `POST /runs` form fields.
5. `web/render.py`: chips UI + debounce JS on the index page.
6. `web/worker.py` / `store.py`: refinement event on submit.
7. Docs: `## D26` section in `decisions.md` (problem / mechanism /
   alternatives rejected / isolation accounting / known residuals), bump the
   `D1`–`D25` allowlist in `.github/scripts/review/prompts/invariant.md:18`
   to `D26`, register this file in `DESIGN.md`'s Document map, cross-reference
   from `bias.md §4`.
8. Tests: schema validation, zero-suggestion path, CSRF rejection, cache hit,
   submit-with-refinement event shape; prompt fixtures for each of the six
   transforms using the production questions above.
