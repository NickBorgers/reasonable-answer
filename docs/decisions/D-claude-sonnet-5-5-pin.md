## D-claude-sonnet-5-5-pin — advance the test reviewer within the Claude family

**Context.** D-ci-model-pinning makes each CI model checkpoint a repository-owned, reviewable
choice. The `test` reviewer was pinned to `claude-sonnet-5`. LiteLLM now serves
`claude-sonnet-5-5`, so the checkpoint can move without leaving the Claude family or the Sonnet tier.

**Decision.** Pin the `test` reviewer to `claude-sonnet-5-5`. The role is a bounded,
checklist-shaped job against `test.md`, so it stays on the Sonnet tier. The change is a newer
checkpoint of the same tier, not a move to a larger model.

**QP3 effect.** This is a checkpoint update inside the Claude family, not a panel recomposition.
The invariant and test reviewers remain Claude-family; the docs, security, and quality reviewers
remain Codex-family; and quality remains cross-family from invariant. The explicit-pin and family
checks in `tests/test_ci_model_pins.py` continue to enforce those properties. No quality principle
is weakened and no new empirical claim is introduced.

**Scope.** No other reviewer model changes. Every other assignment remains as recorded by
D-ci-model-pinning and D-claude-opus-5-5-pin.
