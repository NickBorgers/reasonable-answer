"""Markdown -> HTML for report bodies.

The report is model-written text: untrusted on the way out, exactly like the question.
So the renderer is configured to *never* pass raw HTML through (`html=False` escapes
any tags in the source) and to keep markdown-it's default link validator, which drops
`javascript:`, `vbscript:` and non-image `data:` URLs. That combination is what makes
it safe to drop the result into the page unescaped; nothing else here may do that.

Tables and strikethrough are enabled on top of CommonMark because reports use them.
Linkify is deliberately left off: it would pull in another dependency to turn bare
URLs into links, and reports cite with explicit `[1]` markers and a Sources section.
"""

from __future__ import annotations

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_MD.enable("table")
_MD.enable("strikethrough")


def to_html(markdown: str) -> str:
    return _MD.render(markdown)
