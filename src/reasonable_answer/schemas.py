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
    note: str = ""
