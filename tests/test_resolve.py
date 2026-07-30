"""The resolver ladder (D-existence-vs-body): identifiers, the free tiers, and what each outcome licenses.

Offline throughout, like `test_fetch.py` — `urllib.request.OpenerDirector.open` is
stubbed, so the real opener and redirect handler stay on the path and the suite needs no
network and no key. Provider responses come from trimmed captures of the real APIs under
`tests/fixtures/resolve/`, so a registry that changes its JSON shape becomes a test
failure when the fixtures are refreshed rather than a production surprise.
"""

from __future__ import annotations

import ast
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fakes import http_sequence, http_stub, json_stub

from reasonable_answer import prompts
from reasonable_answer.fetch import (
    FetchedSource,
    Provider,
    ResolutionTier,
    SourceFetcher,
    SourceMetadata,
    SourceOutcome,
)
from reasonable_answer.resolve import SourceResolver, UnknownProvider, build
from reasonable_answer.resolve import identifiers as ids
from reasonable_answer.resolve.identifiers import IdKind
from reasonable_answer.search import QueryBudget
from reasonable_answer.taxonomy import Lens

FIXTURES = Path(__file__).parent / "fixtures" / "resolve"


def fixture(name: str):
    body = (FIXTURES / name).read_text()
    return json.loads(body) if name.endswith(".json") else body


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://provider.test/x", code, "no", {}, None)


#: The commonest shape a paywalled academic citation takes: a publisher landing page
#: whose path carries the DOI, refusing automated clients.
PAYWALL_URL = "https://www.tandfonline.com/doi/full/10.1038/s41586-021-03819-2"
DOI_URL = "https://doi.org/10.1038/s41586-021-03819-2"
PAGE = "<html><head><title>AlphaFold</title></head><body><p>Full body text.</p></body></html>"


# ------------------------------------------------------------------ identifiers


@pytest.mark.parametrize(
    ("url", "kind", "value"),
    [
        (DOI_URL, IdKind.DOI, "10.1038/s41586-021-03819-2"),
        ("https://link.springer.com/article/10.1007/s00234-021-02655-5",
         IdKind.DOI, "10.1007/s00234-021-02655-5"),
        # The DOI arrives in a querystring, and must stop at the parameter separator.
        ("https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0123456&type=x",
         IdKind.DOI, "10.1371/journal.pone.0123456"),
        # Case is not significant in a DOI, so the cache key must not be either.
        ("https://doi.org/10.1038/S41586-021-03819-2", IdKind.DOI, "10.1038/s41586-021-03819-2"),
        ("https://arxiv.org/abs/1706.03762", IdKind.ARXIV, "1706.03762"),
        # The version suffix is dropped: the registry answers for the paper, not the
        # revision — which is also why an arXiv body is only ever a mirror.
        ("https://arxiv.org/pdf/1706.03762v7", IdKind.ARXIV, "1706.03762"),
        ("https://arxiv.org/abs/math.GT/0309136", IdKind.ARXIV, "math.gt/0309136"),
        ("https://pubmed.ncbi.nlm.nih.gov/34265844/", IdKind.PMID, "34265844"),
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8371605/", IdKind.PMCID, "PMC8371605"),
    ],
)
def test_identifier_extraction(url, kind, value):
    ident = ids.extract(url)
    assert ident is not None and ident.kind is kind and ident.value == value


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bbc.co.uk/news/some-story",
        "https://example.org/10.notadoi/x",  # the registrant must be numeric
        "https://example.org/10.1234/",  # a prefix with no suffix is not a record
        "",
    ],
)
def test_a_url_with_no_identifier_yields_none(url):
    """The quiet, correct outcome for most citations. Guessing here is the dangerous
    direction: a mangled identifier is one no registry has heard of, and that feeds a
    blocking `fabricated_citation`."""
    assert ids.extract(url) is None


def test_trailing_punctuation_is_not_part_of_the_doi():
    assert ids.extract("(see https://doi.org/10.1038/abc123).").value == "10.1038/abc123"


def test_a_doi_wins_over_an_arxiv_id_in_the_same_url():
    """arXiv mints DOIs now, and the DOI path reaches strictly more registries."""
    url = "https://arxiv.org/abs/1706.03762?doi=10.48550/arXiv.1706.03762"
    assert ids.extract(url).kind is IdKind.DOI


# ------------------------------------------------------- the single egress point


def test_no_module_under_resolve_imports_urllib_request():
    """The single-egress-point claim, enforced mechanically rather than promised.

    Everything in `resolve/` reaches the network through `fetch.http_get`, which is what
    keeps the bounded, http(s)-only opener and its redirect cap on every path. A future
    contributor adding `import urllib.request` for one quick call would silently open a
    second way out; this makes that a test failure instead.
    """
    package = Path(__file__).resolve().parents[1] / "src" / "reasonable_answer" / "resolve"
    modules = sorted(package.glob("*.py"))
    assert modules, "the resolve package should have modules to check"

    offenders = []
    for module in modules:
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "urllib.request" or n.startswith("urllib.request.") for n in names):
                offenders.append(module.name)
    assert offenders == []


# ------------------------------------------------------------------- providers


def crossref(**kwargs):
    from reasonable_answer.resolve.scholarly import Crossref

    return Crossref(timeout=5, **kwargs)


