## D-report-template — every report follows a conclusion-first frame, whoever writes it

**The problem.** Nothing pinned a report's shape. `WRITER_SYSTEM` asked for "clear section
headings" and the first-draft prompt ended at "Return the report in Markdown", so each model in
the writer pool imposed its own habits: the overall form of the output varied with which alias
happened to draft, and the reports read as layered analysis a reader had to excavate for the
answer. For a system whose point is to hand someone with a preconceived notion a clear,
defensible answer, burying the conclusion is a product defect, not a style preference.

**Decision.** A fixed frame with a free middle, stated once as `prompts.REPORT_SKELETON`:
`## Conclusion` first — a direct two-to-four-sentence cited answer that also names the strongest
opposing view — then `## Key findings`, then `## The strongest counterargument`, then topical
sections of the writer's choosing, then `## Sources`, byte-exact and last. Prompt-only: no new
mechanical gate, no new critic category. A skeleton violation is already the
`unclear_structure` lens's business.

**The frame lives in the writer's system prompt, not the first-draft prompt.** `writer_system()`
is the one composition point every writer call shares — first draft, every revision, the polish
pass. A frame stated only at the first draft decays: the revision prompts are template-unaware,
and a polish pass is otherwise free to restructure. And a seeded run never sees the first-draft
prompt at all — round 1 *is* the seed — so the system prompt is what steers a seeded run toward
the frame at its first revision. Seeded round-1 artifacts are deliberately not restructured at
intake.

**The counterargument is prominent by design, and the rationale is epistemic, not
persuasion-maximizing.** The intent of this system is to help people change their mind: a reader
arrives with a notion and should meet the strongest version of the other side early — inside the
conclusion itself, and in a dedicated section ahead of the topical detail — never as an
afterthought at the tail. The persuasion literature was checked before leaning on it, and it
carries less than the folklore says: refutational two-sided messages beat one-sided, which beat
non-refutational two-sided ([O'Keefe 1999](https://dokeefe.net/pub/OKeefe99AICA.pdf), k=107,
r≈.08 vs r≈−.05), but the classic claim that two-sidedness works best on *opposed* audiences did
not survive that meta-analysis, and the effects are small with prediction intervals spanning
zero. The modern result ([Xu & Petty 2022](https://doi.org/10.1177/0146167220988371),
[2024](https://doi.org/10.1177/01461672221128113)) is narrower and more useful: for entrenched
attitudes, a two-sided message increases
*openness*, mediated by the reader feeling their view was respectfully and strongly stated. So
the frame is justified by what it forces the pipeline to do — confront the strongest opposing
evidence where critics can see whether it was answered — with any persuasive benefit treated as
upside, not as the load-bearing claim.

**Two hazards the skeleton text guards against by name.** First, the strawman: presenting the
opposing case weakly nulls the openness effect entirely ([Xu & Petty 2022, study
2](https://doi.org/10.1177/0146167220988371)), and an LLM
asked for "the strongest counterargument" will happily manufacture a weak one — so the skeleton
requires the form proponents would accept, and prefers an honest "reasonable objections exist,
chiefly X" over a manufactured steelman. Second, the unanswered objection: raising a
counterargument without engaging it is *worse than one-sided* in O'Keefe's meta-analysis (the
non-refutational case), so the
skeleton forbids raising an objection and leaving it unanswered, and tells topical sections to
answer objections where they arise rather than deferring them all to the counterargument
section (a quarantined block is the weakest-supported arrangement; interweaving is the
dependable one).

**What this touches and what it does not.** `## Sources` stays byte-exact because
`fetch._SOURCES_HEADING` matches only a heading whose text is the word "sources" and
`triage._locate_url` assumes the section is last. No top-level `#` title, because
`export_markdown` already emits `# {question}` above the body. The `omitted_counterargument`
critic category is unchanged and should simply fire less: its target section is now structural.
Rendering the frame as a layered mobile reading experience is a separate decision.
