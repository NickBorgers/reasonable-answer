## D-census-gated-repair — a draft that fails the citation census gets one repair turn before the critics see it

**The finding.** Production run `run-05289e3ce78c`, round 7 (writer `mistral`): a revision listing
twelve sources in `## Sources` came back with **zero** `[n]` markers in the body (`body_markers=0`,
`cited_entries=0`, `cited_sources_dropped=13`) and touched 15 of its 20 changed paragraphs outside
anything a fix task named (`out_of_scope=15`). Round 8 (writer `nemotron`) then "resolved" the
uncited-claim findings the evidence lens filed against that draft — the only lever a marker-less body
leaves the critics — by deleting most of the report (`entries_removed=9`, `out_of_scope=10` of 12
changed paragraphs). The shipped report had one paragraph and three sources, down from twelve. Across
the prior 48 hours, `mistral` produced a marker-less or source-less draft five times in four runs, and
`nemotron` removed sources in five of seven revisions. Healthy patch revisions in the same window —
`deepseek` reviser — ran `out_of_scope` at 0-4, occasionally 6, and never a marker-less body.

Both numbers already existed on the `generate` audit event before this decision. `_citation_fields`
(D-writer-citation-continuity) counts `body_markers`/`source_entries`/`cited_sources_dropped` on every
draft; `_scope_fields` (D-scoped-revision) counts `out_of_scope` on every patch-mode revision. Both are
warn-only by explicit, argued design — D-writer-citation-continuity's "Deliberately not done" names "a
generate-time gate or repair turn" outright, and D-scoped-revision reasoned that "an enforcing tier is
worth building only if these numbers say the prompt does not hold." This run is that evidence. The
prompt already forbids all of it — `WRITER_CITATION_REVISION` says keep every marker on a kept claim,
`WRITER_PATCH_CLOSE` says change only what a task names — but a prompt rule is not enforcement, and a
mechanical measurement with no consumer catches nothing.

**Decision.** Two mechanical gates on the numbers already computed, each spending at most
`revision.repair.repair_cap` (default 1) extra calls to the **same writer** that produced the failing
draft, before triage, before any critic, before the `OrchestratorView` or the controller sees anything.

1. **Marker-less body.** `body_markers == 0 and source_entries > 0`. Checked on every draft
   `_citation_fields` measures — the first draft, a patch, a rule-9 polish pass, a rule-13 rewrite —
   because a whole-document regeneration is exactly where D-writer-citation-continuity found markers
   get lost, and a first draft that lists sources with no marker in the body is the same defect on the
   very first tick.
2. **Out-of-scope rewrite.** `out_of_scope > revision.repair.max_out_of_scope` (default 6), on a
   **patch-mode revision only** — never the first draft, a polish pass, or a rule-13 rewrite, the same
   three cases `_scope_fields` already stays silent for, and additionally never under
   `revision.mode: rewrite`: a writer told to regenerate the whole document is doing what it was asked
   when it touches everything, so a high `out_of_scope` there is the mode working, not a defect. The
   default of 6 is D-scoped-revision's own observed healthy ceiling, restated as a threshold rather
   than left as a fact in a decision file nothing reads at runtime.

**The repair turn.** `graph._writer_repair` makes one more call to the same alias, same system prompt
shape, same call timeout as the generation it repairs — nothing about the call machinery differs, only
the prompt does. `prompts.writer_repair_turn` states the gate(s) that fired with their concrete numbers
(only the sentence for a gate that actually fired — a run tuned to tolerate a high `out_of_scope` never
sees that wording on a marker-only failure), then hands back exactly three things: the writer's own
failing draft, the previous artifact it revised from, and the fix tasks it already had. Nothing else.
No tool is offered — the point of this turn is to restore text the writer already produced, not to run
another research pass, so `web_search`/`read_source` are withheld rather than left available and
unused. The writer sees no critic identity and no new source text: everything in the prompt is
something it already held before this call.

`graph._repair_draft` re-measures the repaired text with the same `_citation_fields`/`_scope_fields`
functions and ships it whether or not the gate cleared. This never loops chasing a clean measurement —
each pass through the bounded `repair_cap` loop either resolves or does not, and once the cap is spent
the draft ships as it stands. A repair call that itself fails (`ModelCallError`, or an empty
completion) is caught, logged, and treated as an unresolved attempt: the *original* draft ships, never
a partial or malformed one, and a repair can never abort a run that would otherwise have continued.

