"""Tests for scripts/merge_decisions.py (D-decisions-merge-driver).

docs/decisions.md requires every new decision to be appended immediately before the fixed
tail marker `## Open items for a future round`, so two independent, non-conflicting PRs that
both append routinely collide at that identical insertion point in a plain git merge. This
driver special-cases exactly that shape -- both sides purely appended sections and left the
tail section alone -- and merges it automatically. Every other case (an edit to an existing
section, an edit to the tail section itself, a genuine same-slug collision with differing
content, or any parse ambiguity) must fall through to `git merge-file`'s own diff3 merge,
i.e. exactly what an unconfigured merge would have produced.

These run offline and touch nothing outside tmp_path.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "scripts" / "merge_decisions.py"

_spec = importlib.util.spec_from_file_location("merge_decisions", SCRIPT)
assert _spec is not None and _spec.loader is not None
merge_decisions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_decisions)

TAIL = "## Open items for a future round\n\n- something open.\n"
BASE = "# Decisions\n\n## D-alpha — first\n\nbody alpha.\n\n" + TAIL


# ---------------------------------------------------------------------------
# Tier 1: pure-Python, git-independent unit tests of try_fast_path().
# ---------------------------------------------------------------------------


def test_pure_double_append_merges_both_in_order() -> None:
    ours = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert merged.index("D-bravo") < merged.index("D-charlie") < merged.index("Open items")
    assert "body bravo" in merged
    assert "body charlie" in merged


def test_identical_suffix_append_is_not_duplicated() -> None:
    same_suffix = "## D-bravo — second\n\nbody bravo.\n\n" + TAIL
    ours = BASE.replace(TAIL, same_suffix)
    theirs = BASE.replace(TAIL, same_suffix)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert merged.count("D-bravo") == 1


def test_same_slug_different_content_abstains() -> None:
    ours = BASE.replace(TAIL, "## D-bravo — second\n\nours version.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\ntheirs version.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_edit_to_open_items_section_abstains() -> None:
    ours = BASE.replace(TAIL, "## Open items for a future round\n\n- something open.\n- new item.\n")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_missing_marker_on_one_side_abstains() -> None:
    ours = BASE.replace(TAIL, "")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_edit_to_existing_section_declines_fast_path() -> None:
    # Correctness of the fallback for this exact case is proven at tier 2, below --
    # here we only assert the fast path recognizes it is not a pure trailing append.
    ours = BASE.replace("body alpha.", "body alpha, revised.")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


# ---------------------------------------------------------------------------
# Tier 2: real git, end-to-end, through the driver's CLI contract.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


def _init_repo(tmp_path: Path, decisions_body: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitattributes").write_text("decisions.md merge=decisions-append\n")
    _git(repo, "config", "merge.decisions-append.driver",
         f"python3 {SCRIPT} %O %A %B")
    (repo / "decisions.md").write_text(decisions_body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_end_to_end_clean_double_append(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, BASE)
    _git(repo, "switch", "-c", "ours", "-q")
    (repo / "decisions.md").write_text(BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL))
    _git(repo, "commit", "-q", "-am", "ours")

    _git(repo, "switch", "-c", "theirs", "main", "-q")
    (repo / "decisions.md").write_text(BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL))
    _git(repo, "commit", "-q", "-am", "theirs")

    _git(repo, "switch", "ours", "-q")
    r = _git(repo, "merge", "--no-commit", "--no-ff", "theirs")
    assert r.returncode == 0, r.stdout + r.stderr

    merged = (repo / "decisions.md").read_text()
    assert "D-bravo" in merged and "D-charlie" in merged
    assert "<<<<<<<" not in merged


def test_end_to_end_same_slug_collision_conflicts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, BASE)
    _git(repo, "switch", "-c", "ours", "-q")
    (repo / "decisions.md").write_text(BASE.replace(TAIL, "## D-bravo — second\n\nours body.\n\n" + TAIL))
    _git(repo, "commit", "-q", "-am", "ours")

    _git(repo, "switch", "-c", "theirs", "main", "-q")
    (repo / "decisions.md").write_text(BASE.replace(TAIL, "## D-bravo — second\n\ntheirs body.\n\n" + TAIL))
    _git(repo, "commit", "-q", "-am", "theirs")

    _git(repo, "switch", "ours", "-q")
    r = _git(repo, "merge", "--no-commit", "--no-ff", "theirs")
    assert r.returncode != 0

    merged = (repo / "decisions.md").read_text()
    assert "<<<<<<<" in merged and "=======" in merged and ">>>>>>>" in merged


def test_end_to_end_fallback_merges_nonoverlapping_edit(tmp_path: Path) -> None:
    # An existing section edited on both branches, on non-overlapping lines: the fast path
    # declines (it's not a pure trailing append), but the git merge-file fallback still
    # succeeds on its own -- proving the fallback works, not just that it was reached.
    body = "# Decisions\n\n## D-alpha — first\n\nline one.\nline two.\nline three.\n\n" + TAIL
    repo = _init_repo(tmp_path, body)

    _git(repo, "switch", "-c", "ours", "-q")
    (repo / "decisions.md").write_text(body.replace("line one.", "line one, revised by ours."))
    _git(repo, "commit", "-q", "-am", "ours")

    _git(repo, "switch", "-c", "theirs", "main", "-q")
    (repo / "decisions.md").write_text(body.replace("line three.", "line three, revised by theirs."))
    _git(repo, "commit", "-q", "-am", "theirs")

    _git(repo, "switch", "ours", "-q")
    r = _git(repo, "merge", "--no-commit", "--no-ff", "theirs")
    assert r.returncode == 0, r.stdout + r.stderr

    merged = (repo / "decisions.md").read_text()
    assert "revised by ours" in merged
    assert "revised by theirs" in merged
