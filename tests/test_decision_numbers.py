"""Tests for scripts/validate-decision-numbers.sh.

docs/decisions.md numbers its sections `## D<n>`, and that number is echoed across
config/, src/, tests/ and docs. The number is allocated by whoever writes the PR, so two
PRs open at once can each pick the same next-free number and collide when both merge
(issue #71). The script refuses a file in which any decision number is defined twice; on a
`pull_request` event the checked-out file is the merge result, so a duplicate there is a
collision that would otherwise land on main.

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


def test_unique_numbers_pass(tmp_path: Path) -> None:
    r = run(write(tmp_path, "## D20 — a\n\n## D21 — b\n\n## D23 — c\n"))
    assert r.returncode == 0, r.stderr


def test_duplicate_number_fails(tmp_path: Path) -> None:
    r = run(write(tmp_path, "## D30 — a\n\n## D31 — b\n\n## D30 — c\n"))
    assert r.returncode == 1
    assert "D30" in r.stderr


def test_substring_numbers_are_distinct(tmp_path: Path) -> None:
    # D3 and D30 share a prefix but are different allocations; neither is a duplicate.
    r = run(write(tmp_path, "## D3 — a\n\n## D30 — b\n"))
    assert r.returncode == 0, r.stderr


def test_references_are_not_allocations(tmp_path: Path) -> None:
    # A summary row (`| D1 | … |`) and prose mentioning D26 twice are references, not
    # `## D<n>` section headers, so they must not be counted as collisions.
    body = (
        "| # | Decision | Rationale |\n"
        "| D1 | first | r |\n"
        "| D1 | still the same row family | r |\n\n"
        "See D26, and again D26, discussed below.\n\n"
        "## D26 — the only real allocation here\n"
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
