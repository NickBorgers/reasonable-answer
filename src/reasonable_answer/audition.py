"""Critic auditioning — does a rostered model actually perform the lens it holds?

`validate_roster_health` checks that a roster is *structurally* sound: pools non-empty,
identities distinct, every lens with an eligible non-author, two families per lens.
`ra doctor` additionally checks that each model is *mechanically* usable: structured
output, tool calls. Neither asks whether a model can find a defect.

That gap is not hypothetical. In run-d5934276fafd two models held first position on
their lenses and returned zero issues on every call they ever made, across artifacts
that other critics then found six and ten material issues in. The run terminated with
counters, statuses and a label that all read as though three lenses had reviewed it.
A silent critic turns the system's central claim — *no eligible reviewer can find a
material defect* — into a tautology, and nothing downstream can tell the difference.

So: fixtures with known planted defects, a **mechanical** grader, and a verdict per
(model, lens).

Four design commitments worth stating plainly.

**The grader is a pure function and never an LLM.** An LLM grader is precisely the
component whose reliability is in question here; using one would make the harness's
trustworthiness depend on the property the harness exists to measure. Grading is
category matching plus a structural-locus window, and nothing else.

**Both directions gate.** A critic that flags everything scores perfect sensitivity
and is worse than useless: it never lets a run converge, it drains the critique
budget, it drives `stagnation_count` to the limit, and rule 13 — after spending its
bounded rewrite (D-scoped-revision) — terminates `exhausted_unresolved` on a report that
was fine. Silence and noise are two ways to fail the same job, so each direction carries
one hardcoded gate no threshold setting can reach — zero obvious planted defects found,
and zero clean reviews of a sound control (D-obvious-per-lens). The thresholds around
them are calibration; those two are not.

**A verdict covers the whole corpus, or it is not a verdict.** A call that fails the
schema is neither a miss nor a false positive, so it is excluded from grading — but
excluding it from the *denominators* too would let a model that reliably breaks on one
fixture have that fixture deleted from its own exam. Every rate below is therefore
paired with a coverage count, and a fixture that never produced a single gradable
review makes the verdict `unfit` before any rate is read (D-audition-failure-coverage).

**The measurement is taken with no fetched sources**, which is a floor strictly below
what production ever hands its evidence critic once a report cites anything at all
(`AUDITION_SOURCE_MODE`, D-audition-source-mode). `sources=None` here matches only the
"nothing to check" case of zero citations extracted from the report — production's
failed-fetch case (a citation that is paywalled, blocked or offline) still renders a
`fetched_sources_block` naming the failure, with the instruction not to re-raise a
not-found `triage.mechanical_citation_issues` has already settled mechanically
(D-existence-vs-body). A verdict here says the model can or cannot perform the lens with
no source scaffolding whatsoever; it says nothing about the sharpened, sources-present
prompt the deployment's evidence lens runs when a page arrives, and nothing about the
weaker but still-present on-its-face prompt production runs on a citation it attempted
and failed to fetch.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import prompts
from .config import AuditionConfig, AuditionThresholds, ConfigError, ReviewConfig, Roster
from .critique import critique_once
from .llm import LLMClient
from .schemas import LensResult, RawIssue, StructuralRef
from .taxonomy import (
    LENS_CATEGORIES,
    MATERIAL_FLOOR,
    SEVERITY_FLOOR,
    SEVERITY_RANK,
    Category,
    Lens,
    counts_for_convergence,
)

#: Fixture corpus shipped with the source tree.
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "audition"

#: A detection may sit this many paragraphs away from the planted locus and still
#: count. Paragraph indexing is genuinely ambiguous at section boundaries and across
#: list blocks, and a critic that names the neighbouring paragraph has still found it.
LOCUS_PARAGRAPH_TOLERANCE = 1

#: Bumped **by hand** when the grading *code* changes in a way a cached verdict cannot
#: survive (D-audition-rubric-identity). `rubric_hash` mixes this into the cache key, so
#: a bump invalidates every stored entry and every slot reads *not audited* until it is
#: re-measured. Bump it when you change:
#:
#:   * `_is_material` — which effective severities count as material;
#:   * `grade` — the strict / same-lens / severity-agreement matching rules, or the
#:     handling of `PlantedDefect.anywhere`;
#:   * `_locus_matches` — the shape of the locus window;
#:   * `run_assignment`'s accounting — what increments which counter, the `Tier.OBVIOUS`
#:     gate, or the treatment of a failed lens.
#:
#: A `judge` gate-order or threshold change needs no bump: `judge` runs at read time
#: against the live `cfg.thresholds`, so the verdict already follows the current rules.
#: The one `judge` change that would need one — a gate reading a `Metrics` counter older
#: entries never collected, which defaults to 0 and reads as a measured zero — is caught
#: automatically, because `rubric_hash` covers the `Metrics` field set.
#:
#: Bumped to 2 by D-minor-floor-fixtures: `grade`'s `severity_agrees` is now derived
#: from `exact` (the same issues that produce `strict`) rather than computed
#: independently, and the comparison is `>=` the floor rather than `==` it. A cached
#: `Metrics.severity_agreements` recorded under version 1 could exceed `strict_hits` —
#: exactly the bug this decision fixes — so it must not be read as though it meant the
#: same thing as a version-2 count.
RUBRIC_VERSION = 2

#: `{{slot}}` in a fixture artifact, substituted from the manifest's slot table.
_SLOT = re.compile(r"\{\{([a-z0-9_]+)\}\}")


class Tier(str, Enum):
    """Difficulty tier.

    Only `obvious` fixtures gate a fail-closed verdict. An `obvious` defect is one a
    competent critic *must* catch — a citation to a paper that does not exist, a
    paragraph that contradicts an earlier one outright. `subtle` fixtures are
    diagnostic: they rank models against each other but must never make a roster
    unusable, because a threshold tuned on subtle cases would fail models that are
    perfectly adequate in production.
    """

    OBVIOUS = "obvious"
    MODERATE = "moderate"
    SUBTLE = "subtle"


class PlantedDefect(BaseModel):
    """One defect deliberately placed in a fixture, with its ground-truth locus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Category
    locus: StructuralRef
    #: Skip the locus window and match this defect anywhere in the artifact.
    #:
    #: Some defects have no honest location. An `omitted_counterargument` or
    #: `incomplete_answer` is defined by absence: a critic may reasonably anchor it to
    #: the thesis that overreaches, the section where material belonged, or the partial
    #: conclusion. Grading those as misses would measure agreement with the fixture
    #: author's filing choice rather than the critic's ability to notice the omission.
    #: `locus` stays required as documentation of where the fixture author considers it
    #: to live.
    anywhere: bool = False
    #: Human-facing only. Never used for matching — matching on prose would either
    #: need an LLM or degenerate into brittle substring checks.
    note: str = ""


