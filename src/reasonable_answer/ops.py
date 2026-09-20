"""A revision as operations on labelled paragraphs, spliced by code (D-ops-revision).

Under `revision.mode: patch` the writer returns the whole report, and every rule that
protects the text it was not asked to touch — return the rest byte-identical, keep every
`[n]` marker on a kept claim, delete a Sources entry only when nothing cites it — is a
sentence in the prompt that the census and scope measurements can only check after the
fact. Under `revision.mode: ops` the writer returns *operations* on the paragraphs of the
draft it was shown, each named by the same `[S<n>.P<m>]` label the critics read, and
this module builds the next report. What the patch prompt asked for, the splice
enforces: a paragraph no operation names is byte-identical by construction; a heading
cannot be addressed; a Sources change that would leave a body marker citing nothing is
refused.

Two pure functions and a convenience:

* `parse_ops` reads the line protocol — lenient about the things writers were seen to
  vary (a code fence around the whole reply, `insert_after` for `insert-after`, brackets
  around the label, `task=` for `tasks=`, a missing final `@@ end`) and strict about
  nothing else, since a protocol that fails a whole reply on one bad line throws away
  the operations that were fine.
* `splice` applies operations to a report and returns the new text with a full account,
  as integers, of what it applied and what it refused.
* `revise` is the two together, returning `None` when nothing could be applied — the
  caller treats that as a failed writer attempt.

No I/O, no model call, no text on any event: every field in `Splice.fields` is a count
(RA-016).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from . import excerpt
from . import report as report_mod
from .fetch import _SOURCES_HEADING
from .schemas import StructuralRef

_HEADER = re.compile(
    r"^\s*@@\s*(replace|delete|insert[-_ ]?after)\s+\[?S(\d+)\.P(\d+)\]?"
    r"(?:\s+tasks?\s*[:=]\s*(.*?))?\s*$",
    re.IGNORECASE,
)
_END = re.compile(r"^\s*@@\s*end\s*$", re.IGNORECASE)
_PROTOCOL_LINE = re.compile(r"^\s*@@")
_TASK_ID = re.compile(r"^T\d+$")
_LABEL_ECHO = re.compile(r"^\[S\d+\.P\d+\]\s*")
_HEADING = report_mod._HEADING

#: Every count the parser and the splice report, so a `generate` event under ops mode
#: always carries the whole set and an absent key can only mean "not ops mode". The
#: census-gated repair turn's own splice reports the same set under `REPAIR_PREFIX`.
PARSE_FIELDS = (
    "ops_fenced",
    "ops_bad_headers",
    "ops_stray_lines",
    "ops_unterminated",
    "ops_without_task",
)
SPLICE_FIELDS = (
    "ops_total",
    "ops_applied",
    "ops_replace",
    "ops_delete",
    "ops_insert",
    "ops_refused_locus",
    "ops_refused_duplicate",
    "ops_refused_heading",
    "ops_refused_empty",
    "ops_refused_dangling",
    "ops_label_echo",
    "ops_heading_echo",
)


#: Prefix under which the repair turn's splice counts ride the `generate` event, so
#: the drafting call's counts and the repair's never share a key (D-ops-revision).
REPAIR_PREFIX = "repair_"


@dataclass(frozen=True)
class Op:
    kind: str
    locus: StructuralRef
    tasks: tuple[str, ...]
    #: the new paragraph text; `""` for a delete
    text: str


@dataclass(frozen=True)
class ParsedOps:
    ops: tuple[Op, ...]
    fields: dict[str, int]


@dataclass(frozen=True)
class Splice:
    text: str
    fields: dict[str, int]


# ------------------------------------------------------------------------ parsing


def _unfenced(reply: str) -> tuple[str, int]:
    """The reply without a code fence wrapped around the whole of it."""
    body = reply.strip()
    if not body.startswith("```"):
        return body, 0
    lines = body.splitlines()
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip(), 1


def _task_ids(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    found = []
    for token in re.split(r"[,\s]+", raw.strip()):
        token = token.strip().upper()
        if _TASK_ID.match(token) and token not in found:
            found.append(token)
    return tuple(found)


def _header(line: str) -> tuple[str, StructuralRef, tuple[str, ...]] | None:
    match = _HEADER.match(line)
    if match is None:
        return None
    kind = match.group(1).lower()
    if kind != "delete" and kind != "replace":
        kind = "insert-after"
    try:
        locus = StructuralRef(section=int(match.group(2)), paragraph=int(match.group(3)))
    except ValidationError:
        # A label outside the schema's bounds is not a label the draft could have shown.
        return None
    return kind, locus, _task_ids(match.group(4))


def parse_ops(reply: str) -> ParsedOps:
    """Read every operation block out of a writer's reply.

    A line beginning `@@` is protocol: a header opens a block, `@@ end` closes it, and any
    other `@@` line is counted as a bad header and dropped. Non-blank text outside a block
    is counted as stray and dropped. A header met while a block is open, or the end of the
    reply with a block open, closes the open block (counted as unterminated) rather than
    losing it. A `delete` block's body is ignored.
    """
    body, fenced = _unfenced(reply)
    fields = dict.fromkeys(PARSE_FIELDS, 0)
    fields["ops_fenced"] = fenced

    ops: list[Op] = []
    current: tuple[str, StructuralRef, tuple[str, ...]] | None = None
    buffer: list[str] = []

    def close() -> None:
        nonlocal current, buffer
        assert current is not None
        kind, locus, tasks = current
        text = "" if kind == "delete" else "\n".join(buffer).strip()
        ops.append(Op(kind, locus, tasks, text))
        current = None
        buffer = []

    for line in body.splitlines():
        if _PROTOCOL_LINE.match(line):
            header = _header(line)
            if header is not None:
                if current is not None:
                    fields["ops_unterminated"] += 1
                    close()
                current = header
                continue
            if _END.match(line):
                if current is not None:
                    close()
                else:
                    fields["ops_bad_headers"] += 1
                continue
            fields["ops_bad_headers"] += 1
            continue
        if current is None:
            if line.strip():
                fields["ops_stray_lines"] += 1
            continue
        buffer.append(line)
    if current is not None:
        fields["ops_unterminated"] += 1
        close()

    fields["ops_without_task"] = sum(1 for op in ops if not op.tasks)
    return ParsedOps(tuple(ops), fields)


# ------------------------------------------------------------------------- splice


def _section_titles(bs: Sequence[report_mod.Block]) -> dict[int, str]:
    titles: dict[int, str] = {}
    for b in bs:
        if b.kind == "heading":
            match = _HEADING.match(b.text)
            titles[b.section] = match.group(2).strip() if match else ""
    return titles


def _sources_sections(bs: Sequence[report_mod.Block]) -> set[int]:
    return {b.section for b in bs if b.kind == "heading" and _SOURCES_HEADING.match(b.text)}


def _normalised(text: str, section_title: str, fields: dict[str, int]) -> str | None:
    """A replacement's text as it will sit in the report, or `None` if it is refused.

    Writers were seen to copy the `[S<n>.P<m>]` label they were shown into the new text,
    and once to open the new text with the section's own heading; both are stripped and
    counted, never refused. A heading anywhere in what remains *is* refused: the labels
    critics read are numbered by heading, so new text that adds one would renumber every
    paragraph after it, and a heading is exactly what no operation may touch.
    """
    text = text.strip()
    stripped = _LABEL_ECHO.sub("", text, count=1)
    if stripped != text:
        fields["ops_label_echo"] += 1
        text = stripped.strip()
    lines = text.splitlines()
    if lines:
        heading = _HEADING.match(lines[0])
        if heading and heading.group(2).strip().casefold() == section_title.casefold():
            fields["ops_heading_echo"] += 1
            text = "\n".join(lines[1:]).strip()
    if not text:
        fields["ops_refused_empty"] += 1
        return None
    parts = report_mod.blocks(text)
    if any(b.kind == "heading" for b in parts):
        fields["ops_refused_heading"] += 1
        return None
    return "\n\n".join(b.text for b in parts)


def _render(
    bs: Sequence[report_mod.Block],
    replaced: dict[int, str | None],
    inserted: dict[int, list[str]],
) -> str:
    out: list[str] = []
    for i, b in enumerate(bs):
        if i in replaced:
            if replaced[i] is not None:
                out.append(replaced[i])  # type: ignore[arg-type]
        else:
            out.append(b.text)
        out.extend(inserted.get(i, ()))
    return "\n\n".join(out)


def splice(previous: str, ops: Sequence[Op]) -> Splice:
    """Apply `ops` to `previous` and return the new report with a count of everything
    that happened.

    Order of work: every operation is normalised and checked against the draft's real
    loci; a second `replace`/`delete` on a locus already replaced or deleted is refused
    (the first wins), while several `insert-after` on one locus are all kept in the
    order written. Every accepted operation outside the `## Sources` section, plus every
    `insert-after` inside it, is applied. Then each Sources `replace`/`delete` is tried
    in the order written and kept only if the body's count of markers citing no entry
    does not rise — "delete an entry only when nothing cites it", generalised to a
    Sources list that sits under one label, using only the public citation census so
    `[n]`, `n.` and `n)` entries all count. A body edit that removes the last marker
    citing an entry therefore licenses deleting that entry in the same reply.

    The result is `report.canonical(previous)` with the operations applied: blocks
    joined by one blank line, headings on their own line, no trailing newline. Text
    inside an untouched paragraph is byte-identical to the draft.
    """
    bs = report_mod.blocks(previous)
    fields = dict.fromkeys(SPLICE_FIELDS, 0)
    index = {(b.section, b.paragraph): i for i, b in enumerate(bs) if b.kind == "paragraph"}
    titles = _section_titles(bs)
    sources = _sources_sections(bs)

    replaced: dict[int, str | None] = {}
    inserted: dict[int, list[str]] = {}
    deferred: list[tuple[int, str | None]] = []

    fields["ops_total"] = len(ops)
    for op in ops:
        fields[{"replace": "ops_replace", "delete": "ops_delete", "insert-after": "ops_insert"}[op.kind]] += 1
        key = (op.locus.section, op.locus.paragraph)
        i = index.get(key)
        if i is None:
            fields["ops_refused_locus"] += 1
            continue
        if op.kind == "delete":
            text: str | None = None
        else:
            text = _normalised(op.text, titles.get(op.locus.section, ""), fields)
            if text is None:
                continue
        if op.kind == "insert-after":
            inserted.setdefault(i, []).append(text)  # type: ignore[arg-type]
            fields["ops_applied"] += 1
            continue
        if i in replaced or any(j == i for j, _ in deferred):
            fields["ops_refused_duplicate"] += 1
            continue
        if op.locus.section in sources:
            deferred.append((i, text))
        else:
            replaced[i] = text
            fields["ops_applied"] += 1

    candidate = _render(bs, replaced, inserted)
    baseline = excerpt.citation_census(candidate)["dangling_markers"]
    for i, text in deferred:
        trial = _render(bs, {**replaced, i: text}, inserted)
        dangling = excerpt.citation_census(trial)["dangling_markers"]
        if dangling > baseline:
            fields["ops_refused_dangling"] += 1
            continue
        replaced[i] = text
        baseline = dangling
        fields["ops_applied"] += 1

    return Splice(_render(bs, replaced, inserted), fields)


def revise(previous: str, reply: str) -> Splice | None:
    """Parse `reply` and splice it into `previous`. `None` when no operation could be
    parsed or none could be applied: there is then no new draft, and the caller records
    the attempt as failed."""
    parsed = parse_ops(reply)
    if not parsed.ops:
        return None
    spliced = splice(previous, parsed.ops)
    if spliced.fields["ops_applied"] == 0:
        return None
    return Splice(spliced.text, {**parsed.fields, **spliced.fields})
