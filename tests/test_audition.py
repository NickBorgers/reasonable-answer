"""Offline tests for the critic audition harness.

Every test here runs with no network. The grader is a pure function by construction,
and `test_grader_needs_no_client` is the test that pins it that way — if grading ever
grows an LLM call, that test stops being satisfiable.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from reasonable_answer import audition, prompts
from reasonable_answer.config import AuditionConfig, AuditionThresholds, Roster
from reasonable_answer.schemas import CritiqueOutput, LensResult, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity

CORPUS = Path(__file__).parent / "fixtures" / "audition"


def issue(
    category: Category,
    section: int,
    paragraph: int,
    severity: Severity | None = None,
    claim_span: str = "span",
):
    from reasonable_answer.taxonomy import SEVERITY_FLOOR

    return RawIssue(
        category=category,
        severity=severity or SEVERITY_FLOOR[category],
        locus=StructuralRef(section=section, paragraph=paragraph),
        claim_span=claim_span,
        rationale="rationale",
        instruction="instruction",
    )


def result(lens: Lens, *issues: RawIssue, failed: bool = False) -> LensResult:
    return LensResult(
        lens=lens,
        artifact_hash="h",
        critic_alias="a",
        critic_identity="provider/model",
        artifact_author_identity=audition.AUDITION_AUTHOR,
        failed=failed,
        issues=list(issues),
    )


# ------------------------------------------------------------------ fixtures


def test_shipped_corpus_loads_and_covers_both_directions():
    fixtures = audition.load_fixtures(CORPUS)
    assert fixtures.fixtures, "corpus is empty"
    assert fixtures.corpus_hash

    planted = [f for f in fixtures.fixtures if not f.is_control]
    controls = [f for f in fixtures.fixtures if f.is_control]
    assert planted, "no planted fixtures: sensitivity would be unmeasurable"
    assert controls, "no control fixtures: the noise direction would be unmeasurable"

    # Every lens must have something to be measured on, or its models grade
    # INSUFFICIENT forever and the harness silently covers nothing.
    for lens in Lens:
        assert any(f.lens is lens for f in planted), f"lens {lens.value} has no planted fixture"


def test_every_lens_sees_all_controls():
    fixtures = audition.load_fixtures(CORPUS)
    controls = {f.id for f in fixtures.fixtures if f.is_control}
    for lens in Lens:
        visible = {f.id for f in fixtures.for_lens(lens)}
        assert controls <= visible


def test_slots_are_substituted_and_deterministic():
    a = audition.load_fixtures(CORPUS)
    b = audition.load_fixtures(CORPUS)
    for left, right in zip(a.fixtures, b.fixtures, strict=True):
        assert left.artifact == right.artifact, "instantiation must not vary between loads"
        assert "{{" not in left.artifact, f"unsubstituted slot left in {left.id}"


def test_planted_loci_exist_in_their_artifact():
    """A manifest pointing at a paragraph that does not exist can never be detected."""
    from reasonable_answer import report as report_mod

    for fixture in audition.load_fixtures(CORPUS).fixtures:
        structure = report_mod.parse(fixture.artifact)
        for defect in fixture.defects:
            assert structure.contains(defect.locus), (
                f"{fixture.id}: planted locus {defect.locus} is not in the artifact"
            )


def test_corpus_hash_changes_when_a_fixture_changes(tmp_path):
    src = audition.load_fixtures(CORPUS)
    copy = tmp_path / "corpus"
    copy.mkdir()
    for fixture_dir in CORPUS.iterdir():
        target = copy / fixture_dir.name
        target.mkdir()
        (target / "artifact.md").write_bytes((fixture_dir / "artifact.md").read_bytes())
        (target / "manifest.yaml").write_bytes((fixture_dir / "manifest.yaml").read_bytes())
    assert audition.load_fixtures(copy).corpus_hash == src.corpus_hash

    edited = next(copy.iterdir()) / "artifact.md"
    edited.write_text(edited.read_text() + "\n\nAn added paragraph.\n")
    assert audition.load_fixtures(copy).corpus_hash != src.corpus_hash


def test_fixture_rejects_category_outside_its_lens(tmp_path):
    """Otherwise the fixture grades every correct critic as blind: triage rejects an
    out-of-scope category, so no valid critic could ever report it."""
    d = tmp_path / "corpus" / "bad"
    d.mkdir(parents=True)
    (d / "artifact.md").write_text("# Q\n\nBody paragraph.\n")
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "lens": "logic",
                "question": "q",
                "defects": [{"category": "uncited_claim", "locus": {"section": 1, "paragraph": 1}}],
            }
        )
    )
    with pytest.raises(audition.FixtureError, match="not in scope"):
        audition.load_fixtures(tmp_path / "corpus")


def test_control_with_defects_is_rejected(tmp_path):
    d = tmp_path / "corpus" / "bad"
    d.mkdir(parents=True)
    (d / "artifact.md").write_text("# Q\n\nBody paragraph.\n")
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "lens": "logic",
                "kind": "control",
                "question": "q",
                "defects": [
                    {"category": "invalid_inference", "locus": {"section": 1, "paragraph": 1}}
                ],
            }
        )
    )
    with pytest.raises(audition.FixtureError, match="control"):
        audition.load_fixtures(tmp_path / "corpus")


# ------------------------------------------------------------------- grading


def test_grader_needs_no_client():
    """The grading path is pure. No client, no network, no config — just data.

    This is the test that keeps an LLM out of the grader. An LLM grader would make the
    harness's trustworthiness depend on the very property it exists to measure.
    """
    fixture = audition.Fixture(
        id="f",
        lens=Lens.LOGIC,
        question="q",
        artifact="# Q\n\nBody.\n",
        defects=(
            audition.PlantedDefect(
                category=Category.CONTRADICTED_CLAIM, locus=StructuralRef(section=4, paragraph=2)
            ),
        ),
    )
    detections = audition.grade(fixture, result(Lens.LOGIC, issue(Category.CONTRADICTED_CLAIM, 4, 2)))
    assert [d.strict for d in detections] == [True]


@pytest.mark.parametrize(
    ("section", "paragraph", "expected"),
    [
        (4, 2, True),   # exact
        (4, 1, True),   # within the paragraph tolerance
        (4, 3, True),
        (4, 4, False),  # outside it
        (3, 2, False),  # right paragraph, wrong section — never a match
    ],
)
def test_locus_window(section, paragraph, expected):
    fixture = audition.Fixture(
        id="f",
        lens=Lens.LOGIC,
        question="q",
        artifact="x",
        defects=(
            audition.PlantedDefect(
                category=Category.CONTRADICTED_CLAIM, locus=StructuralRef(section=4, paragraph=2)
            ),
        ),
    )
    found = audition.grade(
        fixture, result(Lens.LOGIC, issue(Category.CONTRADICTED_CLAIM, section, paragraph))
    )
    assert found[0].strict is expected


def test_same_lens_category_confusion_scores_lens_but_not_strict():
    """Critics reasonably disagree between two evidence categories on one sentence.
    Grading that as a miss would penalize a critic that is doing its job."""
    fixture = audition.Fixture(
        id="f",
        lens=Lens.EVIDENCE,
        question="q",
        artifact="x",
        defects=(
            audition.PlantedDefect(
                category=Category.UNCITED_CLAIM, locus=StructuralRef(section=3, paragraph=1)
            ),
        ),
    )
    found = audition.grade(
        fixture, result(Lens.EVIDENCE, issue(Category.MISREPRESENTED_SOURCE, 3, 1))
    )
    assert found[0].strict is False
    assert found[0].same_lens is True


def test_anywhere_defect_ignores_the_locus_window():
    """An omission has no honest locus; a critic may file it anywhere sensible."""
    fixture = audition.Fixture(
        id="f",
        lens=Lens.COMPLETENESS,
        question="q",
        artifact="x",
        defects=(
            audition.PlantedDefect(
                category=Category.OMITTED_COUNTERARGUMENT,
                locus=StructuralRef(section=1, paragraph=1),
                anywhere=True,
            ),
        ),
    )
    found = audition.grade(
        fixture, result(Lens.COMPLETENESS, issue(Category.OMITTED_COUNTERARGUMENT, 9, 9))
    )
    assert found[0].strict is True


def test_minor_severity_issue_does_not_count_as_a_detection():
    """A `stylistic` note on the right paragraph is not finding the defect. Grading is
    on post-clamp material severity, which is what triage would count."""
    fixture = audition.Fixture(
        id="f",
        lens=Lens.COMPLETENESS,
        question="q",
        artifact="x",
        defects=(
            audition.PlantedDefect(
                category=Category.OMITTED_COUNTERARGUMENT,
                locus=StructuralRef(section=2, paragraph=1),
            ),
        ),
    )
    found = audition.grade(
        fixture, result(Lens.COMPLETENESS, issue(Category.STYLISTIC, 2, 1, Severity.MINOR))
    )
    assert found[0].strict is False
    assert found[0].same_lens is False


def test_material_count_applies_the_severity_floor():
    """A critic under-rating a blocking category still raised a material issue —
    triage would clamp it up, so the noise measure must too."""
    noisy = result(
        Lens.EVIDENCE,
        issue(Category.FABRICATED_CITATION, 1, 1, Severity.MINOR),
        issue(Category.STYLISTIC, 1, 1, Severity.MINOR),
    )
    assert audition.material_issue_count(noisy) == 1


# ------------------------------------------------------------------ verdicts


def metrics(**kwargs) -> audition.Metrics:
    base = dict(alias="a", identity="provider/model", lens=Lens.EVIDENCE, calls=10)
    return audition.Metrics(**{**base, **kwargs})


THRESHOLDS = AuditionThresholds()


def test_silent_critic_is_unfit():
    """The llama-4-scout signature: never flags anything, on any artifact."""
    silent = metrics(planted_total=6, obvious_total=6, control_runs=4, control_clean_runs=4)
    judgement = audition.judge(silent, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert "0 of 6" in judgement.reasons[0]


def test_silent_critic_is_unfit_under_every_threshold_setting():
    """No amount of threshold tuning may permit a model that finds nothing obvious —
    a lens staffed by it is not being reviewed."""
    silent = metrics(planted_total=6, obvious_total=6, control_runs=4, control_clean_runs=4)
    permissive = AuditionThresholds(
        min_obvious_sensitivity=0.0,
        warn_lens_sensitivity=0.0,
        max_control_material_rate=99.0,
        warn_control_material_rate=99.0,
        max_schema_failure_rate=1.0,
    )
    assert audition.judge(silent, permissive).verdict is audition.Verdict.UNFIT


def test_flagging_everything_is_also_unfit():
    """Perfect sensitivity, and useless: it manufactures work every round, drains the
    critique budget and drives the run to rule 13 on a sound report."""
    noisy = metrics(
        planted_total=6,
        strict_hits=6,
        same_lens_hits=6,
        obvious_total=6,
        obvious_hits=6,
        control_runs=4,
        control_material_issues=20,
        control_clean_runs=0,
    )
    judgement = audition.judge(noisy, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert any("invents" in r for r in judgement.reasons)


def test_schema_failures_are_unfit_and_distinct_from_silence():
    broken = metrics(calls=10, schema_failures=8, planted_total=2, obvious_total=2, obvious_hits=2)
    judgement = audition.judge(broken, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert any("schema" in r for r in judgement.reasons)


def test_competent_critic_is_fit():
    good = metrics(
        planted_total=6,
        strict_hits=5,
        same_lens_hits=6,
        obvious_total=4,
        obvious_hits=4,
        control_runs=4,
        control_material_issues=0,
        control_clean_runs=4,
    )
    assert audition.judge(good, THRESHOLDS).verdict is audition.Verdict.FIT


def test_partial_sensitivity_is_marginal_not_unfit():
    ok_ish = metrics(
        planted_total=10,
        strict_hits=4,
        same_lens_hits=5,
        obvious_total=4,
        obvious_hits=3,
        control_runs=4,
        control_material_issues=0,
        control_clean_runs=4,
    )
    assert audition.judge(ok_ish, THRESHOLDS).verdict is audition.Verdict.MARGINAL


def test_unmeasured_is_insufficient_never_fit():
    assert audition.judge(metrics(calls=0), THRESHOLDS).verdict is audition.Verdict.INSUFFICIENT
    assert audition.judge(metrics(calls=4), THRESHOLDS).verdict is audition.Verdict.INSUFFICIENT


# ------------------------------------------------------- roster-level warnings


def roster() -> Roster:
    return Roster(
        writers=["w1", "w2"],
        critics={
            "logic": ["c_good", "c_weak"],
            "evidence": ["c_good", "w1", "c_weak"],
            "completeness": ["c_good", "w1", "w2"],
        },
    )


IDENTITIES = {
    "w1": "p/w1",
    "w2": "p/w2",
    "c_good": "p/good",
    "c_weak": "p/weak",
}


def test_warns_when_a_weak_critic_sits_in_the_confirmation_position():
    """Position 3 is unreachable on pass 1 and reached on the rule 8 top-up, where a
    false clean raises cleared_count to 2 and terminates the run `accepted`."""
    judgements = {
        ("p/weak", Lens.EVIDENCE): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(roster(), IDENTITIES, judgements)
    assert any("position 3" in w and "strong_met" in w for w in warnings)


def test_no_position_warning_when_the_weak_critic_is_first():
    judgements = {
        ("p/weak", Lens.LOGIC): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(roster(), IDENTITIES, judgements)
    assert not any("position" in w for w in warnings)


def test_warns_when_an_entire_lens_is_unstaffed():
    judgements = {
        ("p/good", Lens.LOGIC): audition.Judgement(audition.Verdict.UNFIT, ()),
        ("p/weak", Lens.LOGIC): audition.Judgement(audition.Verdict.MARGINAL, ()),
    }
    warnings = audition.roster_warnings(roster(), IDENTITIES, judgements)
    assert any("unstaffed" in w and "logic" in w for w in warnings)


def test_assignments_dedupe_by_resolved_identity():
    """Two aliases for one model are one reviewer (RA-017) and one audition."""
    r = Roster(
        writers=["w1", "w2"],
        critics={"logic": ["a", "b"], "evidence": ["a"], "completeness": ["a"]},
    )
    identities = {"w1": "p/w1", "w2": "p/w2", "a": "p/same", "b": "p/same"}
    logic_slots = [s for s in audition.assignments(r, identities) if s.lens is Lens.LOGIC]
    assert len(logic_slots) == 1


# --------------------------------------------------------------------- cache


def entry(**kwargs) -> audition.CacheEntry:
    base = dict(
        metrics=metrics(),
        corpus_hash="corpus",
        prompt_hash="prompt",
        repetitions=3,
        recorded_at=time.time(),
    )
    return audition.CacheEntry(**{**base, **kwargs})


def test_cache_entry_is_invalidated_by_corpus_prompt_or_repetitions():
    e = entry()
    assert e.matches("corpus", "prompt", 3)
    assert not e.matches("other-corpus", "prompt", 3)
    assert not e.matches("corpus", "other-prompt", 3)
    assert not e.matches("corpus", "prompt", 5)


def test_cache_entry_expires():
    now = time.time()
    fresh = entry(recorded_at=now)
    old = entry(recorded_at=now - 31 * 86400)
    assert not fresh.is_stale(now, 30)
    assert old.is_stale(now, 30)


def test_corrupt_cache_reads_as_empty_never_as_passing(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json")
    assert audition.load_cache(path) == {}

    path.write_text('{"k": {"unexpected": true}}')
    assert audition.load_cache(path) == {}


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "nested" / "cache.json"
    entries = {audition.cache_key("p/m", Lens.EVIDENCE): entry()}
    audition.save_cache(path, entries)
    loaded = audition.load_cache(path)
    assert loaded[audition.cache_key("p/m", Lens.EVIDENCE)].metrics.identity == "provider/model"


def test_prompt_hash_tracks_the_critic_prompt(monkeypatch):
    before = audition.prompt_hash()
    monkeypatch.setattr(prompts, "CRITIC_SYSTEM", prompts.CRITIC_SYSTEM + " extra clause")
    assert audition.prompt_hash() != before


# -------------------------------------------------------------- running, offline


class ScriptedClient:
    """Returns a fixed issue list per call. No network."""

    def __init__(self, respond):
        self.respond = respond
        self.prompts: list[tuple[str, str]] = []

    def structured(self, alias, system, user, schema, max_tokens=0):
        self.prompts.append((system, user))
        return CritiqueOutput(issues=self.respond(alias, user))


def test_run_assignment_measures_both_directions_offline():
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.EVIDENCE, position=0)

    # A critic that finds the planted uncited claim and nothing else. The span is a
    # real quote from S3.P1 because `require_verbatim_spans` defaults on, exactly as
    # in a run — a loose quote fails the lens closed rather than scoring a detection.
    span = "Every credible study of the 2021-2023 period"

    def respond(alias, user):
        if span in user:
            return [issue(Category.UNCITED_CLAIM, 3, 1, claim_span=span)]
        return []

    m = audition.run_assignment(ScriptedClient(respond), slot, fixtures, repetitions=1)
    assert m.calls == len(fixtures.for_lens(Lens.EVIDENCE))
    assert m.schema_failures == 0
    assert m.strict_hits == 1
    assert m.control_runs == 2
    assert m.control_material_issues == 0
    assert audition.judge(m, THRESHOLDS).verdict is not audition.Verdict.INSUFFICIENT


def test_run_assignment_uses_the_production_critic_prompt():
    """The harness must exercise the production prompt, or it measures a critic that
    does not exist in a run."""
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.EVIDENCE, position=0)
    client = ScriptedClient(lambda alias, user: [])
    audition.run_assignment(client, slot, fixtures, repetitions=1)

    systems = {system for system, _ in client.prompts}
    assert systems == {prompts.CRITIC_SYSTEM}

    fixture = next(f for f in fixtures.for_lens(Lens.EVIDENCE) if f.id == "uncited-claim-01")
    from reasonable_answer import report as report_mod

    expected = prompts.critic_user(
        Lens.EVIDENCE, fixture.question, report_mod.render_with_loci(fixture.artifact), None
    )
    assert any(user == expected for _, user in client.prompts)


def test_failed_lens_counts_as_schema_failure_not_as_silence():
    """Different problems, different fixes: one is a prompt/mode issue, the other means
    replace the model. Conflating them would send an operator down the wrong path."""
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.LOGIC, position=0)

    class Broken:
        def structured(self, alias, system, user, schema, max_tokens=0):
            # An out-of-scope category fails the lens closed in triage.
            return CritiqueOutput(issues=[issue(Category.UNCITED_CLAIM, 1, 1)])

    m = audition.run_assignment(Broken(), slot, fixtures, repetitions=1)
    assert m.schema_failures == m.calls
    assert m.planted_total == 0
    assert m.control_runs == 0
    assert audition.judge(m, THRESHOLDS).verdict is audition.Verdict.UNFIT


def test_audition_is_disabled_by_default():
    """A checkout with no credential must behave exactly as it always has."""
    cfg = AuditionConfig()
    assert cfg.enabled is False
    assert cfg.enforce is False


# ----------------------------------------------------- startup enforcement


def _write_cache(path, roster_obj, identities, verdict_metrics, corpus_hash, reps=3, age_days=0):
    entries = {}
    for slot in audition.assignments(roster_obj, identities):
        entries[audition.cache_key(slot.identity, slot.lens)] = audition.CacheEntry(
            metrics=verdict_metrics(slot),
            corpus_hash=corpus_hash,
            prompt_hash=audition.prompt_hash(),
            repetitions=reps,
            recorded_at=time.time() - age_days * 86400,
        )
    audition.save_cache(path, entries)


def _fit(slot):
    return audition.Metrics(
        alias=slot.alias,
        identity=slot.identity,
        lens=slot.lens,
        calls=10,
        planted_total=4,
        strict_hits=4,
        same_lens_hits=4,
        obvious_total=4,
        obvious_hits=4,
        control_runs=2,
        control_clean_runs=2,
    )


def _silent(slot):
    return _fit(slot).model_copy(update={"strict_hits": 0, "same_lens_hits": 0, "obvious_hits": 0})


@pytest.fixture
def enforcing_config(tmp_path):
    from reasonable_answer.config import Config

    return Config(
        roster=roster(),
        audition=AuditionConfig(enforce=True, cache_path=tmp_path / "cache.json"),
        runs_dir=tmp_path / "runs",
    )


def test_enforce_aborts_on_an_unfit_critic(enforcing_config):
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _enforce_audition

    corpus = audition.load_fixtures().corpus_hash
    _write_cache(
        enforcing_config.audition.cache_path, roster(), IDENTITIES, _silent, corpus
    )
    with pytest.raises(ConfigError, match="unfit"):
        _enforce_audition(enforcing_config, IDENTITIES)


def test_enforce_aborts_when_the_cache_is_missing(enforcing_config):
    """Otherwise enforcement is satisfiable by deleting the cache file — the one
    failure mode a fail-closed check must not have."""
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _enforce_audition

    assert not enforcing_config.audition.cache_path.exists()
    with pytest.raises(ConfigError, match="no fresh audition"):
        _enforce_audition(enforcing_config, IDENTITIES)


def test_enforce_aborts_on_a_stale_audition(enforcing_config):
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _enforce_audition

    corpus = audition.load_fixtures().corpus_hash
    _write_cache(
        enforcing_config.audition.cache_path, roster(), IDENTITIES, _fit, corpus, age_days=31
    )
    with pytest.raises(ConfigError, match="no fresh audition"):
        _enforce_audition(enforcing_config, IDENTITIES)


def test_enforce_passes_when_every_slot_is_fit_and_fresh(enforcing_config):
    from reasonable_answer.graph import _enforce_audition

    corpus = audition.load_fixtures().corpus_hash
    _write_cache(enforcing_config.audition.cache_path, roster(), IDENTITIES, _fit, corpus)
    assert _enforce_audition(enforcing_config, IDENTITIES) == []


def test_without_enforce_an_unfit_critic_only_warns(tmp_path):
    """The default must never block a run — an operator blocked by an expired
    audition disables the harness outright, which is worse than a loud warning."""
    from reasonable_answer.config import Config
    from reasonable_answer.graph import _enforce_audition

    config = Config(
        roster=roster(),
        audition=AuditionConfig(enforce=False, cache_path=tmp_path / "cache.json"),
        runs_dir=tmp_path / "runs",
    )
    corpus = audition.load_fixtures().corpus_hash
    _write_cache(config.audition.cache_path, roster(), IDENTITIES, _silent, corpus)

    warnings = _enforce_audition(config, IDENTITIES)
    assert any("unfit" in w for w in warnings)


def test_startup_never_spends_calls_on_an_audition(tmp_path, monkeypatch):
    """A run reads the cache and nothing else. If starting a run could trigger an
    audition, every run would silently cost |critics| x |fixtures| x repetitions."""
    from reasonable_answer.config import Config
    from reasonable_answer.graph import _enforce_audition

    def explode(*args, **kwargs):
        raise AssertionError("startup must not run an audition")

    monkeypatch.setattr(audition, "run_audition", explode)
    monkeypatch.setattr(audition, "run_assignment", explode)

    config = Config(
        roster=roster(),
        audition=AuditionConfig(cache_path=tmp_path / "cache.json"),
        runs_dir=tmp_path / "runs",
    )
    _enforce_audition(config, IDENTITIES)
