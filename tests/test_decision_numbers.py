"""Tests for scripts/validate-decision-numbers.sh.

docs/decisions.md identifies each decision by a slug derived from its subject
(`D-source-verification`), not by a number from a shared counter (D-decision-slugs, which
supersedes D-decision-gate). Slugs cannot collide between two concurrently-open PRs, so this
gate exists to catch the one thing they do not prevent: the *same* slug defined twice. A
decision is defined in either of two forms — a `## D-<slug> — …` prose section or a
`| D-<slug> | … |` top-table row — and the gate must refuse a duplicate across their union,
including the table form its predecessor could not see.

These run offline and touch nothing outside tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "validate-decision-numbers.sh"
DECISIONS = _ROOT / "docs" / "decisions.md"


def run(path: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the script against a single decisions file, with a minimal PATH."""
    return subprocess.run(
        ["bash", str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def write(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "decisions.md"
    f.write_text(body)
    return f


def test_unique_slugs_pass(tmp_path: Path) -> None:
    body = (
        "| # | Decision | Rationale |\n"
        "| D-alpha | first | r |\n\n"
        "## D-beta — second\n\n## D-gamma — third\n"
    )
    r = run(write(tmp_path, body))
    assert r.returncode == 0, r.stderr


def test_duplicate_prose_slug_fails(tmp_path: Path) -> None:
    r = run(write(tmp_path, "## D-alpha — a\n\n## D-beta — b\n\n## D-alpha — c\n"))
    assert r.returncode == 1
    assert "D-alpha" in r.stderr


def test_duplicate_across_forms_fails(tmp_path: Path) -> None:
    # The blind spot the predecessor gate had: a slug defined once as a table row and once
    # as a prose section is still a duplicate. This is the collision four numbers hid in.
    body = "| # | Decision | Rationale |\n| D-alpha | terse | r |\n\n## D-alpha — the expansion\n"
    r = run(write(tmp_path, body))
    assert r.returncode == 1
    assert "D-alpha" in r.stderr


def test_slug_prefix_is_not_a_duplicate(tmp_path: Path) -> None:
    # Distinct slugs that share a prefix are different decisions, not a collision.
    r = run(write(tmp_path, "## D-social-bias — a\n\n## D-social-bias-audit — b\n"))
    assert r.returncode == 0, r.stderr


def test_references_and_mapping_rows_are_not_definitions(tmp_path: Path) -> None:
    # A prose mention of a slug, and the old->new mapping table (whose first cell is an old
    # numeric id and whose slug sits in the *second* column), are references, not definitions.
    body = (
        "| old id | new slug |\n"
        "|---|---|\n"
        "| D18 | `D-alpha` |\n"
        "| D24 | `D-alpha` |\n\n"
        "See D-alpha, and again D-alpha, discussed below.\n\n"
        "## D-alpha — the only real definition here\n"
    )
    r = run(write(tmp_path, body))
    assert r.returncode == 0, r.stderr


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    r = run(tmp_path / "nope.md")
    assert r.returncode == 2


def test_shipped_decision_log_has_no_collisions() -> None:
    # A live guard: whatever lands on this branch (and, on a PR, the merge result) must
    # already be collision-free. Deterministic and offline — it only reads the file.
    r = run(DECISIONS)
    assert r.returncode == 0, r.stderr
