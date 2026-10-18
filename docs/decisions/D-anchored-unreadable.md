## D-anchored-unreadable — an `unreadable` verdict mints a finding only when it is anchored

**The finding.** D-unreadable-citation-finding turned a sentence whose every claim-check pair was
`unreadable` into a major `uncited_claim`. The quality reviewer blocked it (qual-uncited-retreat-1,
QP10): an `unreadable` verdict is a model's unanchored classification, and D-claim-check-inconclusive-verdicts
had said it is "not a reading of the page"; minting a finding whose rationale asserted the response
"is not the work" and "no reader can check it" weakened QP10's rule that an unread body supports no
finding about what a page contains, with nothing mechanical behind it.

**The answer.** The finding is not a claim about what the work says, and it is no longer resting on
the checker's say-so. It states only the observable form of the fetched response, and only when the
pipeline itself can check that form. QP10's "an unread body supports no content finding" therefore
holds unchanged: no finding is minted about the page's content, the claim's truth, or the checker's
opinion of either.

**The decision.**

1. **Span-anchored `unreadable`.** `ClaimVerdict.support_span` on an `unreadable` answer is a verbatim
   line of the page text shown that evidences it is not the document's own text (a login or purchase
   wall, catalog or navigation boilerplate). `claimcheck._check_pair` verifies it with the same
   normalised containment test as `supported`/`contradicted`, against the text actually shown to
   the checker. Found: the pair is *anchored*. Missing or not found: the verdict is still valid and
   still counted `unreadable`, but unanchored, and mints nothing — the pre-D-unreadable-citation-finding
   behaviour. Unlike `supported`, a bad span does not make the verdict unchecked: it is not a claim
   about the sentence, and rejecting it would only turn an honest `unreadable` into a retry loop. The
   span is clipped by the schema's `MAX_SPAN`.
2. **Fetch-outcome anchoring.** A pair whose fetch outcome is `paywalled`, `blocked`, `metadata_only`
   or `unreadable` (content type) is anchored without a span: the pipeline observed it. Such
   pairs never reach the checker today (`FetchedSource.ok` is false, so they are `unchecked:
   page_not_read`); they are marked anchored there. `error`, `empty`, `not_found` and the budget
   outcomes are not anchored: they say nothing about what the page is (a not-found has its own
   finding, D-notfound-fabrication). A fetch can also succeed with a `full_text` outcome on a catalog,
   paywall stub or landing page; only (1) can catch those, which is why (1) is primary.
3. **Sentence rule.** Every pair of the sentence must be an anchored non-body. Any `supported`,
   `contradicted` or `absent`, any unanchored `unreadable`, any other `unchecked`/`aborted`, or a
   pair cut by `max_pairs`, leaves the sentence alone.
4. **Text.** Fixed prose from entry numbers: the cited pages, as fetched, came back as access-wall,
   catalog or navigation text or with no readable body, so the claim cannot be checked against its
   citation here. It no longer says "no reader can check it" or that the response "is not the work".
   Never page text, the span or the checker's `reason` (docs/isolation.md).
5. **Counts.** `ClaimCheck.counts()` gains `unreadable_anchored` beside `unreadable`; the
   `claim_check` event carries both. Verdict records carry `anchored`. The verdict-cache key version
   is bumped, and the system prompt changed, so no pre-change verdict is reused.
6. **Knob and the rest.** `claim_check.unreadable_finding` still gates the whole finding; `false`
   restores `unreadable` minting nothing. Category, severity floor, per-slot scope and `reconcile`
   (an `unreadable` retires no critic finding) are unchanged.

This supersedes, in place, rule 1 and the text of rule 5 of D-unreadable-citation-finding.

**Invariants.** *Fail-closed lens validation:* anchoring is stricter than before; unanchored mints
nothing. *Severity floors:* unchanged. *Termination:* unchanged, still dischargeable. *Untrusted
text:* neither page text nor the span enters the finding. *Author exclusion / blind orchestrator:*
untouched.

**Deliberately not done.** No retry when an `unreadable` span is missing. No heuristic classifier of
page text in the pipeline. No cross-slot agreement. The run label's "bodies read" count is unchanged.
