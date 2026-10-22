## D-read-before-citing — unread citations receive a mechanical finding

**The finding.** `read_source` has been offered to writers since D-writer-source-reads, and a
reviser may reopen the draft's own citations since D-writer-rereads-cited-sources, but both are
*affordances*: nothing makes a writer use them. A production writer on a long-context, tool-optional
model drafted a whole report from memory — zero searches, zero reads — and listed a bibliography of
which a share was invented addresses. Later writers patched that bibliography without rebuilding it,
so a source chosen from memory kept backing sentences it never supported into the accepted report. A
writer that does read re-reads only some of what it cites. The result is a citation that asserts "I
read this" when no model did.

**The decision.** Effective only when the run both reads (`search.read_sources`) and verifies
(`search.verify_sources`) — the gating every reading feature already uses; with either off, no prompt
byte, finding or event field changes.

1. **Writer rule** (enforced since D-writer-must-read, which supersedes in place the earlier prompt-only
   status). `prompts.WRITER_READ_FIRST_ADDENDUM`, appended to the writer system prompt in
   that case: search for and `read_source` every page before citing it; cite only pages read in full
   text; on a revision read a cited page before keeping a claim that rests on it. It restates, and
   does not loosen, D-writer-citation-continuity (never renumber). Superseded in part by
   D-no-shrink-discharge: "re-source" means cite a different page the writer has read in full text;
   deleting the citation while the claim stays is not re-sourcing, and a revision that does it is
   filed against.
2. **A run-wide read log.** `State.read_log` maps URL to the `SourceOutcome` value of every page a
   writer's `read_source` resolved (`graph._merge_read_log`). It is built from the successful
   attempt's `ReadSession`, reuses the data `_read_fields` already derives, and refetches nothing. A
   full-text read is never downgraded by a later failure. `not_retrieved` (allowlist refusal) and
   `budget_exhausted` are not facts about a page and are not recorded. Per-call allowlists
   (D-writer-source-reads) are untouched: the log never widens what any writer may read, and no
   writer is shown another's page text.
3. **A mechanical finding.** `triage.mechanical_unread_issues`, minted in `graph._critique_one` beside
   the D-bibliography-integrity findings (evidence lens, completed review only): one finding per sentence (covering every
   entry it cites) for each `## Sources` entry that has an address, is cited by a body marker, and whose URL the log does not
   hold at `full_text`. Entries are bounded by the same `search.max_source_urls` ceiling.
   - **Category `uncited_claim`, severity `major`** (its floor, so the clamp is a no-op). No new
     category (taxonomy is unchanged): the reader is told a claim rests on a source and cannot rely on
     anyone having checked it, which is what `uncited_claim` names. Critics remain forbidden from
     raising it on a marked claim; this is pipeline-minted, as a dangling marker already is.
     `major` rather than `minor` because a `minor` is outside convergence and would not change a
     writer's behavior, which is the failure being fixed.
   - **Always dischargeable.** An unattempted page: read it, or re-source, weaken or remove the claim.
     A page whose read already failed (`not_found`, `paywalled`, `blocked`, `unreadable`, `empty`,
     `error`, `metadata_only`) returns the same refusal for the rest of the run (the fetch cache is
     monotone), so that finding says not to read it again and to re-source. Once the run's read budget
     is spent, reading cannot discharge anything and no finding is minted, so the rule can never
     demand the undoable. Entries with no address cannot be read and get none.
   - **Anchoring.** Every unread entry is covered. Each is anchored on its first citing sentence not
     already taken by an earlier entry's finding (the `_issue_key` dedupe would otherwise collapse
     them). When every sentence citing an entry is taken (`... confirmed [1][2].`), the entry is
     folded into that sentence's one finding, which names every label in `citation_id` and carries
     each entry's fixed instruction.
   - **A not-found page** still carries its own blocking `fabricated_citation` from verification
     (D-notfound-fabrication); the unread finding on top of it adds only the re-source instruction.
4. **Measurement.** The `generate` event carries `cited_unread`, an integer: the addressable cited
   entries not yet read in full text, computed on the shipped text whether or not the budget is spent.
   Absent when the rule is off.

**Isolation (docs/isolation.md).** The finding's fields are an entry label, the entry's address
(truncated to 120 characters, as the duplicate-entry finding already does), a closed-vocabulary
outcome and fixed instruction text. No page text and no other model's words enter a context. The read
log holds addresses and a closed vocabulary only, lives in graph state, and is read by the evidence
lens's mechanical block and the event — never by `OrchestratorView`, the controller or any prompt.
The event carries counts.

**Cost and residual.** More reads per run (bounded by `read_char_budget`, unchanged). A writer may
still read a page and misread it; that is claim-level verification's question (D-claim-level-verification),
not this rule's. Running without a read tool is unaffected.

**Enforcement and rollback.** D-writer-must-read adds a separate, default-on
`search.require_writer_reads` gate: a writer that drafts without a full-text read is retried, then
the pool is rotated, within the existing attempt budget. If that walk cannot obtain a read draft,
the held unread draft still ships with `unread_accepted: 1`; this decision's mechanical finding
continues to cover its unread citations. Turning off `search.require_writer_reads` disables that
retry gate but not this finding. Turn off `search.read_sources` or `search.verify_sources` to disable
both behaviors.
