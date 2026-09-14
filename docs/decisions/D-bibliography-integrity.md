## D-bibliography-integrity — the bibliography's referential integrity is settled mechanically, because no lens can own it

**The finding.** Every critic finding must anchor to a verbatim `claim_span` in a body paragraph
(`triage.validate_issue`, `docs/isolation.md`). A defect whose whole subject is the reference list
therefore **cannot be expressed in the schema**, and none of the three lenses owns it. That is not a
gap in the prompts; it is a gap in what a critic is able to say. Synthetic reports demonstrate the
shapes: a body marker can lack a matching entry, and an entry can be listed but never cited, or
listed twice under one address. Each condition is decidable by string comparison between the
report's markers and its own `## Sources` list, with nothing fetched and nothing judged.

The warrant is the mechanism, checkable offline from the repository: `tests/test_triage.py` builds
each synthetic shape and asserts what is minted.

**The decision.** `triage.mechanical_bibliography_issues(report_text, structure, sources=None)` mints
these findings deterministically, called from `graph._critique_one` under the same gate as its
precedent `triage.mechanical_citation_issues` (D-notfound-fabrication): the **evidence** lens, and
only on a **completed** review — with verification on or off, because none of them needs a fetch.
Three checks, each stating an observable fact and an instruction the writer can apply without new
sources:

| finding | category | floor | locus |
|---|---|---|---|
| a `[n]` cited in the body with no entry numbered `n` | `uncited_claim` | major | first citing paragraph |
| an entry the body never cites | `unclear_structure` | minor | the paragraph listing it |
| two entries under one URL | `unclear_structure` | minor | the paragraph listing the duplicate |

Markers are parsed with `excerpt._MARKER` and `excerpt._cited` — ranges expanded, `## Sources`
excluded — so "cited" means here exactly what it means when `excerpt` picks anchors for a source
(D-claim-anchored-excerpts); entries are numbered as `excerpt.entry_numbers` numbers them.

Nothing is minted where the report has no `## Sources` section, or where its body carries no citation
marker at all: that report has a defect, and it is the one the writer template and the completeness
lens already own. The complete number-to-entry map resolves body markers, while at most
`search.max_source_urls` entries receive the per-entry checks and duplicate scan. This keeps the
anti-pathological work ceiling from manufacturing missing-entry findings for valid later entries.

> Superseded in part by **D-uncited-bibliography**. The claim above that the writer template and
> the completeness lens own a body with no citation marker at all was wrong: the completeness lens
> has no citation category, and prod run `run-116cc0ea4cac` shipped eleven entries and no marker
> without any mechanical finding. A report whose `## Sources` lists at least one real reference (a
> URL or an explicit number), under a body with no marker, now mints exactly **one** `uncited_claim`
> at `major`, anchored at the first quotable body sentence, and no per-entry findings. A report with
> no `## Sources` section still mints nothing, and the three checks below are unchanged once any
> marker exists.

**Every minted field is bounded on construction.** `excerpt._ENTRY_NUMBER` accepts a digit run of
any length, so a bibliography number can be longer than the fields that would interpolate it. A
number is cut to a short label (`_citation_label`, `_LABEL_MAX`) before it enters `citation_id`,
`rationale` or `instruction`, and each of those passes through `_bounded` with its schema limit, so
`RawIssue` construction cannot raise however the report numbered its list. That matters because the
mechanical block runs inside `graph._critique_one` under `pool.map`, where an uncaught
`ValidationError` would abort the whole critique node rather than fail one lens closed;
`tests/test_triage.py` pins 120- and 400-digit numbers on every check.

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
  only; `OrchestratorView` gains one more counted category and no content.
* *Author exclusion*, *termination*, and *untrusted text never instructing a generator* — untouched.
  A minted `rationale`/`instruction` is pipeline-authored, and the only report-derived text it
  carries is a bounded `claim_span`, a bounded label and a truncated URL.

QP1 holds: nothing here reads model prose to decide anything — a marker either has an entry or it
does not. QP10 holds: nothing here reads a fetched body or infers content or existence from an
address; what a page contains and whether an address resolves stay the fetch path's questions.

Two of the three findings carry `unclear_structure`, a **completeness** category, while being minted
beside an **evidence** review. That is deliberate: the fact is settled where the pipeline already
settles citation facts, and the category is the one that describes the defect to the writer. Nothing
downstream reads lens ownership off a stored `LensResult` — materiality is computed per issue
(`counts_for_convergence`), `clean_records` asks the same question, and `validate_issue` runs only on
model output. Minting an evidence-lens category the writer would misread, or inventing a new
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
  revisiting if judgement-shaped bibliography defects require it.
* *Widen `fetch.coverage` to report these.* Coverage is a report and never a gate
  (docs/convergence.md); a count that does not mint a defect does not get a report fixed.
* *Mint findings from a URL's shape — a bare domain cited for a figure as `misrepresented_source`,
  a template-looking path (`…--PR_123456`) as `fabricated_citation`.* An earlier draft of this
  decision did both, and the review was right to refuse them: QP10 reserves `misrepresented_source`
  for what fetched text shows, and the convergence rule reserves mechanical fabrication for a
  definitive not-found. A site root is a real, resolving address, and an address that looks
  templated is a suspicion, not a fact — and a false positive on the second is `blocking`. Both are
  the fetch path's and the evidence critic's questions, with the page in hand; neither is a string
  comparison against the report's own text.
* *Dropping a finding whose `instruction` reads as a withdrawal ("no action needed", "not a
  defect").* Also in the earlier draft, also rightly refused. A substring match over a
  model-authored 400-character field is not a test that the instruction asks for nothing —
  "Remove this from the list and cite the primary trial instead" matches and is actionable — and
  a drop that runs before `clean_records` can mint a clean lens record from a critic's phrasing,
  which is the launder D-writer-disputes and D-notfound-fabrication closed from the other side. A
  finding leaves the counted stream only on an explicit `upheld` verdict; the prompt-side rule that
  a critic never files an issue in order to withdraw it (D-source-fidelity-direction-and-scope) is
  where this belongs.

**Deliberately not done.**

* *Title / author / date agreement between the fetched body and the bibliography entry* —
  existence-vs-identity is a fetch-path decision with its own evidence bar and failure modes (a
  redirect to a landing page is not a retitled document). Named here so it is not mistaken for an
  oversight.
* *Surfacing URL shape to the critic as information* — a note in `fetched_sources_block` that an
  entry addresses a site root rather than a page, for the evidence critic to weigh with the body in
  hand. A prompt change with its own audition consequences; not done here.
* *Source independence* — shared authorship or data can make nominally separate entries dependent.
  That is judgement, not mechanics; a lens question, not a string one.
* *Any change to `fetch.coverage` counts or its label.* The counts keep meaning what
  D-observed-source-coverage says they mean; these findings are defects, and defects are a separate
  channel.
* *No prompt text changes*, so the audition rubric's `prompt_hash` / `rubric_hash` surface is
  untouched and no cached audition verdict goes stale.
