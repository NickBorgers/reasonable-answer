## D-citation-links — a citation is a link a reader can follow to the passage that was checked

**The problem.** A report cites with `[n]` markers and a `## Sources` list, and the web page
rendered both as plain text: the renderer's linkify is off (`web/markdown.py`), so even the URLs in
`## Sources` could not be clicked, and a marker gave a reader no way to reach the page it cites,
let alone the passage in it. Checking a claim meant finding the entry by number, copying its URL
and searching the page by hand. Where claim check is on, the pipeline had already found that
passage (`D-claim-level-verification`) and kept it in the run directory, unseen by any reader.

**Decision.** Citations become links at **render time**, for human readers only.

- Every body marker becomes a link per number to that bibliography entry's URL, as markdown whose
  visible text is still `[n]`. A list or range (`[1, 3]`, `[2-4]`) becomes one `[n]` link per
  number, because one anchor cannot point at three pages. A range `excerpt` would not expand
  (`[1-400]`), a number with no entry URL, a marker inside code or an existing link, a
  backslash-escaped marker, a number already defined as a link reference (`[3]: …`), and every
  marker inside `## Sources` are left exactly as written.
- Every URL inside `## Sources` becomes an autolink. Linkify stays off: URLs elsewhere in the
  body are model-written text that no citation stands behind, and linking them would add links
  nothing in the pipeline chose.
- A marker link carries a **text fragment** (`#:~:text=…`) when, and only when, a claim-check
  record for the shipped artifact holds a `supported` verdict for that exact (number, citing
  sentence) pair, with its verbatim `support_span`, on that entry's URL — and no record for the
  pair says `contradicted`. When several critics' records qualify, the one with the lowest
  sequence number wins, so the link does not change between page loads.
- One module builds the links (`citelinks.linked_markdown`), and the report page, Copy markdown,
  `export.md`, `export.html` and `ra export` all call it with the same spans, so every surface a
  report leaves by links identically (D-verdict-attached).

**Why render time, not the artifact.** Models quote the report. A critic's `claim_span` has to be
found in the report text (`triage._require_quote`, through `triage._normalize`, which does not
strip link syntax), and so does a dispute's claim span. A writer-authored `[[3]](https://…#:~:text=…)`
inside a sentence would make that sentence unquotable as prose, and the critique that quoted it
would be rejected as invented. It would also spend writer tokens on URLs, change the artifact hash
on every re-link, and put a URL the writer chose into the one place critics treat as the claim.
So the stored `final.md`, `GET /runs/<id>/report.md` (still byte for byte), the drafts under
`reports/`, and everything any model sees are unchanged. No prompt, isolation boundary,
`OrchestratorView` field or controller rule is touched.

**Why only verified spans become fragments.** A fragment is a claim: "the words backing this
sentence are *here*". A span taken from anywhere else — a writer's support manifest, an excerpt
window, a best-scoring passage — would present an unverified match as the evidence, and a reader
who lands on highlighted text reasonably takes it as the checked passage. A `supported` verdict is
the one place the pipeline has already required a verbatim span from the page and accepted it
(`schemas.ClaimVerdict`). A pair any checker called `contradicted` gets a plain link: pointing at
one checker's supporting passage while another found the page contradicting the claim would hide
the disagreement. The records are matched to the text being rendered by its own hash, which is
`final.json`'s `artifact_hash` by construction, so a record for an earlier draft never lends a
fragment to the shipped one — and a page whose `final.json` will not parse still gets its links.

**Fragment encoding.** Whitespace is collapsed. A span of at most ten words is matched whole; a
longer one as `textStart,textEnd` from its first and last five words, so a small difference between
the extracted page text and the live page's DOM inside the span is outside the two matching terms.
This follows the text-fragment directive's `textStart,textEnd` range form and its percent-encoding
requirements ([WICG text-fragment specification](https://wicg.github.io/scroll-to-text-fragment/)).
The implementation additionally encodes `-`, `,` and `&` so they cannot be read as directive
delimiters. A URL that already has a `#fragment` gets `:~:text=…` appended to it rather than a
second `#`; a URL that already carries a `:~:` directive is left alone.

**Why `rel="noreferrer noopener"` on every rendered link.** No Referrer-Policy is set anywhere
(`web/render.py`, D-base-path), and a report page's URL carries the run id, which is the credential
for reading the run (D-id-as-credential). Until now the report body produced few clickable links;
this decision produces one per citation, so without the attribute following a source would send
the page URL, run id included, to the cited site in the `Referer` header. The `link_open` renderer
rule sets it on the token, so no report text can omit it, and it holds in the exported HTML file
too. The HTML Standard defines `noreferrer` to suppress the `Referer` header when following the
link ([WHATWG, link type `noreferrer`](https://html.spec.whatwg.org/multipage/links.html#link-type-noreferrer)).
`html=False`, the disabled `image` rule and markdown-it's default link validator are unchanged; a
link is still inert until a reader clicks it, so the "opening a report makes no request" property
(D-verdict-attached) is untouched.

**Degradation.** Deep links are best-effort and fail to plain links, never to broken ones:

- claim check off, the pair over `max_pairs`, the page unread, or the verdict `absent`,
  `unreadable` or `unchecked` → a plain link to the entry URL;
- a `.pdf` URL → a plain link, because this decision does not establish text-fragment behavior for
  non-HTML document viewers and therefore does not promise a passage jump there;
- a browser without text-fragment support, or a page where the directive does not match the live
  text → the fragment has no highlighting or scroll effect
  ([MDN, Text fragments](https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments));
- `purge --content-only` removes `critiques/` → plain links; it removes `final.md` too, so a purged
  run has no report to render in the first place;
- a missing, truncated or malformed record file → skipped, never raised (`store.read_claim_checks`).

**Out of scope.** The writer's support manifest (`support/rNN.json`) as a second span source — it
is the writer's own assertion, not a check. Linking body URLs outside `## Sources`. Linking a
marker's number inside a Sources entry back to the sentences that cite it.
