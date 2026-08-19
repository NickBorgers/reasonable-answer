## D-answer-card — the report page leads with the conclusion, and fails open to the page it replaced

**The problem.** The report page rendered the body as one blob below the page's own furniture: on a
375px viewport a reader got the question, the status badge and its meaning sentence, the back-link,
the run id, and a four-control share row — roughly a screen and a half — before the report's first
sentence. D-report-template makes every report open with a `## Conclusion` section; a page that
buries that section under chrome squanders exactly what the frame exists to deliver, and the narrow
viewport measured above is where that burial costs the most.

**Decision.** When the rendered body opens with a `## Conclusion` h2, `render_report` splits it at
its `<h2>` boundaries and reassembles the page conclusion-first: the conclusion as a distinct card
*above* the status/share furniture, the counterargument section boxed where it stands, the trailing
`## Sources` folded behind its entry count. Server-side string transform in `web/render.py`, CSS
only, no new JS, no CSP change.

**Fail open, because the structure is model-written.** The frame is a prompt, not a validator
(D-report-template), so the splitter trusts nothing: a body with preamble before the first heading, a
first heading that is not `Conclusion`, or a heading carrying inline markup gets the exact page the
route served before this existed — one plain article under the furniture. A pre-frame run, a seeded
artifact, and a writer that ignored the skeleton all degrade to the old page rather than to a broken
one. markdown-it with `html=False` emits bare `<h2>text</h2>` blocks joined by newlines, which is
what makes the split safe to do with a string operation; anything off that shape falls through.

**The counterargument is boxed, never folded.** An objection separated from its answer reads as
stronger than it is — the same reason D-report-template forbids raising an objection without engaging
it. So the counterargument gets visual prominence in place (a bordered box in the article flow), and
no disclosure control that could ever show the objection without the engagement.

**Sources fold on screen and duplicate for print.** The fold is the one collapse this page makes: a
reference list of long URLs is the least-read, most space-hungry section on a phone. A closed
`<details>` prints as nothing, and the print stylesheet exists precisely because a report that
reaches paper must keep its verdict and its evidence — so the fold is `screen-only` and a
`print-only` duplicate carries the full list onto paper — a CSS-only mechanism with no `beforeprint`
JS, consistent with this decision's no-new-JS, no-CSP-change constraint. `export.html` is untouched:
the transform lives in `web/render.py`, downstream of the shared markdown renderer, so the no-script
export keeps its single-article shape.

**Deliberately not done.** No sticky section-jump bar, no collapsed topical sections, no citation
drill-in yet — each builds on this sectionizer and each is its own decision; collapse-by-default in
particular is deferred to that separate decision rather than adopted here, precisely because folding
a section away from its answer is the kind of tradeoff this decision is careful about. And no
restructuring of the run page: it shows the pipeline, not the report.
