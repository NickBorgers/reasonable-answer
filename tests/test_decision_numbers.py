"""Tests for scripts/validate-decision-numbers.sh.

The decision registry is an index plus one file per decision (D-decision-per-file):
``docs/decisions.md`` carries the identifier scheme and the finding tables, while
``docs/decisions/D-<slug>.md`` holds the prose for ``D-<slug>``. Each decision is identified by a
slug derived from its subject (`D-source-verification`), not by a number from a shared counter
(D-decision-slugs, which supersedes D-decision-gate).

Slugs cannot collide between two concurrently-open PRs, so this gate does not exist to catch a
numbering race. It exists to catch what slugs do not prevent, in two parts:

* **uniqueness** — the *same* slug defined twice, across the union of both definition forms: a
  ``## D-<slug> — …`` heading opening a decision file, or a ``| D-<slug> | … |`` index-table row.
  The predecessor gate read only the prose form and could not see the table half, where four
  reused numbers had each named two decisions.
* **shape** — what makes a slug's definition unambiguous now that it is no longer "the one
  section with that heading in the one file": every directory entry is a regular file named
  ``D-<slug>.md``, each opens with the matching heading and holds exactly one, and no prose
  section is left behind in the index.

These run offline and touch nothing outside tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "validate-decision-numbers.sh"
DECISIONS = _ROOT / "docs" / "decisions.md"


def run(index: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the script against one registry, with a minimal PATH.

    The script takes only the index path and derives the decision directory from it as a
    sibling of the same stem, which is what lets a fixture tree stand in for docs/.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), str(index)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def write(tmp_path: Path, index: str, decisions: dict[str, str] | None = None) -> Path:
    """Build a fixture registry: an index file plus a sibling decisions/ directory."""
    f = tmp_path / "decisions.md"
    f.write_text(index)
    d = tmp_path / "decisions"
    d.mkdir()
    for name, body in (decisions or {}).items():
        (d / name).write_text(body)
    return f


# --- uniqueness -----------------------------------------------------------------------------


def test_unique_slugs_pass(tmp_path: Path) -> None:
    index = "| # | Decision | Rationale |\n| D-alpha | first | r |\n"
    files = {
        "D-beta.md": "## D-beta — second\n\nbody.\n",
        "D-gamma.md": "## D-gamma — third\n\nbody.\n",
    }
    r = run(write(tmp_path, index, files))
    assert r.returncode == 0, r.stderr


def test_duplicate_across_forms_fails(tmp_path: Path) -> None:
    # The blind spot the predecessor gate had: a slug defined once as an index-table row and
    # once as a decision file is still a duplicate. This is the collision four numbers hid in.
    index = "| # | Decision | Rationale |\n| D-alpha | terse | r |\n"
    r = run(write(tmp_path, index, {"D-alpha.md": "## D-alpha — the expansion\n"}))
    assert r.returncode == 1
    assert "D-alpha" in r.stderr


def test_duplicate_table_rows_only_fails(tmp_path: Path) -> None:
    index = (
        "| # | Decision | Rationale |\n"
        "| D-alpha | first definition | r |\n"
        "| D-alpha | second definition | r |\n"
    )
    r = run(write(tmp_path, index))
    assert r.returncode == 1
    assert "D-alpha is defined 2 times (files: 0, index table: 2)" in r.stderr


def test_slug_prefix_is_not_a_duplicate(tmp_path: Path) -> None:
    # Distinct slugs that share a prefix are different decisions, not a collision.
    files = {
        "D-social-bias.md": "## D-social-bias — a\n",
        "D-social-bias-audit.md": "## D-social-bias-audit — b\n",
    }
    r = run(write(tmp_path, "# index\n", files))
    assert r.returncode == 0, r.stderr


def test_references_and_mapping_rows_are_not_definitions(tmp_path: Path) -> None:
    # A prose mention of a slug, and the old->new mapping table (whose first cell is an old
    # numeric id and whose slug sits in the *second* column), are references, not definitions.
    index = (
        "| old id | new slug |\n"
        "|---|---|\n"
        "| D18 | `D-alpha` |\n"
        "| D24 | `D-alpha` |\n\n"
        "See D-alpha, and again D-alpha, discussed in its own file.\n"
    )
    r = run(write(tmp_path, index, {"D-alpha.md": "## D-alpha — the only real definition\n"}))
    assert r.returncode == 0, r.stderr


# --- shape ----------------------------------------------------------------------------------


def test_filename_disagreeing_with_heading_fails(tmp_path: Path) -> None:
    # The filename is the identifier a citation resolves through, so a file whose heading
    # defines a different slug makes that resolution wrong in a way nothing else would catch.
    r = run(write(tmp_path, "# index\n", {"D-gamma.md": "## D-beta — b\n"}))
    assert r.returncode == 1
    assert "D-gamma" in r.stderr and "D-beta" in r.stderr


def test_two_decisions_in_one_file_fails(tmp_path: Path) -> None:
    # One file per decision is the property that makes adds conflict-free; a file holding two
    # is also a slug whose defining path is ambiguous.
    body = "## D-beta — b\n\nbody.\n\n## D-delta — d\n\nbody.\n"
    r = run(write(tmp_path, "# index\n", {"D-beta.md": body}))
    assert r.returncode == 1
    assert "exactly one" in r.stderr


def test_heading_must_open_the_file(tmp_path: Path) -> None:
    body = "Some preamble that is not the heading.\n\n## D-beta — b\n"
    r = run(write(tmp_path, "# index\n", {"D-beta.md": body}))
    assert r.returncode == 1
    assert "does not open with" in r.stderr


def test_missing_title_separator_fails(tmp_path: Path) -> None:
    # `## D-beta` with no ` — <title>` is not the definition form the registry, the reviewer
    # prompts and tests/decision_registry.py all key on.
    r = run(write(tmp_path, "# index\n", {"D-beta.md": "## D-beta\n\nbody.\n"}))
    assert r.returncode == 1


def test_stray_file_in_the_directory_fails(tmp_path: Path) -> None:
    # Fail closed: anything that is not `D-<slug>.md` is either a decision the citation tests
    # cannot resolve or a slug two paths could claim.
    r = run(write(tmp_path, "# index\n", {"README.md": "notes\n"}))
    assert r.returncode == 1
    assert "not a decision file" in r.stderr


def test_prose_section_left_in_the_index_fails(tmp_path: Path) -> None:
    # A section left behind in the index is the shared insertion point coming back, and a
    # decision defined in two places at once.
    index = "# index\n\n## D-beta — b\n\nbody.\n"
    r = run(write(tmp_path, index, {"D-beta.md": "## D-beta — b\n"}))
    assert r.returncode == 1
    assert "prose sections" in r.stderr


def test_an_added_decision_needs_no_other_edit(tmp_path: Path) -> None:
    """The property the split exists for: adding a file is a complete, valid decision.

    Two independently added decisions both pass with the index untouched — which is what makes
    two decision-bearing PRs conflict-free, and is worth asserting rather than assuming.
    """
    index = "# index\n\n## Open items for a future round\n\n- something open.\n"
    reg = write(tmp_path, index, {"D-alpha.md": "## D-alpha — a\n"})
    assert run(reg).returncode == 0

    (tmp_path / "decisions" / "D-omega.md").write_text("## D-omega — o\n")
    r = run(reg)
    assert r.returncode == 0, r.stderr
    assert index == reg.read_text(), "the index was not touched by adding a decision"


# --- structural errors ----------------------------------------------------------------------


def test_missing_index_is_an_error(tmp_path: Path) -> None:
    r = run(tmp_path / "nope.md")
    assert r.returncode == 2


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    # Fail closed rather than reporting a registry of table rows alone as well-formed.
    f = tmp_path / "decisions.md"
    f.write_text("# index\n")
    r = run(f)
    assert r.returncode == 2


# --- the shipped registry -------------------------------------------------------------------


def test_shipped_decision_log_is_well_formed() -> None:
    # A live guard: whatever lands on this branch (and, on a PR, the merge result) must already
    # be collision-free and correctly shaped. Deterministic and offline — it only reads files.
    r = run(DECISIONS)
    assert r.returncode == 0, r.stderr
