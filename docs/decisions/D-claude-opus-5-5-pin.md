## D-claude-opus-5-5-pin — advance the Opus CI assignments within the Claude family

**Context.** D-ci-model-pinning makes each CI model checkpoint a repository-owned, reviewable
choice. The invariant reviewer and the Claude branches of the issue-author and fixer maps were
pinned to `claude-opus-5`; changing that literal therefore has to move the setup contract, pipeline
description, and QP3 evidence register with the workflows.

**Decision.** Pin those three assignments to `claude-opus-5-5`. The invariant role remains on the
Opus tier because its never-abstain backstop and merge-gate responsibilities are unchanged. The
runtime-selected Claude author and fixer remain on the same tier because their open-ended,
repository-wide task is unchanged. All three literals move together so the author and fixer maps
remain identical and the documented proxy requirements match the jobs that run.

**QP3 effect.** This is a checkpoint update inside the Claude family, not a panel recomposition.
The invariant and test reviewers remain Claude-family; the docs, security, and quality reviewers
remain Codex-family; and quality remains cross-family from invariant. The explicit-pin and family
checks in `tests/test_ci_model_pins.py` continue to enforce those properties. No quality principle
is weakened and no new empirical claim is introduced.

**Scope.** At the time of this decision, no other reviewer model changed: the bounded test reviewer
remained on `claude-sonnet-5`, and every Codex assignment remained as recorded by
D-ci-model-pinning. D-claude-sonnet-5-5-pin records the test reviewer's later repin.
