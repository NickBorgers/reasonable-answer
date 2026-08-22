## D-answer-obligations — every explicit question clause is material, and a substitute objection is not a counterargument

**Context.** The closed taxonomy could not name a fluent report that answers only one explicit
question clause or substitutes an adjacent, easier question. Such a report can satisfy the section
template and reach convergence even though a literal part of the user's question remains unanswered.

The audition corpus exposed a related specification mismatch. After D-fixture-report-shape,
`omitted-counterargument-01` deliberately contains a substantial `## The strongest counterargument`
section aimed at cordon-boundary effects while omitting the load-bearing distributional objection.
Its manifest correctly calls that the weakened substitute forbidden by the report template, but the
taxonomy still defined `omitted_counterargument` only as an opposing view being absent. The fixture
was testing a stronger and more useful rule than the production critic had been given.

**Decision.** Add `incomplete_answer` to the completeness lens with a mechanical `major` floor. It
means that an explicit, material part of the question is unanswered, or that the report answers an
adjacent question in its place. The writer must treat every explicit part as an answer obligation:
answer each in the conclusion and support each in the body. A question about change or comparison
requires the baseline and contrast needed to make the answer intelligible.

This category is deliberately literal. It does not license a critic to infer an unstated goal,
invent a "question behind the question," demand an optional angle, or choose arbitrary additional
depth. Those remain outside the report's obligations unless the question states them. A critic
anchors the issue to the partial conclusion or closest present passage and puts the missing explicit
obligation in `rationale`; span validation is unchanged.

Broaden `omitted_counterargument` without adding a second counterargument category. It now also
covers a purported opposing case that substitutes an easier adjacent objection and therefore does
not challenge a load-bearing conclusion. The critic anchors either to that weak substitute or to the
claim the absent view bears on, and puts the stronger missing case in `instruction`. A section heading
never earns completeness by itself.

**Measurement.** Add an obvious completeness fixture as a question-level matched pair. Its artifact
is byte-identical to the sound Dust Bowl control; only its question adds a second explicit obligation
about agricultural unionization, which the report never addresses. The pair therefore matches on
length, structure, citations, topic, and prose quality. The new fixture intentionally increases the
full-audition cost, and the corpus and prompt/rubric hashes invalidate old verdicts rather than
pretending the changed measurement is comparable.

**Invariants and limits.** Author exclusion, the blind orchestrator, fail-closed lens validation,
upward-only severity clamping, termination, and the untrusted-text boundary are unchanged. The new
category flows through the existing closed enum, per-lens allowlist, category-count map, and material
total. The QP1/QP5/QP8 application is recorded in `quality-principles.md`: the category has a
mechanical floor, changes no cross-context traffic, and is measured by the existing deterministic
audition aggregation and rubric-identity boundary. This decision does not add more critics per lens
(#135), conceptual-conflation checks (#136), writer-visible retrieved sources or claim-level
traceability (#137), or observed verification coverage in exported reports (#138); those are
separate changes with separate costs and failure modes.
