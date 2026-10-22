## D-writer-must-read — a writer that drafts without reading is retried once, then rotated, then shipped with a warning

**The finding.** D-read-before-citing put "search and `read_source` before citing" in the writer
prompt and checks the result mechanically, but nothing made a writer comply. Production showed a pool
member that answers immediately whenever tools are optional: every round it authored had zero
searches and zero reads, its first draft was written from memory, and a share of its listed sources
did not resolve. Writers rotate round-robin (D-writer-rotation-pool), so every round that member
authors starts from memory, and a writer that never reads can discharge a read finding only by
deleting the source. The tools are offered; `tool_choice` is `auto`; the model declines them. (Run
evidence is in the PR that introduced this decision, not here: QP9.)

**The decision.** Effective only where `search.read_sources` and `search.verify_sources` are on and the
new rollback knob `search.require_writer_reads` (default `true`) is not turned off. With any of them
off nothing changes: no prompt byte, event field or call.

1. **The test.** After a writer call returns a usable draft (non-empty; under `revision.mode: ops`, one
   whose splice applied), the draft is *unread* when (a) that call completed no `read_source` at
   `full_text`, and (b) the shipped text cites an addressable `## Sources` entry that is **not in the
   run's read log at all** (`State.read_log`, D-read-before-citing). A revision that keeps only entries an
   earlier writer read, or whose read already failed, is not caught: the first needs no new read, the
   second is the mechanical finding's job and re-reading cannot help. `graph._unread_draft`.
2. **Enforcement is provider-agnostic.** It is a check on the result and a re-ask, not a request
   parameter. The re-ask is the *same* writer, once, with `prompts.WRITER_UNREAD_NUDGE` appended to the user
   message: fixed text that names no run material and no output format, so it holds under patch, rewrite
   and ops revision (the lesson of D-ops-revision's format-neutral `_ANSWER_NOW`). It is a fresh call with a
   fresh `ReadSession`; the rejected draft is not shown to it.
3. **Never kill a run over it. Fallback order**, inside the existing `budgets.writer_attempts` walk
   (the retry spends one attempt; the budget is not widened):
   1. the same writer, nudged, once;
   2. if that is also unread, the next pool member via the normal rotation (each member is nudged at most
      once);
   3. if the pool has no member not yet tried, or the attempt budget is spent, the **held** (most recent)
      unread draft is accepted with a recorded warning; the evidence lens's mechanical finding then
      carries the unread citations as it does today. A one-writer pool therefore costs exactly one extra
      call, and a pool with only unreachable members still reaches the existing fatal path unchanged
      (no draft was ever held).
   A call that fails (`ModelCallError`, empty reply, malformed ops) is a failed attempt exactly as before.
4. **Observability.** Each unread attempt emits `generate_failed` with `failure_class: unread_draft`
   (countable, as D-writer-failure-class requires). The `generate` event gains `unread_retry` (nudged
   retries made this generation) and `unread_accepted` (1 when an unread draft shipped because nothing
   better came back), both integers, both present only while the gate is in force. No URL or text.
5. **`tool_choice` is deliberately not forced.** `LLMClient.complete` offers one tool set on every
   round and the loop's termination relies on a model being able to answer; "required"/"any" semantics
   and acceptance differ across the providers in the roster, and structured-mode and NIM quirks cannot be
   verified offline. The result check works on every provider and cannot wedge a tool loop. A per-provider
   first-round forcing remains open once it can be verified live.

**Isolation (docs/isolation.md).** The nudge is constant text; the held draft and the read log reach no
other context and neither `OrchestratorView` nor the controller. A session stays scoped to one writer call.

**Cost and residual.** At most one extra writer call per unread writer attempt, and only for a draft that
would otherwise ship from memory. A writer that reads one page and cites others passes the test (the
mechanical finding still covers the rest). Removing a persistently non-reading member is a roster decision
and is not made here.

**Rollback.** `search.require_writer_reads: false`, or turn off `read_sources` / `verify_sources`.
