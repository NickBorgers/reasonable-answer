"""The paid tiers: what the rendering provider is allowed to ask for, and what it is not.

Offline like every other suite here — `urllib.request.OpenerDirector.open` is stubbed, so
the real opener, the real redirect handler and `fetch._request`'s credential handling all
stay on the path under test.

The file exists chiefly for one test. `test_the_renderer_is_never_asked_to_disguise_itself`
is the doctrine of D-existence-vs-body/D-paid-tier-page expressed as something CI fails on
rather than as a comment
somebody can delete: rendering a page is in scope, impersonating a browser to defeat a bot
wall is not, and the difference is one string in a request body.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
from fakes import json_stub
from pydantic import ValidationError

from reasonable_answer.fetch import (
    FetchedSource,
    Provider,
    ResolutionTier,
    SourceOutcome,
)
from reasonable_answer.resolve import SourceResolver
from reasonable_answer.resolve.base import ProviderUnavailable
from reasonable_answer.resolve.extraction import (
    EXTRACTION_PROVIDERS,
    FORBIDDEN_PROXY_MODES,
    Firecrawl,
)
from reasonable_answer.search import QueryBudget

URL = "https://news.example/story"
BLOCKED = FetchedSource(url=URL, status=403, error="HTTP 403")


def _capture(monkeypatch, response):
    """Stub the opener, returning the list that collects each outgoing request."""
    seen = []

    def opened(self, req, *a, **k):
        seen.append(req)
        return response()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", opened)
    return seen


# ------------------------------------------------------------------------ doctrine


def test_the_renderer_is_never_asked_to_disguise_itself(monkeypatch):
    """`fetch.py` refuses to spoof a browser to get past a 403. Buying that same
    impersonation by the page from a vendor would be the same act with an invoice
    attached, so stealth is absent rather than defaulted off — there is no knob.

    Asserted against the serialised request body, not against the options constant, so
    that a future code path assembling its own payload is caught too.
    """
    seen = _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "Article text."}}))
    Firecrawl(api_key="k", timeout=5).extract(URL)

    body = json.loads(seen[0].data)
    assert body["proxy"] == "basic"
    blob = json.dumps(body).lower()
    for mode in FORBIDDEN_PROXY_MODES:
        assert mode not in blob, f"{mode!r} is impersonation bought by the page"


def test_no_configuration_can_turn_stealth_on():
    """Not a knob defaulted to off — no knob. A config field would make the doctrine an
    operator preference, which is exactly what it must not be."""
    from reasonable_answer.config import ExtractionTierConfig

    fields = set(ExtractionTierConfig.model_fields)
    assert not {f for f in fields if "stealth" in f or "proxy" in f}
    # `extra="forbid"` means a roster cannot smuggle one in either.
    with pytest.raises(ValidationError):
        ExtractionTierConfig(enabled=True, provider="firecrawl", proxy="stealth")


# ------------------------------------------------------------------------ credential


def test_the_api_key_travels_in_a_header_and_never_in_the_url(monkeypatch):
    seen = _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "text"}}))
    Firecrawl(api_key="secret-key", timeout=5).extract(URL)

    assert seen[0].headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in seen[0].full_url


def test_the_credentialled_call_refuses_redirects(monkeypatch):
    """Leans on the guard `fetch._request` provides: a key must not be replayable at a
    host nobody here chose, so the request is not redirectable at all."""
    caps = []

    def opened(self, req, *a, **k):
        caps.extend(h.max_redirections for h in self.handlers if hasattr(h, "max_redirections"))
        return json_stub({"data": {"markdown": "text"}})

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", opened)
    Firecrawl(api_key="k", timeout=5).extract(URL)

    assert caps == [0]


# ------------------------------------------------------------------------ behaviour


def test_a_rendered_body_can_settle_a_dispute_because_it_is_the_cited_page(monkeypatch):
    """The distinction from an open-access mirror, and the reason it matters.

    A mirror is a *different document* and sets `body_source_url`, which `dispute.py`
    refuses to adjudicate on. A rendered page is the cited URL itself, read by a client
    that can run its JavaScript, so it carries no mirror marker and stays usable.
    """
    _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "The claim, verbatim."}}))
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5),
        extraction_budget=QueryBudget(4),
    )
    result = resolver.resolve(URL, BLOCKED, _never_fetched)

    assert result.ok and result.outcome is SourceOutcome.FULL_TEXT
    assert result.body_source_url is None, "this is the cited page, not a copy of it"
    assert result.tier is ResolutionTier.EXTRACTION
    assert result.provider is Provider.FIRECRAWL
    assert "The claim, verbatim." in result.text


def test_extraction_is_not_attempted_against_a_definitive_not_found(monkeypatch):
    """Nothing to render at a URL the server says is not there — and a success against a
    soft-404 landing page would overwrite D-notfound-fabrication's mechanical finding."""
    seen = _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "text"}}))
    missing = FetchedSource(url=URL, status=404, error="HTTP 404")
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5), extraction_budget=QueryBudget(4)
    )
    result = resolver.resolve(URL, missing, _never_fetched)

    assert seen == [], "no paid call for a URL that does not exist"
    assert result.outcome is SourceOutcome.NOT_FOUND
    assert result.unresolvable, "D-notfound-fabrication must still mint fabricated_citation"


