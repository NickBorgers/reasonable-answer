## D-notfound-fabrication — a definitive not-found is `fabricated_citation`, settled mechanically; every other failed fetch is not

**The problem.** Source verification (D-source-verification) fetches the pages a report cites and hands them to the
evidence lens so `fabricated_citation` can mean *the URL does not resolve* rather than *implausible
on its face* (the convergence table). But the prompt rendered **every** failed fetch identically —
`COULD NOT FETCH: <error>` — and then told the critic, correctly for a 403/timeout/paywall,
*"never raise a defect on the basis of a failed fetch."* An HTTP **404** (and 410 Gone) is not
"could not read"; it is "does not exist" — the single status that proves the URL does not resolve.
Lumping it with the unreadable class laundered the one signal that establishes fabrication, so the
evidence lens was instructed to ignore exactly the fact that would flag it. `docs/convergence.md`
carried the same contradiction: its table said a non-resolving URL is `fabricated_citation` while
the paragraph below said a failed fetch is never evidence of fabrication.

The failure mode is concrete and reproducible: a writer that runs zero searches with retrieval
enabled fills `## Sources` from parametric memory, and if every one of those cited URLs returns HTTP
404, the old prompt led the evidence lens to raise **zero** issues on it, because it had been told
to. `fabricated_citation` floors at `blocking` (taxonomy), so suppressing it is what converts a
wholly-fabricated bibliography into a clean evidence lens and ships it. This diff pins the regression
publicly rather than resting the claim on private run audit material: `tests/test_fetch.py` replays a
twelve-of-twelve-404 fetch result and asserts the evidence lens does **not** come back clean, while a
twelve-of-twelve-403 run still does — so the empirical claim above is checkable from the diff itself
(QP9).

**Decision.** Split *unreachable* from *unreadable*. A cited URL that returns a definitive not-found
(HTTP 404 or 410) yields a `fabricated_citation` **mechanically** — raised in the fetch path
(`triage.mechanical_citation_issues`, called from `graph._critique_one`), where the fetch already
happens — so the finding is a fact the pipeline reports, not a judgement a critic model must elect
to make. This mirrors `dispute.adjudicate_mechanical` (a citation category a fetched page settles
without an arbiter) and QP10 (verification is fetched text, never parametric memory). The finding is
attached only to a **completed** review; a failed lens is discarded and re-critiqued (rule 2), and
because the per-run fetch cache is warm the finding is simply re-derived on the next attempt, so
nothing is lost and no failed-lens result is silently promoted to countable.

Every other failure class — 403, a connection error/timeout, an unreadable content type, an empty
body — is unchanged: no defect, surfaced honestly. The distinction rides on `FetchedSource.unresolvable`
against `NOT_FOUND_STATUSES = {404, 410}`. *(Reconciled by PR #96: `unresolvable` now reads
`outcome is SourceOutcome.NOT_FOUND` rather than re-deriving from `status`, so the definition cannot
drift from the closed vocabulary and a not-found established without an HTTP code — a registry that
has never heard of the identifier — reaches triage too; and the failure classes are now each surfaced
under their own `SourceOutcome` label rather than one flat "could not fetch".)*

**Preferred over prompt-only.** The issue offered a fallback (approach 2): stop laundering the
status in the prompt and let the critic raise it. Rejected as the primary route because
`fabricated_citation` floors at `blocking` and forces `needs_human_review` — too consequential to
leave to a critic model choosing to make it, when the fetch already proves it. The not-found
escalation is therefore the pipeline's job, not the model's — the critic still judges
misrepresentation and on-its-face plausibility and must not double-raise on a fetch failure.
*(At D-notfound-fabrication this was expressed as "the critic prompt is left unchanged: 'never raise a defect on the
basis of a failed fetch' stays correct." PR #96 reconciled the wording: because triage now mints the
finding, the evidence-lens prompt stops sharpening `fabricated_citation` toward the critic at all —
inviting the critic to raise it as well would double-report one defect, both copies at the blocking
floor — and instead tells the critic a not-found has already been recorded mechanically and must not
be raised again. The safety property D-notfound-fabrication established, that mechanical minting never depends on a
critic electing to act, is unchanged and is pinned by
`test_a_not_found_source_is_not_offered_to_the_critic_to_raise_again`; `misrepresented_source` still
sharpens, and only when a source's body actually arrived.)*

**Spec.** `docs/convergence.md` no longer contradicts itself: the not-found row of the verification
table is now explained as mechanical, and the "a failed fetch is never evidence of fabrication"
paragraph is scoped to the failure classes it was written for — the `run-75eb136b9bfb`
future-dated-citation failure mode it guards against (a *judgement* about date plausibility, D-run-date-grounding) is
untouched and must not return. The RA-019 test matrix row is updated and populated: 404/410 →
mechanical blocking finding; 403/timeout/unreadable/empty → no defect (each pinned); the
twelve-of-twelve-404 regression asserts the evidence lens does not come back clean.

**Invariants.** Fail-closed lenses, severity floors clamp-up-only, blind orchestrator, and author
exclusion are all preserved: the mechanical finding is a normal `fabricated_citation` at its
existing floor, its text reaches only the writer-facing `Defect` and the audit store (never
`OrchestratorView`, which stays counts-only), and it never touches who may critique what.
