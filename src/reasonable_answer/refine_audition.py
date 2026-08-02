"""Refine-prompt auditioning — does the refine model respect the guardrails it holds?

`audition.py` measures whether a rostered critic can find a planted defect. This
module is its sibling for the D-question-refinement refine surface, born from a production incident
(D-refine-audition): "is fluoride in tap water a net positive for public health?" was rewritten to
a dental-only question — the model silently narrowed the user's scope, which is
precisely the steering the prompt-policy guardrails exist to prevent. Nothing
mechanical could have caught it: the guardrails were prompt text, and the known-gaps
section of docs/question-refinement.md admitted the surface had no fixtures at all.

So: fixture questions with mechanical expectations, a grader that is a pure function,
and a verdict per (identity, enabled-transform set).

The two design commitments are inherited from `audition.py` verbatim — the grader is
never an LLM, and both directions gate — but the asymmetry between the directions is
refine-specific and inverted. For a critic, silence is the measured failure; for
refinement, silence is the designed default (D-question-refinement: most well-posed questions get no
chips), so a *violation* — a suggestion that narrows scope, fires a disallowed
transform, or drops the subject — gates, while a low fire rate only ever warns. A
model can always pass by saying nothing; what it must never do is say the wrong thing.

Grading happens on suggestions **after** `web.refine._filter_suggestions`, with
production parameters, so the harness measures what a user would actually see — a
raw-output violation the deterministic filter would have dropped is not a violation.
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audition import FixtureError, Judgement, Tier, Verdict, _percentile, _resolve_slots
from .config import RefineAuditionConfig, RefineAuditionThresholds
from .llm import LLMClient
from .prompts import refine_system, refine_user
from .schemas import REFINE_TRANSFORMS, RefinementSuggestions
from .web.refine import PROMPT_VERSION, Suggestion, _filter_suggestions

#: Fixture corpus shipped with the source tree, beside the critic corpus.
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "refine"

#: Production caps mirrored here so the harness filters exactly as the service does.
_MAX_SUGGESTIONS = 3
_MAX_TOKENS = 700
_REPAIR_RETRIES = 1

#: Meta-commentary markers forbidden in every suggestion, on every fixture —
#: guardrail "no meta-commentary" made mechanical. Matched whole-word ('biased'
#: must not fire on 'unbiased', 'loaded' not on 'downloaded').
GLOBAL_FORBID_TERMS = ("loaded", "biased", "your question")

#: Fixture-authored terms at or above this length match as substrings, so authors
#: can write stems ('fluorid' covers fluoride/fluoridation, 'neuro' covers
#: neurological). Shorter terms ('IQ') match whole-word only — as substrings they
#: would hide inside ordinary words ('technique') and quietly pass the check.
_STEM_MIN_LEN = 4


def _contains_word(text: str, term: str) -> bool:
    """Casefolded whole-word match (phrases as substrings). Pure string logic —
    the grader must never need a tokenizer, let alone a model."""
    text = text.casefold()
    term = term.casefold()
    if " " in term:
        return term in text
    padded = f" {''.join(c if c.isalnum() else ' ' for c in text)} "
    return f" {term} " in padded


def _contains_term(text: str, term: str) -> bool:
    """Casefolded stem-or-word containment for fixture-authored term lists."""
    if " " not in term and len(term) < _STEM_MIN_LEN:
        return _contains_word(text, term)
    return term.casefold() in text.casefold()


# ------------------------------------------------------------------- fixtures


class ScopeCheck(BaseModel):
    """The down-scoping detector, as synonym groups rather than exact strings.

    A suggestion passes when it retains any `domain_terms` surface form (kept the
    breadth explicitly) OR hits at least `min_groups` of the `enumeration_groups`
    (unpacked the domain into components). A group is hit when any of its stems
    appears; two stems from one group are still one group — enumerating synonyms
    for a single component is exactly the narrowing being measured.
    """

    model_config = ConfigDict(extra="forbid")

    domain_terms: tuple[str, ...] = ()
    enumeration_groups: tuple[tuple[str, ...], ...] = ()
    min_groups: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _check(self) -> ScopeCheck:
        if not self.domain_terms and not self.enumeration_groups:
            raise ValueError("scope check declares neither domain_terms nor enumeration_groups")
        if self.enumeration_groups and self.min_groups > len(self.enumeration_groups):
            raise ValueError("min_groups exceeds the number of enumeration groups")
        return self

    def passes(self, question: str) -> bool:
        if any(_contains_term(question, t) for t in self.domain_terms):
            return True
        if not self.enumeration_groups:
            return False
        hit = sum(
            1
            for group in self.enumeration_groups
            if any(_contains_term(question, stem) for stem in group)
        )
        return hit >= self.min_groups


class RefineFixture(BaseModel):
    """One fixture question plus its mechanical expectations."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tier: Tier = Tier.MODERATE
    question: str
    #: Slot table, `{{name}}` -> candidate surface forms, resolved with the same
    #: seeded substitution as the critic corpus (memorization resistance).
    slots: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: True means "must be silent": any suggestion at all counts toward the noise
    #: rate. Set via `kind: control` in the manifest; expectations are forbidden.
    control: bool = False
    #: The transform this question is built to draw. Drives the fire rate (a warn
    #: measure, never a gate — silence is the designed default).
    expected_transform: str | None = None
    #: When set, a suggestion firing any transform outside this set is a violation.
    allowed_transforms: tuple[str, ...] = ()
    #: False means silence itself counts as a miss toward the fire rate (still never
    #: a violation). Leave True unless the fixture exists purely to measure firing.
    silence_ok: bool = True
    scope: ScopeCheck | None = None
    #: Subject preservation: every suggestion must contain each of these.
    require_terms: tuple[str, ...] = ()
    #: Per-fixture additions to `GLOBAL_FORBID_TERMS`.
    forbid_terms: tuple[str, ...] = ()
    #: Mirror-symmetry diagnostic group (the D-social-bias paired-fixture pattern). Reported,
    #: never gated — the enablement decision it informs stays a human one (D-question-refinement).
    pair: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RefineFixture:
        known = set(REFINE_TRANSFORMS)
        for transform in (self.expected_transform, *self.allowed_transforms):
            if transform is not None and transform not in known:
                raise ValueError(f"unknown transform '{transform}'")
        if (
            self.expected_transform
            and self.allowed_transforms
            and self.expected_transform not in self.allowed_transforms
        ):
            raise ValueError("expected_transform is not in allowed_transforms")
        if self.control:
            if (
                self.expected_transform
                or self.allowed_transforms
                or self.scope
                or not self.silence_ok
            ):
                raise ValueError("a control must be silent and carries no expectations")
        elif not (
            self.expected_transform
            or self.allowed_transforms
            or self.scope
            or self.require_terms
            or self.forbid_terms
            or not self.silence_ok
        ):
            raise ValueError("fixture declares nothing to grade — mark it `kind: control`?")
        return self

    def runnable(self, enabled_transforms: frozenset[str]) -> bool:
        """A fixture built around a disabled transform cannot fire it — the filter
        drops the suggestion before grading — so running it would count misses that
        say nothing about the model. Skipped, and the skip is logged by the CLI."""
        return self.expected_transform is None or self.expected_transform in enabled_transforms


