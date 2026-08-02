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
    ReviewConfig,
    Roster,
)
from reasonable_answer.schemas import CritiqueOutput, LensResult, RawIssue, StructuralRef
from reasonable_answer.taxonomy import LENS_CATEGORIES, Category, Lens, Severity
from reasonable_answer.triage import clean_records

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

    # `control_material_rate` is a mean over `controls * repetitions` runs and is
    # compared against a threshold of 1.0. With two controls at the shipped
    # `repetitions: 3`, one residual soundness flaw in one control moves that mean by
    # 0.5 — half the distance to the unfit line, which is how the pre-D-control-soundness
    # corpus mis-graded every evidence critic. Four bounds one control's leverage at
    # 0.25; the shipped corpus carries six (D-fixture-report-shape).
    assert len(controls) >= 4, (
        f"{len(controls)} controls: one bad control would move `control_material_rate` "
        f"by {1 / len(controls):.2f} against a threshold of 1.0"
    )

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


def test_every_lens_has_an_obvious_tier_fixture():
    """D-obvious-per-lens. Both fail-closed sensitivity gates in `judge` count planted
    defects on `tier: obvious` fixtures only. A lens whose whole planted set is
    `moderate` has `obvious_total == 0`, so both gates are skipped and a critic that
    returns nothing on every call grades MARGINAL — which `enforce_fitness` does not
    block. The completeness lens shipped in exactly that state.
    """
    fixtures = audition.load_fixtures(CORPUS)
    planted = [f for f in fixtures.fixtures if not f.is_control]
    for lens in Lens:
        mine = [f for f in planted if f.lens is lens]
        assert any(f.tier is audition.Tier.OBVIOUS and f.defects for f in mine), (
            f"lens {lens.value} has no tier: obvious planted fixture — both fail-closed "
            f"gates in judge() are dead for it and a silent critic grades marginal"
        )


def test_every_lens_has_a_locus_anchored_planted_defect():
    """D-obvious-per-lens, the other half. `anywhere: true` skips the locus window
    entirely, so a lens whose every planted defect sets it measures only "did the critic
    name a category from my lens", not "did it find the defect" — and a critic that
    reflexively raises one material issue of a fixed category on every artifact scores
    perfect sensitivity on that lens.
    """
    fixtures = audition.load_fixtures(CORPUS)
    for lens in Lens:
        anchored = [
            f.id
            for f in fixtures.fixtures
            if f.lens is lens and any(not d.anywhere for d in f.defects)
        ]
        assert anchored, (
            f"lens {lens.value} has no planted defect with a real locus — its sensitivity "
            f"score would not depend on where the critic looked"
        )


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


def test_planting_a_minor_floor_category_is_rejected(tmp_path):
    """D-minor-floor-fixtures. Every detection credit requires post-clamp materiality, so
    a category floored below `MATERIAL_FLOOR` can only be found by a critic that
    escalates past its own floor. `loaded_language` is minor on purpose (D-social-bias):
    a critic that reports it at exactly that severity is doing what the taxonomy asks
    and would score `strict = False`, `same_lens = False` — blind, on a metric that
    feeds MARGINAL. The fixture would be grading escalation, not detection."""
    d = tmp_path / "corpus" / "bad"
    d.mkdir(parents=True)
    (d / "artifact.md").write_text("# Q\n\nBody paragraph.\n")
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "lens": "logic",
                "question": "q",
                "defects": [
                    {"category": "loaded_language", "locus": {"section": 1, "paragraph": 1}}
                ],
            }
        )
    )
    with pytest.raises(audition.FixtureError, match="below the material floor"):
        audition.load_fixtures(tmp_path / "corpus")


def test_every_shipped_planted_category_is_detectable():
    """The corpus side of the same property: a planted defect no correct critic could
    be credited for is a fixture that measures nothing it claims to."""
    from reasonable_answer.taxonomy import SEVERITY_FLOOR, is_material

    for fixture in audition.load_fixtures(CORPUS).fixtures:
        for defect in fixture.defects:
            assert is_material(SEVERITY_FLOOR[defect.category]), (
                f"{fixture.id}: '{defect.category.value}' floors below material and can "
                f"never be credited by `grade`"
            )


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


#: Inline `[1]` marker and a numbered `## Sources` entry — the citation mechanics
#: `prompts.REPORT_SKELETON` actually mandates (D-fixture-report-shape). The corpus used
#: author-year citations with bulleted sources until then, which is a form production
#: writers are instructed never to emit.
_INTEXT_CITE = re.compile(r"\[(\d{1,2})\]")
_SOURCE_ENTRY = re.compile(r"^(\d{1,2})\.\s+\S")

#: A contested question sourced from one or two places earns `one_sided_sourcing`,
#: which floors to `major` — a finding the critic would be right to raise, scored
#: against it as invention. Four is the floor, not a target.
MIN_CONTROL_SOURCES = 4


def _controls():
    return [f for f in audition.load_fixtures(CORPUS).fixtures if f.is_control]


