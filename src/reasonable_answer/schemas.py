"""Closed schemas for every boundary in the pipeline.

Three of these are load-bearing for isolation (docs/isolation.md):

* ``RawIssue``   — what a critic may emit. Anything outside this fails the lens.
* ``Defect``     — what reaches the next writer. No provenance, no verdict language.
* ``OrchestratorView`` — the *only* thing the blind LLM orchestrator ever sees:
  bounded ints and enums, no identifiers, no hashes, no free text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .taxonomy import Category, Lens, Severity

# Bounded lengths for untrusted, model-authored text fields (docs/architecture.md).
MAX_SPAN = 400
MAX_RATIONALE = 400
MAX_INSTRUCTION = 400
MAX_EXPECTED_SUPPORT = 300
MAX_CITATION_ID = 120
MAX_ISSUES_PER_LENS = 25
MAX_DISPUTE_GROUNDS = 400
MAX_EVIDENCE_QUOTE = 400
MAX_EVIDENCE_URL = 500
MAX_DISPUTES = 10
MAX_LOCATOR = 120
MAX_SUPPORT_SPAN = 400
MAX_SUPPORTED_CLAIM = 400
MAX_SUPPORT_ENTRIES = 40


class StructuralRef(BaseModel):
    """A bounded structural locus — never free text (RB-007)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section: int = Field(ge=0, le=999)
    paragraph: int = Field(ge=0, le=999)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"S{self.section}.P{self.paragraph}"


class RawIssue(BaseModel):
    """One issue as emitted by a critic lens. Untrusted; strictly validated."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    severity: Severity
    locus: StructuralRef
    claim_span: str = Field(min_length=1, max_length=MAX_SPAN)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE)
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION)
    related_span: str | None = Field(default=None, max_length=MAX_SPAN)
    citation_id: str | None = Field(default=None, max_length=MAX_CITATION_ID)
    expected_support: str | None = Field(default=None, max_length=MAX_EXPECTED_SUPPORT)


class CritiqueOutput(BaseModel):
    """The whole of a critic's response."""

    model_config = ConfigDict(extra="forbid")

    issues: list[RawIssue] = Field(default_factory=list, max_length=MAX_ISSUES_PER_LENS)


class LensResult(BaseModel):
    """Outcome of running one lens against one artifact. Audit-side (has provenance)."""

    model_config = ConfigDict(extra="forbid")

    lens: Lens
    artifact_hash: str
    critic_alias: str
    critic_identity: str
    artifact_author_identity: str
    failed: bool = False
    failure_reason: str | None = None
    issues: list[RawIssue] = Field(default_factory=list)
    attempt: int = 1
    confirm_state: bool = False


class Defect(BaseModel):
    """A generator-facing fix-task. Depersonalized: no lens, no model, no verdict."""

    model_config = ConfigDict(extra="forbid")

    locus: StructuralRef
    category: Category
    severity: Severity
    claim_span: str
    rationale: str
    instruction: str
    related_span: str | None = None
    citation_id: str | None = None
    expected_support: str | None = None
    #: True when a writer disputed this finding and adjudication overruled the
    #: dispute (D-writer-disputes). A bare boolean by design: it tells the writer "this task was
    #: independently reviewed and stands — apply it, do not dispute it again",
    #: carrying no verdict prose and no provenance.
    adjudicated: bool = False


class Dispute(BaseModel):
    """A writer's challenge to one fix-task: a claim that the task is factually
    wrong. Writer-authored, therefore untrusted; strictly bounded (D-writer-disputes)."""

    model_config = ConfigDict(extra="forbid")

    task_index: int = Field(ge=0, le=99)
    grounds: str = Field(min_length=1, max_length=MAX_DISPUTE_GROUNDS)
    evidence_url: str | None = Field(default=None, max_length=MAX_EVIDENCE_URL)
    evidence_quote: str | None = Field(default=None, max_length=MAX_EVIDENCE_QUOTE)


class WriterDisputes(BaseModel):
    """The whole of a writer's dispute pass. An empty list is the normal outcome."""

    model_config = ConfigDict(extra="forbid")

    disputes: list[Dispute] = Field(default_factory=list, max_length=MAX_DISPUTES)