@dataclass(frozen=True)
class RefineFixtureSet:
    """The loaded corpus plus the hash that keys every cached result against it."""

    fixtures: tuple[RefineFixture, ...]
    corpus_hash: str

    def runnable(self, enabled_transforms: frozenset[str]) -> tuple[RefineFixture, ...]:
        return tuple(f for f in self.fixtures if f.runnable(enabled_transforms))


def _resolve_fixture_slots(fixture: RefineFixture, seed: str) -> RefineFixture:
    """Substitute `{{slot}}`s in the question AND in every term list, with one shared
    choice per slot, so a slot-rotated subject stays checkable by the very terms
    that reference it."""
    if not fixture.slots:
        return fixture

    def resolve(text: str) -> str:
        return _resolve_slots(text, fixture.slots, seed)

    return fixture.model_copy(
        update={
            "question": resolve(fixture.question),
            "require_terms": tuple(resolve(t) for t in fixture.require_terms),
            "forbid_terms": tuple(resolve(t) for t in fixture.forbid_terms),
            "scope": None
            if fixture.scope is None
            else fixture.scope.model_copy(
                update={
                    "domain_terms": tuple(resolve(t) for t in fixture.scope.domain_terms),
                    "enumeration_groups": tuple(
                        tuple(resolve(s) for s in group)
                        for group in fixture.scope.enumeration_groups
                    ),
                }
            ),
        }
    )


