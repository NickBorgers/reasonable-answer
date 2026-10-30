## D-verdict-not-restatement — a claim restated with "no source cited" is not an answer to "which claims are backed?"

**The finding.** Run `run-c71835ca7355` seeded a petition page and asked "Which allegations in this
article are backed up by facts?" It ended `accepted`, every lens cleared by two models, with
`cited: 0` in the source coverage. The final report restated each allegation as the petition's and
added "No independent source or court record confirming these details is cited in the petition or
identified in this report". Three of its four writer turns made no search. The question asked for a
verdict on each claim, and the report gave none.

**Why every lens cleared.** A report that asserts nothing has nothing for the logic or evidence
lens to attack. The completeness lens should have caught it, but its brief names "stating the
limitation explicitly" as an acceptable resolution and forbids demanding a specific external
document as the only fix. A restatement with a disclaimer satisfies both.

**The decision.** One more reading of `incomplete_answer` (no new category, floor unchanged), added
to the completeness brief only when the run's writers can search. Where the question asks whether,
or which of, the claims in a document are true or supported, a claim the report restates with no
verdict is `incomplete_answer`, and the instruction asks the writer to search for it and state what
the record shows.

**Why conditional on retrieval.** Without a search tool the fix does not exist. The finding would
loop to the round cap. The trigger therefore lives in `prompts.VERDICT_TRIGGER`, appended when
`Runtime.search_enabled`, and not in `taxonomy.LENS_BRIEF`. With it off, the prompt and the
audition hash are unchanged.

**Narrowings.** A claim whose paragraph states what was searched and that nothing was found is
answered. A claim no search could settle needs no verdict. For this finding only, the instruction
may require new source material; the generic rule against that stays in force elsewhere.

**What this does not establish.** The tests verify that the trigger reaches the prompt exactly when
search is on, that it touches no other lens, and that no category or floor moves. They do not
measure detection or false-positive rates. Those are audition questions, and the first test is a
rerun of the petition seed.

**Not done.** Extracting the seed's claims into a checklist and requiring one search attempt per
claim is a larger change and is deliberately out of scope here.
