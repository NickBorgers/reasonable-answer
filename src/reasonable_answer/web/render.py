"""HTML rendering.

Hand-written rather than templated: the surface is three pages, and keeping it in
Python means no template loader, no packaging of template files, and no build step
in the container. Every interpolation of run-derived text goes through `esc`.
"""

from __future__ import annotations

import html
import time
from typing import Any

from ..config import Config
from .markdown import to_html
from .registry import RoundSnapshot, RunSummary

STATUS_TONE = {
    "accepted": "good",
    "converged_unconfirmed": "ok",
    "exhausted_unresolved": "warn",
    "needs_human_review": "bad",
    "aborted": "bad",
    "running": "live",
    "queued": "live",
    "interrupted": "warn",
}

STATUS_MEANING = {
    "accepted": "every lens cleared by two distinct non-author models on the final artifact",
    "converged_unconfirmed": "every lens cleared, but a lens had only one eligible reviewer",
    "exhausted_unresolved": "reached the cap or stagnated with only non-blocking issues left",
    "needs_human_review": "reached the cap, stagnated or cycled with blocking issues present",
    "aborted": "fatal: a model was unavailable or a review could not be completed",
    "queued": "waiting for a worker",
    "running": "in progress",
    "interrupted": "the process stopped before finishing; this run can be resumed",
}


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _ago(ts: float | None) -> str:
    if not ts:
        return "—"
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _short(identity: str | None) -> str:
    """`openrouter/mistralai/mistral-large-2512` -> `mistral-large-2512`, which is what
    a human is actually scanning for."""
    if not identity:
        return "—"
    return identity.split("/")[-1]


# --------------------------------------------------------------------- layout


def render_layout(title: str, body: str, live: bool = False) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Belt to the renderer's braces: the report is model-written, so even if some future
     construct slips past markdown-it, the browser has no directive that lets this page
     fetch anything off-origin. `unsafe-inline` covers the stylesheet and the SSE script,
     both of which are literals in this file; `connect-src 'self'` is the progress stream. -->
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <a class="brand" href="/">reasonable&#8209;answer</a>
  <span class="tag">consensus-reviewed with in-artifact sourcing</span>
</header>
<main>{body}</main>
{'<script>' + LIVE_JS + '</script>' if live else ''}
</body>
</html>"""


# ---------------------------------------------------------------------- index


def render_index(runs: list[RunSummary], queue_depth: int, config: Config) -> str:
    rows = (
        "\n".join(_run_row(r) for r in runs)
        or '<tr><td colspan="5" class="empty">No runs yet. Ask something above.</td></tr>'
    )
    depth = (
        f'<p class="queued-note">{queue_depth} run(s) waiting for a worker.</p>'
        if queue_depth
        else ""
    )
    # Omitted entirely when URL seeds are off, so the form never offers something the
    # handler will reject.
    seed_url_field = (
        """<label for="seed_url">&hellip;or a URL <span class="hint">a web page, PDF or
      .docx to fetch and convert</span></label>
    <input type="url" id="seed_url" name="seed_url" placeholder="https://example.org/report.pdf">"""
        if config.seed.allow_url
        else ""
    )
    body = f"""
<section class="panel">
  <h1>Ask a question</h1>
  <p class="lede">A roster of models will take turns writing and critiquing an answer until no
  eligible reviewer can find a material defect &mdash; or until the cap stops them.
  Expect this to take <strong>10&ndash;25 minutes</strong>.</p>
  <form method="post" action="/runs">
    <label for="question">Question</label>
    <textarea id="question" name="question" rows="3" required maxlength="{config.max_question_chars}"
      placeholder="Is remote work better for software team productivity?"></textarea>
    <label for="seed">Seed report <span class="hint">optional &mdash; an existing draft to improve
      instead of starting from scratch</span></label>
    <textarea id="seed" name="seed" rows="5" maxlength="{config.max_report_chars}"
      placeholder="Paste a draft &mdash; Markdown or HTML.&#10;&#10;Leave empty to write from scratch."></textarea>
    {seed_url_field}
    <button type="submit">Start run</button>
  </form>
  {depth}