def test_crossref_parses_a_real_response_shape(monkeypatch):
    seq = http_sequence(json_stub(fixture("crossref_work.json")))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)

    record = crossref().metadata(ids.extract(DOI_URL))

    assert record.title == "Highly accurate protein structure prediction with AlphaFold"
    assert record.authors == ("John Jumper", "Richard Evans", "AlphaFold Team")
    assert record.year == 2021
    assert record.venue == "Nature"
    assert record.doi == "10.1038/s41586-021-03819-2"
    assert record.registry == "crossref"
    # JATS markup would read as structure once fenced into a prompt.
    assert record.abstract.startswith("Abstract Proteins are essential to life")
    assert "<jats:p>" not in record.abstract


def test_crossref_404_is_absence_and_a_500_is_not(monkeypatch):
    """The tri-state the whole ladder rests on. A registry that is merely down must never
    be read as a registry that has never heard of the source."""
    from reasonable_answer.resolve.base import ProviderUnavailable

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", http_sequence(http_error(404))
    )
    assert crossref().metadata(ids.extract(DOI_URL)) is None

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", http_sequence(http_error(500))
    )
    with pytest.raises(ProviderUnavailable):
        crossref().metadata(ids.extract(DOI_URL))


def test_the_polite_pool_contact_is_sent_only_when_configured(monkeypatch):
    seq = http_sequence(json_stub(fixture("crossref_work.json")))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)
    crossref(contact_email="ops@example.org").metadata(ids.extract(DOI_URL))
    assert "mailto=ops%40example.org" in seq.urls[0]

    seq = http_sequence(json_stub(fixture("crossref_work.json")))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)
    crossref().metadata(ids.extract(DOI_URL))
    assert "mailto" not in seq.urls[0]


def test_openalex_rebuilds_the_abstract_and_names_the_oa_pdf(monkeypatch):
    from reasonable_answer.resolve.scholarly import OpenAlex

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(json_stub(fixture("openalex_work.json"))),
    )
    record = OpenAlex(timeout=5).metadata(ids.extract(DOI_URL))
    # Shipped as {word: [positions]}; a word-position map is not something a critic can
    # judge a citation against.
    assert record.abstract == "Proteins are essential to life."
    assert record.doi == "10.1038/s41586-021-03819-2"

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(json_stub(fixture("openalex_work.json"))),
    )
    assert OpenAlex(timeout=5).open_access_url(ids.extract(DOI_URL)).endswith(".pdf")


def test_unpaywall_refuses_to_be_built_without_a_contact_email():
    """Unpaywall answers 422 to an anonymous request, so constructing one would produce a
    provider that fails on every call."""
    from reasonable_answer.resolve.scholarly import ContactEmailRequired, Unpaywall

    with pytest.raises(ContactEmailRequired):
        Unpaywall(timeout=5, contact_email="")


def test_unpaywall_reads_the_best_oa_location(monkeypatch):
    from reasonable_answer.resolve.scholarly import Unpaywall

    seq = http_sequence(json_stub(fixture("unpaywall.json")))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)
    url = Unpaywall(timeout=5, contact_email="ops@example.org").open_access_url(
        ids.extract(DOI_URL)
    )
    assert url.endswith(".pdf")
    assert "email=ops%40example.org" in seq.urls[0]


def test_europe_pmc_reads_a_search_result_and_prefers_the_pdf(monkeypatch):
    from reasonable_answer.resolve.scholarly import EuropePmc

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(json_stub(fixture("europepmc_search.json"))),
    )
    ident = ids.extract("https://pubmed.ncbi.nlm.nih.gov/34265844/")
    record = EuropePmc(timeout=5).metadata(ident)
    assert record.year == 2021 and record.venue == "Nature"
    assert record.authors[0] == "Jumper J"

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(json_stub(fixture("europepmc_search.json"))),
    )
    assert EuropePmc(timeout=5).open_access_url(ident).endswith("?pdf=render")


def test_europe_pmc_is_not_authoritative_about_a_doi():
    """Its coverage is biomedical. Reading a physics DOI's absence from it as
    non-existence would mint a blocking defect out of a coverage boundary."""
    from reasonable_answer.resolve.scholarly import EuropePmc

    provider = EuropePmc(timeout=5)
    assert provider.supports(IdKind.DOI)
    assert not provider.authoritative(IdKind.DOI)
    assert provider.authoritative(IdKind.PMID)


def test_europe_pmc_empty_result_list_is_absence(monkeypatch):
    from reasonable_answer.resolve.scholarly import EuropePmc

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(json_stub({"hitCount": 0, "resultList": {"result": []}})),
    )
    ident = ids.extract("https://pubmed.ncbi.nlm.nih.gov/99999999/")
    assert EuropePmc(timeout=5).metadata(ident) is None


def test_arxiv_parses_an_atom_entry(monkeypatch):
    from reasonable_answer.resolve.scholarly import Arxiv

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(http_stub(fixture("arxiv_entry.xml"), ctype="application/atom+xml")),
    )
    record = Arxiv(timeout=5).metadata(ids.extract("https://arxiv.org/abs/1706.03762"))
    assert record.title == "Attention Is All You Need"
    assert record.authors == ("Ashish Vaswani", "Noam Shazeer")
    assert record.year == 2017 and record.venue == "arXiv"


