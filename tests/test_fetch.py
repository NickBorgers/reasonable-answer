"""Source verification: URL extraction, bounded fetching, and what each lens may see.

Offline throughout — `urllib.request.OpenerDirector.open` is stubbed, so the suite
keeps its "no network, no API keys" property.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO

import pytest
from fakes import http_stub

from reasonable_answer import prompts
from reasonable_answer.fetch import FetchedSource, SourceFetcher, extract_source_urls
from reasonable_answer.taxonomy import Lens

# ------------------------------------------------------------------- extraction

REPORT = """# Title

Body claiming a thing [1].

## Sources

[1] https://example.org/a
[2] https://example.org/b
"""


def test_extracts_urls_from_the_sources_section():
    assert extract_source_urls(REPORT) == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_ignores_urls_outside_the_sources_section():
    report = "# T\n\nSee https://example.org/passing-mention for context.\n"
    # A URL mentioned in passing is not a citation the report stands behind; fetching
    # it would spend budget on something no claim depends on.
    assert extract_source_urls(report) == []


def test_stops_at_the_next_heading():
    report = REPORT + "\n## Appendix\n\nhttps://example.org/not-a-source\n"
    assert "https://example.org/not-a-source" not in extract_source_urls(report)


def test_deduplicates_and_respects_the_limit():
    report = "## Sources\n\n" + "\n".join(
        f"[{i}] https://example.org/{i % 3}" for i in range(10)
    )
    urls = extract_source_urls(report, limit=2)
    assert urls == ["https://example.org/0", "https://example.org/1"]


def test_strips_trailing_punctuation():
    assert extract_source_urls("## Sources\n\n[1] https://example.org/a.\n") == [
        "https://example.org/a"
    ]


def test_no_sources_section_yields_nothing():
    assert extract_source_urls("# T\n\nJust prose.\n") == []


# ---------------------------------------------------------------------- fetching


#: Shared with the seed-ingest tests, which stub the same opener.
_stub = http_stub


PAGE = """<html><head><title>CAP theorem</title>
<style>.x{color:red}</style></head>
<body><script>var a=1;</script><p>Consistency, availability, partition tolerance.</p>
<p>Pick two.</p></body></html>"""


def test_fetch_extracts_visible_text_and_title(monkeypatch):
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: _stub(PAGE)
    )
    result = SourceFetcher().fetch("https://example.org/a")

    assert result.ok
    assert result.title == "CAP theorem"
    assert "Consistency, availability, partition tolerance." in result.text
    assert "Pick two." in result.text
    # Script and style content is not page prose and would only dilute the text the
    # critic reads.
    assert "var a=1" not in result.text
    assert "color:red" not in result.text


def test_text_is_truncated_to_the_configured_limit(monkeypatch):
    body = "<html><body><p>" + ("word " * 5000) + "</p></body></html>"
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: _stub(body)
    )
    result = SourceFetcher(max_chars=100).fetch("https://example.org/a")
    assert len(result.text) <= 100


def test_results_are_cached_per_url(monkeypatch):
    calls = []

    def once(self, *a, **k):
        calls.append(1)
        return _stub(PAGE)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", once)
    fetcher = SourceFetcher()
    fetcher.fetch("https://example.org/a")
    fetcher.fetch("https://example.org/a")

    # The same '## Sources' list is re-verified every round; without the cache a
    # ten-round run re-downloads the same pages ten times.
    assert len(calls) == 1


def test_http_error_is_recorded_not_raised(monkeypatch):
    def boom(self, *a, **k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    result = SourceFetcher().fetch("https://example.org/missing")

    assert not result.ok
    assert result.status == 404
    assert "404" in result.error


def test_unreadable_content_type_is_reported_honestly(monkeypatch):
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub("%PDF-1.4", ctype="application/pdf"),
    )
    result = SourceFetcher().fetch("https://example.org/paper.pdf")

    # A PDF is a perfectly good citation this cannot read. Saying so beats reporting
    # an empty page, which would read as evidence against the source.
    assert not result.ok
    assert "unreadable content type" in result.error


# ------------------------------------------------------------------------- pdfs


def _pdf_fetcher(**kwargs):
    return SourceFetcher(read_pdfs=True, **kwargs)


def test_a_cited_pdf_is_read_rather_than_refused(monkeypatch):
    """The gap this closes: `_pdf_to_markdown` shipped with D-seed-conversion, and until now every
    cited PDF still came back `unreadable content type (application/pdf)`."""
    pytest.importorskip("pypdf")
    from fakes import minimal_pdf

    pdf = minimal_pdf("Findings", "Margin fell four points.")
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub(pdf, ctype="application/pdf"),
    )
    result = _pdf_fetcher().fetch("https://example.org/paper.pdf")

    assert result.ok
    assert "Margin fell four points." in result.text


def test_pdf_reading_stays_off_unless_asked(monkeypatch):
    from reasonable_answer.fetch import SourceOutcome

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub(b"%PDF-1.4", ctype="application/pdf"),
    )
    result = SourceFetcher().fetch("https://example.org/paper.pdf")

    assert not result.ok
    assert result.outcome is SourceOutcome.UNREADABLE
    assert "unreadable content type" in result.error


def test_a_truncated_pdf_is_refused_not_parsed(monkeypatch):
    """A truncated PDF is a mangled file, not a shorter document. Handing one to pypdf
    yields either an exception or nonsense presented to a critic as the source's text —
    the same rule `ingest.from_url` applies to a truncated seed."""
    from reasonable_answer.fetch import SourceOutcome

    parsed = []
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub(b"%PDF-1.4" + b"x" * 5_000, ctype="application/pdf"),
    )
    monkeypatch.setattr(
        "reasonable_answer.textconv.pdf_to_markdown",
        lambda *a, **k: parsed.append(1) or "should never be reached",
    )
    result = _pdf_fetcher(pdf_max_bytes=1_000).fetch("https://example.org/paper.pdf")

    assert not parsed, "the parser must never see a body that hit the cap"
    assert result.outcome is SourceOutcome.UNREADABLE
    assert "cap" in result.error


def test_a_scanned_pdf_says_so_rather_than_looking_empty(monkeypatch):
    """No text layer is a permanent property of that URL, unlike an EMPTY page which
    may just be a rendering problem. The critic is owed the difference."""
    from reasonable_answer.fetch import SourceOutcome

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub(b"%PDF-1.4 fake", ctype="application/pdf"),
    )
    monkeypatch.setattr("reasonable_answer.textconv.pdf_to_markdown", lambda *a, **k: "   ")
    result = _pdf_fetcher().fetch("https://example.org/scan.pdf")

    assert result.outcome is SourceOutcome.UNREADABLE
    assert "no text layer" in result.error


def test_a_pdf_served_as_octet_stream_is_still_read(monkeypatch):
    """Repositories routinely mislabel PDFs, and the magic bytes are unavailable here
    because the first request deliberately reads no body."""
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub(b"%PDF-1.4 fake", ctype="application/octet-stream"),
    )
    monkeypatch.setattr(
        "reasonable_answer.textconv.pdf_to_markdown", lambda *a, **k: "Real prose."
    )
    result = _pdf_fetcher().fetch("https://repo.example/files/paper.pdf?download=1")

    assert result.ok and result.text == "Real prose."


@pytest.mark.parametrize(
    ("ctype", "body", "expected_caps"),
    [
        ("text/html", "<html><body><p>hi</p></body></html>", [400_000]),
        # Two requests for a PDF: the first reads no body (`want_body` declines the
        # content type), the second downloads under the larger cap.
        ("application/pdf", b"%PDF-1.4 fake", [400_000, 25_000_000]),
    ],
)
def test_the_larger_pdf_cap_applies_only_to_pdfs(monkeypatch, ctype, body, expected_caps):
    """400 KB exists so one enormous *page* cannot exhaust a run. Reading PDFs must not
    buy that back for HTML."""
    from reasonable_answer import fetch as fetch_mod

    caps = []
    real_http_get = fetch_mod.http_get

    def recording(url, *, max_bytes, **kwargs):
        caps.append(max_bytes)
        return real_http_get(url, max_bytes=max_bytes, **kwargs)

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: _stub(body, ctype=ctype)
    )
    monkeypatch.setattr("reasonable_answer.textconv.pdf_to_markdown", lambda *a, **k: "prose")
    monkeypatch.setattr(fetch_mod, "http_get", recording)

    _pdf_fetcher(max_bytes=400_000, pdf_max_bytes=25_000_000).fetch("https://example.org/a.pdf")

    assert caps == expected_caps


def test_pdf_reading_without_pypdf_refuses_to_start(monkeypatch, tmp_path, config):
    """Fail closed at load, like a missing search credential.

    Discovering the missing dependency at the first cited PDF costs a run's worth of
    tokens to find out, and arrives disguised as a per-source `unreadable` that reads
    like the site's fault rather than ours.
    """
    import builtins

    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _pdf_reading_enabled

    config = config.model_copy(
        update={"sources": config.sources.model_copy(update={"enabled": True})}
    )
    config.sources.pdf = config.sources.pdf.model_copy(update={"enabled": True})

    real_import = builtins.__import__

    def no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("no module named pypdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    with pytest.raises(ConfigError, match="ingest"):
        _pdf_reading_enabled(config)


def test_both_switches_are_required_to_read_pdfs(config):
    """One tier being on must never turn another on — the reason for two switches."""
    from reasonable_answer.graph import _pdf_reading_enabled

    only_tier = config.model_copy(
        update={"sources": config.sources.model_copy(update={"enabled": False})}
    )
    only_tier.sources.pdf = only_tier.sources.pdf.model_copy(update={"enabled": True})

    assert _pdf_reading_enabled(config) is False
    assert _pdf_reading_enabled(only_tier) is False


def test_non_http_scheme_is_refused():
    result = SourceFetcher().fetch("file:///etc/passwd")
    assert not result.ok
    assert "http(s)" in result.error


def test_byte_cap_bounds_what_is_read_off_the_wire():
    """The declared bound that stops one enormous page exhausting a run.

    Distinct from max_chars, which truncates *extracted text* after the whole body has
    already been read — a 2GB page would still be pulled into memory first.
    """
    read_sizes: list[int | None] = []
    body = ("<html><body><p>" + "x" * 100_000 + "</p></body></html>").encode()

    class _Resp:
        headers = {"Content-Type": "text/html"}
        status = 200

        def read(self, amt=None):
            read_sizes.append(amt)
            return body[:amt] if amt else body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import urllib.request as _u

    original = _u.OpenerDirector.open
    _u.OpenerDirector.open = lambda self, *a, **k: _Resp()
    try:
        result = SourceFetcher(max_bytes=500, max_chars=100_000).fetch(
            "https://example.org/huge"
        )
    finally:
        _u.OpenerDirector.open = original

    # One byte past the cap, never unbounded: the sentinel is how `http_get` tells a
    # body that just fits from one that was cut off. Only the cap's worth is kept.
    assert read_sizes == [501], "read() must be given the byte cap, not called unbounded"
    assert len(result.text) < 1_000


def test_a_redirect_out_of_http_is_refused(monkeypatch):
    """SSRF-adjacent regression.

    Python's stock redirect handler allows `ftp:` targets and `build_opener()` ships an
    FTPHandler, so checking only the initial URL does not deliver http(s)-only fetching.
    A cited page could 302 verification into another egress protocol.
    """
    import urllib.error

    from reasonable_answer.fetch import _BoundedRedirects

    handler = _BoundedRedirects(3)
    with pytest.raises(urllib.error.HTTPError, match="non-http"):
        handler.redirect_request(
            _FakeReq(), None, 302, "Found", {}, "ftp://evil.example/payload"
        )


@pytest.mark.parametrize(
    "target", ["https://example.org/ok", "http://example.org/ok"]
)
def test_http_redirects_are_still_followed(target):
    from reasonable_answer.fetch import _BoundedRedirects

    result = _BoundedRedirects(3).redirect_request(
        _FakeReq(), None, 302, "Found", {}, target
    )
    assert result.full_url == target


def test_a_zero_cap_refuses_the_very_first_redirect():
    """A limit of zero must mean zero.

    The stock handler consults `max_redirections` only once `redirect_dict` exists —
    from the second hop onwards — so it would follow one redirect on a zero cap and
    N+1 on a cap of N. Cosmetic for a cited page; load-bearing for `search.py`, whose
    request carries an API key.
    """
    import urllib.error

    from reasonable_answer.fetch import _BoundedRedirects

    with pytest.raises(urllib.error.HTTPError, match="past the cap"):
        _BoundedRedirects(0).redirect_request(
            _FakeReq(), None, 302, "Found", {}, "https://example.org/ok"
        )


def test_the_cap_counts_hops_not_repeats():
    """A cap of N permits exactly N hops, not N+1."""
    import urllib.error

    from reasonable_answer.fetch import _BoundedRedirects

    handler = _BoundedRedirects(2)
    req = _FakeReq()
    req.redirect_dict = {"https://example.org/1": 1, "https://example.org/2": 1}
    with pytest.raises(urllib.error.HTTPError, match="past the cap"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.org/3")


# ------------------------------------ credential-bearing requests (`_request`)


def _hop(
    status: int = 200,
    *,
    location: str | None = None,
    body: bytes = b"",
    ctype: str = "text/html",
):
    """One canned response, shaped enough for the real handler chain to process it."""
    hdrs = Message()
    hdrs["Content-Type"] = ctype
    if location is not None:
        hdrs["Location"] = location

    class _Hop(BytesIO):
        def __init__(self):
            super().__init__(body)
            self.status = self.code = status
            self.msg = "canned"
            self.headers = hdrs

        def info(self):
            return self.headers

        def geturl(self):
            return location or ""

    return _Hop()


def _transport(monkeypatch, *responses):
    """Answer each hop from a canned response, capturing the Request that carried it.

    Stubbed at `AbstractHTTPHandler.do_open`, one layer below the house stub on
    `OpenerDirector.open`, because a redirect is only *processed* above that layer:
    stubbing `open` would take the code under test off the path entirely. The real
    `_BoundedRedirects` runs here, and so does CPython's header-copying
    `redirect_request` beneath it — which is the whole point, since the guard exists to
    hold against what the stdlib actually does rather than what it is described as doing.
    """
    sent: list = []
    queue = list(responses)

    def do_open(self, http_class, req, **kwargs):
        sent.append(req)
        return queue.pop(0)

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", do_open)
    return sent


#: A provider call's shape: identification plus two credentials under names nobody
#: could have enumerated in advance.
_CREDENTIALLED = {
    "User-Agent": "reasonable-answer/1.0",
    "Authorization": "Bearer sk-live-secret",
    "X-API-Key": "sk-live-secret",
}


def test_a_credential_is_not_replayed_at_a_redirect_target(monkeypatch):
    """The reason `_request` may take headers at all.

    CPython's `redirect_request` copies every header but content-length/content-type
    onto the redirect target, cross-host and without asking. A provider that 302s would
    otherwise hand our API key to whatever host it named — and the allowlisted hop here
    shows the strip is unconditional, not a side effect of refusing the redirect.
    """
    from reasonable_answer.fetch import _request

    sent = _transport(
        monkeypatch,
        _hop(302, location="https://cdn.example/moved"),
        _hop(200, body=b"landed"),
    )
    resp = _request(
        "https://api.example/scrape",
        method="POST",
        data=b'{"url": "https://x.test"}',
        headers=_CREDENTIALLED,
        timeout=5,
        max_bytes=1_000,
        max_redirects=1,
        allowed_hosts=frozenset({"api.example", "cdn.example"}),
    )

    assert resp.body == b"landed"
    first, second = ({k.lower() for k in req.headers} for req in sent)
    assert {"authorization", "x-api-key"} <= first, (
        "the first hop must carry them, or this test proves nothing"
    )
    assert not {"authorization", "x-api-key"} & second
    # Content negotiation survives; the request is still recognisably ours.
    assert "user-agent" in second


def test_a_redirect_off_the_allowlist_is_refused(monkeypatch):
    """Defence in depth behind the strip above: even a credential-free follow-up is a
    request to a host the caller never named, made with the caller's egress."""
    from reasonable_answer.fetch import _request

    _transport(
        monkeypatch,
        _hop(302, location="https://evil.example/collect"),
        _hop(200, body=b"never reached"),
    )
    with pytest.raises(urllib.error.HTTPError, match="outside the allowlist"):
        _request(
            "https://api.example/scrape",
            headers=_CREDENTIALLED,
            timeout=5,
            max_bytes=1_000,
            max_redirects=1,
            allowed_hosts=frozenset({"api.example"}),
        )


