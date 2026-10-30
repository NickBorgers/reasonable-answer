## D-audition-retrieval-variant — the audition can review a fixture as a run whose writers search, and hashes that prompt when it does

**The gap.** D-verdict-not-restatement added a completeness-lens trigger that is sent only when the
run's writers hold a search tool: `prompts.critic_user(..., retrieval=True)` appends
`prompts.VERDICT_TRIGGER`, and `critique.critique_once` passes the flag through. `ra audition` could
not reach it. `audition.run_assignment` called `critique_once` without `retrieval`, so every
audition call was reviewed under the base prompt, and no fixture asked for verdicts on a named
document's claims. `audition.prompt_hash` covered only the base surface, so an edit to the trigger
text would have left every cached verdict in place. That was harmless while nothing measured the
trigger, and it would stop being harmless the moment something did.

**The decision.** Three parts.

1. **A per-fixture manifest field, `retrieval`, default `false`.** `run_assignment` passes
   `retrieval=fixture.retrieval` to `critique_once`. It is a property of the fixture, not of the
   audition run, because the trigger is a reading of one question shape: a corpus where every
   fixture is reviewed with the trigger would test every other fixture under a prompt it was not
   written for. The field is allowed on a control. It names the prompt surface a fixture is read
   under, not a lens it belongs to, so it asserts none of the scope D-control-soundness forbids a
   control to declare, and `for_lens` still hands controls to every lens. On the logic and evidence
   lenses the variant prompt is byte-identical to the base one, so a retrieval control measures
   those lenses exactly as any other control does.

2. **`prompt_hash(*, retrieval=False)`.** With `retrieval` off the digest is computed exactly as it
   was before this decision, byte for byte, and a test recomputes the old algorithm to hold that.
   With it on, the tag `AUDITION_RETRIEVAL_VARIANT` and the variant prompt of every lens are hashed
   after the base surface, so an edit to `VERDICT_TRIGGER` changes the identity. Callers pass the
   corpus's own flag, `FixtureSet.retrieval` (true when any fixture uses the variant): `ra audition`,
   the `ra doctor` cache cells and `cached_judgements` (and through it the `enforce` gate) all do.
   This is D-audition-source-mode's criterion applied once more: a surface the harness exercises
   belongs in the identity, and one it never reaches does not. A corpus with no retrieval fixture,
   such as a `--fixtures` corpus written before this change, keeps its previous `prompt_hash`. If a
   writer and a reader ever disagreed on the flag, they would compute different hashes, and the entry
   would read *not audited*, never a pass.

3. **Three fixtures on a fictional subject.** The subject is an unnamed city, its housing authority
   and a tenants' association's open letter. No real person, organisation or dispute is named, and
   the run that motivated D-verdict-not-restatement is not reproduced.
   - `incomplete-answer-02` (completeness, `incomplete_answer`, `retrieval: true`): the question asks
     which of the letter's allegations are backed up by facts, and the report restates each one as the
     letter's, adding that no source confirming or refuting it is identified. The defect is graded
     `anywhere`, for the reason `incomplete-answer-01` is (D-absence-anchor): the missing verdict has
     no span of its own, and the restating paragraphs and the Conclusion are all present text a critic
     may honestly quote. A test checks that a span quoted from the restating paragraph at the planted
     locus passes `triage.validate_issue`.
   - `control-verdict-given-01` (`retrieval: true`): the same question, answered with a verdict per
     allegation. One allegation is answered by naming what was searched and saying that nothing found
     confirms or rules it out. Another is a motive claim that gets no verdict. Those are the
     trigger's two narrowings, so a critic that ignores them scores as noise.
   - `control-no-verdict-asked-01` (`retrieval: true`): an explanatory question about the same
     authority, which asks for no verdicts on any document's claims. The trigger's last line says
     not to raise it there.

   **The planted fixture is `moderate`, not `obvious`.** Both fail-closed sensitivity gates in
   `judge` count obvious-tier defects only. Until the trigger has been measured on rostered critics,
   a fixture for it should rank them, and it should not be able to make one `unfit`. Promoting it is a
   later decision, and that decision needs a measurement.

**Tension with D-audition-source-integrity, stated rather than hidden.** That decision holds cited
publications to "resolves at a stable locator". A fictional subject cannot meet that bar. The new
fixtures follow the precedent of `control-base-paid-leave-01` and `conceptual-conflation-01`, which
cite an unnamed jurisdiction's institutional records with no locator. The alternative was a real
dispute, which would have put a real party's allegations into a public corpus as test material, and
that was rejected. The remaining risk is that an evidence critic files `fabricated_citation` against
an unlocatable record on a control and is scored as noise. That risk already exists for the
paid-leave pair. This decision adds two more controls that carry it.

**What happens to cached verdicts.** Adding three fixtures changes `corpus_hash`, and the shipped
corpus now exercises the variant, so its `prompt_hash` changes too. Every cached verdict for the
shipped corpus therefore reads *not audited* until `ra audition` is re-run. Either change alone would
cause that, as every corpus addition already does (D-audition-rubric-identity,
D-audition-source-integrity). With the shipped `audition.enforce: false` a stale cache warns and does
not block. The prompt-hash change also invalidates logic and evidence verdicts, whose prompts did not
change. That breadth is not new: `prompt_hash` has always been one identity across all three lenses,
and splitting it per lens is out of scope here.

**What this does not establish.** The rig can now measure whether a critic raises the verdict
finding where it applies and withholds it where the narrowings or the question shape say to. This
decision ships no such measurement and makes no claim about any model's detection or false-positive
rate on the trigger. The offline tests show that the fixtures load, that the field reaches the
prompt, that the hash follows the trigger text, and that a scripted critic following the trigger
scores as intended. They do not show what a real critic does. One planted fixture and two controls
make a narrow sample, and the fixtures are not a minimal pair: a verdict and a restatement differ in
every paragraph that addresses a claim.

**Invariants.** Untouched. Author exclusion, the blind orchestrator, fail-closed lenses, severity
floors, termination and the untrusted-text boundary are all unaffected. No category, floor, grading
rule, threshold or `RUBRIC_VERSION` changes. The production prompt is unchanged: the audition now
sends a prompt production already sends whenever `Runtime.search_enabled`.
