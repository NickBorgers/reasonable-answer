## D-writer-rotation-pool — writers rotate; the previous author is not excluded

**The finding.** `roles.writer_pool` removed the previous author on every revision round, so a
roster with one reachable writer had zero eligible writers from round two and the run died as
`RosterExhausted` — "writer pool contains no model other than the current author". That is not a
hypothetical: it is the recorded terminal state of production runs
[`run-48dc92fd3a4f`](https://reasonable-answer.nickborgers.net/runs/run-48dc92fd3a4f/audit.json)
and
[`run-e356ab3260bb`](https://reasonable-answer.nickborgers.net/runs/run-e356ab3260bb/audit.json)
(2026-09-18, build `b2eb19f`, readable by run id under D-id-as-credential). In both, the `startup`
event's `warnings` record a degraded roster — `mistral-large-3` and `nemotron-3-ultra` could not be
probed, and the run proceeded without them (D-degraded-roster) — the one `generate` event names
`deepseek-v4-flash` as the author of the first draft, and `finalize` is `aborted` with the message
above: the only writer that was up was barred from writing the second draft because it had
written the first. The same day's
[`run-021a9032ce99`](https://reasonable-answer.nickborgers.net/runs/run-021a9032ce99/audit.json)
shows the other shape the fallback was built for: three `generate_failed` events on that same sole
writer, then `aborted` — "every eligible writer failed". Operator observations beyond those runs —
how long the providers stayed unreachable, how many runs were lost — are motivation and not
warrant here; no outage duration or run rate is claimed. The question the recorded aborts posed was
which property the exclusion was protecting.

[isolation.md](../isolation.md) already answered it: principle #7 "is fundamentally about *not
sharing a context*, not about model identity", writer rotation "is not one of the seven principles
and never was", and the decorrelation layer is the **critic roster**. D-scoped-revision kept rotation
as "cheap insurance, not a load-bearing property"; the stated reason for three writers was
availability (D-provider-retry). The exclusion had become the thing *costing* availability.

**The evidence (QP12 §4).** This narrows QP4's surface, so `docs/quality-principles.md` moves with
it and two references are added by URL. The register's existing QP4 sources are about a model
*judging* its own output (Panickssery et al. 2024) or correcting it with *no outside signal* (Huang
et al. 2024); none measures a generator revising from a defect list that other models produced.
[Tyen et al. 2024](https://arxiv.org/abs/2311.08516) measure exactly that split. They show "that
poor self-correction performance stems from LLMs' inability to find logical mistakes, rather than
their ability to correct a known mistake": the same models that made the errors, given the mistake
location from outside, correct them, and that "boosts downstream task performance across our 5
reasoning tasks, indicating that LLMs' correction abilities are robust". That is the division of
labour this pipeline enforces — critics the author never includes *find and locate*; the writer,
author or not, *corrects* from a `{locus, category, severity}` task list — and it says the step
exclusion was guarding is the one a model does well on its own output. Its limit is stated: the
tasks are reasoning benchmarks with ground-truth locations, not prose reports under an LLM critic.
[Kamoi et al. 2024](https://arxiv.org/abs/2406.01297) bound the other half: self-correction "works
well in tasks that can use reliable external feedback" and is undemonstrated "with feedback from
prompted LLMs" outside tasks suited to it. That bound applies to this loop's critic feedback
whoever the reviser is, so it is neither an argument for the withdrawn rule nor changed by this
decision; the loop's efficacy stays a measured property (critic audition gates, production
convergence). Neither source compares self-revision against revision by a *different* writer, so
the previous author is made *eligible*, not preferred — rotation stays wherever a second writer is
up.

**The decision.** The writer pool is the whole `roster.writers` list, in order. The next draft goes
to the member after the one that wrote the last draft, wrapping, so on an uninterrupted run draft
`k` is `writers[k % n]`. A failed attempt moves to the next member (D-provider-retry) and the
rotation then continues from the member that *succeeded* — `_generate` adds the attempt offset to
the counter before the usual increment — so a fallback skips a writer and never repeats one. The
previous author is not excluded. A roster degraded to one writer (D-degraded-roster) is a one-deep
rotation that still gets the whole `writer_attempts` budget, spaced.

- `roles.writer_pool(roster)` and `roles.next_writer(roster, rotation)` drop the author argument;
  `_generate` drops the human-seed special case, since nothing excluded anyone.
- Author exclusion for **critics** is untouched: a model never critiques a draft it wrote, on any
  lens, at resolved identity, confirmation critiques included.
- The `writer(Rₙ₊₁) ∈ writer_pool \ {writer(Rₙ)}` line in [DESIGN.md](../DESIGN.md) and
  [architecture.md](../architecture.md) becomes the rotation rule above; the "no model ever
  patches its own last draft" clause in [isolation.md](../isolation.md) and `config/roster.yaml` is
  withdrawn; D-provider-retry, D-scoped-revision and D-writer-rereads-cited-sources carry
  superseded-in-part notes; the D-alternating-refine-game registry row distinguishes writer
  rotation from critic-side exclusion.

**Tests.** `test_next_writer_is_round_robin_over_the_whole_pool`,
`test_next_writer_wraps_over_a_three_writer_pool` and
`test_a_single_writer_is_a_one_deep_rotation_not_a_fatal` in `tests/test_roles.py` replace the two
tests that pinned the exclusion. In `tests/test_graph.py`,
`test_drafts_are_written_round_robin_over_the_whole_pool` pins the counter across a three-writer
run, `test_a_failed_attempt_moves_on_and_the_rotation_follows_the_writer_that_succeeded` pins the
retry bookkeeping (a | b fails, c | a),
`test_a_one_writer_roster_revises_its_own_draft_and_survives_a_failed_attempt` pins the motivating
outage itself (one writer, every draft its own, an empty attempt retried on the same identity after
a wait, and no critique ever by that writer), and the D-provider-retry retry test now asserts the
attempt after an empty completion goes to the next pool member — the previous author.

**Deliberately not done.** The first draft of every run still goes to `writers[0]`; a per-run offset
would spread that but the roster's fit-first logic-pool ordering reasons about which rounds
`writers[0]` authors, so it is its own decision. No comparison of self-revision against rotated
revision has been run; each `audit.json` carries author per `generate` and material count per
`triage`, and under the shipped roster self-revision occurs only through the retry fallback, so the
arms are distinguishable from the events alone.
