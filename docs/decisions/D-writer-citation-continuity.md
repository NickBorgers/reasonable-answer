## D-writer-citation-continuity — a citation is a marker in its sentence, a reviser keeps it, and every draft's markers are counted

**The finding.** Production run `run-116cc0ea4cac` stopped at the round cap (rule 5,
`needs_human_review`) and shipped a report whose `## Sources` list held eleven entries while its body
carried no inline `[n]` marker at all. `run-b06b18e7df3f` showed the milder form: a body citing eight of
its seventeen entries. A body with no marker disables three checks at once, none of which says so:

- `claimcheck.pairs` pairs only sentences that carry a marker (D-claim-level-verification), so it paired
  nothing;
- `triage.mechanical_bibliography_issues` returns nothing for a body with no marker
  (D-bibliography-integrity);
- the evidence critic's own `uncited_claim` judgement was left to fire alone, and did so erratically —
  19 and 21 issues in one round, none in the next, four after a one-paragraph change.

Three causes sit on the writer side:

1. **The prompts never said it.** Only the polish goal said "remove no citation". Nothing told a patch
   or rewrite writer to keep a marker on a claim it keeps, nothing forbade renumbering, nothing said what
   to do with an entry nothing cites any more — and the critics' instruction "remove the attribution"
   was never defined for the writer, which can read it as "delete the marker and keep the sentence".
   `REPORT_SKELETON` asked for "one numbered entry per citation" without saying a citation is the marker.
2. **Nothing measured it.** No `generate` event field counted markers, so the loss was found by reading
   the shipped report.
3. **A reviser could not open what it cites.** That half is D-writer-rereads-cited-sources.

**Decision.** Rules in the prompts, and a census on the event log. No gate.

*The shared rule.* `WRITER_SYSTEM` says a citation is the `[n]` marker inside the sentence it supports —
naming a source in prose, or listing it under Sources, cites nothing — and that every entry is cited by
at least one body marker and every marker has an entry. `REPORT_SKELETON` item 5 now asks for one entry
per source the body cites with an inline marker. Both ride the system prompt, so first drafts, revisions
and polish passes all hold them (D-report-template).

*The revision rule.* `WRITER_CITATION_REVISION`, carried inside `WRITER_RESOLUTION_STANDARD` so every
revision mode holds it (D-no-hedge-discharge placed the standard there for the same reason):

- keep every marker on a claim you keep, and remove a marker only together with its claim;
- delete an entry only when no remaining sentence cites it;
- **never renumber** — a removed entry leaves its number unused, and a new source takes the next number
  after the highest. A renumbered bibliography edits every citing paragraph, which patch mode's
  byte-identical rule forbids (D-scoped-revision), so without this the two rules collide;
- "remove the attribution" means: take the marker off that sentence, then cite an entry that does state
  the claim, restrict the claim to what a cited entry states, or label it as the report's own inference —
  never leave it standing as fact with no marker;
- a listed source may be cited for another claim it supports, read first where `read_source` is available.

*One mechanical instruction changes with it.* The duplicate-address finding D-bibliography-integrity mints
told the writer to "merge the entries and renumber", which the rule above now forbids — a writer handed
both would be told opposite things. Its instruction now merges into the lower-numbered entry, points the
higher number's markers at it and leaves that number unused. Category, severity, locus and span are
unchanged; only the fixed instruction text differs.

`WRITER_REWRITE_CLOSE` and `WRITER_PATCH_CLOSE` are untouched, so the D-scoped-revision A/B still
differs in exactly its close: both arms' prompts grow by the same standard and by nothing else. No
critic prompt changes, so no audition verdict goes stale.

*The census.* `excerpt.citation_census` and `excerpt.citation_changes`, spread into every `generate`
event by `graph._citation_fields` — first drafts, patches, rule-9 polish passes and rule-13 rewrites
alike, because whole-document regenerations are where markers get lost. Unlike `_scope_fields`, absence
is never "not applicable" for the census; only the comparison fields need a previous draft.

| field | on | meaning |
|---|---|---|
| `source_entries` | every draft | entries in `## Sources` (`fetch.source_entries`) |
| `body_markers` | every draft | marker occurrences before the Sources heading; `[1, 3]` is one |
| `cited_entries` | every draft | entries whose number some body marker cites (ranges expanded) |
| `dangling_markers` | every draft | distinct cited numbers no entry carries |
| `cited_sources_dropped` | revisions | sources the previous body cited and this one does not |
| `cited_sources_added` | revisions | sources this body cites and the previous one did not |
| `entries_removed` | revisions | sources listed before and not listed now |

A source is identified across drafts by its cleaned entry URL (`fetch.entry_url`), or — for an entry
with none — by its whitespace-normalized text with the list marker and number removed. Renumbering is
therefore never read as loss. The numbering rule is `excerpt.entry_numbers`'s own, so the census counts
what claim check and excerpting read. Every field is an integer: `events.jsonl` outlives a content purge
and carries no URL and no text (RA-016).

**Warn-only.** A warning is logged when a draft lists sources and carries no marker, and when a polish
pass drops a cited source. Nothing rejects a draft, triggers a repair turn, or reaches a critic, the
`OrchestratorView` or the controller. A generate-time gate would spend a writer attempt on a defect the
critics and the bibliography checks already own, and the census has to come first to show how often it
happens — the D-scoped-revision warn-only doctrine.

> Superseded in part by **D-census-gated-repair**. The census had shown that a marker-less body
> happens; a later production run showed what it costs downstream — the bibliography and claim checks
> pair nothing, and the next writer "resolved" the resulting findings by deleting most of the report.
> `body_markers == 0 and source_entries > 0` now spends up to `revision.repair.repair_cap` bounded
> repair calls to the same writer before any critic reads the draft. The census itself, its fields, and
> the "no critic, no `OrchestratorView`, no controller" boundary are unchanged; only "nothing rejects a
> draft, triggers a repair turn" no longer holds for this one condition. `revision.repair.enabled:
> false` restores this section exactly.

> Superseded in part by **D-ops-revision**: "delete a Sources entry only when no remaining sentence
> cites it" is mechanical under `revision.mode: ops` — a Sources operation that would raise the body's
> count of markers citing no entry is refused at splice time and counted (`ops_refused_dangling`).
> The prompt rule stays, for `patch` and `rewrite` and for the writer's own understanding; the census
> on every `generate` event is unchanged.

**Deliberately not done.**

- A generate-time gate or repair turn, for the reason above — narrowed by D-census-gated-repair to the
  marker-less-body condition specifically; a "too few markers" threshold below is still not done.
- A "too few markers" threshold. Partial coverage already draws per-entry orphan findings
  (D-bibliography-integrity); a report with *no* marker drawing one finding is a separate, later decision.
- Tolerating a numbered `## 10. Sources` heading.
- Critic prompt changes.
