## D-reviewer-retry-transient — a reviewer gets one retry, bounded by what is safe to repeat

**The problem.** The judge requires every selected role to be present and fail-closes to
`pipeline_error` when one is missing. That direction is correct and stays: a reviewer that
returned no valid artifact must never read as a reviewer that cleared. But it makes the panel
all-or-nothing, so the price of one reviewer dying is not one reviewer — it is a NO-GO the PR did
not earn, a red merge gate, and a re-read that costs five reviewers instead of one.

PR #196 paid exactly that. The `invariant` role's transcript ends in `API Error: 400` two turns in,
`$0.34` and forty seconds into a run that had read nothing yet. Nothing about the change under
review was involved, and nothing about the pipeline was broken; a provider had a bad second. The
published verdict was a NO-GO, and the PR needed a manual `/review` to get a real one.

This is the same shape as D-test-waits-are-barriers and belongs to the same class of cost: the
pipeline is deliberately built to amplify a missing input, so anything that can go missing without a
defect behind it is expensive in a way its own runtime does not show.

**The decision.** A reviewer gets **one retry**. `review-agent-run` takes `max_attempts` (default 1)
and retries the container invocation, and `review-reviewer.yml` is the only caller that passes 2.

The retry is bounded by what is safe to repeat, not by how badly the pipeline wants an artifact:

- **Only a read-only caller opts in.** A reviewer pushes nothing, publishes its comment in a later
  step, and has its artifact deleted before the retry starts, so a second invocation is a true
  repeat of the first. The fixer, the resolver and the author push, open a pull request, or carry a
  resumable session; for them a second invocation starts on a tree the first one already changed,
  which is not a retry but a different action. They stay at the default.
- **A resumed session is never retried**, whatever the caller passes. `run-in-container.sh`
  contains a failed resume on purpose — exit 0 with a sentinel, so the cold fallback runs
  (D-resume-stall-guard's neighbourhood). Retrying at the container boundary would race that
  handoff rather than help it.
- **Only a fast failure is retried**, inside `retry_within_seconds` (default 600). A deadline-shaped
  failure has already spent the job's budget and is precisely the failure a second identical attempt
  would most likely repeat. Bounding it this way also keeps the arithmetic honest: one fast failure
  plus one full 30-minute attempt still fits the reviewer job's 45-minute timeout, so the retry can
  never be the reason a scarce self-hosted runner is held to the outer bound.
- **The retry starts clean.** The dead attempt's result JSON is removed before the next invocation,
  so a half-written artifact can never be what the judge reads. Its transcript is preserved as
  `<role>-attempt1-output.log`, so the failure is still diagnosable after a green retry — the case
  where nothing else would record that it happened.

**Nothing about the gate is loosened.** A retry buys one more attempt, never a pass. If the second
attempt fails the role is still absent, the judge still fails the cycle closed, and the verdict is
the same `pipeline_error` it would have been. What changes is only how often a verdict nobody issued
is published because a provider returned a 400.

**Why not the alternatives.** Retrying *inside* `run-in-container.sh` would sit in the script that
owns the agent deadline, the stall guard and the resume containment — the most safety-critical file
in the pipeline, and the one whose failure modes are hardest to reason about. The container boundary
is where a failure is already a plain non-zero exit, and it also covers a wedged image pull. Making
the *judge* tolerant of a missing role was rejected outright: that is the fail-closed property
itself, and it is exactly what stops a reviewer that crashed from counting as one that approved.

**Invariants.** None of the six tabulated pipeline-core safety invariants is in reach: this changes
how many times a read-only reviewer may be invoked, and touches no module that builds a model's
context, scores a finding, or decides a stop. The pipeline invariant it does border — a reviewer
that produced no valid artifact fails the run closed — is unchanged and re-asserted by
`tests/test_ci_agent_retry.py`, which pins that a retry which also fails still fails the step. The
reviewer stays read-only: nothing in this change gives any stage a path to the branch.
