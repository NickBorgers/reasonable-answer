# reasonable-answer

Takes a question (and optionally a seed report) and produces a higher-quality report whose
argument is *sound* — where "sound" means **no eligible reviewer can find a material defect**,
not that anyone asserted it was good.

Models take turns writing and critiquing, and a report is never critiqued — on any dimension —
by the model that wrote it. The decision to stop belongs to deterministic code and to a referee
that never sees the report at all.

This site is the design documentation. It carries no content of its own; every page below is a
file in [`docs/`](https://github.com/NickBorgers/reasonable-answer/tree/main/docs), published
unchanged.

## Start here

- **[The concept](concepts.md)** — the approachable tour of *why* the system is shaped this
  way, with the minimum of jargon. Read this first.
- **[Design overview](DESIGN.md)** — the hub: what it is, the roster, the alternating refine
  game, and the isolation principles that hold it together.

## How it works

- **[Architecture](architecture.md)** — the LangGraph graph, node roles, role assignment,
  failure handling, resumability.
- **[Epistemic isolation](isolation.md)** — what each agent sees, what it never sees, and why
  the boundary is the context window.
- **[Convergence and stopping](convergence.md)** — the observable-category taxonomy, the
  ordered stop decision, and the terminal statuses.
- **[Question refinement](question-refinement.md)** — the pre-run reframing suggestions: the
  taxonomy, the guardrails, and what they deliberately will not do.
- **[Social bias rules](bias.md)** — the three observable-text rules critics apply, and what a
  bias finding may not be.

## Operating it

- **[Authentication](authentication.md)** — who the web interface believes you are, and the
  proxy that has to be in front of it.
- **[SSRF egress isolation](ssrf-egress-isolation.md)** — the infrastructure half of the fetch
  boundary: what the network must enforce that the application cannot.
- **[CI and the review pipeline](ci-pipeline.md)** — what runs on a pull request, the agentic
  review graph, and the merge gate.
- **[CI setup](ci-setup.md)** — the manual, admin-only setup: runners, secrets, variables,
  Pages, branch protection.

## The record

- **[Design decisions](decisions.md)** — every decision and every adversarial review that
  produced it, in the order they happened.

## Running it

To clone it, configure a roster and actually run the thing, see the
[README on GitHub](https://github.com/NickBorgers/reasonable-answer#readme).
