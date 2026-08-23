## D-addressed-blockers-visible — a blocker the fixer closed is reported as closed, not as a blocker

**The problem.** The finalize comment on PR #153 announced `✅ GO — cleared at cycle 1` and then,
eleven lines later, a heading reading **Blocking issues** listing two `high`/`medium` findings. Both
had been fixed by the fixer in that same cycle. Nothing in the section said so. The only signal was a
count inside a *Why* bullet — "2 blocker(s) addressed by fixer" — and the per-reviewer table still
showed `quality · request_changes · 2`. Read top to bottom, the comment said the merge gate had
cleared a PR with two outstanding blockers, which is precisely the failure the gate exists to prevent.
The owner read it that way, which is the only test that matters for a comment.

The renderer had no way to do better: it printed every reviewer's `blocking_issues[]` unconditionally,
because the verdict artifact carried only `unaddressed_blocker_ids` — empty by construction on a GO.
The judge knew which blockers the fixer had been credited with; that knowledge simply never left it.

**The decision.** The credited set travels with the verdict as `addressed_blocker_ids`, and the
comment partitions on it.

- Every verdict carries the namespaced ids the fixer was credited with. A verdict is constructed in
  exactly three places — `aggregate()`, the inline no-reviewer-artifacts verdict in `judge.mjs`, and
  `checkExpectedRoles()` — and the field is on all three, empty on the two fail-closed
  `pipeline_error` paths, which never reach aggregation. A field the renderer reads unconditionally
  must not be undefined on exactly the paths taken when something has already gone wrong; the
  `checkExpectedRoles()` site is reached when a *reviewer* has failed, which is the last place a
  second, unrelated defect should appear.
- The comment renders two headings — *still outstanding* and *raised this cycle, fixed by the fixer* —
  and each is emitted only when it has entries, so a clean panel still shows no blocker section at all.
  The fixed list names the commit the fix landed in when one did.
- The per-reviewer table counts **outstanding** blockers, with the fixed count beside it
  (`0 (2 fixed)`). The table is read first, so leaving a bare `2` there would reproduce the same
  contradiction one line higher up.

**Why partition rather than suppress.** Hiding a fixed blocker would make the comment agree with
itself by deleting the record of what the reviewer actually found — and that record is why the panel
runs. A reader needs to see that `quality` raised a QP12 drift and that it was answered, not that
nothing happened.

**Why the judge's set and not a recomputation.** The comment and the merge gate must never disagree
about which blockers stand. `addressed_blocker_ids` is the same set the verdict was computed from, so
a divergence is not merely unlikely, it is unrepresentable. Matching is on the namespaced `role/id`
for the reason the judge namespaces: two reviewers can raise the same bare id, and a bare-id lookup
would report a still-open blocker as fixed — a false clear, in the one direction that must not fail.
Absent field, absent artifact, or unreadable JSON all render everything as outstanding.

**Invariants.** None of the six is in reach — this changes what the pipeline *says*, never what it
*decides*. The verdict, `unaddressed_blocker_ids`, the merge-gate status and every cycle rule are
untouched, and the aggregation tests that pin the GO/NO-GO boundary are unchanged. The one contract
change is additive: a new field on the verdict artifact, which nothing outside the renderer reads.
