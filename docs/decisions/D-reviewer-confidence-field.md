## D-reviewer-confidence-field — the finding arrays admit the field the prompts make reviewers compute

**The problem.** The review pipeline was NO-GOing PRs that every reviewer who completed had
approved. A reviewer attached `confidence` to a `non_blocking_notes[]` entry; that array's items
set `additionalProperties: false` and did not admit the field; ajv rejected the *whole* artifact;
the judge then failed closed on a selected role that produced no valid artifact. On 2026-08-01
this hit six of the eight open resolver PRs (#123–#130) for roughly seven wasted review cycles,
sometimes burning both cycles of a single PR. It is intermittent per role invocation — the model
sometimes writes the number down and sometimes does not — so #124 and #125 passed the same
pipeline the same day.

The cause is a contradiction inside the reviewer contract, not a misbehaving model. Every reviewer
prompt states the **0.7 confidence ladder**: a finding the reviewer is less than 0.7 confident in
belongs in `non_blocking_notes[]` rather than `blocking_issues[]`. Deciding which array a finding
goes in is therefore done in terms of a number the reviewer has in hand — and `confidence` is
*required* on every `fix_suggestions[]` entry, so it is already in the output contract one array
over. The schema then forbade the field on exactly the arrays the prompt had it reasoning about.
A reviewer that recorded the number it was told to use lost every finding it had.

**Decision — admit `confidence` on both finding arrays, bounded `[0, 1]`, nullable on notes.**
Admitted rather than forbidden: the number is real signal a reader of the artifact wants, and
refusing it costs not one finding but all of them. Nullable on `non_blocking_notes[]` matches
`severity` and `source` there. The bound stays live, so `confidence: 1.5` is still a failure.

**Decision — the two finding arrays must admit the same fields, enforced mechanically.**
`blocking_issues[]` and `non_blocking_notes[]` describe the same findings at different confidence;
the ladder is literally "same finding, other array". This is the fourth instance of one class —
`id`, `decision_ref` (#29), `category` (#35), `confidence` (#75, then #131) — and each was fixed by
admitting the one field, which closes an instance, not the class. `reviewer-v1.json`'s own comment
says `severity` and `source` were admitted "pre-emptively to close the class"; that pre-emption
used the wrong frame. The leak-prone set is not *fields a blocker has* but **every field named
anywhere in a reviewer's output contract or prompt**, which is why it missed the one field required
on `fix_suggestions[]` and cited in all five prompts.

`.github/scripts/review/schema-parity.test.mjs` closes the class in the gate that already runs on
every change under `.github/scripts/review/**`:

- the two arrays' property sets must stay **equal** — admitting a field to one fails PR validation
  until it is on both;
- every descriptive field **required on `fix_suggestions[]`** must appear on both arrays — the rule
  that would have caught `confidence` before it shipped;
- neither array may switch to `additionalProperties: true`, which would make the parity assertion
  vacuous while silently accepting hallucinated fields;
- `confidence` keeps its `[0, 1]` bounds on both.

A deliberately one-sided field is still allowed: it goes in the test's `ASYMMETRIC` map with its
reason, which makes the exception a reviewed act rather than an oversight.

**What this is not.** The judge's fail-closed aggregation is untouched and is not being weakened.
A reviewer that fails must block the merge rather than drop out of the review set — that behaviour
was correct, and it is what made a self-inflicted schema bug visible instead of silently shrinking
the panel. What changes is only that a reviewer following its own prompt no longer produces an
invalid artifact. The prompts' confidence ladder is likewise unchanged; the schema is the side that
was wrong. Nor is this a general tolerance layer: unknown properties still fail closed, in the same
narrow spirit as the `maxLength` normalizer, which shortens over-long strings and deliberately
never drops an unknown field.

**Known limit.** Reviewer artifacts are validated against **main's** copy of the schema, so this
change does nothing for its own reviewers — the flake can still kill a role on the PR that fixes
it. That is what happened to the first attempt at this fix (PR #81, closed unmerged 2026-07-30,
NO-GO'd on cycle 1 by this exact bug class hitting its `invariant` reviewer). The limit is recorded
in [ci-pipeline.md](../ci-pipeline.md) rather than worked around, because the alternative — validating
against the PR's own schema — would let a PR relax the contract its own review is checked against.
