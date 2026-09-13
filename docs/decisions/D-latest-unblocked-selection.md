## D-latest-unblocked-selection — ship the latest round among those with the fewest blocking issues

**The finding.** On every non-accepted terminal, `graph._finalize` ships the round minimising
`100·blocking + 10·major + 1·minor` over each artifact's latest triage
(`controller.best_scoring_index`, `controller.latest_scores_per_artifact`; spec:
[convergence.md](../convergence.md), D-latest-round-tiebreak). Fifteen runs finished on the
production instance between 2026-09-10 and 2026-09-13. Every one ran all eight rounds and none was
`accepted`. Their per-round material counts and the round each shipped:

| run | chosen | material by round |
|---|---|---|
| run-076a743a775c | 7 | 16,9,9,14,8,10,6,13 |
| run-188459deba66 | 5 | 25,3,16,15,4,7,6,10 |
| run-1dd853cbbfd0 | 7 | 11,9,7,7,6,3,1,7 |
| run-20e735f62b06 | 1 | 4,13,7,18,7,13,7,17 |
| run-31169ad53140 | 6 | 7,10,10,11,9,8,7,11 |
| run-3fbc01d7c4d6 | 8 | 13,10,16,7,21,3,5,5 |
| run-4783c2d9cb81 | 4 | 5,6,6,4,7,6,11,8 |
| run-5728bdbc1057 | 5 | 22,8,8,13,8,11,6,16 |
| run-80a189d3670d | 8 | 8,8,4,4,4,6,10,3 |
| run-897ca0e6c7a5 | 2 | 11,6,9,6,7,9,11,5 |
| run-b06b18e7df3f | 8 | 16,31,12,15,9,14,9,8 |
| run-c859ff5bf071 | 5 | 7,15,7,12,6,7,9,8 |
| run-e6aba2c94fe8 | 5 | 9,7,11,4,1,2,4,4 |
| run-ebac4175d67f | 7 | 13,10,11,12,9,11,9,11 |
| run-ec7c2e2cf598 | 7 | 11,11,10,10,10,3,2,9 |

Eleven of the fifteen shipped a round other than the last, and eleven shipped a round other than
the one the loop stopped on. Ten independent expert reviews of these reports — one per report,
written without sight of each other — reached the same conclusion about the rule, from different
directions:

- **It selects on critic noise.** run-ec7c2e2cf598: `11,11,10,10,10,3,2,9`, round 7 shipped; round
  8's jump "came overwhelmingly from one critic — mistral-large on the logic lens returned 5, 5, 0,
  9 issues across its four turns". run-1dd853cbbfd0: round 7 scored `material: 1` with glm-5.2 and
  gemma-4 on logic, round 8 — two paragraphs different — scored 7 with mistral-large, which
  "returns 4–6 logic issues on every artifact it sees while glm-5.2 returns 0–1 on the same
  artifacts … the shipped artifact is the one the softest panel happened to draw".
  run-188459deba66: counts "move by a factor of five between adjacent rounds on near-identical
  artifacts".
- **It rewards evasion.** run-c859ff5bf071 shipped round 5, the minimum and "also the most hedged
  round … because hedges are unfalsifiable and therefore unflaggable. The selection rule rewards
  evasion: the cheapest way to reduce the flag count is to assert less."
- **It prefers the least-scrutinised text.** run-ebac4175d67f: rounds 5 and 7 tied at 9 and 7
  shipped; round 8's higher count partly reflects that its critics were the first to read a
  sentence introduced in round 7, so "selecting the minimum-flag round systematically prefers
  artifacts whose newest content has had the *least* scrutiny" — and the shipped round 7 carried an
  un-audited and wrong calculation.
- **It reasons about a different document than the rest of the run.** run-897ca0e6c7a5 shipped
  round 2, while the arbiter had overruled two defects in round 3 that are still open and still
  listed on the shipped round-2 draft: "the two subsystems are reasoning about different
  documents".
- **It is unauditable.** Two reviews independently: "the event log records only the scalar
  `material` count per triage, so the decision is unauditable after the fact"; and, of
  run-31169ad53140, which shipped round 6 at `material: 8` over round 7 at 7, "whatever severity
  weighting produces 'round 6' should be emitted as a per-round score in the audit, or a reviewer
  cannot tell a considered choice from a bug". This is verifiable in the trail as it stands: the
  `triage` events carry `material` and not the `(blocking, major, minor)` tuple the selection
  reads, so the choice of round genuinely cannot be recomputed from an `audit.json`.

The counter-case, noted honestly: run-80a189d3670d shipped round 8, which was both the last round
and the material minimum (`8,8,4,4,4,6,10,3`), and its reviewer's complaint was the opposite one —
that round 7 "made things measurably worse" and nothing prevents a regression. Under the new rule
that run's outcome is unchanged, but the protection the reviewer wanted is not added here either.

These figures come from the operator's `audit.json` trail and from reviews that are not part of
this repository, so as in D-scoped-revision and D-claim-scoped-patch they are the *motivation* and
not the warrant (QP9). The warrant is the mechanism, and it is checkable in the tree: under
`revision.mode: patch` (D-scoped-revision, D-claim-scoped-patch) each round applies the previous
round's fix tasks to a near-identical artifact, so the major and minor counts being compared are
critic judgements over almost the same text. D-claim-scoped-patch's own "what this does not fix"
section already records that variance — "a two-paragraph patch took the material count from 1 to
7" — and it is larger than the between-round difference the ranking is supposed to measure.
Blocking counts are different in kind: `fabricated_citation` is mostly minted mechanically from a
404 (D-notfound-fabrication) rather than judged, and `contradicted_claim` is the rarest logic
category. Low variance, and the one class a reader must never be handed.

