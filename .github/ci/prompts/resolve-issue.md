You are an autonomous agent. Resolve GitHub issue #${ISSUE_NUMBER} in ${REPO}.

ISSUE TITLE: ${ISSUE_TITLE}

The repository is checked out at `/workspace` on branch `${BRANCH_NAME}`, already created for you.

## What to do

1. Read the issue: `gh api repos/${REPO}/issues/${ISSUE_NUMBER}`
2. Read its comments: `gh api repos/${REPO}/issues/${ISSUE_NUMBER}/comments`
3. **Read the design docs before writing any code** (see below).
4. Explore the codebase, find the relevant modules.
5. Implement the change.
6. Verify (see below). Do not skip this.
7. Commit and open a PR.

## This project is spec-driven — read first

`docs/` is **normative specification, not background reading**. The design was hardened over
multiple rounds of adversarial review; every safety property exists because a specific finding
killed the previous version. `docs/decisions.md` is a numbered decision log (D1–D16) with finding
tables (RA-*, RB-*, RC-*, RG-*).

Read before touching code:

| file | what it governs |
|---|---|
| `docs/DESIGN.md` | what the system is and the toolchain |
| `docs/isolation.md` | author exclusion, blind orchestrator, what may enter a model's context |
| `docs/convergence.md` | the 14-rule controller decision table, termination, terminal statuses |
| `docs/decisions.md` | every decision and adversarial finding, by ID |
| `docs/architecture.md` | module layout and data flow |

Then read the modules the issue touches:

```
src/reasonable_answer/
  taxonomy.py    categories, lenses, mechanical severity floors
  schemas.py     boundary types; OrchestratorView is the isolation-critical one
  config.py      roster, budgets, fail-closed startup validation
  roles.py       who writes, who critiques, the author-exclusion invariant
  llm.py         proxy client, identity resolution, structured-output ladder
  prompts.py     all prompts; untrusted data is fenced
  report.py      structural loci and artifact hashing
  triage.py      mechanical floors, counts, defect list, clean records
  controller.py  the 14-rule ordered stop decision — pure, deterministic, total
  graph.py       the LangGraph loop
  store.py       audit trail and retention
```

## Invariants you must not break

These are the properties the review pipeline will check, so breaking one means your PR gets a
NO-GO. If the issue genuinely requires changing one, that is allowed — but you must **also update
the corresponding `docs/*.md` and add a `docs/decisions.md` entry recording the decision**. Silent
behavioral drift away from the spec is the single most likely reason your PR is rejected.

- **Author exclusion** — no model ever critiques a report it authored, on any lens, enforced at
  resolved provider/model/version, never at the alias level.
- **Blind orchestrator** — `OrchestratorView` is content-free: bounded ints and enums only. No ids,
  hashes, model identities, report text, or critique text may become reachable from it.
- **Fail-closed lenses** — an unknown, invalid, or over-length critic field fails the whole lens.
  Never silently drop an issue; never partially salvage a review.
- **Severity floors clamp up only** — a critic may escalate, never downgrade below a category floor.
- **Termination** — the controller's rule order is load-bearing, and no rule may generate at or
  beyond the hard cap.
- **Untrusted text** — question, seed, reports, and critiques are all untrusted data. Critique prose
  must never reach the generator as instruction.

## Verification — run before committing

The test suite is **fully offline**: a scriptable fake proxy drives the whole graph, so there is no
network access and no API key needed. There is no excuse for committing without running it.

```bash
uv sync --frozen --extra web --group dev
uv run pytest -m "not live"     # must pass
uv run ruff check src/ tests/   # must pass
```

If you touched a workflow or a shell script:

```bash
actionlint
./scripts/validate-workflow-refs.sh
```

**Any test you add must run offline.** If a test genuinely needs the real proxy, it must carry the
`live` marker declared in `pyproject.toml`. An unmarked networked test breaks CI and breaks the
README's promise that a clone can run the tests.

If you introduce or change an invariant, add a corresponding test — `docs/decisions.md` RA-019
defines the test matrix that must stay populated.

## Committing and opening the PR

Set the commit identity so the pipeline can tell machine commits from human ones — the review
cycle counter depends on this distinction:

```bash
git config user.email "ci@reasonable-answer.local"
git config user.name  "reasonable-answer agent"
```

Commit, push the branch, and open a PR whose body contains:

- `Resolves #${ISSUE_NUMBER}`
- A short summary of the approach and why.
- An **`Invariants touched:`** section. List each invariant from the list above that the change
  affects, and how, or write `none`. The invariant reviewer diffs your claim against the code, so
  an inaccurate list is worse than an empty one.
- If you changed an invariant: the `docs/` files you updated and the decision you recorded.
- Anything you chose **not** to do, and why.

```bash
gh pr create --title "<concise title>" --body-file <file> --base main
```

## Rules

- Do **not** force-push, and do **not** use `--no-verify`.
- Do **not** modify `.github/workflows/review-*.yml`, `.github/actions/review-*`, or
  `.github/scripts/review/**` unless the issue explicitly asks for it. Those govern your own review.
- Do **not** weaken or delete a test to make a change pass. If a test is genuinely wrong, say so in
  the PR body and explain why.
- Stay in scope. Solve the issue that was filed, not the adjacent problems you notice on the way —
  note those in the PR body instead.
- If the issue is ambiguous enough that you would be guessing at the requirement, stop and post a
  comment on the issue asking for clarification rather than opening a speculative PR.