class ArbiterVerdict(BaseModel):
    """The arbiter's entire output: one boolean and a bounded audit-only reason.

    The reason never enters any model context — it goes to the audit store."""

    model_config = ConfigDict(extra="forbid")

    dispute_upheld: bool
    reason: str = Field(min_length=1, max_length=400)


AdjudicationVerdict = Literal["upheld", "overruled", "dismissed"]
AdjudicationMethod = Literal[
    "mechanical",
    "arbiter",
    "no_eligible_arbiter",
    "arbiter_failed",
    "budget_exhausted",
    "duplicate",
    "invalid",
]


class AdjudicationRecord(BaseModel):
    """One entry in the per-run adjudicated-facts registry (D-writer-disputes). Lives in
    checkpointed graph state; only `upheld` records ever suppress anything."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    claim_span: str
    verdict: AdjudicationVerdict
    method: AdjudicationMethod
    round: int


class SupportEntry(BaseModel):
    """One link in the traceability chain a writer that read its sources can emit
    (D-writer-source-reads): citation id -> URL -> locator -> verbatim support span ->
    supported claim.

    Writer-authored and therefore untrusted, and bounded field by field like every
    other model-authored text in this file. It is **audit-side only**: nothing here
    reaches another model's context, becomes a defect, or is visible to the
    orchestrator or the controller — see `support.check`, which is the only consumer.
    """

    model_config = ConfigDict(extra="forbid")

    #: The marker as it appears in the report body — "1" for a claim cited [1].
    citation_id: str = Field(min_length=1, max_length=MAX_CITATION_ID)
    #: The source's URL, or the identifier (a DOI) where that is what was resolved.
    url: str = Field(min_length=1, max_length=MAX_EVIDENCE_URL)
    #: Where in the source the support sits: page, chapter, section, table. Optional
    #: because a short web page genuinely has no locator, and inventing one would be
    #: worse than omitting it — but a book cited with no locator is exactly the
    #: bibliography-level provenance this field exists to expose.
    locator: str | None = Field(default=None, max_length=MAX_LOCATOR)
    #: Quoted from the source, verbatim. Mechanically checked against the body when
    #: one was read.
    support_span: str = Field(min_length=1, max_length=MAX_SUPPORT_SPAN)
    #: Quoted from the report, verbatim. Checked against the report the same way.
    claim: str = Field(min_length=1, max_length=MAX_SUPPORTED_CLAIM)


class SupportManifest(BaseModel):
    """The whole of one writer's traceability pass. An empty list is a valid answer —
    a draft whose reads all failed has nothing to trace."""

    model_config = ConfigDict(extra="forbid")

    entries: list[SupportEntry] = Field(
        default_factory=list, max_length=MAX_SUPPORT_ENTRIES
    )


class CleanRecord(BaseModel):
    """Immutable per-lens attestation, keyed to one artifact hash (RC-001/RC-002)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_hash: str
    lens: Lens
    critic_identity: str
    artifact_author_identity: str


class SeverityCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocking: int = 0
    major: int = 0
    minor: int = 0


AcceptanceLiteral = Literal["none", "weak_met", "strong_met"]


class OrchestratorView(BaseModel):
    """The blind LLM's ENTIRE input. Bounded ints/enums only — no ids, no text.

    Noninterference (RB-008) is defined over this type: two runs with an equal
    ``OrchestratorView`` must get the same recommendation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: dict[str, SeverityCounts]
    totals: SeverityCounts
    delta_material_vs_prev: int
    lenses_failed: int
    round: int
    min_ticks: int
    hard_cap: int
    roster_size: int
    lens_cleared: dict[str, int]
    acceptance: AcceptanceLiteral
    polish_used: int
    polish_cap: int
    stagnation_count: int
    cycle_detected: bool


class OrchestratorRecommendation(BaseModel):
    """The blind orchestrator's only authority: the minor-polish judgment (rule 9)."""

    model_config = ConfigDict(extra="forbid")

    polish_recommended: bool
    reason_code: Literal[
        "material_issues_remain",
        "minor_issues_worth_polishing",
        "minor_issues_not_worth_polishing",
        "clean",
    ]


