## D-fixer-grounded-judgment — the cold review fixer exercises grounded judgment, not a mechanical checklist

*(D-run-date-grounding is allocated to run-scoped date grounding, landed separately.)*

The cold fixer's original gate was mechanical by design: a fix had to name a file and line,
be fully determined by the blocker's own description, stay inside reviewer-named files, and
stay under a line cap — and the reconstructed-intent record could only make it skip, never
apply. That posture was borrowed from the reference pipeline's earliest fixer and priced
every judgment call as unaffordable for an agent without the author's reasoning.

In practice it made the fixer nearly useless on exactly the blockers that stall a PR. On
PR #40, cycle 2 skipped both open blockers: one asked compose to adopt an egress-isolation
pattern **already documented in `docs/ssrf-egress-isolation.md`**, the other asked for a
test pinning a new branch, with a whole neighbouring test file to mirror. Neither fix
required the author's private reasoning — both were sitting in the repository — but both
failed the checklist, the cycle cap tripped, and the PR went to `needs-human-review` with
work an agent could have done.

**Decision.** The mechanical gate is replaced by a grounding requirement, adopted from the
current hide-my-list fixer posture: the cold fixer decides like an engineer, and may apply
any fix it can anchor in (1) the repository's existing content and structure, (2) the PR's
reconstructed intent, (3) the reviewer's finding, connected by (4) its own engineering
judgment — with no line cap and no reviewer-named-files-only rule. Each `addressed[].how`
must state the grounding. What it may not do is **invent**: a fix requiring a design
decision the repository has not made, an architectural redesign, or a change the context
record shows to be deliberate is skipped with a reason, exactly as before.

What does *not* change, because the risk it bounds is unchanged: scope stays limited to
reviewer findings (judgment governs *how* a finding closes, never *whether* to do unraised
work); the context record still cannot widen scope and is still untrusted text; a cold
fixer still cannot claim `body_clarification` (schema-enforced — recorded intent is not the
author's own); the docs-coupling rule for invariant-touching fixes still applies; and the
verification run before exit matters *more* under a wider reach, not less. The safety story
is not "the fixer cannot do much" but that the judge grades the pre-fix reviewed SHA, not the
fixer's output: the fixed SHA is not reviewed again (D-fixer-merges-not-rebases), so the pre-fix panel, the fixer's own
gates, and this verification run are the backstop.
