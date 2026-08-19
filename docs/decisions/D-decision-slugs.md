## D-decision-slugs — decisions are identified by a subject slug, not a shared counter

**The problem.** A decision identifier was a number allocated from a single sequence on `main`, so
choosing one meant knowing what every in-flight PR had already chosen. Two PRs opened against the same
base necessarily draw the same next-free number from that shared maximum, and the collision surfaces only
at merge — by construction, not by luck, because neither PR's merge ref contains the other's choice. The
only remedy the counter offered was to renumber, and a renumber is a push: it resets the review cycle and
spends a full panel run. That cost lands in the most expensive place a rename can, because the number is
echoed into the commit subject, the PR title and body, and across `config/`, `src/`, `tests/` and docs.

The counter had also failed silently in this very file. Four old numbers each named **two** different
decisions — the old→new mapping at the top of this file splits each into two slugs (for example
`D-open-weight-roster` and `D-source-verification`, which shared one number, and `D-redeploy-survival`
and `D-critic-audition`, which shared another). The gate could not catch it, because it matched only the
`## ` prose headings and never the table rows, so "is this identifier defined twice?" was a question it
could not answer for the table-form half of the decisions.

**The decision.** Identify each decision by a **slug derived from its subject** (`D-source-verification`),
coined by the authoring PR. Two concurrent PRs cannot collide, because a slug is chosen from the
decision's own content, not from a global maximum — neither PR needs to read the other. Ordering moves
into file position, and the append point is fixed: immediately before `## Open items for a future round`.
Every existing decision was renamed once, on purpose, and the old→new mapping is published at the top of
this file so historical citations stay resolvable. The duplicate gate survives but is rebuilt to read
**both** definition forms, closing the blind spot above; a companion test
(`tests/test_citation_resolution.py`) asserts that every decision-shaped citation across `docs/`, `src/`,
`tests/`, `config/` and the reviewer prompts names a slug this file actually defines, so a citation to a
decision that does not exist now fails CI — a property the numeric scheme never had.

**What D-decision-gate got right, and what changed.** D-decision-gate's premise was that a decision
identifier "is echoed across `config/`, `src/`, `tests/` and docs — so a collision costs a repo-wide
rename," and it chose to refuse collisions at an offline, secret-free gate rather than pay that rename.
The premise was correct and the gate is kept: a duplicate identifier is still refused at the same
secret-free PR job, now in both surface forms. What changed is the rest. D-decision-gate kept
authoring-time *numeric* allocation and explicitly declined to rename after merge, judging the rename too
expensive to pay. This decision pays it once, deliberately, to make the collision impossible by
construction instead of merely caught after the fact — trading a single bounded rename for the removal of
a recurring, unbounded one. Its "a gap in the sequence is legal" caveat is now moot: slugs have no
sequence, so there are no gaps to leave.

**Invariants.** None of the tabulated safety invariants is in reach — author exclusion, the blind
`OrchestratorView`, fail-closed lenses, severity floors, controller termination and the untrusted-text
boundary all live in the pipeline core and are untouched. This is repository governance: it constrains
how a *document* is identified, not what enters any model's context. The rename itself is mechanical —
each decision's body says exactly what it said before, under its new name.