def _split_sources(artifact: str) -> tuple[str, list[str]]:
    body, _, sources = artifact.partition("\n## Sources")
    return body, [ln for ln in sources.splitlines() if _SOURCE_ENTRY.match(ln)]


@pytest.mark.parametrize("fixture_id", [f.id for f in _controls()])
def test_control_citations_resolve_in_both_directions(fixture_id):
    """D-control-soundness, the mechanically checkable part.

    Catches a dangling in-text citation and an orphan Sources entry. It does NOT catch
    the failure that motivated the decision — a claim with no citation marker at all,
    which no regex can distinguish from prose that needs none. That half rests on the
    soundness contract in each manifest and on review.

    Parametrized over *every* control rather than a hand-written pair, so a control
    added later cannot join the corpus without its citations resolving
    (D-fixture-report-shape).
    """
    fixture = next(f for f in _controls() if f.id == fixture_id)
    body, entries = _split_sources(fixture.artifact)
    assert entries, f"{fixture_id}: no numbered '## Sources' entries"

    # Numbers, not surnames: a numbered reference list is the identity, so the same
    # source listed twice under two numbers is two citable entries and would be caught
    # below as two entries the body must cite separately.
    numbers = [int(_SOURCE_ENTRY.match(e).group(1)) for e in entries]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"{fixture_id}: Sources entries are numbered {numbers}, not 1..n — an inline "
        f"[n] marker would resolve to the wrong entry or to none"
    )
    assert len(numbers) >= MIN_CONTROL_SOURCES, (
        f"{fixture_id}: {len(numbers)} sources — thin enough to earn `one_sided_sourcing`"
    )

    cited = {int(n) for n in _INTEXT_CITE.findall(body)}
    for n in sorted(cited):
        assert n in numbers, f"{fixture_id}: in-text [{n}] resolves to no Sources entry"
    for n in numbers:
        assert n in cited, f"{fixture_id}: Sources entry {n} is never cited"


#: Planted fixture -> the control that is the same artifact with the defect removed
#: (D-conceptual-conflation). Only the pairs whose minimality is claimed in their manifests
#: are listed: D-fixture-report-shape shipped four bases without asserting a
#: one-paragraph mutation, and retrofitting that claim onto them is not this decision's
#: work.
MINIMAL_PAIRS = (
    ("conceptual-conflation-01", "control-base-paid-leave-01"),
    ("overstated-claim-03", "control-base-open-source-01"),
)


@pytest.mark.parametrize(("planted_id", "control_id"), MINIMAL_PAIRS)
def test_a_paired_control_differs_from_its_plant_in_one_paragraph(planted_id, control_id):
    """D-conceptual-conflation, the confound half of D-fixture-report-shape applied per pair.

    A planted fixture and its control are graded on disjoint metrics — sensitivity on one,
    noise on the other — so any difference between them other than the defect is a feature
    a critic can score on without reading the argument. Both new categories are
    especially exposed to this: "was there a conflation" and "did this report about paid
    leave bother me" produce the same finding unless the two artifacts are otherwise
    identical. Asserting the mutation is exactly one paragraph is what makes the pair
    measure the defect.
    """
    from reasonable_answer import report as report_mod

    fixtures = {f.id: f for f in audition.load_fixtures(CORPUS).fixtures}
    planted, control = fixtures[planted_id], fixtures[control_id]
    assert planted.question == control.question, "a pair must answer the same question"
    assert control.is_control and not planted.is_control

    left = [p.text for p in report_mod.parse(planted.artifact).paragraphs]
    right = [p.text for p in report_mod.parse(control.artifact).paragraphs]
    assert len(left) == len(right), (
        f"{planted_id} has {len(left)} paragraphs against {len(right)} in {control_id}: "
        f"paragraph count is itself a feature separating the two classes"
    )
    differing = [i for i, (a, b) in enumerate(zip(left, right, strict=True)) if a != b]
    assert len(differing) == 1, (
        f"{planted_id} and {control_id} differ in {len(differing)} paragraphs, not one"
    )

    # ...and the one that differs is the one the manifest plants the defect at, or its
    # neighbour: a pair that mutates a paragraph the grader never looks at measures nothing.
    (index,) = differing
    changed = report_mod.parse(planted.artifact).paragraphs[index]
    assert any(
        audition._locus_matches(d.locus, StructuralRef(section=changed.section, paragraph=changed.paragraph))
        for d in planted.defects
    ), f"{planted_id}: the mutated paragraph S{changed.section}.P{changed.paragraph} is not a planted locus"


def _headings(artifact: str) -> list[str]:
    return [ln[3:].strip() for ln in artifact.splitlines() if ln.startswith("## ")]


