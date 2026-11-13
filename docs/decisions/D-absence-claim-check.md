## D-absence-claim-check — a claim that the evidence is missing must rest on a search for it

**The finding.** A production run was `accepted` with a report saying that "no source reviewed
here" measured the thing the question asked about. That evidence exists and is easy to find. The
writers that shipped those drafts ran no search on that topic, and no critic could object: critics
have no search tool, so to every lens an absence claim looks like a modest, well-hedged sentence.
That observation is the motivation. The warrant is the mechanism, which is checkable in this
repository:

- `WRITER_SEARCH_ADDENDUM` already tells the writer to search before writing "no source addresses
  this" or "this has not been studied". Nothing enforces it.
- D-no-draft-history's instruction tells a writer to state a gap "as a fact about the sources ('no
  source reviewed here reports ...')", which invites exactly this shape of sentence.
- No mechanical check reads a sentence for what it says is *absent*, and no critic can test one.

**Decision.** Effective while `search.enabled` and `review.absence_check` (default true) are both on.

1. **Detection: a fixed phrase list.** `triage.absence_claim_sentences` scans body sentences, with
   the `## Sources` section excluded, for:
   - *no* followed by up to two words and an evidence noun (source, study, data, evidence,
     research, survey, paper, analysis, literature, publication, report, experiment, trial). Not
     when it starts a quantity or an idiom: *no fewer / more / less / longer*, *by no means*.
   - *none / neither of the* followed by an evidence noun.
   - *has / have / had not / never (yet) been (…ly) studied / measured / examined / …*.
   - *remains / is / are (largely) unstudied / unmeasured / …*.
   - *the literature / evidence / research / data is silent / lacking / absent / unavailable*.
   - *gap in the literature*, and *lack / absence / dearth of … data / evidence / research*.

   It is a matching rule, not a judgement.
2. **Backing: a search the writer ran on the topic.** Each `ReadSession` keeps, in memory only, the
   queries its call ran. A query counts only when the tool result was not a `SEARCH FAILED` block,
   so a refused query does not count. When `generate` ships a draft, an absence sentence is
   *backed* if one of the drafting call's queries shares at least `review.absence_min_overlap`
   (default 2) topic words with it. Topic words are the sentence's words with stop words and the
   absence vocabulary removed, and plurals folded. The bar drops to the sentence's own count of
   topic words when that is lower, so a sentence with none is backed by any query. Backing is
   remembered by the sentence's normalised text (`triage.absence_key`) in
   `State.absence_backed`, and it carries to later drafts while the sentence survives verbatim.
   A later writer need not re-search a claim an earlier one checked. An edited sentence is a new
   claim. The repair turn has no tools, so a sentence the repair introduces is unbacked unless it
   was backed before.
3. **The finding.** The evidence result of every completed review gains one pipeline-minted
   `uncited_claim` at its `major` floor per paragraph that holds an unbacked absence sentence. The
   finding is anchored on that sentence. Its rationale and instruction are fixed wording: search for
   the evidence the sentence says is missing; if found, read and cite it; if your searches find
   nothing, the claim may stay; otherwise remove the sentence or narrow it to the sources the report
   cites. It is dischargeable three ways in the report: search, remove, or narrow. Being at the
   floor, it blocks `accepted` like any material finding. No taxonomy row changes, and the clamp is
   a no-op.
4. **Suppressed once the query budget is spent.** Searching could then no longer discharge it, and
   a finding that only deletion can clear is the shrink-to-pass the read-budget gates already
   refuse to teach.
5. **Measurement.** `generate` carries `absence_claims` and `absence_unbacked` for the shipped
   draft. Once per draft, an `absence_check` event records `absence_claims`, `absence_backed` and
   `absence_findings`. All are integers (RA-016). The queries themselves are never written by this
   mechanism.

**Why deterministic word overlap, and not a model.** A model asked "did this search cover this
claim?" would be a judge reading the report and the query, which this pipeline reserves for critics
in fresh contexts. Word overlap is checkable, cannot be argued with, and fails in a known direction.
A lazy query that happens to share two words backs the claim (a false negative). A claim phrased far
from its query does not (a false positive, which the writer clears by searching in the claim's own
words). The point is to make "search before you assert absence" happen, not to grade the search.

**Why critics do not get a search tool.** docs/isolation.md gives web search to writers only.
Critics see fetched pages, and only on the evidence lens, so the fetch boundary stays one place.
This check needs no critic network access. The writer, which already holds the tool, is the one
asked to search.

**Isolation (docs/isolation.md).** The finding carries only `claim_span`, a bounded verbatim
sentence from the current report, plus fixed rationale and instruction. No query text, page text or
critic prose enters it. `State.absence_backed` is report-derived, like `report`, and enters no
prompt, no `OrchestratorView` and no controller input beyond the ordinary severity tally. The
writer's queries are read from its own call's session and never cross to another context.

**Residuals.** (1) The phrase list misses paraphrases ("researchers have yet to look at"), and
narrowing to "the sources cited here do not measure X" discharges the finding without a search. That
is an honest, scoped statement and the intended exit. (2) A query that shares two topic words with
the claim backs it even if it was a poor search. A record of the query text, where one is kept,
is what lets a human judge that. (3) A source that *reports* an absence ("the review found no
evidence of harm [3]") matches too. It is usually backed, because the writer searched to find that
source.

**Rollback.** `review.absence_check: false` removes the finding, the event and the `generate`
fields.
