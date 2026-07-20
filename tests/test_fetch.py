"""Source verification: URL extraction, bounded fetching, and what each lens may see.

Offline throughout — `urllib.request.OpenerDirector.open` is stubbed, so the suite
keeps its "no network, no API keys" property.
"""

from __future__ import annotations

import urllib.error
import urllib.request

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
        [FetchedSource(url="https://example.org/a", error="HTTP 403")]
    )
    assert "COULD NOT FETCH: HTTP 403" in block
    # Sites block automated clients, paywall, and go down. Treating that as "the source
    # does not exist" would manufacture BLOCKING defects from transient conditions.
    assert "NOT that the source is fake" in block
    assert "Never raise a defect on the basis of a failed fetch" in block


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
    def fetch_all(self, urls):
        return [FetchedSource(url=u, title="T", text="PAGE BODY MARKER") for u in urls]


@pytest.mark.parametrize("lens", [Lens.LOGIC, Lens.COMPLETENESS])
def test_other_lenses_never_see_the_fetched_pages(lens, tmp_path, identities, config):
    """Isolation, not an optimization.

    Logic and completeness cannot raise a citation category, so page text would widen
    what they see without widening what they may report — and every extra channel into
    a lens is a way for material to reach a scope with no use for it.
    """
    from reasonable_answer.graph import _critique_one

    rt, client = _runtime(tmp_path, identities, config, fetcher=_Fetcher())
    _critique_one(rt, lens, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1)

    assert "PAGE BODY MARKER" not in client.calls[-1].user


def test_evidence_lens_sees_the_fetched_pages(tmp_path, identities, config):
    from reasonable_answer.graph import _critique_one

    rt, client = _runtime(tmp_path, identities, config, fetcher=_Fetcher())
    _critique_one(
        rt, Lens.EVIDENCE, "q?", REPORT, "h" * 64, "vendor-a/model-a", set(), attempt=1
    )

    assert "PAGE BODY MARKER" in client.calls[-1].user
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
