"""The bibliographic registries: Crossref, OpenAlex, Unpaywall, Europe PMC, arXiv, CORE.

One module, not six. Each of these is a single bounded request and a dozen lines of shape
translation; a file per provider would be six docstrings restating the same contract and
one real difference each. What varies is worth reading side by side — which identifier
kinds a provider covers, whether its *silence* means anything, and where it hides the
abstract.

Five of the six are keyless. **CORE is not**, and that difference is load-bearing rather
than incidental: a keyed provider needs a credential-bearing request (a token in a header,
a no-redirect opener, fail-closed startup validation), and "enabled without a key" has to
be fatal at load rather than a tier that silently spends its budget on 401s and reports
them as coverage. D40 added that posture for the paid tiers, and CORE inherits it.

Coverage, and therefore what a denial is worth (`authoritative`):

* **Crossref** is the DOI registry of record. It does not hold every DOI — DataCite and
  mEDRA mint their own — so a denial is contributory, never sufficient on its own.
* **OpenAlex** indexes Crossref, PubMed, DataCite and more, and answers for DOIs and
  PMIDs. Its `best_oa_location` also serves tier 1, which is why it appears in both
  provider lists.
* **Unpaywall** is tier 1 only. It answers "is there a free copy of this DOI" and nothing
  useful about existence, and it *requires* a contact email — the one provider that is
  simply unavailable without one.
* **Europe PMC** covers biomedicine. Authoritative for PMIDs and PMCIDs, and pointedly
  NOT for DOIs: a physics DOI is absent from it as a matter of scope, and reading that
  absence as non-existence would mint a blocking defect out of a coverage boundary.
* **arXiv** is authoritative for arXiv ids and is always open access, so its metadata and
  its open-access answer come from one call.
* **CORE** aggregates open-access repositories. Tier 1 only, and deliberately last in the
  order: the keyless providers answer the same question, and CORE earns its call only for
  what they miss.
"""

from __future__ import annotations

import html
import re
import urllib.parse

from ..fetch import Provider, SourceMetadata
from . import base
from .base import ProviderUnavailable
from .identifiers import Identifier, IdKind


class UnknownProvider(ValueError):
    """A roster named a provider this package does not implement.

    Fatal at startup rather than a silently skipped tier: an operator who typed
    `openalexx` has enabled a tier that will never run, and a retrieval feature that
    quietly does nothing while reporting success is the failure `_build_searcher` already
    refuses to allow.
    """


class ContactEmailRequired(ValueError):
    """This provider cannot run anonymously at all.

    Distinct from `UnknownProvider` because the remedy is different and so is the
    severity: a missing contact email costs one provider and is a warning, whereas an
    unrecognised name is a typo that silently disables a tier and is fatal.
    """


def _with_contact(url: str, param: str, contact_email: str) -> str:
    """Append the polite-pool contact parameter, when one is configured.

    Crossref and OpenAlex route requests carrying a contact address into a separate,
    better-behaved rate-limit pool. Its absence is degraded service, not a broken
    configuration — hence a warning at startup and a plain anonymous request here.
    """
    if not contact_email:
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{urllib.parse.urlencode({param: contact_email})}"


class Crossref:
    """`api.crossref.org/works/{doi}` — existence and citation details for a DOI."""

    name = Provider.CROSSREF
    ENDPOINT = "https://api.crossref.org/works/"

    def __init__(self, *, timeout: float, contact_email: str = "") -> None:
        self._timeout = timeout
        self._contact_email = contact_email

    def supports(self, kind: IdKind) -> bool:
        return kind is IdKind.DOI

    def authoritative(self, kind: IdKind) -> bool:
        return kind is IdKind.DOI

    def metadata(self, ident: Identifier) -> SourceMetadata | None:
        url = _with_contact(
            self.ENDPOINT + urllib.parse.quote(ident.value, safe="/"),
            "mailto",
            self._contact_email,
        )
        payload = base.json_get(url, timeout=self._timeout)
        if payload is None:
            return None
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ProviderUnavailable("crossref response carried no work record")
        return SourceMetadata(
            title=_first(message.get("title")),
            authors=tuple(_crossref_authors(message.get("author"))),
            year=_crossref_year(message.get("issued")),
            venue=_first(message.get("container-title")) or _text(message.get("publisher")),
            doi=_text(message.get("DOI")) or ident.value,
            registry=self.name.value,
            # Crossref abstracts are JATS XML fragments, tags and all.
            abstract=_strip_markup(_text(message.get("abstract"))),
        )


