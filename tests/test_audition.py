"""Offline tests for the critic audition harness.

Every test here runs with no network. The grader is a pure function by construction,
and `test_grader_needs_no_client` is the test that pins it that way — if grading ever
grows an LLM call, that test stops being satisfiable.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
import yaml
from fakes import structured_with_repair

from reasonable_answer import audition, prompts
from reasonable_answer.config import (
    AuditionConfig,
    AuditionThresholds,
    Budgets,
    ConfigError,
    Roster,
)
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


def test_every_material_category_has_a_planted_fixture():
    """D-category-coverage. Per-lens coverage is not per-category coverage.

    `grade` scores the relaxed `same_lens` match and `judge` gates on lens-level rates,
    so a critic wholly blind to one category still grades FIT on the strength of the
    categories its lens does cover — the lens looks measured and the blind spot is
    invisible. `misrepresented_source` sat uncovered from the corpus's first day.

    Scoped to the categories that floor at `major` or `blocking`, because those are the
    only ones a detection can be scored on: `_is_material` gates every hit, so a
    minor-floor category (`unclear_structure`, `loaded_language`) can earn credit only
    when a critic volunteers an escalation. Requiring a fixture for those would assert a
    measurement the grader cannot make.
    """
    from reasonable_answer.taxonomy import SEVERITY_FLOOR, is_material

    planted = {
        defect.category
        for fixture in audition.load_fixtures(CORPUS).fixtures
        for defect in fixture.defects
    }
    material = {
        category
        for category in Category
        if category is not Category.STYLISTIC and is_material(SEVERITY_FLOOR[category])
    }
    missing = sorted(category.value for category in material - planted)
    assert not missing, f"material categories with no planted fixture: {', '.join(missing)}"


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


def test_control_declaring_a_lens_is_rejected(tmp_path):
    """D-control-soundness. `lens` on a control asserts a scope nothing honors: `for_lens`
    hands controls to every lens regardless, so the field reads as a soundness claim
    that was never checked — which is how two controls carrying real uncited claims
    graded every competent evidence critic as an inventor of defects."""
    d = tmp_path / "corpus" / "bad"
    d.mkdir(parents=True)
    (d / "artifact.md").write_text("# Q\n\nBody paragraph.\n")
    (d / "manifest.yaml").write_text(
        yaml.safe_dump({"lens": "evidence", "kind": "control", "question": "q"})
    )
    with pytest.raises(audition.FixtureError, match="declares lens"):
        audition.load_fixtures(tmp_path / "corpus")


def test_planted_fixture_without_a_lens_is_rejected(tmp_path):
    """The other direction: nothing would ever grade it."""
    d = tmp_path / "corpus" / "bad"
    d.mkdir(parents=True)
    (d / "artifact.md").write_text("# Q\n\nBody paragraph.\n")
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "question": "q",
                "defects": [{"category": "uncited_claim", "locus": {"section": 1, "paragraph": 1}}],
            }
        )
    )
    with pytest.raises(audition.FixtureError, match="declares no lens"):
        audition.load_fixtures(tmp_path / "corpus")


#: `Surname (Year)` / `Surname et al. (Year)` / `A, B and C (Year)` — the last
#: capitalised word before the year is the one that must appear in `## Sources`.
_INTEXT_CITE = re.compile(r"([A-Z][A-Za-z-]+)(?:\s+et\s+al\.)?\s*\((\d{4})\)")
_SOURCE_ENTRY = re.compile(r"^-\s+([A-Z][A-Za-z-]+)[^(]*\((\d{4})\)")

#: A contested question sourced from one or two places earns `one_sided_sourcing`,
#: which floors to `major` — a finding the critic would be right to raise, scored
#: against it as invention. Four is the floor, not a target.
MIN_CONTROL_SOURCES = 4


def _split_sources(artifact: str) -> tuple[str, list[str]]:
    body, _, sources = artifact.partition("\n## Sources")
    return body, [ln for ln in sources.splitlines() if ln.startswith("- ")]


@pytest.mark.parametrize("fixture_id", ["control-sound-01", "control-sound-02"])
def test_control_citations_resolve_in_both_directions(fixture_id):
    """D-control-soundness, the mechanically checkable part.

    Catches a dangling in-text citation and an orphan Sources entry. It does NOT catch
    the failure that motivated the decision — a claim with no citation marker at all,
    which no regex can distinguish from prose that needs none. That half rests on the
    soundness contract in each manifest and on review.
    """
    fixture = next(
        f for f in audition.load_fixtures(CORPUS).fixtures if f.id == fixture_id
    )
    body, entries = _split_sources(fixture.artifact)
    assert entries, f"{fixture_id}: no '## Sources' section"

    # Count *distinct* sources, not raw entries: `one_sided_sourcing` is a property of how
    # many separate sources back a claim, so the same source listed twice must not count
    # twice. Normalise each entry to its `(surname, year)` identity; an entry the regex
    # cannot parse falls back to its own text, so nothing is silently collapsed.
    def _identity(entry):
        m = _SOURCE_ENTRY.match(entry)
        return (m.group(1), m.group(2)) if m else entry.strip()

    distinct = {_identity(e) for e in entries}
    assert len(distinct) >= MIN_CONTROL_SOURCES, (
        f"{fixture_id}: {len(distinct)} distinct sources — thin enough to earn `one_sided_sourcing`"
    )

    for name, year in sorted(set(_INTEXT_CITE.findall(body))):
        assert any(name in entry and year in entry for entry in entries), (
            f"{fixture_id}: in-text '{name} ({year})' resolves to no Sources entry"
        )
    for match in filter(None, map(_SOURCE_ENTRY.match, entries)):
        surname, year = match.group(1), match.group(2)
        assert surname in body, f"{fixture_id}: Sources entry '{surname} ({year})' is never cited"


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
    # `fixtures_owed=0` keeps the coverage gate vacuous by default so each verdict test
    # exercises the gate it names; the coverage tests below pass it explicitly.
    base = dict(
        alias="a", identity="provider/model", lens=Lens.EVIDENCE, calls=10, fixtures_owed=0
    )
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
    critique budget and drives the run to rule 13's terminal on a sound report."""
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


