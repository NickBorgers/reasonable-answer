"""The refine audition harness (D33), fully offline — no network, no proxy.

Grading is deliberately a pure function (`grade_refine` takes a fixture and a tuple
of suggestions, nothing else), so most of this file needs no client at all. The
runner tests use a purpose-built stub, same reasoning as `tests/test_refine.py`:
`FakeClient` in tests/fakes.py speaks the graph's schemas, not
`RefinementSuggestions`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from reasonable_answer import prompts, refine_audition
from reasonable_answer.audition import FixtureError, Verdict
from reasonable_answer.config import RefineAuditionConfig, RefineAuditionThresholds, RefineConfig
from reasonable_answer.refine_audition import (
    RefineCacheEntry,
    RefineFixture,
    RefineMetrics,
    ScopeCheck,
    grade_refine,
    judge_refine,
    load_refine_cache,
    load_refine_fixtures,
    pair_asymmetries,
    refine_cache_key,
    refine_cached_judgement,
    refine_prompt_hash,
    run_refine_audition,
    save_refine_cache,
)
from reasonable_answer.schemas import RefinementSuggestion, RefinementSuggestions
from reasonable_answer.web.refine import Suggestion

DEFAULT_ENABLED = frozenset(RefineConfig().enabled_transforms)


def suggestion(
    question: str,
    transform: str = "name_the_outcome",
    label: str = "name the outcome",
) -> Suggestion:
    return Suggestion(transform=transform, label=label, question=question)


def fixture(**overrides: Any) -> RefineFixture:
    base: dict[str, Any] = {
        "id": "fx",
        "question": "Is fluoride in tap water a net positive for public health in the US?",
        "allowed_transforms": ("name_the_outcome",),
        "scope": {
            "domain_terms": ("public health",),
            "enumeration_groups": (("dental", "teeth"), ("skeletal", "bone"), ("neuro",)),
            "min_groups": 2,
        },
        "require_terms": ("fluorid",),
    }
    base.update(overrides)
    return RefineFixture.model_validate(base)


# ------------------------------------------------------------------------ corpus


def test_shipped_corpus_loads_and_covers_both_directions():
    corpus = load_refine_fixtures()
    assert any(f.control for f in corpus.fixtures)
    assert any(not f.control for f in corpus.fixtures)


def test_every_default_enabled_transform_has_a_positive_fixture():
    # A transform with no fixture is a transform whose firing behaviour is
    # unmeasured — the exact gap D33 exists to close.
    corpus = load_refine_fixtures()
    expected = {f.expected_transform for f in corpus.fixtures if f.expected_transform}
    covered = expected | {
        t for f in corpus.fixtures for t in f.allowed_transforms if f.scope or f.require_terms
    }
    assert covered >= DEFAULT_ENABLED


def test_corpus_hash_is_stable_and_edit_sensitive(tmp_path: Path):
    d = tmp_path / "corpus"
    (d / "one").mkdir(parents=True)
    (d / "one" / "manifest.yaml").write_text(
        "kind: control\nquestion: 'What does the research say about X in France?'\n"
    )
    first = load_refine_fixtures(d).corpus_hash
    assert load_refine_fixtures(d).corpus_hash == first
    (d / "one" / "manifest.yaml").write_text(
        "kind: control\nquestion: 'What does the research say about Y in France?'\n"
    )
    assert load_refine_fixtures(d).corpus_hash != first


def test_slots_resolve_in_question_and_term_lists(tmp_path: Path):
    d = tmp_path / "corpus"
    (d / "one").mkdir(parents=True)
    (d / "one" / "manifest.yaml").write_text(
        "question: 'Is {{city}} pro-police or anti-police?'\n"
        "expected_transform: split_the_either_or\n"
        "require_terms: ['{{city}}']\n"
        "slots: {city: [Chicago, Houston]}\n"
    )
    fx = load_refine_fixtures(d).fixtures[0]
    assert fx.require_terms[0] in ("Chicago", "Houston")
    # The same seeded choice serves the question and the term that references it.
    assert fx.require_terms[0] in fx.question


def test_control_with_expectations_is_rejected(tmp_path: Path):
    d = tmp_path / "corpus"
    (d / "bad").mkdir(parents=True)
    (d / "bad" / "manifest.yaml").write_text(
        "kind: control\nquestion: 'Well posed?'\nexpected_transform: name_the_outcome\n"
    )
    with pytest.raises(FixtureError):
        load_refine_fixtures(d)


def test_fixture_that_grades_nothing_is_rejected(tmp_path: Path):
    d = tmp_path / "corpus"
    (d / "bad").mkdir(parents=True)
    (d / "bad" / "manifest.yaml").write_text("question: 'Just a question?'\n")
    with pytest.raises(FixtureError):
        load_refine_fixtures(d)


def test_unknown_transform_is_rejected(tmp_path: Path):
    d = tmp_path / "corpus"
    (d / "bad").mkdir(parents=True)
    (d / "bad" / "manifest.yaml").write_text(
        "question: 'Q?'\nexpected_transform: rewrite_everything\n"
    )
    with pytest.raises(FixtureError):
        load_refine_fixtures(d)


def test_empty_corpus_is_rejected(tmp_path: Path):
    with pytest.raises(FixtureError):
        load_refine_fixtures(tmp_path)


def test_disabled_transform_fixture_is_skipped_not_run():
    corpus = load_refine_fixtures()
    runnable = corpus.runnable(DEFAULT_ENABLED)
    assert all(f.expected_transform != "question_behind_the_question" for f in runnable)
    # ...but the pair is present in the full corpus, waiting for --transforms.
    assert any(f.pair for f in corpus.fixtures)


# ----------------------------------------------------------------------- grading


def test_grader_needs_no_client():
    # Pure: a fixture and a tuple of suggestions in, a grade out. If this ever
    # needs a client, the harness has lost the property that makes it trustworthy.
    grade = grade_refine(fixture(), (suggestion("What about fluoride and public health?"),))
    assert not grade.violation


def test_downscoped_suggestion_is_a_scope_violation():
    # The production regression verbatim: "public health" narrowed to dental-only.
    grade = grade_refine(
        fixture(),
        (
            suggestion(
                "What is the impact of fluoride in tap water on dental health "
                "outcomes for children in the United States?"
            ),
        ),
    )
    assert grade.scope_violation


def test_enumerated_suggestion_passes_scope():
    grade = grade_refine(
        fixture(),
        (
            suggestion(
                "What are the measured effects of fluoridation on dental, skeletal, "
                "and neurological outcomes in the United States?"
            ),
        ),
    )
    assert not grade.scope_violation


def test_retained_domain_term_passes_scope():
    grade = grade_refine(
        fixture(),
        (suggestion("What are the effects of fluoridated water on public health in the US?"),),
    )
    assert not grade.scope_violation


def test_two_stems_from_one_group_count_as_one_group():
    # Enumerating synonyms for a single component is exactly the narrowing being
    # measured — teeth + dental is still just the dental component.
    grade = grade_refine(
        fixture(),
        (suggestion("What does fluoride do to teeth and dental caries in the US?"),),
    )
    assert grade.scope_violation


def test_silence_always_passes():
    assert not grade_refine(fixture(), ()).violation
    assert not grade_refine(fixture(silence_ok=True), ()).violation


def test_disallowed_transform_is_a_violation():
    grade = grade_refine(
        fixture(),
        (suggestion("What about fluoride and public health?", transform="split_the_either_or"),),
    )
    assert grade.transform_violation


def test_missing_required_term_is_a_violation():
    grade = grade_refine(
        fixture(),
        (suggestion("What are the effects of water treatment on public health?"),),
    )
    assert grade.term_violation


def test_meta_commentary_is_a_violation_everywhere():
    grade = grade_refine(
        fixture(),
        (
            suggestion(
                "What about fluoride and public health?", label="your question is loaded"
            ),
        ),
    )
    assert grade.term_violation


def test_global_forbid_terms_match_whole_words_only():
    # 'unbiased' must not trip 'biased', 'downloaded' must not trip 'loaded'.
    grade = grade_refine(
        fixture(),
        (
            suggestion(
                "What does unbiased research say about fluoride and public health?",
                label="downloaded studies",
            ),
        ),
    )
    assert not grade.term_violation


def test_short_terms_match_whole_words_only():
    scope = ScopeCheck(enumeration_groups=(("IQ",), ("bone",)), min_groups=1)
    assert not scope.passes("What is the best technique for water treatment?")
    assert scope.passes("Does fluoride affect IQ in children?")


def test_scope_check_requires_something_to_check():
    with pytest.raises(ValueError):
        ScopeCheck()
    with pytest.raises(ValueError):
        ScopeCheck(enumeration_groups=(("a",),), min_groups=2)


# ----------------------------------------------------------------------- judging


def _metrics(**overrides: Any) -> RefineMetrics:
    base: dict[str, Any] = {
        "alias": "refiner",
        "identity": "vendor/refiner",
        "transforms": tuple(sorted(DEFAULT_ENABLED)),
        "calls": 20,
        "graded_runs": 15,
        "control_runs": 5,
        "control_clean_runs": 5,
        "expected_total": 10,
        "fired": 8,
        "obvious_runs": 10,
    }
    base.update(overrides)
    return RefineMetrics.model_validate(base)


THRESHOLDS = RefineAuditionThresholds()


def test_clean_metrics_are_fit():
    judgement = judge_refine(_metrics(), THRESHOLDS)
    assert judgement.verdict is Verdict.FIT


def test_no_calls_is_insufficient():
    judgement = judge_refine(_metrics(calls=0, graded_runs=0, control_runs=0), THRESHOLDS)
    assert judgement.verdict is Verdict.INSUFFICIENT


def test_nothing_graded_is_insufficient():
    judgement = judge_refine(
        _metrics(graded_runs=0, control_runs=0, obvious_runs=0, expected_total=0), THRESHOLDS
    )
    assert judgement.verdict is Verdict.INSUFFICIENT


def test_single_obvious_violation_is_unfit_even_with_perfect_fire_rate():
    # The fluoride fixture is obvious-tier for exactly this reason: silence was
    # always available, so one narrowing in the sample is disqualifying.
    judgement = judge_refine(
        _metrics(violation_runs=1, obvious_violation_runs=1, fired=10), THRESHOLDS
    )
    assert judgement.verdict is Verdict.UNFIT


def test_noisy_on_controls_is_unfit():
    judgement = judge_refine(
        _metrics(control_suggestions=4, control_clean_runs=1), THRESHOLDS
    )
    assert judgement.verdict is Verdict.UNFIT


def test_low_fire_rate_alone_is_only_marginal():
    judgement = judge_refine(_metrics(fired=2), THRESHOLDS)
    assert judgement.verdict is Verdict.MARGINAL
    assert any("fire rate" in r for r in judgement.reasons)


def test_schema_failures_are_unfit_before_anything_else():
    judgement = judge_refine(
        _metrics(schema_failures=10, graded_runs=0, control_runs=0), THRESHOLDS
    )
    assert judgement.verdict is Verdict.UNFIT


# ------------------------------------------------------------------------- cache


def test_cache_entry_matches_and_invalidates():
    entry = RefineCacheEntry(
        metrics=_metrics(),
        corpus_hash="c1",
        prompt_hash="p1",
        repetitions=5,
        recorded_at=1_000.0,
    )
    assert entry.matches("c1", "p1", 5)
    assert not entry.matches("c2", "p1", 5)
    assert not entry.matches("c1", "p2", 5)
    assert not entry.matches("c1", "p1", 3)
    assert not entry.is_stale(1_000.0 + 86400, 30)
    assert entry.is_stale(1_000.0 + 31 * 86400, 30)


def test_prompt_hash_changes_when_guardrails_change(monkeypatch: pytest.MonkeyPatch):
    # Editing REFINE_GUARDRAILS must invalidate every cached refine verdict — a
    # verdict is a claim about a prompt surface that would no longer exist.
    before = refine_prompt_hash(DEFAULT_ENABLED)
    monkeypatch.setattr(prompts, "REFINE_GUARDRAILS", prompts.REFINE_GUARDRAILS + " (edited)")
    assert refine_prompt_hash(DEFAULT_ENABLED) != before


def test_prompt_hash_changes_with_prompt_version(monkeypatch: pytest.MonkeyPatch):
    before = refine_prompt_hash(DEFAULT_ENABLED)
    monkeypatch.setattr(refine_audition, "PROMPT_VERSION", 99)
    assert refine_prompt_hash(DEFAULT_ENABLED) != before


def test_candidate_set_entry_does_not_evict_production_entry(tmp_path: Path):
    candidate = frozenset(DEFAULT_ENABLED | {"question_behind_the_question"})
    assert refine_cache_key("m", DEFAULT_ENABLED) != refine_cache_key("m", candidate)
    path = tmp_path / "cache.json"
    entries = {
        refine_cache_key("m", DEFAULT_ENABLED): RefineCacheEntry(
            metrics=_metrics(), corpus_hash="c", prompt_hash="p", repetitions=5, recorded_at=1.0
        ),
        refine_cache_key("m", candidate): RefineCacheEntry(
            metrics=_metrics(), corpus_hash="c", prompt_hash="p2", repetitions=5, recorded_at=2.0
        ),
    }
    save_refine_cache(path, entries)
    assert set(load_refine_cache(path)) == set(entries)


def test_corrupt_cache_degrades_to_empty(tmp_path: Path):
    path = tmp_path / "cache.json"
    path.write_text("{not json")
    assert load_refine_cache(path) == {}


def test_cached_judgement_reads_only_the_cache(tmp_path: Path):
    cfg = RefineAuditionConfig(cache_path=tmp_path / "cache.json")
    assert refine_cached_judgement(cfg, "vendor/m", DEFAULT_ENABLED) is None
    corpus_hash = load_refine_fixtures().corpus_hash
    entries = {
        refine_cache_key("vendor/m", DEFAULT_ENABLED): RefineCacheEntry(
            metrics=_metrics(),
            corpus_hash=corpus_hash,
            prompt_hash=refine_prompt_hash(DEFAULT_ENABLED),
            repetitions=cfg.repetitions,
            recorded_at=1_000.0,
        )
    }
    save_refine_cache(cfg.cache_path, entries)
    judgement = refine_cached_judgement(cfg, "vendor/m", DEFAULT_ENABLED, now=1_001.0)
    assert judgement is not None and judgement.verdict is Verdict.FIT
    # A stale entry must read as unmeasured, never as its old verdict.
    assert (
        refine_cached_judgement(cfg, "vendor/m", DEFAULT_ENABLED, now=1_000.0 + 40 * 86400)
        is None
    )


# ------------------------------------------------------------------------ runner


@dataclass
class StubClient:
    """`respond(user) -> RefinementSuggestions`; raising counts as a call failure."""

    respond: Callable[[str], RefinementSuggestions]
    calls: list[str] = field(default_factory=list)

    def structured(self, alias: str, *, system: str, user: str, schema: type, **kwargs: Any):
        assert schema is RefinementSuggestions
        self.calls.append(user)
        return self.respond(user)


def _corpus(tmp_path: Path, *manifests: tuple[str, str]) -> Any:
    d = tmp_path / "corpus"
    for name, body in manifests:
        (d / name).mkdir(parents=True)
        (d / name / "manifest.yaml").write_text(body)
    return load_refine_fixtures(d)


def _cfg(**overrides: Any) -> RefineAuditionConfig:
    base: dict[str, Any] = {"repetitions": 1, "max_concurrency": 1}
    base.update(overrides)
    return RefineAuditionConfig.model_validate(base)


def test_runner_grades_post_filter(tmp_path: Path):
    # The model emits a suggestion for a transform outside the enabled set. The
    # production filter drops it, so the harness must see silence — not a
    # transform violation, and not a firing.
    corpus = _corpus(
        tmp_path,
        (
            "fx",
            "question: 'Is X better?'\n"
            "expected_transform: name_the_outcome\n"
            "allowed_transforms: [name_the_outcome]\n",
        ),
    )
    client = StubClient(
        respond=lambda _u: RefinementSuggestions(
            suggestions=[
                RefinementSuggestion(
                    transform="question_behind_the_question",
                    label="the real question",
                    question="What do people actually mean by X?",
                )
            ]
        )
    )
    metrics = run_refine_audition(
        client, "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg()
    )
    assert metrics.calls == 1
    assert metrics.fired == 0
    assert metrics.violation_runs == 0


def test_runner_counts_schema_failures_separately(tmp_path: Path):
    corpus = _corpus(tmp_path, ("fx", "kind: control\nquestion: 'Well posed about Z?'\n"))

    def boom(_u: str) -> RefinementSuggestions:
        raise RuntimeError("malformed output")

    metrics = run_refine_audition(
        StubClient(respond=boom), "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg()
    )
    assert metrics.schema_failures == 1
    assert metrics.control_runs == 0  # a failed call is not a clean control run


def test_runner_control_accounting(tmp_path: Path):
    corpus = _corpus(tmp_path, ("fx", "kind: control\nquestion: 'Well posed about Z?'\n"))
    client = StubClient(
        respond=lambda _u: RefinementSuggestions(
            suggestions=[
                RefinementSuggestion(
                    transform="name_the_outcome", label="name it", question="Which outcome of Z?"
                )
            ]
        )
    )
    metrics = run_refine_audition(
        client, "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg(repetitions=2)
    )
    assert metrics.control_runs == 2
    assert metrics.control_suggestions == 2
    assert metrics.control_clean_runs == 0
    assert metrics.control_suggestion_rate == 1.0


def test_runner_skips_fixtures_for_disabled_transforms(tmp_path: Path):
    corpus = _corpus(
        tmp_path,
        ("fx", "question: 'Settled Q?'\nexpected_transform: question_behind_the_question\n"),
        ("ctl", "kind: control\nquestion: 'Well posed about Z?'\n"),
    )
    client = StubClient(respond=lambda _u: RefinementSuggestions(suggestions=[]))
    metrics = run_refine_audition(
        client, "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg()
    )
    assert metrics.calls == 1  # only the control ran


def test_silence_ok_false_counts_toward_fire_rate(tmp_path: Path):
    corpus = _corpus(
        tmp_path,
        ("fx", "question: 'Q about W?'\nsilence_ok: false\n"),
    )
    client = StubClient(respond=lambda _u: RefinementSuggestions(suggestions=[]))
    metrics = run_refine_audition(
        client, "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg()
    )
    assert metrics.expected_total == 1
    assert metrics.fired == 0
    assert metrics.violation_runs == 0  # a miss is never a violation


def test_pair_asymmetry_is_reported_not_gated(tmp_path: Path):
    corpus = _corpus(
        tmp_path,
        (
            "a",
            "question: 'Settled A?'\ntier: subtle\npair: p1\n"
            "expected_transform: question_behind_the_question\n",
        ),
        (
            "b",
            "question: 'Settled B?'\ntier: subtle\npair: p1\n"
            "expected_transform: question_behind_the_question\n",
        ),
    )

    def fire_on_a(user: str) -> RefinementSuggestions:
        if "Settled A?" in user:
            return RefinementSuggestions(
                suggestions=[
                    RefinementSuggestion(
                        transform="question_behind_the_question",
                        label="the adjacent question",
                        question="Why does belief in A persist?",
                    )
                ]
            )
        return RefinementSuggestions(suggestions=[])

    enabled = frozenset(DEFAULT_ENABLED | {"question_behind_the_question"})
    metrics = run_refine_audition(
        StubClient(respond=fire_on_a), "refiner", "vendor/refiner", enabled, corpus, _cfg()
    )
    assert pair_asymmetries(corpus, metrics) == {"p1": 1.0}
    # Perfectly asymmetric, yet the verdict pipeline never sees it.
    judgement = judge_refine(metrics, THRESHOLDS)
    assert judgement.verdict is not Verdict.UNFIT


def test_obvious_tier_accounting(tmp_path: Path):
    corpus = _corpus(
        tmp_path,
        (
            "fx",
            "tier: obvious\n"
            "question: 'Is F a net positive for public health in the US?'\n"
            "allowed_transforms: [name_the_outcome]\n"
            "scope:\n"
            "  domain_terms: ['public health']\n"
            "  enumeration_groups: [['dental'], ['skeletal'], ['neuro']]\n"
            "  min_groups: 2\n",
        ),
    )
    client = StubClient(
        respond=lambda _u: RefinementSuggestions(
            suggestions=[
                RefinementSuggestion(
                    transform="name_the_outcome",
                    label="name the outcome",
                    question="What is the effect of F on dental health in the US?",
                )
            ]
        )
    )
    metrics = run_refine_audition(
        client, "refiner", "vendor/refiner", DEFAULT_ENABLED, corpus, _cfg()
    )
    assert metrics.obvious_runs == 1
    assert metrics.obvious_violation_runs == 1
    assert metrics.scope_violations == 1
    assert judge_refine(metrics, THRESHOLDS).verdict is Verdict.UNFIT
