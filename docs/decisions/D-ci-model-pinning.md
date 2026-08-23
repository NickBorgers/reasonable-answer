## D-ci-model-pinning — every CI role names the model it runs, and the runtime-chosen agents default to Codex

**The problem.** Two separate ones, found together while looking for the pipeline's API cost.

The first is that no CI role named a model. `review-agent-run` had exposed a `model:` input since
it was written and **no workflow had ever passed it**, so the two Claude roles ran whatever the
Claude Code CLI currently defaults to, and the three Codex roles ran a `gpt-5.5` literal buried in
a heredoc in `run-in-container.sh`. QP3's stated surface was the `agent:` inputs — but `agent:`
fixes only a model *family*. Which checkpoint actually reviewed a PR was therefore not a property
of this repository at all: a vendor shipping a new CLI default silently re-composed the review
panel, with no diff, no decision, and nothing for the panel to review. A verdict has to be
attributable to something, and "whatever the CLI felt like" is not it.

The second is that the cost-shaped choices had never been made deliberately. Nothing in `docs/`
or `config/` argues about per-role API price anywhere; the economics discussed are cycles, tokens
per re-review, and third-party search quotas. So the assignment of the *expensive* family to a
role was, in four of five cases, undocumented — only `quality` had a stated reason.

**Decision — pin the model everywhere.** `model:` becomes required on `review-agent-run`, checked
at runtime rather than merely declared, since a composite action does not enforce `required: true`.
The `gpt-5.5` literal leaves `run-in-container.sh` entirely and the codex path fails closed on an
unset `AGENT_MODEL`. The five reviewer pins sit in `review-pipeline.yml` beside the `agent:` they
qualify, because that adjacency is what lets a reader check the panel's composition in one place.

| role | agent | model | why this tier |
|---|---|---|---|
| `invariant` | claude | `claude-opus-5` | never-abstain backstop on the six invariants and the merge gate; nothing downstream catches what it misses |
| `test` | claude | `claude-sonnet-5` | bounded, checklist-shaped work against the table in `test.md` |
| `docs` | codex | `gpt-5.6-luna` | the most mechanical role — prose against diff, decision entry present |
| `security` | codex | `gpt-5.6-sol` | guards the egress boundary, where a miss reaches production rather than the next cycle |
| `quality` | codex | `gpt-5.6-sol` | may not rely on remembered literature, so it must actually fetch and read cited sources |

This also pays off the gap noted above: `invariant`, `docs`, `security`, and `test` now carry a
stated reason for their family, which QP3 asks of a new role and which they had never had.

**What the "why this tier" column is, and is not.** It records the *shape of each role's task* —
how much of the repository it must hold, whether it must fetch and read sources, whether anything
downstream catches what it misses. Those are properties of this pipeline, checkable against the
prompts in `.github/scripts/review/prompts/`. It is **not** a measured claim that a given alias
is adequate for a given role, or that one costs less than another: no benchmark was run, and the
relative capability of these checkpoints is asserted by their vendors, not established here. The
tiering is a deliberate, revisable bet, and the thing that would falsify it is a role that starts
missing defects it used to catch — visible as blockers appearing only after a human review.

**Decision — Codex becomes the default author, and the cold fixer.** `CI_AGENT_DEFAULT` defaults
to `codex`, and the cold fixer is pinned to it. Resolving an issue end to end is the pipeline's
longest-running agent task by its configured budget — a 60-minute timeout against the reviewers'
30 — and it is the stage where family diversity is *not* at
stake: whatever writes a PR, all five reviewer roles still read it afterwards, and author
exclusion is enforced by context, not by vendor ([isolation.md](../isolation.md) — model identity
is the secondary boundary there, the context window the primary one). The cold fixer's existing
rationale already said the author's identity buys nothing when there is no session to resume, so
the pin was free to move. Because the agent is chosen at runtime in both stages, each resolves
the model through an `agent_model()` map written inline in its own workflow.

**Why that map is duplicated rather than extracted.** It was a shared script first, and the
fixer died at exit 127 on the very PR that introduced it. The reviewer and fixer jobs run the
pipeline's own logic from **main's** checkout — that is what stops a PR editing the pipeline
reviewing it — so a `scripts/` helper added by a PR does not exist for that PR's own review.
Workflow YAML ships with the PR; a new file in the tree does not. Two copies in the YAML is
therefore the only form that bootstraps, and `tests/test_ci_model_pins.py` asserts they stay
identical, doing offline the anti-drift job the shared file was there to do. It also refuses any
new `./scripts/*.sh` call from those two workflows, so the trap does not get re-set.

**What this is not.** It is not a move to a single-family panel. The split stays three Codex, two
Claude, with `quality` cross-family from `invariant` per D-quality-reviewer, and QP3 now has an
executable check (`tests/test_ci_model_pins.py`) rather than resting on reviewer attention alone.
QP3's surface column is *widened* to include `model:` — a strengthening, so §4 of the register,
which gates weakening a row on new fetchable evidence, does not apply.

**The cost basis, and its limits.** The motivating request was to cut CI API spend. No cost claim
is made here, because none can be supported: this repository has no per-role cost telemetry, no
spend was measured before or after, and vendor list prices are neither cited in the register nor
stable enough to state as fact in a normative document. So the tier assignments are a bet on task
shape, not a demonstrated saving, and the direction of the net change is genuinely unknown.

What *is* established, and is the actual justification for this decision, is the first half: the
pipeline now names the model each role runs, which it previously did not. That claim needs no
citation — it is a property of the diff. Measuring per-role spend, and revisiting these tiers
against evidence rather than against task shape, is an open item below.

**Known risk.** Making Codex the default author makes `codex exec resume --last` the fixer's
common path, and the cold-fixer fallback — which D-resume-timeout added — has to date never
actually fired in production. The timeout containment bounds a hang, but the path stays lightly
exercised, and this change puts more traffic on it.