def test_a_redirect_inside_the_allowlist_is_followed(monkeypatch):
    """Without this the refusal above would pass for the wrong reason: a guard that
    refuses every redirect is not an allowlist."""
    from reasonable_answer.fetch import _request

    sent = _transport(
        monkeypatch,
        _hop(302, location="https://cdn.example/moved"),
        _hop(200, body=b"landed"),
    )
    resp = _request(
        "https://api.example/scrape",
        headers=_CREDENTIALLED,
        timeout=5,
        max_bytes=1_000,
        max_redirects=1,
        allowed_hosts=frozenset({"api.example", "cdn.example"}),
    )

    assert resp.body == b"landed"
    assert [req.full_url for req in sent] == [
        "https://api.example/scrape",
        "https://cdn.example/moved",
    ]


def test_a_zero_cap_refuses_the_first_hop_end_to_end(monkeypatch):
    """The hop counting is asserted directly elsewhere; this is the same cap seen from
    the caller's side, which is how a provider adapter will actually rely on it."""
    from reasonable_answer.fetch import _request

    _transport(
        monkeypatch,
        _hop(302, location="https://api.example/elsewhere"),
        _hop(200, body=b"never reached"),
    )
    with pytest.raises(urllib.error.HTTPError, match="past the cap"):
        _request(
            "https://api.example/scrape",
            headers=_CREDENTIALLED,
            timeout=5,
            max_bytes=1_000,
            max_redirects=0,
            allowed_hosts=frozenset({"api.example"}),
        )