def test_arxiv_reports_an_error_entry_as_absence(monkeypatch):
    """arXiv answers an unknown id with HTTP 200 and an entry titled 'Error', so the
    status line cannot carry this one."""
    from reasonable_answer.resolve.scholarly import Arxiv

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(http_stub(fixture("arxiv_absent.xml"), ctype="application/atom+xml")),
    )
    assert Arxiv(timeout=5).metadata(ids.extract("https://arxiv.org/abs/9999.99999")) is None


def test_arxiv_offers_the_pdf_not_the_abstract_page(monkeypatch):
    """The `/abs/` page fetches cleanly as HTML and is an *abstract*. Handing it back as
    the source's body would present a summary as the full text."""
    from reasonable_answer.resolve.scholarly import Arxiv

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        http_sequence(http_stub(fixture("arxiv_entry.xml"), ctype="application/atom+xml")),
    )
    url = Arxiv(timeout=5).open_access_url(ids.extract("https://arxiv.org/abs/1706.03762"))
    assert url == "https://arxiv.org/pdf/1706.03762"


# ------------------------------------------------------------- CORE (the keyed provider)


def test_core_refuses_to_be_built_without_an_api_key():
    """The keyed counterpart to `test_unpaywall_refuses_to_be_built_without_a_contact_email`.
    A keyed provider with no key makes no successful call ever, so constructing one would
    spend the tier's budget on 401s and report them as coverage — fatal, not degraded."""
    from reasonable_answer.resolve.scholarly import ApiKeyRequired, Core

    with pytest.raises(ApiKeyRequired):
        Core(timeout=5, api_key="")


def test_core_reads_the_full_text_link_and_answers_only_for_a_doi(monkeypatch):
    """`open_access_url` parses CORE's `fullTextLink`, and `supports` accepts a DOI alone —
    CORE keys on the DOI, so an arXiv or PMID identifier is not its question to answer."""
    from reasonable_answer.resolve.scholarly import Core

    core = Core(timeout=5, api_key="secret")
    assert core.supports(IdKind.DOI)
    assert not core.supports(IdKind.ARXIV)

    seq = http_sequence(json_stub({"fullTextLink": "https://core.ac.uk/download/42.pdf"}))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)
    assert core.open_access_url(ids.extract(DOI_URL)) == "https://core.ac.uk/download/42.pdf"


def test_core_without_a_key_is_fatal_not_skipped_like_a_missing_contact_email():
    """`_construct`'s two credential arms are graded differently on purpose: a missing
    contact email warns and skips (a courtesy), a missing API key propagates. This drives
    the `except ApiKeyRequired: raise` arm and the `NEEDS_API_KEY` true-branch that feeds
    it — the false side is already exercised by every keyless provider."""
    from reasonable_answer.resolve import build
    from reasonable_answer.resolve.scholarly import ApiKeyRequired

    with pytest.raises(ApiKeyRequired):
        build(
            identifier_providers=[],
            identifier_timeout=5,
            identifier_budget=10,
            open_access_providers=["core"],
            open_access_timeout=5,
            open_access_budget=10,
            core_api_key="",
        )


def test_core_with_a_key_is_constructed_on_the_open_access_tier():
    """The positive side of the same branch: a key present, `NEEDS_API_KEY` routes it to
    the constructor, and CORE joins the open-access providers rather than being skipped."""
    from reasonable_answer.resolve import build

    resolver, _ = build(
        identifier_providers=[],
        identifier_timeout=5,
        identifier_budget=10,
        open_access_providers=["core"],
        open_access_timeout=5,
        open_access_budget=10,
        core_api_key="secret",
    )
    assert [p.name for p in resolver._open_access_providers] == [Provider.CORE]


# ------------------------------------------------------------- provider records


def test_metadata_fields_are_capped_like_search_results():
    """Per-field caps, in the manner of `search.MAX_TITLE_CHARS`: a registry record is
    third-party text in a critic's context and one pathological abstract must not
    dominate what the evidence lens reads about twelve sources."""
    from reasonable_answer import fetch as fetch_mod

    record = SourceMetadata(
        title="t" * 5_000,
        authors=tuple(f"author {i}" for i in range(50)),
        abstract="a" * 50_000,
        venue="v" * 5_000,
    )
    assert len(record.title) == fetch_mod.MAX_METADATA_TITLE_CHARS
    assert len(record.authors) == fetch_mod.MAX_METADATA_AUTHORS
    assert len(record.abstract) == fetch_mod.MAX_METADATA_ABSTRACT_CHARS
    assert len(record.venue) == fetch_mod.MAX_METADATA_VENUE_CHARS


# --------------------------------------------------------------------- the ladder


def resolver(*, metadata=(), open_access=(), metadata_budget=10, oa_budget=10):
    return SourceResolver(
        metadata_providers=list(metadata),
        open_access_providers=list(open_access),
        metadata_budget=QueryBudget(metadata_budget),
        open_access_budget=QueryBudget(oa_budget),
    )


class FakeProvider:
    """A provider whose answers are scripted, so a ladder test is about the ladder."""

    def __init__(
        self,
        name,
        *,
        record="unavailable",
        oa=None,
        kinds=(IdKind.DOI,),
        authoritative_for=None,
    ):
        self.name = name
        self._record = record
        self._oa = oa
        self._kinds = set(kinds)
        self._authoritative = set(
            authoritative_for if authoritative_for is not None else kinds
        )
        self.metadata_calls = 0
        self.oa_calls = 0

    def supports(self, kind):
        return kind in self._kinds

    def authoritative(self, kind):
        return kind in self._authoritative

    def metadata(self, ident):
        from reasonable_answer.resolve.base import ProviderUnavailable

        self.metadata_calls += 1
        if self._record == "unavailable":
            raise ProviderUnavailable("scripted")
        return self._record

    def open_access_url(self, ident):
        self.oa_calls += 1
        return self._oa


