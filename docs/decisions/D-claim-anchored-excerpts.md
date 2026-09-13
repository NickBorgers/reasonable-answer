## D-claim-anchored-excerpts — the evidence critic is shown the part of a page the claim would be in, not the first 6,000 characters

**The finding.** A production run on the question *"Does it use fewer natural resources to use
white cotton bath towels and throw them away every year vs dark towels replaced every four years?"*
(`run-4783c2d9cb81`, build `417fbc9`) shipped `needs_human_review` with four blocking or major
defects standing. An expert review of the report read the cited pages and found that three of the
four were wrong: the EEA briefing cited as `[1]` states the 80% / 14% / 3% life-cycle split verbatim,
and the Lindström article cited as `[5]` states "production is 50% of water consumption after 100
washes" verbatim. The evidence critic had filed `misrepresented_source` against both, with rationales
of the form *"source [1] does not state that the production phase accounts for 80%"* and *"the fetched
text for source [5] is a landing page that mentions a new study but does not provide the actual
data"*.

The mechanism is in the fetch path, not the model. `search.fetch_max_chars` (6,000) capped the
extracted text of each page, and `prompts.fetched_sources_block` showed the critic the *first* 6,000
characters. Fetching those two pages through the project's own `fetch.SourceFetcher` with the cap
lifted: the EEA page extracts to 31,230 characters and its 80% figure sits at offset 10,313, the 14% and
3% figures at 14,155–14,342; the Lindström page extracts to 14,181 characters and its 50% figure sits at
offset 8,689. The critic was shown navigation, a cookie notice and the page's introduction, was told
"the page text is truncated — if the claim plausibly appears in a part you cannot see, do not raise
an issue", and raised the issue.

That is not one run's bad luck. Across the fifteen runs finished on prod between 2026-09-10 and
2026-09-13, the terminal `outstanding_defects` lists carry 22 `misrepresented_source` findings with a
resolvable citation. Refetching each cited page uncapped and searching it for the numeric tokens of
the finding's own `claim_span`:

| where the claim's figures sit in the page | findings |
|---|---|
| **all** past the 6,000-character cap | 5 |
| **some** past the cap, some within | 5 |
| within the cap | 3 |
| absent from the body altogether | 4 |
| page could not be fetched today | 2 |
| claim carries no numeric token to search for | 2 |
| citation id resolves to no entry | 1 |

Ten of twenty-two were findings against a page that states the figure — past the point the critic
could see. This is the failure QP10 names in so many words: a bound that *"silently truncat[ed]
evidence into apparent absence"*. The prompt-level mitigation D-source-verification put in place (tell
the critic the text is truncated) does not work with these critics, and there is no reason to expect
a stronger sentence to work either: a critic that has been handed a page and finds the claim absent
from it is doing exactly what it was asked to do.

These figures come from the operator's own `audit.json` trail and are the motivation, not the warrant
(QP9). The warrant is the code path, checkable offline: the critic is shown `text[:6000]` and the
figures are past 6,000.

**The decision.** *Which* characters of a page the critic sees is chosen by the report's claims,
not by position. A new module, `excerpt`, does deterministic string work and nothing else:

* `excerpt.entry_numbers` maps each cited URL to the bibliography number(s) the report lists it
  under — the entry's own `[3]` / `3.` when it states one, its position otherwise — and
  `excerpt.citing_sentences` returns every sentence of the body citing that number, with `[1, 3]`
  and `[2-4]` expanded. Those sentences are the **anchors** for that source.
* `excerpt.select` scans the retained body in overlapping windows, scores each window by the anchor
  tokens it contains — numbers and percentages at four times the weight of content words, years and
  one- or two-digit counts at a word's weight, stopwords and the citation marker itself ignored —
  and shows, inside the same `fetch_max_chars` budget as before: the page's opening (title, date and
  scope live there), then the best-scoring windows widened to whole sentences, in document order,
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
from, and what `dispute.adjudicate_mechanical`'s containment test and `support.check` search. The
shared fetch cache stores the larger of that and `read_max_chars`; verification's `CappedFetcher`
clips to `fetch_body_max_chars`, so the D-writer-source-reads guarantee — `read_max_chars` never
widens what verification or adjudication sees — holds unchanged with the larger number in the same
place. The per-artifact `source_char_budget` is untouched and now counts what is *shown*, so two
long pages excerpted to 6,000 each are both shown where their raw bodies would have withheld one.

Replayed on the motivating run's two pages, the excerpter puts the 80%, 14% and 3% figures in front
of the critic at 5,618 of 6,000 characters, and the 50%-after-a-hundred-washes figure at 5,886 —
seven and eight excerpts respectively, the deepest at offset 14,772.

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
sees "the pages the report cites, fetched and fenced" — a better-chosen part of them.

**Why not the alternatives.**

* *Raise `fetch_max_chars`.* Ten of the twenty-two pages ran 13,000–44,000 characters; a cap that
  holds them all is a 50,000-character-per-page context, and `source_char_budget` would then withhold
  every page but the first on any bibliography over one entry. Principle #6 (lost-in-the-middle) is
  the reason the per-artifact bound exists, and it applies with more force to a page shown whole than
  to a page shown at its relevant passages.
* *The per-source sub-context D-unbounded-evidence scoped as its follow-up.* Still the right end
  state — every body read, no two sharing a context — and this decision is a step toward it, not away:
  a sub-reader needs the same anchors to know what it is checking, and `excerpt` supplies them. It is
  not done here because it is a new critic surface (an extra model call per source per critic, its
  own schema and validation, its own audition question), and the measured defect is fixed without it.
* *A mechanical guard in triage that drops a `misrepresented_source` whose figure is in the unshown
  part of the body.* Rejected because it acts after the critic has been misled rather than before, and
  because a finding's `claim_span` does not always carry a searchable token (two of the twenty-two did
  not).

**Deliberately not done.** No change to the fetch tiers, the byte cap, the timeout or the egress
model — the same bytes are read off the wire; more of them are kept. No change to what writers are
shown through `read_source` (`read_max_chars` is a separate cap with its own decision). No
scope-fit or evidentiary-direction check: the same review found the *other* defect class — a source
whose population is not the question's, a source quoted verbatim for a conclusion it cuts against —
and that is a change to what the evidence lens is asked, recorded separately. No live A/B in this
PR: the measurement is the `misrepresented_source` share of terminal defects on the next runs on
this build, read the way [run-provenance.md](../run-provenance.md) prescribes, and the
`characters shown` header in the critic's own prompt where a run's prompts are inspected.