# ------------------------------------------------- coverage (D-audition-failure-coverage)


def censored_by_failure(**kwargs) -> audition.Metrics:
    """A model with perfect rates on the fixtures it did not fail on.

    The evidence lens owes 5 fixtures x 3 repetitions = 15 calls, so failing every
    repetition of one fixture is 3/15 — exactly `max_schema_failure_rate`, which the
    strict `>` gate admits.
    """
    base = dict(
        calls=15,
        schema_failures=3,
        fixtures_owed=5,
        planted_total=6,
        strict_hits=6,
        same_lens_hits=6,
        obvious_total=6,
        obvious_hits=6,
        control_runs=6,
        control_material_issues=0,
        control_clean_runs=6,
    )
    return metrics(**{**base, **kwargs})


def test_a_fixture_no_call_ever_graded_is_unfit_not_fit():
    """The censoring: a fixture whose every repetition failed leaves `planted_total`,
    `obvious_total` and `control_runs` with it, so every headline rate is computed over
    a corpus subset the model selected by failing — and reads as `fit`."""
    censored = censored_by_failure(uncovered_fixtures=("one-sided-sourcing-01",))
    # The schema gate does not fire: 20% is at the maximum, not over it.
    assert censored.schema_failure_rate <= THRESHOLDS.max_schema_failure_rate

    judgement = audition.judge(censored, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert "one-sided-sourcing-01" in judgement.reasons[0]

    # Same rates, same schema failures, fixture actually measured: fit. That contrast is
    # the whole finding — nothing but coverage separates these two verdicts.
    measured = censored.model_copy(update={"uncovered_fixtures": ()})
    assert audition.judge(measured, THRESHOLDS).verdict is audition.Verdict.FIT


def test_coverage_is_not_tunable_away():
    """Like the zero-obvious rule: not a question of degree. A rate no call contributed
    to is not a lenient measurement, it is the absence of one."""
    censored = censored_by_failure(uncovered_fixtures=("uncited-claim-01",))
    permissive = AuditionThresholds(
        min_obvious_sensitivity=0.0,
        warn_lens_sensitivity=0.0,
        max_control_material_rate=99.0,
        warn_control_material_rate=99.0,
        max_schema_failure_rate=1.0,
    )
    assert audition.judge(censored, permissive).verdict is audition.Verdict.UNFIT


def test_failing_every_control_does_not_read_as_clean():
    """The noise direction censors the same way, and more quietly: `_ratio` returns 0.0
    for a zero denominator, and `judge` gates the noise checks on `control_runs`, so a
    model that breaks on controls specifically switches those gates off rather than
    failing them."""
    censored = censored_by_failure(
        schema_failures=6,
        uncovered_fixtures=("control-sound-01", "control-sound-02"),
        control_runs=0,
        control_material_issues=0,
        control_clean_runs=0,
    )
    assert censored.control_material_rate == 0.0, "unmeasured is indistinguishable from clean"

    # Under the shipped thresholds 6/15 also trips the schema gate; the point here is
    # that coverage catches it independently, for a deployment that tuned that gate up.
    tolerant = AuditionThresholds(max_schema_failure_rate=1.0)
    judgement = audition.judge(censored, tolerant)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert "control-sound-01" in judgement.reasons[0]


def test_metrics_cannot_claim_more_uncovered_fixtures_than_it_owed():
    with pytest.raises(ValueError, match="contradicts itself"):
        metrics(fixtures_owed=1, uncovered_fixtures=("a", "b"))


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


def test_a_cached_verdict_predating_coverage_reads_as_not_audited(tmp_path):
    """`fixtures_owed` is required rather than defaulted, so an entry written before
    coverage accounting existed fails validation and is dropped. It cannot say whether
    it measured the whole corpus, and a record that cannot say that must degrade to
    unmeasured — never carry its old rates forward as a pass."""
    legacy = entry().model_dump(mode="json")
    legacy["metrics"].pop("fixtures_owed")
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({audition.cache_key("p/m", Lens.EVIDENCE): legacy}))
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

    def __init__(self, respond, critic_repair_retries=0):
        self.respond = respond
        self.prompts: list[tuple[str, str]] = []
        self.budgets = Budgets(critic_repair_retries=critic_repair_retries)

    def structured(self, alias, system, user, schema, max_tokens=0, validate=None,
                   repair_retries=None):
        def produce(attempt_user):
            self.prompts.append((system, attempt_user))
            return CritiqueOutput(issues=self.respond(alias, attempt_user))

        # The audition harness must exercise the *production* validation path, which now
        # runs inside the call — a stub that skipped it would grade a critic on issues a
        # real run would have rejected.
        return structured_with_repair(alias, user, produce, validate, repair_retries)


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
    assert m.fixtures_owed == len(fixtures.for_lens(Lens.EVIDENCE))
    assert m.uncovered_fixtures == (), "a graded zero is a measured miss, not a gap"
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
        budgets = Budgets(critic_repair_retries=0)

        def structured(self, alias, system, user, schema, max_tokens=0, validate=None,
                       repair_retries=None):
            # An out-of-scope category fails the lens closed in triage — and keeps
            # failing it, because no repair can turn an evidence category into a logic
            # one. `structured_with_repair` is what runs that validation, exactly as the
            # real client does.
            return structured_with_repair(
                alias,
                user,
                lambda _u: CritiqueOutput(issues=[issue(Category.UNCITED_CLAIM, 1, 1)]),
                validate,
                repair_retries,
            )

    m = audition.run_assignment(Broken(), slot, fixtures, repetitions=1)
    assert m.schema_failures == m.calls
    assert m.planted_total == 0
    assert m.control_runs == 0
    judgement = audition.judge(m, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    # The reported *cause* stays mechanical, ahead of the coverage gate this model also
    # trips: an operator told "never graded 6 fixtures" would go looking for a judgement
    # problem, when the fix is a prompt or an output mode.
    assert "schema" in judgement.reasons[0]


def test_all_repetitions_failing_one_fixture_leaves_that_fixture_uncovered():
    """The attack the coverage gate closes, end to end and offline.

    A model that deterministically breaks on one fixture — that artifact reliably drives
    it out of schema — fails 3 of 15 evidence calls, which the strict `>` schema gate
    admits at exactly 20%. Before coverage accounting, the fixture then contributed
    nothing to any denominator: sensitivity was computed as though it did not exist.

    The evidence lens now owes 6 fixtures, not 5 (D-category-coverage planted
    `misrepresented-source-01`), so one fixture failing every repetition no longer lands
    the rate exactly on `max_schema_failure_rate` by itself (1/6, not 1/5). To keep
    exercising the boundary the gate is calibrated against — "admitted at exactly the
    threshold, not merely under it" — a second, otherwise-clean fixture is made to fail
    schema validation on exactly one of its calls too. `repetitions=5` is chosen so the
    arithmetic is exact: 5 (the fully-failed fixture) + 1 (the single flake) = 6 failing
    calls out of 6 fixtures x 5 repetitions = 30, i.e. 20% on the nose. The flaky fixture
    still has four gradable calls, so it stays covered; only the target is uncovered.
    """
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.EVIDENCE, position=0)
    owed = fixtures.for_lens(Lens.EVIDENCE)
    target = "one-sided-sourcing-01"
    target_question = next(f for f in owed if f.id == target).question
    flaky = next(f for f in owed if f.id != target and not f.is_control)
    flaky_question = flaky.question
    repetitions = 5
    flaky_calls_seen = {"n": 0}

    def respond(alias, user):
        if target_question in user:
            # Out of scope for `evidence`, so triage fails the lens closed and keeps
            # failing it — no repair can turn a logic category into an evidence one.
            return [issue(Category.INVALID_INFERENCE, 1, 1)]
        if flaky_question in user:
            flaky_calls_seen["n"] += 1
            if flaky_calls_seen["n"] == 1:
                # One out-of-scope call, then clean — a single flake, not a break.
                return [issue(Category.INVALID_INFERENCE, 1, 1)]
            return []
        return []

    m = audition.run_assignment(ScriptedClient(respond), slot, fixtures, repetitions=repetitions)
    total_calls = len(owed) * repetitions
    assert m.calls == total_calls == 30
    assert m.schema_failures == repetitions + 1 == 6
    assert m.schema_failure_rate == pytest.approx(THRESHOLDS.max_schema_failure_rate)
    assert m.uncovered_fixtures == (target,)
    assert m.fixtures_covered == len(owed) - 1

    judgement = audition.judge(m, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert target in judgement.reasons[0]


def test_audition_warns_by_default_and_has_no_inert_enabled_flag():
    """Two properties, both load-bearing.

    Enforcement is off by default (D-critic-audition). And there is no `enabled` flag: it existed,
    gated nothing — `ra audition` measures, everything else only reads the cache — and
    a config knob that cannot change behaviour reads as a safety control while being
    inert. Re-adding one should mean re-arguing that.
    """
    assert AuditionConfig().enforce is False
    assert "enabled" not in AuditionConfig.model_fields


# --------------------------------------------------------- the enforcement gate


def unfit_cache(path: Path, identity: str, lens: Lens, cfg: AuditionConfig) -> None:
    """Write a cache the gate will actually accept: real corpus and prompt hashes, the
    configured repetition count, recorded now. Anything less and the entry is discarded
    as not-about-this-harness and the gate passes for the wrong reason."""
    silent = metrics(
        identity=identity, lens=lens, planted_total=6, obvious_total=6,
        control_runs=4, control_clean_runs=4,
    )
    assert audition.judge(silent, cfg.thresholds).verdict is audition.Verdict.UNFIT
    audition.save_cache(
        path,
        {
            audition.cache_key(identity, lens): audition.CacheEntry(
                metrics=silent,
                corpus_hash=audition.load_fixtures().corpus_hash,
                prompt_hash=audition.prompt_hash(),
                repetitions=cfg.repetitions,
                recorded_at=time.time(),
            )
        },
    )


def test_enforce_off_lets_an_unfit_critic_through(tmp_path):
    """The shipped posture: a loud warning, never a block."""
    cfg = AuditionConfig(cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    audition.enforce_fitness(cfg, roster(), IDENTITIES)  # does not raise


def test_enforce_on_refuses_to_start_with_an_unfit_assigned_critic(tmp_path):
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    with pytest.raises(ConfigError) as exc:
        audition.enforce_fitness(cfg, roster(), IDENTITIES)
    assert "c_good" in str(exc.value) and "logic" in str(exc.value)


def test_enforce_ignores_a_verdict_about_a_model_no_longer_rostered(tmp_path):
    """Swapping the unfit model out is the fix, and it must take effect immediately —
    the cache still holds its verdict, but it staffs nothing."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/dropped", Lens.LOGIC, cfg)
    audition.enforce_fitness(cfg, roster(), IDENTITIES)  # does not raise


def test_enforce_does_not_block_on_stale_or_unmeasured_verdicts(tmp_path):
    """Absence of evidence is not evidence of incapacity. Blocking here would couple
    every run to a cache only a paid, rate-limited proxy can refill."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    audition.enforce_fitness(cfg, roster(), IDENTITIES)  # empty cache: does not raise

    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    later = time.time() + (cfg.max_age_days + 1) * 86400
    audition.enforce_fitness(cfg, roster(), IDENTITIES, now=later)  # stale: does not raise


def test_the_gate_takes_no_client_and_so_can_never_spend(tmp_path):
    """`test_grader_needs_no_client` in spirit, for the startup path. The gate runs on
    every `ra run` and every web boot; if it ever grew a client it would quietly bill an
    audition per run, and a keyless checkout would stop booting."""
    import inspect

    for fn in (audition.enforce_fitness, audition.cached_judgements):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"client", "llm"}, f"{fn.__name__} gained a call path"
