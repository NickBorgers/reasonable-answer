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
