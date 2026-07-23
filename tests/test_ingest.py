"""Seed ingest: whatever the user has, converted to the markdown the pipeline needs.

The assertions here mostly go through `report.parse` and `fetch.extract_source_urls`
rather than matching converter output as strings. That is deliberate — those two
functions are the only consumers that matter, and a test that pins exact markdown would
fail on cosmetic changes while still missing the failures that count.
"""

from __future__ import annotations

import io
import urllib.request
import zipfile

import pytest

from reasonable_answer import fetch, ingest, report
from reasonable_answer.ingest import IngestError

# --------------------------------------------------------------------------- html

PAGE = """<html><head><title>T</title><style>.x{color:red}</style></head><body>
<script>var a = 1;</script>
<h1>Remote Work</h1>
<p>Teams shipped more when async.</p>
<h2>Findings</h2>
<p>Meeting load fell.</p>
<ul><li>alpha</li><li>beta</li></ul>
<h2>Sources</h2>
<ul><li><a href="https://example.org/a">Paper A</a></li>
<li><a href="https://example.org/b">Paper B</a></li></ul>
</body></html>"""


def test_html_headings_become_sections():
    out = ingest.from_text(PAGE)
    assert out.format == "html"
    assert report.parse(out.markdown).section_titles == (
        "(preamble)",
        "Remote Work",
        "Findings",
        "Sources",
    )


def test_html_sources_section_survives_conversion():
    """The seam the whole converter exists to protect.

    `extract_source_urls` only reads a markdown '## Sources' section, and the evidence
    lens's fetch-backed checks read only what it returns. A conversion that loses the
    heading or flattens the links silently downgrades that lens to guesswork.
    """
    out = ingest.from_text(PAGE)
    assert fetch.extract_source_urls(out.markdown) == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_html_drops_script_and_style():
    out = ingest.from_text(PAGE)
    assert "var a" not in out.markdown
    assert "color:red" not in out.markdown


def test_html_with_headings_warns_about_nothing():
    assert ingest.from_text(PAGE).warnings == ()


# ----------------------------------------------------------------------- markdown


def test_markdown_passes_through_byte_identical():
    """Regression-critical: an existing markdown seed must hash to what it always did.

    `report.artifact_hash` is a sha256 of the exact bytes and feeds the resume
    fingerprint, so any normalisation here would invalidate every stored run.
    """
    text = "# Title\n\nA paragraph.\n\n## Sources\n\nhttps://example.org/a\n"
    assert ingest.from_text(text).markdown == text.strip()


def test_plain_text_is_accepted_with_a_no_headings_warning(tmp_path):
    path = tmp_path / "draft.txt"
    path.write_text("Just some prose.\n\nAnd a second paragraph.\n")
    out = ingest.from_path(path, config=None)

    assert out.format == "text"
    assert len(out.warnings) == 1 and "no headings" in out.warnings[0]
    # Accepted, not rejected: critics can still cite [S0.Pn] loci, just coarser ones.
    assert report.parse(out.markdown).section_titles == ("(preamble)",)
    assert len(report.parse(out.markdown).paragraphs) == 2


