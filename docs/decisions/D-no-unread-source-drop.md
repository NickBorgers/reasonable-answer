## D-no-unread-source-drop — a revision may not drop a read source without reading, and a report may not shrink its way to acceptance

**The finding.** The operator's production runs showed revisions that answered evidence findings by
deleting claims together with their cited sources, round after round, until critics had little left to
object to and the run ended `accepted` on a report that cited half the sources an earlier draft had read.
Those observations are the **motivation, not the warrant** (QP9); the run identifiers and per-writer
counts are in the PR that introduced this decision.

**The warrant is the mechanism, and it is checkable in this repository.**

- D-no-shrink-discharge mints a finding for a dropped source only when the claim it supported
  *survives* (word overlap). Withdrawing the claim with its source is an allowed discharge by design.
- D-writer-must-read sets a draft aside only when its call read nothing **and** it cites a page nobody
  has read. A revision that reads nothing and only keeps pages an earlier writer read passes, and so
  does one that deletes some of them.
- Critics review one draft at a time and count what is wrong with it, not what is missing relative
  to an earlier draft. Fewer claims give them fewer chances to object. Selection
  (D-latest-unblocked-selection) ranks rounds on blocking counts alone.

So deleting a read source and its claim was free at every stage: no gate at the call, no finding at
critique, no effect on selection. The cheapest answer to an evidence finding was to remove the
evidence. That is a property of the code, whatever any one writer does with it.

**The decision.** Both parts are effective only where `search.read_sources` and
`search.verify_sources` are on (the D-read-before-citing posture: without a read log there is no way to
tell a real source from a fabricated one). Each has its own rollback knob under `review`, not
`budgets`, so `_run_fingerprint` is unchanged.

1. **What counts as a source.** `triage.read_cited_sources`: the cleaned addresses of the entries the
   body cites that the run's read log (`State.read_log`) holds at `full_text`. An entry nobody read, one
   whose read failed (`not_found`, `paywalled`, ...) and an entry with no address never count; two
   entries under one address count once. So removing a fabricated or unreadable citation, which its own
   finding asks for (D-notfound-fabrication, D-read-before-citing), is never treated as a loss.
2. **A drop needs a read** (`review.drop_requires_read`, default `true`). After a writer call returns a
   usable revision — not a first draft, not a polish pass — it is set aside when (a) the call completed
   no `read_source` at `full_text` and (b) it stops citing at least one source that the previous draft
   cited and the read log holds at `full_text` (`triage.dropped_read_sources`). Re-reading one of the
   draft's own cited pages counts as reading, so a writer that checks a page before removing it passes.
   The handling is D-writer-must-read's walk, extended rather than copied (`graph._set_aside_class`):
   the same writer once, with the fixed `prompts.WRITER_DROP_NUDGE`; then the next pool member; then
   the held draft ships with a warning. One nudged retry per writer is shared across both tests, the
   walk stays inside `budgets.writer_attempts`, and the write-before-read test runs first so its
   behaviour is unchanged. Each set-aside attempt emits `generate_failed` with failure class
   `unread_source_drop`. The `generate` event gains `drop_retry` and `drop_accepted` (integers, present
   while the gate is in force).
3. **Backstop: a large net loss is a finding** (`review.source_loss_finding`, default `true`). `generate`
   records `cited_read` (the count above for the draft it ships) and raises `State.cited_read_peak`, the
   largest such count of the run; it is never lowered. A completed completeness review of a draft whose
   count has fallen by at least `review.source_loss_fraction` of the peak (default `0.5`) gains one
   pipeline-minted `incomplete_answer` finding at `blocking`, anchored on the report's first body
   sentence (`triage.mechanical_source_loss_issues`). A `source_loss_check` event records `cited_read`,
   `cited_read_peak` and `source_loss_findings` once per draft.
4. **Why completeness and `incomplete_answer`.** The defect is how much of the evidence the run found
   the answer still stands on — a property of the report as a whole, not of one claim's support, so
   `uncited_claim` (D-no-shrink-discharge's category, about a specific claim) does not fit and there is
   no claim sentence to anchor it on. `one_sided_sourcing` is a judgement about the balance of sources,
   not their number. `incomplete_answer` is the category for an answer that leaves out what it could
   have said from material in reach, and the critic prompt already limits it to fixes available inside
   the report; the peak shows they are.
5. **Why `blocking`, above the `major` floor.** Production runs `review.selection: latest_unblocked`,
   which ranks on blocking alone (D-latest-unblocked-selection). A `major` would stop acceptance of the
   shrunken draft but, at the hard cap, would not stop it from shipping over the fuller earlier draft —
   which is the outcome this decision exists to prevent. Minting above the floor is an escalation, which
   RC-005 permits, so the clamp is a no-op. D-no-draft-history made the same choice for the same reason.
   A draft that still carries the finding at the hard cap ends `needs_human_review` (rule 5), which is
   an honest description of a run whose last draft lost half its evidence.
6. **Why one half.** A heuristic argued from mechanism, not a measured value. The finding counts
   sources, not which claim each supported, so it cannot tell a justified removal from a convenient one;
   the threshold must be loose enough that ordinary revision — replacing a weak source, removing one a
   critic showed does not support its claim — never trips it. At one half, the report has lost at least
   as much of its read evidence as it keeps, which no single justified removal does once the
   bibliography has three or more read sources. Lower fractions fire on one-source swaps in a short
   bibliography; higher ones let most of the evidence go before anything notices. With a peak of one or
   two, losing one source is half and fires; that is deliberate, since such a report has lost most of
   what it stood on. The floor the writer must reach is stated in the instruction
   (`triage.source_loss_floor`).
7. **The exit is in the report, so it cannot force non-convergence.** Every source behind the peak was
   read in full text during this run, so the floor never asks for evidence the run has not shown
   exists; reading a page again counts. The instruction says to read pages, cite what they support, and
   cite nothing unread. The finding is suppressed once the run's read budget is spent, when reading could
   no longer discharge it (as D-read-before-citing's finding is). Termination is unchanged
   (docs/convergence.md): no rule, no controller input and no budget is added; generation still advances
   `round` toward `hard_cap`.

**Isolation (docs/isolation.md).** The nudge is constant text with no count and no URL. The finding
carries the current count, the peak, the floor and fixed wording, plus the report's own first sentence
as its anchor — no page text, no critic prose, no previous-draft text. `cited_read_peak` is an integer
in graph state; it enters no prompt, no `OrchestratorView` and no controller input. Events carry
integers only (RA-016).

**Checkpoint compatibility.** `State.cited_read_peak` is read with `.get`. A run checkpointed before
the field existed resumes without it: the completeness lens mints nothing for the draft in hand, and the
next `generate` sets the peak from that draft. The run's earlier, larger peak is not reconstructed.

**Residuals.** (1) A writer can satisfy the drop gate by reading any one page, then drop several; the
backstop catches it only once the loss reaches the fraction. (2) A source read in full text but
off-topic, or one a critic showed misrepresented, still counts toward the peak; a report that removes
half of such sources must find replacements. The threshold is set so that removing one never does this
on a bibliography of three or more. (3) The backstop can be met by citing read pages that support little;
claim checking and the evidence critic judge that, not this rule. (4) With reading or verification off,
neither part runs: there is no read log to tell a fabricated source from a real one.

**Rollback.** `review.drop_requires_read: false` removes the call gate and its event fields;
`review.source_loss_finding: false` removes the finding and the `source_loss_check` event. The peak and
`cited_read` stay on the `generate` event while reading and verification are on.
