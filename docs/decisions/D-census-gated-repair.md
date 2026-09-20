## D-census-gated-repair — a draft that fails the citation census gets one repair turn before the critics see it

**The finding.** A production revision came back with every `[n]` marker gone from its body while its
`## Sources` list was still there. The census on the `generate` event recorded exactly that
(`body_markers=0`, `source_entries>0`) and the scope measurement recorded that most of the changed
paragraphs were ones no fix task named. Both numbers were warn-only, so the draft went to the critics
as it stood. The only findings a marker-less body leaves the evidence lens are uncited-claim findings,
and the next round's writer discharged those by deleting the claims — and their sources — rather than
restoring the markers. The shipped report was a fraction of the previous round's.

That observation comes from the operator's private audit trail and is recorded here without
identifiers or rates. As in D-no-hedge-discharge and D-role-call-timeouts, it is the **motivation and
not the warrant** (QP9): it moves nothing that a deployment inherits by omitting a field.

**The warrant is the mechanism, and it is checkable in this repository.** The prompt already forbids
both outcomes: `WRITER_CITATION_REVISION` says keep every marker on a kept claim and delete an entry
only when nothing cites it; `WRITER_PATCH_CLOSE` says change only what a task names and return the
rest byte-identical. The measurements that detect a violation already exist: `_citation_fields`
(D-writer-citation-continuity) counts markers and entries on every draft, `_scope_fields`
(D-scoped-revision) counts `out_of_scope` on every patch-mode revision. Both decisions made them
warn-only on purpose — D-writer-citation-continuity's "Deliberately not done" names "a generate-time
gate or repair turn"; D-scoped-revision said "an enforcing tier is worth building only if these
numbers say the prompt does not hold." A prompt rule is not enforcement, and a measurement nothing
consumes catches nothing. This decision adds the consumer, bounded, off by default.

**Decision.** Two mechanical gates on the numbers already computed. Either firing spends at most
`revision.repair.repair_cap` extra calls (default 1) to the **same writer** that produced the failing
draft, before triage, before any critic, before the `OrchestratorView` or the controller sees anything.

1. **Marker-less body.** `body_markers == 0 and source_entries > 0`. Checked on every draft
   `_citation_fields` measures — the first draft, a patch, a rule-9 polish pass, a rule-13 rewrite —
   because a body that lists sources and cites none of them violates the citation prompt on every
   kind of draft, the first tick included.
2. **Out-of-scope rewrite.** `out_of_scope > revision.repair.max_out_of_scope`, on a **patch- or
   ops-mode revision** (D-ops-revision). It never fires on the first draft, a polish pass, or a
   rule-13 rewrite — the
   three cases `_scope_fields` already stays silent for — and never under `revision.mode: rewrite`,
   where a writer told to regenerate the whole document is doing what it was asked when it touches
   everything. It also never fires under `revision.scope_check: off`, because `_scope_fields` then
   returns no `out_of_scope` at all: no measurement, no gate.

`RepairConfig.enabled` defaults to `false`, which preserves the warn-only behaviour exactly. The
shipped production roster opts in with `enabled: true`. `max_out_of_scope` is an operator ceiling
with a code default of 6; the repository holds no evidence for a portable threshold, and none is
claimed here. `restated` paragraphs are already excluded from `out_of_scope` (D-claim-scoped-patch),
so a fix carried to every copy of a claim never trips gate 2.

**The repair turn.** `graph._writer_repair` calls the same alias with the same system prompt shape
and the same call timeout as the generation it repairs; only the user prompt differs, and no tool is
offered — the job is to restore text the writer already produced, not to research further, so
`web_search` and `read_source` are withheld rather than left available. The prompt,
`prompts.writer_repair_turn`, contains exactly these inputs, every one of them something the writer
already held or produced:

- the run's date line, as on every writer call;
- the gate sentence(s) with their concrete counts — only for a gate that actually fired;
- the question;
- the writer's own failing draft;
- the previous artifact, on a revision;
- the fix tasks it already had.

