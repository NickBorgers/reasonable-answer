# Convergence — taxonomy, signals, and the stop decision (v3)

The controller decides when the report is sound enough to ship, when further ticks are just
nitpicking, or when substantive disagreement won't resolve. It reads only **signals**, never the
report, and it is bounded so it **always terminates**.

> **Isolation unit = the context window, not the model** (see [isolation.md](./isolation.md)).
> Fresh, blind contexts defeat the *primary* bias (social/context drift) regardless of model;
> a diverse roster is a *secondary* layer that decorrelates model blind spots and enables strong
> same-artifact acceptance. The roster is **role-structured** (D-per-lens-critics/D-critic-only-specialists): a writer pool plus
> per-lens critic pools headed by the model best matched to each lens, sized to give **≥2
> eligible non-author model families per lens** for strong acceptance.
>
> Those two models both read **every** draft (D-front-loaded-depth, `review.depth: 2`): review depth
> is what a pass spends, not what the end of a run collects. See
> [Review depth](#review-depth-both-reviewers-read-every-draft-d-front-loaded-depth) below for
> what that does and does not change about the table.
>
> *Eligible* throughout this document means **structurally** eligible — non-author, distinct
> identity, distinct family — which is all the controller reads. D-critic-audition adds a separate
> **demonstrated-capability** term (`ra audition` grades each critic `fit` / `marginal` / `unfit`);
> under `audition.enforce` a cached `unfit` verdict fails startup closed *before* the graph runs, so
> it never reaches the stop decision below. It gates whether the roster may run, not what any lens
> predicate here means.

## The observable-category taxonomy (RA-006) with mechanical severity floors (RB-006, RC-005)

Every issue carries an **observable category** and a **severity**. The critic proposes a severity,
but **triage clamps it up to a mechanical, category-specific floor** — the critic can only
*escalate*, never downgrade below the floor. There is **no critic-supplied materiality exception**
(removed per RC-005); floors are fully mechanical.

| lens | category | meaning | **mechanical floor** |
|------|----------|---------|----------------------|
| evidence | `fabricated_citation` | citation cannot be what it claims on its face | **blocking** |
| evidence | `misrepresented_source` | cited source does not support the claim as stated — including a source whose own finding cuts against the proposition it is cited for, or whose population/boundary/period is not the claim's (D-source-fidelity-direction-and-scope) | **major** |
| evidence | `uncited_claim` | material claim with no citation | **major** |
| evidence | `one_sided_sourcing` | sources drawn from one outlet or viewpoint cluster where genuine alternatives exist ([bias.md](./bias.md)) | **major** |
| logic | `contradicted_claim` | claim contradicts another claim or a cited source, however many sections apart the two passages sit | **blocking** |
| logic | `invalid_inference` | conclusion does not follow from premises, including a stated derivation that does not yield its own number and an absence-of-evidence step | **major** |
| logic | `overstated_claim` | claim stronger than its support | **major** |
| logic | `conceptual_conflation` | two materially distinct things are treated as interchangeable, and the substitution carries an inference | **major** |
| logic | `loaded_language` | evaluative characterization smuggled in as description, neither attributed nor argued ([bias.md](./bias.md)) | minor |
| completeness | `incomplete_answer` | an explicit, material part of the question is unanswered or replaced by an adjacent question, including a comparative question answered with no magnitude and a comparison whose decisive consideration is never stated | **major** |
| completeness | `omitted_counterargument` | a material opposing view is missing, or a purported opposing case substitutes an easier objection that does not challenge a load-bearing conclusion | **major** |
| completeness | `unclear_structure` | organization/clarity impedes evaluation | minor |
| completeness | `unexamined_presupposition` | a contested premise of the question is inherited rather than surfaced and examined ([bias.md](./bias.md)), or one reading of an ambiguous question is answered without saying which | **major** |
| any | `stylistic` | cosmetic preference | minor (**ignored** for convergence) |

The three social-bias categories are constrained by their own rulebook — what a bias finding may
and may not be (span-anchored, no viewpoint quotas, no intent attribution) — in
[bias.md](./bias.md); they enter triage, floors and the counts exactly like every other category.

**Severity floor for convergence = `major`.** `material = blocking + major`. Convergence requires
`material == 0`; `minor`/`stylistic` never block. (Flooring `overstated_claim`/
`incomplete_answer`/`omitted_counterargument` at `major` is deliberately conservative. `SEVERITY_FLOOR`
is a hardcoded constant (`taxonomy.py`) with no config surface — tunable only by changing the code,
not by a run-time setting.)

### Conceptual conflation, and anchors for empirical scope claims (D-conceptual-conflation)

> **Normative.** This subsection governs `Category.CONCEPTUAL_CONFLATION`, the empirical-anchor
> reading of `overstated_claim`, `LENS_BRIEF[Lens.LOGIC]`, and the three matching writer standards
> in `prompts.py::WRITER_SYSTEM`. Changing one side without the other is docs-as-spec drift.

**`conceptual_conflation` — the trigger.** Both halves are required:

1. two **materially distinct** concepts, mechanisms, units or populations are treated as
   interchangeable; **and**
2. the substitution is what carries a **load-bearing** inference or conclusion — keep the two
   apart and the conclusion no longer follows as stated.

The taxonomy names three distinctions that can satisfy this trigger. A **formal rule** (what a
statute, policy or specification provides), the **mediated mechanism** that implements it (who
administers it, at what
rate, subject to what other rule), and the **observed outcome** downstream are three propositions,
each needing its own support. The **units actually measured** and the **wider population** a claim
is made about are two different sets. And **groups that reach the same outcome by different
mechanisms** are not one group; a claim that generalizes across them, or a remedy that assumes one
lever reaches all of them, is where that shows.

**What it is not.** These exclusions are load-bearing — without them the category becomes a licence
to demand arbitrary distinctions, which is exactly the noise direction the audition measures:

- **Not terminology preference.** A different word for the same thing is not a conflation, whatever
  the critic would have called it.
- **Not a subgroup quota.** The absence of a breakdown is not the defect; a substitution is. There
  is no per-population disaggregation the report owes for its own sake.
- **Not a distinction that makes no difference here.** Where one mechanism, or one body of evidence,
  genuinely covers both things, treating them together is correct.
- **Not a defended aggregation.** A report that draws the distinction and then aggregates,
  explicitly, has done the work; disagreeing with the aggregation is `invalid_inference` territory
  if it is anything.

**Floor `major`, and not `blocking`.** It is `invalid_inference`'s sibling: the substitution is the
step the argument turns on, so a material floor is what forces the revision. Not blocking, because
unlike a contradiction nothing in the report is thereby shown false — and the fix is always
available inside the report (draw the distinction, or restrict the claim to the concept the support
covers). `related_span`, when supplied, must be a verbatim quote like the other logic categories'
(`triage.IN_ARTIFACT_RELATED`): both poles of a substitution are passages the report contains. It
stays optional, so a single sentence that fuses the two with no second passage is still reportable.

**Empirical scope claims are `overstated_claim`, explicitly.** Where a claim turns on **magnitude,
prevalence, timing or change**, and its only support is a thematic assertion rather than a concrete
figure or a source that states it, the claim is stronger than its support — which is the definition
of `overstated_claim`, widened here in the open rather than by drift. It is deliberately *not* a
new evidence category: the defect survives a perfect citation (a real source that describes the
phenomenon and measures nothing about it), so it is not a sourcing failure, and an evidence category
demanding a number would be unsatisfiable under `search.enabled: false`. It is deliberately not
writer-side-only either, because a writer standard nothing can raise is not detectable.

Two narrowings keep it from becoming "quantify everything":

- A claim about **kind, mechanism or character** turns on none of the four and owes no anchor;
  neither does one already qualified to the cases its support covers.
- The instruction may **never** demand a specific dataset or document as the only acceptable fix.
  Qualifying the claim to what the support establishes is always a complete resolution — the same
  resolvability contract every critic instruction carries. **Qualifying the claim means restricting
  it**, not annotating it; see the next section.

### What "weaken the claim" means (D-no-hedge-discharge)

The resolvability contract above guarantees the writer an escape from a demand it cannot satisfy.
It is not a licence to keep the claim and hedge it, and both prompts now say so.

**Writer side** (`prompts.WRITER_RESOLUTION_STANDARD`, carried by every non-polish revision in every
`revision.mode`). A fix task is resolved by changing the claim or its support, never by appending a
qualifier to a claim that is kept. Weakening a claim means restricting it to what the support
establishes — a narrower population, a smaller magnitude, the cases actually measured, one named
source's finding — or removing it. Attaching *this remains an extrapolation*, *this is not directly
established*, *this cannot be verified from the citation* or *this is unverified* to a claim that
keeps its figure and its citation resolves nothing, and the defect is filed again. An evaluative
qualifier (*according to anecdotal accounts*, *so-called*, *merely*) never stands in for a citation:
a claim no source establishes is removed, or restated as the report's own inference and labelled as
one. A limitation is stated once, where it applies — D-claim-scoped-patch carries a *fix* to every
restatement of a claim, and a caveat is not a fix.

**Critic side** (the `instruction` bullet, shared by all lenses). Where the acceptable resolution is
to weaken the claim, the instruction must say what the weakened claim would be: the population it
should be restricted to, the smaller magnitude the support carries, or the source it should be
attributed to. An instruction whose cheapest compliant reading is *state that this is unverified* or
*clarify that this figure is the author's own calculation* is not a fix and must not be offered,
because it leaves the claim, its figure and its citation exactly as they are.

**With search enabled** (`WRITER_SEARCH_ADDENDUM`) two further rules apply, both gated on retrieval
because both ask the writer to go and look. An absence claim — *no source addresses this* — is a
claim about the literature and is searched for like any other. And currency is checked against the
run date (D-run-date-grounding): where the newest evidence the report rests on is more than a year
older than the run date, on a question whose answer moves — regulation, litigation, guidelines,
standards, prices, product generations, model versions — the writer searches for what changed since
and states how recent its evidence is.

The measurement is `additive_only` on the `generate` event, and it is **warn-only**: nothing rejects
a draft for it. See [isolation.md](isolation.md#scoping-the-edit-is-not-narrowing-the-review-d-scoped-revision).

### Arithmetic, magnitude and the decisive consideration (D-decisive-quantities)

> **Normative.** This subsection governs the arithmetic, distant-contradiction and
> absence-of-evidence readings of `invalid_inference` / `contradicted_claim`; the magnitude,
> decisive-consideration and multiple-reading readings of `incomplete_answer` /
> `unexamined_presupposition`; `LENS_BRIEF[Lens.LOGIC]` and `LENS_BRIEF[Lens.COMPLETENESS]`; the
> matching entries in `prompts.py::_CATEGORY_MEANING`; and the four matching writer standards in
> `prompts.py::WRITER_SYSTEM`. Changing one side without the other is docs-as-spec drift.

No new category and no floor change. Like D-conceptual-conflation's widening of
`overstated_claim`, these are readings of existing categories stated in the open rather than left
to drift, because in production no lens owned arithmetic and no lens asked whether the argument
that settles the question was present.

**Logic lens — three rules.**

1. **Arithmetic and units.** Where the report states a derivation — a product, a ratio, a share of
   a total, a unit conversion, a range computed from stated inputs — the critic reproduces it from
   the inputs the report itself states. A result that does not follow from those inputs, a unit
   that changes between premise and result, or a scenario label that does not match the range
   attached to it, is `invalid_inference`, with the recomputed value in the rationale. Two
   narrowings are part of the rule: a figure stated to fewer significant figures than its inputs
   is not a defect, and
   an input the report never states is not a defect *of the derivation* — a claim resting on an
   unstated input is `overstated_claim` under D-conceptual-conflation's anchoring rule.
2. **Distant contradictions are expected.** A claim contradicted by the conclusion, by a key
   finding, or by a figure stated elsewhere in the report is `contradicted_claim` however many
   sections apart the two passages sit; the other passage goes in `related_span`, which is already
   verbatim-anchored against the whole artifact (`triage.IN_ARTIFACT_RELATED`), not against the
   cited paragraph. Two figures for the same quantity that differ by more than their stated
   precision are a contradiction wherever in the report they appear. Nothing about the mechanism changes here —
   distance was never in the definition; the prompt simply never said a distant second passage was
   expected, and critics read the omission as a restriction.
3. **Absence of evidence is not evidence of absence.** "No evidence of X at level L",
   "insufficient data to determine" and "not established" do not mean "no X at L". A conclusion
   carrying the second while its support says only the first is `invalid_inference`. The
   narrowing: a report that states the evidence is insufficient and concludes accordingly has read
   it correctly, and is not this defect.

**Completeness lens — the frame and three triggers.** The completeness question is *what would the
asker do with this answer, and which input to that decision is missing* — not whether every item on
a topic list is covered. That frame is not itself a trigger; the three triggers are:

4. **Magnitude.** Where the question asks which of two things is larger, better or more, or asks
   how much, an answer with no magnitude on either side — no figure, no order-of-magnitude
   estimate, no break-even — is `incomplete_answer`, **but only where the report's own cited
   material, or ordinary arithmetic from facts it states, would supply one**. It is not a demand
   for precision: an order of magnitude, or the break-even point, is a complete answer. A question
   about **kind, mechanism or character** needs no magnitude, mirroring the same carve-out in
   D-conceptual-conflation.
5. **The decisive consideration.** Where one argument settles the comparison — a term common to
   both sides cancels, a cost is already sunk, a stated dose sits against a published limit, one
   option repeats a production cycle the other does not — and the report argues its way past it
   without ever stating it, that is `incomplete_answer`, with the consideration named in the rationale.
   The narrowing: only where the consideration follows from facts the report itself states or
   cites, so the fix is available inside the report.
6. **Readings of the question.** Where the question's wording admits more than one reading — a
   causal boundary ("alone", "impact", "adequately"), an undefined tier ("mid-tier") — and the
   report answers one of them without saying which, that is `unexamined_presupposition`. The fix is
   to state the reading taken and, where the answer would change under another reading, to say so.

As with every critic instruction, none of these may demand a specific dataset or document as the
only acceptable fix; stating the limitation, or qualifying the claim, is always a resolution.

**Writer standards** are symmetric, in `WRITER_SYSTEM`: state the argument that settles the
question; where arithmetic settles it, show the arithmetic with its inputs and units, because a
derivation described is a derivation to be performed; for a comparison give a magnitude for each
side and the break-even where one exists, and state a counterargument's size relative to the main
effect; a heading claims no more than its section supports; and say which reading of an ambiguous
question is being answered.

**Citations are markers, and a revision keeps them (D-writer-citation-continuity).** `WRITER_SYSTEM`
says a citation is the `[n]` marker inside the sentence it supports — naming a source in prose or
listing it under Sources cites nothing — and that every entry is cited by some marker and every marker
has an entry. Every revision mode carries `WRITER_CITATION_REVISION` inside the resolution standard: keep
every marker on a kept claim; delete an entry only when nothing cites it — refused mechanically under
`revision.mode: ops`, where a Sources operation that would leave a body marker citing nothing is not
applied (D-ops-revision); never renumber, so a patch
does not rewrite every citing paragraph; and "remove the attribution" means re-cite, restrict, or label
as inference, never leave the claim standing unmarked. Every `generate` event carries a citation census
(`source_entries`, `body_markers`, `cited_entries`, `dangling_markers`, and on revisions
`cited_sources_dropped`, `cited_sources_added`, `entries_removed`). No draft is rejected on it and it is
not a controller input, but since D-census-gated-repair a marker-less body with sources
(`body_markers == 0 and source_entries > 0`) spends up to `revision.repair.repair_cap` extra calls
(default 1) to the same writer before the draft
ships to any critic — see
[isolation.md](isolation.md#scoping-the-edit-is-not-narrowing-the-review-d-scoped-revision) for the gate
and the out-of-scope one alongside it.

**Headings are writer-side only, deliberately.** A section heading is not quotable: `report.parse`
puts heading text in `Structure.section_titles` and never in a `Paragraph`, so a heading is absent
from both the cited paragraph and `Structure.full_text`, and `triage._require_quote` would reject
any `claim_span` drawn from one — failing the whole lens closed. A critic rule telling a lens to
quote a heading would therefore be a rule to fail. The writer standard ships; the critic trigger
does not, until headings are quotable.

### Evidence handling (RA-011, D-in-artifact-citations, D-retrieval-opt-in)

The report **carries its own citations**; the evidence lens challenges any material `uncited_claim`,
any on-its-face `misrepresented_source`, and any `fabricated_citation`. Citations must be
well-formed/resolvable in format.

**Retrieval is opt-in and off by default in code (D-retrieval-opt-in); the shipped `config/roster.yaml` opts in
(D-run-date-grounding).** The two postures differ in what a citation *is*:

* **`search.enabled: false` (default)** — no external retrieval, exactly as D-in-artifact-citations specifies. A diverse
  roster can still share a factual blind spot — error correlation survives differences in training
  data, architecture, and provider ([Kim et al. 2025](https://arxiv.org/abs/2506.07962)) — and a
  citation is whatever the writer recalled. Output is labeled *consensus-reviewed with in-artifact
  sourcing (no external retrieval)*, not fact-checked.
* **`search.enabled: true`** — writers hold a `web_search` tool and may cite only URLs a search
  actually returned, so a citation is a real, retrieved page. Output is labeled *consensus-reviewed
  with retrieved sourcing*, still not fact-checked. Startup fails closed if a writer cannot emit
  tool calls, because such a writer would still produce a `## Sources` section and fill it from
  memory — and no downstream check distinguishes that from a retrieved citation.

* **`search.read_sources: true`** (D-writer-source-reads, requires `enabled`) — the writer also
  holds `read_source` and may open a page **its own** searches returned, so a citation can be
  attached to text the writer read rather than to a snippet. This does **not** move the label: a
  read page shows what a page says, not that the page is right, so the output stays
  *consensus-reviewed with retrieved sourcing*. What it changes is what the writer is entitled to
  assert and what the run can afterwards show — see the traceability paragraph below. On a revision,
  where `verify_sources` is also on, the writer may also open the pages the draft it revises lists
  (`search.read_cited_sources`, default on; D-writer-rereads-cited-sources), so a fix task about a
  cited claim can be worked against the page rather than the draft's paraphrase of it.

**Retrieval alone does not make the report fact-checked.** It constrains where citations come from;
it does not establish that a cited page *supports the specific claim attached to it* — the
attributable-to-identified-sources distinction ([Rashkin et al. 2021](https://arxiv.org/abs/2112.12870)),
and the gap the labeling here is careful not to paper over. The empirical case for that caution:
a preregistered study of commercial legal-research tools built on retrieval still measured
hallucination rates of 17–33%, against vendor claims of being hallucination-free
([Magesh et al. 2024](https://arxiv.org/abs/2405.20362)).

**Source verification (D-source-verification), also opt-in and off by default — including in the shipped roster,
which enables retrieval only (D-run-date-grounding): verification fetches model-chosen URLs, and the egress
boundary that makes that safe is a deployment concern outside this repo
(docs/ssrf-egress-isolation.md).** With `search.verify_sources: true`, addressable cited pages are
deduplicated and attempted up to the anti-pathological `search.max_source_urls` ceiling
(D-unbounded-evidence), then handed to the **evidence lens only** as untrusted data; unaddressable
entries and addressable entries beyond that ceiling remain unchecked and are recorded as not attempted.
Two categories change character:

| category | verification off | verification on |
|---|---|---|
| `fabricated_citation` | implausible on its face | the URL does not resolve |
| `misrepresented_source` | plainly would not support the claim, in its words, its direction or its scope | the fetched page does not contain the claim, states something materially different, reports a finding cutting the other way, or is about a different population/boundary/period |

Only the evidence lens receives them. Logic and completeness cannot raise a citation category, so
page text would widen what those lenses see without widening what they may report.

**A definitive not-found is resolution; every other failed fetch is not (D-notfound-fabrication).** An HTTP 404 or 410
(Gone) establishes that the cited URL does not exist — which is exactly what the table above calls
`fabricated_citation` under verification. That case is settled **mechanically**, in the fetch path
(`triage.mechanical_citation_issues`, raised from `graph._critique_one`), so the finding is a fact
the pipeline reports rather than a judgement a critic model must elect to make; it clamps to its
`blocking` floor like any other `fabricated_citation`.

**Every other failed fetch is never evidence of fabrication.** Sites block automated clients (403),
paywall, time out, go offline, or serve a body this cannot read — and treating that "could not
read" as "does not exist" would manufacture `blocking` defects out of transient network conditions.
The critic is told never to raise a defect on the basis of a failed fetch; only the definitive
not-found above is escalated, and that escalation is the pipeline's, not the model's. Each class is
now surfaced under its own `SourceOutcome` label (`BLOCKED`, `COULD NOT READ`, …) rather than one
flat "could not fetch", and an opt-in tier (`sources.enabled` **and** `sources.pdf.enabled`, both off
by default, fatal at startup without `pypdf`) **reads** a cited PDF rather than reporting it as an
unreadable content type — so "a body this cannot read" narrows to formats no converter handles.

**Bibliography integrity is settled mechanically (D-bibliography-integrity).** Every critic finding
anchors to a verbatim `claim_span` in a body paragraph, so a defect whose whole subject is the
reference list cannot be expressed in the critic schema and no lens owns it. Three such defects are
decidable by string comparison against the report's own text — no fetched body is read and no URL is
judged by its shape, so nothing here reaches past what the report itself says (QP10) — and
`triage.mechanical_bibliography_issues` mints them from `graph._critique_one` under the same gate as
the not-found finding above — the evidence lens, on a **completed** review, and with verification
on or off, because none of them needs a fetch:

| observable fact | category | floor | locus |
|---|---|---|---|
| a `[n]` cited in the body with no entry numbered `n` | `uncited_claim` | major | first citing paragraph |
| an entry the body never cites | `unclear_structure` | minor | the paragraph listing it |
| two entries under one URL | `unclear_structure` | minor | the paragraph listing the duplicate |
| a Sources list with a real reference (a URL or an explicit number) and no marker anywhere in the body (D-uncited-bibliography) | `uncited_claim` | major | first sentence of the first quotable body paragraph |

No category is added to the taxonomy table, and each finding is minted **at** its category's floor,
so the clamp is a no-op and RC-005's direction is untouched. These are pipeline-authored facts, not
critic judgements: like the not-found finding they bypass `validate_issue`, whose subject is
model-authored fields, and like it they never attach to a failed lens. Every field they carry is
bounded on construction — a bibliography number of any length is cut to a short label before it is
interpolated — so minting can never raise out of the critique node. Markers are read with
`excerpt`'s parser (ranges expanded, the Sources section excluded), entries are numbered as
`excerpt.entry_numbers` numbers them, and at most `search.max_source_urls` entries are considered
for per-entry orphan and duplicate checks. A report with no `## Sources` section mints nothing here.
A body with no citation marker at all is the last row: that check scans the complete parsed Sources
list and reports its full entry count, minting **one** finding and no per-entry orphans or duplicates,
because every entry would otherwise be an orphan and one defect would be counted once per entry. A
Sources section whose only content is not a reference ("None.") mints nothing. The evidence
critic's own `uncited_claim` findings are kept alongside it, never dropped in its favour.

**What the critic is shown of a page is chosen by the claims, not by position
(D-claim-anchored-excerpts).** A fetched body is retained up to `search.fetch_body_max_chars`, and
one critic is shown at most `search.fetch_max_chars` of it — as the page's opening plus the passages
that best match the report's own sentences citing that source (`excerpt.select`: deterministic,
numbers weighted above content words, whole sentences, document order), each under its character
range, with `[…]` marking what is not shown and a header stating how much of the page is. Every
entry is labelled with the bibliography number(s) the report lists the URL under. A fixed prefix can
omit claim-relevant text later in a page even when retrieval succeeded. The rule the critic is given
is unchanged in substance and sharpened in wording: a
page shown in part is truncated, and a claim missing from the excerpts is not evidence that the page
lacks it — `misrepresented_source` is raised only where an excerpt addresses the same point and
states something materially different. `dispute.adjudicate_mechanical` searches the retained body,
not the excerpts; `support.check` is a separate mechanism entirely, working from `session.reads`
(capped at `read_max_chars`) rather than this cap.

#### Direction and scope (D-source-fidelity-direction-and-scope)

> **Normative.** This subsection governs the meaning of `Category.MISREPRESENTED_SOURCE` in both
> columns of the table above (`prompts._CATEGORY_MEANING` and the sharpened meaning `critic_user`
> substitutes when a body arrived), `LENS_BRIEF[Lens.EVIDENCE]`, and the closing rules of
> `prompts.fetched_sources_block`. Changing one side without the other is docs-as-spec drift.

**Support is not word-matching.** "Does not support the claim as stated" covers three failures, not
one. The page may not say it. The page may say it and mean the opposite of what it is cited for —
**direction**. Or the page may say it about something else — **scope**. The evidence lens is asked
both questions of every cited claim whose page it holds:

1. **Direction — does the page assert this, in this direction?** A source can be quoted
   verbatim-correctly for a proposition its own finding, conclusion or headline result cuts against.
   Two recurring shapes: a meta-analysis that found *no* relationship cited as establishing one, and
   a body's "insufficient data to determine" rendered as a determination — absence of evidence read
   as evidence of absence.
2. **Scope — is the page's scope the claim's scope?** A finding stays attached to the population,
   product, category, system boundary, dose or wavelength band, and period it was measured on. A
   real, correctly quoted source about a different one of those, restated as if it were about the
   question's, does not support the claim. This is the same rule `WRITER_SYSTEM` already states for
   writers, now raisable.

Both are `misrepresented_source`, at its unchanged `major` floor. Scope is deliberately **not**
`conceptual_conflation`: that category is the logic lens's and is about the report's own reasoning,
where this is about what a cited page is evidence *for*. The verification-off meaning keeps its
`plainly` — with no page in hand the bar is still what the citation would support on its face — so
what widens is the kinds of failure, not the confidence required to report one.

**Three exclusions, load-bearing.** Without them the widened category is a licence to object, which
is the noise direction the audition measures. It is **not** a stylistic mismatch of wording where
the substance matches; **not** a source merely *broader* than the claim when it genuinely covers the
claim's case; and **not** a demand for a source the writer cannot get. The resolvable fixes are
re-attributing the claim to a source the report already carries, restricting the claim to the scope
the source covers, or removing the attribution. A `misrepresented_source` instruction must therefore
say what the source actually says, quoted or paraphrased from the excerpt, and may never propose
keeping the citation while labelling the claim unverified.

**An unread body licenses no finding about content.** `BLOCKED`, `COULD NOT READ`, `NO READABLE
TEXT`, `NOT ATTEMPTED`, `COULD NOT RESOLVE`, `FETCHED, TEXT WITHHELD` and registry-metadata-only
entries say nothing about what the page contains — `BLOCKED` in particular now says so in both
directions, where it previously said only that the source may still exist. A body that is plainly
not the article (navigation, a cookie notice, a menu, a paywall teaser) counts as unread. And a
claim carrying a citation marker is never `uncited_claim`: its problem, if any, is what that
citation supports, and where that cannot be checked the honest finding is none — the alternative
asks a writer to delete a good citation in order to satisfy "add a citation".

**Two hygiene rules, all three lenses.** The `=== SECTION n: title ===` lines and `[S<n>.P<m>]`
markers `report.render_with_loci` adds are addressing scaffolding for the review, not part of the
report; section numbers are handles, and a gap in them is an artifact of the rendering rather than a
defect in the report's organization. And a critic may not file an issue in order to withdraw it:
there is no retraction, so an issue whose rationale concludes it is not a defect, or whose
instruction requires no action, is omitted rather than filed.
**Each cited claim is checked against its page in its own context (D-claim-level-verification,
opt-in, `claim_check.enabled`, requires `search.verify_sources`).** Every sentence of the report
body that carries a citation marker is paired mechanically with the fetched page the bibliography
lists under that number (`claimcheck.pairs`: the report's own loci, `excerpt`'s sentence split and
marker expansion, `excerpt.entry_numbers`). Each pair is then checked by the evidence critic's own
model in a **fresh context holding one sentence, its paragraph and one page** — the page as
claim-anchored excerpts up to `claim_check.page_max_chars`, shown whole when it fits — and answers a
closed verdict: `supported`, `contradicted`, `absent` or `unreadable`, with `supported` and
`contradicted` anchored to a verbatim span of the page or rejected. Verdicts become findings
mechanically, like the not-found above:

| verdict | page shown whole | page shown in part |
|---|---|---|
| `contradicted` | `misrepresented_source` (major) | `misrepresented_source` (major) |
| `absent` | `misrepresented_source` (major) | nothing; counted as `absent_partial` |
| `supported`, `unreadable` | nothing | nothing |
| unchecked (call failed, span not in page, no page, past `max_pairs`, `aborted` after `max_consecutive_failures` unchecked calls in a row) | nothing | nothing |

A page whose body was cut before its end — at `search.fetch_max_bytes` on the wire, at
`sources.pdf.max_pages`, at a cache or consumer character cap — is **always** "shown in part",
however short what survived (`FetchedSource.truncated` → `Excerpted.complete` is false), and the
checker and the critic are both told the page continues past the last character retained
(D-claim-check-inconclusive-verdicts).

Every failure lands toward the writer: an unchecked pair mints nothing, and the critic's own
whole-document `misrepresented_source` judgement is kept for exactly the pairs the checker did not
**settle** and dropped for the ones it did (`claimcheck.reconcile`: same paragraph, the critic's span
inside the checked sentence), so one claim is never counted twice under two spans. A pair is settled
by `supported`, `contradicted`, or `absent` from a page shown whole — the verdicts that read the page.
`unreadable` and `absent` from a page shown in part settle nothing: they mint no finding, so they may
retire none, and the critic's judgement on that sentence stands as it did before the checker existed
(D-claim-check-inconclusive-verdicts). The findings ride
the critic's `LensResult` — so they clamp, deduplicate, count toward `material`, withhold the clean
record, and reach the writer as tasks exactly as a critic's own would — and a 402 during checking
fails the lens with the account class so the run defers (D-credit-exhaustion-defers). Verdicts are
memoised for the runtime per (critic resolved identity, complete system prompt, complete user
prompt): the memo covers every input the checker sees, so a changed paragraph, source metadata or
date cannot reuse a stale verdict. Each family still forms its own
view, and it is never a clean record. Counts go to a `claim_check` event; the sentences, spans and
reasons go to the run's critiques directory. No controller rule, no `ControllerInput` or
`OrchestratorView` field, and no budget changes; calls per pass are bounded by citation markers ×
depth and the anti-pathological `claim_check.max_pairs`, and a pass whose calls fail
`claim_check.max_consecutive_failures` times in a row records the rest `aborted` without a call, so
a proxy that has stopped answering costs a few timeouts inside the critic's slot rather than one per
pair (D-claim-check-inconclusive-verdicts).

**Existence is checkable even when the body is not (D-existence-vs-body, off by default).** With `sources.enabled`
and `sources.identifiers.enabled` both true, a cited URL that carries a DOI or PMID and would not
hand over its body is asked about at a bibliographic registry (Crossref, OpenAlex by default; arXiv
ids and PMCIDs are covered when arXiv and Europe PMC are added to the tier's provider list). A
confirmed record yields `metadata_only` — or `paywalled`, when the direct fetch was also refused —
and the critic is shown the citation details and, where one exists, the abstract, announced as
confirmation that the source **exists** and explicitly labelled as not being its text. This does
not sharpen
`misrepresented_source`: an abstract is a summary the authors wrote, so a claim's absence from one
is not evidence the paper does not make it, and the critic is told never to raise that category
against a source shown only as metadata. It does move a real paywalled paper out of the class that
looks like a fabricated one. In the other direction, an identifier that *every* authoritative
registry denies is a not-found and reaches the mechanical finding above — gated hard, because that
finding is blocking. With `sources.open_access.enabled` also true a free copy may be read; such a body
is marked as coming from a mirror rather than the cited URL, and can never settle a dispute about
that URL, because a preprint is not the version of record.

**Claim-level traceability is recorded, and is not a stop input (D-writer-source-reads).** With
`search.support_manifest` on (which requires `search.read_sources`), a draft **whose writer read at
least one page body** is followed by a separate structured pass in which that writer records
`citation_id -> url -> locator -> support_span -> claim` for every claim resting on a page it read.
A draft whose reads all failed — blocked, paywalled, not found, unreadable, or out of budget —
produces no manifest at all rather than an empty one: with no body in hand the pass would collect
spans nothing could check, which is the arrangement the `support_manifest` config guard refuses at
load time. So "no manifest for this round" means *nothing was readable*, never *nothing was
supported*. `support.check` rules on each
entry mechanically — the claim must be in the report, and the span must be in the cited page's
**own** body. Both must still contain text after quote normalization; markup-only or whitespace-only
values cannot establish support merely because the empty string is a substring of every document.
The check distinguishes "checked and false" (`span_not_found`) from the three ways an entry is
simply not checkable: no body was read (`body_not_read`, which covers an abstract and a paywall),
the body came from an open-access copy (`different_document`), or the writer never opened the
source (`not_retrieved`).

**None of this reaches the controller.** No verdict becomes a `Defect`, no count appears on
`OrchestratorView`, and no rule in the table below reads any of it: the manifest goes to
`support/rNN.json` and its verdict counts to `events.jsonl`, and that is the whole of its effect.
Termination, acceptance, the severity floors and the 14 rules are exactly what they were. That is
not caution for its own sake — the manifest's author is the writer whose report is under review, so
a manifest that fed acceptance would let a writer grade its own review, which is the arrangement
principle #7 exists to forbid. A `supported` verdict is a statement that the chain is traceable, not
that the claim is true, and the label above is unchanged for the same reason.

**Every prompt carries the run's date (D-run-date-grounding).** A date-plausibility judgement ("this citation is
future-dated, so it must be fabricated") is only as good as the judge's sense of what day it is —
and without grounding, that sense is the critic model's training-data recency. Run
`run-75eb136b9bfb` stagnated to `needs_human_review` because the evidence lens repeatedly flagged
legitimate current-year citations, including one dated the previous day, as "future-dated"
blocking fabrications the writer could never resolve. The date is captured once at intake
(`run_date`, UTC) and injected into every writer and critic prompt, so a confirmation critique
stays byte-identical even across midnight (RB-010). It is deliberately absent from the audition
prompt-hash surface: it is run context, not lens semantics.

**The label states measured coverage, not that verification was enabled (D-observed-source-coverage).**
With verification on, `final.json`'s label is the observation — *consensus-reviewed — source review:
15 cited; 3 addressable; 3 existence confirmed; 3 source bodies read (backing 3 cited entries);
12 not independently checked* — because a switch that is on says nothing about how much of a
bibliography it reached.
`fetch.coverage` tallies the shipped draft's own `## Sources` section in **entries**: `cited`,
`addressable` / `not_addressable`, `attempted` / `not_attempted`, and a disposition per attempt
(`body_backed_entries`, `metadata_only`, `blocked_or_unreadable`, `not_found`,
`budget_exhausted`). `bodies_read` separately counts distinct cited URLs whose body was read, so two
bibliography entries sharing one URL render as two body-backed entries and one body, never two
bodies. `existence_confirmed` is derived from body-backed entries and registry hits, and
`not_independently_checked` is derived only from unaddressable, unattempted, blocked or unreadable,
and budget-exhausted entries. A definitive not-found is independently checked and found absent, so
it belongs in neither derived count. The tally is taken where the evidence lens fetches and keyed to
the artifact's hash, so a
non-accepted terminal that ships an earlier draft reports *that* draft's coverage; a draft with no
record reads as *not recorded*, which is neither zero coverage nor a pass. At review depth above 1
the lens tallies the same bibliography once per critic; an artifact still gets exactly **one**
record, selected by a stable total ordering of independent checks, distinct bodies, body-backed
entries, metadata confirmations, and definitive absences. Equal-reach observations therefore do
not fall back to arrival order. Record replacement and its audit event share one lock, so the last
coverage event and `final.json` cannot diverge under concurrency. The markdown export, the HTML
export and the run page render the same breakdown. Coverage is a report, never a gate: it enters no
controller rule, no
`OrchestratorView`, and mints no defect.

Two readings the counts must never be given, carried as a caveat under every rendering of them. An
entry that was **not independently checked is unverified, not suspect**. A **blocked or paywalled
entry was unreadable, not absent** — reading it as absence is exactly the inference
D-notfound-fabrication forbids. A definitive not-found is the contrasting case: it was independently
checked and establishes that a cited page does not exist. Coverage is measured with verification
*off* too, where every entry is unchecked by configuration rather than by outcome and the rendering
says so; the two labels for those postures are unchanged, because neither ever claimed verification.

Even with both options on, the output is **not fact-checked**. Verification establishes that a cited
source exists and, when a body can be read, that the page says something compatible with the claim;
a registry-confirmed source whose body cannot be read proves existence only, and an open-access
mirror is a different document from the cited page. It does not establish that the page is *right*,
nor that the roster picked good sources in the first place — and now it does not claim to have
reached more of the bibliography than it did.

## Two signal schemas — content-free vs. operational (RB-004, RB-008)

**`OrchestratorView`** — the *only* thing the blind LLM orchestrator sees. Bounded ints/enums,
**no** identifiers, hashes, free text, or loci:

```
OrchestratorView {
  counts: { <category>: {blocking, major, minor} }
  totals: { blocking, major, minor }
  delta_material_vs_prev: int
  lenses_failed: int
  round: int, min_ticks: int, hard_cap: int
  roster_size: int
  lens_cleared: { <lens>: int }   # # distinct non-author model FAMILIES clean for this lens on current hash
  acceptance: enum{none, weak_met, strong_met}   # derived from lens_cleared + roster eligibility
  polish_used: int, polish_cap: int
  stagnation_count: int, cycle_detected: bool
}
```

**`ControllerInput`** — the deterministic controller (not an LLM; still blind to report content)
holds `OrchestratorView` **plus** every operational predicate the decision table consumes (RD-002),
nested as `view` rather than flattened:

```
ControllerInput {
  view: OrchestratorView                          # polish_used/polish_cap live here, not flattened
  fatal: bool
  fatal_reason: str | None                         # set on the fatal path; not surfaced elsewhere
  run_id, artifact_hash, artifact_hash_history
  author_identity                                  # resolved provider/model/version
  lens_status: [LensStatus]                        # per-lens cleared_count/eligible_count/
                                                     #   unused_eligible for the CURRENT artifact_hash,
                                                     #   pre-derived by roles.lens_statuses from the
                                                     #   hash-keyed clean records — no raw record list
                                                     #   or roster identity map reaches the controller
  critique_attempts_remaining: int               # lens-failure retry budget
  confirmation_attempts_remaining: int           # bounds the per-lens top-up loop (rule 8)
  polish_recommended: bool                        # from the orchestrator LLM
  stagnation_limit: int, cycle_period: int
  rewrites_used: int, rewrite_cap: int            # rule 13's bounded rewrite (D-scoped-revision)
}
```

Identifiers live here, never in the LLM's view. **Noninterference** (RB-008) is defined over
`OrchestratorView`. `rewrites_used`/`rewrite_cap` sit on `ControllerInput` and deliberately *not*
on `OrchestratorView`: `polish_used`/`polish_cap` are in the view because rule 9 is the blind LLM's
one authority and it must see its own budget, but rule 13 is fully deterministic, so surfacing the
rewrite budget to the LLM would widen the RB-008 noninterference surface to buy nothing.

## Acceptance evidence — immutable, hash-keyed records (RC-001, RC-002)

Records are **per-lens** (D-per-lens-critics): each lens has its own ordered critic pool, from which
`roles.critic_slate` draws the configured cross-family slate. Every completed critic can contribute
a **per-lens clean record**, created only when that review finds no material issue for the lens's
categories. Each record is immutable and keyed by:

```
CleanRecord { artifact_hash, lens, critic_resolved_identity, artifact_author_identity }
```

**Clean records reset on every generation, unconditionally — regardless of whether the new text's
hash actually differs from the last.** Keying the reset on "the hash changed" would let a clean
record earned under a *different* author satisfy acceptance for a byte-identical regeneration by
someone else; resetting on every `_generate` call closes that gap — stale attestations never
satisfy acceptance (closes RC-002).

A lens is **strongly-cleared** for the current hash when clean records cover **≥2 distinct
non-author model families**; **weakly-cleared** when exactly one family does (because the roster
has only one eligible family for that lens, or only one has reviewed cleanly so far). Then:

- **`strong_met`** (default): `material == 0` **and every lens is strongly-cleared** → terminal
  **`accepted`**. Every dimension has been independently double-checked by different, blind-spot-
  decorrelated models; no model ever reviews its own draft.
- **`weak_met`**: `material == 0` **and every lens is at least weakly-cleared**, with at least one
  lens only weakly-cleared **because the roster cannot supply a second eligible non-author family**
  for it (`roster_limited`) → terminal **`converged_unconfirmed`**. An honest, weaker guarantee that
  names exactly which dimension lacked a second reviewer. (All evidence is current-hash-only; there
  is no cross-artifact "consecutive-clean" mechanism.)

Why a lens with one capable family can't be strongly-cleared: another checkpoint from that family
shares the blind spots QP2 is meant to decorrelate, so a second *distinct eligible family* per lens
is required (RC-001, generalized per-lens).

The confirming critique runs through the **identical critique interface/prompt**; `confirm_state`
is a controller-side label applied **after** output, invisible to the model, fresh context, no
cache reuse (RB-010).

## Review depth: both reviewers read every draft (D-front-loaded-depth)

`review.depth` (default **2**, overridable per lens via `review.per_lens`) is how many eligible
non-author critics read a lens on **every** generated artifact, before any revision. Each is a
separate call through the same interface as any other critique — same prompt, fresh context, blind
to the other critic and to what it found.

```
review:
  depth: 2                 # critics per lens per pass; 1 is the old single-critic pass
  per_lens: {evidence: 3}  # optional per-lens override
```

Depth was previously 1 in all but name: the second reviewer was collected by **rule 8**, which
fires only after a pass has already reported `material == 0`. So the second opinion could not
participate in discovery, and when it disagreed the run had already paid for a clean pass to find
out. D-front-loaded-depth records the mechanically testable scheduling change.

Three properties bound it:

* **A ceiling, not a quota.** A pass runs `min(depth, fresh eligible non-author families)` critics.
  A `roster_limited` lens still runs one critic and still terminates `converged_unconfirmed`
  through rule 10 — depth can never turn a weak guarantee into an abort.
* **Eligibility is enforced per slot.** The slate is drawn by `roles.critic_slate` from
  `eligible_critics`, which has already dropped the author and deduplicated by resolved
  provider/model; the slate then admits at most one critic from each model family, and
  `assert_author_exclusion` re-checks at the moment of the call. No slate contains the author, one
  model twice behind aliases (RA-017), or same-family checkpoints presented as independent (QP2).
* **One finding is counted once.** Two critics on a lens routinely report the same defect;
  `triage.distinct_issues` collapses them on `(section, paragraph, category, claim_span)` — the
  key the defect list already used — keeping the highest severity, so a second reviewer may
  escalate a finding and can never soften it (RC-005). `tally`, the defect list and the
  stagnation signature therefore all see one finding once.

**The decision table is unchanged** — no rule added, removed, renumbered or reordered, and no new
`ControllerInput` or `OrchestratorView` field. Rule 8 keeps its job (it is still the only way an
under-cleared clean artifact reaches `strong_met`, still bounded by `confirmation_attempts`) and
loses its shift: at depth 2 a clean pass normally arrives already strongly-cleared, so rule 8
becomes the top-up for **incomplete depth** rather than the normal discovery path. Termination
survives untouched, because every measure that bounds the loop counts passes, generations and
budgets — never calls.

### What `lenses_failed` counts (rules 2 and 3)

`lenses_failed` is the number of lenses with **no completed review of the current artifact**
(`triage.unreviewed_lenses`). That matches the old latest-result reading on every depth-1 discovery
pass, but deliberately differs after a rule-8 confirmation fails while the lens already holds a
completed review. The same distinction also applies within a depth-2 slate when one critic
completed and the other failed:

* Fail-closed still applies to a **review**, whole: one bad field fails the call it appeared in,
  after the repair budget, and nothing from it is salvaged or silently dropped.
* Counting the *lens* as failed there would discard a complete, valid review in order to re-ask,
  which is the opposite of what fail-closed protects. Before D-front-loaded-depth, a failed rule-8
  confirmation overwrote the completed review, sent the run to rule 2, and exhausted at rule 3
  (`aborted`). It now returns to rule 8 when another qualified witness remains, or ends through
  rule 10/11 (`converged_unconfirmed` / `exhausted_unresolved`).
* The shortfall is not forgiven. It lands on `cleared_count`, so the artifact cannot be accepted:
  if it is clean, rule 8 restores the depth; if it is not, rule 14 replaces the artifact anyway.

## The stop decision — one exhaustive ordered table (RB-009, RC-003, RC-004)

The controller evaluates these **in order; first match wins**. This is the *whole* controller
function — lens-failure, polish, and cycle handling are all in the table (RC-003), and the
incomplete-review check precedes every clean/material/cap conclusion (RC-004). Inputs are exactly
the `ControllerInput` fields above (`view` unpacked field-by-field where a rule needs an
`OrchestratorView` value).

The **non-generating** clean-artifact rules (7, 8, 10, 11) are **not gated on `round`**, so
confirmation top-up (rule 8) remains reachable *at the cap* — it neither generates nor advances
`round` (fixes RG-001). The one clean-artifact rule that **generates** (rule 9, polish) *is*
cap-gated (`round < hard_cap`) so the hard cap stays hard (RH-001). The `material > 0` cap terminals
(rules 5–6) are cap-gated too.

**Config invariant (validated at startup, fail closed):** `0 < min_ticks < hard_cap`. This
guarantees rule 4 (the only other generating rule) can never fire at or beyond the cap, so **no
rule generates once `round ≥ hard_cap`** and the hard cap is genuinely hard (RI-001).

| # | Condition | Action / terminal |
|---|-----------|-------------------|
| 1 | `fatal` (writer pool empty, or every eligible writer attempt failed) | **aborted** |
| 2 | `lenses_failed > 0` **and** `critique_attempts_remaining > 0` | **re-critique** the unreviewed lens(es) (→ Critiquing); `critique_attempts_remaining -= 1`; the resulting triage pass still updates `prev_material`/`prev_signature`/`stagnation_count` and the scoreboard, but rule 2 precedes rules 4–14, so nothing about this partial pass ever reaches a stop decision |

`lenses_failed` counts lenses with **no completed review** of the current artifact, not
lenses one of whose reviews failed — see [Review depth](#what-lenses_failed-counts-rules-2-and-3).
A lens with no eligible non-author critic, and a review that stays malformed past its repair
budget, both land here too: each is recorded as a *failed* `LensResult` rather than raising
`fatal` directly, so they reach the table through rule 2's re-critique and — once the budget
is spent — rule 3's `aborted`, not through rule 1.

A lens only reaches rule 2 once the critic has already been given
`budgets.critic_repair_retries` chances to correct itself *before the lens is failed*,
shown what its rejected field should have quoted and what it actually submitted, and asked
for a patch rather than another review (D-repair-turn-context; see `docs/isolation.md`).
Those chances are separate model calls against a narrow repair schema, not extra passes at
the critique prompt — the same budget covers the review call's own schema repairs. Rule 2 is the
expensive fallback — it discards every issue in the response and re-asks a different
model — so it must not be the first response to a fixable quoting slip. When the pool of
eligible critics is exhausted, successive attempts rotate through it rather than re-asking
the model that just failed.

Rotation only spreads retries within one artifact, because `used_critics` resets with every
generation. A critic whose **calls** keep failing across drafts is handled separately
(D-failing-critic-sidelined): a pass in which every review an alias attempted failed at the call
layer (`LensResult.failure_class` is a `ModelCallError` class — not `schema_violation`,
`unstaffed` or `http_402`) is a strike, a completed review clears the count, and at
`review.critic_strike_limit` consecutive strikes (default 2) the alias is removed from every
critic pool for the rest of the run and a `critic_sidelined` event is written. It is never
sidelined if `validate_roster_health` would refuse the roster without it. Slates *and*
`lens_statuses` read the narrowed pools through `_critic_roster`, so a lens thinned to one
family is `roster_limited` and can end only `converged_unconfirmed` — the same verdict as a
startup that never had the alias (D-degraded-roster). No rule changes: sidelining changes who is
asked, never how many passes the budgets allow.

| 3 | `lenses_failed > 0` **and** no budget | **aborted** (cannot complete a review) |
| 4 | `round < min_ticks` | **continue** (generate) — never accept before `min_ticks` |
| 5 | `round ≥ hard_cap` **and** `blocking > 0` | **needs_human_review** |
| 6 | `round ≥ hard_cap` **and** `major > 0` | **exhausted_unresolved** |
| 7 | `material == 0` **and** `strong_met` | **accepted** |
| 8 | `material == 0` **and** `top_up_possible` (some lens `toppable` **and** `confirmation_attempts_remaining > 0`) | **re-critique** *every* toppable lens in this one pass (not just one), each by a fresh eligible non-author model (→ Critiquing, **no** generation); `confirmation_attempts_remaining -= 1` once for the pass. At `review.depth ≥ 2` this is the top-up for *incomplete depth*, not the normal discovery path (D-front-loaded-depth) |
| 9 | `material == 0` **and** `round < hard_cap` **and** `minor > 0` **and** `polish_recommended` **and** `polish_used < polish_cap` | **continue** (polish → generate; `polish_used += 1`) |
| 10 | `material == 0` **and** `weak_met` (every under-cleared lens is `roster_limited`) | **converged_unconfirmed** |
| 11 | `material == 0` (not strong, not toppable, not weak — confirmation budget spent) | **exhausted_unresolved** (clean-but-unconfirmed) |
| 12 | `cycle_detected` | **needs_human_review** (freeze the selected version) |
| 13 | `material > 0` **and** `stagnation_count ≥ K` **and** `rewrites_used < rewrite_cap` | **continue** (generate — a **full-document rewrite** by a fresh writer, ignoring `revision.mode` — `patch`, `rewrite` or `ops` alike; `rewrites_used += 1`, `stagnation_count := 0`) |
| 13 | `material > 0` **and** `stagnation_count ≥ K` | early terminal: **needs_human_review** if `blocking>0` else **exhausted_unresolved** |
| 14 | `material > 0` | **continue** (generate from defect list) |

Rule 13's two branches are one rule, in the shape rule 13 already had (it branches internally on
`blocking > 0` too). The rewrite branch exists because of **D-scoped-revision**: under
`revision.mode: patch` every revision edits only the paragraphs a fix task named — plus the passages
that restate the same claim, since D-claim-scoped-patch — so a run can only
accrete, and a stuck signal has one thing left to try before it means "more ticks will not move it".
With `rewrite_cap: 0` the rule is exactly the terminal it always was. Resetting `stagnation_count` is
load-bearing — left at the limit, the next tick re-fires rule 13 and spends the whole rewrite budget
in consecutive ticks without ever judging a rewritten draft on its own signal.

Per-lens predicates: a lens is **`toppable`** when `cleared_count < 2` and a not-yet-used eligible
non-author model remains; **`roster_limited`** when `eligible_count < 2` (can never be strongly-
cleared). `strong_met` = `material==0` ∧ every lens `cleared_count ≥ 2`. `weak_met` = `material==0`
∧ every lens `cleared_count ≥ 1` ∧ every lens with `cleared_count < 2` is `roster_limited`.

**Totality & termination:** first-match semantics selects exactly one rule for every input state,
and rules 1–14 leave no state unhandled (every `material == 0` state matches one of 7–11; every
`material > 0` state matches 5/6 at the cap, or 12/13/14 otherwise). Each continue action strictly
decrements a finite measure: generation advances `round` toward `hard_cap` (rules 4, 9, 13, 14) and —
given the `min_ticks < hard_cap` config invariant — no rule generates once `round ≥ hard_cap`; the
lens-failure retry budget bounds rule 2; `polish_cap`
(and the `round < hard_cap` gate) bounds rule 9; **`confirmation_attempts_remaining`
bounds rule 8** — critically, rule 8 re-critiques *without* generating, so it cannot loop forever
and falls through to rule 10/11's terminal when the budget is spent. Cycle (rule 12) and stagnation
(rule 13, once its rewrite budget is spent) force early exit. So the machine always halts.

**Rule 13's rewrite branch does not weaken that argument** (D-scoped-revision). It generates, so it is
bounded twice over: `rewrite_cap` is finite and `rewrites_used` strictly increases toward it, and the
generation advances `round` toward `hard_cap` exactly like rules 4, 9 and 14. It needs no `round <
hard_cap` gate of its own, because rules 5 and 6 are cap-gated and precede it in the table for every
state with `material > 0` — so rule 13 is already unreachable at or beyond the cap, and RI-001's
"no rule generates once `round ≥ hard_cap`" still holds.

**LLM authority is scoped to rule 9 only** — the minor-polish judgment. Every other rule is
deterministic and overrides the orchestrator; the LLM can never skip `min_ticks`, pass the cap, or
accept with material issues.

### Disputes do not touch the decision table (D-writer-disputes)

The writer dispute channel adds an `adjudicate` node on the one-way `generate → critique` edge
and **nothing else**: no new `ControllerInput` or `OrchestratorView` field, no new rule, no rule
reordering. The termination argument above survives unchanged:

* `adjudicate` introduces no new cycle — it can only route forward into `Critiquing`.
* Its work is bounded: ≤ `disputes.max_per_pass` disputes per generation, a strictly decreasing
  whole-run `disputes.budget`, and a once-per-key registry that makes repeated disputes free.
* Suppression of `upheld`-adjudicated findings happens **before** `tally`, so it only *removes*
  issues from the counts. It can flip a state from material to clean — reaching rules 7–11
  earlier — but it can never create a generating state at or beyond the cap.
* A writer that keeps refusing an overruled task converges to the existing terminals: identical
  drafts trip `cycle_detected` (rule 12), an unchanged signature trips stagnation (rule 13 — which
  may spend its bounded rewrite first, then terminates).
  The dispute budget bounds *spend*, not termination — termination was never its job.

`round` still advances only in the generate node; rules 4/9/13/14 remain the only generating rules
and keep their gates (RI-001, RH-001).

- **material issue:** severity ≥ floor (`major`).
- **signal-stagnation:** the per-category `{blocking, major}` multiset is unchanged for `K`
  consecutive **triage passes** (a *stuck signal*, not proven semantic repetition) — every completed
  triage increments or resets the counter, including a rule-2 re-critique pass over the same draft,
  not only the passes that follow a fresh generation.
- **cycle:** the `artifact_hash` sequence repeats with period ≤ `L` (byte-level).
- **selected version:** the draft a non-accepted terminal ships, chosen by
  `controller.select_shipped_index` from one row per artifact — each artifact's **latest** triage
  (RC-002), ordered by round. Which rule runs is `review.selection`
  (D-latest-unblocked-selection):
    - `fewest_defects` (**code default**, the rule this system always had): minimal
      `w_b·blocking + w_m·major + w_n·minor`, ties → **latest round** (D-latest-round-tiebreak).
    - `latest_unblocked` (**opt-in**; the shipped `config/roster.yaml` sets it, the way it opts
      into search): keep the rows with the minimum **blocking** count; among those, the **latest
      round**. Major and minor counts do not enter selection. The case for it is an operator
      judgement about their own runs — that on the near-identical artifacts `revision.mode: patch`
      produces, those counts move with the critic slate more than with the prose — and because
      that judgement rests on observations the repository cannot cite (QP9), it is a deployment
      posture and not the default.
  Both are pure functions of bounded categorical counts, so QP1 holds either way; under both, ties
  in the deciding quantity go to the latest round, so D-latest-round-tiebreak's tie direction is
  kept, not reversed. The `triage` event records `blocking`/`major`/`minor` alongside `material`,
  so the selection is reconstructible from the audit trail under either rule.

### Terminal statuses (RA-012, RC-001)

| status | meaning |
|--------|---------|
| `accepted` | **every lens strongly-cleared** on the identical final artifact — each dimension cleared by ≥2 distinct non-author models |
| `converged_unconfirmed` | every lens at least weakly-cleared, but ≥1 lens is `roster_limited` (only one eligible non-author model) — the record names the under-reviewed dimension |
| `exhausted_unresolved` | cap/stagnation reached with only non-blocking issues, or clean-but-unconfirmed at cap; returned **with annotations** |
| `needs_human_review` | cap/stagnation/cycle reached with **blocking** issues present |
| `aborted` | either a fatal writer failure (empty writer pool or every eligible writer attempt failed), or a failed-lens path that reaches rule 3 after rule 2 cannot recover it (including zero eligible non-author critics or exhausted malformed/incomplete-review repairs); a provider unreachable at *startup* degrades the roster or defers the attempt instead — D-degraded-roster; a provider *account* that refuses to pay mid-run (HTTP 402) defers the run instead of reaching rule 1 or 3 — D-credit-exhaustion-defers |

A known-unacceptable artifact is **never** labeled `accepted` or `converged_unconfirmed`.

These five are the statuses the **controller** issues, and they are the only ones ever
written to `final.json`. The registry reports two further *lifecycle* states that the
controller never issues and that carry no verdict about the artifact:

| state | meaning |
|-------|---------|
| `interrupted` | the process went away mid-run; the checkpoint makes it resumable |
| `abandoned` | recovery gave up — the resume attempt cap or startup-deferral cap was reached, or the run's inputs no longer match its checkpoint |

`abandoned` is terminal for the UI, but it is deliberately **not** a `final.json`: giving
up is not a verdict, and the audit trail must never claim the controller reached one. A
human can always resume past it.

**Under web-worker recovery**, an attempt refused by startup validation writes a `deferred`
event and stays `interrupted` (D-deferred-not-abandoned) — it is not a third state. What it
changes is the *count*: a deferred attempt cancels itself against the resume cap, because
the models being unreachable is a fact about the deployment rather than about this run, and
every queued run would have failed identically. The registry distinguishes it only in the
note it shows ("the model roster was unreachable; it retries automatically"), so a run
parked by someone else's rate limit does not read like one that died. Deferrals are
themselves capped by `max_deferred_attempts`, generously and separately from
`max_resume_attempts`: a deployment that refuses that many boots in a row is a
configuration nobody is coming to fix, and the run is `abandoned` like any other recovery
gave up on, rather than deferring silently forever.

**A provider account that cannot pay defers mid-run too** (D-credit-exhaustion-defers). A 402 is
not retried (`ProviderAccountError`). When every writer attempt failed and one was an account
refusal, or when an account refusal is why a lens holds no completed review, the node raises
`ProviderAccountExhausted` instead of returning `fatal` or a failed lens: the controller never
sees it, the checkpoint stays at the last completed node, and the worker writes `deferred` with
the closed reason `provider_account` under the same cap. Rules 1 and 3 still fire for every other
failure.

Startup validation deferral is a property of `RunWorker._drain`, not of `StartupRefused`. A
direct `ra run` calls `build_runtime` itself and has no registry lifecycle to move: when startup
validation raises `ConfigError`, the CLI prints `fail closed:` with the full diagnostic message
and exits `2`. By contrast, a mid-run `ProviderAccountExhausted` is resumable: the direct CLI
prints `deferred:` with the run id and resume command, and exits `75` (`EX_TEMPFAIL`). It writes no
`deferred` event and moves no registry lifecycle state; those are worker responsibilities.

## Lifecycle state machine

```mermaid
stateDiagram-v2
    [*] --> Intake
    Intake --> Generating: question only
    Intake --> Critiquing: seed provided (seed = R1)
    Generating --> Critiquing
    Critiquing --> Triaging
    Triaging --> Controlling: OrchestratorView + ControllerInput
    Controlling --> Critiquing: rules 2 (lens-fail), 8 (confirm top-up — same artifact)
    Controlling --> Generating: rules 4,9(polish),13(rewrite),14 (continue)
    Controlling --> Accepted: rule 7
    Controlling --> ConvergedUnconfirmed: rule 10
    Controlling --> ExhaustedUnresolved: rules 6,11,13(non-blocking, no rewrite left)
    Controlling --> NeedsHumanReview: rules 5,12,13(blocking, no rewrite left)
    Controlling --> Aborted: rules 1,3
    Accepted --> [*]
    ConvergedUnconfirmed --> [*]
    ExhaustedUnresolved --> [*]
    NeedsHumanReview --> [*]
    Aborted --> [*]
```

The confirming critique re-enters at `Critiquing` and returns through `Controlling` like any other
critique — no side path to a terminal state (RB-003).