@pytest.mark.parametrize("fixture_id", [f.id for f in audition.load_fixtures(CORPUS).fixtures])
def test_every_artifact_has_the_shape_production_writers_are_told_to_emit(fixture_id):
    """D-fixture-report-shape.

    The audition measures critics with the production critic prompt, so its value rests
    on the fixtures looking like what a critic sees in a run. `prompts.REPORT_SKELETON`
    is what the writer is held to; a corpus that violates it audits critics on a document
    class no production writer may emit, shifting locus distribution, the organization
    cues the completeness lens judges, and citation mechanics.
    """
    fixture = next(f for f in audition.load_fixtures(CORPUS).fixtures if f.id == fixture_id)
    artifact = fixture.artifact

    assert not re.search(r"^# \S", artifact, re.MULTILINE), (
        f"{fixture_id}: has a top-level '#' title — the report is the body only"
    )
    assert artifact.startswith("## Conclusion\n"), (
        f"{fixture_id}: must open with '## Conclusion', nothing before it"
    )
    last_line = artifact.rstrip().splitlines()[-1]
    assert _SOURCE_ENTRY.match(last_line), (
        f"{fixture_id}: must end on a numbered '## Sources' entry, not {last_line[:40]!r}"
    )

    headings = _headings(artifact)
    assert headings[0] == "Conclusion", f"{fixture_id}: first section is {headings[0]!r}"
    assert headings[-1] == "Sources", f"{fixture_id}: last section is {headings[-1]!r}"
    for required in ("Key findings", "The strongest counterargument"):
        assert required in headings, f"{fixture_id}: no '## {required}' section"
    assert _INTEXT_CITE.search(artifact), f"{fixture_id}: no inline [n] citations"


#: How far the longest artifact may exceed the shortest. Corpus class was readable off
#: length alone before D-fixture-report-shape — controls ran 652-656 words against
#: 239-357 for the planted fixtures, a 2.7x spread — so a model could score by being
#: conservative on long reports and aggressive on short ones without detecting anything.
MAX_LENGTH_SPREAD = 1.5


def test_corpus_class_is_not_readable_off_length():
    """D-fixture-report-shape, the confound half.

    Sensitivity and noise are measured on disjoint fixture sets, so any feature that
    separates those sets is a shortcut past the measurement. Length is the easiest one
    to acquire accidentally and the easiest to check.
    """
    fixtures = audition.load_fixtures(CORPUS).fixtures
    lengths = {f.id: len(f.artifact.split()) for f in fixtures}
    control = sorted(lengths[f.id] for f in fixtures if f.is_control)
    planted = sorted(lengths[f.id] for f in fixtures if not f.is_control)
    assert control and planted

    longest, shortest = max(lengths.values()), min(lengths.values())
    assert longest <= MAX_LENGTH_SPREAD * shortest, (
        f"corpus spans {shortest}-{longest} words ({longest / shortest:.2f}x): "
        f"{min(lengths, key=lengths.get)} .. {max(lengths, key=lengths.get)}"
    )

    # Overlapping ranges are necessary but weak — [500, 900] and [899, 901] overlap.
    # Requiring each class's median to sit inside the other's range rules out the two
    # classes merely touching at one end.
    assert control[len(control) // 2] <= planted[-1] and control[len(control) // 2] >= planted[0], (
        f"control median sits outside the planted range {planted[0]}-{planted[-1]}"
    )
    assert planted[len(planted) // 2] <= control[-1] and planted[len(planted) // 2] >= control[0], (
        f"planted median sits outside the control range {control[0]}-{control[-1]}"
    )


def test_corpus_class_is_not_readable_off_source_count():
    """D-fixture-report-shape, the same confound one step along.

    Controls carried six dense sources against three for a typical planted fixture, and
    the check has to be made per lens rather than corpus-wide: a lens only ever sees its
    own planted fixtures plus the controls, so a gap that closes in aggregate can stay
    wide open inside `for_lens`.
    """
    fixtures = audition.load_fixtures(CORPUS)
    counts = {f.id: len(_split_sources(f.artifact)[1]) for f in fixtures.fixtures}
    control = sorted(counts[f.id] for f in fixtures.fixtures if f.is_control)

    for lens in Lens:
        planted = sorted(counts[f.id] for f in fixtures.for_lens(lens) if not f.is_control)
        assert planted, f"lens {lens.value} has no planted fixture"
        assert set(planted) == set(control), (
            f"on {lens.value}, planted fixtures have source counts {sorted(set(planted))} "
            f"against {sorted(set(control))} for controls: a class-exclusive count would "
            f"be a usable proxy for whether the artifact has a defect"
        )


def test_incomplete_answer_fixture_changes_the_question_not_the_report():
    """D-answer-obligations: isolate answer coverage from every artifact-level cue."""
    fixtures = {f.id: f for f in audition.load_fixtures(CORPUS).fixtures}
    control = fixtures["control-base-dust-bowl-01"]
    planted = fixtures["incomplete-answer-01"]

    assert planted.artifact == control.artifact
    assert planted.question != control.question
    assert "unionization" in planted.question
    assert Category.INCOMPLETE_ANSWER in {defect.category for defect in planted.defects}


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


