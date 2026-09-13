## D-latest-unblocked-selection — a second, opt-in selection rule: the latest round among those with the fewest blocking issues

**The finding.** On every non-accepted terminal, `graph._finalize` ships the round minimising
`100·blocking + 10·major + 1·minor` over each artifact's latest triage
(`controller.best_scoring_index`, `controller.latest_scores_per_artifact`; spec:
[convergence.md](../convergence.md), D-latest-round-tiebreak). Two properties of that rule are
checkable in the tree and are the subject of this decision.

*It ranks on counts the revision loop cannot hold still.* Under `revision.mode: patch`
(D-scoped-revision, D-claim-scoped-patch) each round applies the previous round's fix tasks to a
near-identical artifact, so the major and minor counts being compared are critic judgements over
almost the same text. D-claim-scoped-patch's own "what this does not fix" section records that
those judgements vary from pass to pass by more than a patch changes — "a two-paragraph patch took
the material count from 1 to 7" — and names slate rotation as the cause: the logic slate alternates
with the writer, and one critic files several times as many issues as its alternates on the same
drafts. A rule that ranks rounds on those counts therefore ranks, in part, on which critics drew
the round. Blocking counts are different in kind: `fabricated_citation` is mostly minted
mechanically from a definitive not-found (D-notfound-fabrication) rather than judged, and
`contradicted_claim` is the rarest logic category. They are the one class a reader must never be
handed, and the least noisy.

*The choice cannot be recomputed from the audit trail.* The `triage` event carried `material` and
not the `(blocking, major, minor)` tuple the selection reads, so which round shipped, and why, was
not reconstructible from an `audit.json`.

The operator's reading of their own recent runs — that the weighted rule was shipping the round
that drew the softest panel, and that the cheapest way to lower a major count under it was to
assert less — is what prompted this decision. Those observations live outside the repository and
cannot be cited (QP9), which is why they motivate an *option* here and do not move the default.

**The decision.** Selection becomes configurable, with the existing rule as the code default and a
second rule available:

- `config.ReviewConfig.selection: Literal["latest_unblocked", "fewest_defects"] = "fewest_defects"`.
  `fewest_defects` is the previous rule, byte for byte. `latest_unblocked` keeps, among each
  artifact's latest triage rows, those with the minimum **blocking** count, and ships the
  **latest** of them; major and minor counts do not enter it. This is not "ship the last draft
  because it is last" — a round that adds a blocking issue still loses to every earlier round that
  has none. The field sits under `review`, deliberately **not** in `Budgets`: `_run_fingerprint`
  hashes that section, and a field there would abandon every paused run at the deploy that
  shipped it.
- The shipped `config/roster.yaml` sets `review.selection: latest_unblocked`, stated there as the
  operator's deployment posture with the reasoning above — the same shape as `search.enabled`,
  where the code default is off and the shipped roster opts in (D-retrieval-opt-in). A deployment
  that wants the weighted rule leaves the field unset.
- `controller.select_shipped_index(scores, mode)` — pure, total, no I/O — dispatches to the new
  `controller.latest_unblocked_index` or to the existing `controller.best_scoring_index`.
  `best_scoring_index` is untouched, so `fewest_defects` reproduces the previous behaviour exactly
  and its tests keep asserting it.
- `graph._finalize` passes `rt.config.review.selection`. `latest_scores_per_artifact` is unchanged:
  RC-002 still decides which row represents an artifact before any rule ranks the rows.
- The `triage` audit event gains `blocking`, `major` and `minor` alongside `material`, which stays.
  That makes the shipped round recomputable from the trail under either rule; it is a fix for both.
- `_finalize`'s "Never ship the last draft just because it is last" comment, and rule 12's note
  string (`freezing best-scoring version` → `freezing the selected version`), now say what is
  actually shipped. No test pinned that note.

**Why the default does not move.** QP9 requires an empirical claim in `docs/` to carry a citation
that supports it as stated. The comparative claim behind `latest_unblocked` — that major and minor
variance across rounds exceeds the between-round difference the ranking is meant to measure, so
that discarding those counts ships a better draft — is supported inside the repository only by the
mechanism argument and by one recorded observation in D-claim-scoped-patch. That is enough to
justify offering the rule and to explain why an operator might choose it; it is not a warrant for
changing what every deployment does. The register's own precedent is `search.enabled` and
`sources.*`: the code default is the conservative behaviour, and the shipped roster records the
operator's choice as a choice.

**D-latest-round-tiebreak is kept, not reversed.** That decision recorded that ties among
equally-scored artifacts go to the latest round. Under `latest_unblocked` the deciding quantity is
narrower, and ties in it still go to the latest round — for the same stated reason, and with the
same disclaimer: recency is not evidence of quality (QP7), it is the deterministic direction the
tie is broken in. Under `fewest_defects` that decision's rule is in force verbatim.

**Invariants.** None of the six moves. Termination is untouched — selection runs in `_finalize`,
after the controller has already issued a terminal status; no rule, `ControllerInput` field, budget
or generating path changes, so the measure argument in [convergence.md](../convergence.md) holds
unchanged and nothing generates at or after the hard cap. The orchestrator sees nothing new:
`OrchestratorView` is not touched and the new counts go to the audit trail, which no generator
reads. Author exclusion, fail-closed lens validation and the severity floors are not in reach. QP1
holds under either rule: the selection is a deterministic function of bounded categorical counts
with no LLM ordinal anywhere near it.

**Why not the alternatives.**

- *Per-critic normalisation of issue rates.* The right answer in the long run. It needs a
  per-critic prior estimated over enough turns to be stable, and the audition (D-critic-audition)
  is where that measurement belongs. Estimating it from the eight turns of a single run would put a
  small-sample LLM-derived rate into a control decision, which is what QP1 exists to prevent.
- *Persistent-defect identity across rounds (span hash + category), shipping the round with the
  fewest persistent flags.* A real design — defect identity must survive the rewording a patch
  performs — and not a change to a selection rule. Named as the follow-up.
- *Keeping major counts with a smaller weight.* Nothing available gives a weight; one chosen to
  make a particular set of runs come out well is fitted to the sample, not measured.
- *A decisiveness or hedging floor.* That is a lens, not a selection rule; it would be a new critic
  obligation with its own prompt, taxonomy category and audition evidence.

**Prompt hashes.** No prompt text changes, so no audition rubric or prompt hash moves and no
cached verdict goes stale.

**Deliberately not done.** No per-critic normalisation and no cross-round defect identity — named
above as the follow-up that would let selection use signal rather than merely refuse the noisiest
of it. No re-scoping of arbiter verdicts to the shipped artifact — mostly moot for a deployment
that ships the latest round, and a real fix belongs with adjudication. No regression guard that
re-rolls a revision which raises the count — a change to the refine loop, not to selection. No
change to `latest_scores_per_artifact`, to any controller rule, or to the terminal statuses. No
live A/B in this PR: `review.selection` exists so that the comparison can be run, read the way
[run-provenance.md](../run-provenance.md) prescribes.
