## D-per-identity-reader-breaker — the evidence reader's circuit breaker is per model identity, not per run

**The finding.** D-writer-evidence-card gave the reader a consecutive-failure breaker
(`evidence_reader.max_consecutive_failures`) whose state was run-wide. The reader runs on the
writing model's own alias, and writers rotate across several models served by different providers.
When one provider rate-limited every reader call, the breaker tripped on that model's failures and
then switched cards off for every writer in the run, including models whose reader calls had never
failed. Those writers read raw openings (`reader_unavailable`) for the rest of the run, which is the
narrow view the card exists to remove (QP10), for a reason that said nothing about them.

**The decision.** Breaker state is kept per reader identity: the resolved provider/model the reader
call runs on, the same identity the cache key already uses. Each identity has its own consecutive
failure count and its own disabled flag.

1. A failed reader call increments only its identity's count. At `max_consecutive_failures` that
   identity is disabled for the rest of the run and its reads return `reader_unavailable` without a
   call.
2. A successful pass resets only its identity's count. The "consecutive" meaning is unchanged: a
   success between failures starts that identity's count over.
3. A tripped identity does not affect any other identity. One provider's rate limit or outage is no
   evidence about another model, so no other writer's cards are withdrawn.
4. The log warning names the identity's alias and a count. It carries no URL and no page text
   (RA-016). The fallback reason stays `reader_unavailable`; no new event field is added, because the
   per-reason fallback counts on the `generate` event already show which writer calls lost cards.

**What this does not change.** The whole-run call ceiling `evidence_reader.max_calls` stays run-wide
and all-or-nothing per page (QP7): the identities share one budget, so splitting the breaker cannot
multiply the cost bound. The failure posture, the cache, the isolation and the card contents are
unchanged. With one identity in play the behavior is the same as before.

**Alternative rejected.** Keeping the run-wide breaker and raising the threshold: a dead proxy would
cost more timeouts, and a bad provider patch would still outlast any threshold. Resetting a disabled
identity after a cool-down was also rejected as a new knob; a disabled identity stays so for the
run, as before.

**Rollback.** Revert the change; `evidence_reader.enabled: false` still switches cards off.