RECORD = SourceMetadata(title="A real paper", year=2021, registry="crossref")
BLOCKED = FetchedSource(url=PAYWALL_URL, status=403, error="HTTP 403")
MISSING = FetchedSource(url=PAYWALL_URL, status=404, error="HTTP 404")
UNREACHABLE = FetchedSource(url=PAYWALL_URL, error="URLError: Name or service not known")


def never_fetched(url):
    raise AssertionError(f"tier 1 must not have fetched {url}")


def test_a_confirmed_source_behind_a_refusal_is_paywalled():
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)])
    result = r.resolve(PAYWALL_URL, BLOCKED, never_fetched)

    assert result.outcome is SourceOutcome.PAYWALLED
    assert result.metadata is RECORD
    assert result.tier is ResolutionTier.IDENTIFIER
    assert result.provider is Provider.CROSSREF
    # And still not `ok`: an abstract is never quotable evidence.
    assert not result.ok and result.text == ""


def test_paywalled_is_never_guessed_from_a_status_alone():
    """HTTP 402 is rare and a real paywall usually answers 200 with a teaser, so the
    corroboration IS the claim. Without a registry the direct verdict stands."""
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF)])  # unavailable
    assert r.resolve(PAYWALL_URL, BLOCKED, never_fetched).outcome is SourceOutcome.BLOCKED


def test_a_confirmed_source_that_merely_would_not_read_is_metadata_only():
    empty = FetchedSource(url=PAYWALL_URL, status=200, error="no readable text")
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)])
    result = r.resolve(PAYWALL_URL, empty, never_fetched)

    assert result.outcome is SourceOutcome.METADATA_ONLY
    assert not result.ok


def test_registry_confirmation_outranks_a_404_on_the_cited_url():
    """The insight the whole feature rests on, in its sharpest form: the citation names
    an identifier a registry holds, so the source is real and a dead link is a dead link.
    `unresolvable` must go false, or D-notfound-fabrication mints a blocking defect against a real paper."""
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)])
    result = r.resolve(DOI_URL, MISSING, never_fetched)

    assert result.outcome is SourceOutcome.METADATA_ONLY
    assert not result.unresolvable


def test_an_identifier_no_authoritative_registry_holds_is_not_found():
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=None)])
    result = r.resolve(DOI_URL, UNREACHABLE, never_fetched)

    assert result.outcome is SourceOutcome.NOT_FOUND
    assert result.unresolvable, "this is what D-notfound-fabrication mints fabricated_citation from"


def test_a_denial_never_overrides_a_live_server():
    """A 403 is a live server refusing a *client*; a 200 that would not parse means the
    URL resolves. Neither may be turned into a fabricated citation by a coverage gap."""
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=None)])
    unreadable = FetchedSource(
        url=PAYWALL_URL, status=200, error="unreadable content type (application/zip)",
        outcome=SourceOutcome.UNREADABLE,
    )

    assert r.resolve(PAYWALL_URL, BLOCKED, never_fetched).outcome is SourceOutcome.BLOCKED
    assert (
        r.resolve(PAYWALL_URL, unreadable, never_fetched).outcome is SourceOutcome.UNREADABLE
    )


def test_a_non_authoritative_denial_is_not_a_denial():
    """Europe PMC's silence about a physics DOI is a coverage boundary. On its own it must
    leave the direct verdict exactly as it found it."""
    provider = FakeProvider(
        Provider.EUROPE_PMC, record=None, kinds=(IdKind.DOI,), authoritative_for=()
    )
    result = resolver(metadata=[provider]).resolve(DOI_URL, UNREACHABLE, never_fetched)

    assert provider.metadata_calls == 1
    assert result.outcome is SourceOutcome.ERROR


def test_an_unavailable_registry_is_never_read_as_absence():
    """A timed-out Crossref would otherwise become evidence that a real paper does not
    exist — a blocking defect manufactured out of a network condition."""
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF)])
    assert r.resolve(DOI_URL, UNREACHABLE, never_fetched).outcome is SourceOutcome.ERROR


def test_a_url_with_no_identifier_leaves_the_direct_verdict_alone():
    provider = FakeProvider(Provider.CROSSREF, record=RECORD)
    news = FetchedSource(url="https://news.test/story", status=403, error="HTTP 403")
    result = resolver(metadata=[provider]).resolve(news.url, news, never_fetched)

    assert result is news
    assert provider.metadata_calls == 0


# ---------------------------------------------------------------------- tier 1


def read_ok(url):
    return FetchedSource(url=url, title="Mirror", text="Body from the mirror.")


