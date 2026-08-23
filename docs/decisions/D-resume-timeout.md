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
