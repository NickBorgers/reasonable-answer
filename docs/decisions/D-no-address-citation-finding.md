## D-no-address-citation-finding — a citation nothing can fetch is a finding, not a way out

**The finding.** With source verification on, every evidence check hangs off a fetchable address:
`fetch_sources` fetches the bibliography's URLs, the evidence lens's claim check runs on what was
fetched, and D-read-before-citing's `mechanical_unread_issues` skips entries with no URL because
`read_source` cannot be pointed at them. A writer could therefore delete the URL from each entry while
keeping the entry and its `[n]` markers: nothing was fetched, nothing was claim-checked, no unread
finding applied, and round selection, which prefers the draft with the fewest material findings, then
preferred that draft. The shipped label read as "cited; none addressable; nothing confirmed; nothing
read". This is the shrink-to-pass pattern D-no-shrink-discharge closed for deleted sources, one step
along: the source is kept and its address removed.

**The decision.** Effective when `search.verify_sources` is on and `review.no_address_finding` is true
(default).

1. **Trigger.** `triage.mechanical_no_address_issues` lists the bibliography entries the body cites
   for which `fetch.entry_url` is None, and mints one finding per citing sentence. A bare identifier
   (`doi:...`, `arXiv:...`) is not an address, exactly as `entry_url` already decides. A fact about the
   current draft only: no previous draft, no read log. A draft that strips URLs from the previous
   draft's entries is caught because its own entries have none, not because of a comparison.
2. **Category and severity: `uncited_claim`, `major`**, pipeline-minted through `triage._minted`, no
   taxonomy change, for the reasons D-no-shrink-discharge gives: the claim stands without support a
   reader can check, and the `major` floor makes the clamp a no-op. Multiple address-less entries in
   one sentence fold into one finding naming each label, as the unread finding does.
3. **Gating: `verify_sources`, not read-first.** The escape exists whenever verification is the check
   in force, with or without writer reading, so the finding does not require `read_sources`. With
   verification off nothing is fetched or checked for anyone, and no finding is minted. It is not
   suppressed when the read budget is spent: the exits need no read.
4. **Dischargeable.** The instruction offers: give the entry a full `https://` URL (a doi.org link, the
   publisher's page, an archive copy) that the writer has read, re-source the claim to a page read, or
   remove the claim. It says deleting the address is no resolution. A restored URL is then subject to
   the unread finding, so an entry cannot gain an address and skip reading.
5. **Measurement.** A `no_address_check` event, once per draft on its first critique pass while the rule
   is in force, carries `no_address_entries` and `no_address_findings`, both integers (RA-016).
6. **Selection.** `review.selection: fewest_defects` ranks on `100*blocking + 10*major + minor` over the
   per-artifact triage rows, which count distinct mechanical findings, so the URL-stripped draft now
   scores worse than one with checkable sources (a test pins this). `latest_unblocked` ranks on
   blocking alone and is unaffected by any `major` finding; under it the finding acts through the
   controller's tally and the writer's instruction, not through selection.

**Legitimately URL-less sources (print books).** The finding applies to them too. The doctrine is that
a claim the pipeline cannot read is not evidence-backed, and the pipeline cannot tell a book from a
stripped web source by looking at the entry. A writer citing a book is told to cite an online copy
(publisher page, archive.org record, Google Books page) or to remove the claim. This costs a
legitimate print-only citation; that cost is accepted and stated plainly rather than allowlisted,
because any allowlist (an ISBN, "pp.") would be the next thing to strip.

**Interactions; no sentence gets two findings for one entry.**
- D-read-before-citing, D-unreadable-citation-finding and D-anchored-unreadable: unread and unreadable
  findings concern addressable entries (`unread_cited_entries` skips `url is None`), and claim-check
  pairs exist only for fetched pages; this finding concerns the rest. The sets of entries are
  disjoint. Where one sentence cites an entry of each kind, both findings share category and
  `claim_span`, and `_issue_key` collapses them to one.
- D-no-shrink-discharge: stripping a URL keeps the marker, so no source is "dropped". If the entry is
  deleted instead, the shrink finding applies, with the same category and span, so the tally counts one.
- D-writer-must-read: unchanged; it governs whether a writer read at all, and this finding is derived
  from the draft's text.

**Isolation (docs/isolation.md).** Fields are entry labels and fixed wording; no page text and no
model-authored text. Nothing reaches the controller view or a critic prompt; the finding is consumed as
any minted finding is, through the revision task list.

**Residuals.** (1) An address-less entry the body never cites is a bibliography orphan
(D-bibliography-integrity), not this finding. (2) An address that is a URL but dead or wrong belongs to
the not-found mechanism, unchanged. (3) Under `latest_unblocked` selection this finding does not affect
which draft ships.

**Rollback.** `review.no_address_finding: false`.