def test_an_open_access_body_is_marked_as_coming_from_elsewhere():
    r = resolver(
        metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)],
        open_access=[FakeProvider(Provider.OPENALEX, oa="https://mirror.test/paper.pdf")],
    )
    result = r.resolve(PAYWALL_URL, BLOCKED, read_ok)

    assert result.ok and result.text == "Body from the mirror."
    # The result is still that citation — read from somewhere else, and saying so.
    assert result.url == PAYWALL_URL
    assert result.body_source_url == "https://mirror.test/paper.pdf"
    assert result.tier is ResolutionTier.OPEN_ACCESS
    assert result.provider is Provider.OPENALEX
    # Tier 0 ran anyway: the attributed title is checkable even when a body arrived.
    assert result.metadata is RECORD


def test_a_mirror_that_will_not_read_falls_back_to_metadata():
    def refused(url):
        return FetchedSource(url=url, status=403, error="HTTP 403")

    r = resolver(
        metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)],
        open_access=[FakeProvider(Provider.OPENALEX, oa="https://mirror.test/paper.pdf")],
    )
    result = r.resolve(PAYWALL_URL, BLOCKED, refused)

    assert result.outcome is SourceOutcome.PAYWALLED
    assert result.body_source_url is None


def test_the_second_open_access_provider_is_not_asked_once_the_first_answers():
    first = FakeProvider(Provider.OPENALEX, oa="https://mirror.test/paper.pdf")
    second = FakeProvider(Provider.UNPAYWALL, oa="https://other.test/paper.pdf")
    r = resolver(open_access=[first, second])
    r.resolve(PAYWALL_URL, BLOCKED, read_ok)

    assert first.oa_calls == 1
    assert second.oa_calls == 0, "they answer the same question; the second costs budget"


def test_no_open_access_call_is_made_for_an_identifier_no_registry_holds():
    oa = FakeProvider(Provider.UNPAYWALL, oa="https://mirror.test/paper.pdf")
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=None)], open_access=[oa])
    r.resolve(DOI_URL, UNREACHABLE, never_fetched)

    assert oa.oa_calls == 0, "no free copy exists of a paper nobody has heard of"


# ----------------------------------------------------------------------- caches


def test_two_urls_naming_one_doi_share_a_single_registry_call():
    provider = FakeProvider(Provider.CROSSREF, record=RECORD)
    r = resolver(metadata=[provider])
    r.resolve(DOI_URL, BLOCKED, never_fetched)
    r.resolve("https://www.nature.com/articles/10.1038/s41586-021-03819-2", BLOCKED, never_fetched)

    assert provider.metadata_calls == 1


def test_asked_and_none_is_distinguished_from_not_asked():
    """Without the distinction a twelve-source report makes twelve identical Unpaywall
    calls to rediscover the same absence."""
    provider = FakeProvider(Provider.UNPAYWALL, oa=None)
    r = resolver(open_access=[provider])
    for _ in range(3):
        r.resolve(DOI_URL, BLOCKED, never_fetched)

    assert provider.oa_calls == 1


def test_the_cache_is_monotone_within_a_run():
    """Once written, never re-resolved: every round of a run judges the same evidence, and
    a provider that was flaky at round two does not become authoritative at round six."""
    provider = FakeProvider(Provider.CROSSREF)  # unavailable
    r = resolver(metadata=[provider])
    first = r.resolve(DOI_URL, BLOCKED, never_fetched)
    provider._record = RECORD  # the registry "recovers"
    second = r.resolve(DOI_URL, BLOCKED, never_fetched)

    assert first.outcome is second.outcome is SourceOutcome.BLOCKED
    assert provider.metadata_calls == 1


# ---------------------------------------------------------------------- budgets


def test_budget_exhaustion_says_so_instead_of_blaming_the_site():
    provider = FakeProvider(Provider.CROSSREF, record=RECORD)
    r = resolver(metadata=[provider], metadata_budget=1)
    r.resolve(DOI_URL, BLOCKED, never_fetched)
    second = r.resolve("https://doi.org/10.1038/other-paper", BLOCKED, never_fetched)

    assert second.outcome is SourceOutcome.BUDGET_EXHAUSTED
    assert second.outcome is not SourceOutcome.BLOCKED


def test_budget_exhaustion_never_suppresses_a_mechanical_not_found():
    """Otherwise a run that exhausts a tier at source five silently stops reporting D-notfound-fabrication's
    finding for sources six through twelve — turning a tier on would weaken a defect the
    pipeline raises without it."""
    r = resolver(metadata=[FakeProvider(Provider.CROSSREF, record=RECORD)], metadata_budget=0)
    result = r.resolve(DOI_URL, MISSING, never_fetched)

    assert result.outcome is SourceOutcome.NOT_FOUND
    assert result.unresolvable


def test_an_exhausted_open_access_budget_is_not_cached_as_no_copy_exists():
    provider = FakeProvider(Provider.UNPAYWALL, oa="https://mirror.test/paper.pdf")
    r = resolver(open_access=[provider], oa_budget=0)
    assert r.resolve(DOI_URL, BLOCKED, never_fetched).outcome is SourceOutcome.BUDGET_EXHAUSTED

    # A question the budget refused was never asked, so nothing was learned to cache.
    generous = resolver(open_access=[provider])
    assert generous.resolve(DOI_URL, BLOCKED, read_ok).ok


# ----------------------------------------------------- wiring through the fetcher


def test_the_ladder_runs_only_when_the_direct_fetch_yielded_nothing(monkeypatch):
    class _Boom:
        def resolve(self, url, direct, fetch_body):
            raise AssertionError("the ladder must not run for a page that read fine")

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda self, *a, **k: http_stub(PAGE)
    )
    assert SourceFetcher(resolver=_Boom()).fetch(PAYWALL_URL).ok


