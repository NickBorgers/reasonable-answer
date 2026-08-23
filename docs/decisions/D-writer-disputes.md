## D-writer-disputes — writers may dispute fix-tasks; adjudication is mechanical-first, identity-blind, and fail-closed toward the finding

**The problem.** Critics were structurally unaccountable. A critic false positive is
indistinguishable from a real defect everywhere downstream: triage counts it, severity floors
escalate it (`fabricated_citation` → blocking), the blind orchestrator sees only counts, and the
next writer's only moves are to comply or to stall the run into stagnation. Sample run
`run-75eb136b9bfb` terminated `needs_human_review` after six hours on exactly this: critics with
stale knowledge flagged real recent events as "future-dated fabrications," and the resulting
fix-tasks (*"correct the date to a factual historical date"*) would have made a compliant writer
**falsify true facts** to satisfy a wrong critic. The whole design audits writers three ways per
round and audits critic *positives* not at all.

**The mechanism** (opt-in, `disputes.enabled: false` by default — with it off, every prompt and
transition is byte-identical to a build without the feature, the D-retrieval-opt-in pattern):

1. **Elicitation.** After a non-polish revision, one *separate* structured call to the same
   writer collects `WriterDisputes`: per dispute a `task_index`, bounded `grounds`, and optional
   `evidence_url` + `evidence_quote`. Any failure degrades to "no disputes", never fatal.
2. **Adjudication**, in a new `adjudicate` node on the one-way generate → critique edge,
   mechanical-first: a citation-category dispute whose `evidence_url` is in the
   `defect_citation_scope` captured at triage from the draft the finding was raised against, whose
   fetch succeeds, and whose `evidence_quote` appears verbatim (triage normalization) in the page
   text is **upheld with no model judgment**. Everything else goes to
   an **arbiter**: a fresh-context model whose resolved identity is neither the disputing writer
   nor any critic that raised the finding (raiser identities come from audit-side
   `defect_provenance` state and are consumed by eligibility code only — never a prompt). The
   arbiter sees the depersonalized finding, the one paragraph it points at, the question, the
   fenced dispute (labelled an interested party's argument), and the fetched page when the
   evidence URL is in that prior-draft `defect_citation_scope`, never merely because the disputing
   writer added it to the current revision. It never sees the report body, an identity, the lens,
   or the round, and its tie-break is explicit: **uncertainty resolves in favor of the finding**.
3. **The adjudicated-facts registry**, in checkpointed state, keyed `(category, normalized
   claim_span)` — the triage dedup key minus locus, since paragraphs shift between revisions
   while a verbatim span does not. Each key is ruled once per run. `upheld` records **suppress**
   matching re-raised issues at the top of triage — before `tally`, `clean_records`,
   `to_defects` and the stagnation signature, so counts, clearance and fix-tasks stay consistent
   by construction; every suppression is an audit event. `overruled` records mark the returning
   defect `adjudicated: true` — a bare boolean telling the next writer the task was independently
   reviewed and stands; re-disputes of it are refused. Every other outcome (`no_eligible_arbiter`,
   `arbiter_failed`, `budget_exhausted`, `duplicate`, `invalid`) is a dismissal: **nothing is
   ever suppressed without an explicit upheld verdict.**

**Controller: untouched.** No new `ControllerInput`/`OrchestratorView` field, no new rule. See
the termination note in [convergence.md](../convergence.md): the node adds no cycle, the budget
strictly decreases, suppression only removes counts, and a writer that refuses an overruled task
falls to the existing cycle/stagnation terminals.

**Alternatives rejected.**
- *Extending `structured()` with the tool loop* so one call could revise and dispute: the
  revision path stays byte-identical this way, the dispute call needs no tools (adjudication does
  the fetching), and `response_format`+`tools` together is the combination small rostered models
  fail unpredictably.
- *A neutral arbiter tie-break*: suppression permanently silences a signal for the run, so it
  must be earned; the clear-cut false positives that motivated the feature are exactly the ones
  the mechanical path settles without any model's judgment.
- *Fail-closed startup when some (writer, critic) pair has no possible arbiter*: dismissal
  already fails safe to the status quo ante, so an uncoverable pair costs a privilege, not a
  safety property — it is a startup *warning*, not a `ConfigError`.

**Isolation accounting** (the seven principles): the two honest tensions are principle 1 — the
dispute `grounds` and the finding's `rationale` are reasoning prose entering the arbiter, which
an appeal cannot avoid; bounded because the arbiter is a **terminal consumer** whose only output
is a closed two-field schema, whose `reason` goes to the audit store only, and whose sole
writer-facing residue is one boolean — and principle 5 — adjudication is the system's only
agonistic structure, so it is **one-shot by construction**: no rebuttal, no iteration, a
default-to-the-finding tie-break, and a once-per-key registry that forbids re-litigation.
Principles 2, 3, 4, 6, 7 hold outright (blind parties, no identities in any prompt, one scoped
question, fresh contexts, arbiter ≠ disputer ≠ raiser at resolved identity). One edge accepted:
a disputed span may survive from the disputing writer's own draft two ticks back — the writer is
a party, not a judge, so its stake is expected.

**Known residuals, accepted and recorded:** a later *genuine* defect matching an upheld key is
suppressed for the rest of the run (logged, per-run scope); a hostile *cited* page could carry
text that mechanically upholds a false dispute (bounded by the already-cited requirement, the
once-per-key rule, and the audit trail); with `verify_sources` off there is no mechanical path
and every citation dispute rides on the arbiter; the dispute config deliberately does not join
the resume fingerprint, so toggling it mid-run changes only whether the *privilege* exists going
forward.
