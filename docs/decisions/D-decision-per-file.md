## D-decision-per-file — one file per decision; the shared anchor is removed, not managed

> Retires **D-decisions-merge-driver**, **D-decisions-merge-regions** and **D-base-moved-resync** by
> deleting the collision they resolved, and amends **D-decision-slugs**, whose ordering clause named
> position in a single file. Those three entries stand as the record of what was tried; the mechanism
> they describe no longer executes.

**The problem.** D-decision-slugs removed the *numbering* coordination between two concurrently-open
decision-bearing PRs: a slug is coined from its own decision's content, so no PR needs to read
another to choose an identifier. It left the *textual* coordination untouched. Every decision was a
`## D-<slug> — …` section appended immediately before `## Open items for a future round` in one
6,300-line file, so two PRs that each added one inserted at the identical base line. A three-way
merge has no way to order two insertions anchored on the same line, so the conflict was guaranteed —
by construction, not by luck, and with no semantic disagreement behind it.

Almost every PR here is agent-authored and most carry a decision, so the guaranteed conflict was the
normal case rather than the exceptional one. Draining the eight-PR stack of 2026-08-18 measured what
that costs (issue #188, and this is proposal D there):

- Every merge to `main` invalidated every other open decision-bearing PR, so a stack of *n* needed a
  driver resync per PR per merge — O(n²) churn.
- An unmergeable PR earned **zero CI, silently**: GitHub creates no `pull_request` workflow runs when
  it cannot build the merge ref, so a conflicted PR could not even earn a verdict to inherit later.
  Two PRs sat forty minutes with pushed fixes and no signal at all.
- Five of the eight ultimately required policy admin-merges for infrastructure verdicts.
- A GitHub **merge queue was impossible**, because the queue cannot register a repo-local merge
  driver. The queue is the platform primitive that would replace the hand-sequenced merge train
  outright.

D-decisions-merge-driver, D-decisions-merge-regions and D-base-moved-resync are each correct and each
made the collision cheaper to resolve. Together they are also the whole answer to the question of
whether managing this collision is worth it: three decisions, `scripts/merge_decisions.py`,
`scripts/register_decisions_driver.sh`, `scripts/sync_pr_with_base.sh`, `sync-open-prs.yml`, four
registration sites, a carve-out in D-inherit-whole-range's tree-identity gate, and roughly 900 lines
of test — all of it load-bearing infrastructure whose only job was compensating for one shared line.

**The decision.** A decision is its own file: `docs/decisions/D-<slug>.md`, whose first line is the
`## D-<slug> — …` heading it defines. `docs/decisions.md` keeps its name and becomes the registry
index — the identifier scheme, the old-number mapping, the early design-dialogue tables, every
`RA-*`/`RB-*`/`RC-*`/`RG-*` finding table, the RA-019 test matrix, and the open items.

Writing a decision therefore **adds a path and edits nothing**. Two decision-bearing PRs touch
disjoint files, so they merge cleanly with no driver, under GitHub's own merge, under `git`, and
inside a merge queue. The filename *is* the identifier, so a citation resolves to a path without a
search: `D-writer-disputes` is `docs/decisions/D-writer-disputes.md`.

`.gitattributes` no longer declares `docs/decisions.md merge=decisions-append`. That single line is
what made the driver run, so removing it is what retires the mechanism; the machinery goes inert
rather than misbehaving, because `sync_pr_with_base.sh` tries the no-driver merge first and pushes
nothing unless the driver is what made the merge succeed. Deleting `merge_decisions.py`,
`register_decisions_driver.sh`, `sync-open-prs.yml` and the four registration sites is a follow-up,
so that the removal is a reviewable deletion rather than a side effect of this split.

**Nothing carries ordering, and nothing needs to.** D-decision-slugs already ruled that a slug
implies no sequence and that a *range* of slugs is meaningless. Its remaining ordering claim was
positional — a decision's place in the file — which this split necessarily drops. No replacement is
introduced: no `date:` front matter, no `after:` field, no generated merge-order index. Each of those
would be a new field to keep true, and the real chronology is already recorded more accurately than
file position ever was, by `git log --diff-filter=A --format='%as %s' -- docs/decisions/`.

**The index is deliberately not an enumeration, and neither is anything else.** This is the part that
decides whether the collision is actually gone. A hand-kept list of the 81 decisions — in
`docs/decisions.md`, in `mkdocs.yml`'s `nav`, or in the `is_spec_critical` allowlist — would put the
shared insertion point straight back, just in a different file, and a merge queue ejects a PR for a
one-line conflict as readily as for a large one. So every place that would have needed a per-decision
entry takes the directory as a whole instead:

| surface | how the directory is covered |
|---|---|
| `mkdocs.yml` | `not_in_nav: /decisions/*.md`. The pages build, are linked from the index and are searchable; `validation.nav.omitted_files` stays strict for every other page. |
| `.github/actions/review-classify/action.yml` | one `is_spec_critical` glob, `docs/decisions/*.md` — which is also how D-spec-critical-coverage's standing requirement is met for the whole directory at once. |
| `pr-validation.yml` | the `decisions` path filter gains `docs/decisions/**`, so a PR adding only a decision file still gets the registry gate. |
| `docs/DESIGN.md`, `docs/index.md`, `AGENTS.md` | name the directory and the naming rule, never the members. |

**`scripts/validate-decision-numbers.sh` becomes a registry-shape check.** Its uniqueness job is
unchanged — no slug defined twice across the union of the two definition forms, the index-table row
and the prose heading — and it gains the three structural checks that make a slug's definition
unambiguous now that it is not "the one section with that heading in the one file": every entry in
the directory is a regular file named `D-<slug>.md`, each opens with the matching `## D-<slug> — …`
heading and holds exactly one, and no prose section is left behind in the index. It stays pure and
offline — one file and one directory, no git, no network, no secret — so it still fits the secret-free
PR gate. The script filename and the `Decision Numbers` job name are unchanged on purpose: that job
is a required status check, and renaming it would silently stop gating.

**Why the split, when D-decisions-merge-driver rejected it.** That entry rejected a split on a cost
estimate, listing the surfaces a split would have to touch: the validator, `test_decision_numbers.py`,
`test_reviewer_prompt_ranges.py`'s membership check, the reviewer prompts, `pr-validation.yml`'s
path-filtered job, and `mkdocs.yml`'s nav comment. The estimate was accurate about *which* surfaces
and wrong about the weight: each is a few lines, they are listed in the table above and in this PR's
diff, and none of them needed a new mechanism. It also argued a split "would still need a variant of
this same driver, or a numbering scheme, to keep the resulting many-file index itself append-safe" —
which is true only of a split that keeps an enumerated index. This one does not have one, which is
why it needs neither.

The deeper reason is that the two approaches differ in kind, not degree. A merge driver makes the
collision cheap to resolve, and every failure mode in issue #188 came from a case where it could not
run: no `pull_request` event, no review cycle, no repo-local git config in a merge queue. Removing the
collision needs nothing to run at all.

**What is given up.** The single-file read — scrolling or `grep`-ing 6,300 lines of decisions in one
buffer — is gone, and no generated concatenation replaces it (a generated file would be one more
shared path). In exchange, `rg <term> docs/decisions/` reports the matching decision by filename,
which is the slug, and a citation now names the file that defines it. The published site loses a
decisions sidebar; search and the index cover it. Deep links are unaffected: nothing in the tree
linked to a `decisions.md#anchor`, and `docs/decisions.md` keeps its path, so every existing
`[…](./decisions.md)` link still resolves.

**Invariants.** None of the six tabulated pipeline-core safety invariants (author exclusion, blind
orchestrator, fail-closed lenses, severity floors, termination, untrusted text) is in reach: this
changes where normative prose is stored, and touches no module that builds a model's context, scores
a finding or decides a stop. It does *widen* D-inherit-whole-range's tree-identity gate back to its
pre-driver form by deleting the one carve-out D-decisions-merge-driver had opened in it — a
driver-resolved `docs/decisions.md` merge could recreate identically and inherit a verdict, and now
there is no such merge, so the gate is a single rule again with no exception: a resolved conflict is
reviewed. That is strictly more fail-closed than what it replaces.

The migration itself was verified to be lossless before anything else changed: each extracted file is
byte-identical to the section it came from apart from relative links gaining one `../` level (every
such link in the file was already the uniform `./page.md` form), and every non-decision section and
the preamble survive verbatim in the index.
