## D-question-refinement — question refinement is offered at the edge, ambient and never blocking

**The problem.** The pipeline already knows questions arrive loaded: `unexamined_presupposition`
(D-social-bias, completeness lens, major floor) exists precisely to catch a writer who accepts a contested
framing as settled. But that machinery fires only after a run is already underway, and the
production run history shows what waiting until then costs. Six runs motivated this decision, and
they fall into four shapes. Two posed a **false either/or** — a political "does X back A or
support B?" and a values question of the "is it better to be honest or nice?" kind — and both
spent their conclusions rejecting the frame rather than answering: 7 rounds to
`needs_human_review`, and `exhausted_unresolved`. One carried an **unverified premise** ("why is
it illegal to do X in Y?") and let it stand uncited while the asker's real, buried question — the
lawful alternative — went unaddressed; 8 rounds, `exhausted_unresolved`. Two asked for a **"net
positive or negative?" scalar verdict** over an unscoped population, outcome set, and timeframe,
which is unanswerable as asked. One asked a **settled verification question** when the report's
real energy went to the adjacent and more interesting question of why the belief persists.

The questions themselves are paraphrased here rather than quoted, and the run IDs left out: they
are a private operator's own queries, and this repository is public. In every case the category
was already nameable — `unexamined_presupposition` (D-social-bias) would tag some of these on sight — but
the finding lands 10–25 minutes and several critique rounds after the one party who could cheaply
reframe the question, the asker, has already walked away from the keyboard. The
fix that costs nothing is upstream: catch the same framing before the run starts, while the asker
is still there to accept, ignore, or edit it.

**The mechanism** (opt-in, `refine.enabled: false` by default):

1. **A closed enum of six transforms**, not free-form rewriting: split-the-either/or,
   check-the-premise-first, name-the-outcome-you-care-about, surface-the-real-goal,
   ask-what's-answerable, and ask-the-question-behind-the-question. Every transform preserves the
   user's subject; the model may change *how* the question is posed, never *what* it is about.
   The sixth — the only transform that lets the model infer an unstated concern rather than
   rephrase what is already on the page — ships **disabled** and is enabled only after a
   paired-fixture audition (mirror questions posed from opposing framings must yield mirror
   suggestions) passes, the same deferred-audition pattern D-social-bias uses for its own cross-critic
   bias-correlation check.
2. **Edge-only placement.** Refinement lives entirely in `web/refine.py`, never inside the graph.
   `_intake` (RA-018) is unchanged; the graph still receives exactly one question and never knows
   refinement existed. This follows the seed-ingestion precedent (PR #25): edge-side
   transformation that is audited but never routes. Keeping it out of `_intake` also keeps the
   resume fingerprint honest — the question the graph fingerprints never depends on a model call.
3. **Server-side offer records are the provenance authority.** Client-submitted
   `refine_offer_id`/`refine_selected` are *claims*, never evidence. They are verified only when
   the offer exists server-side, the index is valid, and the submitted question exactly equals
   that suggestion's stored text. A forged or stale claim degrades to an `unverified` mark; it
   can never fabricate an audit trail, because the record it would have to fake was never written
   from client input in the first place.
4. **Two layers of guarantee.** Enforced: schema validation plus deterministic per-entry checks
   (transform in the enabled set, length caps, ends with `?`, no control characters, no
   duplicates, count bound) — any failure degrades silently to zero suggestions. Prompt policy:
   no meta-commentary, no steering, subject preservation, silence-as-default, one transform per
   suggestion — fixture-tested, never assumed, not enforced.
5. **Retention follows content, not code path.** Full refinement content — question at offer,
   suggestions offered, chosen text — is written to `runs/<id>/refinements/refinement.json`,
   folded into `CONTENT_DIRS` so the existing directory-level content purge removes it alongside
   reports and critiques. `events.jsonl`, which survives purges, gets only hashes and enum
   fields: `{offer_id, transform, selected_index, question_sha256, original_sha256, provenance}`.
   `question.txt` continues to hold exactly the question that ran, per the resume-fingerprint
   rule.

**Alternatives rejected.**
- *An interstitial confirmation step after "Start run"*: makes every user pay a click to benefit
  a minority of loaded questions, and reads as a correction gate ("are you sure?") rather than
  the felt experience of being understood.
- *Refinement inside the graph, at `_intake`*: would make the question the graph fingerprints
  depend on a model call, breaking resume, and would put the reframing decision in the pipeline's
  hands rather than the asker's — the opposite of the intent.
- *Trusting client-submitted provenance*: a `refine_offer_id`/`refine_selected` pair from the
  client is exactly the kind of untrusted input RA-010 already treats every model-adjacent field
  as; recording it as fact would let a forged or replayed claim write a false audit trail.
- *A distinct alias name as an isolation claim*: a different alias name alone proves nothing
  about backend isolation, and can even add contention rather than remove it, via model swapping
  onto the same underlying resource pool.

**Isolation accounting** (the seven principles): refinement is not part of the alternating game —
no writer, no critic, no orchestrator role is added or touched — so principles 2–7 hold by
absence of contact: no new social context, no authorship question, no new lens, no new step
inside the alternating loop, no new context accumulation, no critic-writer coupling. Principle 1
is the one with an honest tension to name: refinement puts a model's words in front of the human
*before* a run exists at all, a channel the pipeline otherwise does not have — there is no
analogous point today where an artifact-first handoff rule even applies, since nothing has been
produced yet. It is bounded the same way every other model-facing channel in this system is
bounded: the transform enum forbids free-form rewriting, the subject is preserved (guardrail 6),
the original wording is always one tap away and always wins ties (never auto-replaced, never
required), and every offered suggestion is written to `refinements/refinement.json`, auditable
per run exactly like a report or critique.

**Known residuals, accepted and recorded:** refinement shares the LiteLLM proxy with runs, so the
honest guarantee is a **small fixed ceiling on live client calls** (`refine.concurrency`), shed
rather than queued when saturated — not zero contention with run traffic, which is a deployment
property (an alias routed to a dedicated backend/resource pool), not something an alias name or
client-side control can provide on its own. A timed-out call's orphan-linger window
(`orphan_linger_seconds`) is best-effort damping on orphan accumulation, not a guarantee — a
stalled backend can outlive any window, since the design does not assume verified
disconnect-cancellation from the underlying proxy/backend. The suggester could itself introduce
spin — a taste for one phrasing over another — mitigated by the closed transform enum, the
prompt-policy guardrails, the highest-risk transform (`question_behind_the_question`) shipping
disabled, and per-run auditability via `refinement.json`, but not eliminated as a possibility. An
expired or evicted offer record downgrades an honest, unforged selection to `unverified`
provenance — indistinguishable, downstream, from a forged one; the cost is a slightly less
informative audit trail, never a false one. Enabling refinement also makes the proxy a **boot**
dependency of the web server rather than only a run dependency — the refine alias joins startup
identity resolution and structured-output probing, so an unreachable proxy stops the UI coming up
at all. That is the intended fail-closed trade (a schema-incapable alias must not first surface on
a user's pause), and it is why the roster baked into the image and the wheel is
`config/roster.default.yaml`, which leaves refinement off so the image still boots with no network
and no credential; `config/roster.yaml`, mounted over it by `compose.yaml`, is where this
deployment opts in.