def test_a_post_body_reaches_the_wire_intact(monkeypatch):
    from reasonable_answer.fetch import _request

    payload = b'{"url": "https://x.test", "formats": ["markdown"]}'
    sent = _transport(monkeypatch, _hop(200, body=b"{}", ctype="application/json"))
    resp = _request(
        "https://api.example/v1/scrape",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-live"},
        timeout=5,
        max_bytes=1_000,
        max_redirects=0,
    )
    (req,) = sent

    assert req.get_method() == "POST"
    assert req.data == payload
    assert req.get_header("Authorization") == "Bearer sk-live"
    assert resp.content_type == "application/json"


def test_http_get_still_follows_a_cross_host_redirect(monkeypatch):
    """`http_get` names no allowlist, and must not acquire one by default: a cited page
    redirecting to another domain is ordinary web behaviour, and refusing it would
    report a live source as unreachable — which D-notfound-fabrication now reads as fabrication."""
    from reasonable_answer.fetch import http_get

    sent = _transport(
        monkeypatch,
        _hop(302, location="https://cdn.elsewhere/final"),
        _hop(200, body=b"<html><body><p>hi</p></body></html>"),
    )
    resp = http_get("https://example.org/a", timeout=5, max_bytes=1_000, accept="text/html")

    assert resp.body.startswith(b"<html")
    assert [req.full_url for req in sent] == [
        "https://example.org/a",
        "https://cdn.elsewhere/final",
    ]
    assert sent[0].get_method() == "GET" and sent[0].data is None


