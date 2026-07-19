## What and why

<!-- What changes, and what problem it solves. -->

Resolves #

## Invariants touched

<!--
List every design invariant this change affects, or write `none`.

  - Author exclusion (no model critiques its own report, at resolved model identity)
  - Blind-orchestrator isolation (OrchestratorView stays content-free)
  - Fail-closed lens validation (a bad field fails the whole lens)
  - Severity floors (clamp up only)
  - Termination (rule order; nothing generates at or beyond the hard cap)
  - Untrusted text (critique prose never reaches the generator as instruction)

The invariant reviewer diffs this list against the code, so an inaccurate list is worse
than an empty one. If you changed an invariant, name the `docs/` files you updated and
the `docs/decisions.md` entry you recorded.
-->

none

## Verification

<!-- What you ran, and what it showed. -->

- [ ] `uv run pytest -m "not live"`
- [ ] `uv run ruff check src/ tests/`
- [ ] `actionlint` (if workflows changed)

## Deliberately not done

<!-- Anything nearby you noticed and left alone, and why. -->
