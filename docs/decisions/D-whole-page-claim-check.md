## D-whole-page-claim-check — a claim is checked against every retained character of its page, so a long page can refute as well as confirm

**The finding.** The claim checker (D-claim-level-verification) showed one fresh context at most
`claim_check.page_max_chars` (30,000) characters of claim-anchored excerpts. For a longer page the
verdict `absent` was `absent_partial` (D-claim-check-inconclusive-verdicts): it minted nothing and
retired nothing, correctly, because absence from excerpts is not absence from the page. The excerpt
selector matches words, so a paraphrased claim, or one whose figure sits in a passage that shares few
words with the sentence, can pull the wrong passages. The consequence is asymmetric: on a long page
the checker can say `supported` or `contradicted` when the excerpts happen to contain the passage,
and can effectively never say "this page does not state this". A bibliography reduced to a few long
documents therefore carried most of its citations past the evidence lens unrefutable, while the
pipeline retains up to `search.fetch_body_max_chars` of each page and the whole of it was available.
Evidence checking is the load-bearing review: the weight of evidence-backed claims is meant to drive
the writing, so the review of that weight cannot be one-sided (QP10).

**The decision.** With `claim_check.whole_page` (default on; `false` is a byte-identical rollback), a
pair whose retained body is longer than `page_max_chars` is checked against **all of it**, chunk by
chunk.

1. **Chunks.** `excerpt.chunk_bounds` covers the body with consecutive ranges of at most
   `page_max_chars` characters. Each range begins `claim_check.chunk_overlap_chars` (default 800)
   characters before the previous one ends, so a passage up to that long straddling a boundary lies
   whole in one chunk; edges snap to whitespace, which only lengthens the overlap. Each chunk is
   shown with a header saying it is section *i* of *n*, its character range, and that text outside it
   may say anything — so a point missing from it is `absent`, never `contradicted`, as the checker's
   prompt already instructs. The prompt is unchanged. Chunks are asked **most relevant first**
   (`excerpt.rank_chunks`: the same distinctive-token weights `excerpt.select` scores windows with,
   summed over the chunk, earlier first on ties), each in its own fresh context holding one sentence,
   its paragraph and one chunk of one page; the closed four-way verdict and the verbatim-span rule
   (`supported`/`contradicted` must quote the chunk shown) are unchanged.
2. **Early stop; no scan past a support.** The first verified `supported` or `contradicted` is the
   pair's verdict. We considered continuing the scan after a `supported` so that a `contradicted`
   elsewhere could take precedence, and decided against it. That scan would cost every supported
   claim a full pass over the page (up to the per-pair cap) to look for a contradiction the claim
   ordinarily does not have, multiplying the common case, and a model asked to find contradictions
   in text unrelated to the claim is the less reliable half of the checker (the prompt itself says
   to prefer `absent` when unsure). The relevance ordering reads the passage most likely to address
   the point first. The residual is stated plainly: a page that supports a sentence in its most
   relevant chunk and qualifies or contradicts it in a less relevant one yields `supported`, and the
   `supported` settles the pair and retires the critic's own `misrepresented_source` finding for it
   (`reconcile`, unchanged). This is the same limit a page shown whole to one context has — a single
   reader may miss a qualification — and is accepted for cost; it is the direction D-claim-level-verification
   already takes, toward the writer. Another critic slot's independent checker still
   forms its own view (QP2).
3. **Whole-page absence.** If every chunk of the body returns a completed `absent`, the pair is
   `absent` with `complete=True`: settled, minting `misrepresented_source` at its existing major
   floor, exactly as a page shown whole does. The conditions are strict and all fail toward the
   writer: every chunk must have been *examined and answered* — a chunk that failed, aborted, was
   skipped because the circuit breaker (`max_consecutive_failures`, which chunk calls feed) opened, or
   was not reached because a cap was hit leaves the pair `absent_partial`; a body that was itself cut
   (`FetchedSource.truncated`: fetch byte cap, PDF page cap, extraction or cache cap) is **never**
   complete however many chunks were checked, because the retained characters are a prefix of the
   page; and `fetch_body_max_chars` truncation sets that same flag.
4. **`unreadable` mixed with `absent`.** An `unreadable` chunk says the checker was shown no document
   text. If every chunk is `unreadable` the pair is `unreadable`, anchored only when every chunk
   quoted a line found in its chunk (D-anchored-unreadable, unchanged). If some chunks are
   `unreadable` and the rest `absent`, the pair is `absent` with `complete=False`: it mints nothing
   and retires nothing. A cookie banner or table of contents chunk is a poor reason to withhold a
   finding, but it is also not a reading of those characters, and "every retained character was
   read" is the condition that makes absence a finding (QP10); fail toward the writer.
5. **Severity and `reconcile`.** No floor changes. A settled whole-page `absent` is `settled`, so
   `reconcile` drops the critic's own `misrepresented_source` finding on the same sentence, as a page
   shown whole already does: the checker read every retained character in fresh contexts and the
   critic read excerpts of it. A partially checked pair settles nothing, as before.
