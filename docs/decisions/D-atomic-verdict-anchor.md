## D-atomic-verdict-anchor — inherit one verdict/anchor object, never a paired latest value

**Finding.** D-inherit-reviewed-anchor originally published the inherited verdict and its reviewed
SHA in two commit statuses, `review/verdict` and `review/reviewed-sha`, then selected the newest of
each context independently. That is not a transaction. The post-push claim race deliberately permits
a second pipeline to reach the same fixer SHA, and `/review` and `synchronize` runs do not share one
concurrency group. If two such runs finalize with different verdicts or reviewed SHAs, their API
writes can interleave so the newest status in each context forms a pair no run published. The inherit
classifier would then be deterministic over false provenance: one run's verdict attached to another
run's anchor.

**Decision.** The trust input is one `review/verdict-anchor` commit status on `post_fix_sha`. Its
description is `reviewed_sha`; its state encodes the verdict (`success` = GO, `failure` = NO-GO).
The inherit classifier selects the newest object in that context once and derives both facts from
that object. Missing, malformed, pending, or error states review normally. `review/verdict` remains
for human-readable compatibility, but it is not consulted by inheritance. This supersedes only
D-inherit-reviewed-anchor's separate-status pairing; its reviewed-SHA origin, ancestry guard,
unchanged-head case, whole-range walk, and tree-identity recreation are unchanged.

**Tests.** `tests/test_ci_inherit_classifier.py` verifies that finalize writes both facts into the
combined status and models interleaved finalizers by exposing a display GO beside an atomic NO-GO
for the same head. Gather inherits NO-GO from the combined object and never queries the display
status as a second trust input.

**Invariants.** None of the six pipeline-core model invariants changes. This strengthens the CI
merge gate described by QP7 and QP8: the classifier still uses only bounded status fields and git
plumbing, but the provenance tuple it consumes is now atomic rather than assembled across races.
