"""Reads the decision registry the way the shipped gate reads it.

The registry is an index plus one file per decision (D-decision-per-file): `docs/decisions.md`
carries the identifier scheme, the `RA-*`/`RB-*`/`RC-*`/`RG-*` finding tables, the RA-019 test
matrix and the open items, while `docs/decisions/D-<slug>.md` holds the prose for `D-<slug>`.
Both definition forms are equally definitions — the `## D-<slug> — …` heading that opens a
decision file, and a `| D-<slug> | … |` row in one of the index tables.

Two tests need that reading and previously each had its own copy of it against a single file
(`test_citation_resolution.py`, `test_reviewer_prompt_ranges.py`). One copy here keeps them from
disagreeing about what "defined" means, which is the thing that would make either test quietly
vacuous. `scripts/validate-decision-numbers.sh` is the shell implementation of the same two
forms; `test_decision_numbers.py` drives it directly.

Not a test module: no `test_` prefix, so pytest imports it without collecting it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The registry index — doctrine, finding tables, test matrix, open items.
INDEX = REPO_ROOT / "docs" / "decisions.md"
#: One file per decision, each named for the slug it defines.
DECISIONS_DIR = REPO_ROOT / "docs" / "decisions"

#: Opens a decision file. Anchored, and requires the em-dash title separator.
_HEADING_RE = re.compile(r"^## (D-[a-z0-9-]+) —", re.M)
#: An index-table row whose *first* cell is the slug. The old->new mapping table does not
#: match: its first cell is an old numeric id and the slug sits in the second column.
_TABLE_RE = re.compile(r"^\| (D-[a-z0-9-]+) \|", re.M)


def decision_files() -> list[Path]:
    """Every per-decision file, sorted for a deterministic failure message."""
    return sorted(DECISIONS_DIR.glob("*.md"))


def defined_slugs() -> set[str]:
    """Slugs the registry defines, in either the heading or the index-table form."""
    slugs = {m.group(1) for m in _TABLE_RE.finditer(INDEX.read_text(encoding="utf-8"))}
    for path in decision_files():
        slugs |= {m.group(1) for m in _HEADING_RE.finditer(path.read_text(encoding="utf-8"))}
    return slugs


def registry_text() -> str:
    """The whole registry as one string, for scanning IDs that are not decision slugs.

    Finding ids (`RA-001`, `RB-010`) stayed numeric and are cited from both the index tables
    and the decision prose, so a check on their range has to read both halves — reading the
    index alone would let the stated max drift below a finding only a decision file mentions.
    """
    parts = [INDEX.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in decision_files()]
    return "\n".join(parts)
