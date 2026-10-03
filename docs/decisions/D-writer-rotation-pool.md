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

**The evidence, fetched (QP12 §4) — and what it does not settle.** This narrows QP4's surface,
so the register row moves with it and two references were fetched and added by URL:
[Kamoi et al. 2024](https://arxiv.org/abs/2406.01297), a critical survey that classifies
self-correction by feedback source — "self-correction works well in tasks that can use reliable
external feedback", and "no prior work demonstrates successful self-correction with feedback from
prompted LLMs" outside tasks suited to it — and
[Stechly, Valmeekam & Kambhampati 2024](https://arxiv.org/abs/2402.08115), who hold GPT-4 as the
generator throughout and vary only the verifier: "significant performance collapse with
self-critique and significant performance gains with sound external verification". Neither varies
the generator's identity, and neither compares a model revising its own draft against a different
model revising it from the same feedback. The register's existing QP4 sources do not either: Huang
et al. 2024 is self-correction *without* external feedback, Panickssery et al. 2024 is
self-preference in an *evaluator*, and Chen, Su & Chiang 2026 is about where a claim sits in a
context. **So the evidence base neither supports nor refutes the withdrawn rule**, and the absence
of a reported generator-identity effect is not claimed as evidence of none. The warrant for this
decision is operational — the exclusion was killing every run on a one-writer roster — plus the
record that nothing in the register ever supported the rule. That is weaker than §4's standard,
and the register's application paragraph says so in those words rather than presenting an
availability decision as a literature result.

What the fetched sources do bound is kept: Kamoi et al.'s negative result about prompted-LLM
feedback limits *this pipeline's* critic feedback whoever the reviser is, so it does not argue for
the withdrawn rule and is neither created nor removed here; the loop's efficacy stays a measured
property (critic audition gates, production convergence), not a literature claim.

**The measurement that would settle it.** Each production run's `audit.json` carries, per
`generate` event, the author and, per `triage` event, the material count on that artifact, so the
change in material count across each revision can be grouped by author today. Over the local audit
set at the time of this decision the writers already differ by that measure (deepseek retired about
2.4 material defects per revision, mistral about 0.8, with a quarter to a third of revisions making
the count worse) — a difference in *writers*, not evidence about *self*-revision. The comparison
this decision leaves open is self-revision against rotated revision on the same question set with
the same critics; under the shipped roster self-revision occurs only through the retry fallback,
so the arms are distinguishable from the `generate` events alone. Until it is run, the register
records the rule as withdrawn without evidence either way.

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
