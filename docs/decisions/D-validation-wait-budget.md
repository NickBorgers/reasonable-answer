## D-validation-wait-budget — the reviewer guard waits longer than validation takes, and looks after its last sleep

**The problem.** Each reviewer's guard requires `PR Validation Required` to have completed
successfully on the reviewed SHA before a self-hosted runner is allocated. On a `pull_request`
event the guard and PR Validation start at the same instant, so the guard polls — and its budget
was 10 polls at 15s, 2m30s. PR Validation on this repository measured 2m15s, 3m12s, 2m57s and
3m03s across four consecutive runs ([30818177449](https://github.com/NickBorgers/reasonable-answer/actions/runs/30818177449),
[30818436118](https://github.com/NickBorgers/reasonable-answer/actions/runs/30818436118),
[30824582023](https://github.com/NickBorgers/reasonable-answer/actions/runs/30824582023), and
[30867764495](https://github.com/NickBorgers/reasonable-answer/actions/runs/30867764495)): three of
the four exceeded the entire budget. The loop also slept *after* its final poll and then gave up
without looking again, so the last 15 seconds were unobserved by construction.

PR #156 lost exactly that race. The
[validation run](https://github.com/NickBorgers/reasonable-answer/actions/runs/30867764495)
succeeded at 01:10:26Z on SHA `a735e39`; the
[review pipeline run](https://github.com/NickBorgers/reasonable-answer/actions/runs/30867764721)
declared it unfinished at 01:10:32Z, six seconds into the dead interval. All five reviewers
skipped, the judge received an empty artifact set, and `finalize` published
`NO-GO — pipeline could not trust its inputs (cycle 1)` on a PR nothing was wrong with. Because a
hand-posted `/review` arrives long after validation has settled and clears on its first poll, the
failure preferentially hit the **unattended** path — the one no human was watching, and the one
this pipeline exists to be.

**The decision.** Keep the requirement; fix its arithmetic.

1. **Poll after the last sleep.** The loop sleeps only *between* polls. A sleep no poll follows is
   an interval the gate can go green in unseen, and it is the interval most likely to contain the
   transition, because it sits closest to when validation finishes. Even on the old budget this
   alone would have let PR #156 through.
2. **A budget well clear of the measured range.** 40 polls at 15s, ~9m45s of waiting against a
   measured worst case of 3m12s. The asymmetry sets it: waiting costs an idle `ubuntu-latest` job
   polling an API, while giving up early costs a five-reviewer panel, a wasted pipeline run and a
   red merge gate. The guard's `timeout-minutes: 15` is the outer bound on that wait and must stay
   above it — if the job timeout fires instead, the guard is killed mid-wait and reports no
   decision at all, which is the same false negative minus the log line explaining it.
3. **The give-up message is derived from the budget, not restated.** The old line read
   `Attempt N/10`, a second copy of the count that a change to the loop would leave stale. A log
   that disagrees with the code is how this defect stayed invisible in the run transcript.

**What is deliberately unchanged.** Whether reviewers should wait on validation at all is not
reopened: they should, because findings about code that does not lint are findings the author
already has. Both refusals stay fail-closed — a **genuine** timeout and a red, cancelled or absent
gate all still skip the review, and no reviewer may read a SHA that did not validate. The bug was
calling a race a failure, not the refusal.

**Verification.** The guard is inline `github-script`, so it is not importable.
`.github/scripts/review/reviewer-guard.test.mjs` extracts the block out of `review-reviewer.yml`
and *runs* it against a stubbed `checks.listForRef`, passing `setTimeout` in as a parameter so the
full budget is exercised in milliseconds and every sleep is recorded — the same "run the deployed
guard rather than restate it" approach `tests/test_ci_model_pins.py` takes with the agent
composite's inline shell guard. Testing an extract keeps one authoritative copy of the logic:
there is no second implementation to drift. The four tests that describe this defect fail against
the pre-fix script and pass after it; the six that pin the fail-closed refusals pass against both,
which is what makes them a fence rather than a restatement. `review-reviewer.yml` is added to the
`review_scripts` paths filter in `pr-validation.yml`, so an edit to the guard runs the test that
covers it.

**Invariants.** None of the six report-pipeline invariants is in reach. Author exclusion and the
blind orchestrator are properties of the refinement graph, not of CI; this changes neither what may
enter a generator's context nor who may critique what. Within the pipeline's own safety model the
change is conservative in the direction that matters: it makes the guard *less* likely to skip a
reviewer, and strictly no more likely to clear one. Every path that clears still requires a
completed, successful `PR Validation Required` on the exact reviewed SHA, and the fork,
author-association and head-SHA refusals are untouched and still evaluated before the wait begins.