class OpenAlex:
    """`api.openalex.org/works/{doi|pmid}:{id}` — metadata *and* a best open-access
    location, which is why it serves both tiers."""

    name = Provider.OPENALEX
    ENDPOINT = "https://api.openalex.org/works/"
    _PREFIX = {IdKind.DOI: "doi", IdKind.PMID: "pmid"}

    def __init__(self, *, timeout: float, contact_email: str = "") -> None:
        self._timeout = timeout
        self._contact_email = contact_email

    def supports(self, kind: IdKind) -> bool:
        return kind in self._PREFIX

    def authoritative(self, kind: IdKind) -> bool:
        return kind in self._PREFIX

    def metadata(self, ident: Identifier) -> SourceMetadata | None:
        work = self._work(ident)
        if work is None:
            return None
        source = ((work.get("primary_location") or {}).get("source")) or {}
        return SourceMetadata(
            title=_text(work.get("display_name")),
            authors=tuple(
                _text((a or {}).get("author", {}).get("display_name"))
                for a in (work.get("authorships") or [])
                if isinstance(a, dict)
            ),
            year=_int(work.get("publication_year")),
            venue=_text(source.get("display_name")),
            doi=_bare_doi(_text(work.get("doi"))) or (
                ident.value if ident.kind is IdKind.DOI else ""
            ),
            registry=self.name.value,
            abstract=_from_inverted_index(work.get("abstract_inverted_index")),
        )

    def open_access_url(self, ident: Identifier) -> str | None:
        work = self._work(ident)
        if work is None:
            return None
        location = work.get("best_oa_location")
        if not isinstance(location, dict):
            return None
        # The PDF first: a landing page is frequently the same paywalled page the direct
        # fetch already failed on, whereas `pdf_url` is the copy someone actually posted.
        return _http_url(location.get("pdf_url")) or _http_url(location.get("landing_page_url"))

    def _work(self, ident: Identifier) -> dict | None:
        prefix = self._PREFIX[ident.kind]
        url = _with_contact(
            f"{self.ENDPOINT}{prefix}:{urllib.parse.quote(ident.value, safe='/')}",
            "mailto",
            self._contact_email,
        )
        return base.json_get(url, timeout=self._timeout)


class Unpaywall:
    """`api.unpaywall.org/v2/{doi}` — open access only, and only with a contact email.

    Unpaywall answers 422 to a request without `email`, so without one this provider
    cannot run at all. `resolve.providers_for` therefore drops it with a warning rather
    than constructing something that fails on every call.
    """

    name = Provider.UNPAYWALL
    ENDPOINT = "https://api.unpaywall.org/v2/"

    def __init__(self, *, timeout: float, contact_email: str) -> None:
        if not contact_email:
            raise ContactEmailRequired("unpaywall requires a contact email")
        self._timeout = timeout
        self._contact_email = contact_email

    def supports(self, kind: IdKind) -> bool:
        return kind is IdKind.DOI

    def open_access_url(self, ident: Identifier) -> str | None:
        url = _with_contact(
            self.ENDPOINT + urllib.parse.quote(ident.value, safe="/"),
            "email",
            self._contact_email,
        )
        payload = base.json_get(url, timeout=self._timeout)
        if payload is None:
            return None
        location = payload.get("best_oa_location")
        if not isinstance(location, dict):
            return None
        return _http_url(location.get("url_for_pdf")) or _http_url(location.get("url"))


