## D-writer-rotation-pool — writers rotate; the previous author is not excluded

**The finding.** Through late September 2026 the production instance was effectively down for a
week: OpenRouter refused the account intermittently (D-credit-exhaustion-defers), the NIM-hosted
writer ended tool loops without an answer, and the one writer paid for directly was the only one
reliably up. On every revision round `roles.writer_pool` removed the previous author, so a roster
that was one writer deep in practice had **zero** eligible writers on round two, and the run died
as `RosterExhausted` — "writer pool contains no model other than the current author". The
provider situation is not fixable from this repository for a month or more (local inference
hardware is the plan), so the question was which property the exclusion was actually protecting.

The answer in the specification is: none that matters. [isolation.md](../isolation.md) already
says that principle #7 "is fundamentally about *not sharing a context*, not about model
identity," that writer rotation "is not one of the seven principles and never was," and that the
decorrelation layer is the **critic roster**. D-scoped-revision repeated the same reasoning when it
kept rotation as "cheap insurance, not a load-bearing property." The stated justification for a
three-writer roster was availability (D-provider-retry) — and the exclusion was now the thing
*costing* availability.

What a writer-side exclusion could buy is small and already bounded elsewhere. Self-correction in
the *same* context degrades reasoning (Huang et al. 2024, cited in isolation.md), but a revision
here runs in a fresh context from an objective defect list the critics produced — the arrangement
principles #1 and #6 permit — and that holds whichever model holds the pen. The genuine residual is
voice: one model's framing persists across its own patches, and `loaded_language` floors at
`minor` (D-social-bias). Rotation already mitigates that whenever more than one writer is up;
rule 13's bounded rewrite is the backstop when it is not.

**The decision.** The writer pool is the whole `roster.writers` list, in order, and drafts go
round-robin over it: draft `k` is written by `writers[k % n]`. The rotation counter carries across
rounds, so no single model authors every revision while others are available — a run does not
collapse onto `writers[0]`. The previous author is not excluded. A roster degraded to one writer
(D-degraded-roster) is a one-deep rotation, and that writer gets the whole `writer_attempts`
budget, spaced, exactly as D-provider-retry specified for a one-deep pool.

- `roles.writer_pool(roster)` and `roles.next_writer(roster, rotation)` drop the author argument.
  `RosterExhausted` is reserved for an empty list, which `Roster` already refuses at load.
- `_generate` no longer special-cases a human seed; nothing excluded anyone, so nothing to waive.
- Author exclusion for **critics** is untouched: a model never critiques a draft it wrote, on any
  lens, at resolved identity, confirmation critiques included. The CI invariant row for author
  exclusion names `next_writer` as an anchor; that anchor still exists and still has nothing to do
  with who reviews.
- Attempts still walk the pool from the rotation's position and wrap; the walk may now reach the
  previous author, which is the intended fallback rather than a leak.

**What this changes in the documents.** The `writer(Rₙ₊₁) ∈ writer_pool \ {writer(Rₙ)}` line in
[DESIGN.md](../DESIGN.md) and [architecture.md](../architecture.md) becomes a rotation rule; the
"no model ever patches its own last draft" clause in [isolation.md](../isolation.md) and
`config/roster.yaml` is withdrawn; D-provider-retry and D-scoped-revision carry superseded-in-part
notes; D-writer-rereads-cited-sources records that its writer-side exclusion claim is superseded;
and the D-alternating-refine-game registry row now distinguishes writer rotation from critic-side
exclusion. `docs/convergence.md`'s `aborted` row loses the "empty writer pool" case.

**Tests.** `test_next_writer_is_round_robin_over_the_whole_pool` and
`test_a_single_writer_is_a_one_deep_rotation_not_a_fatal` in `tests/test_roles.py` replace the
two tests that pinned the exclusion. `test_drafts_are_written_round_robin_over_the_whole_pool` in
`tests/test_graph.py` pins the counter across a whole run, and the D-provider-retry retry test now
asserts that the attempt after an empty completion goes to the next pool member — the previous
author — rather than to the same model.

**Deliberately not done.** The first draft of every run still goes to `writers[0]`. Offsetting the
rotation per run would spread first-draft authorship across runs, but the roster's critic ordering
reasons about which rounds `writers[0]` authors (fit-first logic pool), so that is a separate
decision. Nothing here measures whether rotation improves outcomes; the only local evidence is that
writers differ in revision *quality*, which argues for choosing writers, not for excluding them.
