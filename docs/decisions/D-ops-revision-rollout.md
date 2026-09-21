## D-ops-revision-rollout — the shipped roster revises by operations

**What changes.** One line of `config/roster.yaml`: `revision.mode: ops`. The code default stays
`patch` (`config.RevisionConfig`), so a deployment that omits the block, or that mounts its own
roster without this line, is unchanged. Everything else in the `revision:` block — `scope_check: warn`,
the census-gated repair settings — is as D-census-gated-repair left it.

**Why now, and why here rather than in D-ops-revision.** D-ops-revision shipped the mechanism off by
default and named two conditions for the flip: the splice's guarantees pinned by tests, and a live
comparison on one build with `revision_mode` as the bucketing field. The first is met — the amendment
of 2026-09-21 closed the four gaps the review found, and `tests/test_ops.py` and `tests/test_graph.py`
pin each guarantee. The second is the operator's to read from their own runs, and it is why this is a
roster decision and not a code one: the observation that motivates it is private (QP9), the flip is
reversible by editing one line back, and the audit record says which mode every round ran under
(`revision_mode` on `generate`, and the `revision` block on `startup` since the amendment), so a
comparison can be made or re-made from `audit.json` at any time without a checkout.

**What the flip changes for a running deployment, stated so it can be checked.**

- A revision's `generate` event carries the `ops_*` counts; a repair turn's carries `repair_ops_*`.
  `revision_mode` reads `ops` on revisions and still `rewrite` on a rule-13 rewrite; the first draft
  and a polish pass are whole documents as before.
- `generate_failed` may carry `failure_class: malformed_ops`. It costs one of `writer_attempts`, as
  `empty_report` does, and the next attempt goes to the next pool member.
- `dangling_markers` on a revision cannot rise through a Sources operation: the splice refuses one
  that would orphan an entry number. It can still rise through a body operation that cites a number
  with no entry, which is D-bibliography-integrity's finding, unchanged.
- Gate 2 of the census-gated repair still applies; under ops its repair turn returns operations and
  carries the block format.

**Rollback.** `revision.mode: patch` in the roster and a restart between runs. Nothing else moves, and
`WRITER_PATCH_CLOSE` is byte-identical to what it was before D-ops-revision, pinned by hash.

**Invariants touched: none.** This is configuration selecting a mode D-ops-revision already argued
touches none; the argument is there and is not repeated here.

**Deliberately not done.** No change to the code default, for the QP9 reason above. No change to the
repair settings or to `scope_check`. No `WRITER_SYSTEM` addendum for ops mode; `malformed_ops` on
`generate_failed` is the number to read before deciding one is needed.

Cites D-ops-revision (the mechanism and the conditions for this flip), D-census-gated-repair (the
repair settings this leaves alone), D-scoped-revision (the mode this replaces in the shipped roster),
D-writer-failure-class (`malformed_ops`), D-bibliography-integrity (the body-side dangling marker this
does not refuse).
