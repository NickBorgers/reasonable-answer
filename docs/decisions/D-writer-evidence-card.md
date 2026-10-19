## D-writer-evidence-card — a writer reads a page through a per-source evidence card, never through a model's summary of it

**The finding.** D-read-before-citing made a writer read every page it cites, and
D-unreadable-citation-finding made a claim resting only on unreadable pages a finding. Neither
changed *what a read shows*. `read_source` hands the writer the page's first
`search.read_max_chars` (6,000) characters, while the run retains up to
`search.fetch_body_max_chars` (120,000) of the same body and the claim checker is shown up to
`claim_check.page_max_chars` of claim-anchored excerpts chosen from all of it
(D-claim-anchored-excerpts). The writer — the role that decides what to claim — has the narrowest
view of a page anywhere in the pipeline. A figure past the opening is invisible to it, which is the
writer-side twin of the critic-side defect D-claim-anchored-excerpts removed: a fixed prefix
silently truncates evidence into apparent absence (QP10). A writer that can only see openings
either cites the page for what the opening suggests, or keeps searching for a page whose opening
happens to carry the point. The whole-run `read_char_budget` compounds it: a writer reading
openings spends 6,000 characters per page whether or not the opening bears on the question.

**The decision.** Behind `evidence_reader.enabled` (code default **off**), effective only when the
run both reads (`search.read_sources`) and verifies (`search.verify_sources`) — the gating every
reading feature uses — a full-text `read_source` runs one fresh, isolated reader pass over the
page's whole retained body and returns an **evidence card** in place of the opening. This is the
per-source sub-context D-unbounded-evidence scoped as its follow-up, applied to the *writer's* read
path: every retained body is read, and no two pages ever share the reader's context.

1. **Isolation: page text and closed labels, nothing else.** The reader's schema (`ReaderOutput`) is
   `page_kind` (an enum: `document_text`, `abstract_or_summary`, `landing_or_catalog`,
   `paywall_or_login`, `navigation_or_listing`, `other`) and a list of passages, each a `quote`
   string and a `kind` enum (`finding`, `figure`, `method`, `scope`, `limitation`, `definition`,
   `other`). `evidence._locate` keeps a quote only when it occurs in the chunk the reader was shown
   — exactly, or differing only in whitespace and letter case, in which case the **page's own
   characters** are kept, not the reader's. A quote not found is dropped, never repaired; a quote
   over `passage_max_chars` is dropped, never clipped, because a verbatim prefix can lose the
   qualification at the end of its sentence. So the only model-authored *text* that reaches a
   writer is none: what it receives is a subset of the page — the class of text `read_source`
   already delivered — fenced by `prompts.evidence_card_block` exactly as `source_read_block` fences
   a raw read, with the untrusted-data note repeated inside the block and every interpolation
   marker-scrubbed (D-fence-scrub-all-directions), plus labels from a fixed list. *Alternative
   rejected:* a bounded, fenced model summary or per-passage "why this matters" line. That is a
   channel by which an untrusted page could address the writer in a model's voice rather than its
   own — laundering, which RA-010 exists to refuse — and it would put a second model's judgement of
   the page into the context that decides what to claim. The labels are advisory and say so in the
   card's closing line; `page_kind` never changes a `SourceOutcome`, the read log or any finding,
   because a model's classification of a page is not a mechanical fact about it (that channel is
   claim_check's `unreadable`, D-unreadable-citation-finding).