@pytest.mark.parametrize("severity", [Severity.MAJOR, Severity.BLOCKING])
def test_escalated_stylistic_issue_does_not_count_as_a_detection(severity):
    """Escalation is legal — `validate_issue` never checks severity — and production
    discards `stylistic` anyway. A critic that filed only a `major` nitpick on the
    planted paragraph would have let the defect through, so the grader must not credit
    it (D-audition-stylistic-parity)."""
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
        fixture, result(Lens.COMPLETENESS, issue(Category.STYLISTIC, 2, 1, severity))
    )
    assert found[0].strict is False
    assert found[0].same_lens is False


def _logic_fixture(category: Category = Category.INVALID_INFERENCE) -> audition.Fixture:
    return audition.Fixture(
        id="f",
        lens=Lens.LOGIC,
        question="q",
        artifact="x",
        defects=(
            audition.PlantedDefect(category=category, locus=StructuralRef(section=2, paragraph=1)),
        ),
    )


@pytest.mark.parametrize(
    ("reported", "agrees"),
    [
        (Severity.BLOCKING, True),   # legal escalation: RC-005 permits exactly this
        (Severity.MAJOR, True),      # at the floor
        (Severity.MINOR, False),     # the clamp had to lift it
    ],
)
def test_severity_agreement_counts_escalation_as_agreement(reported, agrees):
    """`invalid_inference` floors at major. A critic proposing `blocking` is exercising
    the one direction the clamp allows, and scoring that as disagreement made the metric
    punish the behaviour the taxonomy invites. Only a proposal triage has to lift
    disagrees. D-minor-floor-fixtures."""
    found = audition.grade(
        _logic_fixture(),
        result(Lens.LOGIC, issue(Category.INVALID_INFERENCE, 2, 1, reported)),
    )
    assert found[0].strict is True, "under-rating a major-floor category is still a detection"
    assert found[0].severity_agrees is agrees


@pytest.mark.parametrize(
    ("planted", "reported", "severity"),
    [
        # The D-minor-floor-fixtures case: a doctrine-compliant minor report used to
        # increment the numerator while contributing nothing to the denominator.
        (Category.OVERSTATED_CLAIM, Category.LOADED_LANGUAGE, Severity.MINOR),
        (Category.OVERSTATED_CLAIM, Category.OVERSTATED_CLAIM, Severity.MINOR),
        (Category.OVERSTATED_CLAIM, Category.OVERSTATED_CLAIM, Severity.BLOCKING),
        (Category.OVERSTATED_CLAIM, Category.CONTRADICTED_CLAIM, Severity.BLOCKING),
        (Category.OVERSTATED_CLAIM, Category.STYLISTIC, Severity.MINOR),
        (Category.CONTRADICTED_CLAIM, Category.CONTRADICTED_CLAIM, Severity.MINOR),
    ],
)
def test_severity_agreement_never_counts_what_strict_did_not(planted, reported, severity):
    """The numerator must sit inside the denominator, or `severity_agreement` is not a
    rate at all — it exceeded 1.0 whenever a critic reported a minor-floor category
    at its floor."""
    found = audition.grade(
        _logic_fixture(planted), result(Lens.LOGIC, issue(reported, 2, 1, severity))
    )
    assert not (found[0].severity_agrees and not found[0].strict)


def test_severity_agreement_is_a_rate_over_a_whole_audition():
    """The aggregate form of the property, through `run_assignment` rather than `grade`:
    a mixed critic — one escalation, one under-rating, one minor report of a minor-floor
    category — must still land in [0, 1]."""
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.LOGIC, position=0)

    # Spans are verbatim quotes from the two artifacts, because `require_verbatim_spans`
    # defaults on exactly as in a run.
    overreach = "will reduce fatalities in any city that builds them"
    framing = "settle the question decisively"

    def respond(alias, user):
        if overreach in user:
            # Escalation on a major-floor category: found, and rated above the floor.
            # Locus (5, 2) is `invalid-inference-01`'s actual ground-truth locus
            # (D-fixture-report-shape moved it there when the fixture was reshaped to
            # REPORT_SKELETON form; see the manifest).
            return [
                issue(
                    Category.INVALID_INFERENCE, 5, 2, Severity.BLOCKING, claim_span=overreach
                )
            ]
        if framing in user:
            # The doctrine-compliant minor reading, at the fixture's actual ground-truth
            # locus (`overstated-claim-02`, S4.P1 — the fixture moved to REPORT_SKELETON
            # shape and its `-02` name under D-minor-floor-fixtures). It is not a
            # detection, and before D-minor-floor-fixtures it incremented the numerator
            # anyway.
            return [
                issue(Category.LOADED_LANGUAGE, 4, 1, Severity.MINOR, claim_span=framing)
            ]
        return []

    m = audition.run_assignment(ScriptedClient(respond), slot, fixtures, repetitions=1)
    assert m.strict_hits == 1, "the escalated invalid_inference must count as found"
    assert m.severity_agreements <= m.strict_hits
    assert 0.0 <= m.severity_agreement <= 1.0


def test_material_count_applies_the_severity_floor():
    """A critic under-rating a blocking category still raised a material issue —
    triage would clamp it up, so the noise measure must too."""
    noisy = result(
        Lens.EVIDENCE,
        issue(Category.FABRICATED_CITATION, 1, 1, Severity.MINOR),
        issue(Category.STYLISTIC, 1, 1, Severity.MINOR),
    )
    assert audition.material_issue_count(noisy) == 1


