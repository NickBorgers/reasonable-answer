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

**The evidence, fetched (QP12 §4).** This narrows QP4's surface, so the register row moves with
it and two new references are cited by URL. The claim they are asked to carry is small: that no
source finds an effect of the *generator's* identity once the feedback is external, so a rule about
the generator's identity has no evidence behind it. [Kamoi et al. 2024](https://arxiv.org/abs/2406.01297),
a critical survey, locates the condition for successful self-correction in the feedback's source —
"self-correction works well in tasks that can use reliable external feedback" — and reports that
"no prior work demonstrates successful self-correction with feedback from prompted LLMs" outside
tasks suited to it. [Stechly, Valmeekam & Kambhampati 2024](https://arxiv.org/abs/2402.08115) keep
the *same* generator throughout and find "significant performance collapse with self-critique and
significant performance gains with sound external verification". Who generated the draft was not
the variable that moved the result in either source.

Two limits are stated so the retreat does not outrun them. Kamoi et al.'s negative result about
prompted-LLM feedback is a bound on *this pipeline's* critic feedback, and that feedback is the same
defect list whether or not the reviser wrote the draft — so it does not argue for the withdrawn
rule, and it is neither created nor removed here; the loop's efficacy stays a measured property
(critic audition gates, production convergence), not a literature claim. And no source measures
self-revision against revision by a different writer, which is why rotation stays wherever a second
writer is up and the previous author is eligible, not preferred. The register's existing QP4
sources are untouched as claims: Huang et al. 2024 is self-correction *without* external feedback,
Panickssery et al. 2024 is self-preference in an *evaluator*, and Chen, Su & Chiang 2026 is about
where a claim sits in a context rather than who generated it, so it is not claimed here.

**The decision.** The writer pool is the whole `roster.writers` list, in order, and drafts go
round-robin over it: the next draft goes to the member after the one that wrote the last draft,
wrapping, so on an uninterrupted run draft `k` is `writers[k % n]`. The one exception is stated
exactly: a failed attempt moves to the next member (D-provider-retry), and the rotation then
continues from the member that *succeeded* — `_generate` adds the attempt offset to the counter
before the usual increment — so a fallback skips a writer and never repeats one. The previous
author is not excluded. While more than one writer is up, no model authors two consecutive drafts
except through that fallback; on a one-writer roster the same model authors every draft, and that
is the point. A roster degraded to one writer
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

**What this changes in the register.** `docs/quality-principles.md` QP4 gains, in its surface
column, the statement that `roles.py` applies the exclusion to review only, an application
paragraph for this decision, and the two references above in the §5 table.

**What this changes in the documents.** The `writer(Rₙ₊₁) ∈ writer_pool \ {writer(Rₙ)}` line in
[DESIGN.md](../DESIGN.md) and [architecture.md](../architecture.md) becomes a rotation rule; the
"no model ever patches its own last draft" clause and writer-role diagram in
[isolation.md](../isolation.md), and the corresponding clause in `config/roster.yaml` are withdrawn;
D-provider-retry and D-scoped-revision carry superseded-in-part
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
