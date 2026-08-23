## D-observed-source-coverage — the run reports what verification reached, not that verification was switched on

**The problem.** `graph._finalize` derived its sourcing label from a runtime boolean. With
`search.verify_sources: true` every run shipped as *consensus-reviewed with verified sourcing*,
whatever the fetches actually returned. The failure is structural: the label described enabled
configuration while `fetch_sources` counted only URLs already selected for fetching, so neither
recorded the bibliography's denominator or whether each entry was checked. The repository's
offline mixed-outcome fixture in `tests/test_source_coverage.py` pins the resulting distinction:
addressable, unaddressable, body-read, metadata-only, blocked, and not-found entries can coexist in
one artifact, while a boolean label cannot report any of those observed differences. That is the
failure mode the labelling discipline of D-in-artifact-citations, D-retrieval-opt-in and
D-source-verification exists to prevent.

**Decision.** Coverage is measured, keyed to an artifact, persisted, and rendered; the categorical
label is replaced by the measurement.

*Measured.* `fetch.coverage` reads the shipped draft's own `## Sources` section and tallies it in
**entries**, not in fetches: `cited`, `addressable` / `not_addressable`, `attempted` /
`not_attempted`, and a disposition for each attempt — `body_backed_entries`, `metadata_only`,
`blocked_or_unreadable`, `not_found`, `budget_exhausted`. `bodies_read` is deliberately outside that
entry partition: it counts distinct cited URLs whose body was read. `existence_confirmed` is derived
from body-backed entries and registry hits. `not_independently_checked` is derived only from
`not_addressable`, `not_attempted`, `blocked_or_unreadable`, and `budget_exhausted`; a definitive
`not_found` is an independent determination of absence, not an unchecked entry. Both derived values
are written from those counts so the summary line and the breakdown cannot disagree. Two entries
citing one URL are two things the report stands on, but the per-run fetch cache collapses them into
one call, so the record says two body-backed entries and one body read rather than calling one body
two.

*Keyed to an artifact.* The tally is taken in `_critique_one` where the evidence lens fetches, and
written into checkpointed state under the artifact's hash — never latest-wins. On a non-accepted
terminal `_finalize` ships the best-scoring draft, which need not be the last one written (issue
#93), so coverage keys the same way the outstanding-defect list does. A draft with no entry reads as
*not recorded*, which is neither zero coverage nor a pass.

*One record per artifact, however many critics read it.* D-front-loaded-depth gives each lens
`review.depth` critics per pass, so at the shipped default the evidence lens tallies the same
bibliography twice, concurrently, against a fetch cache that is monotone but last-write-wins
(`fetch.SourceFetcher.fetch`). Two critics that both miss the cache on the same URL can therefore
observe genuinely different outcomes, and no aggregate of the two exists to read back afterwards.
`graph._record_coverage` keeps the observation that **reached furthest** rather than whichever
thread happened to finish first. Its total ordering compares entries independently checked,
distinct bodies read, body-backed entries, registry confirmations, definitive absences and the
remaining disposition counts, with a canonical-record tie-breaker for future fields. Equal-reach
but different observations therefore cannot fall back to arrival order. Taking the maximum is not
the same as claiming both critics saw it: it is the honest reading of "what did verification reach
for this draft", which is a question about the run, not about a critic. A record update and its
`source_coverage` event execute under the same lock, so two callbacks cannot interleave and the
**last** event for an artifact is always the one `final.json` carries. This is deliberately not true
of `fetch_sources`, which still fires once per evidence critic and therefore double-counts a
depth-2 pass; it was never a coverage measurement, which is the gap this decision exists to close.

*Persisted and rendered.* `final.json` gains `source_coverage`; `export.Provenance` carries it; the
markdown export, the HTML export and the run page render the same breakdown from one definition, as
they already do for the defect list. Every row is a bounded non-negative integer derived from the
artifact's own text — no URL, no page text, no model identity — so the record is safe for the audit
trail on RA-016's terms, and `OrchestratorView` is untouched: the controller still sees none of it.

*The label.* With verification on, the label is now the observation —
`consensus-reviewed — source review: 15 cited; 3 addressable; 3 existence confirmed; 3 source bodies
read (backing 3 cited entries); 12 not independently checked`. Verification on with nothing recorded
says exactly that rather than falling back to the old wording, because an absent measurement must
not read as a passing one. The two non-verification labels are unchanged: neither ever claimed
verification, so neither was overstating anything — but their coverage is still measured and still
rendered, so a retrieval-only run now states in its export how much of its bibliography went
unchecked, which its label never could.

**What the numbers must not be read as saying.** Two misreadings are invited by the counts and
foreclosed in the rendering, which carries the caveat under every breakdown. An entry that was not
independently checked is *unverified*, not suspect. A `blocked` or `paywalled` entry was
*unreadable*, not absent — reading it as absence is precisely the inference D-notfound-fabrication
forbids. A definitive not-found is independently checked and establishes that a cited page does not
exist. The existence-vs-body doctrine of D-existence-vs-body survives intact in the columns:
`metadata_only` confirms existence and is counted separately from `body_backed_entries` and
`bodies_read`, because a registry record is not the source's text. Entry counts and distinct-body
counts are labelled separately everywhere they render.

**Where the measurement is deliberately conservative.** Entry splitting is a heuristic over
model-written markdown — list markers where the section has them, one entry per line where it does
not — and "addressable" means *carries an http(s) URL*, so a bare `doi:10.…` with no resolver URL
counts as not independently addressable. Text before the first list marker is treated as section
prose, so a URL there may be fetched without entering the bibliography denominator. All three
choices err toward reporting **less** coverage than was achieved, which is the only direction a
claim about verification is allowed to be wrong in. The counts are therefore reported as observed,
never as a completeness claim.

**Deliberately not done.** No controller change: coverage does not gate acceptance, does not enter
`OrchestratorView`, and mints no defect. No deduplication of `fetch_sources`, which fires once per
evidence critic and so reports a depth-2 pass twice — pre-existing, visible only in the audit trail,
and a change to an event this decision does not own. A bibliography that is entirely unaddressable is a fact the
export now states, not a blocking finding — turning it into one is a severity-floor decision with
its own failure modes and needs its own entry. No new fetching at finalize: the tally comes from
outcomes the evidence lens already produced, so `_finalize` still performs no I/O and a resumed run
reports the coverage its checkpoint carries. No change to `search.max_sources`, whose truncation is
now visible as `not_attempted` rather than fixed.