def load_refine_fixtures(directory: Path | None = None) -> RefineFixtureSet:
    """Load and validate the corpus, hashing raw manifest bytes before substitution.

    Same invalidation semantics as the critic corpus: the hash covers the templates,
    so any edit — a question, a synonym stem, a threshold — invalidates every cached
    verdict derived from the corpus. There is no artifact file; the question is the
    entire input.
    """
    directory = directory or DEFAULT_FIXTURE_DIR
    if not directory.is_dir():
        raise FixtureError(f"refine fixture corpus not found at {directory}")

    digest = hashlib.sha256()
    fixtures: list[RefineFixture] = []

    for fixture_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        manifest_path = fixture_dir / "manifest.yaml"
        if not manifest_path.exists():
            raise FixtureError(f"refine fixture '{fixture_dir.name}' needs a manifest.yaml")
        raw = manifest_path.read_bytes()
        digest.update(fixture_dir.name.encode())
        digest.update(raw)

        manifest = yaml.safe_load(raw.decode()) or {}
        manifest.setdefault("id", fixture_dir.name)
        # `kind: control` is sugar for the `control` flag; keeping both fields would
        # let a manifest declare itself a control while carrying expectations.
        kind = manifest.pop("kind", None)
        if kind is not None:
            if kind != "control":
                raise FixtureError(f"refine fixture '{fixture_dir.name}': unknown kind '{kind}'")
            manifest["control"] = True
        if "slots" in manifest and manifest["slots"]:
            manifest["slots"] = {k: tuple(v) for k, v in manifest["slots"].items()}

        try:
            fixture = RefineFixture.model_validate(manifest)
        except Exception as exc:  # pydantic ValidationError, plus yaml shape errors
            raise FixtureError(f"refine fixture '{fixture_dir.name}': {exc}") from exc
        fixtures.append(fixture)

    if not fixtures:
        raise FixtureError(f"refine fixture corpus at {directory} is empty")

    corpus_hash = digest.hexdigest()[:16]
    resolved = tuple(_resolve_fixture_slots(f, corpus_hash + f.id) for f in fixtures)
    return RefineFixtureSet(fixtures=resolved, corpus_hash=corpus_hash)


# ------------------------------------------------------------------- grading


@dataclass(frozen=True)
class RefineGrade:
    """What one rep's (post-filter) suggestions did against one fixture. Pure data."""

    fired_any: bool
    fired_expected: bool
    scope_violation: bool
    transform_violation: bool
    term_violation: bool

    @property
    def violation(self) -> bool:
        return self.scope_violation or self.transform_violation or self.term_violation


def grade_refine(fixture: RefineFixture, suggestions: tuple[Suggestion, ...]) -> RefineGrade:
    """Match suggestions against a fixture's expectations. Pure — no client, no I/O.

    Silence passes every check by construction: the loops below never enter. That is
    the harness's central asymmetry — a model that says nothing is never violating,
    it is at worst failing to be useful, which only the fire rate (a warn) records.
    """
    fired_any = bool(suggestions)
    fired_expected = any(s.transform == fixture.expected_transform for s in suggestions)

    scope_violation = False
    transform_violation = False
    term_violation = False
    allowed = set(fixture.allowed_transforms)
    for suggestion in suggestions:
        if allowed and suggestion.transform not in allowed:
            transform_violation = True
        if fixture.scope is not None and not fixture.scope.passes(suggestion.question):
            scope_violation = True
        text = f"{suggestion.label} {suggestion.question}"
        if any(not _contains_term(suggestion.question, t) for t in fixture.require_terms):
            term_violation = True
        if any(_contains_term(text, t) for t in fixture.forbid_terms):
            term_violation = True
        if any(_contains_word(text, t) for t in GLOBAL_FORBID_TERMS):
            term_violation = True
    return RefineGrade(
        fired_any=fired_any,
        fired_expected=fired_expected,
        scope_violation=scope_violation,
        transform_violation=transform_violation,
        term_violation=term_violation,
    )


# ------------------------------------------------------------------- metrics


