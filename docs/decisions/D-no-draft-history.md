## D-no-draft-history — the report never talks about earlier drafts of itself

**The finding.** The operator's private audit trail contained a final report that narrated its own
revision history. That observation is recorded here without identifiers, counts, or examples. It is
the **motivation and not the warrant** (QP9): the reader receives only the final report, so an earlier
draft is not an artifact the reader can inspect.

**The warrant is the mechanism, and it is checkable in this repository.** Revision writers receive
the previous artifact and fix tasks, and existing evidence-task wording in `prompts.py` uses
draft-relative language such as "previous draft" and permits removing a claim. Before this decision,
`WRITER_SYSTEM` prohibited only general process meta-commentary; it did not say that the final report
must omit the revision frame supplied as input. The writer's returned text becomes the report, while
the ordinary critic contract had no deterministic check for draft-history narration. Thus the prompt
surface permitted input-only revision context to survive into the reader-facing artifact. The fixed
prompt rule, mechanical detector, bounded repair, and blocking backstop below close that repository-
visible path without relying on the private observation as evidence that it occurs at any rate.

**The decision.** Effective while `review.draft_history_check` is true (default).

1. **Prevention: prompt text.** `WRITER_SYSTEM` gains one rule: the report is the only document the
   reader receives, so it never mentions an earlier draft, a fix task or a reviewer; a withdrawn claim
   is deleted without trace; a gap is stated as a fact about the sources. The revision prompt adds
   that removing a claim means deleting it. Both are unconditional prompt text, not behind the knob.
2. **Detection: a fixed phrase list.** `triage.draft_history_sentences` scans body sentences (the
   `## Sources` section excluded) for: an *earlier / previous / prior / original / first / last …
   draft / revision / iteration*; *this / the current / the present draft / revision / iteration*;
   *removed / withdrawn as unsupported / unverified*; *claim(s) / figure(s) / assertion(s) removed*;
   and a dash followed by *Removed*. It is a matching rule, not a judgement.
3. **Exits from a false positive.** A phrase followed by "of" and the name of some other document
   ("the earlier draft of the bill") does not match; "of this report" still does. A phrase is also
   exempt when the question itself uses that noun, so a question about a draft law may say "the
   draft". The finding's instruction names the first exit, so a writer caught by a false positive can
   always clear it in the report.
4. **Repair turn: gate 3 of D-census-gated-repair.** Before any critic reads a new draft, a draft
   with one or more such sentences spends the existing `revision.repair.repair_cap` on the same
   writer, with the same licence rules. The prompt carries a count and fixed wording, never the
   matched sentences. `repair_reason` keeps its old values when gate 1 or 2 also fired; it is
   `draft_history` when gate 3 fired alone, and `repair_draft_history: 1` marks every repair gate 3
   took part in. `repair_resolved` requires every fired gate to clear.
5. **Backstop: a blocking finding.** The completeness lens result of every completed review gains one
   pipeline-minted `unclear_structure` finding per matching paragraph, anchored on its first matching
   sentence, at **`blocking`**. The category's floor is `minor`; minting above the floor is an
   escalation, which RC-005 permits, so the clamp is still a no-op. Blocking is the point: production
   runs `review.selection: latest_unblocked`, which ranks on blocking alone, so a `major` would let
   the narrating draft ship over a clean earlier one. No taxonomy row changes.
6. **Measurement.** Each `generate` event carries `draft_history_refs`, the count of matching
   sentences in the draft that shipped from that generation (an integer, RA-016).

**Isolation (docs/isolation.md).** The finding carries only `claim_span`, a bounded verbatim sentence
from the current report, plus fixed rationale and instruction. The repair prompt carries a count. No
critic prose, page text or previous-draft text enters either. Nothing reaches the controller view
beyond the ordinary severity tally.

**Residuals.** (1) The phrase list misses paraphrases ("as noted before revision", "an older version
said"). The prompt rule is the first line; the list catches the shapes production showed. (2) "this
draft regulation" matches when the question does not say "draft"; the writer's exit is "the draft
regulation". (3) Section headings are not scanned, because a finding must anchor on a paragraph
sentence.

**Rollback.** `review.draft_history_check: false` removes the repair gate, the finding and the event
field. The prompt text stays.
