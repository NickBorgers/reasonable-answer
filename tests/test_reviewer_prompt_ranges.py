"""The reviewer prompts must cite the decision scheme actually in use.

They used to enumerate a numeric ID *range* (`D1`-`D43`) so the invariant and docs reviewers
could reject invented IDs — but a range is hand-written prose that drifted behind
``docs/decisions.md`` every time a decision was added (issue #69), and under subject slugs a
range is meaningless anyway: slugs have no order, so a span cannot say which ids exist
(D-decision-slugs). Since D-decision-per-file the registry is the index plus one file per
decision, and ``decision_registry`` is the shared reader for both halves.

For *decisions* the check is therefore inverted from coverage to membership, which is strictly
stronger: it catches a reviewer prompt citing a decision that was never written — something the
range check could not do. It also fails if a prompt still states a numeric `D<lo>`-`D<hi>`
decision range, so the drift hazard cannot come back for decisions.

*Finding* IDs (`RA`/`RB`/`RC`/`RG`) did **not** become slugs — they stay numeric and are still
enumerated as ranges by at least one prompt (``prompts/invariant.md``). So the issue-#69 drift
guard is retained for those prefixes: any numeric finding range a prompt states must still cover
the real IDs in the registry — the index *and* the decision files, since findings are cited from
both (widening only). A prompt that uses the wildcard form
(`RA-*`) states no range and is vacuously fine. Fully offline: it only reads files already in
the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from decision_registry import defined_slugs, registry_text

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = [
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "invariant.md",
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "docs.md",
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "quality.md",
]

_SLUG_CITE_RE = re.compile(r"\bD-[a-z][a-z0-9-]*\b")
# A numeric decision range in backticks, e.g. `D1`-`D43` — the drift-prone form now banned.
_NUM_RANGE_RE = re.compile(r"`D\d+`\s*[–—-]\s*`D\d+`")

# Finding-ID prefixes tabulated in the registry index. These stay numeric (they did not become slugs),
# so a prompt may still enumerate them as a range, and that range can still drift behind the
# registry — the exact issue-#69 hazard, retained here for the still-numeric prefixes.
_FIND_PREFIXES = ("RA", "RB", "RC", "RG")
_FIND_ID_RE = re.compile(r"\b(" + "|".join(_FIND_PREFIXES) + r")-(\d+)\b")
# A stated finding range in a prompt: `<PREFIX>-<lo>`<sep>`<PREFIX>-<hi>`, same prefix both ends.
_FIND_RANGE_RE = re.compile(
    r"`(?P<p>" + "|".join(_FIND_PREFIXES) + r")-(?P<lo>\d+)`[^`]*`(?P=p)-(?P<hi>\d+)`"
)


def _actual_finding_ids() -> dict[str, set[int]]:
    """Every finding number anywhere in the registry, grouped by prefix."""
    text = registry_text()
    ids: dict[str, set[int]] = {p: set() for p in _FIND_PREFIXES}
    for prefix, num in _FIND_ID_RE.findall(text):
        ids[prefix].add(int(num))
    return ids


def _stated_finding_ranges(prompt: Path) -> dict[str, tuple[int, int]]:
    """Numeric finding ranges the prompt declares, by prefix (last one wins if repeated)."""
    text = prompt.read_text(encoding="utf-8")
    ranges: dict[str, tuple[int, int]] = {}
    for m in _FIND_RANGE_RE.finditer(text):
        ranges[m.group("p")] = (int(m.group("lo")), int(m.group("hi")))
    return ranges


def test_registry_has_slugs() -> None:
    """Guard the guard: if the parser stops finding slugs, membership checks are vacuous."""
    slugs = defined_slugs()
    assert len(slugs) >= 40, f"only {len(slugs)} slugs parsed from the registry — parser broken"


def test_registry_has_finding_ids() -> None:
    """Guard the guard: finding-range coverage is vacuous if no finding IDs parse."""
    actual = _actual_finding_ids()
    assert actual["RA"], "no RA-<n> findings parsed from the registry — parser is broken"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_cites_only_real_slugs(prompt: Path) -> None:
    """Every decision slug a reviewer prompt cites must be defined by the registry, so a
    reviewer populating `decision_ref` from the prompt cites something real."""
    defined = defined_slugs()
    cited = set(_SLUG_CITE_RE.findall(prompt.read_text(encoding="utf-8")))
    unknown = sorted(cited - defined)
    assert not unknown, f"{prompt.name} cites undefined decision slugs: {unknown}"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_states_no_numeric_range(prompt: Path) -> None:
    """No prompt may re-introduce a numeric `D<lo>`-`D<hi>` decision range — the form that
    drifted behind the registry and that slugs make meaningless."""
    text = prompt.read_text(encoding="utf-8")
    assert not _NUM_RANGE_RE.search(text), (
        f"{prompt.name} states a numeric decision-id range; decisions are slug-identified now"
    )


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_finding_ranges_cover_registry(prompt: Path) -> None:
    """Finding IDs stay numeric, so a prompt that enumerates a `RA-<lo>`-`RA-<hi>` range can
    still drift behind the registry (issue #69). Every such range must cover min..max of
    that prefix in the registry. Widening only: adding RA-021 must fail here until the prompt
    catches up. A prompt using the wildcard form (`RA-*`) states no range and is vacuously fine."""
    actual = _actual_finding_ids()
    stated = _stated_finding_ranges(prompt)
    for prefix, (lo, hi) in stated.items():
        real = actual[prefix]
        if not real:
            continue
        lo_real, hi_real = min(real), max(real)
        assert lo <= lo_real and hi >= hi_real, (
            f"{prompt.name}: stated {prefix} range {prefix}-{lo:03d}..{prefix}-{hi:03d} does not "
            f"cover the registry's {prefix}-{lo_real:03d}..{prefix}-{hi_real:03d}. Widen the "
            f"range in {prompt.name} to match the registry, or use the `{prefix}-*` form."
        )