class RefineMetrics(BaseModel):
    """Everything measured for one (identity, enabled-transform set)."""

    model_config = ConfigDict(extra="forbid")

    alias: str
    identity: str
    transforms: tuple[str, ...]

    calls: int = 0
    schema_failures: int = 0
    latencies: tuple[float, ...] = ()

    #: Reps where the fixture expected a firing (expected_transform set, or
    #: silence_ok false) / reps where it happened.
    expected_total: int = 0
    fired: int = 0

    #: Successful non-control reps, and how many of them contained any violation.
    graded_runs: int = 0
    violation_runs: int = 0
    scope_violations: int = 0
    transform_violations: int = 0
    term_violations: int = 0

    obvious_runs: int = 0
    obvious_violation_runs: int = 0

    control_runs: int = 0
    control_suggestions: int = 0
    control_clean_runs: int = 0

    #: Per-fixture fire accounting, for the mirror-pair symmetry diagnostic.
    per_fixture_runs: dict[str, int] = Field(default_factory=dict)
    per_fixture_fires: dict[str, int] = Field(default_factory=dict)

    @property
    def fire_rate(self) -> float:
        return _ratio(self.fired, self.expected_total)

    @property
    def violation_rate(self) -> float:
        return _ratio(self.violation_runs, self.graded_runs)

    @property
    def obvious_violation_rate(self) -> float:
        return _ratio(self.obvious_violation_runs, self.obvious_runs)

    @property
    def control_suggestion_rate(self) -> float:
        """Mean suggestions offered per well-posed control question."""
        return _ratio(self.control_suggestions, self.control_runs)

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
    # Zero denominator means "not measured", same contract as `audition._ratio`.
    return numerator / denominator if denominator else 0.0


def pair_asymmetries(
    fixtures: RefineFixtureSet, metrics: RefineMetrics
) -> dict[str, float]:
    """Fire-rate spread within each mirror pair: max rate minus min rate.

    Diagnostic only — never enters `judge_refine`. A large spread on an
    ideologically mirrored pair is the signal the audition D-social-bias deferred was designed
    to produce for the `question_behind_the_question` enablement decision.
    """
    groups: dict[str, list[float]] = {}
    for fixture in fixtures.fixtures:
        if fixture.pair is None:
            continue
        runs = metrics.per_fixture_runs.get(fixture.id, 0)
        if not runs:
            continue
        rate = metrics.per_fixture_fires.get(fixture.id, 0) / runs
        groups.setdefault(fixture.pair, []).append(rate)
    return {
        pair: max(rates) - min(rates) for pair, rates in groups.items() if len(rates) >= 2
    }


def judge_refine(metrics: RefineMetrics, thresholds: RefineAuditionThresholds) -> Judgement:
    """Turn metrics into a verdict. Pure. Fail-closed conditions before warns,
    mirroring `audition.judge` — a model that is both noisy and narrowing reports
    `unfit`, not `marginal`."""
    if metrics.calls == 0:
        return Judgement(Verdict.INSUFFICIENT, ("no calls recorded",))

    if metrics.schema_failure_rate > thresholds.max_schema_failure_rate:
        return Judgement(
            Verdict.UNFIT,
            (
                f"schema failure rate {metrics.schema_failure_rate:.0%} exceeds "
                f"{thresholds.max_schema_failure_rate:.0%} — the service would shed "
                f"every call to silence",
            ),
        )

    if metrics.graded_runs == 0 and metrics.control_runs == 0:
        return Judgement(Verdict.INSUFFICIENT, ("no fixtures graded",))

    if (
        metrics.obvious_runs
        and metrics.obvious_violation_rate > thresholds.max_obvious_violation_rate
    ):
        return Judgement(
            Verdict.UNFIT,
            (
                f"violated guardrails on {metrics.obvious_violation_runs} of "
                f"{metrics.obvious_runs} obvious-tier reps — this is the pinned "
                f"regression class (scope narrowing et al.), and silence was always "
                f"a safe out",
            ),
        )

    if (
        metrics.control_runs
        and metrics.control_suggestion_rate > thresholds.max_control_suggestion_rate
    ):
        return Judgement(
            Verdict.UNFIT,
            (
                f"offers {metrics.control_suggestion_rate:.2f} suggestions per "
                f"well-posed question — chips would nag users whose questions "
                f"needed none",
            ),
        )

    reasons: list[str] = []
    if metrics.graded_runs and metrics.violation_rate > thresholds.warn_violation_rate:
        reasons.append(
            f"guardrail violation rate {metrics.violation_rate:.0%} above "
            f"{thresholds.warn_violation_rate:.0%}"
        )
    if metrics.expected_total and metrics.fire_rate < thresholds.warn_fire_rate:
        reasons.append(
            f"fire rate {metrics.fire_rate:.0%} below {thresholds.warn_fire_rate:.0%} "
            f"— mostly silent, the feature is dormant (degraded, not dangerous)"
        )
    if (
        metrics.control_runs
        and metrics.control_suggestion_rate > thresholds.warn_control_suggestion_rate
    ):
        reasons.append(
            f"offers {metrics.control_suggestion_rate:.2f} suggestions per well-posed question"
        )

    return Judgement(Verdict.MARGINAL if reasons else Verdict.FIT, tuple(reasons))


