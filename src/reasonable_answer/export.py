"""Handing a finished run to someone who was not there.

The deployment posture is tailnet-only, and callers are authenticated only against a
trusted header (D-identity-header), so sharing a result with someone outside that means handing over a
*file*, not a link. That constrains what an export has to
carry: the recipient has no run page to check, no event log to read, and no way to ask
what the badge said. `final.md` alone does not survive that trip — an `accepted` report
and a `needs_human_review` report with three blocking defects are byte-indistinguishable
as prose, and the whole point of the pipeline is the difference between them.

So every export is *report + review record*, mechanically derived from `final.json`
(D-verdict-attached). Nothing here is model-authored except the report body and the defect prose that
was already shown on the run page; the status, label, reviewer list and hashes are the
pipeline's own record.

Layering: nothing here imports `web/` at module scope, and `web/render.py` imports the
status vocabulary *from* here. That is not tidiness — `web` is an optional extra, and
`ra export --format md` has to work on a core install. The one function that needs the
web layer (`export_html`, for the renderer and the stylesheet) imports it at call time
and is the only part that requires the extra.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .build import describe_build

#: Not a terminal status — no controller rule produces it. It is what an export says
#: when `final.json` will not parse: the verdict is *unknown*, which is a different
#: claim from `aborted` and the only one the evidence supports.
UNREADABLE_RECORD = "unreadable_record"

#: What each status *is*. Lives here rather than in the renderer because the CLI needs
#: it without the web extra installed, and because these words end up in a file someone
#: keeps — they are part of the result, not part of the page.
STATUS_MEANING = {
    "accepted": "every lens cleared by two cross-family non-author models on the final artifact",
    "converged_unconfirmed": "every lens cleared, but a lens had only one eligible model family",
    "exhausted_unresolved": "reached the cap or stagnated with only non-blocking issues left",
    "needs_human_review": "reached the cap, stagnated or cycled with blocking issues present",
    "aborted": "fatal: a model was unavailable or a review could not be completed",
    "queued": "waiting for a worker",
    "running": "in progress",
    "interrupted": "the process stopped before finishing; this run can be resumed",
    UNREADABLE_RECORD: "the stored record for this run could not be read",
}

#: Long-form caveat per terminal status, aimed at a reader holding only this file.
#: `STATUS_MEANING` says what the status *is*; this says what to do about it.
STATUS_ADVICE = {
    "accepted": "No eligible reviewer could find a material defect in this artifact.",
    "converged_unconfirmed": (
        "Every dimension was cleared, but at least one had only a single eligible "
        "reviewer — so that dimension rests on one opinion, not a consensus."
    ),
    "exhausted_unresolved": (
        "The loop stopped before every issue was resolved. The remaining issues are "
        "non-blocking, but they are listed below and were not fixed."
    ),
    "needs_human_review": (
        "The loop stopped with blocking issues outstanding. This report is NOT "
        "consensus-clean: read the outstanding defects below before relying on it."
    ),
    "aborted": (
        "The run failed before it could finish reviewing. Treat this text as a draft "
        "that was never fully critiqued."
    ),
    UNREADABLE_RECORD: (
        "This run's stored record could not be read, so the verdict is unknown — not "
        "absent, unknown. Nothing here establishes whether the report was accepted or "
        "shipped with defects outstanding; check the run directory before relying on it."
    ),
}


#: Shown under the defect heading when defects were recorded but keyed to a draft other
#: than the shipped one (issue #93). An empty section would read as a clean result — the
#: opposite of what a non-accepted terminal means — so the absence is stated, not left
#: to be inferred.
_DEFECTS_NOT_KEYED = (
    "The defects on record were raised against a different draft than the one shipped "
    "here, so they are not listed and the shipped artifact's own defect set was not "
    "recorded. This report was not cleared — do not read the absence of a list as clean."
)


#: An exported file is opened from a filesystem, from a mail attachment, from wherever
#: the recipient put it. It has no origin worth talking to, so nothing but its own
#: inline stylesheet is permitted — no script at all, unlike the served pages.
_EXPORT_CSP = (
    "default-src 'none'; img-src 'none'; style-src 'unsafe-inline'; "
    "form-action 'none'; base-uri 'none'"
)


def _esc(value: Any) -> str:
    """Same contract as `web.render.esc`, duplicated rather than imported so this
    module stays importable without the web extra."""
    return html.escape(str(value if value is not None else ""))


def _oneline(value: Any) -> str:
    """Critic instructions are prose the model wrote. A newline inside one would end
    the markdown list item it is being emitted into, so they are flattened."""
    return " ".join(str(value if value is not None else "").split())


@dataclass(frozen=True)
class Provenance:
    """Everything an export says about a report that is not the report."""

    run_id: str
    question: str
    status: str
    label: str
    rounds: int
    chosen_round: int | None
    artifact_hash: str | None
    exported_on: str
    outstanding: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reviewers: list[tuple[str, str]] = field(default_factory=list)
    #: True when defects were recorded but none are keyed to the shipped artifact — an
    #: older draft's list, which must be withheld rather than shown against this text
    #: (issue #93). Distinct from an empty `outstanding`, which means genuinely none.
    outstanding_withheld: bool = False
    #: The stored build record, or None for a run that predates stamping
    #: (D-run-build-stamp). Rendered through `build_line`, which is "" when there is
    #: nothing to say, so an older run's record renders exactly as it always did.
    build: dict[str, Any] | None = None

    @property
    def meaning(self) -> str:
        return STATUS_MEANING.get(self.status, "")

    @property
    def advice(self) -> str:
        return STATUS_ADVICE.get(self.status, "")

    @property
    def clean(self) -> bool:
        return self.status in ("accepted", "converged_unconfirmed")

    @property
    def short_hash(self) -> str:
        return (self.artifact_hash or "")[:12]

    @property
    def build_line(self) -> str:
        """The commit this run ran on, or "" if the record does not name one."""
        return describe_build(self.build)


def provenance(
    question: str,
    final: dict[str, Any] | None,
    run_id: str,
    *,
    exported_on: str | None = None,
    unreadable: bool = False,
) -> Provenance:
    """`unreadable=True` is for a run whose `final.json` exists but will not parse.

    It is not the same as passing `None`. `None` means no verdict was ever reached and
    honestly renders as `aborted`; `unreadable` means one may exist and cannot be read,
    and says so instead of picking a status the record does not support.
    """
    # Defence in depth for the same failure arriving by another route: a summary that
    # decoded to something other than an object cannot be read as one.
    if not isinstance(final, dict):
        unreadable = unreadable or final is not None
        final = {}
    if unreadable:
        return Provenance(
            run_id=run_id,
            question=question,
            status=UNREADABLE_RECORD,
            label="",
            rounds=0,
            chosen_round=None,
            artifact_hash=None,
            exported_on=exported_on or time.strftime("%Y-%m-%d", time.gmtime()),
        )
    artifact_hash = final.get("artifact_hash")
    raw_defects = list(final.get("outstanding_defects") or [])
    outstanding = _for_artifact(raw_defects, artifact_hash)
    return Provenance(
        run_id=run_id,
        question=question,
        status=final.get("terminal_status") or "aborted",
        label=final.get("label") or "",
        rounds=final.get("rounds") or 0,
        chosen_round=final.get("chosen_round"),
        artifact_hash=artifact_hash,
        exported_on=exported_on or time.strftime("%Y-%m-%d", time.gmtime()),
        outstanding=outstanding,
        # Defects existed but none belong to the shipped draft: say so, never imply
        # the empty list means the report came back clean.
        outstanding_withheld=bool(raw_defects) and not outstanding,
        warnings=list(final.get("warnings") or []),
        reviewers=_reviewers(final.get("clean_records") or [], artifact_hash),
        build=final.get("build") if isinstance(final.get("build"), dict) else None,
    )


def _for_artifact(
    records: list[dict[str, Any]], artifact_hash: str | None
) -> list[dict[str, Any]]:
    """The records keyed to *this* artifact's hash (RC-001) — clean records or defects.

    Both of an export's annotation surfaces are per-artifact claims: who cleared the
    report, and what is still wrong with it. Each earlier draft collects its own, and
    the shipped draft on a non-accepted terminal need not be the last one written.
    Listing another draft's would credit a reviewer with clearing text it never saw, or
    charge this report with a defect it never contained — the same error in opposite
    directions. One filter serves both, so a third surface inherits the discipline.

    With no hash to key against there is no way to tell which draft a record belongs to,
    so nothing is attributed. Crediting or charging everything is the failure this guard
    exists to prevent, and it would happen precisely when the record is least reliable.
    """
    if not artifact_hash:
        return []
    return [r for r in records if r.get("artifact_hash") == artifact_hash]


def _reviewers(records: list[dict[str, Any]], artifact_hash: str | None) -> list[tuple[str, str]]:
    """(lens, critic) for the clean records that attest to *this* artifact."""
    seen: list[tuple[str, str]] = []
    for record in _for_artifact(records, artifact_hash):
        pair = (str(record.get("lens", "")), str(record.get("critic_identity", "")))
        if pair not in seen:
            seen.append(pair)
    return sorted(seen)


# --------------------------------------------------------------------- markdown


def export_markdown(
    question: str,
    report: str,
    final: dict[str, Any] | None,
    run_id: str,
    *,
    exported_on: str | None = None,
    unreadable: bool = False,
) -> str:
    """`final.md` with its review record appended, as one pasteable document.

    Deliberately not the same bytes as `report.md`: that route stays the raw shipped
    artifact, for anything that hashes or diffs it.
    """
    prov = provenance(question, final, run_id, exported_on=exported_on, unreadable=unreadable)
    # The title is flattened: a question containing a newline would otherwise end the
    # `#` heading and let the rest of it start blocks of its own.
    lines = [f"# {_oneline(prov.question)}", "", report.strip(), "", "---", "", "## Review record", ""]

    # Empty bullets are dropped rather than emitted blank: a blank line inside a
    # markdown list splits it into two lists wherever it lands.
    bullets = [
        f"- Review label: {prov.label}" if prov.label else "",
        f"- Rounds: {prov.rounds}"
        + (f" (shipped the draft from round {prov.chosen_round})" if prov.chosen_round else ""),
        f"- Run: `{prov.run_id}`" + (f" · artifact `{prov.short_hash}`" if prov.short_hash else ""),
        f"- Built from: `{prov.build_line}`" if prov.build_line else "",
        f"- Exported: {prov.exported_on}",
    ]
    lines += [
        f"**Status: {prov.status.replace('_', ' ')}** — {prov.meaning}.",
        "",
        prov.advice,
        "",
        *[b for b in bullets if b],
        "",
    ]

    if prov.reviewers:
        lines += ["### Reviewed clean by", ""]
        lines += [f"- {lens}: `{critic}`" for lens, critic in prov.reviewers]
        lines.append("")

    if prov.outstanding:
        lines += ["### Outstanding defects in this report", ""]
        lines += [
            f"- **{d.get('severity')}** · {d.get('category')} — {_oneline(d.get('instruction'))}"
            for d in prov.outstanding
        ]
        lines.append("")
    elif prov.outstanding_withheld:
        lines += ["### Outstanding defects in this report", "", _DEFECTS_NOT_KEYED, ""]

    if prov.warnings:
        lines += ["### Warnings", ""]
        lines += [f"- {_oneline(w)}" for w in prov.warnings]
        lines.append("")

    lines += [
        "",
        "*Produced by reasonable-answer: models take turns writing and critiquing, and no "
        "model reviews a report it wrote. This is not a fact-check.*",
    ]
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


# ------------------------------------------------------------------------- html


def provenance_html(prov: Provenance) -> str:
    """The review record as a page section — used by the export *and* by the run page,
    so what a reader sees on screen is what a recipient gets in the file."""
    rows = "".join(
        f"<div class='rec-row'><dt>{_esc(k)}</dt><dd>{v}</dd></div>"
        for k, v in _record_rows(prov)
    )

    reviewers = ""
    if prov.reviewers:
        items = "".join(
            f"<li><span class='lens-name'>{_esc(lens)}</span> "
            f"<span class='mono'>{_esc(critic)}</span></li>"
            for lens, critic in prov.reviewers
        )
        reviewers = f"<h3>Reviewed clean by</h3><ul class='reviewers'>{items}</ul>"

    outstanding = ""
    if prov.outstanding:
        items = "".join(
            f'<li><span class="chip {_esc(d.get("severity"))}">{_esc(d.get("severity"))}</span>'
            f'<span class="chip">{_esc(d.get("category"))}</span> '
            f"{_esc(d.get('instruction'))}</li>"
            for d in prov.outstanding
        )
        outstanding = (
            "<h3>Outstanding defects in this report</h3>"
            f'<ul class="defects">{items}</ul>'
        )
    elif prov.outstanding_withheld:
        outstanding = (
            "<h3>Outstanding defects in this report</h3>"
            f'<p class="advice">{_esc(_DEFECTS_NOT_KEYED)}</p>'
        )

    warnings = ""
    if prov.warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in prov.warnings)
        warnings = f"<h3>Warnings</h3><ul class='defects'>{items}</ul>"

    return f"""