</section>

<section class="panel">
  <h2>Runs</h2>
  <table class="runs">
    <thead><tr><th>status</th><th>question</th><th>rounds</th><th>started</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>

<section class="panel roster">
  <h2>Roster</h2>
  <p class="lede">A report is never critiqued &mdash; on any lens &mdash; by the model that wrote it.</p>
  <div class="roster-grid">
    <div><h3>writers</h3><ul>{_model_list(config.roster.writers)}</ul></div>
    {"".join(f"<div><h3>{esc(lens)}</h3><ul>{_model_list(pool)}</ul></div>"
             for lens, pool in config.roster.critics.items())}
  </div>
</section>
"""
    return render_layout("reasonable-answer", body)


def _model_list(models: list[str]) -> str:
    return "".join(f"<li>{esc(m)}</li>" for m in models)


def _run_row(run: RunSummary) -> str:
    question = run.question if len(run.question) <= 90 else run.question[:87] + "…"
    return f"""<tr>
  <td>{_badge(run.status)}</td>
  <td class="q"><a href="/runs/{esc(run.run_id)}">{esc(question)}</a></td>
  <td class="num">{run.rounds or "—"}</td>
  <td class="dim">{_ago(run.started_at)}</td>
  <td class="dim mono">{esc(run.run_id)}</td>
</tr>"""


def _badge(status: str) -> str:
    tone = STATUS_TONE.get(status, "ok")
    label = status.replace("_", " ")
    pulse = ' <span class="pulse"></span>' if tone == "live" else ""
    return f'<span class="badge {tone}" title="{esc(STATUS_MEANING.get(status, ""))}">{esc(label)}{pulse}</span>'


# ------------------------------------------------------------------ run page


def render_run(
    summary: RunSummary,
    timeline: list[RoundSnapshot],
    report: str | None,
    final: dict[str, Any] | None,
    lens_names: list[str],
) -> str:
    resume = (
        f"""<form method="post" action="/runs/{esc(summary.run_id)}/resume" class="inline">
        <button type="submit" class="secondary">Resume this run</button></form>"""
        if summary.status == "interrupted"
        else ""
    )

    downloads = (
        f"""<a class="button" href="/runs/{esc(summary.run_id)}/report">Read the report</a>
        <a class="secondary button" href="/runs/{esc(summary.run_id)}/report.md">report.md</a>
        <a class="secondary button" href="/runs/{esc(summary.run_id)}/audit.json">audit.json</a>"""
        if report
        else f'<a class="secondary button" href="/runs/{esc(summary.run_id)}/audit.json">audit.json</a>'
    )

    # Once there is a report to read, the report is the page and the round-by-round
    # trail is supporting evidence — so it moves below and folds away. While the run is
    # live it is the only thing there is to look at, so it stays open.
    progress = f"""<section class="panel" id="progress"
   data-stream="/runs/{esc(summary.run_id)}/stream"
   data-live="{'1' if summary.is_live else '0'}">
{render_run_progress(summary, timeline, lens_names)}
</section>"""
    if report:
        progress = f"""<details class="fold">
  <summary>How it got here — {summary.rounds or "no"} round{"" if summary.rounds == 1 else "s"} of
  write and critique</summary>
  {progress}
</details>"""

    body = f"""
<section class="panel run-head">
  <div class="run-title">
    <h1>{esc(summary.question)}</h1>
    <div class="run-meta">
      {_badge(summary.status)}
      <span class="dim mono">{esc(summary.run_id)}</span>
      <span class="dim">started {_ago(summary.started_at)}</span>
    </div>
    <p class="lede">{esc(STATUS_MEANING.get(summary.status, ""))}
    {(" — " + esc(summary.terminal_note)) if summary.terminal_note else ""}</p>
  </div>
  <div class="run-actions">{downloads}{resume}</div>