def test_one_logical_fetch_walks_the_whole_ladder_in_order(monkeypatch):
    """The sequence is the point: cited URL, registries, mirror — and the mirror is
    fetched through the same direct path, which is why its body is real page text."""
    seq = http_sequence(
        http_error(403),  # the cited URL refuses an automated client
        json_stub(fixture("crossref_work.json")),  # tier 0: existence + details
        json_stub(fixture("openalex_work.json")),  # tier 0, second registry
        json_stub(fixture("openalex_work.json")),  # tier 1: best_oa_location
        http_stub(PAGE),  # the mirror itself
    )
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)

    resolver_, warnings = build(
        identifier_providers=["crossref", "openalex"],
        identifier_timeout=5,
        identifier_budget=10,
        open_access_providers=["openalex"],
        open_access_timeout=5,
        open_access_budget=10,
    )
    result = SourceFetcher(resolver=resolver_).fetch(PAYWALL_URL)

    assert warnings == []
    assert [u.split("/")[2] for u in seq.urls] == [
        "www.tandfonline.com",
        "api.crossref.org",
        "api.openalex.org",
        "api.openalex.org",
        "www.nature.com",
    ]
    assert result.ok and "Full body text." in result.text
    assert result.tier is ResolutionTier.OPEN_ACCESS
    assert result.body_source_url.endswith(".pdf")
    assert result.metadata.title.startswith("Highly accurate")


def test_the_mirror_is_fetched_once_and_never_re_enters_the_ladder(monkeypatch):
    """No recursion, by an explicit depth argument rather than a convention: a mirror that
    redirects into another mirror could otherwise walk a run's whole fetch budget."""
    seen: list[str] = []

    class _Loop:
        def resolve(self, url, direct, fetch_body):
            seen.append(url)
            # A mirror that itself fails. If the depth argument were not threaded through,
            # this would land back here and keep going.
            fetch_body("https://mirror.test/copy")
            return direct

    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", http_sequence(http_error(403), http_error(403))
    )
    result = SourceFetcher(resolver=_Loop()).fetch(PAYWALL_URL)

    assert seen == [PAYWALL_URL], "the ladder saw the cited URL exactly once"
    assert not result.ok


def test_a_resolved_result_is_cached_like_any_other(monkeypatch):
    seq = http_sequence(
        http_error(403),
        json_stub(fixture("crossref_work.json")),
    )
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", seq)
    resolver_, _ = build(
        identifier_providers=["crossref"],
        identifier_timeout=5,
        identifier_budget=10,
        open_access_providers=[],
        open_access_timeout=5,
        open_access_budget=0,
    )
    fetcher = SourceFetcher(resolver=resolver_)
    first = fetcher.fetch(DOI_URL)
    second = fetcher.fetch(DOI_URL)

    assert first is second
    assert seq.exhausted, "the second fetch made no request at all"
    # Self-describing: empty text and a non-None error, so every consumer written before
    # this feature already treats it as the failure it is.
    assert first.outcome is SourceOutcome.PAYWALLED
    assert first.text == "" and first.error is not None


# ------------------------------------------------------------------ construction


def test_a_disabled_tier_constructs_nothing():
    resolver_, _ = build(
        identifier_providers=["crossref"],
        identifier_timeout=5,
        identifier_budget=10,
        open_access_providers=[],
        open_access_timeout=5,
        open_access_budget=10,
    )
    assert resolver_._open_access_providers == []
    assert len(resolver_._metadata_providers) == 1


def test_an_unknown_provider_name_is_fatal():
    """A typo silently disables a tier the operator believes they enabled — the same
    class of failure `_build_searcher` refuses to start with."""
    with pytest.raises(UnknownProvider, match="openalexx"):
        build(
            identifier_providers=["openalexx"],
            identifier_timeout=5,
            identifier_budget=10,
            open_access_providers=[],
            open_access_timeout=5,
            open_access_budget=10,
        )


def test_a_provider_listed_under_the_wrong_tier_is_fatal():
    with pytest.raises(UnknownProvider):
        build(
            identifier_providers=["unpaywall"],  # answers open access, not existence
            identifier_timeout=5,
            identifier_budget=10,
            open_access_providers=[],
            open_access_timeout=5,
            open_access_budget=10,
            contact_email="ops@example.org",
        )


def test_a_missing_contact_email_costs_one_provider_and_warns():
    resolver_, warnings = build(
        identifier_providers=[],
        identifier_timeout=5,
        identifier_budget=10,
        open_access_providers=["unpaywall", "arxiv"],
        open_access_timeout=5,
        open_access_budget=10,
    )
    assert [p.name for p in resolver_._open_access_providers] == [Provider.ARXIV]
    assert warnings and "contact email" in warnings[0]


def test_no_provider_request_url_reaches_the_logs(monkeypatch, caplog):
    """RA-016: names are safe to log, URLs are not — the polite-pool querystring carries
    the operator's email, and the identifier is derived from private run material."""
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", http_sequence(http_error(500))
    )
    with caplog.at_level("DEBUG"):
        r = resolver(metadata=[FakeProvider(Provider.CROSSREF)])
        r.resolve(DOI_URL, BLOCKED, never_fetched)
        build(
            identifier_providers=["crossref"],
            identifier_timeout=5,
            identifier_budget=1,
            open_access_providers=[],
            open_access_timeout=5,
            open_access_budget=0,
            contact_email="ops@example.org",
        )[0].resolve(DOI_URL, BLOCKED, never_fetched)

    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert "crossref" in blob, "the provider name is what an operator needs"
    assert "ops@example.org" not in blob
    assert "10.1038" not in blob
    assert "api.crossref.org" not in blob


