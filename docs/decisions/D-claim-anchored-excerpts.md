## D-claim-anchored-excerpts — the evidence critic is shown the part of a page the claim would be in, not the first 6,000 characters

**The finding.** The verification path retained only the first `search.fetch_max_chars` characters
of each extracted page, and `prompts.fetched_sources_block` showed that fixed prefix to the critic.
Claim-relevant text later in an otherwise successfully fetched page was therefore absent from the
critic's evidence. This is the failure QP10 names as silently truncating evidence into apparent
absence. The mechanism is reproducible offline with a synthetic page whose cited passage occurs
after the prefix cap; the excerpt tests exercise that case without publishing run artifacts or user
content.

**The decision.** *Which* characters of a page the critic sees is chosen by the report's claims,
not by position. A new module, `excerpt`, does deterministic string work and nothing else:

* `excerpt.entry_numbers` maps each cited URL to the bibliography number(s) the report lists it
  under — the entry's own `[3]` / `3.` when it states one, its position otherwise — and
  `excerpt.citing_sentences` returns every sentence of the body citing that number, with `[1, 3]`
  and `[2-4]` expanded. Those sentences are the **anchors** for that source.
* `excerpt.select` scans the retained body in overlapping windows, scores each window by the anchor
  tokens it contains — numbers and percentages at four times the weight of content words, years and
  one- or two-digit counts at a word's weight, stopwords and the citation marker itself ignored —
  and shows, inside the same `fetch_max_chars` budget as before: the page's opening (reserved for
  page-identifying context), then the best-scoring windows widened to whole sentences, in document order,
  merged where they touch. A body that fits the budget is shown whole. A source no sentence cites, or
  whose anchors match nothing, is shown from its start, which is exactly what it got before.
* `excerpt.render` states how much of the page is shown, labels each excerpt with its character
  range, and marks omitted text with `[…]`. `fetched_sources_block` labels each entry with the
  report's own bibliography number(s) rather than its index in the deduplicated fetch list, so a
  critic can pair an excerpt with the citation it is checking.

Two configuration values now do the two jobs one used to do. `search.fetch_max_chars` (6,000) keeps
its name and its meaning — characters of page text shown to one critic per page — and gains the
excerpt semantics above. A new `search.fetch_body_max_chars` (120,000, validated `>=
fetch_max_chars`) is how much extracted text is *retained* per page: the pool the excerpts are chosen
from, and what `dispute.adjudicate_mechanical`'s containment test searches — not `support.check`,
which works from `session.reads` and stays capped at `read_max_chars` regardless. The shared fetch
cache stores the larger of `fetch_body_max_chars` and `read_max_chars`; verification's `CappedFetcher`
clips to `fetch_body_max_chars`, so the D-writer-source-reads guarantee — `read_max_chars` never
widens what verification or adjudication sees — holds unchanged with the larger number in the same
place. The per-artifact `source_char_budget` is untouched and now counts what is *shown*, so two
long pages excerpted to 6,000 each are both shown where their raw bodies would have withheld one.

Widening `Runtime.fetcher` to `fetch_body_max_chars` would silently have widened a fourth consumer
too: a dispute's arbiter, which fetches the disputed evidence page through the same handle
(`graph._adjudicate`) and renders it into its prompt verbatim, with no excerpting of its own. An
arbiter's `dispute_upheld` verdict suppresses a defect outright, so that page's untrusted text
matters more per character than a critic's does, not less — it must not grow twenty-fold as a side
effect of a change scoped to verification and mechanical adjudication. `Runtime` therefore gains a
second handle, `dispute_fetcher`: the same `CappedFetcher` class, wrapping the same `source_fetcher`
so no page is fetched twice, but clipped to `fetch_max_chars` like the critic's excerpt rather than
`fetch_body_max_chars` like `fetcher`. `_adjudicate` reads the arbiter's page through this handle,
never through `fetcher`.

**The critic's rule, sharpened not changed.** The block's closing rules now say what an excerpt is,
that a page shown in part is truncated, that a claim missing from the excerpts is *not* evidence the
page lacks it, and that `misrepresented_source` is raised only where an excerpt addresses the same
point and states something materially different. The category's verification-on meaning
(`_CATEGORY_MEANING`) is unchanged. The audition harness passes no source packet
(D-audition-source-mode) and `critic_user` with no `report_text` renders the block exactly as before,
so the audition prompt hash and every cached verdict are untouched.

**Invariants.** None of the six move. What one lens is shown of a page changes; what any lens may
raise, how severities clamp, who reviews whom, what the orchestrator sees and how the controller
terminates do not. Fetched text was already untrusted data fenced into the evidence lens alone, and
an excerpt is a substring of it. Isolation.md's role table needs no edit: the evidence critic still
sees "the pages the report cites, fetched and fenced" — a better-chosen part of them. The arbiter's
untrusted-page budget is explicitly unchanged by this decision (`dispute_fetcher` above); it stays
at `fetch_max_chars`, exactly what it was before `fetcher` widened for verification and mechanical
adjudication.

**Why not the alternatives.**

* *Raise `fetch_max_chars`.* A larger fixed prefix still cannot guarantee that it includes a cited
  passage, and it consumes more of `source_char_budget` per page. Principle #6 (lost-in-the-middle)
  is the reason the per-artifact bound exists, and it applies with more force to a page shown whole
  than to a page shown at its relevant passages.
* *The per-source sub-context D-unbounded-evidence scoped as its follow-up.* Still the right end
  state — every body read, no two sharing a context — and this decision is a step toward it, not away:
  a sub-reader needs the same anchors to know what it is checking, and `excerpt` supplies them. It is
  not done here because it is a new critic surface (an extra model call per source per critic, its
  own schema and validation, its own audition question), and the fixed-prefix defect is addressed
  without it.
* *A mechanical guard in triage that drops a `misrepresented_source` whose figure is in the unshown
  part of the body.* Rejected because it acts after the critic has been given incomplete evidence
  rather than before, and because a finding's `claim_span` need not carry a searchable token.

**Deliberately not done.** No change to the fetch tiers, the byte cap, the timeout or the egress
model — the same bytes are read off the wire; more of them are kept. No change to what writers are
shown through `read_source` (`read_max_chars` is a separate cap with its own decision). No
scope-fit or evidentiary-direction check: the same review found the *other* defect class — a source
whose population is not the question's, a source quoted verbatim for a conclusion it cuts against —
and that is a change to what the evidence lens is asked, recorded separately. No live A/B in this
PR: the measurement is the `misrepresented_source` share of terminal defects on the next runs on
this build, read the way [run-provenance.md](../run-provenance.md) prescribes, and the
`characters shown` header in the critic's own prompt where a run's prompts are inspected.
