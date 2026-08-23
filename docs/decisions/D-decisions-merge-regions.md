## D-decisions-merge-regions — the decisions merge driver reasons about the colliding region, not the whole file

> **Retired by D-decision-per-file**, which removed the collision this narrowing made cheaper to
> resolve rather than narrowing it further: each decision is its own file, so there is no shared
> insertion point and `.gitattributes` declares no merge driver. The mechanism below no longer
> executes.

**The problem.** D-decisions-merge-driver built the right mechanism and gave it the wrong
precondition. It asks each side for a *whole-file* property — the entire delta must be a trailing
append of complete `## D-<slug> — …` sections, with the `## Open items for a future round` section
untouched — when the ambiguity it resolves is local: two insertions anchored on the same base line,
which a three-way merge has no way to order. Any unrelated clean change elsewhere in a 5,000-line
registry therefore disarmed the driver for a collision it played no part in.

The repository's own rules and history produce three shapes that the whole-file precondition rejects,
and every one is behaviour this repository *prescribes*:

* **An existing section revised in place.** "Decisions are superseded in place, never deleted", so a
  supersession landing on the base while a branch appends is routine. It is also what a decision
  that corrects a stale cross-reference does — `D-minimax-retirement` rewrote one sentence inside an
  older decision ([commit 122360a](https://github.com/NickBorgers/reasonable-answer/commit/122360a6b13fbf168bdbce8fd595df5d6092fe54)).
* **An Open-items bullet.** A decision that closes or opens an item edits the section this file uses
  as its tail marker.
* **A new section that is not last.** A branch that hand-resolved an earlier collision ends up with
  its own section placed *above* the one it merged in, so its delta is an insert rather than an
  append — permanently, for every later merge.

[PR #161](https://github.com/NickBorgers/reasonable-answer/pull/161) records a concrete branch shape
combining all three: a non-last added section on the PR side, plus shared-section and Open-items edits
alongside added sections on the base side. The unresolved ambiguity was the ordering of the added
sections, not the independent edits elsewhere in the file.

**The decision.** State the rule regionally. The driver splits each side's head at every `## `
heading, peels off the sections that side **added**, three-way merges everything that remains — the
sections all three versions share, and the Open-items tail, each on its own — with `git merge-file`,
and reassembles: merged core, then ours' new sections, then theirs', then the merged tail. The only
thing it decides for itself is the ordering of the two insertions. Everything else is decided by the
same `git merge-file` an unconfigured merge would have used, on a smaller input.

Two details are load-bearing. **Section identity is the whole heading line, not the slug**: the head
legitimately carries headings that are not decision-shaped — the identifiers preamble, the
adversarial-review round headings, every pre-slug `## D<n> — …` section — and keying on slugs would
make the driver decline on the real file forever. **Only the join points are normalised**: the log's
spacing between sections is not uniform (some abut the next heading with a single newline, which is
exactly what defeated the old suffix parse on `3c248a5`), so the split is a partition of slices
whose round trip is asserted, and the one blank line between reassembled pieces is the only byte the
driver writes that neither side wrote.

**What it still declines** — each falling through to `git merge-file` on the whole file, i.e. the
no-driver baseline, unchanged: a section deleted or its heading rewritten; a new `## ` heading that
is not decision-shaped; a slug named on both sides, or one already defined in the base; a section
added with no body; a real conflict inside a shared section or inside the tail; any parse ambiguity,
including a duplicated or missing tail marker. Neither side adding a section also declines — there
is no insertion-ordering ambiguity, so the driver knows nothing git does not.

Because the recombination is performed by this driver rather than by git, it is checked before it is
returned: no conflict markers in the result, exactly one tail marker, and the set of headings equal
to the base's plus the added ones, each exactly once. A failure declines; it never repairs. The
fallback is always a correct answer, which is what makes fail-closed cheap here.

`scripts/register_decisions_driver.sh` gains a matching smoke case — an append on one side against
an unrelated in-place revision and an Open-items bullet on the other must merge, keeping both — so a
driver that silently regressed to the whole-file rule fails registration instead of quietly
declining forever. The existing same-section-conflict case is unchanged and still proves the
fallback leaves markers.

**Invariants.** None of the six pipeline-core safety invariants is in reach; none constrains how a
model's context is built. D-decisions-merge-driver's narrowing of D-inherit-whole-range's
tree-identity gate is *widened* by exactly the set of merges described above: a `docs/decisions.md`
merge this driver resolves recreates identically from the trusted `main` checkout and can inherit a
prior verdict. The gate remains a deterministic function of git content and never an LLM judgement,
and the trust boundary is unchanged — every registration site still executes the driver from `main`,
never from the PR under review, because the inherit step is a verifier and the sync steps hold
`WORKFLOW_PAT`. What grew is the share of a single file's conflicts that resolve without a human;
what did not grow is who is allowed to run the code that resolves them.

Four things were considered and deliberately left alone: same-slug collisions (a genuine
disagreement, not an ordering ambiguity), section deletions and heading rewrites, preserving a
mid-file insertion point (new sections normalise to just before the tail marker, which is where
D-decision-slugs says a decision goes), and two PRs adding rows to the same "Key design decisions"
table, which still meet inside the core merge.
