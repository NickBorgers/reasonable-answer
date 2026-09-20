## D-ops-revision — a revision returns operations on labelled paragraphs, and the splice enforces what the patch prompt only asked for

**The finding.** Under `revision.mode: patch` the writer returns the whole report, and every rule that
protects the text it was not asked to touch is a sentence in the prompt: return every other paragraph
byte-identical (`WRITER_PATCH_CLOSE`), keep every `[n]` marker on a kept claim, delete a Sources entry
only when nothing cites it, never renumber (`WRITER_CITATION_REVISION`). The census and scope
measurements (D-writer-citation-continuity, D-scoped-revision) detect a violation after the fact, and
D-census-gated-repair spends one bounded repair turn when they fire. Nothing makes a violation
impossible. A production patch revision came back with every body marker gone and most of its changed
paragraphs outside any fix task; the census recorded it, the repair turn did not restore it, and the
next round's writer discharged the resulting uncited-claim findings by deleting the claims and their
sources. That observation comes from the operator's private audit trail and is recorded here without
identifiers or rates. As in D-census-gated-repair, it is the **motivation and not the warrant** (QP9).

**The warrant is the mechanism, and it is checkable in this repository.** The paragraphs of a report
already have stable labels — `[S<n>.P<m>]`, the loci critics cite (RB-007) and the rendering critics read
(`report.render_with_loci`). A revision expressed as *operations on those labels*, applied by code, makes
the patch prompt's rules properties of the splice instead of requests to the model: a paragraph no
operation names is byte-identical because nothing touched it; a heading cannot be renumbered or dropped
because no label addresses one; a Sources change that would leave a body marker citing nothing is
refused because the splice checks the citation census before keeping it. `tests/test_ops.py` pins each
of those properties, and `tests/test_graph.py` pins the wiring end to end on the fake proxy.

**Decision.** A third `revision.mode`, `ops`. The writer is shown the draft with its labels and the fix
tasks numbered `T1`, `T2`, …, and returns operations in a Markdown-native line protocol:

```
@@ replace S2.P3 tasks=T1,T4
<the complete new Markdown text of that paragraph>
@@ end

@@ delete S3.P1 tasks=T2
@@ end

@@ insert-after S2.P3 tasks=T1
<one new paragraph, placed after S2.P3>
@@ end
```

`ops.parse_ops` reads the reply; `ops.splice` builds the next report from the previous artifact and
the operations; `ops.revise` is the two together. Critics, triage, the controller, the artifact hash,
the store and every renderer keep seeing plain Markdown: the operations never leave `_generate`.
`patch` stays the code default; the production roster is unchanged by this decision, and a later
decision flips it after a live A/B on the same build (`revision_mode` on every `generate` event is the
bucketing field).

**Why a line protocol and not JSON.** The payload is prose — whole paragraphs of Markdown with quotes,
brackets and line breaks — and prose inside a JSON string literal is exactly where structured output
from a general-purpose model breaks: one dropped closing brace loses the entire reply, and every
newline and quote has to be escaped by a model that is writing paragraphs, not data. A line protocol
degrades one block at a time: a block missing its `@@ end` is closed by the next header or the end of
the reply and counted, a block with an unknown locus is refused and counted, and the operations that
were fine still apply. This is reasoning about failure shape, not a measured rate; the counts on the
`generate` event are where a rate will come from.

**The block model (`report.Block`, `report.blocks`, `report.canonical`).** `report.parse` kept heading
titles only, which is all a locus needs but not enough to rebuild a document, so the splice needs the
raw heading line. `blocks()` returns every heading and paragraph in order, numbered exactly as `parse`
numbers them, and `parse` is now a projection of it — the two cannot disagree about which text
`S<n>.P<m>` names. `canonical()` is the form the splice emits: blocks joined by one blank line,
headings on their own line, no trailing newline, text inside a paragraph untouched. The identity
contract is `splice(x, []).text == canonical(x)`, and `revision_scope` reports nothing changed for it
(its key already collapses whitespace).

**The splice, in order.**

