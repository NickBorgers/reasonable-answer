## D-decisive-quantities — a lens owns arithmetic, magnitude, and the argument that settles the question

**The finding.** A round of expert defect reviews of recent production reports converges on one
gap: no lens owned numbers, and no lens asked whether the argument that settles the question was
present. The categories to express both already existed; nothing told a critic to look. The failure
modes, stated as modes rather than as incidents:

* **A derivation described but not performed.** A report states its method — "divide by X to get
  Y" — and the numbers downstream are the undivided ones. The sentence describing the method is
  frequently *added late, in response to a critic*, without touching the figures it was supposed to
  justify: the prose is patched and the arithmetic is not, so the defect is created by a fix.
* **A unit that changes between premise and result.** A figure lifted from a source in one unit is
  restated in another with no conversion, and the resulting order-of-magnitude error propagates
  through every derived quantity.
* **Two figures for one quantity, sections apart.** A report prints a datapoint that falsifies its
  own derived intensity, in the same document, unreconciled; or a headline states one threshold
  while the body states another several sections away. These are ordinary contradictions, and the
  logic lens did not file them — `related_span` is already checked against the whole artifact
  (`triage.IN_ARTIFACT_RELATED`), not against the cited paragraph, so distance was never a
  restriction in the mechanism, only in how the brief read.
* **The decisive arithmetic simply absent.** A capacity question whose answer is one multiplication;
  a dose question whose answer is one comparison against a published limit the report already
  cites in full. The report discusses the topic at length and never performs the one step.
* **A comparison with no magnitudes.** A comparative question answered in kind-language only, in
  which a counterargument orders of magnitude too small to matter headlines a section, because
  nothing on the page says how big it is relative to the main effect.
* **The consideration that settles it, never stated.** A term common to both sides cancels, or a
  cost is already sunk — facts the report itself supplies — and the report argues its way past the
  point instead of making it.
* **Absence of evidence read as evidence of absence.** A body's "insufficient data to determine" is
  carried into a conclusion as "no effect", and findings raised against that step are overruled
  because no rule names it.
* **One reading of an ambiguous question, answered silently.** A question turning on a causal
  boundary or an undefined tier admits more than one reading; the report answers one and never says
  which, so a reader cannot tell whether the answer is to their question.

Those reviews motivate this decision; they are not its warrant (QP9, and audit content stays in the
audit trail). The warrant is the mechanism, checkable in code and reproducible from the synthetic
fixtures in `tests/`: the categories, the floors and the span validation are all unchanged, and what
changes is the text of two lens briefs, four category meanings and four writer standards, each
pinned by a test.

**The decision.** Six readings of four existing categories, stated in the open — the same discipline
D-conceptual-conflation used to widen `overstated_claim`: each trigger ships with its narrowing, and
none may demand a specific document as the only fix.

*Logic lens* (`taxonomy.LENS_BRIEF[Lens.LOGIC]`, `prompts._CATEGORY_MEANING`):

1. **Arithmetic and units** — reproduce a stated derivation from the inputs the report states; a
   result that does not follow, a unit that changes between premise and result, or a scenario label
   that does not match its range is `invalid_inference`, with the recomputed value in the rationale.
   Narrowed: a figure stated to fewer significant figures than its inputs is not a defect, and
   an unstated input is `overstated_claim`
   territory, not a defect of the derivation.
2. **Distant contradictions are expected** — `contradicted_claim` however many sections apart the
   passages sit, with the second passage in `related_span`; two figures for the same quantity that
   differ by more than their stated precision are a contradiction wherever they appear.
3. **Absence of evidence** — "insufficient data" read as "no effect" is `invalid_inference`.
   Narrowed: a report that says the evidence is insufficient and concludes accordingly is correct.

*Completeness lens* (`taxonomy.LENS_BRIEF[Lens.COMPLETENESS]`, `prompts._CATEGORY_MEANING`), framed
by one sentence — the question is what the asker would do with this answer and which input to that
decision is missing, not whether every item on a topic list is covered — the observed failure being
a lens that files a missing-topic finding while the distinction the asker would actually act on
stays absent:

4. **Magnitude** — a comparative or "how much" question answered with no magnitude on either side is
   `incomplete_answer`, **only where** the report's own cited material or ordinary arithmetic from
   facts it states would supply one. An order of magnitude or a break-even is complete; a question
   about kind, mechanism or character needs no magnitude (the same carve-out as
   D-conceptual-conflation's).
5. **The decisive consideration** — where one argument settles the comparison (a common term
   cancels, a cost is sunk, a dose sits against a published limit, one option repeats a production
   cycle the other does not) and the report argues around it, that is `incomplete_answer`, with the
   consideration named in the rationale. Narrowed to considerations that follow from what the report
   itself states or cites, so the fix is in-report.
6. **Readings of the question** — a question whose wording admits more than one reading, answered
   under one without saying which, is `unexamined_presupposition`; the fix is to state the reading
   and to say where the answer would flip.

*Writer* (`prompts.WRITER_SYSTEM`, four bullets in the existing style): state the argument that
settles the question, and show the arithmetic with its inputs and units where arithmetic settles it
— a derivation described is a derivation to be performed; for a comparison give both magnitudes, the
break-even, and a counterargument's size relative to the main effect; a heading claims no more than
its section supports; say which reading of an ambiguous question you answer.

All six are normative in
[convergence.md](../convergence.md#arithmetic-magnitude-and-the-decisive-consideration-d-decisive-quantities),
and the taxonomy table's four meaning cells were updated with them.

**Headings: the writer standard ships, the critic trigger does not.** The spec for this change
included a fourth logic rule — a heading that asserts more than its section supports is in scope,
quoted as `claim_span` — a real failure mode, since a section heading that asserts what the prose
beneath it disclaims is outside every lens's reach today. It is not shipped, because it would be a
rule to fail the lens.
`report.parse` puts heading text in `Structure.section_titles` and in no `Paragraph`, so a heading
appears neither in the cited paragraph nor in `Structure.full_text`, and `triage._require_quote`
rejects a `claim_span` drawn from one — through both repair attempts, taking the whole lens down
fail-closed. `tests/test_taxonomy.py::test_heading_text_is_not_quotable_so_no_heading_trigger_ships`
pins the observation, so the day headings become quotable the reason for the omission is visible.
The writer-side standard has no such constraint and ships.

**Invariants: none move.** No category is added or removed, so `LENS_CATEGORIES` and `SEVERITY_FLOOR`
are byte-identical and severity floors still clamp up only. `triage.validate_issue` and
`IN_ARTIFACT_RELATED` are untouched, so lens validation still fails closed on a span that is not
really there — rule 2 relies on existing whole-artifact anchoring rather than relaxing it. Author
exclusion, the blind orchestrator, termination and the untrusted-text boundary are not in the diff:
this change is prompt text and the documents that govern it. Nothing enters a generator context that
did not enter it before, and the report and question stay inside `DATA_FENCE`.

**Why not the alternatives.**

* *A new `bad_arithmetic` category.* Rejected. A derivation that does not yield its own number is
  precisely a conclusion that does not follow from its stated premises, which is what
  `invalid_inference` means and what it is already floored for. A new category costs a floor
  decision, a `LENS_CATEGORIES` entry, an audition fixture pair and a rubric-hash break, and buys a
  second name for one defect — which is how one error ends up filed twice, at its floor, twice, and
  double-counted in the material-issue tally the controller uses to pick the shipped round.
* *A dedicated numeric-consistency pass.* Rejected here, named below — it is a fourth critic shape,
  not a prompt change.
* *Writer standards only.* Rejected, for the reason D-conceptual-conflation gave: a standard no lens
  can raise is not detectable, and the system's claim is that no eligible reviewer finds a material
  defect, not that the writer was told not to make one. The writer bullets ship as the symmetric
  half, not as the whole fix.
* *Making magnitude unconditional.* Rejected. "Every comparative question owes a number" is the
  noise direction the audition measures as invented issues, and it is unsatisfiable under
  `search.enabled: false`. The trigger fires only where the report's own cited material or ordinary
  arithmetic on its own stated facts would supply the magnitude — which also keeps the fix in-report.

**Audition impact.** `prompt_hash` changes (both lens briefs and four category meanings), so cached
audition verdicts go stale and `audition.enforce` reads *not audited* until `ra audition` is re-run.
`rubric_hash` does **not** change: it hashes `LENS_CATEGORIES` and `SEVERITY_FLOOR`, and neither
moved. No new fixtures ship with this change, which is the honest statement of what it is —
widened triggers within measured categories, whose false-positive direction the existing paired
controls (`control-base-open-source-01` in particular, a qualitative report that declines a
prevalence claim and says why) already measure on every lens.

**Deliberately not done.**

* **A dedicated numeric-consistency pass** — extract every quantity and unit in the report, check
  the set for mutual consistency and recompute each stated derivation mechanically. That is a fourth
  critic shape with its own contract, its own failure mode and its own roster staffing, not a
  widening of an existing lens. It is the right home for the "two figures for the same quantity"
  check if the prompt-level version under-fires.
* **Retrieval for the completeness lens**, so it could file "the report omits X that the literature
  has". That widens a critic context to external text and is an isolation decision (what may enter
  which context, and on whose authority), not a prompt one. Every trigger here is deliberately
  bounded to what the report already states or cites, so the fix stays in-report under
  `search.enabled: false`.
* **The heading trigger for critics**, above: it needs `report.parse` to make heading text quotable
  — probably a synthetic paragraph or a heading-aware `claim_span` scope in `validate_issue` — which
  changes the locus contract and belongs in its own decision.
* **No floor change and no new fixtures.** Both were considered and neither is warranted by a
  widening that adds no category.
