"""Taxonomy totality: every category is fully wired, or an unguarded lookup blows up.

`clamp_to_floor` and `prompts.critic_user` both do bare dict lookups by category.
A category added to the enum but not to `SEVERITY_FLOOR` / `_CATEGORY_MEANING` /
`LENS_CATEGORIES` fails at runtime, mid-run, on the first critic that raises it.
These tests move that failure to CI.
"""

from __future__ import annotations

from reasonable_answer.prompts import _CATEGORY_ANCHOR, _CATEGORY_MEANING, critic_user
from reasonable_answer.taxonomy import (
    LENS_CATEGORIES,
    LENSES,
    SEVERITY_FLOOR,
    Category,
    Lens,
    Severity,
)

#: The categories whose defect is an absence or a property of arrangement, so no span of
#: "the offending text" exists and `claim_span` must anchor to present text instead (D-absence-anchor).
ABSENCE_CATEGORIES = (
    Category.INCOMPLETE_ANSWER,
    Category.OMITTED_COUNTERARGUMENT,
    Category.UNEXAMINED_PRESUPPOSITION,
    Category.UNCLEAR_STRUCTURE,
)


def test_every_category_has_a_severity_floor():
    assert set(SEVERITY_FLOOR) == set(Category)


def test_every_category_has_a_prompt_meaning():
    assert set(_CATEGORY_MEANING) == set(Category)


def test_every_category_has_a_claim_span_anchor():
    assert set(_CATEGORY_ANCHOR) == set(Category)


def test_every_category_belongs_to_a_lens():
    reachable = {c for cats in LENS_CATEGORIES.values() for c in cats}
    assert reachable == set(Category)


def test_non_stylistic_categories_belong_to_exactly_one_lens():
    for category in Category:
        if category is Category.STYLISTIC:
            continue
        owners = [lens for lens in LENSES if category in LENS_CATEGORIES[lens]]
        assert len(owners) == 1, f"{category.value} owned by {owners}"


def test_bias_floors_match_bias_md():
    # docs/bias.md §5 is normative for these three values (D-social-bias).
    assert SEVERITY_FLOOR[Category.ONE_SIDED_SOURCING] is Severity.MAJOR
    assert SEVERITY_FLOOR[Category.LOADED_LANGUAGE] is Severity.MINOR
    assert SEVERITY_FLOOR[Category.UNEXAMINED_PRESUPPOSITION] is Severity.MAJOR


def test_bias_categories_reach_their_lens_prompt():
    expected = {
        Lens.EVIDENCE: Category.ONE_SIDED_SOURCING,
        Lens.LOGIC: Category.LOADED_LANGUAGE,
        Lens.COMPLETENESS: Category.UNEXAMINED_PRESUPPOSITION,
    }
    for lens, category in expected.items():
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        assert category.value in prompt
    # ...and never the other lenses' prompts (scope stays closed).
    assert Category.ONE_SIDED_SOURCING.value not in critic_user(Lens.LOGIC, "q", "# r\n\nbody\n")


def test_claim_span_anchor_reaches_each_lens_prompt_and_only_its_own():
    """A lens is told what to anchor for every category it may raise, and for none it
    may not — the anchors follow the same closed scope as the meanings table (D-absence-anchor)."""
    for lens in LENSES:
        prompt = critic_user(lens, "q", "# r\n\nbody\n")
        for category in Category:
            anchor = _CATEGORY_ANCHOR[category]
            if category in LENS_CATEGORIES[lens]:
                assert anchor in prompt, f"{lens.value} prompt omits {category.value} anchor"
            else:
                assert anchor not in prompt, f"{lens.value} prompt leaks {category.value} anchor"


def test_absence_categories_anchor_to_present_text():
    """The gap this closes: a defect of absence has no span of the missing thing, so a
    critic told only "quote the offending text" quotes what is *not* in the paragraph,
    fails `_require_quote` through the whole repair budget, and fails the lens closed.

    Four of five lens failures in a 48h production window were exactly this, all on
    completeness, across two critic models. So each absence category must name the
    present text it anchors to, and the general rule must say so in the prompt body."""
    prompt = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")
    assert "the report does NOT say" in prompt
    assert "Never quote or compose the missing material" in prompt
    for category in ABSENCE_CATEGORIES:
        assert category in LENS_CATEGORIES[Lens.COMPLETENESS]
        assert _CATEGORY_ANCHOR[category] in prompt
    # The three whose defect is missing *content* must redirect that content to a field
    # that is not span-validated, or the advice is "drop the issue" by implication.
    assert "`rationale`" in _CATEGORY_ANCHOR[Category.INCOMPLETE_ANSWER]
    assert "`instruction`" in _CATEGORY_ANCHOR[Category.OMITTED_COUNTERARGUMENT]
    assert "`rationale`" in _CATEGORY_ANCHOR[Category.UNEXAMINED_PRESUPPOSITION]


def test_completeness_scope_covers_literal_obligations_and_rejects_easy_substitutes():
    prompt = critic_user(Lens.COMPLETENESS, "q", "# r\n\nbody\n")
    assert "every explicit, material part of the question" in prompt
    assert "answers an adjacent question in its place" in prompt
    assert "does not challenge a load-bearing conclusion" in prompt
    assert "Do not invent an unstated goal" in prompt
