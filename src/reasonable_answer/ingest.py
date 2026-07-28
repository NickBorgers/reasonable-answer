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
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from . import report as report_mod
from . import textconv
from .config import Config
from .fetch import http_get

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


# ---------------------------------------------------------------------- converters

# The conversions themselves live in :mod:`.textconv`, shared with citation
# verification. These wrappers exist for one reason: a seed failure must reach the CLI
# and web edges as `IngestError`, which is the type they catch and render. A
# `ConversionError` escaping from here would surface as a traceback instead of a
# message the user can act on.


def _pdf_to_markdown(data: bytes) -> str:
    try:
        return textconv.pdf_to_markdown(data)
    except textconv.ConversionError as exc:
        raise IngestError(str(exc)) from exc


def _docx_to_markdown(data: bytes, max_uncompressed: int) -> str:
    try:
        return textconv.docx_to_markdown(data, max_uncompressed)
    except textconv.ConversionError as exc:
        raise IngestError(str(exc)) from exc


def _html_to_markdown(html: str) -> str:
    return textconv.html_to_markdown(html)


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
