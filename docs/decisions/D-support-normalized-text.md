## D-support-normalized-text — normalized-empty text cannot establish support

**Finding.** `SupportEntry` bounds the writer's raw `claim` and `support_span`, but quote
normalization intentionally removes markdown punctuation and whitespace. A raw value such as `*`
or a pair of backticks therefore satisfies the schema while normalizing to the empty string, and Python considers
the empty string a substring of every report and source body. The support manifest could record
`supported` without establishing any quoted text.

**Decision.** `support.check` normalizes each claim and span once and requires the normalized value
to be non-empty before testing containment. A normalized-empty claim is `claim_not_in_report`; a
normalized-empty span is `span_not_found`. This mirrors the existing `triage._verbatim` rule for
critic evidence, preserves every manifest entry, and changes no critic, orchestrator or controller
input. `tests/test_support.py` fixes the markup-only and whitespace-only cases at the mechanical
boundary.
