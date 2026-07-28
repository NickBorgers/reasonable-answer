"""Pull a bibliographic identifier out of a cited URL. Pure regex, no network, no I/O.

Separate from the providers because this is the only part of the ladder that can be
wrong in the dangerous direction. A *mangled* identifier is not a harmless miss: it is
an identifier no registry has heard of, and "no registry has heard of it" is the input
to D38's mechanical `fabricated_citation`, which floors at blocking. So the rules here
are deliberately narrow — a pattern that is not confidently an identifier yields None,
and the ladder then leaves the direct fetch's verdict exactly as it found it.

`extract` returns at most one identifier, by precedence, because one is all the ladder
needs: every provider keys off a single record, and a URL that carries both a DOI and an
arXiv id names one document either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class IdKind(str, Enum):
    DOI = "doi"
    ARXIV = "arxiv"
    PMID = "pmid"
    PMCID = "pmcid"


@dataclass(frozen=True)
class Identifier:
    """A normalised identifier. `key` is what the resolver's caches are keyed on, so
    two URLs naming one DOI share a single Crossref call."""

    kind: IdKind
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value}"


#: A DOI is `10.<registrant>/<suffix>`. The suffix may contain almost anything, so the
#: class is defined by what ends it inside a URL rather than by what it admits: a query
#: separator, a fragment, whitespace, or a quote. Trailing punctuation is trimmed
#: afterwards — `extract_source_urls` already strips a sentence-final period from the
#: URL, but a DOI inside a parenthesis or an `&`-joined querystring reaches here intact.
_DOI = re.compile(r"(10\.\d{4,9}/[^\s\"'<>&?#]+)", re.IGNORECASE)

#: Both arXiv id schemes: post-2007 `2401.12345` and the pre-2007 archive form
#: `math.GT/0309136`. The version suffix (`v3`) is matched so it can be *dropped* — the
#: registry answers for the paper, not for a revision of it, and the difference between
#: revisions is precisely why an arXiv body is treated as a mirror rather than as the
#: version of record.
_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})"
_ARXIV_URL = re.compile(
    rf"arxiv\.org/(?:abs|pdf|html|ftp/(?:arxiv/)?papers/\d+)/({_ARXIV_ID})(?:v\d+)?",
    re.IGNORECASE,
)
_ARXIV_PREFIXED = re.compile(rf"arxiv[:/]\s*({_ARXIV_ID})(?:v\d+)?", re.IGNORECASE)

_PMCID = re.compile(r"\bPMC(\d{5,9})\b", re.IGNORECASE)
_PMID = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{4,9})", re.IGNORECASE)

#: Characters that end a citation rather than belong to it. Trimmed from the right of a
#: DOI only: over-trimming a real suffix would fabricate a miss, so this is limited to
#: the marks that cannot legally end a DOI in running text.
_TRAILING = ".,;:)]}>\"'"


def extract(url: str) -> Identifier | None:
    """The first identifier the URL confidently carries, or None.

    Precedence is DOI, arXiv, PMCID, PMID — most authoritative first. A DOI wins over an
    arXiv id in the same URL because arXiv now mints DOIs (`10.48550/arXiv.…`) that the
    same registries answer for, so the DOI path reaches strictly more coverage.
    """
    text = url or ""

    match = _DOI.search(text)
    if match:
        doi = match.group(1).rstrip(_TRAILING)
        # A DOI with nothing after the slash is a prefix, not a record.
        if "/" in doi and doi.split("/", 1)[1]:
            return Identifier(IdKind.DOI, doi.lower())

    match = _ARXIV_URL.search(text) or _ARXIV_PREFIXED.search(text)
    if match:
        return Identifier(IdKind.ARXIV, match.group(1).lower())

    match = _PMCID.search(text)
    if match:
        return Identifier(IdKind.PMCID, f"PMC{match.group(1)}")

    match = _PMID.search(text)
    if match:
        return Identifier(IdKind.PMID, match.group(1))

    return None


__all__ = ["IdKind", "Identifier", "extract"]
