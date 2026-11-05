## D-claude-sonnet-5-5-pin — repin the test reviewer within the Claude panel

**Context.** D-ci-model-pinning makes each CI model checkpoint a repository-owned, reviewable
choice. The `test` reviewer was pinned to `claude-sonnet-5`; this change proposes replacing that
explicit workflow literal while leaving the reviewer's `agent: claude` assignment unchanged.

**Decision.** Pin the `test` reviewer to `claude-sonnet-5-5`. The role remains a bounded,
checklist-shaped job against `test.md`; no reviewer role or agent assignment changes.

**QP3 effect.** This is a checkpoint update inside the Claude family, not a panel recomposition.
The invariant and test reviewers remain Claude-family; the docs, security, and quality reviewers
remain Codex-family; and quality remains cross-family from invariant. The explicit-pin and family
checks in `tests/test_ci_model_pins.py` continue to enforce those properties. No quality principle
is weakened. This decision records repository configuration and makes no claim about external
model availability or relative capability.

**Scope.** No other reviewer model changes. Every other assignment remains as recorded by
D-ci-model-pinning and D-claude-opus-5-5-pin.
