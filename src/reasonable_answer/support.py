"""The support manifest and its mechanical check (D-writer-source-reads).

A writer that can read its sources can be asked a question it could not answer before:
*where exactly does this claim's support appear?* The manifest is that answer, one entry
per link in the chain — citation id, URL or identifier, locator, verbatim support span,
supported claim — and this module is what decides, without a model, whether the answer
holds up.

Two rules shape everything here.

**Mechanical, or silent.** A verdict is reached by string containment against text this
run actually holds: the report the writer just produced, and the page bodies the same
writer read through `read_source`. Where there is no body there is no verdict — the
entry is recorded `body_not_read` and nothing is inferred from it. An unchecked entry is
not a failed one, and this module never guesses which.

**Audit-side.** Nothing computed here enters a model's context, becomes a `Defect`,
touches `OrchestratorView`, or reaches the controller. The manifest answers "can a human
reading this run trace the claim to the page?", which is a property of the record, not a
term in the stop decision (docs/convergence.md). Wiring it into acceptance would also
hand a writer a lever on its own review, since the writer authors the manifest.

Three verdicts encode distinctions the issue that prompted this work names explicitly
and that must not collapse into "unsupported":

* `different_document` — the body came from an open-access copy, not the cited URL. A
  preprint is not the version of record, so a span found in it does not establish that
  the cited document contains it. `fetch.FetchedSource.body_source_url` is the flag,
  and `dispute.adjudicate_mechanical` refuses the same case for the same reason.
* `body_not_read` — a registry record, an abstract, a paywall, a block. An abstract is a
  summary the authors wrote; absence from it is not absence from the paper, and presence
  in it is not full-text support (D-existence-vs-body).
* `not_retrieved` — the writer named a source it never opened. Common and legitimate:
  a snippet-level citation is still a citation. It is a statement about provenance
  depth, which is what the manifest exists to expose.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .fetch import FetchedSource, SourceOutcome
from .schemas import SupportEntry, SupportManifest
from .triage import _normalize

SupportVerdict = Literal[
    "supported",
    "span_not_found",
    "claim_not_in_report",
    "different_document",
    "body_not_read",
    "not_retrieved",
]

#: The one verdict that says the chain was checked end to end and held.
VERIFIED: SupportVerdict = "supported"

#: Verdicts reached without a body in hand. Counted separately from `span_not_found`
#: everywhere, because "not checkable" and "checked and false" are different facts and
#: an operator reading a tally must not have to guess which one a number is.
UNCHECKED: frozenset[str] = frozenset(
    {"different_document", "body_not_read", "not_retrieved"}
)


@dataclass(frozen=True)
class CheckedEntry:
    """One manifest entry with the verdict the check reached."""

    entry: SupportEntry
    verdict: SupportVerdict

    @property
    def checked(self) -> bool:
        return self.verdict not in UNCHECKED


def check(
    manifest: SupportManifest,
    report_text: str,
    bodies: Mapping[str, FetchedSource],
) -> list[CheckedEntry]:
    """Rule on every entry, in order. Never raises; never drops an entry.

    `bodies` is what one writer call read — `reading.ReadSession.reads`. Keyed by the
    URL the writer asked for, which is the URL it must name in the manifest for the
    chain to be traceable at all.

    Order matters. The report check comes first because an entry whose claim is not in
    the report is untraceable whatever the source says: it points at nothing. Only then
    is the source consulted.
    """
    report = _normalize(report_text or "")
    return [CheckedEntry(entry=e, verdict=_verdict(e, report, bodies)) for e in manifest.entries]


def _verdict(
    entry: SupportEntry, normalized_report: str, bodies: Mapping[str, FetchedSource]
) -> SupportVerdict:
    if _normalize(entry.claim) not in normalized_report:
        return "claim_not_in_report"

    source = bodies.get(entry.url.strip())
    if source is None:
        return "not_retrieved"
    if source.outcome is not SourceOutcome.FULL_TEXT:
        return "body_not_read"
    if source.body_source_url is not None:
        return "different_document"
    if _normalize(entry.support_span) not in _normalize(source.text):
        return "span_not_found"
    return VERIFIED


def tally(checked: list[CheckedEntry]) -> dict[str, int]:
    """Counts per verdict, for `events.jsonl`.

    Counts only: the verdicts are a closed vocabulary and the numbers are numbers, so
    this is safe where the spans, claims and URLs it summarizes are not (RA-016). The
    full manifest goes to a purgeable content directory instead.
    """
    counts: dict[str, int] = {}
    for item in checked:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    return counts


def record(checked: list[CheckedEntry]) -> list[dict]:
    """The manifest as it is written to `support/`: every entry, with its verdict.

    Content-bearing — spans quoted from the report and from third-party pages — so this
    belongs in a `CONTENT_DIRS` directory and never in the event log.
    """
    return [
        {**item.entry.model_dump(mode="json"), "verdict": item.verdict} for item in checked
    ]


def locator_coverage(checked: list[CheckedEntry]) -> int:
    """How many entries named a page/chapter/section/table.

    The gap that prompted this work was bibliography-level provenance — a whole book
    attached to a narrow claim — and that is invisible in a support tally, because such
    an entry can be perfectly `supported` on a span from page one. Counting locators is
    what makes it visible.
    """
    return sum(1 for item in checked if (item.entry.locator or "").strip())