class EuropePmc:
    """The Europe PMC REST search — metadata and open-access full text for the
    biomedical literature."""

    name = Provider.EUROPE_PMC
    ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self, *, timeout: float, contact_email: str = "") -> None:
        self._timeout = timeout

    def supports(self, kind: IdKind) -> bool:
        return kind in (IdKind.PMID, IdKind.PMCID, IdKind.DOI)

    def authoritative(self, kind: IdKind) -> bool:
        # Not DOIs. Europe PMC's scope is biomedical, so its silence about a DOI outside
        # that scope is a coverage boundary and not evidence about the world.
        return kind in (IdKind.PMID, IdKind.PMCID)

    def metadata(self, ident: Identifier) -> SourceMetadata | None:
        record = self._record(ident)
        if record is None:
            return None
        return SourceMetadata(
            title=_text(record.get("title")),
            authors=tuple(a.strip() for a in _text(record.get("authorString")).split(",")),
            year=_int(record.get("pubYear")),
            venue=_text(record.get("journalTitle")),
            doi=_text(record.get("doi")),
            registry=self.name.value,
            abstract=_strip_markup(_text(record.get("abstractText"))),
        )

    def open_access_url(self, ident: Identifier) -> str | None:
        record = self._record(ident)
        if record is None:
            return None
        urls = ((record.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
        free = [
            u
            for u in urls
            if isinstance(u, dict) and _text(u.get("availabilityCode")).upper() in ("OA", "F")
        ]
        # A PDF beats an HTML landing page for the same reason it does at OpenAlex, and
        # `documentStyle` is how Europe PMC says which is which.
        free.sort(key=lambda u: _text(u.get("documentStyle")).lower() != "pdf")
        for entry in free:
            url = _http_url(entry.get("url"))
            if url:
                return url
        return None

    def _record(self, ident: Identifier) -> dict | None:
        query = {
            IdKind.PMID: f'EXT_ID:{ident.value} AND SRC:"MED"',
            IdKind.PMCID: f"PMCID:{ident.value}",
            IdKind.DOI: f'DOI:"{ident.value}"',
        }[ident.kind]
        url = f"{self.ENDPOINT}?" + urllib.parse.urlencode(
            {"query": query, "format": "json", "resultType": "core", "pageSize": 1}
        )
        payload = base.json_get(url, timeout=self._timeout)
        if payload is None:
            return None
        results = ((payload.get("resultList") or {}).get("result")) or []
        if not results:
            # An empty result list is Europe PMC's way of saying "no such record"; the
            # HTTP status is 200 either way.
            return None
        record = results[0]
        return record if isinstance(record, dict) else None


class Arxiv:
    """`export.arxiv.org/api/query` — metadata and the PDF, from one Atom response.

    Parsed with narrow regexes rather than an XML parser, which needs a word because
    `textconv` reaches for `xml.etree` on a .docx and argues there that the classic XXE
    vectors do not apply — stdlib ElementTree resolves no external entities and fetches
    no DTDs, and `http_get`'s byte cap bounds the residual entity-expansion risk here
    exactly as the uncompressed-size guard does there. So security is *not* the reason,
    and claiming it would contradict a position this codebase already took.

    The reason is narrower: five fields out of a machine-generated Atom feed, where the
    only structure that matters is one `<entry>` element. A parser would be more robust
    against a feed that restructured itself, which arXiv's has not in twenty years; it
    would also be the second XML idiom in the package. If this ever grows past a handful
    of fields, ElementTree is the right answer and `textconv` is the precedent.
    """

    name = Provider.ARXIV
    ENDPOINT = "https://export.arxiv.org/api/query"
    #: arXiv is open access by definition, so tier 1's answer is a constant.
    PDF = "https://arxiv.org/pdf/"

    _ENTRY = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
    _TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
    _SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
    _AUTHOR = re.compile(r"<author>\s*<name>(.*?)</name>", re.DOTALL)
    _PUBLISHED = re.compile(r"<published>(\d{4})-")
    _TOTAL = re.compile(r"<opensearch:totalResults[^>]*>(\d+)</opensearch:totalResults>")

    def __init__(self, *, timeout: float, contact_email: str = "") -> None:
        self._timeout = timeout

    def supports(self, kind: IdKind) -> bool:
        return kind is IdKind.ARXIV

    def authoritative(self, kind: IdKind) -> bool:
        return kind is IdKind.ARXIV

    def metadata(self, ident: Identifier) -> SourceMetadata | None:
        entry = self._entry(ident)
        if entry is None:
            return None
        return SourceMetadata(
            title=_strip_markup(_match(self._TITLE, entry)),
            authors=tuple(html.unescape(a).strip() for a in self._AUTHOR.findall(entry)),
            year=_int(_match(self._PUBLISHED, entry)),
            venue="arXiv",
            registry=self.name.value,
            abstract=_strip_markup(_match(self._SUMMARY, entry)),
        )

    def open_access_url(self, ident: Identifier) -> str | None:
        if self._entry(ident) is None:
            return None
        # The PDF, not the `/abs/` page. The abstract page would fetch cleanly as HTML
        # and would then be handed to the critic as the source's *body*, which is a lie:
        # it is an abstract. If PDF reading is off, this fetch fails and the ladder falls
        # back to metadata — honest, and visibly so in the outcome.
        return f"{self.PDF}{urllib.parse.quote(ident.value, safe='/')}"

    def _entry(self, ident: Identifier) -> str | None:
        url = f"{self.ENDPOINT}?" + urllib.parse.urlencode(
            {"id_list": ident.value, "max_results": 1}
        )
        body = base.text_get(url, timeout=self._timeout, accept="application/atom+xml")
        if body is None:
            return None
        total = _match(self._TOTAL, body)
        if total == "0":
            return None
        match = self._ENTRY.search(body)
        if match is None:
            return None
        entry = match.group(1)
        # arXiv answers an unknown-but-well-formed id with a 200 and a single entry
        # titled "Error", so the status line cannot carry this one.
        if _strip_markup(_match(self._TITLE, entry)).lower().startswith("error"):
            return None
        return entry


class ApiKeyRequired(RuntimeError):
    """A provider that cannot run without a credential, configured without one.

    Distinct from `ContactEmailRequired`, which is a courtesy this system can proceed
    without. A keyed provider with no key makes no successful call ever, so constructing
    it would spend the tier's budget on a column of 401s and report them as coverage.
    """


class Core:
    """`api.core.ac.uk/v3/discover` — open-access full text, aggregated (D40).

    The one provider here that needs a credential, which is why it sits with the paid
    tiers rather than beside Unpaywall: not because CORE charges (the tier it is on is
    free), but because "enabled without a key" has to be fatal at load, and that is the
    fail-closed rule the paid tiers already carry.

    Deliberately last in the open-access order. Crossref and OpenAlex answer the same
    question without a credential, and CORE is worth the call only for what they miss.
    """

    name = Provider.CORE
    ENDPOINT = "https://api.core.ac.uk/v3/discover"
    #: Read by `resolve._construct`, which passes a key only to providers that say they
    #: need one. The alternative — probing the constructor signature — cannot distinguish
    #: "takes no key" from "called wrongly".
    NEEDS_API_KEY = True

    def __init__(self, *, timeout: float, api_key: str = "", contact_email: str = "") -> None:
        if not api_key:
            raise ApiKeyRequired("core requires an API key")
        self._timeout = timeout
        self._api_key = api_key

    def supports(self, kind: IdKind) -> bool:
        return kind is IdKind.DOI

    def open_access_url(self, ident: Identifier) -> str | None:
        payload = base.json_post(
            self.ENDPOINT,
            payload={"doi": ident.value},
            api_key=self._api_key,
            timeout=self._timeout,
        )
        if payload is None:
            return None
        return _http_url(payload.get("fullTextLink"))


#: The closed set of provider names a roster may list, and the constructor for each.
#: Keeping this a mapping rather than a chain of `if name ==` is what lets
#: `providers_for` fail closed on an unknown name with the valid set in the message.
PROVIDERS = {
    Provider.CROSSREF.value: Crossref,
    Provider.OPENALEX.value: OpenAlex,
    Provider.UNPAYWALL.value: Unpaywall,
    Provider.EUROPE_PMC.value: EuropePmc,
    Provider.ARXIV.value: Arxiv,
    Provider.CORE.value: Core,
}


# ------------------------------------------------------------------ shape helpers


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first(value: object) -> str:
    if isinstance(value, list) and value:
        return _text(value[0])
    return _text(value)


def _int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _match(pattern: re.Pattern[str], text: str) -> str:
    found = pattern.search(text)
    return found.group(1) if found else ""


_MARKUP = re.compile(r"<[^>]{0,200}>")


def _strip_markup(value: str) -> str:
    """Drop tags and resolve entities.

    Crossref abstracts are JATS XML and Europe PMC's carry occasional inline markup. Left
    in, the tags would read as structure once the abstract is fenced into a prompt — the
    same reason `search._clean` strips Brave's `<strong>` markers. The bounded `{0,200}`
    is so a stray `<` in prose cannot make this scan the whole abstract.
    """
    return " ".join(html.unescape(_MARKUP.sub(" ", value)).split())


def _http_url(value: object) -> str | None:
    url = _text(value)
    return url if url.lower().startswith(("http://", "https://")) else None


def _bare_doi(value: str) -> str:
    """OpenAlex returns a DOI as `https://doi.org/10.…`; the record wants the DOI."""
    lowered = value.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            return value[len(prefix) :]
    return value


def _crossref_authors(value: object) -> list[str]:
    out: list[str] = []
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        name = " ".join(
            part for part in (_text(entry.get("given")), _text(entry.get("family"))) if part
        )
        if name or _text(entry.get("name")):
            out.append(name or _text(entry.get("name")))
    return out


def _crossref_year(issued: object) -> int | None:
    parts = (issued or {}).get("date-parts") if isinstance(issued, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        return _int(parts[0][0])
    return None


def _from_inverted_index(index: object) -> str:
    """Rebuild an OpenAlex abstract from its inverted index.

    OpenAlex ships abstracts as `{word: [positions]}` rather than as prose. Reconstructed
    here because the alternative is showing the critic a word-position map, which is not
    something a reader can judge a citation against.
    """
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, slots in index.items():
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if isinstance(slot, int):
                positions.append((slot, str(word)))
    positions.sort()
    return " ".join(word for _, word in positions)


__all__ = [
    "PROVIDERS",
    "ApiKeyRequired",
    "Arxiv",
    "ContactEmailRequired",
    "Core",
    "Crossref",
    "EuropePmc",
    "OpenAlex",
    "UnknownProvider",
    "Unpaywall",
]
