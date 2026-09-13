## D-bibliography-integrity — the bibliography's referential integrity is settled mechanically, because no lens can own it

**The finding.** Every critic finding must anchor to a verbatim `claim_span` in a body paragraph
(`triage.validate_issue`, `docs/isolation.md`). A defect whose whole subject is the reference list
therefore **cannot be expressed in the schema**, and none of the three lenses owns it. That is not a
gap in the prompts; it is a gap in what a critic is able to say. Ten expert reviews of recent
production reports found the same class of defect over and over, and in every case it is decidable
by string comparison against the report's own text:

* `run-80a189d3670d` (fluoride, 8 rounds): `[3]` is listed and cited nowhere in the body; `[2]` and
  `[15]` are the same WHO document listed twice under different years, and eleven in-text citations
  rest on `[2]`. The reviewer's own root-cause note: *"an entry with zero in-text citations cannot
  generate a span-anchored defect … so a defect that lives in the bibliography alone cannot be
  expressed."* Across eight rounds no critique event ever raised one.
* `run-5728bdbc1057` (DEA): `[11]` is cited in the body and has no entry; two entries are cited zero
  times; `source_coverage` reported 11/11 bodies read.
* `run-1dd853cbbfd0` (9/11): `[5]` — an Amazon product page for a conspiracy book — and `[13]` appear
  only in `## Sources`, where a reader takes them as supporting material.
* `run-c859ff5bf071` (rug): 6 of 15 entries are never cited in the body.
* `run-ebac4175d67f` (water): six bare-domain entries (`https://www.datacenterfrontier.com`) are
  cited for specific figures; coverage recorded *24 cited; 24 existence confirmed*.
* `run-188459deba66` (China GDP): placeholder URLs —
  `ft.com/content/12345678-90ab-cdef-1234-567890abcdef`, `moodys.com/research/…--PR_123456`,
  `…Measuring-the-Chinese-Economy-54321`. SPA shells and soft-404s answer 200, so the fetcher counted
  them as bodies read and the label said *22 existence confirmed*.
* `run-80a189d3670d` again, from the other end: a critic filed a finding and withdrew it in its own
  `instruction` field — *"No action needed… Removing from list per instructions"* — and it shipped at
  `major`, because the severity floor clamps a category and triage has no notion of an instruction
  that asks for nothing.

Those run ids and counts are motivation, not warrant (QP9): the audit trail is not in this repo. The
warrant is the mechanism, and it is checkable offline from the diff — `tests/test_triage.py` builds
each shape out of report text and asserts what is minted.

**The decision.** `triage.mechanical_bibliography_issues(report_text, structure, sources=None)` mints
these findings deterministically, called from `graph._critique_one` under the same gate as its
precedent `triage.mechanical_citation_issues` (D-notfound-fabrication): the **evidence** lens, and
only on a **completed** review. Five checks, each stating an observable fact and an instruction the
writer can apply without new sources:

| finding | category | floor | locus |
|---|---|---|---|
| a `[n]` cited in the body with no entry numbered `n` | `uncited_claim` | major | first citing paragraph |
| an entry the body never cites | `unclear_structure` | minor | the paragraph listing it |
| a **cited** entry whose only address is a bare domain | `misrepresented_source` | major | first citing paragraph |
| an entry whose URL is an unfilled template | `fabricated_citation` | blocking | first citing paragraph, else the entry |
| two entries under one URL | `unclear_structure` | minor | the paragraph listing the duplicate |

Markers are parsed with `excerpt._MARKER` and `excerpt._cited` — ranges expanded, `## Sources`
excluded — so "cited" means here exactly what it means when `excerpt` picks anchors for a source
(D-claim-anchored-excerpts); entries are numbered as `excerpt.entry_numbers` numbers them. Placeholder
detection is one rule over hex order: a run of **six or more** consecutive ascending or descending
digits in the path, a UUID-shaped identifier whose hex is itself counting, or a run of x's. It is
deliberately tight, because the cost of a false positive is a `blocking` finding against a real
citation; `tests/test_triage.py` pins arXiv ids, DOIs, PMIDs, ISBN-13s, commit hashes, real UUIDs and
`PR_2024_0112` as not firing.

Nothing is minted where the report has no `## Sources` section, or where its body carries no citation
marker at all: that report has a defect, and it is the one the writer template and the completeness
lens already own. At most `search.max_source_urls` entries are considered — the same
anti-pathological ceiling, which must never bind on a real bibliography.