2. **Who reads: the drafting writer's own alias, in its own context.** The reader is bound per
   writer call in `graph._retrieval_kwargs` to the alias making that call, mirroring how the claim
   checker runs under the evidence critic's own slot (D-claim-level-verification); no roster role
   is added, so no new alias is probed, resolved or audited. Its context holds the run's question,
   the writer's optional `purpose` argument (model output, fenced, at most 300 characters, never
   echoed back) and one chunk of one page the call's `ReadSession` already admitted — never the
   draft, a fix task, another page, a critique or anyone's identity. **Author exclusion does not
   apply**: it governs who may *judge* an artifact (principle #7), and the reader judges nothing — it
   selects page text for the model that will then be reviewed by non-authors as before. Using the
   writer's own model is what keeps a second model's selection out of the writer's evidence.
   *Alternative rejected:* a dedicated `roster.reader` alias. It would add a startup probe and
   identity surface, and make an unaudited third model's judgement the filter on what every writer
   sees.
3. **Failure posture: toward the writer seeing something, and never disguised.** A failed or
   timed-out call (`reader_failed`), a schema violation past one repair, a spent reader-call budget
   (`reader_budget_spent`), a breaker that has tripped (`reader_unavailable`, after
   `max_consecutive_failures` failures in a row, so a dead proxy costs a few timeouts rather than
   one per read) and a pass with no verified passage (`no_verified_passage`) all fall back to the
   raw opening, rendered by `source_read_block` **byte-identically** to a build without this, and
   counted under that closed reason. A card is rendered only for a read the session recorded with
   one, so a fallback cannot be presented as a card. A read whose fetch was not `full_text` never
   reaches the reader. A provider-account refusal is a fallback here like any other failure; the
   writer's own next call meets the same refusal and defers the run as before
   (D-credit-exhaustion-defers), which a tool handler raising mid-loop would not do more cleanly.
4. **Read log: unchanged in meaning.** A card is built only from a `full_text` body, and the
   session records the read with that outcome and the card's passages as its `text`. So
   D-read-before-citing's `read_log` records a card-backed read as the full-text read it is — the
   log names the fetch outcome, which is what it named when the writer was shown 6,000 characters
   of the body — and `support.check` and the manifest pass work from exactly the passages the writer
   saw. Fallbacks are recorded as today's raw reads.
5. **Budget and cost.** `read_char_budget` keeps its definition — page text handed to writers — and
   is now charged the card's characters, not the body's; a partial grant keeps the leading passages
   that fit, and a grant that fits none is `budget_exhausted` as a raw read into an empty budget is.
   At the defaults a card is at most `card_max_chars` (5,000) of `max_passages` (8) passages, so a
   card costs less than a raw opening and carries the page's relevant material from anywhere in it.
   Reader calls have their own hard whole-run ceiling, `evidence_reader.max_calls` (300), charged
   **all or nothing per page** — a card that read half the page would present that half's passages
   as the page's relevant material. **Chunking:** a body longer than `chunk_chars` (40,000) is split
   into consecutive chunks overlapping by `chunk_overlap_chars` (800), one call each; each quote is
   verified against its own chunk, its offset mapped back into the body, overlap repeats merged
   first-wins, the survivors capped in the reader's own order and shown in page order with their
   approximate position. At the default retained body that is at most four calls per page. **Cache:**
   one pass per (resolved writer identity, URL, body, question, purpose, prompt and bounds) per run,
   memoised in the runtime as claimcheck's verdicts are; keyed on identity so one writer model is
   never handed another's selection. *Alternative rejected:* redefining `read_char_budget` as
   "bodies read". The bound exists for the writer's context (principle #6), and the reader's context
   is separate and bounded by `chunk_chars`.
6. **Defaults.** Off in code, so a deployment that never sets it is byte-identical in every prompt,
   tool definition (`read_source_tool` without `purpose`), event field and record. The committed
   deployment roster opts in with a comment; that is inert there, because reading and verification
   are off in it, and it takes effect wherever an operator has turned both on. No production
   observation is cited as warrant for a default; the motivation above is a mechanism argument
   (QP9), in the manner of D-latest-unblocked-selection.
7. **Observability.** The `generate` event carries, only when cards are in force: `cards_built`,
   `card_passages_kept`, `card_passages_dropped` (unfound, out of bounds, or past the card caps),
   `card_fallbacks` (counts by closed reason), `cards_cached`, `reader_calls` and `reader_seconds`.
   Each reader call is a `model_call` with purpose `evidence_reader`. Counts only; no URL, quote or
   purpose enters `events.jsonl` (RA-016). The `startup` event records `evidence_cards`.

**Invariants.** None of the eleven moves. Author exclusion, the blind orchestrator, fail-closed lens
validation, severity floors, termination, dispute blindness, identity, idempotent replay and
cross-model confirmation are untouched: this changes what one writer is shown of a page it was
already allowed to read, not who reviews, what any lens may raise, or what the controller sees. The
untrusted-text invariant is unchanged in kind — page text was already fenced into the writer's
context by `read_source` — and the new reader context receives the same fenced page text the writer
would have, plus the writer's own fenced words. The reader's output reaches the writer only as page
substrings and enums.

**Deliberately not done.** No change to the critics' view, the claim checker's view, the
arbiter's page or mechanical adjudication: each reads through its own fetcher and cap as before.
The same reader could serve the evidence critic's sub-context (the original D-unbounded-evidence
scope); that is a separate critic-surface change. `page_kind` is not aligned with the claim
checker's `unreadable`: that verdict now mints only when anchored to a verified page line
(D-anchored-unreadable), and `page_kind` carries no anchor, which is why it is advisory and never
mints or retires a finding. Anchoring it the same way is a possible follow-up.

**Rollback.** `evidence_reader.enabled: false`, or turning off either `search.read_sources` or
`search.verify_sources`.
