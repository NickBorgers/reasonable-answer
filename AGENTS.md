# AGENTS.md

Orientation for coding agents. This file is a map, not a manual: it states the few things you
cannot infer from the repo, and points at the documents that hold the rest. `CLAUDE.md` is a
symlink to this file.

**What this is.** A LangGraph pipeline that refines a report until no eligible reviewer can find a
material defect in it. Models alternate writing and critiquing under strict epistemic isolation; a
deterministic controller that never sees the report decides when to stop.

## Setup and verification

The devcontainer is the supported entry point: clone, open, `make test`. The suite is fully
offline — a fake proxy drives the whole graph, so no network and no API key are needed.

```bash
uv sync --frozen --extra web --group dev
uv run pytest -m "not live"     # must pass; --extra web is required or collection fails
uv run ruff check src/ tests/   # must pass
actionlint                      # if you touched .github/workflows/
```

`uv` only — there is no pip/poetry path. There is no type checker and no formatter; ruff
(`line-length = 110`) is the whole style gate. Anything you can learn from `Makefile`,
`pyproject.toml`, `compose.yaml`, or `config/roster.yaml` is not repeated here — those files are
heavily commented and are the primary reference.

## `docs/` is normative specification, not background reading

This is the rule agents break most. Every safety property exists because a specific adversarial
finding killed the previous version. Changing behavior means updating the matching `docs/*.md`
**and** adding a decision entry — one new file, `docs/decisions/D-<slug>.md` — in the same PR;
silent drift is the top cause of a NO-GO from CI review.

| document | what it governs |
|---|---|
| [docs/DESIGN.md](./docs/DESIGN.md) | what the system is, the toolchain, the document map |
| [docs/architecture.md](./docs/architecture.md) | module layout and data flow |
| [docs/isolation.md](./docs/isolation.md) | author exclusion, blind orchestrator, what may enter a context |
| [docs/convergence.md](./docs/convergence.md) | the 14-rule controller table, termination, terminal statuses |
| [docs/decisions.md](./docs/decisions.md) | the decision-log index: identifier scheme, adversarial findings, test matrix, open items |
| [docs/decisions/](./docs/decisions/) | one file per decision — `D-<slug>.md` defines `D-<slug>` |
| [docs/quality-principles.md](./docs/quality-principles.md) | the `QP<n>` evidence register CI audits against |
| [docs/bias.md](./docs/bias.md) | the observable-text social-bias rules |
| [docs/authentication.md](./docs/authentication.md) | who the web interface believes you are |
| [docs/deployment-profile.md](./docs/deployment-profile.md) | how the production instance is actually configured |
| [docs/run-provenance.md](./docs/run-provenance.md) | which build produced a run — **read this before comparing runs across a change** |
| [docs/ssrf-egress-isolation.md](./docs/ssrf-egress-isolation.md) | the infrastructure half of the fetch boundary |
| [docs/ci-pipeline.md](./docs/ci-pipeline.md) | the agentic review pipeline and the merge gate |

Six of the invariants CI checks — author exclusion, blind orchestrator, fail-closed lenses, severity
floors clamping up only, termination, untrusted text never reaching a generator as instruction —
are stated in full in `.github/ci/prompts/resolve-issue.md`. They are not the whole set:
`.github/scripts/review/prompts/invariant.md` audits eleven numbered invariant rows (those six plus
bias-category anchoring, dispute-adjudication blindness, identity/ownership, round/hash idempotent
replay, and cross-model confirmation) plus a twelfth row for docs-as-spec drift.

## Decision identifiers

`docs/decisions.md` is the registry index; `docs/decisions/` holds the decisions themselves. Each
decision is identified by a **slug derived from its subject** — not by a number from a shared counter
(D-decision-slugs supersedes D-decision-gate). Coin a slug that describes the decision
(`D-source-verification`, not an opaque number); two concurrently-open PRs cannot collide, because
neither needs to know what the other chose.

**Write a decision as a new file: `docs/decisions/D-<slug>.md`, whose first line is
`## D-<slug> — …`.** Add the file and edit nothing else — no insertion point, no index entry, no nav
entry (`mkdocs.yml`'s `not_in_nav` covers the directory). That is the whole point: two
decision-bearing PRs touch disjoint paths, so they are conflict-free by construction and a merge
queue can build them (D-decision-per-file, which retires the `decisions-append` merge driver).
There is **no ordering** — a slug never implies a sequence, a range of slugs is meaningless
(enumerate them instead), and `git log --diff-filter=A -- docs/decisions/` is the real chronology.

Duplicates — the same slug defined twice, in the prose-heading *or* the index-table form — fail the
required `Decision Numbers` check (`scripts/validate-decision-numbers.sh`), which also refuses a
filename that disagrees with the heading it contains. Decisions are superseded
in place, never deleted. The reviewer prompts describe the slug scheme rather than a numeric range,
so adding a decision no longer requires widening any hand-written range;
`tests/test_reviewer_prompt_ranges.py` asserts that for the three reviewer prompts it covers
(`invariant.md`, `docs.md`, `quality.md`). The repo-wide guarantee — every `D-<slug>` citation
anywhere in the tree (docs, code, tests, config, README/AGENTS, all reviewer prompts) resolves to
a slug the registry actually defines — is `tests/test_citation_resolution.py`.

## Commits and PRs

Conventional Commits, lowercase, declarative subject (`feat(resolve): render a page, never
disguise who is asking for it (D-paid-tier-page)`). Cite the decision ID in the subject when the commit
introduces or implements one. PRs are required; fill in every section of
`.github/pull_request_template.md` — the invariant reviewer diffs your "Invariants touched" list
against the code, so an inaccurate list is worse than an empty one.

If you are a CI agent, the last line of the PR body must be exactly
`Author-Session: ${AGENT}/${RUN_ID}` — that trailer is how the fixer resumes your session instead
of falling back to a cold agent with no context.

## Things that will bite you

- **Do not force-push, and do not use `--no-verify`.**
- **Do not weaken, skip, or delete a test to make a change pass.** If a test is genuinely wrong,
  argue it in the PR body.
- **Do not touch `.github/workflows/review-*`, `.github/actions/review-*`, or
  `.github/scripts/review/**` unless the task explicitly asks.** Those govern your own review.
- **Stay in scope.** Note adjacent problems in the PR's "Deliberately not done" section.
- **Never add a test that needs the network.** Mark it `live` if it truly must, or you break the
  "clone → run tests" promise. CI always runs `-m "not live"`.
- **A new page under `docs/`** must be added to `nav:` in `mkdocs.yml`, to the document map in
  `docs/DESIGN.md`, and — if normative — to the `is_spec_critical` allowlist in
  `.github/actions/review-classify/action.yml`. `mkdocs build --strict` also fails on any relative
  link that leaves `docs/`; link to root or `.github/` files by absolute URL. **`docs/decisions/` is
  the one exception and is meant to be**: `not_in_nav` and an `is_spec_critical` glob already cover
  the whole directory, so a new `D-<slug>.md` needs no edit anywhere (D-decision-per-file). A
  relative link out of it climbs one extra level — `../isolation.md`, not `./isolation.md`.
- **Browser impersonation and stealth proxying are doctrine, not gaps.** The outbound user agent is
  fixed and the extraction provider's proxy mode is pinned; tests assert it.
- **Ambiguous task → ask, don't guess.** Comment on the issue rather than opening a speculative PR.