# ------------------------------------------------------------ startup construction


def with_sources(config, **tiers):
    """`config`, with the master switch on and the named tiers configured."""
    sources = config.sources.model_copy(update={"enabled": True, **tiers})
    return config.model_copy(update={"sources": sources})


def test_no_tier_enabled_builds_no_resolver(config):
    from reasonable_answer.config import IdentifierTierConfig
    from reasonable_answer.graph import _build_resolver

    assert _build_resolver(config, []) is None
    # The master switch off is enough on its own: one tier's switch must never be able to
    # turn the feature on by itself.
    only_tier = config.model_copy(
        update={
            "sources": config.sources.model_copy(
                update={"identifiers": IdentifierTierConfig(enabled=True)}
            )
        }
    )
    assert _build_resolver(only_tier, []) is None


def test_enabling_one_tier_never_constructs_the_other(config):
    from reasonable_answer.config import IdentifierTierConfig
    from reasonable_answer.graph import _build_resolver

    cfg = with_sources(config, identifiers=IdentifierTierConfig(enabled=True))
    built = _build_resolver(cfg, [])

    assert [p.name for p in built._metadata_providers] == [Provider.CROSSREF, Provider.OPENALEX]
    assert built._open_access_providers == []


def test_an_unknown_provider_name_refuses_to_start(config):
    from reasonable_answer.config import ConfigError, IdentifierTierConfig
    from reasonable_answer.graph import _build_resolver

    cfg = with_sources(
        config,
        identifiers=IdentifierTierConfig(enabled=True, providers=["crossrefff"]),
    )
    with pytest.raises(ConfigError, match="crossrefff"):
        _build_resolver(cfg, [])


def test_a_missing_contact_email_warns_about_what_is_actually_lost(config, monkeypatch):
    """A warning, not a failure — but one that names the consequence. 'RA_CONTACT_EMAIL
    is unset' tells an operator nothing; 'your requests are being served from the
    anonymous rate-limit pool' tells them whether they care."""
    from reasonable_answer.config import IdentifierTierConfig
    from reasonable_answer.graph import _build_resolver

    monkeypatch.delenv("RA_CONTACT_EMAIL", raising=False)
    cfg = with_sources(config, identifiers=IdentifierTierConfig(enabled=True))
    warnings: list[str] = []
    assert _build_resolver(cfg, warnings) is not None

    joined = " ".join(warnings)
    assert "RA_CONTACT_EMAIL" in joined
    assert "anonymous rate-limit pool" in joined


def test_the_contact_email_comes_from_the_environment(config, monkeypatch):
    monkeypatch.setenv("RA_CONTACT_EMAIL", " ops@example.org ")
    assert config.sources.contact_email == "ops@example.org"


def test_open_access_with_core_but_no_key_refuses_to_start(config, tmp_path, monkeypatch):
    """CORE is the one keyed member of the open-access tier, so it inherits the paid tiers'
    fail-closed posture: enabling it with no key resolvable is fatal at startup, the
    counterpart to the *warning* a missing contact email earns."""
    from reasonable_answer.config import OpenAccessTierConfig
    from reasonable_answer.graph import _build_resolver
    from reasonable_answer.search import SearchConfigError

    monkeypatch.delenv("CORE_API_KEY", raising=False)
    cfg = with_sources(
        config,
        open_access=OpenAccessTierConfig(
            enabled=True,
            providers=["core"],
            core_token_file=str(tmp_path / "absent.token"),
        ),
    )
    with pytest.raises(SearchConfigError):
        _build_resolver(cfg, [])


def test_open_access_without_pdf_reading_says_what_it_will_cost(config):
    """arXiv has no other form, and most other free copies are PDFs too. The tier still
    helps; it just helps far less than its call budget implies."""
    from reasonable_answer.config import OpenAccessTierConfig
    from reasonable_answer.graph import _build_resolver

    cfg = with_sources(config, open_access=OpenAccessTierConfig(enabled=True))
    warnings: list[str] = []
    _build_resolver(cfg, warnings)

    assert any("sources.pdf.enabled is off" in w for w in warnings)


def test_tiers_without_anything_to_fetch_say_so(config):
    """The tiers do nothing unless `search.verify_sources` is on, because nothing fetches
    in the first place. Silence there is a config that looks enabled and is inert."""
    from reasonable_answer.config import IdentifierTierConfig
    from reasonable_answer.graph import _build_resolver

    cfg = with_sources(config, identifiers=IdentifierTierConfig(enabled=True))
    warnings: list[str] = []
    _build_resolver(cfg, warnings)

    assert any("verify_sources" in w for w in warnings)


# ------------------------------------------------------------ the two hard guards


def metadata_page(outcome=SourceOutcome.METADATA_ONLY, abstract="The quoted sentence."):
    return FetchedSource(
        url=PAYWALL_URL,
        error="HTTP 403; crossref confirms the source exists",
        outcome=outcome,
        metadata=SourceMetadata(title="A real paper", abstract=abstract, registry="crossref"),
        tier=ResolutionTier.IDENTIFIER,
        provider=Provider.CROSSREF,
    )


