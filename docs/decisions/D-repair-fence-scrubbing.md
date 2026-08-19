## D-repair-fence-scrubbing — reused critic inputs are scrubbed before a repair turn

The repair turn reuses the complete original critic prompt before appending the rejected field and
source excerpt. Scrubbing only those appended values is insufficient: an end marker embedded in the
question, rendered report or fetched-source entry would already have closed an earlier data fence,
and the repair call would preserve that escape unchanged.

The boundary is therefore enforced where those original blocks are constructed. `critic_user`
marker-scrubs the question and rendered report, and `fetched_sources_block` marker-scrubs each whole
entry before joining it inside the source fence. The latter covers every third-party field that can
enter an entry — page text, URL, title, mirror URL, registry metadata and fetch error — without
depending on each entry shape to remember the rule independently. The repair turn keeps its existing
scrubbing for the rejected value and source excerpt.

This is not a new exception to RA-010; it closes a gap between D-repair-turn-context's stated
boundary and its implementation. The regression inspects the complete repair prompt with markers in
the question, report and fetched source as well as the appended repair fields, so an assertion over
only the prompt tail cannot miss an earlier raw close marker again.
