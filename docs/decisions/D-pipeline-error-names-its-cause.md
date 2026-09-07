## D-pipeline-error-names-its-cause — the fail-closed verdict says what stopped the panel

**The problem.** `pipeline_error` is the verdict for "the judge could not trust its inputs", and its
most common shape is that no reviewer artifact exists. The judge can see the empty directory. It
cannot see why, because a guard that refuses produces no artifact and leaves no trace in anything the
verdict reads — so the verdict described the symptom, `no reviewer artifacts (reviews skipped?)`, and
the published comment added that this is "usually a reviewer or orchestration bug".

Both sentences point away from the answer. The usual cause is a fact a guard read and logged one job
upstream: `PR Validation Required` was red on the reviewed SHA, so every guard refused. PR #197 is
the worked example — one flaky test reddened validation, five guards refused, and the comment
proposed a reviewer or orchestration bug. Recovering the truth took a walk back through four jobs'
logs.

That cost is not one-off. Every reader pays it again, and it grows: Actions logs expire, so a
`pipeline_error` older than the retention window is not diagnosable at all — the one artifact that
outlives them is the comment, which was the thing pointing the wrong way. The reasoning is the same
as D-reviewer-retry-transient and D-test-waits-are-barriers: this pipeline's failures are read by
someone later, and a message that guesses wrong is more expensive than one that says less.

**The decision.** The guard's own words travel with the refusal, and the verdict prints them.

Each refusal branch in the reviewer guard sets a `reason` alongside its `ok=false` — a red or
non-success gate names the conclusion it read, a timeout says it waited, a superseded head names the
head that replaced it, a fork names the fork, an untrusted author names the association. That
becomes the guard job's output, then the reviewer workflow's `skip_reason`, then
`guard_skip_reasons` on the pipeline's judge call, then `GUARD_SKIP_REASONS` in the judge's
environment. The pipeline collects them because it is the only stage that can see all five.

The judge deduplicates before rendering: five guards refusing over one red gate is one fact about one
SHA, and printing it five times is noise, not evidence. Distinct reasons are all named, because
guards can refuse for different reasons in the same run — a moved head and a red gate — and naming
only the first would be a new way to mislead. The comment's blurb stops guessing at a cause and
points at the reason list instead.

**What is deliberately unchanged.** The verdict, the category, the fail-closed direction, the merge
gate and the cycle accounting are all exactly what they were: a `pipeline_error` is still a NO-GO,
still not an inheritable judgement (D-nonjudgement-outcomes), and still consumes no cycle when no
guard cleared. This changes one sentence in a comment and nothing about what the pipeline decides.

**A caller that supplies no reasons keeps the old wording.** The judge input is optional and falls
back to the generic sentence, so a refusal shape that somehow sets no reason yields a verdict that is
vague rather than one asserting a cause it does not have. Saying less is the failure mode to prefer
here — the whole point of the change is that a confident wrong sentence costs more than an unhelpful
true one.

**Invariants.** None of the six tabulated pipeline-core safety invariants is in reach: this changes
what a verdict *says*, not what it decides, and touches no module that builds a model's context,
scores a finding, or decides a stop. The pipeline invariant it borders — a reviewer that produced no
valid artifact fails the run closed — is untouched and still covered by `judge.test.mjs`. Nothing
here grants any stage a path to the branch, and the reason strings are composed from the guard's own
API reads (a check conclusion, a head SHA, a repo name), never from PR-controlled prose.