@pytest.mark.parametrize("severity", [Severity.MAJOR, Severity.BLOCKING])
def test_escalated_stylistic_issue_on_a_control_is_not_noise(severity):
    """The unfit gate this feeds says such a critic would make "runs stagnate rather
    than converge". A stylistic finding cannot stagnate a run — `tally` ignores it and
    it never withholds a clean record — so it is not noise the gate may count."""
    noisy = result(Lens.LOGIC, issue(Category.STYLISTIC, 1, 1, severity))
    assert audition.material_issue_count(noisy) == 0


@pytest.mark.parametrize("lens", list(Lens))
@pytest.mark.parametrize("severity", list(Severity))
def test_grader_materiality_agrees_with_triage_for_every_category(lens, severity):
    """The anti-drift test. For every (category, severity) a critic could legally
    report, the grader counts an issue exactly when triage withholds the lens's clean
    record for it. Both read one predicate; this pins that they keep doing so."""
    for category in LENS_CATEGORIES[lens]:
        one = result(lens, issue(category, 1, 1, severity))
        graded_material = audition.material_issue_count(one) == 1
        blocks_clearance = not clean_records([one])
        assert graded_material is blocks_clearance, (category, severity)


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


def test_a_critic_that_is_never_clean_on_a_sound_report_is_unfit():
    """D-obvious-per-lens. The cheapest degenerate strategy: raise exactly one material
    issue of the right category on every artifact. It scores perfect sensitivity, and
    its `control_material_rate` lands on exactly 1.00 — which is not *greater than* the
    1.0 default, so the noise gate let it through and the verdict was MARGINAL, which
    `enforce_fitness` does not block.
    """
    always_fires = metrics(
        planted_total=4,
        strict_hits=4,
        same_lens_hits=4,
        obvious_total=2,
        obvious_hits=2,
        control_runs=4,
        control_material_issues=4,
        control_clean_runs=0,
    )
    assert always_fires.control_material_rate == 1.0
    assert always_fires.control_material_rate <= THRESHOLDS.max_control_material_rate
    judgement = audition.judge(always_fires, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.UNFIT
    assert any("clean" in r for r in judgement.reasons)


def test_never_clean_is_unfit_under_every_threshold_setting():
    """The mirror of `test_silent_critic_is_unfit_under_every_threshold_setting`: a
    critic that never lets a sound report through blocks convergence whatever the
    calibration says, so the gate is hardcoded rather than tunable."""
    always_fires = metrics(
        planted_total=4,
        strict_hits=4,
        same_lens_hits=4,
        obvious_total=2,
        obvious_hits=2,
        control_runs=4,
        control_material_issues=4,
        control_clean_runs=0,
    )
    permissive = AuditionThresholds(
        min_obvious_sensitivity=0.0,
        warn_lens_sensitivity=0.0,
        max_control_material_rate=99.0,
        warn_control_material_rate=99.0,
        max_schema_failure_rate=1.0,
    )
    assert audition.judge(always_fires, permissive).verdict is audition.Verdict.UNFIT


def test_an_occasional_false_positive_is_not_the_never_clean_gate():
    """The gate is about *never*, not about noise in degrees — a critic clean on some
    sound reports and not others is what `warn_control_material_rate` is for."""
    occasionally_noisy = metrics(
        planted_total=4,
        strict_hits=4,
        same_lens_hits=4,
        obvious_total=2,
        obvious_hits=2,
        control_runs=4,
        control_material_issues=2,
        control_clean_runs=2,
    )
    judgement = audition.judge(occasionally_noisy, THRESHOLDS)
    assert judgement.verdict is audition.Verdict.MARGINAL
    assert not any("clean" in r for r in judgement.reasons)


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
    """Position 3 is outside the default review depth of 2, so it is unreachable on
    pass 1 and reached on the rule 8 top-up, where a false clean raises cleared_count
    to 2 and terminates the run `accepted`."""
    judgements = {
        ("p/weak", Lens.EVIDENCE): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(roster(), IDENTITIES, judgements)
    assert any("position 3" in w and "strong_met" in w for w in warnings)


def test_no_position_warning_for_the_head_of_the_pool():
    """Position 1 has always read every draft, so its own verdict is the whole story
    and a position warning would say nothing the verdict does not."""
    judgements = {
        ("p/good", Lens.LOGIC): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(roster(), IDENTITIES, judgements)
    assert not any("position" in w for w in warnings)


def test_a_front_loaded_weak_critic_is_flagged_as_reading_every_draft():
    """D-front-loaded-depth: at review depth 2, position 2 is no longer a rule-8
    formality — it reads every draft, so the warning has to say so rather than repeat
    the old 'unreachable on the first pass' line, which is now false."""
    judgements = {
        ("p/weak", Lens.LOGIC): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(
        roster(), IDENTITIES, judgements, ReviewConfig(depth=2)
    )
    assert any("position 2" in w and "EVERY draft" in w for w in warnings)
    assert not any("unreachable" in w for w in warnings)


def test_depth_one_puts_the_second_critic_back_on_the_rule_8_top_up():
    """The threshold is the deployment's own depth, not a constant."""
    judgements = {
        ("p/weak", Lens.LOGIC): audition.Judgement(audition.Verdict.UNFIT, ("silent",)),
    }
    warnings = audition.roster_warnings(
        roster(), IDENTITIES, judgements, ReviewConfig(depth=1)
    )
    assert any("position 2" in w and "rule 8 confirmation top-up" in w for w in warnings)


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
        rubric_hash="rubric",
        require_verbatim_spans=True,
        repetitions=3,
        recorded_at=time.time(),
    )
    return audition.CacheEntry(**{**base, **kwargs})


def matches_current(e: audition.CacheEntry, **overrides) -> bool:
    """`matches` against the entry's own dimensions, with named overrides."""
    args = dict(
        corpus_hash="corpus",
        prompt_hash="prompt",
        repetitions=3,
        rubric_hash="rubric",
        require_verbatim_spans=True,
    )
    args.update(overrides)
    return e.matches(
        args.pop("corpus_hash"), args.pop("prompt_hash"), args.pop("repetitions"), **args
    )


def test_cache_entry_is_invalidated_by_any_dimension_of_what_it_measured():
    """Corpus, prompts, repetitions, grading rubric and span-validation regime are all
    part of what a score means (D-audition-rubric-identity). A verdict carried across a
    change in any of them is a claim about a measurement that no longer exists."""
    e = entry()
    assert matches_current(e)
    assert not matches_current(e, corpus_hash="other-corpus")
    assert not matches_current(e, prompt_hash="other-prompt")
    assert not matches_current(e, repetitions=5)
    assert not matches_current(e, rubric_hash="other-rubric")
    assert not matches_current(e, require_verbatim_spans=False)


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


def test_a_pre_rubric_cache_file_reads_as_not_audited(tmp_path):
    """Backward compatibility, in the only direction that is safe. An entry written
    before `rubric_hash`/`require_verbatim_spans` existed cannot say which rules it was
    graded under, so it must degrade to *not audited* — never be read as a pass
    (D-audition-rubric-identity)."""
    path = tmp_path / "cache.json"
    old_shape = {
        "metrics": metrics().model_dump(mode="json"),
        "corpus_hash": "corpus",
        "prompt_hash": "prompt",
        "repetitions": 3,
        "recorded_at": time.time(),
    }
    path.write_text(json.dumps({audition.cache_key("p/m", Lens.LOGIC): old_shape}))
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


def test_prompt_hash_carries_the_source_mode(monkeypatch):
    """The hash covers the source-less surface only, so the mode has to be in the key.

    Without the tag, a sources-present audition mode would key its verdicts identically
    to the source-less ones measured today and inherit them wholesale — a claim about a
    measurement that was never taken (D-audition-source-mode).
    """
    before = audition.prompt_hash()
    monkeypatch.setattr(audition, "AUDITION_SOURCE_MODE", "sources:fixture-packet")
    assert audition.prompt_hash() != before


def test_rubric_hash_is_stable_across_calls():
    assert audition.rubric_hash() == audition.rubric_hash()


def test_rubric_hash_tracks_the_hand_bumped_version(monkeypatch):
    """The half that covers grading *code* — `grade`, `_is_material`, `_locus_matches`,
    `run_assignment`'s accounting — which nothing can hash for us."""
    before = audition.rubric_hash()
    monkeypatch.setattr(audition, "RUBRIC_VERSION", audition.RUBRIC_VERSION + 1)
    assert audition.rubric_hash() != before


def test_rubric_hash_tracks_the_grading_tables(monkeypatch):
    """The half that is hashed from the data, so it can never be forgotten."""
    from reasonable_answer.taxonomy import LENS_CATEGORIES, SEVERITY_FLOOR, SEVERITY_RANK

    before = audition.rubric_hash()
    monkeypatch.setattr(
        audition, "LOCUS_PARAGRAPH_TOLERANCE", audition.LOCUS_PARAGRAPH_TOLERANCE + 1
    )
    assert audition.rubric_hash() != before

    monkeypatch.undo()
    floors = dict(SEVERITY_FLOOR) | {Category.UNCITED_CLAIM: Severity.BLOCKING}
    monkeypatch.setattr(audition, "SEVERITY_FLOOR", floors)
    assert audition.rubric_hash() != before

    monkeypatch.undo()
    ranks = dict(SEVERITY_RANK) | {Severity.MINOR: 5}
    monkeypatch.setattr(audition, "SEVERITY_RANK", ranks)
    assert audition.rubric_hash() != before

    monkeypatch.undo()
    scopes = dict(LENS_CATEGORIES)
    scopes[Lens.LOGIC] = scopes[Lens.LOGIC] + (Category.UNCITED_CLAIM,)
    monkeypatch.setattr(audition, "LENS_CATEGORIES", scopes)
    assert audition.rubric_hash() != before


def test_rubric_hash_tracks_the_metrics_field_set(monkeypatch):
    """A `judge` gate reading a counter that older entries never collected would see 0
    and call it a measured zero. Hashing the field set makes that self-invalidating, so
    adding a metric needs no `RUBRIC_VERSION` bump to stay honest."""
    before = audition.rubric_hash()
    fields = dict(audition.Metrics.model_fields)
    fields["hallucinated_loci"] = fields["strict_hits"]
    monkeypatch.setattr(audition.Metrics, "model_fields", fields)
    assert audition.rubric_hash() != before


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
    # real quote from S5.P1 because `require_verbatim_spans` defaults on, exactly as
    # in a run — a loose quote fails the lens closed rather than scoring a detection.
    span = "Every credible study of the 2021-2023 period"

    def respond(alias, user):
        if span in user:
            return [issue(Category.UNCITED_CLAIM, 5, 1, claim_span=span)]
        return []

    m = audition.run_assignment(ScriptedClient(respond), slot, fixtures, repetitions=1)
    assert m.calls == len(fixtures.for_lens(Lens.EVIDENCE))
    assert m.schema_failures == 0
    assert m.fixtures_owed == len(fixtures.for_lens(Lens.EVIDENCE))
    assert m.uncovered_fixtures == (), "a graded zero is a measured miss, not a gap"
    assert m.strict_hits == 1
    assert m.control_runs == sum(1 for f in fixtures.fixtures if f.is_control)
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


def test_audition_measures_the_source_less_prompt_surface():
    """The verdict is a floor claim about the prompt with no fetched pages in it.

    Production runs `verify_sources` on (docs/deployment-profile.md), so its evidence
    critic can see fetched page text and a `misrepresented_source` sharpened into a
    checkable fact. Fixtures ship no source packet, so the harness exercises neither —
    a deliberate scope (D-audition-source-mode) rather than an oversight, and pinned
    here so it stays a property of the code and not just a claim in a document.
    """
    from reasonable_answer import report as report_mod
    from reasonable_answer.fetch import FetchedSource

    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.EVIDENCE, position=0)
    client = ScriptedClient(lambda alias, user: [])
    audition.run_assignment(client, slot, fixtures, repetitions=1)

    fixture = next(
        f for f in fixtures.for_lens(Lens.EVIDENCE) if f.id == "fabricated-citation-01"
    )
    rendered = report_mod.render_with_loci(fixture.artifact)
    seen = {user for _, user in client.prompts}
    assert prompts.critic_user(Lens.EVIDENCE, fixture.question, rendered, None) in seen
    with_page = prompts.critic_user(
        Lens.EVIDENCE,
        fixture.question,
        rendered,
        [FetchedSource(url="https://example.org/a", title="T", text="Body text.")],
    )
    assert with_page not in seen


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
    it out of schema — fails calls at exactly the strict `>` schema gate's threshold.
    Before coverage accounting, the fixture then contributed nothing to any denominator:
    sensitivity was computed as though it did not exist.

    One fixture failing every repetition has not landed the rate on
    `max_schema_failure_rate` by itself since the corpus outgrew five fixtures per lens.
    To keep exercising the boundary the gate is calibrated against — "admitted at exactly
    the threshold, not merely under it" — enough *other* fixtures are made to fail
    validation on exactly one of their calls to make up the difference. Each of those
    still has `repetitions - 1` gradable calls, so all of them stay covered; only the
    target is uncovered.

    The arithmetic is derived rather than hardcoded, because it has now been re-derived
    twice as the corpus grew (D-category-coverage and D-fixture-report-shape added planted
    fixtures and controls; D-conceptual-conflation added two more controls, which every
    lens owes). `repetitions=5` is the one choice that survives the next growth too: the
    threshold is 20%, so the wanted failure count is `owed * repetitions / 5`, which is a
    whole number for every corpus size when `repetitions` is a multiple of 5. The exact-
    rate assertion below is what would catch it if that ever stopped holding.
    """
    fixtures = audition.load_fixtures(CORPUS)
    slot = audition.Assignment(alias="a", identity="p/m", lens=Lens.EVIDENCE, position=0)
    owed = fixtures.for_lens(Lens.EVIDENCE)
    target = "one-sided-sourcing-01"
    target_question = next(f for f in owed if f.id == target).question
    repetitions = 5
    total_calls = len(owed) * repetitions
    wanted_failures = total_calls * THRESHOLDS.max_schema_failure_rate
    assert wanted_failures == int(wanted_failures), (
        f"{total_calls} calls cannot fail at exactly "
        f"{THRESHOLDS.max_schema_failure_rate:.0%} — the boundary would go untested"
    )
    # The target burns `repetitions` of the failure budget on its own; the rest is spread
    # one call each over other fixtures, taken in corpus order so the set is deterministic.
    flaky = [f for f in owed if f.id != target][: int(wanted_failures) - repetitions]
    assert 0 < len(flaky) < len(owed), "the flake budget must fit inside the corpus"
    flaky_questions = {f.question for f in flaky}
    flaky_calls_seen: dict[str, int] = {}

    def respond(alias, user):
        if target_question in user:
            # Out of scope for `evidence`, so triage fails the lens closed and keeps
            # failing it — no repair can turn a logic category into an evidence one.
            return [issue(Category.INVALID_INFERENCE, 1, 1)]
        for q in flaky_questions:
            if q in user:
                flaky_calls_seen[q] = flaky_calls_seen.get(q, 0) + 1
                if flaky_calls_seen[q] == 1:
                    # One out-of-scope call, then clean — a single flake, not a break.
                    return [issue(Category.INVALID_INFERENCE, 1, 1)]
                return []
        return []

    m = audition.run_assignment(ScriptedClient(respond), slot, fixtures, repetitions=repetitions)
    assert m.calls == total_calls
    assert m.schema_failures == repetitions + len(flaky) == wanted_failures
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


def unfit_cache(
    path: Path,
    identity: str,
    lens: Lens,
    cfg: AuditionConfig,
    require_verbatim_spans: bool = True,
) -> None:
    """Write a cache the gate will actually accept: real corpus, prompt and rubric
    hashes, the span-validation regime it was measured under, the configured repetition
    count, recorded now. Anything less and the entry is discarded as
    not-about-this-harness and the gate passes for the wrong reason."""
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
                rubric_hash=audition.rubric_hash(),
                require_verbatim_spans=require_verbatim_spans,
                repetitions=cfg.repetitions,
                recorded_at=time.time(),
            )
        },
    )


def test_enforce_off_lets_an_unfit_critic_through(tmp_path):
    """The shipped posture: a loud warning, never a block."""
    cfg = AuditionConfig(cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    audition.enforce_fitness(cfg, roster(), IDENTITIES, True)  # does not raise


def test_enforce_on_refuses_to_start_with_an_unfit_assigned_critic(tmp_path):
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    with pytest.raises(ConfigError) as exc:
        audition.enforce_fitness(cfg, roster(), IDENTITIES, True)
    assert "c_good" in str(exc.value) and "logic" in str(exc.value)


def test_enforce_ignores_a_verdict_about_a_model_no_longer_rostered(tmp_path):
    """Swapping the unfit model out is the fix, and it must take effect immediately —
    the cache still holds its verdict, but it staffs nothing."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/dropped", Lens.LOGIC, cfg)
    audition.enforce_fitness(cfg, roster(), IDENTITIES, True)  # does not raise


def test_enforce_does_not_block_on_stale_or_unmeasured_verdicts(tmp_path):
    """Absence of evidence is not evidence of incapacity. Blocking here would couple
    every run to a cache only a paid, rate-limited proxy can refill."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    audition.enforce_fitness(cfg, roster(), IDENTITIES, True)  # empty cache: no raise

    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    later = time.time() + (cfg.max_age_days + 1) * 86400
    # stale: does not raise
    audition.enforce_fitness(cfg, roster(), IDENTITIES, True, now=later)


def test_a_rubric_version_bump_drops_cached_verdicts_to_not_audited(tmp_path, monkeypatch):
    """D-audition-rubric-identity. The verdict below was produced by grading rules that
    no longer exist, so it must stop being authoritative the moment they change — and it
    must degrade to *not audited*, the same direction a corpus edit degrades in."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg)
    assert audition.cached_judgements(cfg, roster(), IDENTITIES, True)

    monkeypatch.setattr(audition, "RUBRIC_VERSION", audition.RUBRIC_VERSION + 1)
    assert audition.cached_judgements(cfg, roster(), IDENTITIES, True) == {}
    audition.enforce_fitness(cfg, roster(), IDENTITIES, True)  # does not raise


def test_flipping_require_verbatim_spans_drops_cached_verdicts_to_not_audited(tmp_path):
    """A loose quote fails the lens closed when spans are required, so the flag changes
    what a critic can score. A verdict measured under one regime says nothing about the
    other, in either direction."""
    cfg = AuditionConfig(enforce=True, cache_path=tmp_path / "c.json")
    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg, require_verbatim_spans=True)
    assert audition.cached_judgements(cfg, roster(), IDENTITIES, False) == {}
    audition.enforce_fitness(cfg, roster(), IDENTITIES, False)  # does not raise

    unfit_cache(cfg.cache_path, "p/good", Lens.LOGIC, cfg, require_verbatim_spans=False)
    assert audition.cached_judgements(cfg, roster(), IDENTITIES, True) == {}
    assert audition.cached_judgements(cfg, roster(), IDENTITIES, False)


def test_the_gate_takes_no_client_and_so_can_never_spend(tmp_path):
    """`test_grader_needs_no_client` in spirit, for the startup path. The gate runs on
    every `ra run` and every web boot; if it ever grew a client it would quietly bill an
    audition per run, and a keyless checkout would stop booting."""
    import inspect

    for fn in (audition.enforce_fitness, audition.cached_judgements):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"client", "llm"}, f"{fn.__name__} gained a call path"
