## D-decision-gate — decision numbers are checked for collision at the gate, not renamed after merge

*(Superseded by D-decision-slugs: the offline, secret-free duplicate gate survives; authoring-time numeric allocation and the "not renamed after merge" stance do not.)*

**The problem.** A decision number (`## D<n>`) is allocated by whoever writes the PR, against
the highest number on main at authoring time. The number is not just prose: it appears in
`config/`, `src/`, `tests/` and several docs, so it is effectively a shared identifier
allocated without a lock. Two PRs open at once each pick the same next-free number and collide
when both merge; worse, when a subagent notices the clash and *independently* renumbers, both
land on the same replacement. This happened three times, most visibly with #54 and #56 both
claiming D-verdict-attached (issue #71). Every collision costs a repo-wide rename.

**The decision.** Keep authoring-time allocation — it is simple, and the number wants to be
chosen while the decision is being written, not minted by machinery at merge — but refuse the
collision at the gate. `scripts/validate-decision-numbers.sh` fails when any `## D<n>` is
defined twice in `decisions.md`, and runs as a required `Decision Numbers` job in
`pr-validation.yml`. The alternative fix idea — allocate the number at merge time — was
rejected: it would have to rewrite the number across `config/`, `src/`, `tests/` and docs in a
merge-time job with write access, which is exactly the kind of branch-writing, credentialed
step the PR gate is built to avoid.

**Why a duplicate on the PR is a collision on main.** On a `pull_request` event GitHub checks
out the *merge ref* — the PR already merged into its base branch — so the file the check reads
is the file that will exist on main once the PR lands. A duplicate there is a collision that
would otherwise reach main, caught before it does. Two simultaneously-open PRs that both add
`D<n>` do not collide against each other's unmerged branches; the first to merge advances main,
and the second's merge ref then carries two `D<n>` and fails. That is why the reviewer should
keep branch protection's "require branches to be up to date before merging" on — it forces the
second PR's check to re-run against the advanced main before it can merge.

**Why its own job, and why it stays pure.** The `tests` job is path-filtered to Python, so a
docs-only PR that adds a colliding section would skip it entirely; the collision check is a
separate path-filtered job (`docs/decisions.md` or the script itself) so that case is covered.
The script reads one file and touches nothing else — no git, no network, no token — so it fits
the secret-free posture of the PR gate and is exercised offline by
`tests/test_decision_numbers.py`, which also asserts the shipped log is collision-free.

**Invariants.** None of the pipeline invariants are in reach: author exclusion, the blind
`OrchestratorView`, fail-closed lenses, severity floors and controller termination are all
untouched. This is repository governance in CI — it constrains how a *document* is numbered,
not what enters any model's context.

**A gap in the sequence is legal; a duplicate is not.** The check refuses a number defined
twice and says nothing about numbers left unused, which is the right asymmetry: renumbering
*your own* unmerged PR out of a collision is cheap, while renaming a merged `D<n>` across
`config/`, `src/`, `tests/` and docs is the expensive thing this exists to prevent. So when two
open PRs pick the same number, one simply moves up and leaves a hole until the other lands. A
reader who finds a missing `D<n>` in this file is looking at a number allocated to a PR still
in flight, not at a deleted decision — decisions are never deleted, only superseded in place.
