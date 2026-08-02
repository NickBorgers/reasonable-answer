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


def test_two_sections_on_one_side_still_merges() -> None:
    # Regression test: slug_sections() once read a section's body by indexing back into
    # `section` (a slice of `suffix` starting at the heading's own offset) using an offset
    # absolute within `suffix`. Correct only for the first section in a suffix -- for any
    # later one it read past the slice's end, saw an empty body, and aborted the whole
    # suffix. That made the fast path silently abstain whenever either side appended more
    # than one decision, which is the common shape (a branch appending one decision while
    # two more landed on main since the merge base).
    ours = BASE.replace(
        TAIL, "## D-bravo — second\n\nbody bravo.\n\n## D-charlie — third\n\nbody charlie.\n\n" + TAIL
    )
    theirs = BASE.replace(TAIL, "## D-delta — fourth\n\nbody delta.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert "D-bravo" in merged and "D-charlie" in merged and "D-delta" in merged


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


def test_same_slug_identical_body_but_differing_suffix_abstains() -> None:
    # Same slug, same body, on both sides -- but each suffix also carries something the
    # other doesn't (D-echo only on ours), so the two suffixes are not equal overall.
    # Concatenating them would duplicate the D-bravo section verbatim rather than merge two
    # distinct decisions -- any cross-side slug intersection must abstain, not just a
    # differing-body one.
    shared = "## D-bravo — second\n\nshared body.\n\n"
    ours = BASE.replace(TAIL, shared + "## D-echo — extra\n\nonly on ours.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, shared + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_duplicate_slug_within_one_sides_suffix_abstains() -> None:
    ours = BASE.replace(
        TAIL, "## D-bravo — second\n\nfirst body.\n\n## D-bravo — second again\n\nsecond body.\n\n" + TAIL
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_section_with_empty_body_abstains() -> None:
    ours = BASE.replace(TAIL, "## D-bravo — second\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
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


def test_duplicated_marker_abstains() -> None:
    # split_at_marker must reject an ambiguous document rather than silently split at the
    # first occurrence -- a malformed decisions.md should never be reasoned about.
    doubled = BASE + TAIL
    ours = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(doubled, ours, doubled) is None


def test_arbitrary_prose_appended_before_marker_abstains() -> None:
    # The suffix must be *entirely* whole decision sections. Prose that isn't shaped like a
    # decision heading must not be silently concatenated in.
    ours = BASE.replace(TAIL, "just a stray note, not a decision.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_non_decision_heading_inside_suffix_abstains() -> None:
    # A `## ` heading that isn't `## D-<slug>`-shaped would otherwise be swallowed into the
    # preceding section's body by slug_sections()'s next-match-or-EOF boundary.
    ours = BASE.replace(
        TAIL,
        "## D-bravo — second\n\nbody bravo.\n\n## Not a decision\n\nsneaked in.\n\n" + TAIL,
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_text_before_first_heading_in_suffix_abstains() -> None:
    ours = BASE.replace(
        TAIL, "preamble text\n\n## D-bravo — second\n\nbody bravo.\n\n" + TAIL
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
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
