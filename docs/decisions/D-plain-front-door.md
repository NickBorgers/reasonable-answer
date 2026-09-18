## D-plain-front-door — the pages a first-time user meets say what they get and how to get it, and nothing else

**The problem.** The index opened with a 120-word paragraph of doctrine — fresh contexts, narrow
questions, the round cap, "plain code, not an LLM, makes that call" — above the question box, then
offered a seed textarea and (where enabled) a URL field on equal footing with the question, and
closed with a second doctrinal paragraph over the roster. The detail belongs in `docs/`; the page
needed to tell a visitor what they would get back and that they could leave.
The report page had the mirror problem: a reader who came back for their answer met the status
badge, a run id, a "shipped from round" note and four take-it-away buttons between the answer card
and the report's second section. D-answer-card had already moved the conclusion above that
furniture; the furniture itself was still a screen of controls most readers never press. And the
status sentence a stranger reads first — "every lens cleared by two cross-family non-author models
on the final artifact" — was written in the vocabulary of `docs/convergence.md`, not in theirs.

**Decision.**

1. *The index lede says what the visitor gets and how long it takes, in plain words.* Several
   different AI models write, then check each other's drafts; no model reviews its own writing;
   accepted when the reviewers find nothing material left to fix, or returned with recorded
   objections if the review limit is reached first; here is what the report contains; 10–25
   minutes; safe to close the tab. The doctrine is one click away in the header
   (`how this works`), where it was already. The config-derived sourcing sentence stays, shortened,
   as a separate line under the form — it is a claim about *this deployment* and the header
   tagline cannot make it.
2. *The optional half of the ask form folds closed.* The seed textarea and the URL field sit in a
   `<details>` under the question; a first visit shows one box and one button. The roster panel
   folds the same way. Both are plain `<details>`: no script, no CSP change, and the refine
   feature's hidden fields and chip container are untouched — `refine.enabled = false` still
   renders the page byte-for-byte without them (docs/question-refinement.md).
3. *The report page keeps only the verdict and one link between the answer and the body.* The run
   id and the shipped round come out of the page chrome — the review record below states both.
   Copy markdown, the two downloads and `audit.json` move into one closed fold at the *end* of the
   report, after the sources. Every control is still on the page and still a public GET; the
   fold changes where a reader meets them, not whether.
4. *The status sentences are rewritten in plain words.* `STATUS_MEANING` is what a stranger reads
   first, on screen and in the exported file (D-verdict-attached), so each entry now says the
   condition without the internal nouns — every review dimension is cleared by two models from
   different model families, neither the model that wrote the final draft. The conditions
   themselves are unchanged and still match the terminal-status table in `docs/convergence.md`.
5. *A live run says what happens now.* Under the status, while the run is in flight: the models are
   working, the page updates itself, close it and come back, the report link appears here.

**What this does not change.** No route, no gate, no invariant. The share controls and the audit
link are asserted present on the report page by the same tests as before; a new test pins that they
sit after the report in a closed fold. Two words in the lede are load-bearing and were chosen, not
merely plain: "drafts" — models check each other's *drafts*, and the sentence must not read as models
arguing with each other, which the design exists to prevent (QP6, and the header tagline's own
note); and "different" — a draft is never reviewed by the model that wrote it (author exclusion,
[isolation.md](../isolation.md)). The verify-sources sentence stays config-derived for the reason
D-source-verification gives: the shipped roster retrieves but does not verify, and a static line
would overclaim.

**Why not a separate "about" page.** The doctrine already has a home, and the visitor's problem was
not that the explanation was missing but that it stood between them and the question box. Moving
it to a second page would have added a page to maintain and left the header link as a duplicate.

**Why the fold is closed and at the end, not open and at the top.** The design keeps the report's
reading path ahead of its file controls, extending D-answer-card's decision to put the conclusion
before page furniture. An open fold is a row of buttons with a heading, which is what this replaces.
