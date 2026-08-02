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
# Reuses scripts/validate-decision-numbers.sh's prose-heading regex verbatim, so a
# "same slug, different body" collision is judged by the same rule that check enforces.
SLUG_HEADING = re.compile(r"^## (D-[a-z0-9-]+) ", re.MULTILINE)


def split_at_marker(text: str) -> tuple[str, str] | None:
    m = TAIL_MARKER.search(text)
    return None if m is None else (text[: m.start()], text[m.start():])


def slug_sections(suffix: str) -> dict[str, str]:
    """slug -> its full section body, from one `## D-<slug>` heading to the next (or EOF)."""
    matches = list(SLUG_HEADING.finditer(suffix))
    return {
        m.group(1): suffix[m.start(): (matches[i + 1].start() if i + 1 < len(matches) else len(suffix))]
        for i, m in enumerate(matches)
    }


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
    if suffix_a == suffix_b:
        return head_o + suffix_a + tail_o  # both sides appended byte-identical content
    slugs_a, slugs_b = slug_sections(suffix_a), slug_sections(suffix_b)
    for slug, body in slugs_a.items():
        if slug in slugs_b and slugs_b[slug] != body:
            return None  # genuine same-slug collision -- abstain, do not silently pick one
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
