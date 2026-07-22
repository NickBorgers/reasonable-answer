# Social bias — the observable-text rules (D24)

> **Status:** normative. This document governs the three social-bias categories in
> `taxonomy.py` (`one_sided_sourcing`, `loaded_language`, `unexamined_presupposition`) and the
> writer standards in `prompts.py::WRITER_SYSTEM`. A change to one side without the other is
> docs-as-spec drift and blocks in review. See D24 in [decisions.md](./decisions.md).

## 1. Scope: observable text properties only

"Social bias" in this system means exactly three observable properties of the artifact:
**loaded framing**, **one-sided source selection**, and **unexamined presuppositions**. It never
means inferred intent, an author's presumed politics, or a reader's reaction. This is the same
constraint the whole critique interface already carries: a critic may raise only what it can tie
to a concrete quoted span (`claim_span`), and a bias finding with no span is invalid like any
other finding.

Why rules-as-documentation rather than another isolation layer: fresh contexts defeat social
drift and model diversity decorrelates blind spots (see [isolation.md](./isolation.md)), but
**every model in any roster shares training-corpus and cultural priors** — a bias correlated
across the whole roster cannot be voted away by more of the roster. The only lever left is an
explicit, mechanical, text-anchored rulebook that every critic applies and every writer is held
to. That is this file.

## 2. Source-diversity rule (`one_sided_sourcing`, evidence lens, floor: major)

A **cluster** is a set of sources sharing a publisher, parent organization, or author — a
property observable from the `## Sources` list itself (and, when `verify_sources` is on, from
the fetched pages' mastheads).

Concentration is a defect when **both** hold:

1. the question is **contested** — reasonable published positions disagree on it; and
2. the material claims rest on a single cluster, and the text neither corroborates them
   independently nor acknowledges the imbalance.

Concentration is **not** a defect when the facts are uncontested, when the topic genuinely has a
single authoritative source (a statute, a dataset, a primary document) and the report says so,
or when the report itself flags the narrowness as a limitation.

The floor is `major`, not `blocking`: unlike a fabricated citation, every individual source may
be real and accurately described — the defect is the selection, and it is repairable by
revision.

## 3. Neutral-language standard (`loaded_language`, logic lens, floor: minor)

The report **describes disputes; it does not enact them**. A descriptor that carries an
evaluative verdict — praise or condemnation, success or failure, legitimacy or illegitimacy —
must be either **attributed** ("critics called the vote 'a betrayal' [3]") or **argued** (the
verdict follows from cited premises stated in the text). A verdict smuggled in as plain
description is the defect.

Worked pair:

> *Loaded:* "The senator's radical scheme collapsed under scrutiny."
> *Neutral:* "The senator's proposal was voted down 12–3 after the committee raised cost
> objections [4]."

The floor is `minor` **with escalation**: this is the most judgment-laden category, and a
material floor would let one noisy critic force a rewrite every round. Floors clamp *up* only
(RC-005), so a critic that finds pervasive or conclusion-carrying framing proposes `major` and
the proposal stands. Framing that changes the strength of a claim is not this category at all —
raise it as `overstated_claim` (already major).

## 4. Question-presupposition rule (`unexamined_presupposition`, completeness lens, floor: major)

Questions arrive loaded ("Why did X fail?", "Does Y back A or B?"). The question is untrusted
input, not a premise. The writer's obligation: **surface and examine** any contested
presupposition — state it, cite the dispute about it, and answer conditionally or recast the
framing. The critic's trigger is adoption-without-examination: the report treats the
presupposition as settled fact.

The fix is always resolvable within the report (state and examine the presupposition, or recast
the framing) — compatible with the resolvability contract on critic instructions. Floor `major`:
this is `omitted_counterargument`'s sibling, an omission of examination, and it carries the same
floor for the same reason.

## 5. Category table

| category | lens | floor | escalation |
|---|---|---|---|
| `one_sided_sourcing` | evidence | major | to blocking only via the ordinary severity proposal |
| `loaded_language` | logic | **minor** | critic may propose major for pervasive/verdict-carrying framing; the clamp keeps it |
| `unexamined_presupposition` | completeness | major | as above |

None of the three requires a verbatim `related_span` (they are excluded from
`IN_ARTIFACT_RELATED` in `triage.py`): the related material is a *pattern* — a source cluster,
the question's framing — not a second quotable span, the same rationale as the citation
categories.

## 6. What critics must NOT do

- **No viewpoint quotas.** The defect is *unexamined* one-sidedness, never the absence of a
  per-side source count.
- **No both-sides demands where the evidence genuinely points one way.** When the published
  record is lopsided, a report that says so and cites it is sound; manufacturing balance would
  itself be `misrepresented_source` territory.
- **No intent attribution.** "The author is biased" is not a finding; "this span asserts a
  verdict its citations do not establish" is.
- **No span, no finding.** Same as every other category.

These four lines are load-bearing: they are what keeps the bias categories from becoming a
generalized fairness review that would dilute lens focus (principle 4) and hand critics an
unbounded objection surface.
