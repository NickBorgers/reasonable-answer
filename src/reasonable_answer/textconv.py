"""Convert PDF, .docx and HTML bytes into markdown.

Split out of :mod:`.ingest` because two callers need it and they cannot both import
it from there. Seed ingest converts a user-supplied document; citation verification
converts a cited PDF — and :mod:`.fetch` is what `ingest` imports to do its own
egress, so `fetch -> ingest` would close a cycle.

Errors are :class:`ConversionError`. `ingest` re-raises them as `IngestError`, which
is the type the CLI and web edges render to a user; `fetch` maps them onto a
:class:`~.fetch.SourceOutcome` instead. Neither meaning belongs in here.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from io import BytesIO

log = logging.getLogger(__name__)

#: Elements whose contents are not page prose. Shared with :mod:`.fetch`, which parses
#: HTML for a different purpose (visible text for a critic, not markdown structure) but
#: has exactly the same list of things that are not text.
SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})


class ConversionError(ValueError):
    """A document this cannot read, described in terms a user could act on."""


# ---------------------------------------------------------------------------- html

class _MarkdownExtractor(HTMLParser):
    """HTML to markdown, shallow on purpose.

    A sibling of `fetch._TextExtractor` rather than a mode on it: that class feeds
    citation verification and wants flat text, and coupling the two would let a bug in
    this converter degrade the evidence lens.

    Out of scope, and it should stay that way: tables, images, nested lists past one
    level, inline emphasis. What must survive is headings and a `## Sources` list of
    links — everything downstream depends on those two and on nothing else here.
    """

    _HEADINGS = {f"h{n}": n for n in range(1, 7)}
    _BREAKS = {"p", "div", "br", "tr", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._heading = 0
        self._prefix = ""
        self._quote = 0
        self._pre = 0
        self._href: str | None = None
        self._link: list[str] = []
        self._ol_counter: list[int | None] = []
        self._was_item = False

    # -- block assembly

    def _flush(self) -> None:
        text = "".join(self._buf)
        self._buf.clear()
        text = text.strip() if not self._pre else text.strip("\n")
        if not text:
            return
        if not self._pre:
            text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
            text = re.sub(r"\s{2,}", " ", text).strip()
        if not text:
            return
        if self._heading:
            text = "#" * self._heading + " " + text
        elif self._prefix:
            text = self._prefix + text
        if self._quote and not self._heading:
            text = "> " + text
        # Consecutive list items are one block, so a list reads as a list and becomes a
        # single locus rather than one per bullet.
        if self._prefix and self._was_item and self.blocks:
            self.blocks[-1] += "\n" + text
        else:
            self.blocks.append(text)
        self._was_item = bool(self._prefix)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self._HEADINGS:
            self._flush()
            self._heading = self._HEADINGS[tag]
        elif tag == "li":
            self._flush()
            counter = self._ol_counter[-1] if self._ol_counter else None
            if counter is None:
                self._prefix = "- "
            else:
                self._prefix = f"{counter}. "
                self._ol_counter[-1] = counter + 1
        elif tag == "ol":
            self._flush()
            self._ol_counter.append(1)
        elif tag == "ul":
            self._flush()
            self._ol_counter.append(None)
        elif tag == "blockquote":
            self._flush()
            self._quote += 1
        elif tag == "pre":
            self._flush()
            self._pre += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            # Anchors and javascript: targets carry nothing a critic can verify.
            self._href = href if href and not href.lower().startswith(("javascript:", "#")) else None
            self._link = []
        elif tag in self._BREAKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in self._HEADINGS:
            self._flush()
            self._heading = 0
        elif tag == "li":
            self._flush()
            self._prefix = ""
        elif tag in ("ol", "ul"):
            self._flush()
            if self._ol_counter:
                self._ol_counter.pop()
        elif tag == "blockquote":
            self._flush()
            self._quote = max(0, self._quote - 1)
        elif tag == "pre":
            self._flush()
            self._pre = max(0, self._pre - 1)
        elif tag == "a":
            text = "".join(self._link).strip()
            self._buf.append(f"[{text}]({self._href})" if self._href and text else text)
            self._href = None
            self._link = []
        elif tag in self._BREAKS:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        (self._link if self._href is not None else self._buf).append(data)

    @property
    def markdown(self) -> str:
        self._flush()
        return "\n\n".join(self.blocks)


def html_to_markdown(html: str) -> str:
    parser = _MarkdownExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # malformed markup: keep whatever parsed
        log.debug("seed html parse stopped early: %s", exc)
    return parser.markdown


# ---------------------------------------------------------------------------- docx

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_HEADING_STYLE = re.compile(r"^heading\s?(\d)$", re.IGNORECASE)


def docx_to_markdown(data: bytes, max_uncompressed: int) -> str:
    """A .docx is a zip of XML, so this needs no dependency — and Word's heading
    styles map onto markdown headings exactly, which makes it the highest-fidelity
    conversion here.

    On XXE: stdlib ElementTree does not resolve external entities or fetch DTDs, so the
    classic vectors do not apply; `defusedxml` would be belt-and-braces for a residual
    entity-expansion risk that the uncompressed-size guard already bounds.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            total = sum(i.file_size for i in zf.infolist())
            if total > max_uncompressed:
                # Checked before any read: the archive may have come from a URL.
                raise ConversionError(
                    f"the .docx expands to {total} bytes, over the {max_uncompressed} limit"
                )
            try:
                document = zf.read("word/document.xml")
            except KeyError as exc:
                raise ConversionError(
                    "not a .docx (no word/document.xml — a legacy .doc, or renamed?)"
                ) from exc
            rels = _docx_rels(zf)
    except zipfile.BadZipFile as exc:
        raise ConversionError("not a readable .docx (bad zip archive)") from exc

    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise ConversionError(f"the .docx contains malformed XML: {exc}") from exc

    blocks: list[str] = []
    # `iter` also reaches paragraphs inside tables, flattening them into prose. Lossy,
    # but a table's text is better read as paragraphs than dropped.
    for para in root.iter(f"{_W}p"):
        text = _docx_paragraph_text(para, rels)
        if not text.strip():
            continue
        props = para.find(f"{_W}pPr")
        blocks.append(_docx_prefix(props) + text.strip())
    return "\n\n".join(blocks)


