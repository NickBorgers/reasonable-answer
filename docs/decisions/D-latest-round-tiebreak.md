## D-latest-round-tiebreak — the best-scoring tie-break ships the latest round, and the spec now says so

**The finding.** `docs/convergence.md` stated that a scoreboard tie between equally-scored artifacts
was broken toward the **earliest** round. The code has broken it toward the **latest** round since
commit 2c54713 (#14, 2026-07-20), and has been asserted that way by
`tests/test_controller.py::test_best_scoring_index_breaks_ties_toward_the_latest_round` since the same
commit. Neither `docs/convergence.md` nor this log was updated when the behavior changed: the spec and
the shipped controller disagreed on which draft a non-`accepted` terminal returns, silently, for four
weeks, until this entry.

**Scope of the record.** `latest_scores_per_artifact` and the tie direction solve distinct problems.
The former applies RC-002 to selection by keeping the scoreboard's last triage of each
`artifact_hash`; it prevents a refuted earlier score for the same bytes from remaining eligible.
`best_scoring_index` then receives one row per artifact, ordered by round, and its `(score, -index)`
key deterministically selects the latest row when distinct artifacts have equal severity totals.
Commit 2c54713 changed both operations, but its stale-triage case establishes the need for the first,
not that refinement is monotonic or that recency is evidence of higher quality between tied artifacts
(QP7).

**The decision.** `docs/convergence.md`'s best-scoring-version rule is corrected to match the code and
its tests: ties go to the latest round. No behavior changes; this decision retroactively records the
shipped deterministic rule and closes the drift between spec and implementation. It does not claim
that latest is intrinsically better or weaken QP7.

**Invariants.** None of the six is in reach. This is a documentation correction only — no code, test,
prompt, or controller rule changes.
