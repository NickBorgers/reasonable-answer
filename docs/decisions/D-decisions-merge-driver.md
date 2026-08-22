## D-decisions-merge-driver — a merge driver resolves the common append-only collision, not a file split

> Superseded in part by **D-decisions-merge-regions**, which restates the recognized shape as a
> rule about the colliding region rather than the whole file. The mechanism, the
> registration-is-conditional argument and the file-split rejection below stand unchanged; the
> original whole-file precondition does not.
>
> **Retired by D-decision-per-file.** The collision this driver resolved no longer exists: each
> decision is its own file, so two decision-bearing PRs touch disjoint paths and `.gitattributes`
> declares no merge driver. The mechanism below no longer executes. The file-split rejection at the
> end of this entry is the one part that was overturned rather than made moot — see
> D-decision-per-file for which of its cost estimates held and which did not.

**The problem.** Every new decision is appended immediately before `## Open items for a future round`
(D-decision-slugs). Almost every PR here is agent-authored (docs/ci-pipeline.md's "Syncing with the
base branch": "Almost every PR here is agent-authored, so when the base moves there is no human in
the loop to resync"), and most add a decision. Two independent, non-conflicting PRs that both append
collide at that identical insertion point: a 3-way merge diffs
each side against the same base line and has no way to order two insertions anchored on it, so the
result is a genuine git conflict with no semantic disagreement behind it — the same shape every time,
regardless of which two decisions collided. The repository already carries dedicated machinery for
exactly this: `review-fixer.yml`'s "Sync with the base branch" step and `fixer.md`'s "Merge conflicts"
section exist because an agent hits this class of conflict routinely enough to need a documented,
gated resolution path rather than treating it as exceptional.

**The decision.** A repo-local git merge driver (`scripts/merge_decisions.py`, registered by
`.gitattributes` and `git config merge.decisions-append.driver`) special-cases the "both sides purely
appended sections before the tail marker" shape and merges it automatically. Under this original
rule, anything else — an edit to an existing section, an edit to the Open-items section itself, a
genuine same-slug collision with differing content, or any parse ambiguity — falls through to exactly
what an unconfigured merge would have done (`git merge-file`'s own diff3 merge, conflict markers and
all). D-decisions-merge-regions now merges the first two cases region-wise when the shared core and
tail merge cleanly; the same-slug and ambiguity cases still decline. The driver is registered at
every place this repository actually runs a merge of this kind: `review-fixer.yml`'s two sync-merge call
sites, `review-pipeline.yml`'s merge-tree recreation step (D-inherit-whole-range), and
`sync-open-prs.yml`'s base-moved resync (D-base-moved-resync), plus `.devcontainer/setup.sh` for a
human resolving the same conflict locally.

Under the original rule, superseded by D-decisions-merge-regions, the recognized whole-file shape
was exact: appended text had to be one or more complete `## D-<slug> — …` sections, nothing else,
with at least one blank line separating the last one from the tail marker. The regional rule keeps
the fail-closed requirements for added sections and parse ambiguity while allowing unrelated prose
edits inside shared sections and the Open-items tail to be merged by `git merge-file`.

**Registration is conditional, because a broken driver is worse than none.** "Falls through to what
an unconfigured merge would have done" is a property of the driver's *decisions*, not of its
*existence*: a driver whose command cannot start does not fall through to anything. Git marks the
path conflicted, leaves "ours" in the worktree with no conflict markers, and records the path as
merely `UU` in the index — and `review-fixer.yml`'s commit step runs `git add -A` before its marker
gate, which resolves that entry and leaves `git ls-files -u` and `git diff --check --cached` both
empty. The gate passes and the pipeline pushes a merge that dropped every base-side change to a
normative spec file, with no marker anywhere for a human or an agent to notice. The whole marker
gate assumes an unresolved conflict leaves markers behind, and a non-executing driver is the one
thing that breaks that assumption. So every site registers through
`scripts/register_decisions_driver.sh`, which first runs the exact command git will run against a
synthetic append (must merge it) and a synthetic same-section conflict (must decline it *and* leave
markers), and registers only if both hold — clearing any earlier registration if they do not. A
missing or broken driver therefore degrades to real plain-git behaviour rather than failing the job:
a stuck PR buys no safety here, and the conflict is then resolved exactly as it was before this
decision existed. The fixer's sync step carries the matching backstop, refusing to continue if a
path routed to the driver comes back conflicted with no markers in it.

Splitting the file into one-decision-per-file was considered and rejected: the single append-only log is
load-bearing in `scripts/validate-decision-numbers.sh` and `tests/test_decision_numbers.py`'s
whole-file duplicate scan, `tests/test_reviewer_prompt_ranges.py`'s membership check, several CI
reviewer prompts under `.github/scripts/review/prompts/` that cite the slug scheme against this one
file, `pr-validation.yml`'s path-filtered `decisions` job, and `mkdocs.yml`'s single top-level nav entry
(with its own comment explaining the file is deliberately not split). A split would touch all of that to
solve a problem a merge driver solves without touching any of it — and it would still need a variant of
this same driver, or a numbering scheme, to keep the resulting many-file index itself append-safe.

**Invariants.** None of the six tabulated pipeline-core safety invariants (author exclusion, blind
orchestrator, fail-closed lenses, severity floors, termination, untrusted text) is in reach — none
constrains how a model's context is built, and this changes none of them. It does narrow
D-inherit-whole-range's tree-identity gate: a `docs/decisions.md` merge this driver resolved now
recreates identically and can inherit a prior verdict, where before this decision any conflict in
that file forced a full review regardless of shape (see docs/ci-pipeline.md's "Cycle control" and
"Syncing with the base branch", and QP7/QP8 in quality-principles.md, all updated alongside this
entry). That narrowing is deliberate and bounded — the gate stays a pure, deterministic function of
git content, never an LLM judgment, and only the append-only shape is affected. It holds only because
every registration executes the driver from the trusted `main` checkout (`$GITHUB_WORKSPACE` in
review-fixer.yml, `$GITHUB_WORKSPACE/main-checkout` in review-pipeline.yml), never the PR checkout
under review: the inherit step is a verifier and must not run code the commit it is verifying
supplied, and the sync steps hold `WORKFLOW_PAT` and must not execute a contributor's edit to this
file before anything is reviewed. The driver's own default is fail-closed within that boundary: any
condition it cannot confirm true (marker missing, an edit inside the head, any slug named on both
sides) makes it abstain to the exact behavior git would have used unconfigured, so a conflict of any
other shape is unaffected and reviewed exactly as before.
