## D-access-wall-detection — a wall served with a 200 is a refusal, not a body

**The problem.** Both rungs that return page text accepted any non-empty text as the cited body.
The direct fetch marked it `FULL_TEXT`; the rendering tier (D-paid-tier-page) took any non-blank
markdown. A bot challenge is usually served with a 200 and parses into a tidy paragraph, so
"Just a moment… Performing security verification" entered the evidence lens as the cited source
and counted as a body read. Production critics have said so in their own rationales — sources
described as "a captcha page", "a reCAPTCHA page" and "a cookie consent page" — and Firecrawl
returned JSTOR's login wall as a page of markdown. Coverage's `bodies_read` overstated what was
read by an amount nothing measured.

**The decision.** `fetch.access_wall(title, text)` names a page that is a wall rather than a
document, as one of three closed kinds: `bot_challenge`, `access_denied`, `login_wall`.

- On the direct fetch, a recognised wall becomes `BLOCKED` with the error
  `access wall (<kind>)`, keeping its HTTP status. `BLOCKED` is already what a refused client
  is: a non-body to the claim check (D-anchored-unreadable), blocked in coverage, never
  evidence of absence (D-notfound-fabrication) — and it sends the citation on down the
  resolver ladder, so the rendering tier gets its turn exactly as after a 403.
- On the rendering tier, a recognised wall is no body: the direct verdict stands, with the
  wall noted in its error, as for a blank render.

**How a wall is recognised, and why so narrowly.** By the phrases a wall uses about *itself*.
Most are taken from walls actually served to this pipeline's clients — Cloudflare, Akamai,
CloudFront, Elsevier, IEEE, SSRN, ResearchGate, Semantic Scholar, NCBI's reCAPTCHA page and JSTOR,
plus the "a required part of this site couldn't load" page the direct fetch gets from Springer
Nature and JSTOR. A few are the standard interstitial wording of DataDome, PerimeterX and
Imperva, which the calibration set happened not to meet. Two bounds keep an article *about* bot walls from being taken for one: no page
longer than `WALL_MAX_CHARS` (6,000) is a wall, and "access denied" — ordinary prose too — counts
only as the title or the opening words of a page under 1,500 characters. A cookie banner over
real text is deliberately not a wall; neither is a page with a newsletter CAPTCHA beside its
content.

Calibrated on 249 saved non-empty responses to 79 cited URLs from four clients (the direct fetch, two
headless-browser configurations and Firecrawl). It flagged 57 pages; every one is a wall. The
short pages it does not flag are landing pages, thin government pages, a soft 404 and a cookie
banner over an abstract — not documents, some of them, but not walls either, and outside this
decision.

**What a false positive costs, and a false negative.** A real page taken for a wall is reported
blocked, so its claims get the claim check's request for a readable copy instead of a check — a
lost check, never a fabrication finding. A missed wall is what happened before this change.
The narrow phrase list trades recall for that asymmetry.

**Not done.** Paywall teasers and cookie-consent-only pages are not detected: their wording is
also ordinary article prose, and the claim check's span-anchored `unreadable` verdict
(D-anchored-unreadable) already covers a page a checker can see is not the document.

**Invariants.** Untouched. The kinds are a closed vocabulary and no page text reaches the error
or the audit trail (RA-016); the change can only remove text from a critic's context, never add
it; `NOT_FOUND` and `fabricated_citation` are not reachable from it.