def test_budget_exhaustion_reads_as_itself_not_as_a_bot_wall(monkeypatch):
    _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "text"}}))
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5), extraction_budget=QueryBudget(1)
    )
    assert resolver.resolve(URL, BLOCKED, _never_fetched).ok
    second = resolver.resolve("https://news.example/other", BLOCKED, _never_fetched)

    assert second.outcome is SourceOutcome.BUDGET_EXHAUSTED


def test_a_provider_failure_leaves_the_free_ladder_s_verdict_alone(monkeypatch):
    """A vendor outage is not evidence about a citation. The direct verdict stands."""
    monkeypatch.setattr(
        Firecrawl, "extract", lambda self, url: (_ for _ in ()).throw(ProviderUnavailable("HTTP 502"))
    )
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5), extraction_budget=QueryBudget(4)
    )
    result = resolver.resolve(URL, BLOCKED, _never_fetched)

    assert result.outcome is SourceOutcome.BLOCKED


def test_an_empty_render_is_no_body_never_an_absent_page(monkeypatch):
    """The renderer has no standing to say a page does not exist, so a blank answer must
    not be allowed to become one."""
    _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "   "}}))
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5), extraction_budget=QueryBudget(4)
    )
    result = resolver.resolve(URL, BLOCKED, _never_fetched)

    assert result.outcome is SourceOutcome.BLOCKED
    assert not result.unresolvable


def test_rendered_text_obeys_the_critic_facing_character_cap(monkeypatch):
    """Every other rung is capped by `SourceFetcher`, which reads the body. This one gets
    markdown straight from a provider, so it is the rung that must cap itself."""
    _capture(monkeypatch, lambda: json_stub({"data": {"markdown": "word " * 5000}}))
    resolver = SourceResolver(
        extractor=Firecrawl(api_key="k", timeout=5),
        extraction_budget=QueryBudget(4),
        max_chars=600,
    )
    assert len(resolver.resolve(URL, BLOCKED, _never_fetched).text) == 600


def test_the_registry_is_open_but_names_nothing_by_default():
    assert set(EXTRACTION_PROVIDERS) == {"firecrawl"}


def _never_fetched(url):
    raise AssertionError("the open-access path must not run in these tests")


# ------------------------------------------------------------------ startup posture


def _sources(config, **tier):
    """A config with the extraction tier configured as given, master switch on."""
    from reasonable_answer.config import ExtractionTierConfig

    return config.model_copy(
        update={
            "sources": config.sources.model_copy(
                update={"enabled": True, "extraction": ExtractionTierConfig(**tier)}
            )
        }
    )


def test_an_enabled_tier_without_a_credential_refuses_to_start(config, tmp_path, monkeypatch):
    """The same posture `_build_searcher` applies to Brave. A tier that starts without its
    key spends its whole budget on 401s and reports them as coverage."""
    from reasonable_answer.graph import _build_resolver
    from reasonable_answer.search import SearchConfigError

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    cfg = _sources(
        config, enabled=True, provider="firecrawl", token_file=str(tmp_path / "absent.token")
    )
    with pytest.raises(SearchConfigError):
        _build_resolver(cfg, [])