**The decision.** Selection becomes: among each artifact's latest triage rows, keep those with the
minimum **blocking** count; among those, ship the **latest** round. Major and minor counts no
longer enter selection. The latest round has absorbed every fix task the run produced and has been
read by every prior pass's findings; a later round's higher major count is, on this evidence, as
likely to be the panel as the prose. This is not "ship the last draft because it is last" — a
round that adds a blocking issue still loses to every earlier round that has none.

Concretely:

- `config.ReviewConfig.selection: Literal["latest_unblocked", "fewest_defects"] = "latest_unblocked"`,
  with both values stated in `config/roster.yaml` so the two rules are A/B-able from configuration,
  the way `revision.mode` is. Deliberately **not** in `Budgets`: `_run_fingerprint` hashes that
  section, and a field there would abandon every paused run at the deploy that shipped it.
- `controller.select_shipped_index(scores, mode)` — pure, total, no I/O — dispatches to the new
  `controller.latest_unblocked_index` or to the existing `controller.best_scoring_index`.
  `best_scoring_index` is untouched, so `fewest_defects` reproduces the previous behaviour exactly
  and its tests keep asserting it.
- `graph._finalize` passes `rt.config.review.selection`. `latest_scores_per_artifact` is unchanged:
  RC-002 still decides which row represents an artifact before any rule ranks the rows.
- The `triage` audit event gains `blocking`, `major` and `minor` alongside `material`, which stays.
  That makes the shipped round recomputable from the trail under either rule — the unauditability
  two reviews found is a property of the log, not of the rule, and it is fixed for both.
- `_finalize`'s "Never ship the last draft just because it is last" comment, and rule 12's note
  string (`freezing best-scoring version` → `freezing the selected version`), now say what is
  actually shipped. No test pinned that note.

**D-latest-round-tiebreak is subsumed, not reversed.** That decision recorded that ties among
equally-scored artifacts go to the latest round. Under `latest_unblocked` the deciding quantity is
narrower, and ties in it still go to the latest round — for the same stated reason, and with the
same disclaimer: recency is not evidence of quality (QP7), it is the deterministic direction the
tie is broken in. Under `fewest_defects` that decision's rule is still in force verbatim.

**Invariants.** None of the six moves. Termination is untouched — selection runs in `_finalize`,
after the controller has already issued a terminal status; no rule, `ControllerInput` field, budget
or generating path changes, so the measure argument in [convergence.md](../convergence.md) holds
unchanged and nothing generates at or after the hard cap. The orchestrator sees nothing new:
`OrchestratorView` is not touched and the new counts go to the audit trail, which no generator
reads. Author exclusion, fail-closed lens validation and the severity floors are not in reach.
QP1 holds and is strengthened rather than weakened: the selection was, and remains, a deterministic
function of bounded categorical counts with no LLM ordinal anywhere near it — this change narrows
which of those counts it reads.

**Why not the alternatives.**

- *Per-critic normalisation of issue rates.* The right answer, and the one two reviews asked for
  directly. It needs a per-critic prior estimated over enough turns to be stable, and the audition
  (D-critic-audition) is where that measurement belongs. Estimating it from the eight turns of a
  single run would put a small-sample LLM-derived rate into a control decision, which is what QP1
  exists to prevent.
- *Persistent-defect identity across rounds (span hash + category), shipping the round with the
  fewest persistent flags.* The strongest proposal in the reviews and the follow-up named below.
  It is a real design — defect identity must survive the rewording a patch performs — and it is
  not a change to a selection rule.
- *Keeping major counts with a smaller weight.* Nothing in the evidence gives a weight; a weight
  chosen to make the observed fifteen runs come out well is fitted to the sample, not measured.
- *Penalising rounds whose changed paragraphs have not yet been critiqued.* run-ebac4175d67f's
  suggestion, and it points the other way from the rest: it would push selection away from the
  latest round, when the latest round is the one that has absorbed every fix.
- *A decisiveness or hedging floor* (run-c859ff5bf071). That is a lens, not a selection rule. It
  would be a new critic obligation with its own prompt, taxonomy category and audition evidence.

**Prompt hashes.** No prompt text changes, so no audition rubric or prompt hash moves and no
cached verdict goes stale.

**Deliberately not done.** No per-critic normalisation and no cross-round defect identity — named
above as the follow-up that would let selection use signal rather than merely refuse the noisiest
of it. No re-scoping of arbiter verdicts to the shipped artifact (run-897ca0e6c7a5's finding):
mostly moot once the latest round ships, and a real fix belongs with adjudication. No regression
guard that re-rolls a revision which raises the count (run-80a189d3670d) — that is a change to the
refine loop, not to selection. No change to `latest_scores_per_artifact`, to any controller rule,
or to the terminal statuses. No live A/B in this PR: `review.selection` exists so that the
comparison can be run, read the way [run-provenance.md](../run-provenance.md) prescribes.