<section class="panel record">
  <h2>Review record</h2>
  <p class="verdict {'ok' if prov.clean else 'caveat'}">
    <strong>{_esc(prov.status.replace("_", " "))}</strong> — {_esc(prov.meaning)}.</p>
  <p class="advice">{_esc(prov.advice)}</p>
  <dl class="record-grid">{rows}</dl>
  {reviewers}
  {outstanding}
  {warnings}
  <p class="colophon">Produced by reasonable-answer: models take turns writing and
  critiquing, and no model reviews a report it wrote. This is not a fact-check.</p>
</section>"""


def _record_rows(prov: Provenance) -> list[tuple[str, str]]:
    rows = []
    if prov.label:
        rows.append(("Review label", _esc(prov.label)))
    rounds = str(prov.rounds)
    if prov.chosen_round:
        rounds += f" (shipped the draft from round {_esc(prov.chosen_round)})"
    rows.append(("Rounds", rounds))
    run = f"<span class='mono'>{_esc(prov.run_id)}</span>"
    if prov.short_hash:
        run += f" · artifact <span class='mono'>{_esc(prov.short_hash)}</span>"
    rows.append(("Run", run))
    # Omitted entirely for a run that predates stamping, rather than shown as "unknown":
    # the record has nothing to say, and saying "unknown" implies it tried and failed.
    if prov.build_line:
        rows.append(("Built from", f"<span class='mono'>{_esc(prov.build_line)}</span>"))
    rows.append(("Exported", _esc(prov.exported_on)))
    return rows


def print_header_html(prov: Provenance) -> str:
    """A title block that exists only on paper.

    Printing a web page loses the browser chrome that told the reader what they were
    looking at, so the printed first page has to reintroduce the question and the
    verdict itself.
    """
    caveat = (
        ""
        if prov.clean
        else f'<p class="print-caveat">{_esc(prov.advice)}</p>'
    )
    hash_part = f" · artifact {_esc(prov.short_hash)}" if prov.short_hash else ""
    return f"""
