"""Taxonomy totality: every category is fully wired, or an unguarded lookup blows up.

`clamp_to_floor` and `prompts.critic_user` both do bare dict lookups by category.
A category added to the enum but not to `SEVERITY_FLOOR` / `_CATEGORY_MEANING` /
`LENS_CATEGORIES` fails at runtime, mid-run, on the first critic that raises it.
These tests move that failure to CI.
"""

from __future__ import annotations

from reasonable_answer.prompts import _CATEGORY_MEANING, critic_user
from reasonable_answer.taxonomy import (
    LENS_CATEGORIES,
    LENSES,
    SEVERITY_FLOOR,
    Category,
    Lens,
    Severity,
)


def test_every_category_has_a_severity_floor():
    assert set(SEVERITY_FLOOR) == set(Category)


def test_every_category_has_a_prompt_meaning():
    assert set(_CATEGORY_MEANING) == set(Category)


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
    # docs/bias.md §5 is normative for these three values (D24).
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
