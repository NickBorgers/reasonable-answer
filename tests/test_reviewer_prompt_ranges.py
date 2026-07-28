"""The reviewer prompts enumerate the valid decision/finding ID ranges so the
invariant and docs reviewers reject invented IDs while accepting real ones. Those
ranges are hand-written prose, so they drift behind ``docs/decisions.md`` every time
a decision is added — issue #69: the invariant prompt still said ``D1``–``D26`` after
D27–D30 had landed, so it could block or mis-flag a PR citing a newer decision.

This check derives the authoritative ID set from ``docs/decisions.md`` and fails when
any range a reviewer prompt states no longer covers it. It is fully offline: it only
reads files already in the repo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "docs" / "decisions.md"
PROMPTS = [
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "invariant.md",
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "docs.md",
    REPO_ROOT / ".github" / "scripts" / "review" / "prompts" / "quality.md",
]

# Prefixes tabulated in decisions.md. RD-/RH-/RI- live only in convergence.md and are
# handled separately by the prompts, so they are deliberately excluded here.
_PREFIXES = ("D", "RA", "RB", "RC", "RG")
_ID_RE = re.compile(r"\b(" + "|".join(_PREFIXES) + r")-?(\d+)\b")
# A stated range in a prompt: `<PREFIX><lo>`<sep>`<PREFIX><hi>`, same prefix both ends.
_RANGE_RE = re.compile(
    r"`(?P<p>" + "|".join(_PREFIXES) + r")-?(?P<lo>\d+)`[^`]*`(?P=p)-?(?P<hi>\d+)`"
)


def _actual_ids() -> dict[str, set[int]]:
    """Every decision/finding number in decisions.md, grouped by prefix."""
    text = DECISIONS.read_text(encoding="utf-8")
    ids: dict[str, set[int]] = {p: set() for p in _PREFIXES}
    for prefix, num in _ID_RE.findall(text):
        ids[prefix].add(int(num))
    return ids


def _stated_ranges(prompt: Path) -> dict[str, tuple[int, int]]:
    """Numeric ID ranges the prompt declares, by prefix (last one wins if repeated)."""
    text = prompt.read_text(encoding="utf-8")
    ranges: dict[str, tuple[int, int]] = {}
    for m in _RANGE_RE.finditer(text):
        ranges[m.group("p")] = (int(m.group("lo")), int(m.group("hi")))
    return ranges


def test_decisions_has_ids() -> None:
    """Guard the guard: if the parser stops finding IDs, coverage checks are vacuous."""
    actual = _actual_ids()
    assert actual["D"], "no D<n> decisions parsed from decisions.md — parser is broken"
    assert max(actual["D"]) >= 30, "expected at least D30 in decisions.md"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_declares_decision_range(prompt: Path) -> None:
    """Each reviewer prompt must still declare a numeric D range at all — catches a
    silent deletion that would leave the reviewer with no bound to check against."""
    assert "D" in _stated_ranges(prompt), (
        f"{prompt.name} no longer states a `D<lo>`-`D<hi>` decision-ID range"
    )


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_ranges_cover_decisions(prompt: Path) -> None:
    """Every numeric range a prompt states must cover all real IDs of that prefix in
    decisions.md. Widening only: adding D31 must fail here until the prompt catches up."""
    actual = _actual_ids()
    stated = _stated_ranges(prompt)
    for prefix, (lo, hi) in stated.items():
        real = actual[prefix]
        if not real:
            continue
        lo_real, hi_real = min(real), max(real)
        assert lo <= lo_real and hi >= hi_real, (
            f"{prompt.name}: stated {prefix} range {prefix}{lo}-{prefix}{hi} does not "
            f"cover decisions.md {prefix}{lo_real}-{prefix}{hi_real}. Update the range "
            f"in {prompt.name} to match docs/decisions.md."
        )