def test_an_enabled_tier_naming_no_provider_refuses_to_start(config):
    """Never a default. Falling back to whichever entry happens to be first would send a
    paid call to a vendor nobody chose."""
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _build_resolver

    with pytest.raises(ConfigError, match="no provider is named"):
        _build_resolver(_sources(config, enabled=True, provider=""), [])


def test_an_unknown_provider_name_refuses_to_start(config, tmp_path, monkeypatch):
    from reasonable_answer.config import ConfigError
    from reasonable_answer.graph import _build_resolver

    monkeypatch.setenv("FIRECRAWL_API_KEY", "k")
    cfg = _sources(config, enabled=True, provider="firecrawll")
    with pytest.raises(ConfigError, match="unknown extraction provider"):
        _build_resolver(cfg, [])


def test_the_call_ceiling_is_derived_from_the_run_s_own_shape(config):
    """Citations and writer-read candidates are the two structural consumers. Derived so
    raising either budget cannot silently start starving the tier at the old number."""
    from reasonable_answer.graph import _extraction_call_ceiling

    assert _extraction_call_ceiling(config) == (
        config.search.max_source_urls * config.budgets.hard_cap
    )

    # An unbounded `read_budget` (the default since D-unbounded-evidence) contributes no
    # derivable term, so the ceiling stays the citation one rather than becoming infinite.
    with_reading = config.model_copy(
        update={
            "search": config.search.model_copy(
                update={"enabled": True, "read_sources": True, "read_budget": None}
            )
        }
    )
    assert _extraction_call_ceiling(with_reading) == (
        config.search.max_source_urls * config.budgets.hard_cap
    )

    with_bounded_reading = config.model_copy(
        update={
            "search": config.search.model_copy(
                update={"enabled": True, "read_sources": True, "read_budget": 24}
            )
        }
    )
    assert _extraction_call_ceiling(with_bounded_reading) == (
        config.search.max_source_urls * config.budgets.hard_cap + 24
    )

    pinned = config.model_copy(
        update={
            "sources": config.sources.model_copy(
                update={"extraction": config.sources.extraction.model_copy(
                    update={"max_calls_per_run": 7}
                )}
            )
        }
    )
    assert _extraction_call_ceiling(pinned) == 7, "an explicit number still wins"


# ---------------------------------------------------------- the delivery seam (D-paid-tier-page)


def test_enabling_delivery_without_a_provider_is_fatal():
    """The seam ships with no provider behind it, so enabling it while naming none can
    never make a call. D-paid-tier-page says that is fatal at load — inert rather than half-built —
    and here that promise is enforced rather than merely written down."""
    from reasonable_answer.config import (
        ConfigError,
        DeliveryTierConfig,
        SourcesConfig,
    )

    with pytest.raises(ConfigError, match="sources.delivery.enabled is on but no provider"):
        SourcesConfig(enabled=True, delivery=DeliveryTierConfig(enabled=True, provider=""))


def test_delivery_is_inert_when_the_master_switch_is_off():
    """Consistent with every other tier: the subsystem does nothing with `sources.enabled`
    off, so a delivery stanza there is not yet a claim to enforce."""
    from reasonable_answer.config import DeliveryTierConfig, SourcesConfig

    # No raise: the empty-provider delivery tier is only fatal once the resolver is on.
    cfg = SourcesConfig(enabled=False, delivery=DeliveryTierConfig(enabled=True, provider=""))
    assert cfg.delivery.enabled and not cfg.delivery.provider


def test_a_named_delivery_provider_is_accepted_even_though_none_ships():
    """The registry is open. Naming a provider clears the fail-closed check; that no such
    provider is built yet is a separate matter the resolver ladder owns."""
    from reasonable_answer.config import DeliveryTierConfig, SourcesConfig

    cfg = SourcesConfig(enabled=True, delivery=DeliveryTierConfig(enabled=True, provider="acme"))
    assert cfg.delivery.provider == "acme"
