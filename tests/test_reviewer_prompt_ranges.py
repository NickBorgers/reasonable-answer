"""The reviewer prompts must cite the decision scheme actually in use.

They used to enumerate a numeric ID *range* (`D1`-`D43`) so the invariant and docs reviewers
could reject invented IDs — but a range is hand-written prose that drifted behind
``docs/decisions.md`` every time a decision was added (issue #69), and under subject slugs a
range is meaningless anyway: slugs have no order, so a span cannot say which ids exist
(D-decision-slugs).

The check is therefore inverted from coverage to membership, which is strictly stronger: it
catches a reviewer prompt citing a decision that was never written — something the range check
could not do. It also fails if a prompt still states a numeric `D<lo>`-`D<hi>` decision range,
so the drift hazard cannot come back. Fully offline: it only reads files already in the repo.
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

_SLUG_CITE_RE = re.compile(r"\bD-[a-z][a-z0-9-]*\b")
# A numeric decision range in backticks, e.g. `D1`-`D43` — the drift-prone form now banned.
_NUM_RANGE_RE = re.compile(r"`D\d+`\s*[–—-]\s*`D\d+`")


def _defined_slugs() -> set[str]:
    text = DECISIONS.read_text(encoding="utf-8")
    slugs = set(re.findall(r"^## (D-[a-z0-9-]+) —", text, re.M))
    slugs |= set(re.findall(r"^\| (D-[a-z0-9-]+) \|", text, re.M))
    return slugs


def test_registry_has_slugs() -> None:
    """Guard the guard: if the parser stops finding slugs, membership checks are vacuous."""
    slugs = _defined_slugs()
    assert len(slugs) >= 40, f"only {len(slugs)} slugs parsed from decisions.md — parser broken"


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_prompt_cites_only_real_slugs(prompt: Path) -> None:
    """Every decision slug a reviewer prompt cites must exist in docs/decisions.md, so a
    reviewer populating `decision_ref` from the prompt cites something real."""
    defined = _defined_slugs()
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
