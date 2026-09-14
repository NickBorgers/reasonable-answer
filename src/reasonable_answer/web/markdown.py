"""Markdown -> HTML for report bodies.

The report is model-written text: untrusted on the way out, exactly like the question.
So the renderer is configured to *never* pass raw HTML through (`html=False` escapes
any tags in the source) and to keep markdown-it's default link validator, which drops
`javascript:`, `vbscript:` and non-image `data:` URLs. That combination is what makes
it safe to drop the result into the page unescaped; nothing else here may do that.

Images are disabled for the same reason. A link is inert until a human clicks it, but
an `<img>` is an automatic outbound GET from the reader's browser the moment the page
loads — which, on a tailnet deployment, is a way for report text to probe addresses
only the reader can reach, or to phone home when a report is opened. `![x](url)` is
left as literal text instead.

Tables and strikethrough are enabled on top of CommonMark because reports use them.
Linkify is deliberately left off: it would pull in another dependency to turn bare
URLs into links, and would link every URL a model writes anywhere in the body. The links a
reader follows are built mechanically before rendering instead (`citelinks`,
D-citation-links): body `[n]` markers become links to their bibliography entry, and the
URLs inside `## Sources` become autolinks. They arrive here as ordinary markdown and pass
the same validator as any other link.

**Every rendered link carries `rel="noreferrer noopener"`.** No Referrer-Policy is set
anywhere, and a report page's URL carries its run id — the credential for reading the run
(D-id-as-credential). Without this, following a citation would hand that id to the cited
site in the `Referer` header. The attribute is set on the token, by the renderer, so no
report text can omit it, and it applies to the exported HTML file as well as the page.

A table is the one construct a model can write that is wider than any phone, so every
table is wrapped in a scrolling `<div>` here rather than left to the stylesheet. Doing it
at the renderer keeps the table a real `<table>` — the CSS-only alternative needs
`display: block`, which throws away the `width: 100%` that makes narrow tables look right
on a desktop — and it is a string the tests can assert on. The wrapper is emitted by the
renderer and never by report text: `html=False` means a model cannot write a `</div>` to
break out of it.
"""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_MD.enable("table")
_MD.enable("strikethrough")
_MD.disable("image")

#: The default `<table>`/`</table>` renderer, which the two rules below delegate to. Bound
#: before the rules replace it so the wrapping is additive rather than a reimplementation.
_RENDER_TOKEN = _MD.renderer.renderToken


def _table_open(tokens: Any, idx: int, options: Any, env: Any) -> str:
    return '<div class="table-scroll">' + _RENDER_TOKEN(tokens, idx, options, env)


def _table_close(tokens: Any, idx: int, options: Any, env: Any) -> str:
    return _RENDER_TOKEN(tokens, idx, options, env) + "</div>"


def _link_open(tokens: Any, idx: int, options: Any, env: Any) -> str:
    tokens[idx].attrSet("rel", "noreferrer noopener")
    return _RENDER_TOKEN(tokens, idx, options, env)


_MD.renderer.rules["table_open"] = _table_open
_MD.renderer.rules["table_close"] = _table_close
_MD.renderer.rules["link_open"] = _link_open


def to_html(markdown: str) -> str:
    return _MD.render(markdown)
