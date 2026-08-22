"""Every decision-shaped citation must resolve to a decision the registry defines.

Decisions are identified by subject slugs (`D-source-verification`), coined per-PR instead of
allocated from a counter (D-decision-slugs). A slug says nothing about whether it exists, so a
citation to one that was never written — or to one deleted by a later edit — resolves to
nothing, and the invariant reviewer treats decision citations as the grounds on which it blocks.
The numeric scheme had no check for this at all.

This test closes that gap for every future PR: it scans the tree for `D-<slug>` citations and
fails if any names a slug the registry does not define. It also holds the line the rename
established — that no bare numeric `D<n>` decision id survives outside the registry's own
old->new mapping table. Fully offline: it only reads files already in the repo.

Since D-decision-per-file the registry is the index plus one file per decision, so "defined"
spans both; `decision_registry` is the single reader for that, shared with
`test_reviewer_prompt_ranges.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from decision_registry import INDEX, decision_files, defined_slugs

REPO_ROOT = Path(__file__).resolve().parents[1]

# The scope the issue names: the docs spec, the code, the tests, the shipped config, and the
# reviewer prompts. README/AGENTS are governance prose that cite decisions too, so they ride
# along. Every path here must cite only real slugs.
SCAN_DIRS = ["docs", "src", "tests", "config"]
SCAN_FILES = ["README.md", "AGENTS.md"] + sorted(
    str(p.relative_to(REPO_ROOT))
    for p in (REPO_ROOT / ".github" / "scripts" / "review" / "prompts").glob("*.md")
)

# These test the identifier machinery itself, so they contain synthetic example slugs
# (`D-alpha`, `D-this-decision-was-never-written`) that are deliberately not real decisions.
EXCLUDE = {
    "tests/test_decision_numbers.py",
    "tests/test_reviewer_prompt_ranges.py",
    "tests/test_citation_resolution.py",
    "tests/test_merge_decisions.py",
    "tests/test_ci_inherit_classifier.py",
    "tests/test_ci_fixer_decisions_driver.py",
    "tests/test_ci_base_moved_resync.py",
}

# A decision slug: `D-` then lowercase words. Does not match the `D-<slug>` prose placeholder
# (`<` is not a slug char) nor a bare numeric `D18`.
SLUG_RE = re.compile(r"\bD-[a-z][a-z0-9-]*\b")
# A bare numeric decision id, the pre-rename form.
NUM_RE = re.compile(r"\bD[0-9]{1,2}\b")


def _scan_paths() -> list[Path]:
    paths: list[Path] = []
    for d in SCAN_DIRS:
        for p in (REPO_ROOT / d).rglob("*"):
            if p.is_file():
                paths.append(p)
    for f in SCAN_FILES:
        p = REPO_ROOT / f
        if p.is_file():
            paths.append(p)
    out = []
    for p in paths:
        rel = str(p.relative_to(REPO_ROOT))
        if rel in EXCLUDE:
            continue
        out.append(p)
    return out


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # a binary fixture holds no citations


def test_registry_parses() -> None:
    """Guard the guard: an empty registry would make every resolution check vacuous."""
    slugs = defined_slugs()
    assert len(slugs) >= 40, f"only {len(slugs)} slugs parsed from the registry — parser broken"
    assert "D-decision-slugs" in slugs
    # The prose half is the half that moved out of the index, so a reader that silently stopped
    # finding the per-decision files would still clear the count above on table rows alone.
    assert len(decision_files()) >= 40, "the per-decision directory is not being read"
    assert "D-decision-per-file" in slugs


def test_every_citation_resolves() -> None:
    """Every `D-<slug>` cited anywhere in scope names a slug the registry defines."""
    defined = defined_slugs()
    offenders: dict[str, list[str]] = {}
    for p in _scan_paths():
        text = _read_text(p)
        if text is None:
            continue
        rel = str(p.relative_to(REPO_ROOT))
        for m in SLUG_RE.finditer(text):
            slug = m.group(0)
            if slug not in defined:
                offenders.setdefault(slug, []).append(rel)
    assert not offenders, (
        "decision citations that resolve to no docs/decisions/D-<slug>.md and no index-table row:\n"
        + "\n".join(f"  {s}: {sorted(set(f))}" for s, f in sorted(offenders.items()))
    )


def test_no_stale_numeric_ids_outside_the_mapping() -> None:
    """No bare numeric `D<n>` decision id survives in scope. The only sanctioned home for the
    old numbers is the registry index's own old->new mapping table, which is excluded here."""
    offenders: dict[str, list[str]] = {}
    for p in _scan_paths():
        if p.resolve() == INDEX.resolve():
            continue  # the mapping table intentionally keeps the old numbers
        text = _read_text(p)
        if text is None:
            continue
        rel = str(p.relative_to(REPO_ROOT))
        for m in NUM_RE.finditer(text):
            offenders.setdefault(m.group(0), []).append(rel)
    assert not offenders, (
        "stale numeric decision ids remain (rename to the D-<slug> scheme):\n"
        + "\n".join(f"  {n}: {sorted(set(f))}" for n, f in sorted(offenders.items()))
    )


def test_an_invented_citation_would_be_caught() -> None:
    """The negative case the numeric scheme never had: a citation to a decision that does not
    exist is not in the defined set, so the resolution check above would flag it."""
    defined = defined_slugs()
    assert "D-this-decision-was-never-written" not in defined
