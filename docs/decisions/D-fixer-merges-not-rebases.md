## D-fixer-merges-not-rebases — the fixer syncs the branch and resolves conflicts; it merges, it never rebases

**The problem.** Almost every PR in this repository is agent-authored, and no agent goes back to
a PR it already opened. When `main` moves, the branch drifts, and there is nobody in the loop to
resync it — the PR sits until a human notices and rebases by hand. The previous position was that
this was deliberate: `docs/ci-pipeline.md` listed "no agent-driven merge-conflict resolution"
under *Deliberately not built*, on the grounds that an agent choosing at conflict markers produces
exactly the unreviewable change this pipeline exists to catch. That reasoning is not wrong about
the risk; it is wrong about the alternative, which in practice is not a careful human resolution
but an indefinitely stale PR.

**The mechanism.** Before the fixer's agent runs, the host attempts
`git merge --no-commit --no-ff origin/<base>` in the PR workspace. `none` (already current) is a
no-op; `clean` is committed by the host and needs no agent at all; `conflicts` leaves the markers
in the working tree, writes the conflicted paths to a file, and the agent resolves them as
ordinary file edits before it touches any reviewer blocker. The host then seals the merge, or
aborts it.

**Merge, not rebase**, for three reasons that all point the same way. A rebase rewrites the SHAs,
and `reviewed_sha` is the key that dedup, the cycle counter, and every artifact name hang from. It
would break the `input_sha == reviewed_sha` gate, since the rebased tree no longer descends from
the SHA the reviewers read. And it needs a force-push from the one job holding a write-capable
PAT, where a non-forced `git push HEAD:<ref>` is a much smaller thing to get wrong. A clean merge
whose tree matches a mechanical recreation lands on the merge-from-base inherit path, so that
resync costs no review cycle. A conflict resolution by the fixer or a human does not inherit: under
D-inherit-whole-range, the panel reads any merge whose tree cannot be recreated exactly.

**The gate that makes it safe to leave a conflict unresolved.** Both fixer prompts tell the agent
to prefer the base branch's structural change and re-apply the PR's intent on top, and — the part
that matters — to leave a marker in place when the two genuinely cannot be reconciled. The host
checks for unmerged index entries *and* for markers in staged content, because a file staged with
its markers intact has neither an unmerged entry nor a resolution. Either one aborts the merge,
labels the PR `needs-human-review`, and comments the unresolved paths. So the honest failure is
cheap and visible, which is what makes "do not guess" a real instruction rather than a wish.

**What is not defended.** A resolution that is syntactically clean and semantically wrong passes
every gate here — `ruff` sees Python, and nothing reads the merge for meaning. The owner's
confirmed intent (see the correction below) is that fixer output — a conflict resolution included
— reaches main without a further review cycle. The protection is the pre-fix panel plus the
fixer's own gates: schema validation against `fix-result-v1.json`, `ruff` at the version pinned in
main's lockfile, the marker gate (no unmerged index entry, no conflict marker in the staged
content), and the remote-head-equality check. None of those reads a merge for meaning, so the
residual is real and it is accepted, not defended against: a wrong-but-clean conflict resolution,
or any other wrong-but-clean fixer output, can reach main unread.

**Correction, from PR #49, and a second correction on top of it.** The paragraph above originally
said the merge commit "arrives on the inherit path", and treated that as an accepted cost. On PR
#49 it was worse than described: reviewers cleared the pre-fix tree, the judge issued a GO, the
fixer pushed a merge carrying four conflict resolutions and two blocker fixes, gather saw
merge-from-base and skipped all four reviewers, and that GO was re-stamped onto a tree nobody had
read. Auto-merge fired three seconds later; 2105 lines landed on main unreviewed.

PR #65's response (`docs/ci-pipeline.md`, `review-pipeline.yml`'s inherit check) was to have gather
refuse to inherit any commit authored as `AGENT_COMMIT_EMAIL`, on the theory that "the fixed SHA
earns its own cycle with its own reviewers" was a property worth restoring by rule rather than
letting it hold by the accident of fixer commits having one parent. That was an agent's invention,
not a design decision the repo owner had made, and it inverted the intent this design was ported
from: the owner has since confirmed that fixer output — including a fixer-authored merge — was
always meant to reach main on the strength of the pre-fix panel and the fixer's own gates, without
a second cycle reading it. The per-author inherit check has been removed accordingly (see the
fixer's claim on its own post-push SHA in `review-fixer.yml`'s Push step, which is what actually
stops a second panel from running).

What PR #49 got wrong that is still worth fixing on its own merits: the merge-gate status must land
on the SHA the fixer actually produced (`post_fix_sha`), never the pre-fix `reviewed_sha` — a gate
written on a SHA that is no longer the PR's head protects nothing. `review-finalize.yml` now takes
`post_fix_sha` as an explicit input and writes `review/cycle`, `review/verdict`, and the merge gate
on it.