def test_the_opener_has_no_handler_for_other_schemes():
    from reasonable_answer.fetch import _http_only_opener

    names = {type(h).__name__ for h in _http_only_opener(3).handlers}
    # build_opener() would have installed all three of these.
    assert not names & {"FTPHandler", "FileHandler", "DataHandler"}
    assert "HTTPHandler" in names and "HTTPSHandler" in names


def test_the_opener_honours_environment_proxies(monkeypatch):
    """The egress-isolation deployment's only internet path is HTTP(S)_PROXY
    (docs/ssrf-egress-isolation.md); the opener must route through it, and the
    handler must be the env-reading kind rather than one pinned at import time."""
    from reasonable_answer.fetch import _http_only_opener

    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:3128")
    proxies = [h for h in _http_only_opener(3).handlers if type(h).__name__ == "ProxyHandler"]
    assert proxies and proxies[0].proxies.get("https") == "http://egress-proxy:3128"


def test_redirect_cap_is_wired_through():
    from reasonable_answer.fetch import _http_only_opener

    redirects = [h for h in _http_only_opener(2).handlers if hasattr(h, "max_redirections")]
    assert redirects and redirects[0].max_redirections == 2


def test_connection_failure_is_recorded_not_raised(monkeypatch):
    """The common real-world 'could not fetch' case, on which the whole
    'a failed fetch is never evidence of fabrication' promise rests."""
    import urllib.error

    def refused(self, *a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refused)
    result = SourceFetcher().fetch("https://example.org/down")

    assert not result.ok
    assert "URLError" in result.error


