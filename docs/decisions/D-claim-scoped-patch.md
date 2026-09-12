## D-claim-scoped-patch — a patch carries a fix to every restatement of the claim, and never stands in for text it did not edit

**The finding.** Every one of the four production runs that finished between 2026-09-10 and
2026-09-12 on the current build ended `exhausted_unresolved` through rule 6 — eight rounds, material
issues outstanding. None reached `material == 0`; the closest was one major, once. The per-round
material counts oscillate rather than fall (`16,31,12,15,9,14,9,8`; `16,9,9,14,8,10,6,13`), which is the
stationary process D-scoped-revision measured six runs earlier — that decision narrowed the *edit* to
the flagged paragraph, and the process it was addressing is still there.

Reading the defects the terminal round left standing shows why. Of the **20 major** defects across the
four runs, **11** are one of three things a paragraph-scoped patch produces by construction:

- **Copies.** The report frame (D-report-template) restates each load-bearing claim up to three
  times — a cited sentence in `## Conclusion`, a cited bullet in `## Key findings`, and the section
  that argues it. A fix task names the one locus its critic quoted. One run ended with the same
  unsupported "roughly 90% efficiency" figure flagged `misrepresented_source` at S1.P1, S2.P1 and
  S6.P1; another with the same "significant portion of developed-world debt" claim flagged
  `overstated_claim` at S1.P1, S2.P1 and S4.P1. Seven of the eleven.
- **Self-contradiction the patch created.** A writer that qualifies, re-cites or removes a claim at
  the named locus and is *forbidden* to touch its copies leaves a report that says two different
  things. The next pass reports exactly that: an `overstated_claim` because the conclusion says
  autonomous aircraft "require" a property the body now says they "benefit from"; a
  `conceptual_conflation` because the body distinguishes two mechanisms the conclusion still lumps;
  an `invalid_inference` because "the report explicitly acknowledges the developed-world share is not
  separately quantified, yet still states the developed-world specific conclusion". Three of the
  eleven, and each is a defect the previous round's fix manufactured.
- **Decay of the untouched text.** A section whose entire body had become the string *(No changes
  required.)* — the writer stood in for text it was told to return byte-identical — flagged
  `unclear_structure` and escalated to major; and section numbering that drifted across rounds
  (a `6.1` with no `6`, a `Section 10` gone) as recurring minors.

The remaining nine are ordinary sourcing and overstatement defects, and one run had none of the
three signatures at all — that run's writers changed 12–33 paragraphs a round, so it was never
patching in the first place, which the `out_of_scope` measurement D-scoped-revision added shows
directly.

These figures come from the operator's own `audit.json` trail, which is not part of this repository,
so as in D-scoped-revision they are the *motivation* and not the warrant (QP9). What the decision
rests on is the mechanism, which is checkable against the frame and the prompt: a frame that
restates every claim three times, and a licence that permits editing one of the three.

**The decision.** The patch licence's unit becomes the **claim**, not the paragraph. Under
`revision.mode: patch` the closing instruction now says: when a task qualifies, weakens, re-cites
or removes a claim, make the same change in every other passage that restates that claim, so the
report does not say two different things about it; those restatement edits are in scope and
nothing else is. Two further sentences close the decay paths observed above: reproduce every
unedited paragraph in full, never a placeholder, an ellipsis or a note such as *(no changes
required)*; and keep every heading's text exactly as it is — no numbering, renumbering, dropping
or merging of sections. The byte-identical rule is unchanged for everything the licence does not
name, and the "byte-identical" wording D-scoped-revision chose is kept for the reason it gave.

**Measurement follows the licence.** `report.revision_scope` takes the tasks' `claim_span`s
alongside their loci and classifies a changed paragraph that no task named, but whose *previous*
text restated a flagged claim, as `restated` rather than `out_of_scope`. The `generate` audit
event gains a `restated` count; `out_of_scope` keeps meaning what it meant — text nobody
complained about, re-rolled — so the A/B D-scoped-revision set up is not muddied by the edits this
decision licenses. Restatement is a longest-common-run-of-words test (`report.restates`,
`RESTATEMENT_MIN_WORDS = 8`, a shorter span must match whole): restatements in this pipeline are
near-verbatim, because the frame asks for the same finding three times and writers copy, while two
paragraphs on the same topic share vocabulary and not a sentence. It is matched against the old
text because that is where the copy was; what the writer turned it into is not the question. The
check stays **warn-only**, exactly as D-scoped-revision and D-refine-audition set it: nothing
rejects a draft on its verdict. With no spans given the report is what it was, so every existing
caller and every pre-existing audit number keeps its meaning.

**Why this is the patch licence corrected and not the rewrite licence returning.** D-scoped-revision's
arithmetic still holds: re-rolling forty paragraphs to fix five grows as many defects as it retires.
A claim's restatements are two or three sentences the fix already implicates in substance — the
critic's finding is about the claim, and the copies say the claim. Editing them is finishing the
fix; leaving them is what turned one finding into three and added a contradiction. Nothing about
what a critic reads, who reviews, author exclusion, the blind orchestrator, or the per-generation
reset of clean records (RC-002) moves; [isolation.md](../isolation.md)'s "scoping the edit is not
narrowing the review" section holds word for word with "paragraph" read as "claim". No controller
rule, `ControllerInput` or `OrchestratorView` field, severity floor, or budget changes, so the
termination argument in [convergence.md](../convergence.md) is untouched.

**What this does not fix, stated plainly.** In one run a two-paragraph patch took the material count
from 1 to 7: five of the six reviews on the earlier draft were clean, and a fresh slate on nearly
the same text found seven majors. That is critic run-to-run variance and slate rotation — the
logic slate alternates with the writer, and in two of the four runs one critic filed four to ten
logic issues per pass on drafts its alternates filed zero to two on — and no writer-side licence
reaches it. The existing answers are the dispute channel (D-writer-disputes), whose whole-run
budget of six was spent in every run and whose arbiter upheld 6 of 24 disputes, and the audition
(D-critic-audition, D-completeness-pool-noise), which is where a critic's invented-issue rate is
measured and acted on. Both are named here as the follow-up, not attempted.

**Deliberately not done.** No change to the frame: collapsing the three restatements into one
would be a reversal of D-report-template, and the conclusion-first shape is what the reader
experience is built on. No critic-side change asking critics to flag every copy — that raises
counts and noise surface to achieve what the writer-side licence achieves for free. No enforcing
tier for placeholders or headings — the measurement comes first, per D-scoped-revision, and a
rejected draft costs one of three `writer_attempts`. No roster or dispute-budget change — those
are measured decisions with their own evidence bar. No live A/B in this PR: the `restated` field
and the terminal-status mix over the next runs on this build are the measurement, read the way
[run-provenance.md](../run-provenance.md) prescribes.
