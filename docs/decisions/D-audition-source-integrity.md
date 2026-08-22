## D-audition-source-integrity — source claims in the audition corpus are fetched claims, not plausible prose

**The observation.** A source audit of the remaining audition corpus, excluding the two controls
already identified by #152 and the five corrections recorded in D-control-defect-sweep, found four
more material defects:

| fixture | category the corpus accidentally admitted | what was wrong |
|---|---|---|
| `control-base-dust-bowl-01` | `misrepresented_source` | It said Long & Siu showed migrants "kept moving" and that the cited studies measured places rather than people. [The paper](https://doi.org/10.1017/S0022050718000591) links individuals only at the 1930 and 1940 censuses, reports their 1940 labour-market outcomes, and cannot observe repeated moves between those endpoints. |
| `one-sided-sourcing-01` | `fabricated_citation` | Its organization and journal slots generated invented publishers and five non-resolving titles, although its manifest promised individually real and accurate citations. A critic could earn same-lens credit for fabrication instead of detecting source selection. |
| `control-base-congestion-pricing-01` | `misrepresented_source` | It promoted the [initial January result](https://www.mta.info/document/165006), about 8 percent, to a first-quarter result and cited a standalone MTA "First Quarter Report" that does not exist under that title. The [April committee book](https://www.mta.info/document/170871) charts January-March and reports March at 13 percent below its historical average. |
| `control-base-remote-work-01` | `overstated_claim` / `misrepresented_source` | It declared task composition the cause of most cross-study disagreement and selection the cause of much of the rest. [Emanuel & Harrington](https://www.newyorkfed.org/research/staff_reports/sr1061) find both negative selection and a negative treatment effect in call-centre work, then explicitly leave incentives, management, site selection and context as unresolved explanations for divergence from other studies. |

The first, third and fourth are defects in controls, so competent critics were graded as noisy for
reporting them. The second is a planted fixture, but `grade` gives relaxed same-lens credit to a
material issue at the locus and the `anywhere` flag deliberately widens that locus. Fabrication was
therefore not harmless background texture: it was an easier route to the evidence sensitivity
credit intended for `one_sided_sourcing`.

**Decision.** Source-dependent audition prose is held to fetched text. Except where
`fabricated_citation` is the declared plant, a cited publication must resolve at a stable locator
and the attributed claim must remain within the publication's design, population, timeframe and
result. Plausibility, a familiar author name and a mechanically numbered bibliography are not
substitutes for resolving and reading the source. This sharpens D-control-soundness's existing
"described only as far as the author can stand behind" contract; it does not add a networked test
or pretend offline code can establish semantic support.

The Dust Bowl base now reports Long & Siu's individual linkage, its observed 1940 outcomes and its
two-endpoint limitation. The congestion-pricing base names the actual MTA committee publication
and preserves March's 13 percent result as a March comparison rather than a quarter-wide estimate.
The remote-work base reports selection and treatment separately and leaves the cross-study causes
unresolved as the paper does. Each corrected source receives a DOI or publisher locator.

`one-sided-sourcing-01` now cites five real EdChoice publications with stable publisher pages,
including its [research synthesis](https://www.edchoice.org/research/123s-of-school-choice-2025/)
and [fiscal model](https://www.edchoice.org/research/fiscal-effects-of-school-choice/). The
body accurately distinguishes a synthesis, a fiscal model, a public-school-effects study, a parent
survey and a capacity study, including their mixed or limiting findings. All five still come from
the same advocacy publisher and no independent source corroborates the resulting recommendation,
so the declared defect remains obvious and is now the only evidence defect. An offline regression
pins the mechanically knowable part: no randomized slots, no unresolved template tokens and five
EdChoice locators. Whether those publications support the prose remains a review obligation.

**The mutants move with their bases.** The Dust Bowl correction is mirrored in
`contradicted-claim-01` and `incomplete-answer-01`; the remote correction is mirrored in
`fabricated-citation-01`; and the MTA correction is mirrored in `omitted-counterargument-01`.
Those edits do not touch any declared planted locus. Correcting only the controls would create a
second feature separating the sensitivity and noise sets and would leave the planted reports with
the same accidental source defects.

**Consequence for the cache.** The artifact and manifest edits change `corpus_hash`, invalidating
every cached critic verdict by design. Those verdicts describe a corpus that handed out genuine
findings, so they must not survive. Re-running the live audition is an operator action after merge;
the offline suite uses no model proxy and cannot manufacture replacement measurements.

**Deliberately not done.** No category, severity floor, matching rule, threshold or grader changes.
The relaxed same-lens metric remains useful for wording variance when a fixture contains only the
defect it declares. No source packets are added: D-audition-source-mode still records that separate
work, and this decision repairs the source-less corpus rather than widening what a live critic sees.
