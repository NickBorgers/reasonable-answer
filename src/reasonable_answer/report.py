"""Report structure: stable loci and content hashing.

Critics must be able to point at *where* a defect is without emitting free text
(RB-007), so every report is rendered with explicit `[S<n>.P<m>]` markers and a
locus is validated against the real structure. A locus outside the structure is a
schema violation, which fails the lens (fail-closed).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .schemas import StructuralRef

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class Paragraph:
    section: int
    paragraph: int
    text: str


@dataclass(frozen=True)
class Structure:
    paragraphs: tuple[Paragraph, ...]
    section_titles: tuple[str, ...]

    def contains(self, ref: StructuralRef) -> bool:
        return any(
            p.section == ref.section and p.paragraph == ref.paragraph for p in self.paragraphs
        )

    def text_at(self, ref: StructuralRef) -> str | None:
        for p in self.paragraphs:
            if p.section == ref.section and p.paragraph == ref.paragraph:
                return p.text
        return None


def parse(report: str) -> Structure:
    """Section 0 is any preamble before the first heading; paragraphs are blank-line
    separated blocks, numbered from 1 within their section."""
    titles: list[str] = ["(preamble)"]
    paragraphs: list[Paragraph] = []
    section = 0
    para_no = 0

    for block in re.split(r"\n\s*\n", report.strip()):
        block = block.strip()
        if not block:
            continue
        heading = _HEADING.match(block.splitlines()[0])
        if heading:
            section += 1
            para_no = 0
            titles.append(heading.group(2).strip())
            rest = "\n".join(block.splitlines()[1:]).strip()
            if rest:
                para_no += 1
                paragraphs.append(Paragraph(section, para_no, rest))
            continue
        para_no += 1
        paragraphs.append(Paragraph(section, para_no, block))

    return Structure(tuple(paragraphs), tuple(titles))


def render_with_loci(report: str) -> str:
    """The exact rendering shown to critics — every paragraph carries its locus."""
    structure = parse(report)
    lines: list[str] = []
    current = -1
    for p in structure.paragraphs:
        if p.section != current:
            current = p.section
            title = structure.section_titles[p.section]
            lines.append(f"\n=== SECTION {p.section}: {title} ===")
        lines.append(f"[S{p.section}.P{p.paragraph}] {p.text}")
    return "\n\n".join(line.strip("\n") for line in lines).strip()


def artifact_hash(report: str) -> str:
    """Byte-level identity of an artifact. Any regeneration or polish yields a new
    hash, which resets the clean-record set (RC-002)."""
    return hashlib.sha256(report.encode("utf-8")).hexdigest()