**Why the same writer.** The census and scope numbers describe what *this model* dropped from *its own*
draft; asking a different model to reconstruct byte-identical paragraphs it never wrote would trade one
defect (lost markers) for another (a second author's prose grafted mid-document, which is exactly what
D-scoped-revision's patch licence exists to prevent). The same author already holds the context needed
to restore what it deleted, and the repair call is bounded and audit-visible either way — visible on
`model_call` events like any other writer call, and on `repair_attempted` on `generate` whether or not
it fires.

**Why one turn, not a loop.** A writer that fails the same gate twice is not converging toward a fix,
and a repair loop chasing zero would spend the run's writer budget on a model already shown not to
resolve it — no different in kind from why `_citation_fields`/`_scope_fields` were warn-only until now:
an enforcing mechanism must be bounded or it becomes a second unbounded loop layered on top of the
controller's own capped one (QP7). `repair_cap` defaults to 1 and is configurable for the operator who
wants to spend more calls chasing the same gate; nothing in the mechanism assumes it is exactly one.

**Why these thresholds and not a stricter gate.** Gate 1 has no tunable — a body that lists sources and
cites none of them in prose is unconditionally a defect, on every draft. Gate 2's `max_out_of_scope: 6`
is deliberately generous: D-scoped-revision's own healthy-run range runs 0-4, occasionally 6, so the
default ceiling is exactly the top of the range a working patch revision already produces, not a
tightened one. A stricter default would fire on ordinary variance and spend calls on drafts that were
never the problem this decision was written to fix.

**Invariants touched: none.** Blind orchestrator and author exclusion are untouched — the repair turn
never reaches triage, the `OrchestratorView`, or the controller, and it is the *same* author repairing
its *own* draft, so no author-exclusion or clean-record eligibility question arises (RC-002's per-hash
reset already fires once, on whichever hash ships). The artifact hash (`report.artifact_hash`) is taken
from the final — possibly repaired — text, so idempotent replay and the resume fingerprint
(`graph._run_fingerprint`, which hashes `roster` and `budgets`, not `revision`) are unaffected. Fail-closed
lenses, severity floors, and untrusted-text-never-reaches-a-generator-as-instruction are all unchanged:
the repair prompt carries only text the writer already held (RA-010).

**Event fields, on `generate`, integers and one closed enum, present only when a gate fired** — no URL,
no report text (RA-016):

| field | meaning |
|---|---|
| `repair_attempted` | 1 if either gate fired and `revision.repair.enabled`, else absent |
| `repair_reason` | `markerless`, `out_of_scope`, or `both` |
| `repair_resolved` | 1 if the gate(s) cleared by the time the repair budget was spent, else 0 |

The existing census/scope fields (`body_markers`, `source_entries`, `out_of_scope`, …) are unchanged in
name and meaning — they describe whichever text actually ships, repaired or not, so an A/B comparison
written against the old warn-only fields still reads correctly; it now also sees fewer marker-less and
over-threshold rows, which is the point.

**Deliberately not done.**

- **No gate on content deleted inside a task's scope.** A writer can still discharge a fix task by
  deleting the paragraph it names — round 8's real failure mode above, `entries_removed` climbing
  inside paragraphs the fix tasks *did* name. That is a selection/triage concern (what the run does
  with a draft that is technically in-scope but worse), not a census-gate one, and belongs to a later
  decision.
- **No change to round selection.** `review.selection` (`fewest_defects` / `latest_unblocked`) is
  untouched; this decision only changes what one generation call produces before selection ever runs.
- **No roster change.** `nemotron`'s deletion behaviour and `mistral`'s marker-loss rate are both
  candidates for `ra audition`, not a mechanical gate — a repair turn catches the shape of the defect
  every time it recurs, which a roster change does not guarantee.
- **A "too few markers" or partial-coverage threshold.** Gate 1 is exactly `body_markers == 0`, the
  total-loss case; partial coverage still routes through the evidence critic and
  D-bibliography-integrity's per-entry orphan findings, unchanged.
- **Merging the repair call's token/search counts into the `generate` event's `tokens`/`searches`/read
  fields.** Those describe the primary drafting call, as they did before; the repair call carries no
  tools to use in the first place.

Cites D-writer-citation-continuity (the census this gates), D-scoped-revision (the scope measurement
this gates, and the healthy-range figures the default threshold is drawn from), D-claim-scoped-patch
(why `restated` paragraphs are already excluded from `out_of_scope` before this gate ever sees it, so a
claim carried to every restatement it is fixed in does not trip gate 2), and D-no-hedge-discharge
(`additive_only`, the sibling measurement this decision does not gate).