class _OnePage:
    def __init__(self, page):
        self.page = page

    def fetch(self, url):
        return self.page


def dispute_for(url: str):
    from reasonable_answer.schemas import Dispute

    return Dispute(task_index=0, grounds="g" * 20, evidence_url=url,
                   evidence_quote="The quoted sentence.")


def citation_defect():
    from reasonable_answer.schemas import Defect, StructuralRef
    from reasonable_answer.taxonomy import Category, Severity

    return Defect(
        locus=StructuralRef(section=1, paragraph=1),
        category=Category.MISREPRESENTED_SOURCE,
        severity=Severity.MAJOR,
        claim_span="the claim",
        rationale="r",
        instruction="i",
    )


REPORT_CITING = f"# T\n\nClaim [1].\n\n## Sources\n\n[1] {PAYWALL_URL}\n"


def test_an_abstract_can_never_uphold_a_dispute():
    """For free, via `.ok`: a METADATA_ONLY result carries no text at all, so there is
    nothing for a quote to match against."""
    from reasonable_answer.dispute import adjudicate_mechanical

    verdict = adjudicate_mechanical(
        dispute_for(PAYWALL_URL), citation_defect(), REPORT_CITING,
        _OnePage(metadata_page()),
    )
    assert verdict is None


def test_a_mirror_body_can_never_uphold_a_dispute_about_the_cited_url():
    """A quote present in arXiv v1 and absent from the published paper would otherwise
    settle a dispute about a page nobody read."""
    from reasonable_answer.dispute import adjudicate_mechanical

    mirror = FetchedSource(
        url=PAYWALL_URL,
        text="The quoted sentence.",
        body_source_url="https://arxiv.org/pdf/1706.03762",
        tier=ResolutionTier.OPEN_ACCESS,
    )
    direct = FetchedSource(url=PAYWALL_URL, text="The quoted sentence.")

    assert adjudicate_mechanical(
        dispute_for(PAYWALL_URL), citation_defect(), REPORT_CITING, _OnePage(mirror)
    ) is None
    # The same quote, read from the cited URL itself, still upholds — the guard is about
    # provenance, not about the quote.
    assert adjudicate_mechanical(
        dispute_for(PAYWALL_URL), citation_defect(), REPORT_CITING, _OnePage(direct)
    ) is True


def test_a_body_less_outcome_cannot_claim_a_body_source():
    with pytest.raises(ValueError, match="carries no body"):
        FetchedSource(
            url="u", error="HTTP 403", outcome=SourceOutcome.BLOCKED,
            body_source_url="https://mirror.test/x",
        )


# ------------------------------------------------------------------ prompt shape


def test_a_confirmed_source_is_announced_as_existing_not_as_a_failure():
    block = prompts.fetched_sources_block([metadata_page()])

    assert "CONFIRMED TO EXIST" in block
    assert "NOT readable" in block
    assert "Title: A real paper" in block
    assert "Registry: crossref" in block


def test_an_abstract_is_labelled_as_not_being_the_source_text():
    block = prompts.fetched_sources_block([metadata_page()])

    assert "ABSTRACT" in block
    assert "NOT the full text" in block
    assert "a summary the authors wrote" in block
    assert "NEVER raise `misrepresented_source` against a source shown only as registry" in block


def test_a_paywalled_source_is_not_offered_as_a_misrepresentation_candidate():
    prompt = prompts.critic_user(Lens.EVIDENCE, "q?", "report", [metadata_page(SourceOutcome.PAYWALLED)])

    assert "behind a paywall" in prompt
    # `misrepresented_source` sharpens only when some source's body actually arrived, and
    # a metadata record is not a body.
    assert "the fetched page does not contain the claim" not in prompt
    assert "plainly does not support the claim" in prompt


def test_a_mirror_body_is_disclosed_to_the_critic():
    mirror = FetchedSource(
        url=PAYWALL_URL, text="Preprint body.",
        body_source_url="https://arxiv.org/pdf/1706.03762",
    )
    block = prompts.fetched_sources_block([mirror])

    assert "NOT read from the cited URL" in block
    assert "https://arxiv.org/pdf/1706.03762" in block
    assert "preprint" in block


def test_the_arbiter_is_told_metadata_settles_nothing_either_way():
    from reasonable_answer.schemas import Dispute

    prompt = prompts.arbiter_user(
        citation_defect(),
        Dispute(task_index=0, grounds="g" * 20, evidence_url=PAYWALL_URL,
                evidence_quote="q"),
        "the paragraph", "q?", metadata_page(),
    )

    assert "CONFIRMED TO EXIST" in prompt
    assert "insufficient to settle this dispute in EITHER direction" in prompt


def test_the_arbiter_is_told_when_it_is_reading_a_mirror():
    from reasonable_answer.schemas import Dispute

    mirror = FetchedSource(
        url=PAYWALL_URL, text="Preprint body.",
        body_source_url="https://arxiv.org/pdf/1706.03762",
    )
    prompt = prompts.arbiter_user(
        citation_defect(),
        Dispute(task_index=0, grounds="g" * 20, evidence_url=PAYWALL_URL, evidence_quote="q"),
        "the paragraph", "q?", mirror,
    )

    assert "NOT read from the cited URL" in prompt