class Fixture(BaseModel):
    """A fixture artifact plus its ground truth."""

    model_config = ConfigDict(extra="forbid")

    id: str
    #: The lens responsible for this fixture's planted defects. `None` on a control,
    #: which has none: a control is graded by *every* lens (see `for_lens`), so naming
    #: one would assert a scope nothing honors. D-control-soundness.
    lens: Lens | None = None
    tier: Tier = Tier.MODERATE
    question: str
    artifact: str
    defects: tuple[PlantedDefect, ...] = ()
    #: Slot table: name -> candidate surface forms, chosen by seeded substitution.
    slots: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @property
    def is_control(self) -> bool:
        """A control carries no planted defect and measures the opposite failure."""
        return not self.defects


def _resolve_slots(text: str, slots: dict[str, tuple[str, ...]], seed: str) -> str:
    """Substitute `{{slot}}` deterministically from a corpus-derived seed.

    The corpus lives in a public repo, so it will reach training data and sensitivity
    will drift upward for reasons unrelated to capability. Rotating the surface forms
    breaks a memorized answer while leaving the planted defect's *structure* intact —
    the fabricated citation is still fabricated, whatever it is named.

    Seeded rather than random so a fixture id yields the same instantiation on every
    machine; an audition that varied run to run could not be cached or compared.
    """
    rng = random.Random(seed)
    chosen = {name: rng.choice(list(options)) for name, options in sorted(slots.items())}

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in chosen:
            raise FixtureError(f"artifact references undefined slot '{{{{{name}}}}}'")
        return chosen[name]

    return _SLOT.sub(sub, text)


class FixtureError(RuntimeError):
    """A malformed fixture. Fatal — a corpus that does not load cannot grade."""


@dataclass(frozen=True)
class FixtureSet:
    """The loaded corpus plus the hash that keys every cached result against it."""

    fixtures: tuple[Fixture, ...]
    corpus_hash: str

    def for_lens(self, lens: Lens) -> tuple[Fixture, ...]:
        """Fixtures a given lens is responsible for, controls included.

        Controls belong to every lens: "does this model invent defects" is a question
        about the model, not about the planted category, and a control graded on only
        one lens would leave the other two unmeasured on noise.

        The load-time consequence is D-control-soundness: a control must be sound under
        every lens, not merely under the one whose noise it was written to measure. A
        control carrying a real uncited claim scores every competent evidence critic as
        an inventor of defects.
        """
        return tuple(f for f in self.fixtures if f.lens is lens or f.is_control)


def load_fixtures(directory: Path | None = None) -> FixtureSet:
    """Load and validate the corpus, hashing raw bytes before substitution.

    The hash covers the *templates*, so editing a fixture invalidates every cached
    result derived from it — which is the point. A corpus edit changes what is being
    measured, and a verdict carried across that edit would be a claim about a
    measurement that no longer exists.
    """
    directory = directory or DEFAULT_FIXTURE_DIR
    if not directory.is_dir():
        raise FixtureError(f"fixture corpus not found at {directory}")

    digest = hashlib.sha256()
    fixtures: list[Fixture] = []

    for fixture_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        artifact_path = fixture_dir / "artifact.md"
        manifest_path = fixture_dir / "manifest.yaml"
        if not artifact_path.exists() or not manifest_path.exists():
            raise FixtureError(
                f"fixture '{fixture_dir.name}' needs both artifact.md and manifest.yaml"
            )

        raw_artifact = artifact_path.read_bytes()
        raw_manifest = manifest_path.read_bytes()
        digest.update(fixture_dir.name.encode())
        digest.update(raw_artifact)
        digest.update(raw_manifest)

        manifest = yaml.safe_load(raw_manifest.decode()) or {}
        manifest["artifact"] = raw_artifact.decode()
        manifest.setdefault("id", fixture_dir.name)
        # `kind: control` is sugar for "no defects"; keeping both would let a manifest
        # declare itself a control while carrying planted defects.
        kind = manifest.pop("kind", None)
        if kind == "control" and manifest.get("defects"):
            raise FixtureError(f"fixture '{fixture_dir.name}' is kind: control but has defects")
        _check_control_manifest(fixture_dir.name, manifest, is_control=not manifest.get("defects"))
        if "slots" in manifest and manifest["slots"]:
            manifest["slots"] = {k: tuple(v) for k, v in manifest["slots"].items()}

        try:
            fixture = Fixture.model_validate(manifest)
        except Exception as exc:  # pydantic ValidationError, plus yaml shape errors
            raise FixtureError(f"fixture '{fixture_dir.name}': {exc}") from exc

        _check_lens_ownership(fixture)
        _check_planted_floor_is_material(fixture)
        fixtures.append(fixture)

    if not fixtures:
        raise FixtureError(f"fixture corpus at {directory} is empty")

    corpus_hash = digest.hexdigest()[:16]
    resolved = tuple(
        f.model_copy(update={"artifact": _resolve_slots(f.artifact, f.slots, corpus_hash + f.id)})
        for f in fixtures
    )
    return FixtureSet(fixtures=resolved, corpus_hash=corpus_hash)


