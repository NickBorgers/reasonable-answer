"""Claim-anchored excerpts of a fetched page (D-claim-anchored-excerpts).

The evidence critic is shown a bounded amount of each cited page. Until this module
existed that bound was the *first* `fetch_max_chars` characters — often a page's
navigation, summary and opening paragraphs — so claim-relevant text later in the page
could be absent even though retrieval succeeded.

This module chooses *which* characters to show instead of *how many*. For each source,
the sentences of the report that cite it are the anchors; the page is scanned in
overlapping windows; each window is scored by how many of an anchor's distinctive tokens
(numbers first, then content words) it contains; and the best windows are shown, with
the opening of the page, inside the same character budget as before. Everything here is
deterministic string work — no model, no network — so it is testable offline and adds
nothing a critic could be prompted through.

What the critic sees is still a *subset* of the page, so the prompt keeps saying so
(`prompts.fetched_sources_block`); what changes is that the subset is the part the
claim would be in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import fetch

#: A citation marker as writers actually produce it: `[3]`, `[1, 3]`, `[2-4]`, `[1;3]`.
_MARKER = re.compile(r"\[(\d+(?:\s*[,;–—-]\s*\d+)*)\]")
_RANGE = re.compile(r"(\d+)\s*[–—-]\s*(\d+)")
#: Widest range a marker may expand to. A `[1-400]` is a typo, not four hundred citations.
_MAX_RANGE = 50

#: An explicit number at the head of a bibliography entry: `[3] …`, `3. …`, `3) …`.
_ENTRY_NUMBER = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s*")

#: Sentence boundaries, loosely: end punctuation followed by whitespace, or a line break.
#: A citing sentence only has to *contain* its marker for this to work, so the split
#: need not be linguistically careful.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")

#: Distinctive tokens. Numbers carry the most weight because they are what a report
#: attributes to a source and what a critic checks; they are also rare enough in prose
#: that a window holding the same number is very likely the passage in question.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\s?%?")
_WORD = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")
_NUMBER_WEIGHT = 4
_WORD_WEIGHT = 1
#: Years and two-digit numbers are too common to anchor on by themselves; they still
#: count, but at a word's weight.
_WEAK_NUMBER = re.compile(r"^(?:19|20)\d{2}$|^\d{1,2}$")

_STOPWORDS = frozenset(
    re.findall(
        r"\S+",
        "about above after again against also among around because been before being "
        "below between both cannot could does doing down during each either every from "
        "further have having here herself himself into itself more most much must "
        "neither never once only other ought over same shall should since some such than "
        "that their theirs them themselves then there these they this those through under "
        "until very were what when where which while whom whose will with within without "
        "would your yours yourself according although another however therefore whereas "
        "whether report reports source sources study studies data claim claims found finds",
    )
)

#: Default excerpting geometry, in characters. A window is about a paragraph; the head
#: is enough of a page's opening to carry its title, date and scope.
DEFAULT_WINDOW = 600
DEFAULT_HEAD = 800


@dataclass(frozen=True)
class Excerpt:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Excerpted:
    """What of a page is shown, and how much of it there was."""

    total_chars: int
    excerpts: tuple[Excerpt, ...]
    #: The body itself was cut before the page's end (`FetchedSource.truncated`), so
    #: `total_chars` is the length of a prefix of the page, not of the page.
    truncated: bool = False

    @property
    def shown_chars(self) -> int:
        return sum(e.end - e.start for e in self.excerpts)

    @property
    def complete(self) -> bool:
        """The reader is looking at the whole page. False for a cut body even when every
        retained character is shown: absence from a prefix is not absence from the page
        (D-claim-check-inconclusive-verdicts)."""
        return (
            not self.truncated
            and len(self.excerpts) == 1
            and self.excerpts[0].start == 0
            and self.excerpts[0].end == self.total_chars
        )

    @property
    def all_retained_shown(self) -> bool:
        return (
            len(self.excerpts) == 1
            and self.excerpts[0].start == 0
            and self.excerpts[0].end == self.total_chars
        )


# ------------------------------------------------------------------ anchors


def citing_sentences(report: str, number: int) -> list[str]:
    """The sentences of the report's body that cite bibliography entry `number`.

    Body means everything before the `## Sources` heading — a bibliography entry's
    own text never cites itself. Markers are expanded (`[2-4]` cites 2, 3 and 4), so a
    sentence citing a range anchors every source in it.
    """
    body = _body(report)
    out: list[str] = []
    for sentence in _SENTENCE_END.split(body):
        sentence = sentence.strip()
        if not sentence:
            continue
        if any(number in _cited(marker) for marker in _MARKER.findall(sentence)):
            out.append(sentence)
    return out


def entry_numbers(report: str) -> dict[str, list[int]]:
    """Cited URL -> the bibliography numbers it is listed under.

    A number is what the entry says it is when it says (`[3]`, `3.`), and its position
    otherwise (`- …`). One URL may sit under several numbers when a writer lists it
    twice; an entry with no URL takes a number and maps to nothing.
    """
    numbers: dict[str, list[int]] = {}
    for position, entry in enumerate(fetch.source_entries(report), 1):
        match = _ENTRY_NUMBER.match(entry)
        explicit = (match.group(1) or match.group(2)) if match else None
        number = int(explicit) if explicit else position
        url = fetch.entry_url(entry)
        if url is not None:
            numbers.setdefault(url, []).append(number)
    return numbers


def anchors_for(report: str, url: str, numbers: dict[str, list[int]] | None = None) -> list[str]:
    """Every sentence in the report that cites `url`, by way of its bibliography numbers."""
    numbers = entry_numbers(report) if numbers is None else numbers
    anchors: list[str] = []
    for number in numbers.get(url, []):
        for sentence in citing_sentences(report, number):
            if sentence not in anchors:
                anchors.append(sentence)
    return anchors


# ------------------------------------------------------------------ citation census


def _numbered_entries(report: str) -> list[tuple[int, str]]:
    """Each bibliography entry with its number, by the rule `entry_numbers` applies."""
    out: list[tuple[int, str]] = []
    for position, entry in enumerate(fetch.source_entries(report), 1):
        match = _ENTRY_NUMBER.match(entry)
        explicit = (match.group(1) or match.group(2)) if match else None
        out.append((int(explicit) if explicit else position, entry))
    return out


def _source_key(entry: str) -> str:
    """What an entry is *about*, independent of the number it sits under.

    Its cleaned URL when it has one, so renumbering a bibliography is never read as
    removing a source; otherwise its whitespace-normalized text with the list marker or
    number taken off the front.
    """
    url = fetch.entry_url(entry)
    if url is not None:
        return url
    text = re.sub(r"^\s*[-*+]\s+", "", entry)
    text = _ENTRY_NUMBER.sub("", text, count=1)
    return " ".join(text.split()).casefold()


def _body_citations(report: str) -> tuple[int, set[int]]:
    """How many markers the body carries, and every entry number they cite."""
    markers = _MARKER.findall(_body(report))
    cited: set[int] = set()
    for marker in markers:
        cited |= _cited(marker)
    return len(markers), cited


def _cited_keys(report: str) -> tuple[set[str], set[str]]:
    """(every entry's source key, the keys of the entries the body cites)."""
    _, cited = _body_citations(report)
    entries = _numbered_entries(report)
    return (
        {_source_key(entry) for _, entry in entries},
        {_source_key(entry) for number, entry in entries if number in cited},
    )


def dangling_numbers(report: str) -> set[int]:
    """Every entry number the body cites that no `## Sources` entry carries — the
    *identities* behind `citation_census`'s `dangling_markers` count. The ops splice
    compares these sets, not the counts: a Sources edit that orphans one entry while
    curing another leaves the count unchanged and is still a broken citation
    (D-ops-revision)."""
    _, cited = _body_citations(report)
    return cited - {number for number, _ in _numbered_entries(report)}


def citation_census(report: str) -> dict[str, int]:
    """How the draft's body markers and its `## Sources` entries agree
    (D-writer-citation-continuity). Counts only — never a URL or a character of text — because these land in
    `events.jsonl`, which outlives a content purge (RA-016).

    * `source_entries` — entries in `## Sources`;
    * `body_markers` — marker occurrences before the Sources heading (`[1, 3]` is one);
    * `cited_entries` — entries whose number at least one body marker cites;
    * `dangling_markers` — distinct numbers the body cites that no entry carries.
    """
    markers, cited = _body_citations(report)
    entries = _numbered_entries(report)
    numbers = {number for number, _ in entries}
    return {
        "source_entries": len(entries),
        "body_markers": markers,
        "cited_entries": sum(1 for number, _ in entries if number in cited),
        "dangling_markers": len(cited - numbers),
    }


def citation_changes(previous: str, report: str) -> dict[str, int]:
    """What a revision did to the draft's citations, by source rather than by number.

    A source is identified by its entry URL (`_source_key`), so a bibliography renumbered
    from 1..n to some other order counts as neither a drop nor an addition.

    * `cited_sources_dropped` — sources the previous body cited and this body does not;
    * `cited_sources_added` — sources this body cites and the previous body did not;
    * `entries_removed` — sources listed before and no longer listed at all.
    """
    before_listed, before_cited = _cited_keys(previous)
    after_listed, after_cited = _cited_keys(report)
    return {
        "cited_sources_dropped": len(before_cited - after_cited),
        "cited_sources_added": len(after_cited - before_cited),
        "entries_removed": len(before_listed - after_listed),
    }


def _body(report: str) -> str:
    match = fetch._SOURCES_HEADING.search(report or "")
    return report[: match.start()] if match else (report or "")


def _cited(marker: str) -> set[int]:
    cited: set[int] = set()
    for part in re.split(r"[,;]", marker):
        span = _RANGE.search(part)
        if span:
            lo, hi = int(span.group(1)), int(span.group(2))
            if lo <= hi <= lo + _MAX_RANGE:
                cited.update(range(lo, hi + 1))
            else:
                cited.add(lo)
            continue
        digits = re.search(r"\d+", part)
        if digits:
            cited.add(int(digits.group()))
    return cited


# ------------------------------------------------------------------ selection


def select(
    body: str,
    anchors: Sequence[str],
    *,
    budget: int,
    window: int = DEFAULT_WINDOW,
    head: int = DEFAULT_HEAD,
    truncated: bool = False,
) -> Excerpted:
    """Choose up to `budget` characters of `body` to show for `anchors`.

    A body that fits is shown whole. Otherwise the opening `head` characters are
    always shown — the page's title, date and scope live there — and the remaining
    budget goes to the windows that best match the anchors, in document order, merged
    where they touch. With no anchors, or none that match anything, the fallback is the
    opening `budget` characters: exactly what was shown before this module existed.

    `truncated` says `body` is a prefix of the page (the fetch hit its byte cap, a PDF
    its page cap); it changes nothing about which characters are chosen and everything
    about what the result claims — such a body is never `complete`.
    """
    total = len(body)
    if total <= budget:
        return Excerpted(total, (Excerpt(0, total, body),), truncated)

    _, head_end = _snap(body, 0, min(head, budget))
    head_end = min(head_end, budget)
    taken: list[tuple[int, int]] = [(0, head_end)]
    spent = head_end

    scored = _score_windows(body, anchors, window=window, start_at=head_end)
    for score, start, end in scored:
        if score <= 0:
            break
        # A window that scores is shown as whole sentences: the figure a claim cites
        # is worth nothing if the excerpt ends three words before it.
        start, end = _to_sentence_edges(body, start, end, slack=window // 2)
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            continue
        if spent + (end - start) > budget:
            continue
        taken.append((start, end))
        spent += end - start

    if len(taken) == 1:
        # Nothing matched: show the opening, as before, rather than the head alone.
        end = min(budget, total)
        return Excerpted(total, (Excerpt(0, end, body[:end]),), truncated)

    return Excerpted(total, tuple(_merged(body, taken)), truncated)


def _score_windows(
    body: str, anchors: Sequence[str], *, window: int, start_at: int
) -> list[tuple[int, int, int]]:
    """Every window of `body` from `start_at` on, scored against the best-matching
    anchor. Sorted best first, earlier first on ties, so selection is deterministic."""
    token_sets = [_tokens(a) for a in anchors]
    token_sets = [t for t in token_sets if t]
    if not token_sets:
        return []
    lowered = body.lower()
    step = max(1, window // 2)
    out: list[tuple[int, int, int]] = []
    start = start_at
    while start < len(body):
        end = min(len(body), start + window)
        start_snapped, end_snapped = _snap(body, start, end)
        chunk = lowered[start_snapped:end_snapped]
        best = 0
        for tokens in token_sets:
            score = 0
            for token, weight in tokens.items():
                if token in chunk:
                    score += weight
            best = max(best, score)
        out.append((best, start_snapped, end_snapped))
        start += step
    out.sort(key=lambda t: (-t[0], t[1]))
    return out


def _tokens(anchor: str) -> dict[str, int]:
    """Distinctive tokens of one anchor sentence, lower-cased, with weights.

    Citation markers are stripped first: `[3]` is not a figure the page would state.
    """
    text = _MARKER.sub(" ", anchor)
    tokens: dict[str, int] = {}
    for raw in _NUMBER.findall(text):
        number = raw.strip().replace(" ", "")
        percent = number.endswith("%")
        bare = number.rstrip("%").rstrip(",.")
        if not bare:
            continue
        number = bare + ("%" if percent else "")
        # A percentage is a specific figure however small; a bare year or a one- or
        # two-digit count is not distinctive enough to carry a window on its own.
        weak = not percent and _WEAK_NUMBER.match(bare) is not None
        weight = _WORD_WEIGHT if weak else _NUMBER_WEIGHT
        tokens[number] = max(tokens.get(number, 0), weight)
    for word in _WORD.findall(text):
        lowered = word.lower()
        if lowered in _STOPWORDS:
            continue
        tokens.setdefault(lowered, _WORD_WEIGHT)
    return tokens


def _snap(body: str, start: int, end: int, slack: int = 40) -> tuple[int, int]:
    """Move a window's edges to the nearest whitespace within `slack`, so an excerpt
    does not open or close mid-word. Never widens past the body."""
    if start > 0:
        for i in range(start, max(0, start - slack), -1):
            if body[i - 1].isspace():
                start = i
                break
    if end < len(body):
        for i in range(end, min(len(body), end + slack)):
            if body[i].isspace():
                end = i
                break
    return start, end


_SENTENCE_EDGE = re.compile(r"[.!?]\s")


def _to_sentence_edges(body: str, start: int, end: int, slack: int) -> tuple[int, int]:
    """Widen a window to the sentence boundaries nearest its edges, within `slack`.

    The start moves back to just after the previous sentence terminator; the end moves
    forward to just after the next one. Where none is within reach the snapped edge
    stands, so a page with no punctuation is still excerpted."""
    before = body[max(0, start - slack) : start]
    edges = list(_SENTENCE_EDGE.finditer(before))
    if edges:
        start = max(0, start - slack) + edges[-1].end()
    elif start - slack <= 0:
        start = 0
    after = _SENTENCE_EDGE.search(body, end, min(len(body), end + slack))
    if after:
        end = after.end() - 1
    elif end + slack >= len(body):
        end = len(body)
    return start, end


def _merged(body: str, taken: list[tuple[int, int]]) -> list[Excerpt]:
    ordered = sorted(taken)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [Excerpt(s, e, body[s:e]) for s, e in merged]


# ------------------------------------------------------------------ rendering


def render(excerpted: Excerpted) -> str:
    """The page text as the critic sees it: a header stating how much of the page this
    is, then each excerpt under its character range, with `[…]` marking the gaps."""
    if excerpted.complete:
        return f"Page text (complete, {excerpted.total_chars:,} characters):\n{excerpted.excerpts[0].text}"
    cut = (
        " The fetch stopped at its cap before the end of the page, so the page continues "
        "past the last character retained and nothing here says what that remainder contains."
        if excerpted.truncated
        else ""
    )
    if excerpted.all_retained_shown:
        # A cut body that fits the budget: every retained character is here, and the
        # page is still not whole. Say the second thing, or "all shown" reads as whole.
        parts = [
            f"Page text: the opening {excerpted.total_chars:,} characters of the page, shown in "
            f"full.{cut} “[…]” marks text not shown."
        ]
    else:
        parts = [
            f"Page text: {excerpted.shown_chars:,} of {excerpted.total_chars:,} characters shown — "
            "the opening of the page, then the passages that best match the sentences of the "
            f"report that cite this source.{cut} “[…]” marks text not shown."
        ]
    previous_end = 0
    for excerpt in excerpted.excerpts:
        if excerpt.start > previous_end:
            parts.append("[…]")
        parts.append(f"(characters {excerpt.start:,}–{excerpt.end:,})\n{excerpt.text}")
        previous_end = excerpt.end
    if previous_end < excerpted.total_chars or excerpted.truncated:
        parts.append("[…]")
    return "\n".join(parts)
