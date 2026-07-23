"""Turn whatever the user actually has into the markdown the pipeline requires.

A seed report is the one artifact that does not come from a model, so it arrives in
whatever form its author had: a PDF, a Word document, a page on the web. The pipeline,
though, does not merely *prefer* markdown — it is built on it:

* `report.parse` keys off `#` headings to build the `[S<n>.P<m>]` loci that critics are
  required to cite. A locus outside the parsed structure fails the lens closed.
* `fetch.extract_source_urls` only reads a markdown `## Sources` section, so a citation
  list that does not survive conversion takes the evidence lens's fetch-backed checks
  with it.

So every converter here owes the same **output contract**, and it is the whole point of
the module: *blocks separated by blank lines, headings alone on their line* — precisely
what `report.parse` consumes. Fidelity beyond that is not a goal; these are lossy
best-effort converters, and where a format carries no heading semantics at all (a bare
.txt, most PDFs) the result is one section plus a warning, not a failure.

Conversion happens at the **edges** (`cli`, `web.app`), never inside the graph, so that
`graph.run(seed=...)` keeps one contract — seed is markdown — and one identity: the text
that is hashed into the resume fingerprint is byte-for-byte the text that is stored,
critiqued, and revised.

Converted text is untrusted third-party material under RA-010, exactly as a pasted seed
always was; conversion changes the encoding, not the trust.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from . import report as report_mod
from .config import Config
from .fetch import _SKIP_TAGS, http_get

log = logging.getLogger(__name__)

#: Formats whose parsers need the whole file. A truncated one is a mangled file, not a
#: shorter document, so truncation is fatal rather than a warning.
_BINARY_FORMATS = frozenset({"pdf", "docx"})

NO_HEADINGS_WARNING = (
    "seed converted from {fmt} but no headings were recovered: the whole document is "
    "one section, so critics can only cite [S0.Pn] loci. Adding '#' headings to the "
    "source would yield sharper critiques."
)


class IngestError(ValueError):
    """A seed the user can fix. Edges render the message; a traceback never escapes."""


@dataclass(frozen=True)
class Ingested:
    """Converted seed text plus the provenance the audit trail records."""

    markdown: str
    #: "text" | "file:<name>" | "url:<url>" — recorded on the intake event.
    source: str
    format: str
    warnings: tuple[str, ...] = ()


# --------------------------------------------------------------------------- entry

def from_text(text: str, *, source: str = "text", fmt: str | None = None) -> Ingested:
    """Text already in hand: the web textarea, or a local .md/.txt/.html file.

    With no filename and no content-type the format is sniffed, so pasting HTML into
    the web form converts rather than landing verbatim in a critic's context.
    """
    fmt = fmt or detect_format(text.encode("utf-8", "replace"), filename=None, content_type=None)
    markdown = _html_to_markdown(text) if fmt == "html" else text
    return _finish(markdown, source=source, fmt=fmt)


def from_path(path: Path, *, config: Config) -> Ingested:
    """Read and convert a local file. Bytes, not text: magic sniffing needs them, and
    it incidentally makes a latin-1 file ingest instead of raising."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IngestError(f"cannot read seed file {path}: {exc.strerror or exc}") from exc
    fmt = detect_format(data, filename=path.name, content_type=None)
    return _convert(data, fmt=fmt, source=f"file:{path.name}", config=config)


def from_url(url: str, *, config: Config) -> Ingested:
    """Fetch and convert a URL the *user* supplied.

    Egress goes through `fetch.http_get`, the same bounded http(s)-only opener that
    citation verification uses. The scheme is checked here as well as there so that
    `file:///etc/passwd` is refused before an opener is ever constructed.
    """
    if not config.seed.allow_url:
        raise IngestError("URL seeds are disabled (seed.allow_url)")
    if not url.lower().startswith(("http://", "https://")):
        raise IngestError(f"a seed URL must be http(s): {url}")

    try:
        resp = http_get(
            url,
            timeout=config.seed.fetch_timeout_seconds,
            max_bytes=config.seed.fetch_max_bytes,
            accept="text/html,text/plain,application/pdf,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document;q=0.9",
        )
    except Exception as exc:
        raise IngestError(f"could not fetch seed URL: {type(exc).__name__}: {exc}"[:300]) from exc

    fmt = detect_format(resp.body, filename=url, content_type=resp.content_type)
    warnings: list[str] = []
    if resp.truncated:
        if fmt in _BINARY_FORMATS:
            raise IngestError(
                f"seed document exceeds {config.seed.fetch_max_bytes} bytes; a truncated "
                f"{fmt} cannot be parsed. Download it and pass the file directly."
            )
        warnings.append(
            f"seed page exceeded {config.seed.fetch_max_bytes} bytes and was truncated"
        )
    out = _convert(resp.body, fmt=fmt, source=f"url:{url}", config=config)
    return Ingested(out.markdown, out.source, out.format, (*warnings, *out.warnings))


def from_seed_arg(raw: str, *, config: Config) -> Ingested:
    """The CLI's `--seed` value: an http(s) URL or a path. Nothing else is guessed."""
    if raw.lower().startswith(("http://", "https://")):
        return from_url(raw, config=config)
    path = Path(raw)
    if not path.exists():
        raise IngestError(f"seed file not found: {raw}")
    return from_path(path, config=config)


