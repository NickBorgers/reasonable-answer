## D-spec-critical-coverage — the classify allowlist must name every normative page, not just the ones present when it was written

**The finding.** `.github/actions/review-classify/action.yml`'s `is_spec_critical` allowlist is
what keeps a docs-only PR from skipping the `security`, `test`, and `quality` reviewers when the
"docs" it touches are actually the specification. The allowlist is enumerated by hand, and three
pages that self-declare or otherwise function as normative had never been added to it:
`docs/ssrf-egress-isolation.md` (the infrastructure half of the fetch boundary `fetch.py` itself
says it is not — [ssrf-egress-isolation.md](../ssrf-egress-isolation.md)), `docs/run-provenance.md`
(the contract for which build produced a run, load-bearing for comparing runs across a change —
[run-provenance.md](../run-provenance.md)), and `docs/question-refinement.md`, which states outright
at its own tail that "the design above is normative." A PR that touched only one of these three
classified as docs-only: `invariant`, `docs`, and `quality` still ran, but `security` and `test`
were skipped outright. For the SSRF boundary document specifically, that is the concrete exposure —
the page governing an egress control gets no `security` review to catch a boundary weakened in
prose.

`docs/deployment-profile.md` was checked and correctly excluded: it says of itself that it is
"descriptive, not normative" and that "nothing here licenses changing" the committed defaults it
records.

**The decision.** Add the three pages to `is_spec_critical`, in the file's existing per-line
style. No change to the allowlist's shape — it stays a hand-maintained allowlist, wrong by default
for anything new, exactly as `docs/ci-pipeline.md` and `AGENTS.md` already say. What changes here
is only that it now agrees with the document map in `docs/DESIGN.md` and the `nav:` in
`mkdocs.yml`, both of which already listed all three pages; only the classify action's allowlist
had drifted.

`docs/ci-pipeline.md`'s "Role selection" section is corrected alongside: its enumeration of the
spec-critical set named only five of the thirteen `docs/` pages the allowlist now carries, and its
claim that "`security` runs unless the change is docs-only" was never the whole rule — the code
also requires the diff to touch one of `security`'s own trigger paths, so a `tests/`-only diff is
not docs-only and still selects no security reviewer. The prose now states the conjunction.

Two stale identifier mentions are fixed alongside, in scope because they are inside the reviewer
prompts this PR was already required to touch under AGENTS.md's "unless the task explicitly
asks" carve-out: `.github/scripts/review/prompts/invariant.md` row 12 asked a PR to add "a new
`D<n>`" to `docs/decisions.md`, and `docs/quality-principles.md` compared `QP<n>` ids to `D<n>`
ids — both predate D-decision-slugs and now read `D-<slug>`, matching every other reference to the
scheme.

**Invariants.** None of the six is in reach; this changes only which reviewer roles a diff
selects and corrects prose describing that selection. No model call, prompt content sent to a
model, critic assignment, severity, or controller rule is touched.