def _check_control_manifest(name: str, manifest: dict, is_control: bool) -> None:
    """A control declares neither `lens` nor `tier`; a planted fixture must declare a lens.

    Both fields are read only on the planted path — `for_lens` hands controls to every
    lens whatever they claim, `_check_lens_ownership` has no defects to check, and
    `obvious_total` counts planted defects only. A control declaring `lens: evidence`
    therefore asserts a scope nothing enforces, and reads as though the corpus had been
    checked for soundness under that one lens when no such check exists in either
    direction (D-control-soundness). Forbidding the field is the mechanical half of that
    decision; the soundness itself is not mechanically checkable and rests on review.
    """
    if is_control:
        declared = [f for f in ("lens", "tier") if f in manifest]
        if declared:
            raise FixtureError(
                f"fixture '{name}' is a control but declares {', '.join(declared)} — a "
                f"control is graded by every lens and gates no sensitivity threshold, so "
                f"the field would assert a scope nothing honors (D-control-soundness)"
            )
    elif "lens" not in manifest:
        raise FixtureError(
            f"fixture '{name}' plants defects but declares no lens — nothing would grade it"
        )


def _check_lens_ownership(fixture: Fixture) -> None:
    """A planted category must belong to the lens declared responsible for it.

    Without this a fixture could plant an `uncited_claim` and declare itself a `logic`
    fixture, then grade every logic critic as blind — `triage.validate_issue` rejects
    an out-of-scope category, so no correct critic could ever score.
    """
    if fixture.lens is None:  # control: no defects to own, checked at load
        return
    owned = LENS_CATEGORIES[fixture.lens]
    for defect in fixture.defects:
        if defect.category not in owned:
            raise FixtureError(
                f"fixture '{fixture.id}': category '{defect.category.value}' is not in "
                f"scope for lens '{fixture.lens.value}' — no valid critic could report it"
            )


def _check_planted_floor_is_material(fixture: Fixture) -> None:
    """A planted category must floor at or above `MATERIAL_FLOOR`. D-minor-floor-fixtures.

    Every detection credit in `grade` requires post-clamp materiality, because that is
    what triage would count and what a run would ever be revised for. A category whose
    floor is *below* that line — `loaded_language` at `minor` under D-social-bias,
    `stylistic`, `unclear_structure` — can therefore only be detected by a critic that
    escalates past its own floor. Planting one grades the doctrinal reading of the
    category as blindness: report the planted category at the severity the taxonomy
    assigns it and you score `strict = False`, `same_lens = False`. The rubric would be
    measuring willingness to escalate, in a metric that feeds `MARGINAL`.

    This is the mechanical half of the decision, in the same sense as
    `_check_control_manifest`. It does not say a minor-floor category is unmeasurable —
    it says it is not measurable *by this grader*, whose one bar is materiality.
    """
    for defect in fixture.defects:
        floor = SEVERITY_FLOOR[defect.category]
        if SEVERITY_RANK[floor] < SEVERITY_RANK[MATERIAL_FLOOR]:
            raise FixtureError(
                f"fixture '{fixture.id}': category '{defect.category.value}' floors at "
                f"'{floor.value}', below the material floor '{MATERIAL_FLOOR.value}' — a "
                f"critic reporting it at its own floor would score as blind, so the "
                f"fixture would grade escalation rather than detection "
                f"(D-minor-floor-fixtures)"
            )


# ------------------------------------------------------------------- grading


@dataclass(frozen=True)
class Detection:
    """Whether one planted defect was found, and how precisely."""

    defect: PlantedDefect
    strict: bool
    same_lens: bool
    #: The critic found it *and* rated it at or above the category floor unaided — the
    #: clamp had nothing to lift. Implies `strict` by construction (D-minor-floor-fixtures):
    #: it is derived from the same issues, so it can never count something `strict` did
    #: not. Escalation is agreement, not disagreement — a critic proposing `blocking` on a
    #: major-floor category is exercising the one direction RC-005 permits.
    severity_agrees: bool


def _locus_matches(planted: StructuralRef, reported: StructuralRef) -> bool:
    return (
        planted.section == reported.section
        and abs(planted.paragraph - reported.paragraph) <= LOCUS_PARAGRAPH_TOLERANCE
    )


def _is_material(issue: RawIssue) -> bool:
    """Whether production would count this issue at all — the production predicate
    itself (`taxonomy.counts_for_convergence`), not a restatement of it.

    So the severity floor is applied, and `stylistic` is discarded whatever severity
    the critic gave it. Both halves matter here. A critic may legally escalate a
    stylistic note to `major`; triage then drops it from the defect list, the tally,
    the provenance registry and the clean-record test alike. Crediting that as a
    detection would score a critic as having caught a defect that, in a real run,
    would have sailed through — and counting it as invented noise would fail a critic
    for findings that cannot stagnate anything (D-audition-stylistic-parity).
    """
    return counts_for_convergence(issue.category, issue.severity)


def grade(fixture: Fixture, result: LensResult) -> tuple[Detection, ...]:
    """Match a critic's issues against ground truth. Pure — no client, no I/O.

    A planted defect counts as found when a reported issue lands within the locus
    window, would survive production triage (`_is_material`), and has a category that
    either matches exactly (`strict`) or belongs to the same lens (`same_lens`). The
    relaxed form exists because critics reasonably disagree between, say,
    `uncited_claim` and `misrepresented_source` on the same sentence, and scoring that
    as a miss would penalize a critic that is doing its job. It stays bounded by the
    materiality test: `stylistic` is in every lens's category set, so without it a
    nitpick on the right paragraph — at any severity — would score as a detection of
    whatever was planted there.

    Both numbers are reported; neither is the whole story alone.

    Every credit here is post-clamp material, so a planted category has to floor at or
    above `MATERIAL_FLOOR` for any of it to be reachable — `_check_planted_floor_is_material`
    holds the corpus to that at load (D-minor-floor-fixtures).
    """
    detections: list[Detection] = []
    for defect in fixture.defects:
        candidates = [
            i
            for i in result.issues
            if defect.anywhere or _locus_matches(defect.locus, i.locus)
        ]
        exact = [i for i in candidates if i.category == defect.category and _is_material(i)]
        strict = bool(exact)
        same_lens = any(
            i.category in LENS_CATEGORIES[fixture.lens] and _is_material(i) for i in candidates
        )
        # Derived from `exact`, so the severity numerator is a subset of the `strict`
        # denominator it is divided by. Testing `>=` rather than `==` the floor keeps a
        # legal escalation on the agreeing side.
        floor = SEVERITY_FLOOR[defect.category]
        severity_agrees = any(SEVERITY_RANK[i.severity] >= SEVERITY_RANK[floor] for i in exact)
        detections.append(
            Detection(
                defect=defect,
                strict=strict,
                same_lens=same_lens,
                severity_agrees=severity_agrees,
            )
        )
    return tuple(detections)


