## D-uncited-bibliography — a bibliography the body never cites is one uncited claim, not silence

**The finding.** Production run `run-116cc0ea4cac` shipped a report with a `## Sources` list of
eleven entries and no inline `[n]` marker anywhere in its body. Nothing mechanical reported it.
`triage.mechanical_bibliography_issues` returned nothing for a body with no marker at all, on the
stated ground (D-bibliography-integrity) that "the writer template and the completeness lens already
own" that defect. The completeness lens has no citation category (`taxonomy.LENS_CATEGORIES`), so it
owns nothing of the kind. The only remaining channel was the evidence critic's own `uncited_claim`
judgement, and in that run it fired erratically: 19 and 21 issues in one round, none in the next,
four after a one-paragraph change. The same gap turns off claim-level verification too, because
`claimcheck.pairs` pairs only sentences that carry a marker, so it found no pairs and checked nothing.

The fact itself is decidable by string comparison, like the three D-bibliography-integrity already
mints: the report lists references, and no body sentence points at any of them.

**The decision.** When a report has a `## Sources` section with at least one entry that is a real
reference (it carries a URL or an explicit number such as `[3]` or `3.`), and no body paragraph
outside that section carries a citation marker, `mechanical_bibliography_issues` mints **exactly
one** finding (`triage._unmarked_bibliography`). It mints no per-entry orphan findings and no
duplicate findings in that state:

| field | value |
|---|---|
| category | `uncited_claim` |
| severity | `major`, the category's floor (`taxonomy.SEVERITY_FLOOR`) |
| locus | the first body paragraph that has a sentence with non-empty normalized text |
| `claim_span` | that sentence (split with `excerpt._SENTENCE_END`), cut to `MAX_SPAN`, confirmed against that paragraph with `_locate_text` |
| `citation_id` | none, since no marker exists to name |
| rationale | `## Sources lists N entries and no body sentence carries a [n] marker, so no claim can be traced to or checked against its source.` |
| instruction | put each supporting entry's `[n]` inside each material claim, starting here; weaken or label as inference any claim no entry supports; remove entries nothing then cites; no new source is needed |

Every field is built through `_minted`, so it is bounded on construction as D-bibliography-integrity
requires. Markers are still read with `excerpt._MARKER` and `excerpt._cited` over body paragraphs
only, so an entry's own `[1]`, or an entry that mentions another entry's number, is not a citation.

**Why one finding and not one per entry.** In that state every entry would be an orphan. Eleven
`unclear_structure` findings, each saying "cite it or remove it", describe one defect eleven times
and point the writer at the cheaper of the two instructions, which is deleting the list. The single
finding describes the actual defect, anchors it where a writer starts attaching markers, and gives
an instruction the writer can carry out without searching again. Once any marker exists, the
per-entry checks come back and name the entries still unattached.

**Why a real reference is required.** `## Sources` followed by "None." or "No sources were
consulted." is split into one entry by `fetch.source_entries`, but that entry claims nothing. A body
cannot fail to cite a list that lists nothing, so such a section mints nothing.

**Gates are unchanged.** It runs where the other bibliography checks run: `graph._critique_one`, on
the **evidence** lens, on a **completed** review, with verification on or off.

**Interactions.**

* The evidence critic's own `uncited_claim` findings are **not** dropped or merged into this one.
  Dropping them would let a mechanical finding launder away a critic's judgement, the move
  D-bibliography-integrity already refused. They describe individual claims, and they collapse with
  this finding only if they share its `_issue_key` (section, paragraph, category, span).
* Two critics of the evidence lens both carry this finding, and `_issue_key` collapses it to one, so
  the totals and the stagnation signature count it once.
* `claimcheck.pairs` stays empty in this state. This finding is what tells the writer why.

**Invariants.**

* *Severity floors clamp up only.* Unchanged. The finding is minted **at** the `uncited_claim`
  floor, so `clamp` is a no-op on it (`test_every_minted_finding_is_at_its_own_severity_floor`
  now covers it). `SEVERITY_FLOOR` is untouched.
* *Fail-closed lens validation.* Unchanged. Like its siblings the finding is pipeline-authored and
  bypasses `validate_issue`. `tests/test_triage.py` runs it through `validate_issue` under the
  evidence lens anyway, including a long, emphasized first sentence. It attaches only to a completed
  review, so a failed lens is never promoted
  (`test_a_failed_evidence_lens_is_not_promoted_by_an_uncited_bibliography_finding`).
* *Blind orchestrator.* `OrchestratorView` gains one more counted `uncited_claim` and no content.
* *Author exclusion, termination, untrusted text never instructing a generator.* Untouched. The only
  report-derived text the finding carries is a bounded `claim_span`, which is text from the writer's
  own draft. The rationale and instruction are fixed pipeline text plus an entry count.

QP1 holds: whether a marker exists is a string fact, and the severity is the floor. QP10 holds: no
fetched body is read and no address is judged.

**Why not the alternatives.**

* *Leave it to the evidence critic.* Run `run-116cc0ea4cac` is the evidence that this does not work.
  A fact the pipeline can settle should not depend on a model choosing to state it
  (D-notfound-fabrication).
* *Mint one orphan per entry.* See above: one defect counted N times, with a cheaper instruction.
* *A completeness-lens category for "no citations".* A taxonomy change, a prompt change and an
  audition change, all to have a model re-derive by judgement what a string comparison settles.
* *Reject the draft at generate time.* The user chose warn-only measurement on the writer side. A
  critique finding goes through the ordinary convergence loop instead of burning writer attempts.

> Supersedes in part **D-bibliography-integrity**, whose rule that a report "whose body carries no
> citation marker at all" mints nothing is replaced by the single finding above. Its three per-entry
> checks, their floors and loci, and everything else it decides are untouched.
