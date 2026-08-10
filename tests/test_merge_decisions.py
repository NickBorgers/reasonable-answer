"""Tests for scripts/merge_decisions.py (D-decisions-merge-regions).

docs/decisions.md requires every new decision to be appended immediately before the fixed
tail marker `## Open items for a future round`, so two independent, non-conflicting PRs that
both append routinely collide at that identical insertion point in a plain git merge. The
driver resolves that one ambiguity: it peels off the sections each side *added*, three-way
merges everything else -- shared sections and the tail -- with `git merge-file`, and
reassembles.

The cases it must still decline (a section deleted or its heading rewritten, a new heading
that is not decision-shaped, the same slug on both sides, a real conflict inside a shared
section or the tail, any parse ambiguity) fall through to `git merge-file`'s own diff3 merge,
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


def test_duplicate_heading_line_in_base_abstains() -> None:
    base = (
        "# Decisions\n\n"
        "## Notes\n\nfirst note.\n\n"
        "## Notes\n\nsecond note.\n\n"
        "## D-alpha — first\n\nbody alpha.\n\n"
        + TAIL
    )
    ours = base.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    theirs = base.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(base, ours, theirs) is None


def test_duplicate_heading_line_within_one_sides_added_sections_abstains() -> None:
    ours = BASE.replace(
        TAIL,
        "## Weird\n\nfirst body.\n\n## Weird\n\nsecond body.\n\n" + TAIL,
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_section_with_empty_body_abstains() -> None:
    ours = BASE.replace(TAIL, "## D-bravo — second\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_open_items_edit_on_one_side_merges_with_an_append_on_the_other() -> None:
    # D-decisions-merge-driver declined this outright. It is the single most common shape in
    # the log's real history -- a decision that also closes or opens an Open item -- and the
    # tail edit has nothing to do with the insertion-point collision. The tail is now merged
    # on its own, so one side's bullet survives alongside the other side's new section.
    ours = BASE.replace(TAIL, "## Open items for a future round\n\n- something open.\n- new item.\n")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert "- new item." in merged
    assert merged.index("D-bravo") < merged.index("Open items")


def test_missing_marker_on_one_side_abstains() -> None:
    ours = BASE.replace(TAIL, "")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_edit_to_an_existing_section_merges_with_an_append_on_the_other_side() -> None:
    # Superseding a decision in place is what CLAUDE.md prescribes ("Decisions are superseded
    # in place, never deleted"), so an in-place revision landing on the base while a branch
    # appends is routine. Under D-decisions-merge-driver the revision -- anywhere in a
    # 5,000-line file -- disarmed the driver for a collision it played no part in.
    ours = BASE.replace("body alpha.", "body alpha, revised.")
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert "body alpha, revised." in merged
    assert "body bravo." in merged


def test_both_sides_editing_the_same_section_differently_abstains() -> None:
    # The other half of the rule above: the shared sections are merged by git merge-file, so a
    # genuine disagreement inside one is a genuine conflict and must reach the fallback.
    ours = BASE.replace("body alpha.", "body alpha, as ours would have it.")
    theirs = BASE.replace("body alpha.", "body alpha, as theirs would have it.")
    theirs = theirs.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_both_sides_editing_open_items_differently_abstains() -> None:
    ours = BASE.replace("- something open.", "- something open, closed by ours.")
    theirs = BASE.replace("- something open.", "- something open, closed by theirs.")
    theirs = theirs.replace(TAIL.replace("- something open.", "- something open, closed by theirs."),
                            "## D-bravo — second\n\nbody bravo.\n\n"
                            + TAIL.replace("- something open.", "- something open, closed by theirs."))
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_section_added_mid_file_still_merges() -> None:
    # The PR #161 shape. A branch that hand-resolved an earlier collision ends up with its own
    # section placed *above* the one it merged in, so its delta is an insert, not an append --
    # permanently, for every later merge. Both new sections land before the tail marker, which
    # is where D-decision-slugs says a decision goes.
    base = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    ours = base.replace("## D-bravo", "## D-charlie — third\n\nbody charlie.\n\n## D-bravo")
    theirs = base.replace(TAIL, "## D-delta — fourth\n\nbody delta.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(base, ours, theirs)
    assert merged is not None
    assert merged.count("## D-charlie") == 1 and merged.count("## D-delta") == 1
    assert merged.index("D-charlie") < merged.index("Open items")
    assert merged.index("D-delta") < merged.index("Open items")


def test_no_blank_line_before_the_marker_still_merges() -> None:
    # The shape that killed 3c248a5 on main: the last section runs into the tail marker with a
    # single newline, so the appended text begins with the newline that completes the blank
    # separator. Only the join points are normalized -- neither side's own bytes are rewritten.
    base = "# Decisions\n\n## D-alpha — first\n\nbody alpha.\n" + TAIL
    ours = base.replace(TAIL, "\n## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    theirs = base.replace(TAIL, "\n## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(base, ours, theirs)
    assert merged is not None
    assert "body alpha.\n\n## D-bravo" in merged
    assert "body bravo.\n\n## D-charlie" in merged
    assert "body charlie.\n\n" + TAIL in merged


def test_non_decision_headings_in_the_head_survive_in_place() -> None:
    # The real file's head carries headings that are not decision-shaped at all: the
    # identifiers preamble, the adversarial-review round headings, and every pre-slug
    # `## D39 — ...` section. Section identity is the heading line, so these merge as ordinary
    # shared sections and are never mistaken for something to move to the tail.
    base = (
        "# Decisions\n\n"
        "## Identifiers: decision slugs, and the old-number mapping\n\nmapping table.\n\n"
        "## D39 — a pre-slug decision\n\nold body.\n\n"
        "## Codex adversarial review — round 1\n\nfindings.\n\n"
        "## D-alpha — first\n\nbody alpha.\n\n" + TAIL
    )
    ours = base.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    theirs = base.replace("old body.", "old body, corrected.")
    merged = merge_decisions.try_fast_path(base, ours, theirs)
    assert merged is not None
    assert merged.index("## Identifiers") < merged.index("## D39") < merged.index("## Codex")
    assert merged.index("## Codex") < merged.index("## D-alpha") < merged.index("## D-bravo")
    assert "old body, corrected." in merged


def test_a_new_non_decision_heading_abstains() -> None:
    # A new `## ` heading that is not a decision section -- including one that appeared inside
    # a fenced block -- is never moved and never concatenated.
    ours = BASE.replace(TAIL, "## Security review — 2026-08\n\nfindings.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_deleting_a_section_abstains() -> None:
    # Decisions are superseded in place, never deleted (CLAUDE.md). A side that dropped one is
    # doing something this driver has no theory of -- including rewriting a heading line, which
    # reads here as a deletion plus an addition.
    base = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    ours = base.replace("## D-bravo — second\n\nbody bravo.\n\n", "")
    theirs = base.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(base, ours, theirs) is None


def test_a_new_slug_that_already_exists_in_the_base_abstains() -> None:
    # Same slug, different heading text: two definitions of one identifier, which
    # scripts/validate-decision-numbers.sh rejects outright.
    base = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    ours = base.replace(TAIL, "## D-bravo — second, restated\n\nanother body.\n\n" + TAIL)
    theirs = base.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(base, ours, theirs) is None


def test_neither_side_added_a_section_abstains() -> None:
    # With nothing added there is no insertion-ordering ambiguity, so the driver knows nothing
    # git does not: hand the whole file to the fallback rather than re-derive its answer.
    ours = BASE.replace("body alpha.", "body alpha, revised by ours.")
    theirs = BASE.replace("- something open.", "- something open, and another.")
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_split_sections_round_trips_irregular_spacing() -> None:
    # The reassembly is a concatenation of slices, so it can only preserve the log's
    # inconsistent blank lines between sections if this identity holds exactly.
    head = (
        "# Decisions\n\n## D-alpha — first\n\nbody alpha.\n"
        "## D-bravo — second\n\nbody bravo.\n\n\n"
        "## D-charlie — third\n\nbody charlie.\n"
    )
    preamble, sections = merge_decisions.split_sections(head)
    assert preamble + "".join(text for _, text in sections) == head
    assert [heading for heading, _ in sections] == [
        "## D-alpha — first", "## D-bravo — second", "## D-charlie — third"
    ]


def test_duplicated_marker_abstains() -> None:
    # split_at_marker must reject an ambiguous document rather than silently split at the
    # first occurrence -- a malformed decisions.md should never be reasoned about.
    doubled = BASE + TAIL
    ours = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(doubled, ours, doubled) is None


def test_prose_appended_before_the_marker_is_merged_as_a_body_edit() -> None:
    # Under the old whole-file rule this abstained, because the appended text was not itself a
    # decision section. Under the region rule it is not "appended content" at all -- it is an
    # edit to the last existing section's body, and it reaches git merge-file as such. Nothing
    # is concatenated blind: the prose stays exactly where its author put it, inside D-alpha.
    ours = BASE.replace(TAIL, "just a stray note, not a decision.\n\n" + TAIL)
    theirs = BASE.replace(TAIL, "## D-bravo — second\n\nbody bravo.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert merged.index("stray note") < merged.index("## D-bravo")


def test_non_decision_heading_inside_suffix_abstains() -> None:
    # A `## ` heading that isn't `## D-<slug>`-shaped would otherwise be swallowed into the
    # preceding section's body by slug_sections()'s next-match-or-EOF boundary.
    ours = BASE.replace(
        TAIL,
        "## D-bravo — second\n\nbody bravo.\n\n## Not a decision\n\nsneaked in.\n\n" + TAIL,
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    assert merge_decisions.try_fast_path(BASE, ours, theirs) is None


def test_text_before_a_new_heading_stays_in_the_preceding_section() -> None:
    # Same reasoning as above, with a new section behind the prose: the prose belongs to
    # D-alpha's body and is merged there, while D-bravo is the added section. The two are
    # never conflated, which is what the old all-or-nothing suffix parse was protecting.
    ours = BASE.replace(
        TAIL, "preamble text\n\n## D-bravo — second\n\nbody bravo.\n\n" + TAIL
    )
    theirs = BASE.replace(TAIL, "## D-charlie — third\n\nbody charlie.\n\n" + TAIL)
    merged = merge_decisions.try_fast_path(BASE, ours, theirs)
    assert merged is not None
    assert merged.index("preamble text") < merged.index("## D-bravo") < merged.index("## D-charlie")


def test_main_falls_back_for_non_utf8_input(tmp_path: Path) -> None:
    base = tmp_path / "base"
    ours = tmp_path / "ours"
    theirs = tmp_path / "theirs"
    expected_base = tmp_path / "expected-base"
    expected_ours = tmp_path / "expected-ours"
    expected_theirs = tmp_path / "expected-theirs"

    base.write_bytes(b"base\n")
    ours.write_bytes(b"ours\n\xff\n")
    theirs.write_bytes(b"theirs\n")
    expected_base.write_bytes(base.read_bytes())
    expected_ours.write_bytes(ours.read_bytes())
    expected_theirs.write_bytes(theirs.read_bytes())

    expected = subprocess.run(
        [
            "git",
            "merge-file",
            "-L",
            "ours",
            "-L",
            "base",
            "-L",
            "theirs",
            expected_ours,
            expected_base,
            expected_theirs,
        ]
    )
    actual = merge_decisions.main(["merge_decisions.py", str(base), str(ours), str(theirs)])

    assert actual == expected.returncode
    assert ours.read_bytes() == expected_ours.read_bytes()


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


def test_end_to_end_reproduces_pr_161(tmp_path: Path) -> None:
    # The shape that left PR #161 conflicted for three days while sync-open-prs.yml reported
    # `state=conflicts` on every push to main: ours inserts its section *above* the last one
    # (the residue of an earlier hand-resolved collision), while the base gains two sections,
    # an in-place correction to an older decision's prose, and an Open-items bullet. Every one
    # of those made the old driver decline; none of them is part of the actual collision.
    body = (
        "# Decisions\n\n"
        "## D-alpha — first\n\nbody alpha, as originally written.\n\n"
        "## D-omega — last before the marker\n\nbody omega.\n\n" + TAIL
    )
    repo = _init_repo(tmp_path, body)

    _git(repo, "switch", "-c", "ours", "-q")
    (repo / "decisions.md").write_text(
        body.replace("## D-omega", "## D-ours — inserted above the last one\n\nbody ours.\n\n## D-omega")
    )
    _git(repo, "commit", "-q", "-am", "ours")

    _git(repo, "switch", "-c", "theirs", "main", "-q")
    appended = "## D-theirs-one — appended\n\nbody one.\n\n## D-theirs-two — appended\n\nbody two.\n\n"
    reopened = TAIL.replace("- something open.", "- something open.\n- a new open item.")
    (repo / "decisions.md").write_text(
        body.replace("body alpha, as originally written.", "body alpha, corrected in place.")
        .replace(TAIL, appended + reopened)
    )
    _git(repo, "commit", "-q", "-am", "theirs")

    _git(repo, "switch", "ours", "-q")
    r = _git(repo, "merge", "--no-commit", "--no-ff", "theirs")
    assert r.returncode == 0, r.stdout + r.stderr

    merged = (repo / "decisions.md").read_text()
    assert "<<<<<<<" not in merged
    for slug in ("D-ours", "D-theirs-one", "D-theirs-two"):
        assert merged.count(f"## {slug} ") == 1
        assert merged.index(slug) < merged.index("Open items")
    assert "body alpha, corrected in place." in merged
    assert "- a new open item." in merged


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
