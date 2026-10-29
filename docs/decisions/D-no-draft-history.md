## D-no-draft-history — the report never talks about earlier drafts of itself

**The finding.** Production run `run-62eba4c35567` (build `25e9609`) shipped a report that narrated
its own revision history in 40 sentences: "the Ballotpedia page the previous draft cited", "Claims in
the earlier draft … are removed as unsupported", "No source read for this revision reports …", and
section bullets titled "Nevada 2024 Measure — Removed". The reader receives only the final report.
They never saw an earlier draft, so these sentences describe nothing they can see, and they crowd
out the answer.

The cause is the fix-task text the recent evidence decisions hand a writer. D-no-shrink-discharge's
finding says "The previous draft supported this claim with its entry …, which this revision no longer
cites", and several findings end with "or remove the claim". A writer answered those tasks by
narrating them into the report. The writer system prompt only said "no meta-commentary about your
process", which no writer read as forbidding this. No critic flagged it, so the controller and round
selection treated the draft as an improvement.

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