6. **Cost bounds.** Per pair, at most `claim_check.max_chunks_per_pair` (default 6, enough for the
   shipped 120,000-character body bound at the default chunk size) chunks are examined, most
   relevant first; per critic per artifact, at most `claim_check.max_chunk_calls` (default 150)
   checker calls are spent on chunks. Memo hits cost no call. A pair that hits either cap stays
   `absent_partial` if no verdict was reached, and is counted in `chunk_cap_hit`. Both are
   anti-pathological ceilings, not spend caps (QP7, QP10): they refuse explicitly rather than
   silently truncating evidence into apparent absence. Neither is in `_run_fingerprint`.
7. **Observability.** With the knob on, `ClaimCheck.counts()` and the `claim_check` event add
   `absent_whole_page` (pairs settled `absent` through chunks; they are also counted in `absent`),
   `chunk_calls` (checker calls spent on chunks, not memo hits) and `chunk_cap_hit` (pairs that hit
   a cap). Every existing count keeps its meaning. With the knob off the event is unchanged. The
   per-pair record gains `chunked`, `chunk_calls` and `cap_hit`.

**Caching the assessment — analysis and conclusion.** Chunking multiplies calls, so whether a
*judgement* (not the page text, which is already cached) may be reused is a real question.

- **Within a run — implemented.** The verdict cache is keyed on
  `(critic resolved identity, sha256(system prompt), sha256(user prompt), "v4")`. The user prompt holds the
  sentence, its paragraph, the source number and URL, the date line and the chunk text with its
  range header, so the key is (identity, prompt version, chunk text hash, sentence + paragraph hash)
  in effect. A checker call is a pure function of those inputs, so re-running it on an unchanged
  sentence against an unchanged chunk buys sampling noise and nothing else. The version was bumped
  from `v3` because chunk prompts are a new family of inputs. Failed, aborted and unchecked results
  are never cached. A cached verdict is never a clean record: clearance is minted from the completed
  review (RC-002). A page that changes by one character changes only the chunks it touches, or all
  of them if the chunk edges shift; the miss is safe either way.
- **Across runs — rejected.** The hit rate is low (sentences and pages change between runs, except a
  rerun of the same question), and every safety condition it would need is either not available or
  creates a worse problem. (a) The key must include the resolved model identity, and a floating alias
  must disable caching; a deployment can route to a floating alias (a `-latest` name) and nothing in
  the repository can say reliably that a given string is or is not floating, so a heuristic would
  fail open exactly when a silent upgrade happens. (b) A prompt + schema + verdict-semantics version
  could be keyed, but every change to what `absent` or `supported` means would have to remember to
  bump it. (e) Storage must stay private and respect ownership (D-id-as-credential, authentication.md):
  a content-addressed verdict store shared across runs lets one run's existence-check reveal that
  another run checked this sentence against this page, which is a cross-owner side channel, and a
  per-owner store is a new persistent store with its own retention. (f) Independence: a frozen single
  draw replaces fresh sampling (QP2), so a wrong verdict persists across runs. That is acceptable for
  neither side: a cached `supported` retires critic findings, and a cached `absent` mints findings,
  and both would then be reproduced without any second look. A TTL (d) bounds the age but not the
  persistence of a wrong draw. With the saving small and the failure mode silent, the cross-run tier
  is not built, not even default-off: shipped-but-off code is a decision someone later flips without
  re-reading this paragraph. Revisit only with a pinned (non-floating) identity registry and a
  measured hit rate.
- **A per-page "what this page asserts" index — assessed, not built.** A question-independent
  summary keyed by body hash, used only to *order or skip* chunks. As an ordering hint it is safe
  (the chunk is still read) but buys little over token-weight ordering, at the price of a model call
  per page and a model-written summary whose accuracy is the component in doubt (QP10: a model's
  recollection of a source is testimony). As a *skip* it is unsafe: skipping a chunk on the strength
  of a summary is absence inferred from the summary, not from the characters. If ordering proves
  inadequate on measured data, revisit as ordering only.

**Invariants.** None move. *Author exclusion, blind orchestrator:* unchanged — the checker is still
the evidence critic's own model, the controller sees nothing new, and the new counts are counts.
*Fail-closed lenses:* every chunk failure leaves the pair `absent_partial` or `unchecked`, adding no
finding. *Severity floors:* untouched; minting is at the category floor. *Termination:* no controller
rule, no `ControllerInput` or `OrchestratorView` field, and calls are bounded per pair and per pass.
*Untrusted text:* the chunk header is pipeline-authored inside the fence the page text sits in; the
finding text is built as before from the sentence and the checker's bounded reason, never page text
beyond the existing verified-span rules.

**Deliberately not done.** No parallelism across chunks (D-claim-level-verification's reason
stands). No contradiction scan after a support (item 2). No cross-run verdict cache and no page
assertion index (above). No change to the checker prompt or to `ClaimVerdict`. No change to the
critic's own excerpt-based review of long pages, which remains a reading of excerpts and so still
cannot assert absence.
