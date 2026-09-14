"""Citations a reader can follow (D-citation-links).

A report cites with plain `[n]` markers and a `## Sources` list, and that is what is
stored, hashed, critiqued and revised. Models quote it verbatim — a critic's span has to
be found in the report text (`triage._require_quote`) — so the stored artifact never
carries link syntax: a `[[3]](https://…)` in a sentence would be text no critic quoting
the sentence as prose could match.

This module adds the links on the way *out*, for human readers only. Everything here is
deterministic string work over the report's own structure — no model, no network — and
it runs at render time, so nothing it produces reaches a model, the event trail or
`report.md`:

* every body marker becomes a link per number (`[2-4]` → `[2]` `[3]` `[4]`), each to its
  bibliography entry's URL, as markdown whose visible text is still `[n]`;
* every URL in the `## Sources` section becomes an autolink, because the renderer's
  linkify is off;
* a marker link carries a text fragment (`#:~:text=…`) only when claim check recorded a
  `supported` verdict for that exact (number, sentence) pair, with the verbatim page span
  it pointed at, and no record for the pair says `contradicted`. A span nobody verified is
  never presented as the passage that backs a claim.

The sentence split, marker expansion and number→URL map are the ones `claimcheck.pairs`
uses, so a record's `(number, sentence)` key is the key computed here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from . import excerpt, fetch
from . import report as report_mod

#: Words kept from each end of a long span. Enough to be unambiguous on a page, few
#: enough that a small extraction difference inside the span does not break the match.
EDGE_WORDS = 5


@dataclass(frozen=True)
class VerifiedSpan:
    """A page passage claim check pointed at when it found a claim supported."""

    url: str
    span: str


#: (bibliography number, citing sentence) → the verified passage for that pair.
Spans = Mapping[tuple[int, str], VerifiedSpan]


# ------------------------------------------------------------------ fragments


def _encode(text: str) -> str:
    """Percent-encode one text-directive term. `,` and `&` are encoded by `quote` with no
    safe characters; `-` is not, and the spec reserves it as the prefix/suffix marker."""
    return quote(text, safe="").replace("-", "%2D")


def text_fragment(span: str) -> str:
    """The `:~:text=` directive for `span`, or `''` for a span with no words.

    Whitespace is collapsed. A span of at most `2 * EDGE_WORDS` words is matched whole;
    a longer one as `textStart,textEnd` from its first and last `EDGE_WORDS` words, which
    the browser resolves to the shortest range starting and ending there.
    """
    words = span.split()
    if not words:
        return ""
    if len(words) <= 2 * EDGE_WORDS:
        return f":~:text={_encode(' '.join(words))}"
    start = _encode(" ".join(words[:EDGE_WORDS]))
    end = _encode(" ".join(words[-EDGE_WORDS:]))
    return f":~:text={start},{end}"


def _is_pdf(url: str) -> bool:
    try:
        return urlsplit(url).path.lower().endswith(".pdf")
    except ValueError:
        return True


def deep_link(url: str, span: str | None) -> str:
    """`url` with a text fragment for `span`, or `url` unchanged.

    Unchanged for a missing span, a PDF (viewers ignore text fragments, so a fragment would
    promise a jump that never happens) and a URL that already carries a directive. A URL
    with a `#fragment` gets the directive appended to it: a second `#` would become part
    of the fragment and match nothing.
    """
    if not span or _is_pdf(url) or ":~:" in url:
        return url
    directive = text_fragment(span)
    if not directive:
        return url
    return f"{url}{directive}" if "#" in url else f"{url}#{directive}"


# ------------------------------------------------------------------ verified spans


def select_spans(records: Iterable[Any]) -> dict[tuple[int, str], VerifiedSpan]:
    """The one verified span per (number, sentence) pair, from claim-check records.

    `records` are `claimcheck.ClaimCheck.as_record()` payloads in sequence order; the first
    `supported` verdict with a non-empty span wins, so the choice is deterministic. A pair
    any record calls `contradicted` gets nothing: checkers disagreeing about a passage is
    not a passage to send a reader to. Malformed entries are skipped, never raised — a
    missing deep link is the whole cost of a bad record.
    """
    chosen: dict[tuple[int, str], VerifiedSpan] = {}
    contradicted: set[tuple[int, str]] = set()
    for record in records:
        verdicts = record.get("verdicts") if isinstance(record, Mapping) else None
        if not isinstance(verdicts, list):
            continue
        for verdict in verdicts:
            if not isinstance(verdict, Mapping):
                continue
            number, sentence, url = verdict.get("number"), verdict.get("sentence"), verdict.get("url")
            if isinstance(number, bool) or not isinstance(number, int):
                continue
            if not isinstance(sentence, str) or not isinstance(url, str):
                continue
            key = (number, sentence)
            if verdict.get("verdict") == "contradicted":
                contradicted.add(key)
            elif verdict.get("verdict") == "supported":
                span = verdict.get("support_span")
                if isinstance(span, str) and span.strip() and key not in chosen:
                    chosen[key] = VerifiedSpan(url, span)
    return {key: span for key, span in chosen.items() if key not in contradicted}


# ------------------------------------------------------------------ protected text

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
#: An inline link or image, allowing one level of brackets in its text (`[see [1]](u)`)
#: and of parentheses in its destination.
_INLINE_LINK = re.compile(r"!?\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]\((?:[^()\n]|\([^()\n]*\))*\)")
_AUTOLINK = re.compile(r"<[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*>")
#: A numeric link reference definition (`[3]: https://…`). A body `[3]` is then already
#: a link, and wrapping it again would nest a link inside a link, which CommonMark refuses.
_NUMERIC_REF_DEF = re.compile(r"^ {0,3}\[(\d+)\]:", re.M)


def _protected(text: str) -> list[tuple[int, int]]:
    """Character ranges no rewrite may touch: fenced code, code spans, existing links."""
    ranges: list[tuple[int, int]] = []
    offset = 0
    fence: tuple[str, int, int] | None = None  # (char, length, start)
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)), offset)
        elif (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= fence[1]
            and not line[match.end() :].strip()
        ):
            ranges.append((fence[2], offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        ranges.append((fence[2], len(text)))
    for pattern in (_CODE_SPAN, _INLINE_LINK, _AUTOLINK):
        ranges.extend(m.span() for m in pattern.finditer(text))
    return ranges


def _inside(position: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < end and position < stop for start, stop in ranges)


# ------------------------------------------------------------------ rewriting


def _destination(url: str) -> str:
    """`url` as a markdown link destination. `|` is encoded so a link in a table cell does
    not split the row, `\\` so it is not read as an escape; a URL with a parenthesis or a
    space takes the angle-bracket form, which needs no balancing."""
    url = url.replace("\\", "%5C").replace("|", "%7C")
    return f"<{url}>" if any(c in url for c in "() ") else url


def _numbers(marker: str) -> list[int] | None:
    """The numbers a marker cites, ascending — or None for a range `excerpt` would not
    expand (`[1-400]`), which is left exactly as written rather than shown as `[1]`."""
    for part in re.split(r"[,;]", marker):
        span = excerpt._RANGE.search(part)
        if span:
            lo, hi = int(span.group(1)), int(span.group(2))
            if not lo <= hi <= lo + excerpt._MAX_RANGE:
                return None
    return sorted(excerpt._cited(marker))


def _marker_markdown(
    marker: str, sentence: str, by_number: Mapping[int, str], spans: Spans, referenced: set[int]
) -> str | None:
    numbers = _numbers(marker)
    if not numbers or any(n in referenced for n in numbers):
        return None
    if not any(n in by_number for n in numbers):
        return None
    parts: list[str] = []
    for number in numbers:
        url = by_number.get(number)
        if url is None:
            parts.append(f"[{number}]")
            continue
        verified = spans.get((number, sentence))
        span = verified.span if verified is not None and verified.url == url else None
        parts.append(f"[[{number}]]({_destination(deep_link(url, span))})")
    return "".join(parts)


def _sentences(paragraph: str) -> Iterable[tuple[int, str]]:
    """(offset, sentence) for each sentence of `paragraph`, split and stripped exactly as
    `claimcheck.pairs` splits it."""
    cursor = 0
    for piece in excerpt._SENTENCE_END.split(paragraph):
        at = paragraph.find(piece, cursor)
        cursor = at + len(piece)
        stripped = piece.strip()
        if stripped:
            yield at + (len(piece) - len(piece.lstrip())), stripped


def linked_markdown(report: str, spans: Spans | None = None) -> str:
    """`report` with its citations as links. Visible text is unchanged apart from a
    multi-number marker becoming one `[n]` per number.

    A marker whose numbers have no entry URL, a marker inside code or an existing link,
    a marker preceded by a backslash, and every marker inside `## Sources` stay as
    written. With no `spans` the marker links are plain URLs.
    """
    if not report:
        return report
    spans = spans or {}
    by_number: dict[int, str] = {}
    for url, listed in excerpt.entry_numbers(report).items():
        for number in listed:
            by_number.setdefault(number, url)
    referenced = {int(n) for n in _NUMERIC_REF_DEF.findall(report)}
    protected = _protected(report)

    edits: list[tuple[int, int, str]] = []
    structure = report_mod.parse(report)
    cursor = 0
    for paragraph in structure.paragraphs:
        at = report.find(paragraph.text, cursor)
        if at < 0:
            continue
        cursor = at + len(paragraph.text)
        titles = structure.section_titles
        title = titles[paragraph.section] if paragraph.section < len(titles) else ""
        if fetch._SOURCES_HEADING.match(f"## {title}"):
            edits.extend(_source_url_edits(report, at, paragraph.text, protected))
            continue
        for offset, sentence in _sentences(paragraph.text):
            base = at + offset
            for match in excerpt._MARKER.finditer(sentence):
                start, end = base + match.start(), base + match.end()
                if _inside(start, end, protected) or report[start - 1 : start] == "\\":
                    continue
                replacement = _marker_markdown(match.group(1), sentence, by_number, spans, referenced)
                if replacement is not None:
                    edits.append((start, end, replacement))

    out = report
    for start, end, replacement in sorted(edits, reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def _source_url_edits(
    report: str, at: int, paragraph: str, protected: list[tuple[int, int]]
) -> Iterable[tuple[int, int, str]]:
    for match in fetch._URL.finditer(paragraph):
        url = fetch._clean_url(match.group(0))
        start = at + match.start()
        end = start + len(url)
        if not url or _inside(start, end, protected):
            continue
        yield start, end, f"<{url}>"