def _docx_prefix(props: ET.Element | None) -> str:
    if props is None:
        return ""
    style = props.find(f"{_W}pStyle")
    name = (style.get(f"{_W}val") or "") if style is not None else ""
    if name.lower() == "title":
        return "# "
    match = _HEADING_STYLE.match(name)
    if match:
        return "#" * min(6, max(1, int(match.group(1)))) + " "
    if props.find(f"{_W}numPr") is not None:
        # numbering.xml would say whether it is a bullet or a number; a bullet is close
        # enough, and `report.parse` cares about neither.
        return "- "
    return ""


def _docx_paragraph_text(para: ET.Element, rels: dict[str, str]) -> str:
    parts: list[str] = []
    for node in para.iter():
        tag = node.tag
        if tag == f"{_W}t":
            parts.append(node.text or "")
        elif tag == f"{_W}tab":
            parts.append(" ")
        elif tag == f"{_W}br":
            parts.append("\n")
        elif tag == f"{_W}hyperlink":
            target = rels.get(node.get(f"{_R}id") or "")
            inner = "".join(t.text or "" for t in node.iter(f"{_W}t")).strip()
            if target and inner:
                # This is what lets a Word sources list reach extract_source_urls.
                parts.append(f"[{inner}]({target})")
                for t in node.iter(f"{_W}t"):
                    t.text = ""
    return "".join(parts)


def _docx_rels(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        raw = zf.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}  # a document with no links
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    return {
        rel.get("Id"): rel.get("Target", "")
        for rel in root.iter(f"{_PKG_REL}Relationship")
        if rel.get("Id")
    }


# ----------------------------------------------------------------------------- pdf

def pdf_to_markdown(data: bytes, *, max_pages: int | None = None) -> str:
    """Text per page, one block per paragraph.

    PDF carries no heading semantics that survive without font-size heuristics, so a
    PDF seed normally lands on the no-headings warning. That is the honest outcome:
    the text is all there, the structure genuinely was not in the file.

    `max_pages` bounds the work for citation verification, where only the first few
    thousand characters are ever shown to a critic and extracting a thousand-page
    appendix would spend real time producing text that is then discarded. A seed passes
    `None`, because a seed *is* the document.
    """
    try:
        import pypdf
    except ImportError as exc:
        raise ConversionError(
            "reading PDFs needs the 'ingest' extra: pip install 'reasonable-answer[ingest]'"
        ) from exc

    try:
        reader = pypdf.PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ConversionError("the PDF is encrypted; supply an unlocked copy")
        wanted = reader.pages if max_pages is None else reader.pages[:max_pages]
        pages = [page.extract_text() or "" for page in wanted]
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"could not read the PDF: {type(exc).__name__}: {exc}"[:200]) from exc

    blocks: list[str] = []
    for page in pages:
        for block in re.split(r"\n\s*\n", page):
            collapsed = re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
            if collapsed:
                blocks.append(collapsed)
    return "\n\n".join(blocks)