def material_issue_count(result: LensResult) -> int:
    """Material issues a critic raised — the noise measure on control fixtures."""
    return sum(1 for i in result.issues if _is_material(i))


# ------------------------------------------------------------------- metrics


class Metrics(BaseModel):
    """Everything measured for one (identity, lens). Serialized into the cache."""

    model_config = ConfigDict(extra="forbid")

    alias: str
    identity: str
    lens: Lens

    #: Fixtures this (identity, lens) owed a measurement on — `for_lens`, controls
    #: included. Required rather than defaulted on purpose: a record that cannot say
    #: what it owed cannot say whether it measured all of it, so an entry written
    #: before coverage accounting existed fails validation and `load_cache` drops it
    #: to *not audited* — never to a pass (D-audition-failure-coverage).
    fixtures_owed: int = Field(ge=0)
    #: Ids of owed fixtures that never produced one gradable review, across every
    #: repetition. Not the same as "looked and found nothing": a graded zero is a
    #: measured miss and lands in the denominators below, whereas these fixtures are
    #: absent from them entirely, which is what makes them dangerous.
    uncovered_fixtures: tuple[str, ...] = ()

    planted_total: int = 0
    strict_hits: int = 0
    same_lens_hits: int = 0
    severity_agreements: int = 0

    obvious_total: int = 0
    obvious_hits: int = 0

    control_runs: int = 0
    control_material_issues: int = 0
    control_clean_runs: int = 0

    calls: int = 0
    schema_failures: int = 0
    latencies: tuple[float, ...] = ()

    @model_validator(mode="after")
    def _coverage_is_consistent(self) -> Metrics:
        if len(self.uncovered_fixtures) > self.fixtures_owed:
            raise ValueError(
                f"{len(self.uncovered_fixtures)} uncovered fixtures against "
                f"{self.fixtures_owed} owed — the record contradicts itself"
            )
        return self

    @property
    def fixtures_covered(self) -> int:
        """Owed fixtures that produced at least one gradable review."""
        return self.fixtures_owed - len(self.uncovered_fixtures)

    @property
    def strict_sensitivity(self) -> float:
        return _ratio(self.strict_hits, self.planted_total)

    @property
    def lens_sensitivity(self) -> float:
        return _ratio(self.same_lens_hits, self.planted_total)

    @property
    def obvious_sensitivity(self) -> float:
        return _ratio(self.obvious_hits, self.obvious_total)

    @property
    def severity_agreement(self) -> float:
        """Share of strict detections the critic rated at or above the floor unaided.

        A rate, so it needs the numerator inside the denominator; `grade` derives both
        from the same issues, which is what makes that hold. It did not before
        D-minor-floor-fixtures — `severity_agrees` was independent of material detection,
        so a non-material report of a minor-floor category incremented the numerator while
        contributing nothing to `strict_hits`, and the ratio could exceed 1.0.
        """
        return _ratio(self.severity_agreements, self.strict_hits)

    @property
    def control_material_rate(self) -> float:
        """Mean material issues invented per control fixture."""
        return _ratio(self.control_material_issues, self.control_runs)

    @property
    def control_clean_rate(self) -> float:
        return _ratio(self.control_clean_runs, self.control_runs)

    @property
    def schema_failure_rate(self) -> float:
        return _ratio(self.schema_failures, self.calls)

    @property
    def latency_p50(self) -> float:
        return _percentile(self.latencies, 0.50)

    @property
    def latency_p95(self) -> float:
        return _percentile(self.latencies, 0.95)


def _ratio(numerator: int, denominator: int) -> float:
    # A denominator of zero means "not measured". Returning 0.0 would read as a
    # failing score and could make an unaudited model look unfit; callers gate on
    # `planted_total`/`control_runs` before trusting a rate.
    return numerator / denominator if denominator else 0.0