1. *Normalisation of each replacement.* A leading `[S<n>.P<m>]` label is stripped and counted
   (`ops_label_echo`) — writers copy the label they were shown into the new text. A first line that is a
   heading whose title equals the target section's title is stripped and counted (`ops_heading_echo`).
   Any heading that remains in the new text refuses the operation (`ops_refused_heading`): the labels
   critics read are numbered by heading, and new text that adds one would renumber every paragraph after
   it. A `replace` or `insert-after` whose text is empty is refused (`ops_refused_empty`), never read as
   a delete.
2. *Locus checks.* A label the draft does not have, or a `P0` (the heading of section `n`), is refused
   (`ops_refused_locus`). A second `replace`/`delete` on a locus already replaced or deleted is refused
   and the first wins (`ops_refused_duplicate`). Several `insert-after` on one locus are all kept, in the
   order written; an `insert-after` beside a `delete` on the same locus is allowed.
3. *Application.* Every accepted operation outside the `## Sources` section, plus every `insert-after`
   inside it, is applied.
4. *The dangling-marker guard.* The citation census of that candidate gives a baseline count of body
   markers that cite no entry. Each Sources `replace`/`delete` is then tried in the order written and
   kept only if that count does not rise; otherwise it is refused (`ops_refused_dangling`). This is
   "delete an entry only when nothing cites it", generalised to a Sources list that sits under one
   label (where a *replace* of the whole list is how an entry is removed), and it uses only the public
   census so `[n]`, `n.` and `n)` entries all count. Body operations run first, so a reply that removes
   the last marker citing an entry may delete that entry in the same reply.
5. *Emission.* The blocks joined as `canonical` joins them. `_generate` strips writer output already, so
   no stored artifact carries a trailing newline in either mode.

**A malformed reply is a failed writer attempt.** When no operation parses, or every operation is
refused, there is no draft to ship and nothing to repair. `_generate` records a `generate_failed` event
with `failure_class="malformed_ops"` (D-writer-failure-class) and the next attempt goes to the next pool
member, exactly as an empty reply does (`empty_report`). D-claim-scoped-patch's "a rejected draft costs
one of `writer_attempts`" is the only option here, and it is the right one: the next pool member is a
different model family, which is the remedy for a formatting failure, and no new budget is introduced
(QP7). A reply with at least one applied operation ships, with its refusals counted; the fix tasks a
refused operation was serving come back from the critics next round.

**The ops close carries the patch licence.** `WRITER_OPS_CLOSE` states the scope rule in the patch
close's words, the claim-unit rule word for word (`WRITER_CLAIM_UNIT`, now shared by both closes so
D-claim-scoped-patch has one text), and the format. It does not repeat "byte-identical", "never
renumber", "no placeholders" or "keep every heading": each is true by construction or refused
mechanically. It adds three sentences the experiment showed were needed: a list shown under one label is
one paragraph, so one item is changed by replacing the list; each Sources entry under its own label is a
paragraph of that section, and where the whole list sits under one label the whole list is replaced; an
entry a paragraph you are not changing still cites cannot be removed. `WRITER_RESOLUTION_STANDARD`,
`WRITER_CITATION_REVISION`, the dispute addendum and the date line are unchanged and present in all
three modes. `WRITER_PATCH_CLOSE` and `WRITER_REWRITE_CLOSE` are byte-identical to before, pinned by
hash in `tests/test_writer_template.py`, so the existing A/B arms have not moved.

**Gates and the repair turn under ops.** `_scope_fields` measures the spliced text against the previous
artifact with the same fields, so patch and ops are A/B-comparable on `out_of_scope`, `restated`,
`additive_only` and `defect_loci_untouched`. Gate 2 (D-census-gated-repair) fires under `patch` *or*
`ops`: the splice cannot stop a writer operating on many paragraphs no task named, and that is exactly
what gate 2 measures. Gate 1 is unchanged. Under the ops licence — `mode: ops`, a revision, not a polish
pass, not a rule-13 rewrite — the repair turn asks for **operations** on the labelled previous draft,
never for a whole document (one output contract per mode), and its reply is spliced into the previous
artifact exactly as the drafting reply was; a repair reply with no applicable operation is an
unresolved attempt that keeps the current draft, as a failed repair call already did. The first draft,
a polish pass and a rule-13 rewrite are whole documents whatever the mode.