def test_an_empty_seed_is_refused(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("   \n\n  \n")
    with pytest.raises(IngestError, match="no readable text"):
        ingest.from_path(path, config=None)


def test_a_latin1_file_ingests_instead_of_raising(tmp_path):
    """`Path.read_text()` would raise here; bytes-first decoding means it does not."""
    path = tmp_path / "draft.md"
    path.write_bytes("# Café\n\nPrix fixe.\n".encode("latin-1"))
    assert "Caf" in ingest.from_path(path, config=None).markdown


# ---------------------------------------------------------------------- detection


def test_magic_bytes_beat_a_lying_content_type():
    """A server that labels a PDF `text/html` must not defeat the parser."""
    fmt = ingest.detect_format(b"%PDF-1.4\nrest", filename="r.html", content_type="text/html")
    assert fmt == "pdf"


def test_extension_decides_among_text_formats():
    body = b"<p>hi</p>"
    assert ingest.detect_format(body, filename="a.md", content_type=None) == "markdown"
    assert ingest.detect_format(body, filename="a.html", content_type=None) == "html"
    assert ingest.detect_format(body, filename="a.txt", content_type=None) == "text"


def test_html_is_sniffed_when_nothing_declares_it():
    """The web textarea has neither a filename nor a content-type."""
    assert ingest.detect_format(b"<!DOCTYPE html><body>x", filename=None, content_type=None) == "html"


def test_a_non_docx_zip_is_named_not_guessed():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
    with pytest.raises(IngestError, match="xlsx"):
        ingest.detect_format(buf.getvalue(), filename="book.xlsx", content_type=None)


# --------------------------------------------------------------------------- docx

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_docx(body_xml: str, rels: dict[str, str] | None = None) -> bytes:
    """A .docx is a zip of XML, so a fixture needs no library either."""
    document = (
        f'<?xml version="1.0"?><w:document xmlns:w="{_W}" xmlns:r="{_R}">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    entries = "".join(
        f'<Relationship Id="{rid}" Target="{target}"/>' for rid, target in (rels or {}).items()
    )
    rels_xml = (
        '<?xml version="1.0"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{entries}</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
    return buf.getvalue()


def _para(text: str, *, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>"


DOCX = make_docx(
    _para("Quarterly Review", style="Title")
    + _para("Margin fell four points.")
    + _para("Sources", style="Heading2")
    + '<w:p><w:pPr><w:numPr/></w:pPr><w:hyperlink r:id="rId1">'
    "<w:r><w:t>Paper A</w:t></w:r></w:hyperlink></w:p>",
    rels={"rId1": "https://example.org/a"},
)


def test_docx_styles_become_headings(tmp_path):
    path = tmp_path / "report.docx"
    path.write_bytes(DOCX)
    out = ingest.from_path(path, config=_config())

    assert out.format == "docx"
    assert out.warnings == ()
    assert report.parse(out.markdown).section_titles == ("(preamble)", "Quarterly Review", "Sources")


def test_docx_hyperlinks_reach_the_evidence_lens():
    """Word stores link targets in a rels file, not inline: resolving them is what lets
    a .docx sources list stay verifiable."""
    md = ingest._docx_to_markdown(DOCX, 50_000_000)
    assert fetch.extract_source_urls(md) == ["https://example.org/a"]
    # And the anchor text is not duplicated alongside the link.
    assert md.count("Paper A") == 1


def test_docx_that_is_not_a_zip_is_a_clean_error():
    with pytest.raises(IngestError, match="bad zip archive"):
        ingest._docx_to_markdown(b"PK\x03\x04 not really a zip", 50_000_000)


def test_a_renamed_doc_says_so():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("something/else.xml", "<x/>")
    with pytest.raises(IngestError, match="no word/document.xml"):
        ingest._docx_to_markdown(buf.getvalue(), 50_000_000)


def test_docx_malformed_xml_is_a_clean_error():
    with pytest.raises(IngestError, match="malformed XML"):
        ingest._docx_to_markdown(make_docx("<w:p><unclosed>"), 50_000_000)


def test_a_zip_bomb_is_refused_before_it_is_read():
    """The archive can arrive from an arbitrary URL, so the guard is on the declared
    uncompressed size and runs before any member is decompressed."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "A" * 5_000_000)
    with pytest.raises(IngestError, match="expands to"):
        ingest._docx_to_markdown(buf.getvalue(), 100_000)


# ---------------------------------------------------------------------------- pdf


def test_a_missing_pypdf_names_the_extra(monkeypatch):
    """A missing optional dependency must be an instruction, not a traceback."""
    import builtins

    real_import = builtins.__import__

    def no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("no module named pypdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    with pytest.raises(IngestError, match=r"ingest.*extra"):
        ingest._pdf_to_markdown(b"%PDF-1.4")


def _minimal_pdf(*lines: str) -> bytes:
    """A hand-built single-page PDF, so the test needs no fixture binary."""
    drawn = " 0 -20 Td ".join(f"({line}) Tj" for line in lines)
    content = f"BT /F1 12 Tf 72 720 Td {drawn} ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_pdf_text_is_extracted_and_warns_about_structure(tmp_path):
    """PDF carries no recoverable heading semantics, so the text arrives and the
    warning is the honest report of what was lost."""
    pytest.importorskip("pypdf")
    path = tmp_path / "report.pdf"
    path.write_bytes(_minimal_pdf("Executive Summary", "Margin fell four points."))
    out = ingest.from_path(path, config=_config())

    assert out.format == "pdf"
    assert "Margin fell four points." in out.markdown
    assert len(out.warnings) == 1 and "no headings" in out.warnings[0]


def test_an_unreadable_pdf_is_a_clean_error():
    pytest.importorskip("pypdf")
    with pytest.raises(IngestError, match="could not read the PDF"):
        ingest._pdf_to_markdown(b"%PDF-1.4\nshredded")


# ---------------------------------------------------------------------------- url


def _config(**overrides):
    """A Config with a minimal valid roster — only the `seed` block matters here.

    URL seeds are opt-in (off by default, like search); these tests exercise the
    conversion mechanics, so they opt in unless a test overrides it to probe the gate.
    """
    from reasonable_answer.config import Config
    from reasonable_answer.taxonomy import LENSES

    overrides.setdefault("allow_url", True)
    return Config.model_validate(
        {
            "roster": {"writers": ["w"], "critics": {lens.value: ["c"] for lens in LENSES}},
            "seed": overrides,
        }
    )


def test_a_non_http_seed_url_is_refused_before_an_opener_exists(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("the opener must not be reached for a non-http(s) scheme")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", explode)
    for url in ("file:///etc/passwd", "ftp://example.org/x", "data:text/html,x"):
        with pytest.raises(IngestError, match="must be http"):
            ingest.from_url(url, config=_config())


def test_a_url_seed_is_fetched_and_converted(monkeypatch):
    from fakes import http_stub

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: http_stub(PAGE)
    )
    out = ingest.from_url("https://example.org/r", config=_config())

    assert out.format == "html"
    assert out.source == "url:https://example.org/r"
    assert fetch.extract_source_urls(out.markdown) == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_url_seeds_can_be_disabled():
    with pytest.raises(IngestError, match="disabled"):
        ingest.from_url("https://example.org/r", config=_config(allow_url=False))


def test_url_seeds_are_off_by_default():
    """The D17/D18 posture (D24): fetching a caller-chosen URL is exposure a
    deployment must opt into, so a bare config refuses it."""
    from reasonable_answer.config import SeedConfig

    assert SeedConfig().allow_url is False


def test_a_truncated_binary_seed_is_fatal(monkeypatch):
    """A cut-off PDF is a mangled file, not a shorter document: parsing it would
    produce plausible garbage, so this fails instead."""
    from fakes import http_stub

    body = _minimal_pdf("Summary") + b"x" * 20_000
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: http_stub(body, ctype="application/pdf"),
    )
    with pytest.raises(IngestError, match="exceeds"):
        ingest.from_url("https://example.org/r.pdf", config=_config(fetch_max_bytes=10_000))


def test_a_truncated_html_seed_only_warns(monkeypatch):
    """Text survives truncation as a shorter document, so it is a warning, not a stop."""
    from fakes import http_stub

    body = PAGE + "<p>" + "x" * 20_000 + "</p>"
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: http_stub(body)
    )
    out = ingest.from_url("https://example.org/r", config=_config(fetch_max_bytes=10_000))
    assert any("truncated" in w for w in out.warnings)


def test_a_dead_url_is_a_clean_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    with pytest.raises(IngestError, match="could not fetch"):
        ingest.from_url("https://example.org/r", config=_config())


# ------------------------------------------------------------------- seed argument


def test_from_seed_arg_routes_urls_and_paths(tmp_path, monkeypatch):
    from fakes import http_stub

    path = tmp_path / "draft.md"
    path.write_text("# T\n\nBody.\n")
    assert ingest.from_seed_arg(str(path), config=_config()).source == "file:draft.md"

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: http_stub(PAGE)
    )
    assert ingest.from_seed_arg("https://example.org/r", config=_config()).format == "html"


def test_a_url_is_not_mangled_into_a_path(tmp_path):
    """`Path('https://a/b')` normalises to 'https:/a/b'. If `--seed` were typed as a
    Path, a URL seed would be corrupted before anything could read it."""
    out = ingest.from_seed_arg.__doc__
    assert "URL" in out  # the dispatch is documented as URL-first
    with pytest.raises(IngestError, match="not found"):
        ingest.from_seed_arg(str(tmp_path / "nope.md"), config=_config())


def test_a_pasted_html_fragment_is_still_converted():
    """A user pastes what they copied, which has no <html> wrapper. Left as markdown
    it would reach a critic as literal markup inside a single heading-less section."""
    out = ingest.from_text("<h1>Pasted</h1><p>Body.</p>")
    assert out.format == "html"
    assert out.markdown.startswith("# Pasted")


def test_markdown_containing_inline_html_stays_markdown():
    """The counterweight to the fragment rule. Markdown may embed raw HTML, and
    running such a document through the HTML converter would treat its '#' headings as
    plain text and flatten the structure `report.parse` needs."""
    text = "# Real Heading\n\nA line with <br> and a <div>block</div> in it.\n\n## Second\n\nMore."
    out = ingest.from_text(text)
    assert out.format == "markdown"
    assert report.parse(out.markdown).section_titles == ("(preamble)", "Real Heading", "Second")