<div class="print-only print-header">
  <h1>{_esc(prov.question)}</h1>
  <p class="print-meta">{_esc(prov.status.replace("_", " "))}
    {(" · " + _esc(prov.label)) if prov.label else ""}</p>
  <p class="print-meta">{_esc(prov.run_id)}{hash_part} · exported {_esc(prov.exported_on)}</p>
  {caveat}
</div>"""


def export_html(
    question: str,
    report: str,
    final: dict[str, Any] | None,
    run_id: str,
    *,
    exported_on: str | None = None,
    unreadable: bool = False,
) -> str:
    """One self-contained file: no stylesheet, font, script or image is fetched.

    That is not only a portability property. `web/markdown.py` disables images so that
    opening a report never causes an outbound request on the reader's behalf; a shared
    file that pulled a webfont would hand that property straight back, and it travels
    to people who have no idea what it is.
    """
    # Imported at call time, not module scope: `web` is an optional extra, and only the
    # HTML export needs it. This also keeps the dependency one-way — `web/render.py`
    # imports `STATUS_MEANING` from here — so the stylesheet has exactly one definition
    # and the printed page and the downloaded file can never drift apart.
    from .web.markdown import to_html
    from .web.render import CSS

    prov = provenance(question, final, run_id, exported_on=exported_on, unreadable=unreadable)
    body = f"""
{print_header_html(prov)}
<section class="panel reading screen-only">
  <p class="question">{_esc(prov.question)}</p>
  <p class="dim">{_esc(prov.status.replace("_", " "))}
    {(" · " + _esc(prov.label)) if prov.label else ""}</p>
</section>
<section class="panel reading">
  <article class="report">{to_html(report)}</article>
</section>
{provenance_html(prov)}"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Stricter than the served pages': an exported file has no origin worth talking to,
     so nothing but its own inline stylesheet is permitted. -->
<meta http-equiv="Content-Security-Policy" content="{_EXPORT_CSP}">
<title>{html.escape(question[:80])} — reasonable-answer</title>
<style>{CSS}</style>
</head>
<body class="exported">
<main>{body}</main>
</body>
</html>
"""


# -------------------------------------------------------------------- filenames

_SLUG = re.compile(r"[^a-z0-9]+")


def export_filename(question: str, run_id: str, ext: str) -> str:
    """A filename safe to put in a `Content-Disposition` header without quoting games.

    Question text reaches this from the request, so the output is restricted to
    `[a-z0-9-]` by construction rather than escaped after the fact.
    """
    slug = _SLUG.sub("-", question.lower()).strip("-")[:50].strip("-")
    stem = f"{slug}-{run_id}" if slug else run_id
    return f"{_SLUG.sub('-', stem.lower()).strip('-')}.{ext}"
