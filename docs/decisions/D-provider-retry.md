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
