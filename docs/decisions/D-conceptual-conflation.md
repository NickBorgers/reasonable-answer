## D-conceptual-conflation — a narrow `conceptual_conflation` logic category, and empirical anchors as an explicit widening of `overstated_claim`

**The problem.** The taxonomy had no name for a report that slides between materially distinct
propositions while keeping one label on them. The motivating report moved between formal policy
eligibility, differential implementation and utilization, and downstream cultural reinforcement;
between household form, access to suburban wealth-building, and representation in a promoted ideal;
and it grouped populations whose exclusion ran through different mechanisms. Every sentence was
individually defensible and every claim was cited, so `uncited_claim`, `misrepresented_source` and
`contradicted_claim` all had nothing to say. `invalid_inference` was the nearest fit and a poor one:
the inference is valid *given* the substitution, and a critic that files it there names the
conclusion rather than the step that produced it, so the fix task tells a writer to fix the wrong
sentence.

The same report made scale, prevalence and change claims through thematic assertion — sources that
described a phenomenon vividly and measured nothing about it. That defect also had no name, and it
is not the same defect: conflation is about *which* proposition is being supported, anchoring is
about *how much* the support licenses.

### Decision 1 — `conceptual_conflation`, logic lens, floor `major`

Two materially distinct concepts, mechanisms, units or populations are treated as interchangeable,
**and** the substitution carries a load-bearing inference or conclusion. Both halves are required.
The three distinctions named in the writer and critic rules — formal rule vs. mediated mechanism
vs. observed outcome; measured units vs. claimed population; heterogeneous mechanisms behind a
shared outcome — and the four exclusions (terminology preference, subgroup quotas, a distinction
that makes no difference because one mechanism or one body of evidence genuinely covers both, and
an aggregation the report draws and defends) are normative in
[convergence.md](../convergence.md#conceptual-conflation-and-anchors-for-empirical-scope-claims-d-conceptual-conflation).

**Why the logic lens rather than a fourth lens, or completeness.** The same reasoning D-social-bias
recorded: a new lens needs its own critic pool, its own double-clearance for strong acceptance, and
its own roster staffing, and this defect belongs to an existing dimension — it is a defect in how
the argument moves, which is what the logic lens reads. Completeness is the wrong home because the
defect is not an absence: the report says something, and what it says is a substitution.

**Floor `major`.** `invalid_inference`'s sibling and floored with it. The substitution is the step
the argument turns on, and only a material floor forces the revision. Not `blocking`: unlike a
contradiction, nothing in the report is thereby shown false, and the resolution — draw the
distinction, or restrict the claim to the concept the support covers — is always available inside
the report.

**`related_span` is verbatim-anchored** (added to `triage.IN_ARTIFACT_RELATED`), unlike the three
bias categories. Their related material is a *pattern* — a source cluster, the question's framing —
with no second span to quote. Both poles of a substitution are passages the report contains, which
is `invalid_inference`'s premise/conclusion shape. The field stays optional, so a single sentence
that fuses the two concepts with no second passage anywhere is still reportable, and this narrows
what a critic may forward to a writer rather than widening it.

### Decision 2 — a missing empirical anchor is `overstated_claim`, widened in the open

The issue asked for this to be settled explicitly rather than absorbed silently, and the three
candidates were weighed:

* **A new evidence category.** *Rejected.* The defect survives a perfect citation — a real source,
  accurately described, that characterizes a phenomenon and measures nothing about it — so it is
  not a sourcing failure, and the evidence lens would be raising something its own guarantee does
  not concern. Worse, an evidence category whose fix is "produce a figure" is unsatisfiable under
  `search.enabled: false`, where the writer has no retrieval at all, and would collide with the
  resolvability contract every critic instruction carries.
* **A report-mode (writer-side) requirement only.** *Rejected as sufficient, adopted as a
  component.* A standard no lens can raise is not detectable, and the whole design's claim is that
  no eligible reviewer can find a material defect — not that writers were told not to make one. The
  writer standards ship (below), and a critic can raise the failure.
* **`overstated_claim`, widened explicitly.** *Adopted.* A claim that turns on magnitude,
  prevalence, timing or change, resting on a thematic assertion rather than a concrete figure or a
  source that states it, *is* a claim stronger than its support. The widening is stated in
  `_CATEGORY_MEANING`, in the logic lens brief, in convergence.md and here — which is what
  distinguishes it from the drift the issue warned against. No floor change: `overstated_claim` was
  already `major`.

Two narrowings are part of the decision, not commentary: a claim about **kind, mechanism or
character** turns on none of the four and owes no anchor, and the instruction may never demand a
specific dataset or document as the only acceptable fix — qualifying the claim to what the support
establishes is always a complete resolution.

### Decision 3 — the floor ships with the measurement, because a minor floor here is unmeasurable

The issue asked to measure sensitivity and control noise **before** enabling a material floor. That
sequencing is not available in this harness, and saying so is part of the decision rather than a
deviation from it. `_check_planted_floor_is_material` (D-minor-floor-fixtures) refuses to load a
fixture planting a category floored below `major`, because every detection credit in `grade`
requires post-clamp materiality — a critic reporting a minor-floor category at its own floor scores
as blind. So a `conceptual_conflation` shipped at `minor` could not enter the corpus at all, and
would land in exactly the un-auditable state the tail of this file already records as an open item
for `loaded_language`: a category in production that the audition cannot see.

The measurement therefore ships **in the same change as the floor**, which is the closest faithful
reading of the request:

* `conceptual-conflation-01` (logic, moderate) plants the substitution, paired with
  `control-base-paid-leave-01` — the identical artifact with one paragraph rewritten to draw the
  same distinctions correctly. The pair is what separates "detects the conflation" from "distrusts
  reports about paid leave".
* `overstated-claim-03` (logic, moderate) plants the unanchored scope claim, paired with
  `control-base-open-source-01`, the **qualitative-evidence control**: a report built on interview
  and account evidence that makes claims about kind, declines the prevalence claim explicitly, and
  says why. A critic that demands a figure there is inventing a material issue.
* Both controls reach **every** lens (`for_lens`), so the noise direction is measured on all three,
  and `control_material_rate` / the never-clean gate are what would catch a widened
  `overstated_claim` reading as "quantify everything".

`prompt_hash` changes (the logic lens brief and two category meanings) and `rubric_hash` changes
(`LENS_CATEGORIES` and `SEVERITY_FLOOR` are hashed as data), so **every cached audition verdict is
invalidated by design** — operators re-run `ra audition` after upgrading, and `audition.enforce`
reads *not audited* until they do. The corpus grows from 17 fixtures to 21 (13 planted, 8 controls);
per-control leverage on `control_material_rate` falls from 0.167 to 0.125, and the aggregate rises
from 29 fixture-runs to 37 (12 evidence, 14 logic, 11 completeness). Two of the eight added runs are
this decision's planted fixtures on the logic lens; the other six are the two new controls, which
every lens owes. Corpus-class confounds hold:
the length spread stays 1.348x with each class's median inside the other's range, and every new
artifact carries exactly five sources like the rest.

`test_all_repetitions_failing_one_fixture_leaves_that_fixture_uncovered` is re-derived rather than
re-tuned: two more controls change the per-lens fixture count, so its `repetitions` moves to 5 and
its failure budget is computed from `max_schema_failure_rate` instead of hardcoded. Five is chosen
because `owed x repetitions / 5` is a whole number for *every* corpus size when `repetitions` is a
multiple of five, so the next fixture addition does not silently stop exercising the boundary; the
exact-rate assertion is what would catch it if it did.

**Writer standards** are symmetric with the critic categories, in `WRITER_SYSTEM`: keep a formal
rule, its implementing mechanism and the observed outcome as three separately supported claims;
keep a finding attached to the units it was measured on rather than restating it about a wider
group; name the difference, or narrow the claim, where a claim covers groups reaching one outcome
by different mechanisms; and give a figure or a source, or qualify the claim, where a claim turns
on magnitude, prevalence, timing or change — while not manufacturing a number for a claim about
kind.

**Known residual, accepted.** Same shape as D-social-bias's: the rules catch what they name. A
substitution between two things the critic does not recognize as distinct passes through, and the
`load-bearing` half of the trigger is a judgement no mechanical check can make. The exclusions bound
the false-positive direction and the controls measure it; nothing bounds the false-negative
direction except the critic's own reading, which is what the audition's sensitivity rate is for.

**Deliberately not done.** No new `docs/` page: the rules live in convergence.md, beside the
taxonomy table they govern, rather than in a `bias.md`-style file of their own. No change to the
controller, `OrchestratorView`, the decision table, or any floor other than the new category's own.
No `tier: obvious` fixture for either new plant — both shipped fixtures are moderate by
construction, and an obvious-tier fixture gates `judge` fail-closed, which is not a claim this
measurement has earned yet.
