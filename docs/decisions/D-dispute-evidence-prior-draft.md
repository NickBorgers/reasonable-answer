## D-dispute-evidence-prior-draft — a dispute's evidence URL is checked against the draft the finding was raised against, not the disputing writer's own revision

**The problem.** D-writer-disputes' mechanical path and its arbiter evidence-fetch gate both read
"the report" to decide whether a dispute's `evidence_url` is one the report already cites — and
both read it from `state["report"]` at the point `_adjudicate` runs. But `_adjudicate` sits on the
one-way `generate → adjudicate → critique` edge: by the time it runs, `state["report"]` is already
the disputing writer's own just-written revision, not the draft the critics reviewed when they
raised the finding. A writer could add an arbitrary URL to its own revision's `## Sources`,
dispute a `fabricated_citation`/`misrepresented_source` finding citing that URL as evidence, and
have `dispute.adjudicate_mechanical` (`cited = fetch.extract_source_urls(report_text)`, matched
against the writer's own text) uphold it mechanically with **no model's judgment at all** — or, if
mechanically inconclusive, have the arbiter evidence-fetch gate (`graph.py`'s `_adjudicate`) fetch
that same self-supplied page and hand its text to the arbiter as "the cited source," at a page no
raising critic ever had the chance to see. Both call sites carried the identical bug because both
independently derived "the citation set" from the same wrong variable; docs/isolation.md's dispute
passage claimed the opposite ("the mechanical path accepts evidence only from a URL the report
already cites"), true only if "the report" meant the draft the finding was raised against, and the
code never enforced that reading.

**The decision.** Capture the citation set at the moment it is actually correct — `_triage`, when
`defects` are minted from `state["report"]` (the draft that was just critiqued) — as a new
checkpointed state field, `defect_citation_scope: list[str]` (`fetch.extract_source_urls(...)` of
that draft, URLs only, never the draft's text). `_generate` reads `report`/`defects` to build
`pending_disputes` but never writes this field, so it survives untouched from the triage that
minted the disputed findings through to the `_adjudicate` call that rules on them — even across a
resume between `generate` and `adjudicate`, the same guarantee `pending_disputes` itself already
relies on. `dispute.adjudicate_mechanical` now takes `cited_sources: Collection[str]` instead of
`report_text: str` (extraction moves to the caller, so both of `_adjudicate`'s citation-membership
checks — the mechanical gate and the arbiter evidence-fetch gate — share one extraction instead of
each recomputing `extract_source_urls` per dispute). `state["report"]` is still used for the
arbiter's paragraph-context lookup (`_paragraph_containing`) — that one is deliberately the
*current* draft, per its own existing comment, because the run only holds the current text and the
writer was told to leave a disputed span intact.

**Why capture at triage, not the alternatives.** Two others were considered. Looking the prior
draft up by hash from `RunStore` was rejected: the store is write-only from the graph's side (no
`RunStore` method reads a report back), so this would add a new read path into on-disk state a
resumed run does not otherwise depend on, for a value the checkpointed graph state can carry more
simply. Carrying the prior draft's full text in state (e.g. alongside `pending_disputes`, or via
the already-checkpointed `scoreboard` rows `_triage` appends) was rejected too: every consumer of
this value only ever needs URL membership, so storing the full text would duplicate potentially
`max_report_chars` of content in state for no reader, and would invite a future caller to reach for
`report_text` again out of convenience — precisely the mistake being fixed. `defect_citation_scope`
stores exactly what is checked and nothing else, and it does not touch any LLM context — the
mechanical gate never did, and the arbiter evidence-fetch gate's fetched-page text still only
reaches the arbiter when the gate passes, exactly as before.

**Reason persistence.** The same docs/isolation.md passage separately claimed the arbiter's
`reason` "goes to the audit store only" — true of nothing, since no code ever read
`ArbiterVerdict.reason` past the point of receiving it; `graph.py`'s adjudication loop discarded it
after using only `.dispute_upheld`. Persisting it (rather than correcting the sentence to describe
a discard) was trivial: `_adjudicate` now attaches `{"verdict": ..., "reason": ...}` to the same
per-dispute content record `rt.store.dispute` already writes to the purgeable `disputes/`
directory, only when an arbiter actually ruled. It never reaches `events.jsonl` (RA-016) and it
never reaches another model's context — the two properties the original sentence was asserting —
it simply now also reaches disk, which is what "goes to the audit store" is supposed to mean.

**Invariants.** None of the six CI-checked invariants names this mechanism directly, but the
change strengthens the dispute channel's own isolation guarantee (D-writer-disputes, principle 1
of the seven in docs/isolation.md): a writer's own revision can no longer manufacture the evidence
that adjudicates a finding raised against a draft it has already superseded. Arbiter ≠ disputer ≠
raiser, the fenced/labelled dispute prose, the closed two-field arbiter schema, and the
once-per-key registry are all untouched.

**Known residual, accepted:** a URL the *prior* draft cited but that has since gone dead, changed
content, or was itself compromised remains a valid mechanical-adjudication source — the guarantee
is "the critics could have seen this," not "this page is trustworthy," which is the same bound
D-writer-disputes already accepted for the pre-fix (single-draft) version of this check.
