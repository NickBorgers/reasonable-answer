#!/usr/bin/env python3
"""Merge driver for docs/decisions.md (D-decisions-merge-regions, narrowing
D-decisions-merge-driver).

Registered by .gitattributes (`docs/decisions.md merge=decisions-append`) plus
`git config merge.decisions-append.driver 'python3 scripts/merge_decisions.py %O %A %B'`.
Git substitutes %O/%A/%B with temp-file paths for the merge base, "ours", and "theirs";
the result must be written back into the %A file. Exit 0 for clean, non-zero for conflict --
the same contract `git merge-file` uses, which is the fallback below.

Fast path, stated as a *region* rule rather than a whole-file one: peel off the `## D-<slug>
— ...` sections each side added, three-way merge everything that is left -- the sections both
sides already had, and the `## Open items for a future round` tail -- with `git merge-file`,
and reassemble with the new sections appended before the tail marker. The only thing this
driver resolves by itself is the ordering of two insertions anchored on the same base line,
which is precisely the ambiguity git cannot resolve and the only one the registry's
append-before-the-marker convention creates.

D-decisions-merge-driver's original rule asked for more than that: each side's *entire* delta
had to be a trailing append with the tail section untouched. A single unrelated prose fix
anywhere in a 5,000-line registry -- superseding a decision in place, closing an Open item --
disarmed the driver for a collision it had nothing to do with. Measured over main, 4 of the 34
slug-era commits to the file satisfied it.

Every case this cannot confirm still falls through to `git merge-file`, i.e. exactly what
running with no driver configured would have produced: a parse ambiguity, a section deleted
or its heading rewritten, a new heading that is not decision-shaped, the same slug named on
both sides, a real conflict inside a shared section or inside the tail. This must never do
worse than the no-driver baseline.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

TAIL_MARKER = re.compile(r"^## Open items for a future round\s*$", re.MULTILINE)
# The decision-heading form the registry requires, carrying the same slug token
# scripts/validate-decision-numbers.sh checks for duplicates.
SLUG_HEADING = re.compile(r"^## (D-[a-z0-9-]+) — .+$", re.MULTILINE)
# Any top-level heading, decision-shaped or not. Section identity is the whole heading *line*,
# not the slug, because the head legitimately carries headings that are not decision-shaped at
# all -- `## Identifiers: decision slugs, and the old-number mapping`, the adversarial-review
# round headings, and every pre-slug `## D39 — ...` section. Keying on slugs would make the
# driver decline on the real file forever.
LEVEL_TWO_HEADING = re.compile(r"^## .+$", re.MULTILINE)
CONFLICT_MARKER = re.compile(r"^<<<<<<< ", re.MULTILINE)


def split_at_marker(text: str) -> tuple[str, str] | None:
    matches = list(TAIL_MARKER.finditer(text))
    if len(matches) != 1:
        return None  # missing, or duplicated -- either way not a shape this driver reasons about
    m = matches[0]
    return text[: m.start()], text[m.start():]


def split_sections(head: str) -> tuple[str, list[tuple[str, str]]]:
    """`(preamble, [(heading_line, whole_section_text)])`, cut at every `## ` heading.

    Each section runs from its own heading to the next heading (or the end of the head), so
    `preamble + "".join(texts) == head` byte for byte -- including the file's irregular blank
    lines between sections, which must survive a round trip untouched. Callers check that
    identity rather than trusting it.
    """
    matches = list(LEVEL_TWO_HEADING.finditer(head))
    if not matches:
        return head, []
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(head)
        sections.append((m.group(0), head[m.start():end]))
    return head[: matches[0].start()], sections


def _merge_three_ways(base: str, ours: str, theirs: str) -> str | None:
    """`git merge-file` on three strings; None if it conflicts or cannot run.

    This is the same command, and therefore the same merge, git would have performed on the
    whole file with no driver registered -- just applied to the part of the file that is not
    one side's newly added sections.
    """
    if ours == theirs:
        return ours  # identical sides need no merge, and no temp files
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(tmp) / name
            p.write_text(text)
            paths.append(str(p))
        result = subprocess.run(
            ["git", "merge-file", "-p", "--quiet", *paths],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None  # a genuine conflict, or a git that could not run: decline either way
        return result.stdout


def _new_sections(
    base_headings: set[str], base_slugs: set[str], head: str
) -> tuple[str, list[tuple[str, str]]] | None:
    """`(core, added)` for one side: the part of its head that the base already had, and the
    whole sections it added. None if the side's head is not a shape this driver reasons about.
    """
    preamble, sections = split_sections(head)
    if preamble + "".join(text for _, text in sections) != head:
        return None  # the round trip this driver's reassembly depends on does not hold
    headings = [heading for heading, _ in sections]
    if len(set(headings)) != len(headings):
        return None  # a heading line repeated within one side -- identity is not well defined
    if not base_headings.issubset(headings):
        return None  # a section was deleted, or its heading rewritten: not this driver's business

    added: list[tuple[str, str]] = []
    added_slugs: set[str] = set()
    for heading, text in sections:
        if heading in base_headings:
            continue
        m = SLUG_HEADING.fullmatch(heading)
        if m is None:
            return None  # a new heading that is not a decision section (a stray `## `, a
            # heading inside a fenced block) -- never moved, never concatenated
        if not text[len(heading):].strip():
            return None  # a heading with no body
        slug = m.group(1)
        if slug in base_slugs or slug in added_slugs:
            return None  # the same slug defined twice; validate-decision-numbers.sh's rule
        added_slugs.add(slug)
        added.append((heading, text))

    core = preamble + "".join(text for heading, text in sections if heading in base_headings)
    return core, added


def _end_with_one_blank_line(text: str) -> str:
    """The join points are the only bytes this driver writes that neither side wrote.

    The registry's spacing between sections is not uniform -- some sections abut the next
    heading with a single newline -- and appending onto a single-newline join would run a new
    heading straight into the previous body. Normalising only where the pieces meet keeps the
    reassembly well formed without rewriting anything either side authored.
    """
    if not text.endswith("\n"):
        return text + "\n\n"
    if not text.endswith("\n\n"):
        return text + "\n"
    return text


def _slugs(text: str) -> set[str]:
    return {m.group(1) for m in SLUG_HEADING.finditer(text)}


def try_fast_path(base: str, ours: str, theirs: str) -> str | None:
    parts = split_at_marker(base), split_at_marker(ours), split_at_marker(theirs)
    if any(p is None for p in parts):
        return None  # marker missing/duplicated on some side -- never seen, abstain
    (head_o, tail_o), (head_a, tail_a), (head_b, tail_b) = parts

    base_preamble, base_sections = split_sections(head_o)
    if base_preamble + "".join(text for _, text in base_sections) != head_o:
        return None
    base_headings = {heading for heading, _ in base_sections}
    if len(base_headings) != len(base_sections):
        return None  # an ambiguous base: the same heading line twice
    base_slugs = _slugs(head_o)

    side_a = _new_sections(base_headings, base_slugs, head_a)
    side_b = _new_sections(base_headings, base_slugs, head_b)
    if side_a is None or side_b is None:
        return None
    (core_a, added_a), (core_b, added_b) = side_a, side_b

    if not added_a and not added_b:
        # Nothing was added, so there is no insertion-ordering ambiguity to resolve and
        # nothing this driver knows that git does not. Hand the whole file back to the
        # fallback rather than re-deriving its answer a piece at a time.
        return None
    if added_a == added_b:
        added = added_a  # both sides added byte-identical sections; emit them once
    elif {SLUG_HEADING.fullmatch(h).group(1) for h, _ in added_a} & {  # type: ignore[union-attr]
        SLUG_HEADING.fullmatch(h).group(1) for h, _ in added_b  # type: ignore[union-attr]
    }:
        # A slug named on both sides, with the two sides not otherwise identical. Emitting
        # both would define one slug twice; emitting one would silently drop the other side's
        # text. Neither is a merge, so this is a real conflict for a human or an agent.
        return None
    else:
        added = added_a + added_b

    merged_core = _merge_three_ways(head_o, core_a, core_b)
    merged_tail = _merge_three_ways(tail_o, tail_a, tail_b)
    if merged_core is None or merged_tail is None:
        return None  # a real conflict inside a shared section, or inside the tail

    result = _end_with_one_blank_line(merged_core)
    for _, text in added:
        result += _end_with_one_blank_line(text)
    result += merged_tail

    # Post-conditions. git did not perform this recombination, so nothing about it is checked
    # by anything downstream: review-fixer.yml's marker gate assumes a conflict leaves markers,
    # and a clean-looking result that dropped or duplicated a section would sail through it.
    # Each of these declines rather than repairs -- the fallback is always a correct answer.
    if CONFLICT_MARKER.search(result):
        return None
    if len(TAIL_MARKER.findall(result)) != 1:
        return None
    result_head, _ = split_at_marker(result)  # type: ignore[misc]
    _, result_sections = split_sections(result_head)
    result_headings = [heading for heading, _ in result_sections]
    expected = list(base_headings) + [heading for heading, _ in added]
    if sorted(result_headings) != sorted(expected):
        return None
    return result


def main(argv: list[str]) -> int:
    o_path, a_path, b_path = argv[1], argv[2], argv[3]
    try:
        merged = try_fast_path(
            Path(o_path).read_text(), Path(a_path).read_text(), Path(b_path).read_text()
        )
    except Exception:
        # An unexpected input (non-UTF-8 bytes, an encoding this driver did not anticipate)
        # must reach the fallback like any other case it declines to reason about, not
        # exit on a traceback -- "falls through to git merge-file" above means every case.
        merged = None
    if merged is not None:
        Path(a_path).write_text(merged)
        return 0
    result = subprocess.run(
        ["git", "merge-file", "-L", "ours", "-L", "base", "-L", "theirs", a_path, o_path, b_path]
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