**Sixth change, on the other side of triage.** `triage.withdraw_no_ops` drops a finding whose
`instruction` matches a withdrawal (`no action (is )?(needed|required)`, `not a defect`,
`remov(e|ing) (this|it)? from (the )?list`), applied once in `graph._triage` next to
`triage.suppress` — before tally, clean records, defects and the stagnation signature, so every
consumer sees the same stream — and counted on the `triage` event as `withdrawn`, with a
`withdrawn_issue` event per drop so the audit trail has no silent hole. This is **not** a severity
downgrade and does not touch RC-005: an instruction that requires no action is unactionable by
construction, so there is no edit a writer could have made that satisfies it and dropping it removes
no signal. The clamp governs the severity of a finding that asks for something; a finding that asks
for nothing never reaches it. (A companion PR in this series adds the prompt-side rule that a
critic must never file an issue in order to withdraw it. Its branch was unpushed when this was
written, so the mechanical half is here; the two are independent — a prompt rule the models follow
makes this a no-op, and this holds when they do not.)

**Invariants.** None of the six move.

* *Severity floors clamp up only* — unchanged, and `SEVERITY_FLOOR` is untouched. Every finding is
  minted **at** its category's floor, so `clamp` is a no-op on all of them
  (`test_every_minted_finding_is_at_its_own_severity_floor`).
* *Fail-closed lens validation* — unchanged. These bypass `validate_issue` exactly as the precedent
  does, because the fields are pipeline-authored rather than model-authored. To keep that from
  becoming a licence, `tests/test_triage.py` runs **every** minted finding through `validate_issue`
  under the lens that owns its category, so a change here cannot quietly mint a span the writer
  cannot locate. Attachment is gated on a completed review, so a failed lens is never promoted to
  countable (`test_a_failed_evidence_lens_is_not_promoted_by_a_bibliography_finding`).
* *Blind orchestrator* — the findings' text reaches the writer-facing `Defect` and the audit store
  only; `OrchestratorView` gains one more counted category and no content. The `withdrawn` count on
  the `triage` event is an audit field, not a view field.
* *Author exclusion*, *termination*, and *untrusted text never instructing a generator* — untouched.
  A minted `rationale`/`instruction` is validator-authored, and the only report-derived text it
  carries is a bounded `claim_span` and a truncated URL, the same shapes the precedent already sends.

Two of the five findings carry `unclear_structure`, a **completeness** category, while being minted
beside an **evidence** review. That is deliberate: the fact is settled where the pipeline already
settles citation facts, and the category is the one that describes the defect to the writer. Nothing
downstream reads lens ownership off a stored `LensResult` — materiality is computed per issue
(`counts_for_convergence`), `clean_records` asks the same question, and `validate_issue` runs only on
model output. Minting an evidence-lens category the writer would misread, or inventing a sixth
category, would both be worse.

**Why not the alternatives.**

* *Ask the evidence critic for it in the prompt.* It cannot answer: an orphan entry has no body
  paragraph to anchor a `claim_span` to, so the finding fails validation and takes the whole lens
  down with it (fail-closed). This is the same argument D-notfound-fabrication made about a 404 —
  a fact the pipeline can establish should not depend on a model electing to state it — with the
  stronger form that here the model *cannot* state it.
* *A bibliography-scoped locus (`S<n>.B<m>`) so critics can point at entries.* A change to the locus
  schema, to `report.parse`, to `validate_issue`, to every critic prompt and to the audition rubric,
  in exchange for letting a model re-derive by judgement what string comparison settles. Worth
  revisiting if judgement-shaped bibliography defects turn up; none of the ten reviews found one.
* *Widen `fetch.coverage` to report these.* Coverage is a report and never a gate (docs/convergence.md);
  a count that does not mint a defect does not get a report fixed. `run-ebac4175d67f`'s six bare
  domains were already inside a coverage line reading *24 existence confirmed*.
* *Fetch the placeholder URLs and let the 404 do the work.* The motivating case is exactly the one
  where that fails: `ft.com` and `moodys.com` answer 200 with an SPA shell, so the fetcher counted the
  placeholders as bodies read. The shape of the URL is the evidence, and it needs no network.
* *Lower the placeholder digit-run threshold to five* so `…-Measuring-the-Chinese-Economy-54321`
  fires. Rejected: five-digit ids are common in real URLs (a Moody's `PR_54321`, a CMS post id), and
  a false positive here is `blocking`. That one entry is caught by the bare-domain and orphan checks
  in other runs, and by a human in this one.

**Deliberately not done.**

* *Title / author / date agreement between the fetched body and the bibliography entry* — the
  `run-80a189d3670d` `[3]` case is "not found **and** wrong date", and existence-vs-identity is a
  fetch-path decision with its own evidence bar and its own failure modes (a redirect to a landing
  page is not a retitled document). Named here so it is not mistaken for an oversight.
* *Source independence* — the same authors, the same dataset, a work cited alongside its own chapter
  as if independent (`run-5728bdbc1057`). Judgement, not mechanics; a lens question, not a string one.
* *Any change to `fetch.coverage` counts or its label.* The counts keep meaning what
  D-observed-source-coverage says they mean; these findings are defects, and defects are a separate
  channel.
* *No prompt text changes*, so the audition rubric's `prompt_hash` / `rubric_hash` surface is
  untouched and no cached audition verdict goes stale.