def _percentile(values: tuple[float, ...], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[idx]


class Verdict(str, Enum):
    FIT = "fit"
    MARGINAL = "marginal"
    UNFIT = "unfit"
    #: Measured, but on too little evidence to say anything.
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Judgement:
    verdict: Verdict
    reasons: tuple[str, ...] = ()


def judge(metrics: Metrics, thresholds: AuditionThresholds) -> Judgement:
    """Turn metrics into a verdict. Pure.

    Order matters: every fail-closed condition is checked before any warn condition,
    so a model that is both noisy and blind reports `unfit` rather than `marginal`.
    The mechanical gates — schema failures, then fixture coverage — come before the
    judgement gates, so a model that cannot be measured is reported as unmeasurable
    rather than graded on whatever fraction of the corpus survived.
    """
    reasons: list[str] = []

    if metrics.calls == 0:
        return Judgement(Verdict.INSUFFICIENT, ("no calls recorded",))

    # Checked before the "no fixtures graded" guard below, because a model that fails
    # every call grades nothing *because* it is broken. Reporting that as INSUFFICIENT
    # would describe a definite, reproducible failure as an absence of evidence.
    if metrics.schema_failure_rate > thresholds.max_schema_failure_rate:
        reasons.append(
            f"schema failure rate {metrics.schema_failure_rate:.0%} exceeds "
            f"{thresholds.max_schema_failure_rate:.0%} — lens results would fail closed"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    # Coverage before any rate, because coverage is what the rates are *over*. A
    # fixture whose every repetition failed contributes nothing to `planted_total`,
    # `obvious_total` or `control_runs`, so the sensitivity and noise rates below would
    # be computed across a corpus subset the model selected by failing — and a model
    # failing under the schema gate's tolerance (3 of 15 calls is exactly one fixture
    # of five, at 20%) could delete a whole fixture from its own exam and still read
    # `fit`. `unfit` rather than `insufficient` for the same reason the schema gate
    # above is: being asked `repetitions` times and returning nothing gradable every
    # time is a definite, reproducible failure, not an absence of evidence.
    if metrics.uncovered_fixtures:
        reasons.append(
            f"never produced a gradable review of {len(metrics.uncovered_fixtures)} of "
            f"{metrics.fixtures_owed} fixtures ({', '.join(metrics.uncovered_fixtures)}) — "
            f"every rate would be measured over the rest of the corpus only"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    if metrics.planted_total == 0 and metrics.control_runs == 0:
        return Judgement(Verdict.INSUFFICIENT, ("no fixtures graded",))

    # A model that finds *nothing* obvious is unfit under every threshold setting.
    # This is the llama-4-scout signature, and no amount of threshold tuning should
    # be able to permit it — a lens staffed by this model is not being reviewed.
    if metrics.obvious_total > 0 and metrics.obvious_hits == 0:
        reasons.append(
            f"found 0 of {metrics.obvious_total} obvious planted defects — this lens "
            f"would be unstaffed"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    if metrics.control_runs and metrics.control_material_rate > thresholds.max_control_material_rate:
        reasons.append(
            f"invents {metrics.control_material_rate:.2f} material issues per sound "
            f"report — runs would stagnate rather than converge"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    # The mirror of the silence gate above, and unreachable by threshold tuning for the
    # same reason (D-obvious-per-lens). A critic that never once returns a clean lens on
    # a sound report never lets one converge: rule 3 needs a clean record, and this model
    # cannot produce one whatever the report says. The rate gate above misses the cheapest
    # version of that strategy — exactly one material issue per artifact scores a
    # `control_material_rate` of 1.00, which is not *greater than* the 1.0 default — so
    # the strategy that most perfectly defeats the harness slipped through the noise
    # direction while scoring full marks on the sensitivity direction.
    if metrics.control_runs and metrics.control_clean_runs == 0:
        reasons.append(
            f"never returned a clean review of a sound report (0 of {metrics.control_runs}) "
            f"— no report could ever clear this lens"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    if metrics.obvious_total and metrics.obvious_sensitivity < thresholds.min_obvious_sensitivity:
        reasons.append(
            f"obvious sensitivity {metrics.obvious_sensitivity:.0%} below "
            f"{thresholds.min_obvious_sensitivity:.0%}"
        )
        return Judgement(Verdict.UNFIT, tuple(reasons))

    if metrics.planted_total and metrics.lens_sensitivity < thresholds.warn_lens_sensitivity:
        reasons.append(
            f"lens sensitivity {metrics.lens_sensitivity:.0%} below "
            f"{thresholds.warn_lens_sensitivity:.0%}"
        )
    if metrics.control_runs and metrics.control_material_rate > thresholds.warn_control_material_rate:
        reasons.append(
            f"invents {metrics.control_material_rate:.2f} material issues per sound report"
        )

    return Judgement(Verdict.MARGINAL if reasons else Verdict.FIT, tuple(reasons))


# -------------------------------------------------------------------- running


@dataclass
class Assignment:
    """One (alias, lens) pair to audition, as the roster actually assigns it."""

    alias: str
    identity: str
    lens: Lens
    #: Index in the lens pool. Positions below the lens's `review.depth` read every
    #: draft (D-front-loaded-depth); the rest are only reachable on the rule 8
    #: confirmation top-up, which is where a false clean grants `strong_met`.
    position: int


def assignments(roster: Roster, identities: dict[str, str]) -> tuple[Assignment, ...]:
    """Every critic slot in the roster, deduplicated by (identity, lens).

    Deduplication is by *resolved* identity for the same reason RA-017 dedupes
    reviewers: two aliases pointing at one model are one model, and auditioning it
    twice would double its weight in the report without adding evidence.
    """
    out: list[Assignment] = []
    seen: set[tuple[str, Lens]] = set()
    for lens in LENS_CATEGORIES:
        for position, alias in enumerate(roster.critics_for(lens)):
            identity = identities[alias]
            if (identity, lens) in seen:
                continue
            seen.add((identity, lens))
            out.append(Assignment(alias=alias, identity=identity, lens=lens, position=position))
    return tuple(out)


#: The author identity handed to the critic prompt during an audition. Fixtures have
#: no real author, and author exclusion is a roster-level property that the harness
#: deliberately does not exercise — it pins the model under test precisely so that
#: every model sees the same input. Using a sentinel keeps `LensResult` well-formed
#: without implying an authorship that does not exist.
AUDITION_AUTHOR = "(audition-fixture)"

#: The source mode every measurement here is taken under, named so it can be part of
#: the cache identity (D-audition-source-mode).
#:
#: Every call the harness makes passes `sources=None`, so the critic sees the
#: unsharpened category-meaning table and no fetched-pages block at all — while the
#: production deployment runs `verify_sources` on (docs/deployment-profile.md) and its
#: evidence critic sees a fetched-pages block on any citation it attempted to fetch,
#: including a failed one (a `BLOCKED` or `NOT FOUND` entry, not silence). That is
#: deliberate: this measures the capability floor a critic brings with no source
#: scaffolding whatsoever, a strictly lower bar than production's failed-fetch case. It
#: is not a claim about the sources-present surface, nor about the weaker but
#: still-present on-its-face prompt production runs on an attempted-and-failed citation,
#: and D-audition-source-mode records what both leave uncertified.
#:
#: The tag is hashed into `prompt_hash`, so a sources-present mode added later gets its
#: own cache line rather than inheriting a verdict measured under this one.
AUDITION_SOURCE_MODE = "sources:none"


def run_assignment(
    client: LLMClient,
    assignment: Assignment,
    fixtures: FixtureSet,
    repetitions: int,
    require_verbatim_spans: bool = True,
) -> Metrics:
    """Audition one model on one lens across the whole corpus. Needs a client."""
    owed = fixtures.for_lens(assignment.lens)
    metrics = Metrics(
        alias=assignment.alias,
        identity=assignment.identity,
        lens=assignment.lens,
        fixtures_owed=len(owed),
    )
    latencies: list[float] = []
    #: Fixtures that produced at least one gradable review. Recorded per fixture rather
    #: than as a count of failures, because "20% of calls failed" cannot distinguish a
    #: model that stumbles once on each of five fixtures from one that is deterministically
    #: broken on a single fixture and therefore never measured on it at all.
    covered: set[str] = set()

    for fixture in owed:
        for _ in range(repetitions):
            started = time.monotonic()
            result = critique_once(
                client,
                assignment.alias,
                assignment.identity,
                assignment.lens,
                fixture.question,
                fixture.artifact,
                hashlib.sha256(fixture.artifact.encode()).hexdigest(),
                AUDITION_AUTHOR,
                # Explicit, because it is a doctrine and not an omission: fixtures ship
                # no source packet, so the measurement is of the source-less surface
                # (AUDITION_SOURCE_MODE, D-audition-source-mode).
                sources=None,
                require_verbatim_spans=require_verbatim_spans,
            )
            latencies.append(time.monotonic() - started)
            metrics.calls += 1

            if result.failed:
                # A failed lens is not a miss and not a false positive — it is a model
                # that cannot emit the schema. Counting it as either would confuse a
                # mechanical problem with a judgement problem, and they have different
                # fixes: one is a prompt/mode issue, the other means replace the model.
                metrics.schema_failures += 1
                continue

            covered.add(fixture.id)

            if fixture.is_control:
                metrics.control_runs += 1
                found = material_issue_count(result)
                metrics.control_material_issues += found
                if found == 0:
                    metrics.control_clean_runs += 1
                continue

            detections = grade(fixture, result)
            metrics.planted_total += len(detections)
            metrics.strict_hits += sum(1 for d in detections if d.strict)
            metrics.same_lens_hits += sum(1 for d in detections if d.same_lens)
            metrics.severity_agreements += sum(1 for d in detections if d.severity_agrees)
            if fixture.tier is Tier.OBVIOUS:
                metrics.obvious_total += len(detections)
                metrics.obvious_hits += sum(1 for d in detections if d.same_lens)

    return metrics.model_copy(
        update={
            "latencies": tuple(latencies),
            "uncovered_fixtures": tuple(f.id for f in owed if f.id not in covered),
        }
    )


def run_audition(
    client: LLMClient,
    roster: Roster,
    identities: dict[str, str],
    fixtures: FixtureSet,
    cfg: AuditionConfig,
    require_verbatim_spans: bool = True,
    only: tuple[Assignment, ...] | None = None,
) -> tuple[Metrics, ...]:
    """Audition every critic slot. Concurrency is bounded the same way runs are."""
    todo = only if only is not None else assignments(roster, identities)

    def work(assignment: Assignment) -> Metrics:
        return run_assignment(
            client, assignment, fixtures, cfg.repetitions, require_verbatim_spans
        )

    with ThreadPoolExecutor(max_workers=cfg.max_concurrency) as pool:
        return tuple(pool.map(work, todo))


# --------------------------------------------------------------------- cache


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: Metrics
    corpus_hash: str
    prompt_hash: str
    #: Identity of the grading rules that turned calls into these `Metrics`.
    rubric_hash: str
    #: The span-validation regime the calls were made under. A loose quote fails the
    #: lens closed when this is on, so it changes what a critic can score.
    require_verbatim_spans: bool
    #: The structured-output mode `LLMClient` was pinned to for every call behind these
    #: `Metrics` — the extraction path a `schema_failures` count is a count *of*
    #: (D-audition-probe-parity). Recorded because a verdict measured under prompt-mode
    #: extraction is not the same claim as one measured under `json_schema`.
    structured_output_mode: str
    repetitions: int
    recorded_at: float

    # `rubric_hash`, `require_verbatim_spans` and `structured_output_mode` are required,
    # with no default, on purpose (D-audition-rubric-identity, D-audition-probe-parity).
    # An entry written before they existed fails `model_validate`, and `load_cache` drops
    # anything that fails — so the older cache degrades to *not audited*, never to a pass
    # carried across a rule change or across a regime the entry cannot name.

    def is_stale(self, now: float, max_age_days: int) -> bool:
        return (now - self.recorded_at) > max_age_days * 86400

    def matches(
        self,
        corpus_hash: str,
        prompt_hash: str,
        repetitions: int,
        *,
        rubric_hash: str,
        require_verbatim_spans: bool,
        structured_output_mode: str | None,
    ) -> bool:
        """A cached verdict is only about the measurement that produced it.

        `prompt_hash` is in the key because editing a lens prompt changes what the
        measurement *means*. Carrying a verdict across a prompt edit would report a
        capability claim for a critic that no longer exists. `rubric_hash` and
        `require_verbatim_spans` are in the key for exactly the same reason: the
        grading rules and the span-validation regime are as much a part of what a
        score means as the corpus and the prompt (D-audition-rubric-identity).

        The newer dimensions are keyword-only because the hashes are all strings and a
        transposed positional argument would silently compare the wrong pair.

        `structured_output_mode` is the one term a caller may decline to compare, and
        it must pass `None` explicitly to decline. Unlike every other dimension here,
        the mode is knowable neither from config nor from the corpus: it is discovered
        by probing the proxy, which costs a call. So the paths that already hold a
        client and are already spending — `ra audition`, `ra audition-refine` — compare
        it and re-measure on a mismatch, while the cache-read-only paths that must never
        spend (`cached_judgements`, and through it the `enforce` startup gate) pass
        `None` and read a verdict whatever mode produced it. D-audition-probe-parity
        argues that asymmetry; the short version is that a free read which dropped a
        mode-mismatched entry would turn a non-deterministic probe into a randomly
        disarmed enforcement gate, and `mode_drift` reports the divergence instead.
        """
        return (
            self.corpus_hash == corpus_hash
            and self.prompt_hash == prompt_hash
            and self.rubric_hash == rubric_hash
            and self.require_verbatim_spans == require_verbatim_spans
            and (
                structured_output_mode is None
                or self.structured_output_mode == structured_output_mode
            )
            and self.repetitions == repetitions
        )


def prompt_hash() -> str:
    """Hash of the prompt surface this harness measures, so an edit invalidates the cache.

    That surface is the source-less one, and only that one. It is not "every prompt
    surface a critic sees": production's evidence critic also sees the sharpened
    `misrepresented_source` meaning and the fetched-pages block, and neither is hashed
    here. Deliberately — nothing in this harness measures a critic under them
    (D-audition-source-mode), so folding them in would discard verdicts that remain
    exactly as true as the day they were recorded, and would advertise a coverage the
    corpus does not have.

    What keeps the narrower hash honest is the mode tag: `AUDITION_SOURCE_MODE` is part
    of the identity, so a sources-present mode added later cannot silently reuse a
    verdict measured without sources.

    The **repair turn** is in, by that same criterion (D-repair-turn-context): the harness
    calls the production `critique_once`, so its repair loop runs here, and whether a
    critic recovers from a quoting slip or fails the lens closed is a measured difference
    in `schema_failures` and in what it is finally graded on. A surface this harness
    exercises belongs in the identity; the sources block, which it never reaches, does not.
    """
    digest = hashlib.sha256()
    digest.update(AUDITION_SOURCE_MODE.encode())
    digest.update(prompts.CRITIC_SYSTEM.encode())
    for lens in LENS_CATEGORIES:
        digest.update(prompts.critic_user(lens, "q", "body", None).encode())
    digest.update(
        # The production shape: `critique` passes no `instruction`, so hashing one with a
        # value would cover a prompt this system never sends. Every surface production
        # does send is exercised — the guidance, its fenced excerpt, the rejected value
        # and the target-naming line.
        prompts.critic_repair_turn(
            user="u",
            error="e",
            guidance="g",
            guidance_excerpt="x",
            rejected="r",
            issue_index=0,
            issue_count=1,
        ).encode()
    )
    return digest.hexdigest()[:16]


def rubric_hash() -> str:
    """Hash of the grading rules a cached verdict was produced under.

    Two halves, mixed into one digest — the same shape `refine_prompt_hash` already
    uses, where a hashed surface is combined with a hand-bumped `PROMPT_VERSION`.

    The rules that are *data* are hashed from the tables themselves, so they can never
    be forgotten: the lens→category map decides what `same_lens` accepts, the severity
    floors and ranks decide what `_is_material` clamps to, and the locus tolerance sets
    the detection window. The `Metrics` field set is hashed too, so a new counter can
    never read as a measured zero on an entry recorded before it existed.

    The rules that are *code* — `grade`, `_is_material`, `_locus_matches`, and
    `run_assignment`'s accounting — carry `RUBRIC_VERSION` instead. Hashing
    `inspect.getsource` over them would be automatic, and was rejected: this file is
    deliberately comment-dense, an audition costs |models| x |fixtures| x repetitions
    calls against a paid proxy, and billing a full re-measurement of the roster for a
    typo fix in a docstring conflicts with the operational goal of invalidating only
    when measurement semantics change. It also breaks under a source-less install. The
    chosen trade is a manually maintained constant for code rules, with automatic
    hashing where the rubric is already represented as data.
    """
    payload = json.dumps(
        {
            "version": RUBRIC_VERSION,
            "locus_paragraph_tolerance": LOCUS_PARAGRAPH_TOLERANCE,
            "lens_categories": {
                lens.value: sorted(c.value for c in categories)
                for lens, categories in LENS_CATEGORIES.items()
            },
            "severity_floor": {c.value: s.value for c, s in SEVERITY_FLOOR.items()},
            "severity_rank": {s.value: rank for s, rank in SEVERITY_RANK.items()},
            "metrics_fields": sorted(Metrics.model_fields),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cache_key(identity: str, lens: Lens) -> str:
    return f"{identity}::{lens.value}"


def load_cache(path: Path) -> dict[str, CacheEntry]:
    """Read the cache, treating any unreadable or malformed file as empty.

    A corrupt cache must degrade to "not audited", never to a passing verdict — the
    whole point is that an unmeasured critic is visibly unmeasured.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, CacheEntry] = {}
    for key, value in (raw or {}).items():
        try:
            out[key] = CacheEntry.model_validate(value)
        except Exception:
            continue
    return out


def save_cache(path: Path, entries: dict[str, CacheEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: v.model_dump(mode="json") for k, v in entries.items()}, indent=2)
    )


# ------------------------------------------------------------------ reporting


class Status(str, Enum):
    """What `ra doctor` shows for a slot. Never blank — a blank reads as a pass."""

    NOT_AUDITED = "not audited"
    STALE = "stale"


def cached_judgements(
    cfg: AuditionConfig,
    roster: Roster,
    identities: dict[str, str],
    require_verbatim_spans: bool,
    now: float | None = None,
) -> dict[tuple[str, Lens], Judgement]:
    """Verdicts for the roster's current critic slots, read from the cache only.

    Never spends a call. Both callers — `ra doctor` and the `enforce` startup gate —
    sit on paths where quietly auditioning a roster's worth of models would be a
    surprise bill. A slot whose entry is missing, stale, or graded against a different
    corpus, prompt surface, grading rubric, span-validation regime or repetition count
    is simply absent from the result: an unmeasured critic must read as unmeasured,
    never as a pass.

    `require_verbatim_spans` is a required argument rather than a defaulted one because
    it lives on `Config`, not `AuditionConfig`, and both callers hold a `Config`. A
    default would let a caller silently compare against the wrong regime — the exact
    class of bug D-audition-rubric-identity exists to close.

    The structured-output mode is deliberately *not* compared here (`None` below,
    D-audition-probe-parity): learning it costs a probe call, which is precisely what
    this function promises never to spend. `mode_drift` reports the divergence for
    callers that have probed anyway.
    """
    try:
        corpus_hash = load_fixtures().corpus_hash
    except FixtureError:
        return {}
    cache = load_cache(cfg.cache_path)
    ph = prompt_hash()
    rh = rubric_hash()
    at = time.time() if now is None else now

    out: dict[tuple[str, Lens], Judgement] = {}
    for slot in assignments(roster, identities):
        entry = cache.get(cache_key(slot.identity, slot.lens))
        if entry is None or not entry.matches(
            corpus_hash,
            ph,
            cfg.repetitions,
            rubric_hash=rh,
            require_verbatim_spans=require_verbatim_spans,
            structured_output_mode=None,
        ):
            continue
        if entry.is_stale(at, cfg.max_age_days):
            continue
        out[(slot.identity, slot.lens)] = judge(entry.metrics, cfg.thresholds)
    return out


def mode_drift(
    cfg: AuditionConfig,
    roster: Roster,
    identities: dict[str, str],
    probed_modes: dict[str, str],
) -> list[str]:
    """Slots whose cached verdict was measured under a different structured-output mode
    than the alias pins to now (D-audition-probe-parity).

    `cached_judgements` cannot compare the mode without spending a probe, so it reads
    across a divergence rather than silently discarding the entry. This is where the
    divergence is said out loud, for a caller that has probed for its own reasons —
    `ra doctor` fills a whole column with the probe results already, so the report is
    free there.

    Takes the modes as data, not a client, for the same reason the gate does: a
    reporting helper on the doctor path must not be able to bill an audition.
    """
    cache = load_cache(cfg.cache_path)
    out: list[str] = []
    for slot in assignments(roster, identities):
        probed = probed_modes.get(slot.alias)
        entry = cache.get(cache_key(slot.identity, slot.lens))
        if probed is None or entry is None or entry.structured_output_mode == probed:
            continue
        out.append(
            f"'{slot.alias}' on {slot.lens.value} was auditioned under structured-output "
            f"mode '{entry.structured_output_mode}' but now probes to '{probed}' — the "
            f"cached verdict measures a different extraction path than a run would use. "
            f"Re-measure with `ra audition --alias {slot.alias}`"
        )
    return out


def enforce_fitness(
    cfg: AuditionConfig,
    roster: Roster,
    identities: dict[str, str],
    require_verbatim_spans: bool,
    now: float | None = None,
) -> None:
    """Under `audition.enforce`, refuse to start with an `unfit` critic assigned.

    Off by default (D-critic-audition: warn by default, enforce opt-in). The asymmetry inside the
    gate is deliberate. `unfit` is a positive measurement that the model cannot perform
    the lens, so a run staffed by one is not reviewing that lens whatever its counters
    say — that is exactly the case fail-closed exists for. `marginal`, `stale` and
    `not audited` stay warnings even with enforcement on: they are absences of evidence,
    and blocking on them couples every run to the freshness of a cache only a paid,
    rate-limited proxy can refill.
    """
    if not cfg.enforce:
        return
    judgements = cached_judgements(cfg, roster, identities, require_verbatim_spans, now=now)
    unfit = sorted(
        f"'{slot.alias}' ({slot.identity}) on {slot.lens.value}"
        for slot in assignments(roster, identities)
        if (j := judgements.get((slot.identity, slot.lens))) is not None
        and j.verdict is Verdict.UNFIT
    )
    if unfit:
        raise ConfigError(
            "fail closed: audition.enforce is on and these assigned critics graded "
            f"unfit: {'; '.join(unfit)} — re-roster them, or re-measure with "
            "`ra audition --force` if the verdict is out of date"
        )


def roster_warnings(
    roster: Roster,
    identities: dict[str, str],
    judgements: dict[tuple[str, Lens], Judgement],
    review: ReviewConfig | None = None,
) -> list[str]:
    """Roster-level consequences of the per-model verdicts.

    Two checks that no single-model verdict can express, plus the position check below,
    whose threshold is the deployment's own review depth.
    """
    warnings: list[str] = []
    slots = assignments(roster, identities)
    review = review or ReviewConfig()

    for slot in slots:
        judgement = judgements.get((slot.identity, slot.lens))
        if judgement is None or judgement.verdict in (Verdict.FIT, Verdict.INSUFFICIENT):
            continue
        # Position-aware: a weak critic is dangerous in a different way depending on
        # where in the pool it sits, and review depth is what decides which. A pass
        # draws the first `depth` fresh eligible models (`roles.critic_slate`), so every
        # slot inside that window reads every draft and every slot outside it is only
        # reached on the rule 8 confirmation top-up.
        depth = review.depth_for(slot.lens)
        if slot.position >= depth:
            warnings.append(
                f"'{slot.alias}' is {judgement.verdict.value} on {slot.lens.value} and sits "
                f"at position {slot.position + 1} in that pool. It is unreachable on the "
                f"first pass and will be reached on the rule 8 confirmation top-up, where "
                f"a false clean raises cleared_count to 2, satisfies strong_met, and "
                f"terminates the run 'accepted'"
            )
        elif slot.position >= 1:
            # Front-loaded by D-front-loaded-depth: this slot used to be a top-up and is
            # now part of ordinary discovery, so its verdict is worth re-measuring
            # against the production-shaped corpus before the roster ships.
            warnings.append(
                f"'{slot.alias}' is {judgement.verdict.value} on {slot.lens.value} and sits "
                f"at position {slot.position + 1} in that pool, inside a review depth of "
                f"{depth}. It reads EVERY draft, so a false clean satisfies strong_met on "
                f"the first pass — re-audition it before trusting this lineup"
            )

    for lens in LENS_CATEGORIES:
        pool = [s for s in slots if s.lens is lens]
        graded = [judgements.get((s.identity, lens)) for s in pool]
        known = [j for j in graded if j is not None and j.verdict is not Verdict.INSUFFICIENT]
        if known and all(j.verdict in (Verdict.MARGINAL, Verdict.UNFIT) for j in known):
            warnings.append(
                f"every audited model on lens '{lens.value}' is marginal or unfit — that "
                f"lens is effectively unstaffed and the run's soundness claim does not "
                f"hold for it"
            )

    return warnings
