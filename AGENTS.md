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
**and** adding a `docs/decisions.md` entry in the same PR; silent drift is the top cause of a
NO-GO from CI review.

| document | what it governs |
|---|---|
| [docs/DESIGN.md](./docs/DESIGN.md) | what the system is, the toolchain, the document map |
| [docs/architecture.md](./docs/architecture.md) | module layout and data flow |
| [docs/isolation.md](./docs/isolation.md) | author exclusion, blind orchestrator, what may enter a context |
| [docs/convergence.md](./docs/convergence.md) | the 14-rule controller table, termination, terminal statuses |
| [docs/decisions.md](./docs/decisions.md) | the numbered decision log (D1–) and adversarial findings |
| [docs/quality-principles.md](./docs/quality-principles.md) | the `QP<n>` evidence register CI audits against |
| [docs/bias.md](./docs/bias.md) | the observable-text social-bias rules |
| [docs/authentication.md](./docs/authentication.md) | who the web interface believes you are |
| [docs/deployment-profile.md](./docs/deployment-profile.md) | how the production instance is actually configured |
| [docs/ssrf-egress-isolation.md](./docs/ssrf-egress-isolation.md) | the infrastructure half of the fetch boundary |
| [docs/ci-pipeline.md](./docs/ci-pipeline.md) | the agentic review pipeline and the merge gate |

The six invariants CI checks — author exclusion, blind orchestrator, fail-closed lenses, severity
floors clamping up only, termination, untrusted text never reaching a generator as instruction —
are stated in full in `.github/ci/prompts/resolve-issue.md` and audited by
`.github/scripts/review/prompts/invariant.md`.

## Decision numbers

`docs/decisions.md` is the registry. Allocate the next free `## D<n> — …` section at authoring
time, not at merge time. Gaps are legal (a PR in flight); duplicates fail the required `Decision
Numbers` check. Decisions are superseded in place, never deleted. Adding a `D<n>` also means
bumping the stated ID ranges in `.github/scripts/review/prompts/{invariant,docs,quality}.md`, or
`tests/test_reviewer_prompt_ranges.py` goes red.

## Commits and PRs

Conventional Commits, lowercase, declarative subject (`feat(resolve): render a page, never
disguise who is asking for it (D40)`). Cite the decision ID in the subject when the commit
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
  link that leaves `docs/`; link to root or `.github/` files by absolute URL.
- **Browser impersonation and stealth proxying are doctrine, not gaps.** The outbound user agent is
  fixed and the extraction provider's proxy mode is pinned; tests assert it.
- **Ambiguous task → ask, don't guess.** Comment on the issue rather than opening a speculative PR.