def test_timeout_is_passed_to_the_opener(monkeypatch):
    seen = {}

    def capture(self, req, timeout=None, **k):
        seen["timeout"] = timeout
        return _stub(PAGE)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", capture)
    SourceFetcher(timeout=7.5).fetch("https://example.org/a")
    assert seen["timeout"] == 7.5


class _FakeReq:
    full_url = "https://example.org/start"
    headers: dict = {}

    def get_method(self):
        return "GET"

    @property
    def origin_req_host(self):
        return "example.org"


def test_page_with_no_text_is_flagged(monkeypatch):
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, *a, **k: _stub("<html><body></body></html>"),
    )
    assert SourceFetcher().fetch("https://example.org/blank").error == "no readable text"


# ------------------------------------------------------------------ prompt shape


def test_fetched_pages_are_fenced_as_untrusted():
    block = prompts.fetched_sources_block(
        [FetchedSource(url="https://example.org/a", title="T", text="Body text.")]
    )
    assert prompts.UNTRUSTED_NOTE in block
    assert prompts.DATA_FENCE in block and prompts.DATA_END in block
    assert "Body text." in block


def test_a_failed_fetch_is_not_presented_as_evidence_of_fabrication():
    block = prompts.fetched_sources_block(
        [FetchedSource(url="https://example.org/a", status=403, error="HTTP 403")]
    )
    assert "BLOCKED" in block and "HTTP 403" in block
    # Sites block automated clients, paywall, and go down. Treating that as "the source
    # does not exist" would manufacture BLOCKING defects from transient conditions.
    assert "NOT that the source is fake" in block
    assert "says nothing at all about whether the source exists" in block


def test_a_blocked_source_keeps_the_on_its_face_bar():
    """403 is the shape most real paywalls take, and it is not evidence of anything.

    A sharpened `fabricated_citation` reading is a checkable standard. Applying it to a
    source nobody could check is how verification manufactures defects.
    """
    blocked = FetchedSource(url="https://example.org/a", status=403, error="HTTP 403")
    prompt = prompts.critic_user(Lens.EVIDENCE, "q?", "report", [blocked])

    assert "BLOCKED" in prompt
    assert "cannot be what it claims on its face" in prompt, "the on-its-face bar stands"


def test_a_not_found_source_is_not_offered_to_the_critic_to_raise_again():
    """D-notfound-fabrication mints that `fabricated_citation` mechanically in
    `triage.mechanical_citation_issues`. Asking the critic for it as well would
    double-report one defect, and both copies carry the blocking floor."""
    missing = FetchedSource(url="https://example.org/a", status=404, error="HTTP 404")
    prompt = prompts.critic_user(Lens.EVIDENCE, "q?", "report", [missing])

    assert "NOT FOUND" in prompt
    assert "ALREADY been recorded" in prompt
    assert "Do not raise it again" in prompt
    # The category definition itself stays the weaker on-its-face one: nothing about a
    # 404 makes the *critic's* judgement of the other citations sharper.
    assert "cannot be what it claims on its face" in prompt


def test_unresolvable_tracks_the_outcome_not_the_status():
    """One source of truth. `unresolvable` is what triage keys off, and a future
    not-found established without an HTTP code must reach it too."""
    from reasonable_answer.fetch import SourceOutcome

    assert FetchedSource(url="u", status=404, error="HTTP 404").unresolvable
    assert not FetchedSource(url="u", status=403, error="HTTP 403").unresolvable
    assert FetchedSource(
        url="u", error="no registry has heard of it", outcome=SourceOutcome.NOT_FOUND
    ).unresolvable


def test_a_body_less_outcome_cannot_be_constructed_as_if_it_had_one():
    """`ok` is what `dispute.adjudicate_mechanical` gates on, so it has to stay true
    that a non-`FULL_TEXT` outcome never carries quotable text."""
    from reasonable_answer.fetch import SourceOutcome

    with pytest.raises(ValueError, match="cannot carry readable body text"):
        FetchedSource(url="u", text="t", outcome=SourceOutcome.METADATA_ONLY)