</section>

{_report_section(report, final)}

{progress}
"""
    return render_layout(f"{summary.question[:60]} — reasonable-answer", body, live=summary.is_live)


def render_run_progress(
    summary: RunSummary, timeline: list[RoundSnapshot], lens_names: list[str]
) -> str:
    if not timeline:
        return '<h2>Progress</h2><p class="empty">Waiting for the first draft…</p>'
    rounds = "\n".join(_round_card(r, lens_names) for r in reversed(timeline))
    return f"<h2>Progress</h2>\n<ol class='timeline'>{rounds}</ol>"


def _round_card(r: RoundSnapshot, lens_names: list[str]) -> str:
    lenses = "\n".join(_lens_row(r, name) for name in lens_names)
    counts = (
        f'<span class="count blocking">{r.blocking} blocking</span>'
        f'<span class="count major">{r.major} major</span>'
        f'<span class="count minor">{r.minor} minor</span>'
        if (r.blocking or r.major or r.minor)
        else '<span class="count clean">no material issues</span>'
    )
    decision = (
        f'<div class="decision"><span class="rule">rule {r.rule}</span>'
        f'<span class="action">{esc(r.action)}</span>'
        f'<span class="dim">{esc(r.note)}</span></div>'
        if r.rule
        else '<div class="decision pending"><span class="dim">deciding…</span></div>'
    )
    polish = '<span class="chip">polish pass</span>' if r.polish else ""
    return f"""<li class="round">
  <div class="round-head">
    <span class="round-no">round {r.round}</span>
    <span class="writer">writer <strong>{esc(_short(r.writer))}</strong></span>
    {polish}
    <span class="dim mono hash">{esc((r.artifact_hash or "")[:12])}</span>
  </div>
  <div class="lenses">{lenses}</div>
  <div class="round-foot">{counts}{decision}</div>
</li>"""


def _lens_row(r: RoundSnapshot, lens: str) -> str:
    snap = r.lenses.get(lens)
    if snap is None:
        return f'<div class="lens pending"><span class="lens-name">{esc(lens)}</span>'\
               f'<span class="dim">waiting…</span></div>'
    if snap.failed:
        return (
            f'<div class="lens failed"><span class="lens-name">{esc(lens)}</span>'
            f'<span class="critic">{esc(_short(snap.critic))}</span>'
            f'<span class="verdict bad" title="{esc(snap.failure_reason)}">lens failed</span></div>'
        )
    verdict = (
        '<span class="verdict good">clean</span>'
        if snap.issues == 0
        else f'<span class="verdict">{snap.issues} issue{"s" if snap.issues != 1 else ""}</span>'
    )
    return (
        f'<div class="lens"><span class="lens-name">{esc(lens)}</span>'
        f'<span class="critic">{esc(_short(snap.critic))}</span>{verdict}</div>'
    )


def _report_section(report: str | None, final: dict[str, Any] | None) -> str:
    if not report:
        return ""
    outstanding = (final or {}).get("outstanding_defects") or []
    warnings = (final or {}).get("warnings") or []

    defects = ""
    if outstanding:
        items = "".join(
            f'<li><span class="chip {esc(d.get("severity"))}">{esc(d.get("severity"))}</span>'
            f'<span class="chip">{esc(d.get("category"))}</span> '
            f'{esc(d.get("instruction"))}</li>'
            for d in outstanding
        )
        defects = f"""<div class="callout warn">
          <h3>Outstanding defects in the shipped report</h3>
          <p>These were raised and not resolved before the run stopped.</p>
          <ul class="defects">{items}</ul></div>"""

    warn = ""
    if warnings:
        warn = '<div class="callout">' + "".join(f"<p>{esc(w)}</p>" for w in warnings) + "</div>"

    chosen = (final or {}).get("chosen_round")
    provenance = (
        f'<p class="lede">Shipped the best-scoring draft (round {esc(chosen)}), '
        f"not necessarily the last one written.</p>"
        if chosen
        else ""
    )

    return f"""
