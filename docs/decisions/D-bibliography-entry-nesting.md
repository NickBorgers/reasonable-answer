## D-bibliography-entry-nesting — indentation says which bibliography lines are references

**The finding.** `fetch.source_entries` split the `## Sources` section on any line opening with a
list marker at up to three spaces of indentation. In an annotated bibliography — the citation, then
an indented sub-bullet of commentary — that marked sub-bullet was read as a second entry. When the
annotation carried no URL, it landed in `not_addressable` and gave the real citation a phantom
unaddressable twin.

`source_entries` was conservative in the direction that understates coverage, which is the safe
direction and is why this was a reporting defect rather than an overstatement. But a 2x error is
misleading rather than cautious, and it masked the metric's real job: now that every addressable
citation is fetched, a non-zero `not_independently_checked` should mean *the writer cited something
with no URL* — a genuine signal about the draft — and instead it was dominated by formatting noise.

**The decision.** Indentation, which the parser already had and discarded, decides nesting. Entries
sit at one depth per section; a marker line deeper than that depth is an annotation of the reference
above it and folds into that entry exactly as an unmarked continuation line always has.

That depth is the shallowest marker that is **not a grouping heading** — a label like
`- Peer-reviewed:` that introduces references rather than being one, recognised by the colon it ends
with and by carrying no address of its own.

An earlier form of this decision anchored the depth on the shallowest marker *carrying a URL*
instead. That looked equivalent and is not, and the difference is the whole of this amendment: in an
annotated bibliography the URL frequently sits in the **annotation** rather than in the reference, so
the anchor landed one level too deep and every reference above it became "shallower". Paired with a
rule that dropped a shallower marker whenever a deeper URL-bearing line followed it, a reference like

```
- Smith, J. (2019). Title. Publisher.
  - Available at: https://example.org/a
```

was discarded and its annotation became the entry. The count could still come out right by
coincidence — three markers in, three entries out — while naming the wrong things, which is how it
survived review the first time. A citation vanishing from the denominator reports *more* of the
bibliography verified than was, and understating coverage is the only direction this heuristic is
permitted to be wrong in.

Recognising the heading by its own text rather than by what follows it also removes the lookahead
entirely, and with it the end-of-section branch that had no way to be exercised. A section whose
markers are all headings falls back to the shallowest marker depth, so a bibliography of nothing but
labels is still represented

The marker pattern's `\s{0,3}` bound is replaced by a captured indent of any width, with tabs
expanded, because a bound on depth cannot express a comparison between depths. Nothing routes on
this pattern: it is only ever used to count.

**Still a heuristic.** This is a guess at model-written markdown and remains one — the observed
count is reported as an observation and never as a completeness claim. What changes is that the
guess is now pinned against explicit fixtures for flat `[n]`, `-`/`*`/`+`, `1.`/`1)`,
wrapped continuations, the annotated two-line form, a tab-indented annotation, grouped references,
a mixed grouped bibliography with an unaddressable reference, and flat and grouped bibliographies
with no URLs each have a fixture in `tests/test_source_coverage.py`, because this heuristic is
load-bearing for a published number.

**Invariants.** None of the six is in reach. The count feeds `fetch.coverage`, which is observation
only: no controller rule reads it, no `OrchestratorView` field carries it, and it mints no defect,
so termination and the blind orchestrator are untouched. Author exclusion, fail-closed lens
validation and severity floors are not in this path. The untrusted-text boundary is unchanged in
kind and degree — the bibliography was already untrusted model output being counted, and it still
reaches no generator as instruction. `extract_source_urls`, which decides what is actually fetched,
is not touched at all; this changes the denominator a run reports, never the egress it performs.