def _convert(data: bytes, *, fmt: str, source: str, config: Config) -> Ingested:
    if fmt == "pdf":
        markdown = _pdf_to_markdown(data)
    elif fmt == "docx":
        markdown = _docx_to_markdown(data, config.seed.docx_max_uncompressed_bytes)
    else:
        text = _decode(data)
        markdown = _html_to_markdown(text) if fmt == "html" else text
    return _finish(markdown, source=source, fmt=fmt)


def _finish(markdown: str, *, source: str, fmt: str) -> Ingested:
    markdown = markdown.strip()
    if not markdown:
        raise IngestError(f"the seed ({fmt}) produced no readable text")
    warnings: list[str] = []
    if not _has_headings(markdown):
        warnings.append(NO_HEADINGS_WARNING.format(fmt=fmt))
    return Ingested(markdown, source, fmt, tuple(warnings))


def _has_headings(markdown: str) -> bool:
    """Ask `report.parse` rather than a regex — the warning must predict the structure
    critics will actually be shown, not a near-enough approximation of it."""
    return len(report_mod.parse(markdown).section_titles) > 1


def _decode(data: bytes) -> str:
    return data.lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace")


# ----------------------------------------------------------------------- detection

def detect_format(data: bytes, *, filename: str | None, content_type: str | None) -> str:
    """One of: pdf | docx | html | markdown | text.

    Magic bytes decide binary formats unconditionally; the declared content-type and
    the extension only disambiguate among text formats. The precedence earns its keep
    on servers that return `Content-Type: text/html` for a body starting `%PDF-` —
    magic wins and the PDF parses.
    """
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise IngestError("seed looks like a zip archive but could not be opened") from exc
        if "word/document.xml" in names:
            return "docx"
        if any(n.startswith("xl/") for n in names):
            kind = "an .xlsx spreadsheet"
        elif any(n.startswith("ppt/") for n in names):
            kind = "a .pptx deck"
        else:
            kind = "a zip archive"
        raise IngestError(f"unsupported seed: this is {kind}, not a .docx")

    ctype = (content_type or "").lower()
    if "html" in ctype:
        return "html"
    if "markdown" in ctype:
        return "markdown"

    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix == ".txt":
        return "text"

    return _sniff_text(_decode(data))


#: Block-level tags. Their presence is what separates a pasted HTML *fragment* — which
#: has no <html> wrapper but is still markup — from prose.
_HTML_BLOCK = re.compile(
    r"<(?:html|body|!doctype html|h[1-6]|p|div|ul|ol|li|table|article|section|blockquote)\b",
    re.IGNORECASE,
)
_MD_HEADING_LINE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)


def _sniff_text(text: str) -> str:
    """Markdown unless it is really markup.

    Markdown headings win outright, and that ordering is the whole subtlety: markdown
    legitimately embeds raw HTML, and running such a document through the HTML
    converter would treat its `#` headings as plain text and collapse the structure
    `report.parse` depends on. Only text with block-level tags and no markdown headings
    is treated as HTML — which is the pasted-fragment case.
    """
    if _MD_HEADING_LINE.search(text):
        return "markdown"
    return "html" if _HTML_BLOCK.search(text) else "markdown"


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
        if tag in _SKIP_TAGS:
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
        if tag in _SKIP_TAGS:
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


def _html_to_markdown(html: str) -> str:
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


def _docx_to_markdown(data: bytes, max_uncompressed: int) -> str:
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
                raise IngestError(
                    f"the .docx expands to {total} bytes, over the {max_uncompressed} limit"
                )
            try:
                document = zf.read("word/document.xml")
            except KeyError as exc:
                raise IngestError(
                    "not a .docx (no word/document.xml — a legacy .doc, or renamed?)"
                ) from exc
            rels = _docx_rels(zf)
    except zipfile.BadZipFile as exc:
        raise IngestError("not a readable .docx (bad zip archive)") from exc

    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise IngestError(f"the .docx contains malformed XML: {exc}") from exc

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

def _pdf_to_markdown(data: bytes) -> str:
    """Text per page, one block per paragraph.

    PDF carries no heading semantics that survive without font-size heuristics, so a
    PDF seed normally lands on the no-headings warning. That is the honest outcome:
    the text is all there, the structure genuinely was not in the file.
    """
    try:
        import pypdf
    except ImportError as exc:
        raise IngestError(
            "PDF seeds need the 'ingest' extra: pip install 'reasonable-answer[ingest]'"
        ) from exc

    try:
        reader = pypdf.PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise IngestError("the PDF is encrypted; supply an unlocked copy")
        pages = [page.extract_text() or "" for page in reader.pages]
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError(f"could not read the PDF: {type(exc).__name__}: {exc}"[:200]) from exc

    blocks: list[str] = []
    for page in pages:
        for block in re.split(r"\n\s*\n", page):
            collapsed = re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
            if collapsed:
                blocks.append(collapsed)
    return "\n\n".join(blocks)