def test_an_error_without_a_named_outcome_never_claims_full_text():
    from reasonable_answer.fetch import SourceOutcome

    assert FetchedSource(url="u", error="boom").outcome is SourceOutcome.ERROR
    assert (
        FetchedSource(url="u", status=404, error="HTTP 404").outcome
        is SourceOutcome.NOT_FOUND
    )
    assert (
        FetchedSource(url="u", status=429, error="HTTP 429").outcome
        is SourceOutcome.BLOCKED
    )


def test_truncation_is_disclosed_so_absence_is_not_read_as_contradiction():
    block = prompts.fetched_sources_block(
        [FetchedSource(url="https://example.org/a", text="x")]
    )
    assert "truncated" in block


def test_categories_sharpen_only_when_pages_are_available():
    without = prompts.critic_user(Lens.EVIDENCE, "q?", "report", None)
    with_pages = prompts.critic_user(
        Lens.EVIDENCE, "q?", "report", [FetchedSource(url="u", text="t")]
    )

    # Without pages the standard is plausibility; with them it is fact.
    assert "on its face" in without
    assert "the fetched page does not contain the claim" in with_pages
    assert "PAGES CITED BY THE REPORT" not in without


# ------------------------------------------------------- which lens sees the pages


def _runtime(tmp_path, identities, config, fetcher=None):
    from fakes import FakeClient

    from reasonable_answer.graph import Runtime
    from reasonable_answer.schemas import CritiqueOutput
    from reasonable_answer.store import RunStore

    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
    )
    return Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-verify"),
        fetcher=fetcher,
    ), client


class _Fetcher:
    """One readable page and one source proven real by a registry but never read.

    Both are third-party text in a critic's context, and registry metadata is a class of
    it that did not exist before D-existence-vs-body — a different vendor, a different shape, and the
    same isolation rule.
    """

    def fetch_all(self, urls):
        from reasonable_answer.fetch import SourceMetadata, SourceOutcome

        return [
            FetchedSource(url=urls[0], title="T", text="PAGE BODY MARKER"),
            FetchedSource(
                url=urls[1],
                error="HTTP 403; crossref confirms the source exists",
                outcome=SourceOutcome.METADATA_ONLY,
                metadata=SourceMetadata(
                    title="REGISTRY TITLE MARKER",
                    abstract="ABSTRACT MARKER",
                    registry="crossref",
                ),
            ),
        ]


#: Every kind of third-party text a fetched source can carry into a prompt.
_SOURCE_MARKERS = ("PAGE BODY MARKER", "REGISTRY TITLE MARKER", "ABSTRACT MARKER")


@pytest.mark.parametrize("lens", [Lens.LOGIC, Lens.COMPLETENESS])
@pytest.mark.parametrize("marker", _SOURCE_MARKERS)
def test_other_lenses_never_see_the_fetched_pages(lens, marker, tmp_path, identities, config):
    """Isolation, not an optimization.

    Logic and completeness cannot raise a citation category, so page text would widen
    what they see without widening what they may report — and every extra channel into
    a lens is a way for material to reach a scope with no use for it.
    """
    from reasonable_answer.graph import _critique_one

    rt, client = _runtime(tmp_path, identities, config, fetcher=_Fetcher())
    _critique_one(rt, lens, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1)

    assert marker not in client.calls[-1].user


def test_evidence_lens_sees_the_fetched_pages(tmp_path, identities, config):
    from reasonable_answer.graph import _critique_one

    rt, client = _runtime(tmp_path, identities, config, fetcher=_Fetcher())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    for marker in _SOURCE_MARKERS:
        assert marker in client.calls[-1].user
    assert "https://example.org/a" in client.calls[-1].user


def test_verification_off_leaves_the_evidence_prompt_unchanged(
    tmp_path, identities, config
):
    from reasonable_answer.graph import _critique_one

    rt, client = _runtime(tmp_path, identities, config, fetcher=None)
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    assert "PAGES CITED BY THE REPORT" not in client.calls[-1].user
    assert "on its face" in client.calls[-1].user


def test_the_audit_trail_records_what_was_fetched(tmp_path, identities, config):
    """Locks the audit-trail contract: a run can be asked afterwards how many cited
    pages were actually readable when the evidence lens judged them."""
    import json

    from reasonable_answer.graph import _critique_one

    class _PartlyFailing:
        def fetch_all(self, urls):
            return [
                FetchedSource(url=urls[0], text="ok"),
                FetchedSource(url=urls[1], error="HTTP 403"),
            ]

    rt, _ = _runtime(tmp_path, identities, config, fetcher=_PartlyFailing())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    events = [
        json.loads(line)
        for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
    ]
    fetched = [e for e in events if e["kind"] == "fetch_sources"]
    assert fetched and fetched[-1]["fetched"] == 2 and fetched[-1]["failed"] == 1