<section class="panel">
  <h2>Report</h2>
  {provenance}
  {defects}
  {warn}
  <article class="report">{to_html(report)}</article>
</section>"""


def render_report(summary: RunSummary, report: str, final: dict[str, Any] | None) -> str:
    """The report on its own page — the thing to hand to someone who wants to *read* it,
    rather than watch the pipeline that produced it."""
    chosen = (final or {}).get("chosen_round")
    provenance = f" · shipped from round {esc(chosen)}" if chosen else ""
    body = f"""
<section class="panel reading">
  <div class="run-meta">
    {_badge(summary.status)}
    <a class="dim" href="/runs/{esc(summary.run_id)}">back to the run</a>
    <span class="dim mono">{esc(summary.run_id)}{provenance}</span>
    <a class="dim" href="/runs/{esc(summary.run_id)}/report.md">report.md</a>
  </div>
  <p class="question">{esc(summary.question)}</p>
  <article class="report">{to_html(report)}</article>
</section>"""
    return render_layout(f"{summary.question[:60]} — reasonable-answer", body)


# ------------------------------------------------------------------- assets

LIVE_JS = """
(function () {
  var el = document.getElementById('progress');
  if (!el || el.dataset.live !== '1') return;
  var src = new EventSource(el.dataset.stream);
  src.addEventListener('progress', function (e) {
    el.innerHTML = e.data;
  });
  src.addEventListener('done', function () {
    src.close();
    location.reload();
  });
  src.onerror = function () { /* browser retries on its own */ };
})();
"""

CSS = """
:root {
  --bg: #fbfaf8; --panel: #ffffff; --ink: #1a1a1a; --dim: #6b6b6b;
  --line: #e4e1dc; --accent: #2f5d50; --good: #2f6f4f; --warn: #8a6d1f;
  --bad: #97331f; --live: #2f5d50; --chip: #f0eeea;
}
:root[data-theme="dark"], html:not([data-theme="light"]) {}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181a; --panel: #1e2124; --ink: #e8e6e3; --dim: #9a9691;
    --line: #2e3236; --accent: #7fbfa8; --good: #7fbfa8; --warn: #d4b062;
    --bad: #e08b76; --live: #7fbfa8; --chip: #2a2e32;
  }
}
:root[data-theme="dark"] {
  --bg: #16181a; --panel: #1e2124; --ink: #e8e6e3; --dim: #9a9691;
  --line: #2e3236; --accent: #7fbfa8; --good: #7fbfa8; --warn: #d4b062;
  --bad: #e08b76; --live: #7fbfa8; --chip: #2a2e32;
}
:root[data-theme="light"] {
  --bg: #fbfaf8; --panel: #ffffff; --ink: #1a1a1a; --dim: #6b6b6b;
  --line: #e4e1dc; --accent: #2f5d50; --good: #2f6f4f; --warn: #8a6d1f;
  --bad: #97331f; --live: #2f5d50; --chip: #f0eeea;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.mono, .hash, .runs td.dim.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85em; }
header {
  display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
  padding: 1.1rem 1.5rem; border-bottom: 1px solid var(--line);
}
.brand { font-weight: 650; letter-spacing: -.01em; color: var(--ink); text-decoration: none; font-size: 1.05rem; }
.tag { color: var(--dim); font-size: .8rem; }
main { max-width: 60rem; margin: 0 auto; padding: 1.5rem; display: grid; gap: 1.25rem; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 1.25rem 1.4rem; }
h1 { font-size: 1.3rem; margin: 0 0 .4rem; letter-spacing: -.01em; }
h2 { font-size: 1rem; margin: 0 0 .9rem; text-transform: uppercase; letter-spacing: .07em; color: var(--dim); }
h3 { font-size: .8rem; margin: 0 0 .4rem; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); }
.lede { color: var(--dim); margin: .2rem 0 1rem; }
.hint { color: var(--dim); font-weight: 400; }
label { display: block; font-weight: 550; margin: .9rem 0 .35rem; font-size: .9rem; }
textarea {
  width: 100%; padding: .7rem .8rem; border: 1px solid var(--line); border-radius: 8px;
  background: var(--bg); color: var(--ink); font: inherit; resize: vertical;
}
textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
button, .button {
  display: inline-block; margin-top: 1rem; padding: .55rem 1.1rem; border: 0; border-radius: 7px;
  background: var(--accent); color: var(--bg); font: inherit; font-weight: 600; cursor: pointer;
  text-decoration: none;
}
button.secondary, .button.secondary {
  background: transparent; color: var(--ink); border: 1px solid var(--line); font-weight: 500;
}
form.inline { display: inline; }
table.runs { width: 100%; border-collapse: collapse; }
.runs th {
  text-align: left; font-size: .72rem; text-transform: uppercase; letter-spacing: .07em;
  color: var(--dim); font-weight: 600; padding: 0 .6rem .5rem 0; border-bottom: 1px solid var(--line);
}
.runs td { padding: .6rem .6rem .6rem 0; border-bottom: 1px solid var(--line); vertical-align: middle; }
.runs tr:last-child td { border-bottom: 0; }
.runs td.q a { color: var(--ink); text-decoration: none; }
.runs td.q a:hover { color: var(--accent); text-decoration: underline; }
.runs td.num { text-align: right; width: 4rem; color: var(--dim); }
.dim { color: var(--dim); }
.empty { color: var(--dim); padding: 1rem 0; }
.badge {
  display: inline-flex; align-items: center; gap: .35rem; padding: .18rem .55rem; border-radius: 999px;
  font-size: .74rem; font-weight: 600; border: 1px solid var(--line); background: var(--chip); white-space: nowrap;
}
.badge.good { color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, transparent); }
.badge.warn { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, transparent); }
.badge.bad { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, transparent); }
.badge.live { color: var(--live); border-color: color-mix(in srgb, var(--live) 40%, transparent); }
.pulse {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor;
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
.run-head { display: flex; gap: 1.25rem; justify-content: space-between; flex-wrap: wrap; }
.run-title { flex: 1 1 24rem; }
.run-meta { display: flex; gap: .7rem; align-items: center; flex-wrap: wrap; margin-bottom: .5rem; }
.run-actions { display: flex; gap: .5rem; align-items: flex-start; flex-wrap: wrap; }
.timeline { list-style: none; margin: 0; padding: 0; display: grid; gap: .7rem; }
.round { border: 1px solid var(--line); border-radius: 9px; padding: .8rem .9rem; }
.round-head { display: flex; gap: .8rem; align-items: baseline; flex-wrap: wrap; margin-bottom: .6rem; }
.round-no { font-weight: 650; }
.writer { color: var(--dim); font-size: .88rem; }
.writer strong { color: var(--ink); font-weight: 600; }
.hash { margin-left: auto; }
.lenses { display: grid; gap: .3rem; }
.lens {
  display: grid; grid-template-columns: 7.5rem 1fr auto; gap: .6rem; align-items: baseline;
  padding: .3rem .5rem; border-radius: 6px; background: var(--bg); font-size: .88rem;
}
.lens.pending { opacity: .55; }
.lens-name { color: var(--dim); text-transform: uppercase; font-size: .72rem; letter-spacing: .06em; }
.critic { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem; }
.verdict { font-weight: 600; font-size: .82rem; }
.verdict.good { color: var(--good); }
.verdict.bad { color: var(--bad); }
.round-foot {
  display: flex; gap: .8rem; align-items: center; flex-wrap: wrap;
  margin-top: .7rem; padding-top: .6rem; border-top: 1px solid var(--line);
}
.count { font-size: .78rem; color: var(--dim); }
.count.blocking { color: var(--bad); font-weight: 600; }
.count.major { color: var(--warn); font-weight: 600; }
.count.clean { color: var(--good); font-weight: 600; }
.decision { margin-left: auto; display: flex; gap: .5rem; align-items: baseline; font-size: .8rem; }
.rule {
  font-family: ui-monospace, monospace; background: var(--chip); padding: .1rem .4rem;
  border-radius: 4px; font-size: .76rem;
}
.action { font-weight: 600; }
.chip {
  display: inline-block; background: var(--chip); border-radius: 4px; padding: .08rem .4rem;
  font-size: .74rem; margin-right: .3rem;
}
.chip.blocking { color: var(--bad); }
.chip.major { color: var(--warn); }
.callout { border: 1px solid var(--line); border-left: 3px solid var(--warn); border-radius: 6px; padding: .8rem 1rem; margin-bottom: 1rem; }
.callout h3 { margin-top: 0; color: var(--ink); }
.defects { margin: .5rem 0 0; padding-left: 1.1rem; }
.defects li { margin-bottom: .4rem; font-size: .9rem; }
/* The report is model-written markdown rendered to HTML, so it is the one place in
   this stylesheet that has to style tags it did not author. Everything is scoped
   under .report for that reason. */
.report {
  line-height: 1.7; background: var(--bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 1.4rem 1.6rem; overflow-wrap: break-word;
}
.report > :first-child { margin-top: 0; }
.report > :last-child { margin-bottom: 0; }
.report h1, .report h2, .report h3, .report h4 { line-height: 1.3; margin: 1.8rem 0 .6rem; }
.report h1 { font-size: 1.5rem; }
.report h2 { font-size: 1.2rem; padding-bottom: .3rem; border-bottom: 1px solid var(--line); }
.report h3 { font-size: 1rem; }
.report h4 { font-size: .95rem; color: var(--dim); }
.report p, .report ul, .report ol, .report blockquote { margin: 0 0 1rem; }
.report li { margin-bottom: .3rem; }
.report a { color: var(--accent); }
.report blockquote {
  border-left: 3px solid var(--line); margin-left: 0; padding: .1rem 0 .1rem 1rem; color: var(--dim);
}
.report code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85em;
  background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: .05rem .3rem;
}
.report pre {
  background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  padding: .8rem 1rem; overflow-x: auto;
}
.report pre code { background: none; border: 0; padding: 0; }
.report table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; font-size: .9rem; }
.report th, .report td { border: 1px solid var(--line); padding: .4rem .6rem; text-align: left; }
.report th { background: var(--panel); }
.report hr { border: 0; border-top: 1px solid var(--line); margin: 1.8rem 0; }
/* The Sources section is a reference list, not prose — tighten it and let long URLs wrap. */
.report h2 + ol, .report h2 + ul { font-size: .9rem; }
.fold > summary {
  cursor: pointer; color: var(--dim); font-size: .9rem; padding: .4rem 0; list-style-position: outside;
}
.fold > summary:hover { color: var(--ink); }
.fold[open] > summary { margin-bottom: .6rem; }
.fold #progress h2 { margin-top: 0; }
.reading { max-width: 48rem; margin: 0 auto; }
.reading .run-meta { margin-bottom: .8rem; }
.reading .question { font-size: 1.05rem; font-weight: 600; margin: 0 0 1rem; }
.roster-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 1rem; }
.roster-grid ul { list-style: none; margin: 0; padding: 0; }
.roster-grid li {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .78rem;
  padding: .15rem 0; color: var(--dim);
}
.queued-note { color: var(--dim); font-size: .85rem; margin-bottom: 0; }
@media (max-width: 34rem) {
  .lens { grid-template-columns: 1fr; gap: .1rem; }
  .decision { margin-left: 0; }
  .hash { margin-left: 0; }
}
"""
