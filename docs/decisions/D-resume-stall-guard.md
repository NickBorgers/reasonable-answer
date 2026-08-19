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