No critic identity, no lens, no new source text (RA-010). The restore instruction — return every
paragraph outside the fix tasks byte-for-byte from the previous artifact — is included only under the
**patch licence**, the same condition the drafting call used to select `WRITER_PATCH_CLOSE`
(`revision.mode == "patch"`, not a polish pass, not a rule-13 rewrite). Under the ops licence, the
repair instead asks for operations on the labelled previous draft and splices the reply into that draft
(D-ops-revision). A polish pass, a rewrite, or a `mode: rewrite` deployment was asked for the whole
document; its repair turn asks for the whole corrected report and says nothing about restoring
paragraphs (D-scoped-revision's exemption carries over unchanged).

`graph._repair_draft` re-measures the repaired text with the same `_citation_fields`/`_scope_fields`
functions and ships it whether or not the gate cleared. It never loops chasing a clean measurement:
each pass through the bounded `repair_cap` loop either resolves or does not, and once the cap is
spent the draft ships as it stands. A repair call that itself fails (`ModelCallError`, or an empty
completion) is caught, logged, and treated as an unresolved attempt: the latest successfully repaired
draft ships (or the original draft when the first attempt fails), never a partial or malformed
completion, and a repair can never abort a run that would otherwise have continued.

**Why the same writer.** The census and scope numbers describe what *this model* dropped from *its own*
draft; asking a different model to reconstruct byte-identical paragraphs it never wrote would trade one
defect (lost markers) for another (a second author's prose grafted mid-document, which is exactly what
D-scoped-revision's patch licence exists to prevent). This is not a critique surface: no verdict is
formed, no finding is filed, no clean record is written, and nothing the repair produces enters review
or control except as the artifact every eligible non-author critic then reads under the unchanged
author-exclusion rule (QP4). The repair call is visible on `model_call` events like any other writer
call, and on `repair_attempted` on `generate`.

**Why one turn, not a loop.** A writer that fails the same gate twice is not converging toward a fix,
and a repair loop chasing zero would spend the run's writer budget on a model already shown not to
resolve it — no different in kind from why `_citation_fields`/`_scope_fields` were warn-only until now:
an enforcing mechanism must be bounded or it becomes a second unbounded loop layered on top of the
controller's own capped one (QP7). `repair_cap` defaults to 1 and is configurable for the operator who
wants to spend more calls chasing the same gate; nothing in the mechanism assumes it is exactly one.

**Invariants touched: none.** Blind orchestrator and author exclusion are untouched — the repair turn
never reaches triage, the `OrchestratorView`, or the controller, and it is the *same* author repairing
its *own* draft, so no author-exclusion or clean-record eligibility question arises (RC-002's per-hash
reset already fires once, on whichever hash ships). The artifact hash (`report.artifact_hash`) is taken
from the final — possibly repaired — text, so idempotent replay and the resume fingerprint
(`graph._run_fingerprint`, which hashes `roster` and `budgets`, not `revision`) are unaffected. Fail-closed
lenses, severity floors, and untrusted-text-never-reaches-a-generator-as-instruction are all unchanged:
every fenced block in the repair prompt is `_neutralized` (D-fence-scrub-all-directions), and the prompt
carries only text the writer already held (RA-010).

**Event fields, on `generate`, integers and one closed enum, present only when a gate fired** — no URL,
no report text (RA-016):

| field | meaning |
|---|---|
| `repair_attempted` | 1 if either gate fired and `revision.repair.enabled`, else absent |
| `repair_reason` | `markerless`, `out_of_scope`, or `both` |
| `repair_resolved` | 1 if the gate(s) cleared by the time the repair budget was spent, else 0 |

`repair_resolved` means what its name says: a repair that makes gate 1 false by deleting the whole
`## Sources` section (so `source_entries` falls to 0) is recorded as **unresolved**, because that is the
delete-to-discharge shape the gate exists to catch, not a fix. The existing census/scope fields
(`body_markers`, `source_entries`, `out_of_scope`, …) are unchanged in name and meaning — they describe
whichever text actually ships, repaired or not, so an A/B comparison written against the old warn-only
fields still reads correctly. The re-measurement inside the repair loop does not repeat the census's
log warnings, so each stays countable as one per generation.

> Superseded in part by **D-ops-revision**: the licence the repair turn inherits is now three-valued.
> Gate 2 fires under `revision.mode: patch` *or* `ops` (the splice cannot stop a writer operating on
> many unnamed paragraphs, which is what gate 2 measures); under the ops licence the repair turn asks
> for operations on the labelled previous draft, never for a whole document, and its reply is spliced
> exactly as the drafting reply was. A repair reply with no applicable operation is an unresolved
> attempt that keeps the current draft, as a failed repair call already did. Everything else here —
> gate 1, the cap, the same-writer rule, the event fields — is unchanged.

**Deliberately not done.**

- **No gate on content deleted inside a task's scope.** A writer can still discharge a fix task by
  deleting the paragraph it names. That is a selection/triage concern (what the run does with a draft
  that is technically in-scope but worse), not a census-gate one, and belongs to a later decision.
- **No change to round selection.** `review.selection` (`fewest_defects` / `latest_unblocked`) is
  untouched; this decision only changes what one generation call produces before selection ever runs.
- **No roster change.** Whether a particular writer drops markers or deletes sources often enough to
  leave the roster is an `ra audition` question, not a mechanical gate — a repair turn catches the
  shape of the defect every time it recurs, which a roster change does not guarantee.
- **A "too few markers" or partial-coverage threshold.** Gate 1 is exactly `body_markers == 0`, the
  total-loss case; partial coverage still routes through the evidence critic and
  D-bibliography-integrity's per-entry orphan findings, unchanged.
- **Merging the repair call's token/search counts into the `generate` event's `tokens`/`searches`/read
  fields.** Those describe the primary drafting call, as they did before; the repair call carries no
  tools to use in the first place.

Cites D-writer-citation-continuity (the census this gates), D-scoped-revision (the scope measurement
this gates, and the patch-licence condition the repair prompt inherits), D-claim-scoped-patch (why
`restated` paragraphs are already excluded from `out_of_scope` before this gate ever sees it),
D-no-hedge-discharge (`additive_only`, the sibling measurement this decision does not gate, and the
motivation-versus-warrant pattern this entry follows), D-role-call-timeouts (the same QP9 pattern for a
configurable deployment option), and D-fence-scrub-all-directions (every fenced block in the repair
prompt is scrubbed).
