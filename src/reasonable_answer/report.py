"""Report structure: stable loci, content hashing, and revision scope.

Critics must be able to point at *where* a defect is without emitting free text
(RB-007), so every report is rendered with explicit `[S<n>.P<m>]` markers and a
locus is validated against the real structure. A locus outside the structure is a
schema violation, which fails the lens (fail-closed).

`revision_scope` reads the same structure from the other end: it measures which
paragraphs a revision actually touched, against the loci the fix tasks named
(D-scoped-revision).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Iterable
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

    @property
    def full_text(self) -> str:
        """Every paragraph, for span checks that may legitimately quote elsewhere."""
        return "\n\n".join(p.text for p in self.paragraphs)

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


# ------------------------------------------------------------------ revision scope


@dataclass(frozen=True)
class ScopeReport:
    """What a revision actually touched, against what it was asked to touch.

    Measurement only (D-scoped-revision). Nothing in the graph rejects a draft on the
    strength of this: it rides the `generate` audit event so the patch-vs-rewrite
    question can be answered from `audit.json` instead of from an impression, and an
    enforcing tier is only worth building if these numbers say the prompt does not hold.
    """

    changed: tuple[StructuralRef, ...]
    out_of_scope: tuple[StructuralRef, ...]
    untouched_defect_loci: tuple[StructuralRef, ...]
    #: Changed paragraphs no task named whose previous text restated a flagged claim —
    #: the edits D-claim-scoped-patch licenses, counted apart from `out_of_scope` so
    #: the number that means "re-rolled text nobody complained about" keeps meaning it.
    restated: tuple[StructuralRef, ...] = ()

    @property
    def in_scope_count(self) -> int:
        return len(self.changed) - len(self.out_of_scope) - len(self.restated)

    def as_event_fields(self) -> dict[str, int]:
        return {
            "changed_paragraphs": len(self.changed),
            "in_scope": self.in_scope_count,
            "restated": len(self.restated),
            "out_of_scope": len(self.out_of_scope),
            "defect_loci_untouched": len(self.untouched_defect_loci),
        }


def _scope_key(text: str) -> str:
    """The identity a paragraph is matched on across a revision.

    Runs of whitespace collapse, because a writer that re-wraps a paragraph at a
    different column has not edited it and counting that as an out-of-scope change
    would bury the signal this exists to produce. Nothing else is folded — case,
    emphasis and punctuation are all things a revision can genuinely change, and
    folding them would hide real edits.
    """
    return re.sub(r"\s+", " ", text).strip()


#: The shortest run of consecutive words a paragraph must share with a flagged claim to
#: count as restating it. Restatements in this pipeline are near-verbatim — the frame
#: asks for the same finding in the conclusion, the key findings and the body, and
#: writers copy — so a long common run is the signature, and eight words is longer than
#: any stock phrase ("in the United States, the number of") while shorter than the
#: claim spans critics quote in practice, which run a sentence. A span with fewer words
#: must match whole.
RESTATEMENT_MIN_WORDS = 8

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def restates(paragraph: str, claim_span: str) -> bool:
    """Whether `paragraph` restates the claim quoted in `claim_span` (D-claim-scoped-patch).

    Compared on words — lower-cased, punctuation dropped — because a restatement
    changes the subject ("Its efficiency" for "EMALS's efficiency"), a tense or a
    comma while carrying the claim across verbatim. Longest common *run*, not shared
    vocabulary: two paragraphs about the same topic share words, only a restatement
    shares a sentence.
    """
    span = _words(claim_span)
    if not span:
        return False
    para = _words(paragraph)
    need = min(RESTATEMENT_MIN_WORDS, len(span))
    match = difflib.SequenceMatcher(None, span, para, autojunk=False).find_longest_match(
        0, len(span), 0, len(para)
    )
    return match.size >= need


def _ref(p: Paragraph) -> StructuralRef:
    return StructuralRef(section=p.section, paragraph=p.paragraph)


def revision_scope(
    previous: str,
    revised: str,
    defect_loci: Iterable[StructuralRef],
    claim_spans: Iterable[str] = (),
) -> ScopeReport:
    """Which paragraphs of `previous` the revision changed, and which were in scope.

    The diff is over paragraph *content*, never over locus numbers. Inserting or
    deleting a paragraph renumbers every locus after it, so comparing `S4.P2` in the
    old report against `S4.P2` in the new one would report the entire tail of the
    document as rewritten the first time a writer adds a paragraph — which is a thing
    fix tasks routinely ask for.

    An insertion is attributed to the paragraphs it sits *between*, and counts as in
    scope when either neighbour was named by a task: "add a sentence acknowledging X"
    is very often honoured as a new paragraph next to the one that was flagged.

    A changed paragraph no task named, whose *previous* text restated one of the
    `claim_spans` (D-claim-scoped-patch), is `restated` rather than `out_of_scope`: the
    patch licence asks the writer to carry a fix to every copy of the claim, and the
    measurement has to be able to tell that from re-rolling text nobody complained
    about. Matching is against the old text, because that is where the copy was; what
    the writer turned it into is not the question. With no spans given the report is
    exactly what it was.
    """
    old = parse(previous).paragraphs
    new = parse(revised).paragraphs
    loci = {(r.section, r.paragraph) for r in defect_loci}
    spans = [span for span in claim_spans if span]

    def in_scope(p: Paragraph) -> bool:
        return (p.section, p.paragraph) in loci

    def restated(p: Paragraph) -> bool:
        return any(restates(p.text, span) for span in spans)

    matcher = difflib.SequenceMatcher(
        a=[_scope_key(p.text) for p in old],
        b=[_scope_key(p.text) for p in new],
        autojunk=False,
    )

    changed: list[StructuralRef] = []
    out_of_scope: list[StructuralRef] = []
    restated_refs: list[StructuralRef] = []
    touched: set[tuple[int, int]] = set()

    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            # Zero-width on the old side: attribute it to the boundary it landed on.
            neighbours = []
            if i1:
                neighbours.append(old[i1 - 1])
            if i1 < len(old):
                neighbours.append(old[i1])
            if not neighbours:
                continue
            changed.append(_ref(neighbours[-1]))
            named = [p for p in neighbours if in_scope(p)]
            touched.update((p.section, p.paragraph) for p in named)
            if not named:
                out_of_scope.append(_ref(neighbours[-1]))
            continue
        for p in old[i1:i2]:
            changed.append(_ref(p))
            if in_scope(p):
                touched.add((p.section, p.paragraph))
            elif restated(p):
                restated_refs.append(_ref(p))
            else:
                out_of_scope.append(_ref(p))

    untouched = [
        StructuralRef(section=s, paragraph=p) for (s, p) in sorted(loci - touched)
    ]
    return ScopeReport(
        changed=tuple(changed),
        out_of_scope=tuple(out_of_scope),
        untouched_defect_loci=tuple(untouched),
        restated=tuple(restated_refs),
    )