def test_the_audit_trail_tallies_which_tier_produced_each_source(
    tmp_path, identities, config
):
    """Across ALL sources, not just the failures (D-existence-vs-body). A source the open-access tier
    rescued is a success and leaves no trace in the failure tally, so without this an
    operator cannot tell whether a tier is earning the calls it spends."""
    import json

    from reasonable_answer.fetch import ResolutionTier, SourceMetadata, SourceOutcome
    from reasonable_answer.graph import _critique_one

    class _Mixed:
        def fetch_all(self, urls):
            return [
                FetchedSource(url=urls[0], text="direct body"),
                FetchedSource(
                    url=urls[1],
                    error="HTTP 403; crossref confirms the source exists",
                    outcome=SourceOutcome.METADATA_ONLY,
                    metadata=SourceMetadata(title="T", registry="crossref"),
                    tier=ResolutionTier.IDENTIFIER,
                ),
            ]

    rt, _ = _runtime(tmp_path, identities, config, fetcher=_Mixed())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    events = [
        json.loads(line)
        for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
    ]
    event = [e for e in events if e["kind"] == "fetch_sources"][-1]
    assert event["tiers"] == {"direct": 1, "identifier": 1}
    assert all(
        key in {t.value for t in ResolutionTier} for key in event["tiers"]
    ), "every audit key must be a member of the closed vocabulary"


def test_fetch_failure_reasons_are_redacted_to_their_class(tmp_path, identities, config):
    """RA-016: a fetch failure enters the `fetch_sources` audit event as a member of the
    closed `SourceOutcome` vocabulary, optionally suffixed with an HTTP status — never
    the URL or page detail that trails `error`. The redaction is now structural rather
    than a `split(':')` that could regress, but an untested guarantee is still an
    untested guarantee."""
    import json

    from reasonable_answer.fetch import SourceOutcome
    from reasonable_answer.graph import _critique_one, _failure_reasons

    secret = "https://secret.example/leaked/path"

    class _LeakyFailures:
        def fetch_all(self, urls):
            return [
                FetchedSource(url=secret, error=f"ConnectionResetError: {secret}"),
                FetchedSource(
                    url=secret,
                    error="unreadable content type (application/pdf)",
                    outcome=SourceOutcome.UNREADABLE,
                ),
                FetchedSource(url=secret, status=403, error="HTTP 403"),
            ]

    # Every key is an enum member, so no free text can reach the event at all. The
    # status rides along only where it changes what an operator would do.
    expected = {"error": 1, "unreadable": 1, "blocked:403": 1}
    reasons = _failure_reasons(_LeakyFailures().fetch_all(["u"]))
    assert reasons == expected
    assert all(
        key.split(":")[0] in {o.value for o in SourceOutcome} for key in reasons
    ), "every audit key must be a member of the closed vocabulary"

    rt, _ = _runtime(tmp_path, identities, config, fetcher=_LeakyFailures())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    events = [
        json.loads(line)
        for line in (rt.store.dir / "events.jsonl").read_text().splitlines()
    ]
    event = [e for e in events if e["kind"] == "fetch_sources"][-1]
    assert event["failure_reasons"] == expected
    # The tail — URL and page-type detail — must appear nowhere in the audit event.
    blob = json.dumps(event)
    assert secret not in blob
    assert "leaked" not in blob
    assert "pdf" not in blob


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_sources": 0},  # verification silently checks nothing
        {"fetch_timeout_seconds": 0},  # every fetch fails instantly
        {"fetch_max_bytes": 0},  # every page reads as empty
        {"fetch_max_chars": 0},  # the critic is shown no page text
    ],
)
def test_out_of_range_fetch_config_is_rejected_at_load(kwargs):
    from pydantic import ValidationError

    from reasonable_answer.config import SearchConfig

    # Each of these would degrade verification to a no-op that still reports success.
    with pytest.raises(ValidationError):
        SearchConfig(**kwargs)


def test_a_report_with_no_sources_section_fetches_nothing(tmp_path, identities, config):
    from reasonable_answer.graph import _critique_one

    class _Boom:
        def fetch_all(self, urls):
            raise AssertionError("should not fetch when there is nothing to fetch")

    rt, client = _runtime(tmp_path, identities, config, fetcher=_Boom())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", "# T\n\nNo sources here.\n", "h" * 64,
        "vendor-a/model-a", set(), attempt=1,
    )
    assert "PAGES CITED BY THE REPORT" not in client.calls[-1].user


# ------------------------------------------- a definitive not-found is fabrication (D-notfound-fabrication)


def test_not_found_status_is_unresolvable():
    """Only a definitive not-found proves the URL does not exist. Every other failure
    is 'could not read', which is never evidence of fabrication."""
    assert FetchedSource(url="u", status=404, error="HTTP 404").unresolvable
    assert FetchedSource(url="u", status=410, error="HTTP 410").unresolvable


@pytest.mark.parametrize(
    "source",
    [
        FetchedSource(url="u", status=403, error="HTTP 403"),  # blocked, not absent
        FetchedSource(url="u", status=500, error="HTTP 500"),  # server error, not absent
        FetchedSource(url="u", error="URLError: Connection refused"),  # no status at all
        FetchedSource(url="u", status=200, error="unreadable content type (application/pdf)"),
        FetchedSource(url="u", status=200, title="T", error="no readable text"),
        FetchedSource(url="u", status=200, title="T", text="body"),  # a good fetch
    ],
)
def test_everything_but_a_not_found_is_resolvable(source):
    assert not source.unresolvable


