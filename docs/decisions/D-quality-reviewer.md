## D-quality-reviewer — a `quality` CI reviewer guards the design's evidence base, which is itself dated and refreshable

**The problem.** The `invariant` reviewer audits conformance: code moving against the current
spec, or either side moving alone (its row 12). A PR that coherently updates code, the normative
doc, *and* `decisions.md` passes it by construction — which is correct for its job and is exactly
the gap. The design's load-bearing choices (author exclusion, fresh-context critique, cross-family
witnesses, mechanical floors and deterministic control, refinement over debate, capped loops,
fetched-text verification) each track a specific published result, and nothing in CI could block
the coherent, self-documented PR that walks the design onto ground that literature refutes — "let
an LLM score convergence 1–10, spec updated to match" would have sailed through the panel.

**Decision.** A fifth reviewer role, `quality`, audits *direction* against a new normative
register, [quality-principles.md](../quality-principles.md): twelve `QP` rows, each naming its
surface and the fetch-verified literature behind it. Conditionally selected (`src/`, `config/`,
every `docs/*.md`, and the review pipeline's own files); may abstain, since `invariant` and `docs`
remain the never-abstain backstop. It runs on **Codex** deliberately: it deconflicts most heavily
with `invariant` (Claude), and the guard on the spec's direction should not share a model family
with the guard on the spec's conformance — the panel applying QP3 to itself.

**Evidence discipline.** The reviewer's parametric memory of the literature is exactly the
testimony this repo distrusts, so a `quality` finding may rest only on the register's References
table (every URL fetch-verified on the marker date) or on text the reviewer fetches during the
review — and it may fetch only URLs appearing in the diff or in that table. No search engines, no
remembered papers. This is `search.verify_sources` applied to the pipeline's own epistemics.

**Freshness.** The register carries a machine-readable `Evidence base last verified: YYYY-MM-DD`
marker. The reviewer compares it to the run date on every review; past twelve months it files one
stably-titled follow-up issue and a non-blocking note — never a blocker, because staleness is the
repository's debt, not any PR's defect. The refresh is a normal PR (human, or the issue-resolution
agent) that re-fetches every reference, searches for superseding work *inside that reviewed PR*,
and bumps the marker — reviewed by the full panel including `quality` itself. Deliberately no
scheduled workflow: the reviewer is the scheduler, the nag persists until paid, and degradation is
honest rather than silent. A principle row may be weakened or retired only with new evidence
fetchable from a URL in the diff (`qual-uncited-retreat` otherwise) — the register must be able to
lose an argument to better evidence, or it is dogma with citations.

**Wiring.** Role enum in `reviewer-v1.json` *and* both id patterns in `fix-result-v1.json` (the
two-schema lesson from the `docs` role); classifier `want_quality`; static caller job plus the
three `needs:` lists and the `reviewed` OR-chain in `review-pipeline.yml`; `quality.md` added to
the prompt-ranges test; `quality-principles.md` added to `is_spec_critical`, the mkdocs nav, and
the DESIGN.md document map. Judge, aggregator, and fixer are untouched — they treat role names as
opaque strings, and needing to change them would have been a design smell.
