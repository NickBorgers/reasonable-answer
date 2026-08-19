## D-unbounded-evidence — a citation the fetcher never saw is worse than an expensive one

`fetch.extract_source_urls(report_text, limit=search.max_sources)` truncated the cited-URL list at
12. A citation past that never became a `FetchedSource`, so it carried no `SourceOutcome`, never
appeared in `prompts.fetched_sources_block`, and reached the evidence critic as a claim citing `[18]`
with no corresponding entry anywhere in its context. Judging such a citation "on its face" is exactly
what `fabricated_citation` licenses. The loop closes there: the evidence lens raises `uncited_claim`,
the writer adds citations, the bibliography passes the cap, and the unverifiable surface grows.
The defect follows directly from the code path: every addressable citation omitted by the spend cap
is absent from the evidence critic's fetched-source context.

**The decision.** A cap that exists only to bound spend comes out. `search.max_sources` (12),
`search.query_budget` (60/run) and `search.read_budget` (24 calls/run) were all spend controls;
`query_budget` and `read_budget` now default to `None`, meaning unbounded, and the spend-driven URL
truncation is replaced by an anti-pathological ceiling. Every addressable cited URL up to
`search.max_source_urls` is attempted, so every citation inside that ceiling carries a real outcome
and none is silently absent from the critic's view. Entries beyond it are recorded as not attempted.
That silent absence, not the count, was the defect.

The round `budgets.hard_cap` stays. It is a forcing function — a run that cannot answer in eight
rounds should stop and say so — and it is the one bound here that was never about money.

**What replaces them, and on what grounds.** Two bounds remain in this area and neither may be
justified by cost:

* `search.max_source_urls` (200) is anti-pathological. The `## Sources` list is untrusted model
  output and every entry is an egress (docs/ssrf-egress-isolation.md), so a report that emits ten
  thousand URLs must not become ten thousand fetches. It must not bind on an ordinary bibliography,
  and a non-zero `not_attempted` in the coverage stats is a bug signal rather than a budgeting
  outcome. It bounds a bug, not a bill, in the manner of
  `sources.extraction.max_calls_per_run`.
* `search.source_char_budget` (60,000) bounds how much page *text* one evidence context holds. That
  is an efficacy limit and is the repository's own position: principle #6 in
  [isolation.md](../isolation.md) cites Liu et al. 2023 on lost-in-the-middle, `ReadBudget` already
  makes the same argument for writers ("a correctness property here rather than a cost one"), and
  the evidence pool is not made of large models. At the anti-pathological ceiling, even the default
  per-page extraction cap would produce a context far beyond the configured 60,000-character bound.

**Listed is not the same as shown.** A source past the character budget still appears, through a
fourth entry shape in `fetched_sources_block`: `FETCHED, TEXT WITHHELD`, stating that the page was
retrieved and read and that its text is not being shown. It is deliberately neither of the two
shapes that already existed. Rendered as a failed fetch it would invite a fabrication finding
against a page we hold; rendered as registry-confirmed it would claim a corroboration nobody made.
The critic is told to raise nothing about that source rather than infer against it — the same
discipline D-existence-vs-body established for the existence-only state.

**The rename is deliberate.** `max_sources` is gone rather than redefined, and `SearchConfig` forbids
unknown keys, so a deployment still setting it fails closed at load instead of silently keeping a
12-source cap under a decision that says every citation is checked.

**Invariants.** None of the six is in reach. Fail-closed lens validation, author exclusion, the blind
orchestrator, severity floors and the controller's termination rules are all untouched: this changes
what reaches one lens's context, not what any of them may report or how anything is counted. The
untrusted-text boundary is unchanged in kind — fetched pages were already untrusted data fenced into
the evidence lens alone — and unchanged in degree at the point that matters, because
`source_char_budget` means a larger bibliography does not mean a larger critic context.

**Follow-up, already scoped.** `source_char_budget` is interim. The evidence critic should read each
cited source in a *sub-context of itself* and receive a bounded structured verdict — an enum plus a
verbatim span re-checked mechanically against the fetched body — so that every body is read while no
two of them ever share a context. That removes the trade this decision has to make. Note that
`support.check` already performs mechanical claim → span → body verification and is confined to the
audit trail for one stated reason: the *writer* authors the manifest, so it "has no lever on its own
review". A non-author sub-reader does not carry that defect, which is what makes the follow-up
tractable on existing machinery.