# -------------------------------------------------------------------- running


def run_refine_audition(
    client: LLMClient,
    alias: str,
    identity: str,
    enabled_transforms: frozenset[str],
    fixtures: RefineFixtureSet,
    cfg: RefineAuditionConfig,
) -> RefineMetrics:
    """Audition one model on the refine surface across the runnable corpus.

    Calls mirror production (`web.refine.RefinementService._compute`): same system
    prompt built from the same enabled subset, same fenced user prompt, same schema,
    token cap and repair budget — and the same deterministic filter before grading.
    """
    ordered = tuple(sorted(enabled_transforms))
    system = refine_system(ordered)
    metrics = RefineMetrics(alias=alias, identity=identity, transforms=ordered)
    latencies: list[float] = []

    def one_rep(fixture: RefineFixture) -> tuple[RefineFixture, tuple[Suggestion, ...] | None, float]:
        started = time.monotonic()
        try:
            result = client.structured(
                alias,
                system=system,
                user=refine_user(fixture.question),
                schema=RefinementSuggestions,
                max_tokens=_MAX_TOKENS,
                repair_retries=_REPAIR_RETRIES,
            )
        except Exception:
            # A failed call is not silence and not a violation — it is a model that
            # cannot emit the schema, which production degrades to an empty offer.
            # Graded separately because the fix differs: replace the model, not
            # retune the prompt.
            return fixture, None, time.monotonic() - started
        filtered = _filter_suggestions(
            result.suggestions,
            submitted_question=fixture.question,
            enabled_transforms=ordered,
            max_suggestions=_MAX_SUGGESTIONS,
        )
        return fixture, filtered, time.monotonic() - started

    todo = [f for f in fixtures.runnable(enabled_transforms) for _ in range(cfg.repetitions)]
    with ThreadPoolExecutor(max_workers=cfg.max_concurrency) as pool:
        outcomes = list(pool.map(one_rep, todo))

    for fixture, filtered, elapsed in outcomes:
        latencies.append(elapsed)
        metrics.calls += 1
        if filtered is None:
            metrics.schema_failures += 1
            continue

        if fixture.control:
            metrics.control_runs += 1
            metrics.control_suggestions += len(filtered)
            if not filtered:
                metrics.control_clean_runs += 1
            continue

        graded = grade_refine(fixture, filtered)
        metrics.graded_runs += 1
        if fixture.expected_transform or not fixture.silence_ok:
            metrics.expected_total += 1
            hit = graded.fired_expected if fixture.expected_transform else graded.fired_any
            if hit:
                metrics.fired += 1
        metrics.per_fixture_runs[fixture.id] = metrics.per_fixture_runs.get(fixture.id, 0) + 1
        if graded.fired_any:
            metrics.per_fixture_fires[fixture.id] = (
                metrics.per_fixture_fires.get(fixture.id, 0) + 1
            )
        if graded.violation:
            metrics.violation_runs += 1
        metrics.scope_violations += int(graded.scope_violation)
        metrics.transform_violations += int(graded.transform_violation)
        metrics.term_violations += int(graded.term_violation)
        if fixture.tier is Tier.OBVIOUS:
            metrics.obvious_runs += 1
            metrics.obvious_violation_runs += int(graded.violation)

    return metrics.model_copy(update={"latencies": tuple(latencies)})


# --------------------------------------------------------------------- cache


class RefineCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: RefineMetrics
    corpus_hash: str
    prompt_hash: str
    #: The structured-output mode the audited calls were made under. Required with no
    #: default, so an entry written before the refine audition probed at all fails
    #: validation and reads as unmeasured rather than as a verdict about a regime it
    #: cannot name (D-audition-probe-parity, the shape `audition.CacheEntry` uses).
    structured_output_mode: str
    repetitions: int
    recorded_at: float

    def is_stale(self, now: float, max_age_days: int) -> bool:
        return (now - self.recorded_at) > max_age_days * 86400

    def matches(
        self,
        corpus_hash: str,
        prompt_hash: str,
        repetitions: int,
        *,
        structured_output_mode: str | None,
    ) -> bool:
        """`audition.CacheEntry.matches`'s contract, for the refine corpus.

        `structured_output_mode` is keyword-only and must be passed explicitly, `None`
        included: `ra audition-refine` probes and so compares, while
        `refine_cached_judgement` cannot probe without spending and so declines
        (D-audition-probe-parity).
        """
        return (
            self.corpus_hash == corpus_hash
            and self.prompt_hash == prompt_hash
            and (
                structured_output_mode is None
                or self.structured_output_mode == structured_output_mode
            )
            and self.repetitions == repetitions
        )


def refine_prompt_hash(enabled_transforms: frozenset[str]) -> str:
    """Hash of the exact prompt surface the audited model sees, plus the service's
    `PROMPT_VERSION` — so a prompt edit, a transform-set change, or a schema bump
    each invalidate the cache. `audition.prompt_hash` covers only critic surfaces
    and is deliberately untouched: the two measurements have disjoint prompts."""
    digest = hashlib.sha256()
    digest.update(refine_system(tuple(sorted(enabled_transforms))).encode())
    digest.update(refine_user("q").encode())
    digest.update(str(PROMPT_VERSION).encode())
    return digest.hexdigest()[:16]


def transforms_hash(enabled_transforms: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(enabled_transforms)).encode()).hexdigest()[:8]


def refine_cache_key(identity: str, enabled_transforms: frozenset[str]) -> str:
    """Keyed on the transform set as well as the identity, so auditioning a
    *candidate* set (say, with `question_behind_the_question` enabled) never
    clobbers the production set's verdict."""
    return f"{identity}::refine::{transforms_hash(enabled_transforms)}"


def load_refine_cache(path: Path) -> dict[str, RefineCacheEntry]:
    """Same degrade-to-empty contract as `audition.load_cache` — corrupt must read
    as "not audited", never as a pass — but validated against the refine entry
    type, which is why the critic loader is not reused directly."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, RefineCacheEntry] = {}
    for key, value in (raw or {}).items():
        try:
            out[key] = RefineCacheEntry.model_validate(value)
        except Exception:
            continue
    return out


def save_refine_cache(path: Path, entries: dict[str, RefineCacheEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: v.model_dump(mode="json") for k, v in entries.items()}, indent=2)
    )


def refine_cached_judgement(
    cfg: RefineAuditionConfig,
    identity: str,
    enabled_transforms: frozenset[str],
    thresholds: RefineAuditionThresholds | None = None,
    now: float | None = None,
) -> Judgement | None:
    """The refine verdict for one identity, read from the cache only — never spends
    a call (`ra doctor` and any startup warning sit on surprise-bill paths, same as
    `audition.cached_judgements`). None means unmeasured, and unmeasured must be
    shown as unmeasured, never as a pass.

    The structured-output mode is not compared, for the reason `cached_judgements`
    does not compare it: learning it costs the probe this function promises not to
    spend (D-audition-probe-parity)."""
    try:
        corpus_hash = load_refine_fixtures().corpus_hash
    except FixtureError:
        return None
    cache = load_refine_cache(cfg.cache_path)
    entry = cache.get(refine_cache_key(identity, enabled_transforms))
    if entry is None:
        return None
    ph = refine_prompt_hash(enabled_transforms)
    at = time.time() if now is None else now
    if not entry.matches(
        corpus_hash, ph, cfg.repetitions, structured_output_mode=None
    ) or entry.is_stale(at, cfg.max_age_days):
        return None
    return judge_refine(entry.metrics, thresholds or cfg.thresholds)