def test_mechanical_issue_raised_only_for_a_not_found():
    """The finding is a fact of the fetch, minted mechanically — a `fabricated_citation`
    at its blocking floor, anchored to the paragraph that cites the dead URL."""
    from reasonable_answer import report as report_mod
    from reasonable_answer import triage
    from reasonable_answer.taxonomy import Category, Severity

    report = "# T\n\nClaim [1][2].\n\n## Sources\n\n[1] https://x.test/gone\n[2] https://x.test/live\n"
    structure = report_mod.parse(report)
    sources = [
        FetchedSource(url="https://x.test/gone", status=404, error="HTTP 404"),
        FetchedSource(url="https://x.test/live", status=200, title="T", text="ok"),
    ]

    issues = triage.mechanical_citation_issues(sources, structure)

    assert len(issues) == 1
    (issue,) = issues
    assert issue.category is Category.FABRICATED_CITATION
    assert issue.severity is Severity.BLOCKING
    assert issue.claim_span == "https://x.test/gone"
    # Anchored to the Sources paragraph, not a placeholder locus.
    assert structure.contains(issue.locus)


@pytest.mark.parametrize(
    "source",
    [
        FetchedSource(url="https://x.test/a", status=403, error="HTTP 403"),
        FetchedSource(url="https://x.test/a", error="TimeoutError: timed out"),
        FetchedSource(url="https://x.test/a", status=200, error="unreadable content type (application/pdf)"),
        FetchedSource(url="https://x.test/a", status=200, title="T", error="no readable text"),
    ],
)
def test_no_mechanical_issue_for_the_unreadable_class(source):
    from reasonable_answer import report as report_mod
    from reasonable_answer import triage

    structure = report_mod.parse("# T\n\nClaim [1].\n\n## Sources\n\n[1] https://x.test/a\n")
    assert triage.mechanical_citation_issues([source], structure) == []


_TWELVE = "# T\n\nClaim.\n\n## Sources\n\n" + "\n".join(
    f"[{i}] https://x.test/page-{i}" for i in range(1, 13)
)


class _AllNotFound:
    def fetch_all(self, urls):
        return [FetchedSource(url=u, status=404, error="HTTP 404") for u in urls]


def test_twelve_of_twelve_404_does_not_clear_the_evidence_lens(tmp_path, identities, config):
    """The `run-d3bb2e4d2d94` regression: a wholly-fabricated bibliography every page of
    which 404s must not produce a clean evidence lens (D-notfound-fabrication)."""
    from reasonable_answer import triage
    from reasonable_answer.graph import _critique_one
    from reasonable_answer.taxonomy import Category

    rt, _ = _runtime(tmp_path, identities, config, fetcher=_AllNotFound())
    result = _critique_one(
        rt, Lens.EVIDENCE, "q?", _TWELVE, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    # The critic elected zero issues (fake returns []); the twelve findings are the
    # pipeline's, raised mechanically from the 404s.
    assert not result.failed
    fabricated = [i for i in result.issues if i.category is Category.FABRICATED_CITATION]
    assert len(fabricated) == 12
    # Not clean: a lens with a material finding mints no clean record.
    assert triage.clean_records([result]) == []
    _, totals = triage.tally([result])
    assert totals.blocking == 12


class _AllBlocked:
    def fetch_all(self, urls):
        return [FetchedSource(url=u, status=403, error="HTTP 403") for u in urls]


def test_twelve_of_twelve_403_still_clears_the_evidence_lens(tmp_path, identities, config):
    """The other side of the split: a blocked client is not a fabricated citation, so a
    clean critic response stays clean — the launder this fix removes must not overshoot
    and start manufacturing blocking defects out of transient conditions."""
    from reasonable_answer import triage
    from reasonable_answer.graph import _critique_one

    rt, _ = _runtime(tmp_path, identities, config, fetcher=_AllBlocked())
    result = _critique_one(
        rt, Lens.EVIDENCE, "q?", _TWELVE, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    assert not result.failed
    assert result.issues == []
    assert len(triage.clean_records([result])) == 1


def test_a_failed_critic_lens_is_not_promoted_by_a_mechanical_finding(
    tmp_path, identities, config
):
    """A failed lens must stay failed (rule 2 re-critiques); a mechanical 404 finding
    must not be smuggled onto it and counted as a completed review. The cached fetch
    re-derives the finding once a critic completes."""
    from fakes import FakeClient

    from reasonable_answer.graph import Runtime, _critique_one
    from reasonable_answer.llm import ModelCallError
    from reasonable_answer.store import RunStore

    def boom(alias, user):
        raise ModelCallError("critic unavailable")

    client = FakeClient(
        identities=identities, critique_fn=boom, report_fn=lambda n: _TWELVE
    )
    rt = Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-fail"),
        fetcher=_AllNotFound(),
    )
    result = _critique_one(
        rt, Lens.EVIDENCE, "q?", _TWELVE, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    assert result.failed
    assert result.issues == []
