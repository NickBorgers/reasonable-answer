#!/usr/bin/env python3
"""Merge driver for docs/decisions.md (D-decisions-merge-driver).

Registered by .gitattributes (`docs/decisions.md merge=decisions-append`) plus
`git config merge.decisions-append.driver 'python3 scripts/merge_decisions.py %O %A %B'`.
Git substitutes %O/%A/%B with temp-file paths for the merge base, "ours", and "theirs";
the result must be written back into the %A file. Exit 0 for clean, non-zero for conflict --
the same contract `git merge-file` uses, which is the fallback below.

Fast path: both sides purely appended whole `## D-<slug> -- ...` sections directly before
the `## Open items for a future round` tail marker, and left that section itself alone.
Every other case -- any parse ambiguity, any edit inside the head other than a pure
trailing append, any edit to the tail section, a same-slug collision with differing
content -- falls through to `git merge-file`, i.e. exactly what running with no driver
configured would have produced. This must never do worse than the no-driver baseline.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TAIL_MARKER = re.compile(r"^## Open items for a future round\s*$", re.MULTILINE)
# The decision-heading form the registry requires, carrying the same slug token
# scripts/validate-decision-numbers.sh checks for duplicates.
SLUG_HEADING = re.compile(r"^## (D-[a-z0-9-]+) — .+$", re.MULTILINE)
# Any top-level heading, decision-shaped or not -- used to catch a non-decision heading
# (or stray prose before the first decision heading) hiding inside an appended suffix.
LEVEL_TWO_HEADING = re.compile(r"^## .+$", re.MULTILINE)


def split_at_marker(text: str) -> tuple[str, str] | None:
    matches = list(TAIL_MARKER.finditer(text))
    if len(matches) != 1:
        return None  # missing, or duplicated -- either way not a shape this driver reasons about
    m = matches[0]
    return text[: m.start()], text[m.start():]


def slug_sections(suffix: str) -> dict[str, str] | None:
    """slug -> its whole `## D-<slug> — ...` section, or None if `suffix` is not *entirely*
    a sequence of such sections: nothing before the first heading, every top-level heading
    slug-shaped (never swallowed into the previous section's body), every section carrying a
    non-empty body and ending in a blank line, and no slug repeated within this one suffix."""
    if not suffix:
        return {}
    matches = list(SLUG_HEADING.finditer(suffix))
    if not matches or matches[0].start() != 0 or not suffix.endswith("\n\n"):
        return None
    if [m.start() for m in matches] != [m.start() for m in LEVEL_TWO_HEADING.finditer(suffix)]:
        return None
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(suffix)
        section = suffix[m.start():end]
        slug = m.group(1)
        if not section[m.end():].strip() or slug in sections:
            return None
        sections[slug] = section
    return sections


def try_fast_path(base: str, ours: str, theirs: str) -> str | None:
    parts = split_at_marker(base), split_at_marker(ours), split_at_marker(theirs)
    if any(p is None for p in parts):
        return None  # marker missing/duplicated on some side -- never seen, abstain
    (head_o, tail_o), (head_a, tail_a), (head_b, tail_b) = parts
    if tail_a != tail_o or tail_b != tail_o:
        return None  # Open-items section touched on some side
    if not head_a.startswith(head_o) or not head_b.startswith(head_o):
        return None  # not a pure trailing append (an existing section was edited)
    suffix_a, suffix_b = head_a[len(head_o):], head_b[len(head_o):]
    slugs_a, slugs_b = slug_sections(suffix_a), slug_sections(suffix_b)
    if slugs_a is None or slugs_b is None:
        return None  # appended content is not entirely complete, unambiguous decision sections
    if suffix_a == suffix_b:
        return head_o + suffix_a + tail_o  # both sides appended byte-identical content
    if set(slugs_a) & set(slugs_b):
        # Any slug named on both sides -- concatenating would either duplicate a section
        # verbatim or define the same slug twice, and a same-body match here is no safer:
        # slug_sections already proved each side's own suffix is internally duplicate-free,
        # but a name shared *across* sides is a collision this driver does not resolve.
        return None
    return head_o + suffix_a + suffix_b + tail_o


def main(argv: list[str]) -> int:
    o_path, a_path, b_path = argv[1], argv[2], argv[3]
    merged = try_fast_path(Path(o_path).read_text(), Path(a_path).read_text(), Path(b_path).read_text())
    if merged is not None:
        Path(a_path).write_text(merged)
        return 0
    result = subprocess.run(
        ["git", "merge-file", "-L", "ours", "-L", "base", "-L", "theirs", a_path, o_path, b_path]
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
