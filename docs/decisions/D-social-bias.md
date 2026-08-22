## D-social-bias — social-bias categories on existing lenses, governed by docs/bias.md

The user intent this system serves includes *avoiding social biases with rules defined in the
repo as documentation* — and until this decision, no such rules existed: every "bias" the docs
addressed was sycophancy (fixed by fresh contexts) or model blind spots (fixed by roster
diversity). Neither layer touches bias that is **correlated across the whole roster** — loaded
framing, one-sided source selection, and presuppositions inherited from the question — because
every capable model shares training-corpus and cultural priors, and a sample run
(run-75eb136b9bfb, a politically loaded question) leaned on a single-viewpoint outlet cluster
with **no lens able to raise the objection**.

**Decision.** Three observable-text categories on the *existing* lenses, governed by a new
normative doc, [bias.md](../bias.md):

| category | lens | floor |
|---|---|---|
| `one_sided_sourcing` | evidence | major |
| `loaded_language` | logic | minor (escalation permitted) |
| `unexamined_presupposition` | completeness | major |

Plus symmetric writer-side standards in `WRITER_SYSTEM` (neutral language, surface contested
presuppositions, multi-cluster sourcing on contested questions).

**Why categories, not a fourth lens.** A `social_bias` lens would need its own critic pool,
double-clearance for strong acceptance, and roster staffing — diluting per-lens confirmation and
raising the roster bar for every deployment — while each of the three defects already belongs
naturally to an existing dimension (sourcing → evidence, framing-verdicts → logic, omitted
examination → completeness). If audition fixtures later show the categories underperform inside
shared lenses, a dedicated lens is the recorded upgrade path.

**Floors** (argument in [bias.md](../bias.md)): `one_sided_sourcing` major — observable from the
`## Sources` list, undermines the evidence guarantee the way `uncited_claim` does, and only a
material floor forces revision; not blocking, because unlike a fabricated citation every source
is real. `loaded_language` minor — the most judgment-laden category; a material floor would hand
one noisy critic a per-round forced-revision lever, while the clamp-up-only rule (RC-005) still
lets a critic *propose* major for pervasive framing and have it stick. `unexamined_presupposition`
major — `omitted_counterargument`'s sibling and always resolvable within the report.

**Deliberately excluded from `IN_ARTIFACT_RELATED`:** the three categories' `related_span`
describes a pattern (a source cluster, the question's framing), not a second quotable span —
the same rationale as the citation categories.

**Deferred:** a cross-critic bias-correlation audition report ("do this roster's critics lean
the same way?"). It needs paired mirror fixtures with directional ground truth over multiple
repetitions — a new fixture design, not a new aggregation — and lands only when that corpus
exists. Known residual, accepted: rules catch what they name; a bias the rulebook does not
describe passes through.

**Operational note:** these categories change `critic_user` for all three lenses, so
`audition.prompt_hash()` changes and every cached audition verdict is invalidated by design —
operators re-run `ra audition` after upgrading.
