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

Two design commitments worth stating plainly.

**The grader is a pure function and never an LLM.** An LLM grader is precisely the
component whose reliability is in question here; using one would make the harness's
trustworthiness depend on the property the harness exists to measure. Grading is
category matching plus a structural-locus window, and nothing else.

**Both directions gate.** A critic that flags everything scores perfect sensitivity
and is worse than useless: it never lets a run converge, it drains the critique
budget, it drives `stagnation_count` to the limit, and rule 13 terminates
`exhausted_unresolved` on a report that was fine. Silence and noise are two ways to
fail the same job.
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
from pydantic import BaseModel, ConfigDict, Field

from . import prompts
from .config import AuditionConfig, AuditionThresholds, Roster
from .critique import critique_once
from .llm import LLMClient
from .schemas import LensResult, RawIssue, StructuralRef
from .taxonomy import LENS_CATEGORIES, SEVERITY_FLOOR, SEVERITY_RANK, Category, Lens, Severity

#: Fixture corpus shipped with the source tree.
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "audition"

#: A detection may sit this many paragraphs away from the planted locus and still
#: count. Paragraph indexing is genuinely ambiguous at section boundaries and across
#: list blocks, and a critic that names the neighbouring paragraph has still found it.
LOCUS_PARAGRAPH_TOLERANCE = 1

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
    #: Some defects have no honest location. An `omitted_counterargument` is defined by
    #: absence: a critic may reasonably anchor it to the thesis that overreaches, to the
    #: section where the rebuttal belonged, or to the conclusion that ignores it. Grading
    #: those as misses would measure agreement with the fixture author's filing choice
    #: rather than the critic's ability to notice the omission. `locus` stays required
    #: as documentation of where the fixture author considers it to live.
    anywhere: bool = False
    #: Human-facing only. Never used for matching — matching on prose would either
    #: need an LLM or degenerate into brittle substring checks.
    note: str = ""


class Fixture(BaseModel):
    """A fixture artifact plus its ground truth."""

    model_config = ConfigDict(extra="forbid")

    id: str
    lens: Lens
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
        if "slots" in manifest and manifest["slots"]:
            manifest["slots"] = {k: tuple(v) for k, v in manifest["slots"].items()}

        try:
            fixture = Fixture.model_validate(manifest)
        except Exception as exc:  # pydantic ValidationError, plus yaml shape errors
            raise FixtureError(f"fixture '{fixture_dir.name}': {exc}") from exc

        _check_lens_ownership(fixture)
        fixtures.append(fixture)

    if not fixtures:
        raise FixtureError(f"fixture corpus at {directory} is empty")

    corpus_hash = digest.hexdigest()[:16]
    resolved = tuple(
        f.model_copy(update={"artifact": _resolve_slots(f.artifact, f.slots, corpus_hash + f.id)})
        for f in fixtures
    )
    return FixtureSet(fixtures=resolved, corpus_hash=corpus_hash)


def _check_lens_ownership(fixture: Fixture) -> None:
    """A planted category must belong to the lens declared responsible for it.

    Without this a fixture could plant an `uncited_claim` and declare itself a `logic`
    fixture, then grade every logic critic as blind — `triage.validate_issue` rejects
    an out-of-scope category, so no correct critic could ever score.
    """
    owned = LENS_CATEGORIES[fixture.lens]
    for defect in fixture.defects:
        if defect.category not in owned:
            raise FixtureError(
                f"fixture '{fixture.id}': category '{defect.category.value}' is not in "
                f"scope for lens '{fixture.lens.value}' — no valid critic could report it"
            )


# ------------------------------------------------------------------- grading


@dataclass(frozen=True)
class Detection:
    """Whether one planted defect was found, and how precisely."""

    defect: PlantedDefect
    strict: bool
    same_lens: bool
    severity_agrees: bool


def _locus_matches(planted: StructuralRef, reported: StructuralRef) -> bool:
    return (
        planted.section == reported.section
        and abs(planted.paragraph - reported.paragraph) <= LOCUS_PARAGRAPH_TOLERANCE
    )


def _is_material(issue: RawIssue) -> bool:
    """Severity after the mechanical floor clamp, which is what triage would count."""
    effective = max(issue.severity, SEVERITY_FLOOR[issue.category], key=_rank)
    return effective in (Severity.BLOCKING, Severity.MAJOR)