class LensStatus(BaseModel):
    """Per-lens acceptance predicates for the current artifact hash."""

    model_config = ConfigDict(extra="forbid")

    lens: Lens
    cleared_count: int
    eligible_count: int
    unused_eligible: int

    @property
    def toppable(self) -> bool:
        return self.cleared_count < 2 and self.unused_eligible > 0

    @property
    def roster_limited(self) -> bool:
        return self.eligible_count < 2


class ControllerInput(BaseModel):
    """Everything the deterministic controller reads. Blind to report *content*."""

    model_config = ConfigDict(extra="forbid")

    view: OrchestratorView
    fatal: bool
    fatal_reason: str | None = None
    run_id: str
    artifact_hash: str
    artifact_hash_history: list[str]
    author_identity: str
    lens_status: list[LensStatus]
    critique_attempts_remaining: int
    confirmation_attempts_remaining: int
    polish_recommended: bool
    stagnation_limit: int
    cycle_period: int
    #: Rule 13's escape valve (D-scoped-revision). Deliberately NOT on `OrchestratorView`:
    #: `polish_used`/`polish_cap` are there because rule 9 is the blind LLM's one
    #: authority and it needs to see its own budget. Rule 13 is fully deterministic, so
    #: putting these in the view would widen the RB-008 noninterference surface to buy
    #: nothing.
    rewrites_used: int = 0
    rewrite_cap: int = 0


Terminal = Literal[
    "accepted",
    "converged_unconfirmed",
    "exhausted_unresolved",
    "needs_human_review",
    "aborted",
]

Action = Literal["generate", "recritique", "terminal"]


class Decision(BaseModel):
    """The controller's verdict for one tick. Fully explained by `rule`."""

    model_config = ConfigDict(extra="forbid")

    rule: int
    action: Action
    terminal_status: Terminal | None = None
    recritique_lenses: list[Lens] = Field(default_factory=list)
    polish: bool = False
    #: Rule 13's stalled-patch-chain rewrite (D-scoped-revision): this generation ignores
    #: `revision.mode` and asks for the whole document. Mutually exclusive with `polish`
    #: — rule 9 only fires when `material == 0` and rule 13 only when `material > 0`.
    full_rewrite: bool = False
    note: str = ""


# --------------------------------------------------------------- question refinement

#: The six bounded reframe transforms (docs/question-refinement.md's taxonomy table).
#: `question_behind_the_question` ships disabled (RefineConfig default) — it is the
#: only transform that authorizes the model to infer an unstated concern, so it stays
#: off until a paired-fixture audition passes (D-question-refinement).
REFINE_TRANSFORMS = (
    "split_the_either_or",
    "check_the_premise_first",
    "name_the_outcome",
    "surface_the_real_goal",
    "ask_whats_answerable",
    "question_behind_the_question",
)

MAX_REFINE_LABEL = 40
MAX_REFINE_QUESTION = 200

RefineTransform = Literal[
    "split_the_either_or",
    "check_the_premise_first",
    "name_the_outcome",
    "surface_the_real_goal",
    "ask_whats_answerable",
    "question_behind_the_question",
]


class RefinementSuggestion(BaseModel):
    """One reframe chip, as the model must emit it. Deterministic post-validation
    (web/refine.py) applies stricter checks on top of this — an in-schema-bounds
    entry can still be dropped for e.g. missing a trailing '?' or duplicating
    another suggestion."""

    model_config = ConfigDict(extra="forbid")

    transform: RefineTransform
    label: str = Field(min_length=1, max_length=MAX_REFINE_LABEL)
    question: str = Field(min_length=1, max_length=MAX_REFINE_QUESTION)


class RefinementSuggestions(BaseModel):
    """The whole of a refine call's output. An empty list is the expected, common
    outcome for a well-posed question — never treated as a schema failure."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[RefinementSuggestion] = Field(default_factory=list, max_length=3)
