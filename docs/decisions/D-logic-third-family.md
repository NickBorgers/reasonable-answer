## D-logic-third-family — a reused family, not a new one, closes the logic-lens gap

**The gap.** D-minimax-retirement's ordering note stated the cost plainly rather than hiding it:
`logic`'s critic pool was `[mistral-large-3, glm-5.2]`, and `mistral-large-3` also writes, so on
every round it authored, author exclusion thinned the lens to `glm-5.2` alone — one family, below
the two `validate_roster_health` requires for a strong `accepted`. The 2026-08-10/11 survey
(the open item this closes; full record in
[model-evaluation-record-2026-08-10.md](../model-evaluation-record-2026-08-10.md)) auditioned
eight candidates against the shipped logic-lens fixture corpus looking for a genuinely new fourth
family. Seven produced interpretable verdicts, spanning six vendors, and **none graded `fit`**. The
gap stayed open for a month.

**The measurement.** Rather than extend that search for a new vendor, this round auditioned models
already present elsewhere in the roster — `gemma4` (the evidence/completeness critic),
`qwen3.8-27b`, and `nemotron-3-super-120b-a12b` — against the same 22-fixture corpus (hash
`9c248e1d249ad301`, matching the 2026-08-10/11 measurement identity), at the shipped
`repetitions: 3`. Full denominators for all three are recorded as a follow-up section in
[model-evaluation-record-2026-08-10.md](../model-evaluation-record-2026-08-10.md), per that page's
own instruction to extend the record rather than restart the survey. Summary:

| model | control material rate | obvious sensitivity | schema failure rate | verdict |
|---|---:|---:|---:|---|
| `gemma4` | 0.00 (0/24) | 1.00 (3/3) | 0.00 (0/42) | **fit** |
| `qwen3.8-27b` | 1.62 (39/24) | 1.00 (3/3) | 0.00 (0/42) | unfit |
| `nemotron-3-super-120b-a12b` | not interpreted | not interpreted | 0.36 (15/42) | unfit — void |

`qwen3.8-27b` cleared the schema and obvious-tier floors but invents material issues at 1.62 per
sound control, well over the `max_control_material_rate: 1.00` ceiling — a noise profile, not a
detection one. `nemotron-3-super-120b-a12b` failed the schema-failure gate outright (0.36 against
the 0.2 ceiling, per `judge()`'s fixed gate order — model-evaluation.md's "read `schema_failures`
before any judgement metric"), so no sensitivity or noise number was even computed; its verdict is
a statement about serving reliability, not judgment quality. `gemma4` cleared every gate with room:
zero invented material issues across 24 control runs, and perfect recall on the `obvious` tier — the
two hardcoded floors `judge()` will not move regardless of threshold tuning (D-obvious-per-lens).

**This is reuse, not a new family — stated as a cost, not hidden.** QP2 (`config/quality-principles.md`)
holds that a same-family witness added to a pool adds correlated errors, not independence.
`gemma4` was already this roster's evidence and completeness critic before this change; adding it to
logic does not introduce the fourth vendor the closed open item was looking for, and the roster's
per-lens family count (QP2's actual enforcement surface, via `critic_slate`/`lens_statuses`) does not
grow. What it does buy: `roster_limited` on logic goes from "true every round `mistral-large-3`
authors" to "not true on any round", because `gemma4` is critic-only and author exclusion can never
remove it. A month of searching for a new family found nothing that clears the noise gate; a family
already measured safe on two other lenses clearing it on a third is a materially different and much
cheaper claim than "the search that failed to find a new family should keep running before this gap
gets fixed at all."

**Ordering.** Fit-first (D-completeness-pool-noise's rule: the pass acts on position 1's silence).
`mistral-large-3` keeps position 1 — its own `fit` verdict and position are not what this change
measured, and re-litigating them is out of scope here. `gemma4` (`fit`, 0.00 invented) takes
position 2, ahead of `glm-5.2` (`marginal`, 0.75 invented) at position 3. Two consequences, both
intentional: on an ordinary round the front-loaded pair (`review.depth: 2`) becomes
`{mistral-large-3, gemma4}` — the lens's two measured-`fit` critics reading every draft, where
before it was one `fit` and one `marginal` — and `glm-5.2` becomes the rule-8 top-up reserve rather
than always-read. On a round `mistral-large-3` authors, the eligible pair is exactly
`{gemma4, glm-5.2}`, which is the two-family floor this decision exists to restore.

**What this deliberately does not do.** It does not re-measure `mistral-large-3` or `glm-5.2` on
logic, does not touch any audition threshold, and does not close the standing evidence-lens or
completeness-lens open items — no evidence-lens measurement exists for any of the three candidates
audited here, and their logic verdicts do not predict one (the same caveat the 2026-08-10/11 record
states for its own candidates). It also does not claim the search for a genuinely new logic-lens
family should stop; it records that reuse was the cheaper, immediately-available fix for the
specific defect measured (author exclusion emptying the pool), and leaves QP2's fuller restoration —
a fourth, actually-new family — as future work, not as solved.