def _rank(severity: Severity) -> int:
    return SEVERITY_RANK[severity]


def grade(fixture: Fixture, result: LensResult) -> tuple[Detection, ...]:
    """Match a critic's issues against ground truth. Pure — no client, no I/O.

    A planted defect counts as found when a reported issue lands within the locus
    window and its category either matches exactly (`strict`) or belongs to the same
    lens (`same_lens`). The relaxed form exists because critics reasonably disagree
    between, say, `uncited_claim` and `misrepresented_source` on the same sentence,
    and scoring that as a miss would penalize a critic that is doing its job. Both
    numbers are reported; neither is the whole story alone.
    """
    detections: list[Detection] = []
    for defect in fixture.defects:
        candidates = [
            i
            for i in result.issues
            if defect.anywhere or _locus_matches(defect.locus, i.locus)
        ]
        strict = any(i.category == defect.category and _is_material(i) for i in candidates)
        same_lens = any(
            i.category in LENS_CATEGORIES[fixture.lens] and _is_material(i) for i in candidates
        )
        severity_agrees = any(
            i.category == defect.category and i.severity == SEVERITY_FLOOR[defect.category]
            for i in candidates
        )
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
    #: Index in the lens pool. Position >= 2 is only reachable on the rule 8
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


def run_assignment(
    client: LLMClient,
    assignment: Assignment,
    fixtures: FixtureSet,
    repetitions: int,
    require_verbatim_spans: bool = True,
) -> Metrics:
    """Audition one model on one lens across the whole corpus. Needs a client."""
    metrics = Metrics(
        alias=assignment.alias, identity=assignment.identity, lens=assignment.lens
    )
    latencies: list[float] = []

    for fixture in fixtures.for_lens(assignment.lens):
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

    return metrics.model_copy(update={"latencies": tuple(latencies)})


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
    repetitions: int
    recorded_at: float

    def is_stale(self, now: float, max_age_days: int) -> bool:
        return (now - self.recorded_at) > max_age_days * 86400

    def matches(self, corpus_hash: str, prompt_hash: str, repetitions: int) -> bool:
        """A cached verdict is only about the corpus and prompts that produced it.

        `prompt_hash` is in the key because editing a lens prompt changes what the
        measurement *means*. Carrying a verdict across a prompt edit would report a
        capability claim for a critic that no longer exists.
        """
        return (
            self.corpus_hash == corpus_hash
            and self.prompt_hash == prompt_hash
            and self.repetitions == repetitions
        )


def prompt_hash() -> str:
    """Hash of every prompt surface a critic sees, so an edit invalidates the cache."""
    digest = hashlib.sha256()
    digest.update(prompts.CRITIC_SYSTEM.encode())
    for lens in LENS_CATEGORIES:
        digest.update(prompts.critic_user(lens, "q", "body", None).encode())
    return digest.hexdigest()[:16]


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


def roster_warnings(
    roster: Roster,
    identities: dict[str, str],
    judgements: dict[tuple[str, Lens], Judgement],
) -> list[str]:
    """Roster-level consequences of the per-model verdicts.

    Two checks that no single-model verdict can express.
    """
    warnings: list[str] = []
    slots = assignments(roster, identities)

    for slot in slots:
        judgement = judgements.get((slot.identity, slot.lens))
        if judgement is None or judgement.verdict in (Verdict.FIT, Verdict.INSUFFICIENT):
            continue
        # Position-aware: a weak critic is far more dangerous late in the pool than
        # early. `pick_critic` prefers an identity that has not yet reviewed this
        # artifact, so index >= 2 is unreachable on the first pass and is reached on
        # the rule 8 confirmation top-up — where clearing the lens is the whole point.
        if slot.position >= 2:
            warnings.append(
                f"'{slot.alias}' is {judgement.verdict.value} on {slot.lens.value} and sits "
                f"at position {slot.position + 1} in that pool. It is unreachable on the "
                f"first pass and will be reached on the rule 8 confirmation top-up, where "
                f"a false clean raises cleared_count to 2, satisfies strong_met, and "
                f"terminates the run 'accepted'"
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
