## D-source-fidelity-direction-and-scope — the evidence lens checks a source's direction and scope, not only whether the page contains the words

**The finding.** Expert defect reviews of recent production reports, read against the runs that
produced them, found one gap repeatedly: the evidence lens asks, in effect, a single question —
*does the page contain this sentence?* — and three defect classes pass it. They were the most
serious defects in the set, and each survived a lens that had the page in hand and cleared the
citation.

*Direction.* A source quoted verbatim-correctly for a proposition its own finding cuts against. The
recurring shapes: a meta-analysis that found **no** relationship between a dose variable and an
outcome, cited as establishing that the relationship is dose-dependent; a review body's "insufficient
data to determine" rendered as a determination, so absence of evidence becomes evidence of absence; a
source's stated caveat read as its conclusion; and a page describing one tier of a product range
cited for a claim about a different tier.

*Scope.* A real, correctly quoted source about a different population, product, category or system
boundary, restated as if it were about the question's. The shapes: a life-cycle figure measured over
an average consumption pattern applied to a product used an order of magnitude more times, where the
report's own other source shows the split moving sharply with use; and a report whose load-bearing
sources are about a neighbouring product category, carrying most of the in-text citation marks.

*A fetch failure converted into an assertion about content.* A `misrepresented_source` filed against
a page that refuses every automated client, asserting a negative about content from an empty body —
whose only available remedy is deleting a good citation. The same finding repeating across rounds
because the extractor returned navigation chrome rather than the article, on a page that states the
claim verbatim. And a blocked page degrading the strongest available finding into `uncited_claim`
with the instruction "add a citation", on a claim that already carried one.

Two hygiene defects cost rounds alongside these. Critics filed `unclear_structure` findings about
section-numbering "gaps" that exist only in `report.render_with_loci`'s scaffolding — a section whose
paragraphs are elsewhere gets no `=== SECTION n: … ===` line — in one case consuming a substantial
share of a lens's output. And a critic, having no way to retract a finding, withdrew one inside the
JSON: rationale trailing off mid-sentence, instruction "no action needed, removing from list". Triage
passed it through at `major`.

The warrant is not that audit trail (QP9); it is the prompt text, checkable in the repo and
reproducible against synthetic fixtures in `tests/test_taxonomy.py` and `tests/test_fetch.py`.
`prompts._CATEGORY_MEANING[MISREPRESENTED_SOURCE]` read "the cited source plainly does not support
the claim as stated" and `LENS_BRIEF[Lens.EVIDENCE]` asked only whether a source "is described as
supporting something it plainly would not support" — neither names direction or scope. The block's
closing rules said `BLOCKED` "says nothing at all about whether the source exists" and did not say it
says nothing about what the source contains. Nothing in either surface distinguished a page that
contradicts a claim from one that supports it, or an unread body from a page that omits the claim.

**The decision.** What the evidence lens is *asked* changes; what triage accepts does not.