**Invariants touched: none.** The handoff carries what the patch prompt already carried plus the
`[S<n>.P<m>]` labels the critics already read and a task ordinal per fix task; every fenced block is
`_neutralized` (D-fence-scrub-all-directions); no critique prose, lens name or critic identity is
added, and `tests/test_isolation.py` asserts the ops prompt differs from the patch prompt only in the
labels, the section lines, the task ids and the close. Critics receive the spliced Markdown in the
unchanged rendering, in fresh blind contexts, under the unchanged author-exclusion rule; the artifact
hash is taken over the spliced text, so idempotent replay is unaffected. The resume fingerprint
(`graph._run_fingerprint`) hashes `roster` and `budgets`, not `revision`, so a run resumed after a mode
change continues under the new mode from its next generation — as a `patch`/`rewrite` change already
did; recorded here, not changed. Fail-closed lenses, severity floors and termination are untouched.

**Event fields on `generate`, integers, present exactly when ops mode ran** (no text, no locus, no task
id — RA-016), and `revision_mode` (`patch` | `rewrite` | `ops`) on every `generate` event:

| field | meaning |
|---|---|
| `ops_total` | operation blocks parsed |
| `ops_replace`, `ops_delete`, `ops_insert` | parsed, by kind |
| `ops_applied` | operations that changed the report |
| `ops_refused_locus` | label not in the draft, or a heading (`P0`) |
| `ops_refused_duplicate` | second replace/delete on one locus (first wins) |
| `ops_refused_heading` | new text would add a heading |
| `ops_refused_empty` | replace/insert with no text |
| `ops_refused_dangling` | Sources change that would orphan a body marker |
| `ops_label_echo`, `ops_heading_echo` | stripped and counted, not refused |
| `ops_without_task` | operations naming no task id |
| `ops_unterminated`, `ops_stray_lines`, `ops_bad_headers`, `ops_fenced` | protocol tolerance, counted |

`generate_failed` gains no field; its `failure_class` (`malformed_ops`) is the whole record.

**Deliberately not done.**

- **The roster flip.** Production stays on `patch`; a separate decision flips it after a live A/B on one
  build, read from `generate` events bucketed by `revision_mode` (docs/run-provenance.md).
- **Ops for a polish pass or a rule-13 rewrite.** Both are whole-document generations by definition.
- **Refusing a *body* operation that introduces a dangling marker.** A "cite `[13]`" replace paired
  with a Sources insert in the other order is legitimate; the guard runs after body operations for that
  reason, and a body marker with no entry is D-bibliography-integrity's finding, unchanged.
- **A per-task discharge check from `tasks=`.** The task ids are for the writer's own bookkeeping and
  for reading a reply; whether a task was discharged is still the critics' verdict.
- **A system-prompt addendum for ops mode.** `WRITER_SYSTEM` is unchanged so first drafts and polish are
  unaffected; if `malformed_ops` proves common, a one-line ops-mode addendum is the lever.
- **Demoting a new sub-heading inside replacement text.** It is refused wholesale rather than turned into
  bold text; a draft that has lost its headings is a rule-13 rewrite's job, not an operation's.
- **Persisting the raw operations reply** beside the report. The counts above are the audit record.

Cites D-scoped-revision (the licence this enforces), D-claim-scoped-patch (the claim-unit rule, now
shared text; its "no enforcing tier for placeholders or headings" is closed under ops, where neither
can exist), D-writer-citation-continuity (the census the dangling-marker guard reads, and "delete an
entry only when nothing cites it", now mechanical), D-census-gated-repair (gate 2 under ops; the repair
turn returns operations), D-writer-failure-class (`malformed_ops`), and D-fence-scrub-all-directions
(every fenced block in the ops prompt and its repair turn is scrubbed).