* **`misrepresented_source` is widened in the open, in both its meanings.** The verification-off
  meaning (`prompts._CATEGORY_MEANING`) and the sharpened meaning `critic_user` substitutes when a
  body arrived both now name the two failures alongside "does not contain the claim": a source whose
  own finding, conclusion or headline result cuts against the proposition it is cited for
  (*direction*), and a source whose population, product, category, system boundary, dose band or
  period is not the claim's while the report restates the finding as if it were (*scope*). The floor
  is unchanged at `major`. Scope is deliberately not `conceptual_conflation` — that category belongs
  to the logic lens and is about the report's own reasoning, where this is about what a cited page is
  evidence *for*. Scope is the writer rule `WRITER_SYSTEM` already states ("a finding stays attached
  to the units, cases or population it was measured on"), now raisable by a critic.
* **The verification-off meaning keeps its `plainly`.** With no page in hand the bar is still what
  the citation would support on its face. What widens is the kinds of failure, not the confidence
  required to report one.
* **`LENS_BRIEF[Lens.EVIDENCE]` states the two questions and their exclusions.** Does the page assert
  this, in this direction? Is the page's scope the claim's scope? And, written like
  D-conceptual-conflation's and load-bearing for the same reason — the audition measures the
  invented-issue rate in exactly this direction — three exclusions: not a stylistic mismatch of
  wording where the substance matches; not a source merely *broader* than the claim when it genuinely
  covers the claim's case; not a demand for a source the writer cannot get. The resolvable fixes are
  re-attributing, restricting the claim to the source's scope, or removing the attribution.
* **Instruction shape.** A `misrepresented_source` instruction must say what the source actually
  says, quoted or paraphrased from the excerpt, so an editor can re-attribute or restrict without
  opening the page; it may never propose keeping the citation and calling the claim unverified.
  (The writer side of hedge-discharge is separate and is not touched here; `WRITER_*` is unedited.)
* **An unread body licenses no finding about content.** In the evidence brief and in
  `fetched_sources_block`'s closing rules: for an entry that is anything but page text — `BLOCKED`,
  `COULD NOT READ`, `NO READABLE TEXT`, `NOT ATTEMPTED`, `COULD NOT RESOLVE`, a fetched-but-withheld
  body, or registry metadata — the critic may not assert what the page does or does not contain, and
  a body that is plainly not the article (navigation, a cookie notice, a menu, a paywall teaser)
  counts as unread. The `BLOCKED` rule gains its second half. And a claim that carries a citation
  marker is never `uncited_claim`: its problem, if any, is what that citation supports, and where
  that cannot be checked the honest finding is none.
* **Two hygiene rules, all three lenses (`critic_user`).** The `=== SECTION n: … ===` lines and
  `[S<n>.P<m>]` markers are addressing scaffolding added for the review; no reader of the report sees
  them, section numbers are handles, and a gap in them is an artifact of the rendering rather than a
  defect. And a critic may not file an issue in order to withdraw it: there is no retraction, every
  filed issue is triaged and acted on, so an issue whose rationale concludes it is not a defect or
  whose instruction requires no action is omitted instead.

`docs/convergence.md` carries the normative statement: the taxonomy row and the verification off/on
table are widened to match, and a new subsection *Direction and scope* governs the prompt constants
the same way the D-conceptual-conflation subsection governs the logic ones.

**The audition cache goes stale, by design.** `audition.prompt_hash` hashes the source-less critic
surface, which includes `LENS_BRIEF` and `_CATEGORY_MEANING`, so every cached verdict recorded under
the old evidence brief no longer matches and is recomputed rather than reused. That is
D-audition-rubric-identity working: a verdict measured under a different question is not evidence
about this one. Nothing fails closed — a stale verdict warns — and the sharpened, sources-present
meaning and the pages block remain outside the hash, as D-audition-source-mode requires.

**Invariants.** None of the six move, and this is the load-bearing claim of the PR. *Fail-closed lens
validation* is untouched: `triage.validate_issue` and `_require_quote` are not edited, so a span that
is not in the paragraph still fails the lens closed. *Severity floors clamp up only*:
`SEVERITY_FLOOR` is unedited and `misrepresented_source` stays `major`. *Author exclusion*, *blind
orchestrator* and *termination* are not in the changed surfaces at all — no scheduling, no signal and
no controller code is touched. *Untrusted text never reaches a generator as instruction* holds
unchanged: fetched bodies are still fenced, still neutralized, still shown to the evidence lens only,
and this PR adds no new text path — it adds rules the critic is given *about* that fenced text. The
change is strictly to what a critic is asked; every mechanical check downstream is the same code it
was.

**Why not the alternatives.**

* *A new category for scope (`out_of_scope_source`).* Rejected. The defect is already the definition
  of `misrepresented_source` — the source does not support the claim as stated — and a new category
  needs a floor, a lens, an anchor, an audition fixture and a row in every table for a distinction no
  triage rule would act on differently. Widening in the open, with the widening recorded here, is the
  same move D-conceptual-conflation made for `overstated_claim`.
* *Routing scope defects to `conceptual_conflation`.* Rejected: the logic lens never sees the fetched
  pages (an isolation requirement, not an optimization), so it cannot tell that a cited page is about
  a different population. Only the lens holding the page can ask the question.
* *A mechanical check — compare the report's claim scope against the page's.* There is no
  deterministic test for "this meta-analysis found no relationship". Mechanical rules already carry
  the cases that admit them (`triage.mechanical_citation_issues` for a definitive not-found), and
  this is not one.
* *Fixing the fetch-failure defects by improving extraction.* Worth doing and orthogonal. A better
  extractor produces fewer navigation-chrome bodies; it does not stop a critic asserting page content
  from a body it never received, which is the defect recorded here.

**Deliberately not done.**

* *The mechanical triage drop for no-action instructions* the withdrawal finding above would also
  support — recognising an instruction that plainly requires no action and dropping the issue, with a
  count on the triage event. That half is implemented in the companion bibliography-integrity PR,
  which adds the triage surface and the event field; this PR keeps only the prompt-side rule, which
  is the narrower change and the one that belongs with the evidence-lens wording.
* *The per-source sub-context reader* (the D-unbounded-evidence follow-up). The two questions here
  are exactly what it would ask per citation, and it is being designed separately.
* *Source independence / duplicate provenance* — two entries by the same authors counted as
  corroboration, which the same reviews found. A candidate for `one_sided_sourcing`'s meaning,
  not this category's.
* *Temporal currency* — a source predating the run date by years. Writer-side search is its own PR;
  a critic-side staleness rule is a candidate follow-up.
* No change to `WRITER_*` prompts, to the fetch tiers, to the excerpter, or to any configuration
  value. No live A/B in this PR: the measurement is the composition of terminal
  `outstanding_defects` on the next runs on this build, read as
  [run-provenance.md](../run-provenance.md) prescribes.
