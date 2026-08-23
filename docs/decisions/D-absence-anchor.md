## D-absence-anchor — a defect of absence anchors its `claim_span` to text that is present

**The problem.** `triage.validate_issue` requires `claim_span` to be a verbatim quote from the
paragraph the critic cited — the anchor that keeps a critic's findings to words the report already
contains, and one of the two things (with the RA-010 data fence) that stop critic text reaching a
writer as authority. The critic prompt stated that requirement in one flat line: *"`claim_span` must
be a short verbatim quote from that paragraph."*

That line is self-evident for every logic and evidence category, because those defects live *in*
text the report contains: the overstated wording, the uncited sentence, the claim a citation is
misdescribed as supporting. Quote the offending text and you are done.

The completeness categories are the opposite, in two distinct ways. Two are defects of *absence*:
`omitted_counterargument` is defined as "a material opposing view … is absent" and
`unexamined_presupposition` as adopting a presupposition "without stating or examining it" — both
material (a `major` floor). The third, `unclear_structure`, is neither absent nor material: it sits
at a `minor` floor and is a property of *arrangement* rather than of any one span. For all three,
"quote the offending text" has **no referent** — an absent view has no span to quote, and a
structural defect is a property of the passage as a whole rather than of a locatable phrase — so a
critic reaches for material that is not in the paragraph (the missing view, or a paraphrase of the
structural problem). It fails `_require_quote`, fails it again on both repair attempts (the hint
hands back the paragraph, which is the right text but not the missing answer the critic went looking
for), and fails the whole lens closed.

The failure is structural: it follows from the category shape, not from any one model's weakness, so
any critic asked only for "a verbatim quote" of an absent view has nothing valid to quote and fails
the same way. Each such failure surfaces as a `claim_span … is not a verbatim quote from the cited
paragraph` violation, and costs a controller re-critique out of the run's bounded `critique_attempts`
budget. That it is a gap in the contract rather than a weak model is why the fix is to the prompt and
not to the roster.

The in-call repair loop (`budgets.critic_repair_retries`) was the earlier response to the same
symptom; `tests/test_critique_repair.py` exercises it against exactly this violation, using
`omitted_counterargument` as its fixture. Repair stopped a *recoverable* slip from costing an
attempt. It cannot help a critic that does not know what the anchor is for, which is why the failure
survived it.

**The decision.** `prompts._CATEGORY_ANCHOR` gives every category an explicit statement of what
`claim_span` anchors to, rendered into the critic prompt for that lens's in-scope categories only —
the same closed scope the meanings table already follows. The prompt body states the general rule
once: *where the defect is something the report does NOT say, `claim_span` still quotes what it DOES
say — the passage the gap bites into.* Each of the three names the present text it anchors to: the
two absence categories point at the claim the missing element bears on, and `unclear_structure`
points at the opening words of the passage whose arrangement is the defect. The two whose missing
element is *content* redirect that content to a field which is not span-validated (`instruction` for
the omitted view, `rationale` for the presupposition), so the advice is not "drop the issue" by
implication.

`related_span` has carried per-category guidance in this prompt since it was written, for exactly
this reason. `claim_span` never did.

**Why this is not a weakening.** `triage.validate_issue`, `_require_quote` and `_normalize` are
byte-identical. The prompt tells a critic *where to find* a valid span; it does not enlarge the set
of spans that validate, and a span that is not really in the paragraph still fails the lens closed.
The change is strictly narrowing on the model side: each category now permits *less* than "some
quote from that paragraph". The alternative fix — relaxing `require_verbatim_spans` for the
completeness lens — was rejected, because span-anchoring is one of the six CI-audited invariants and
the completeness lens is precisely where an unanchored span would be most tempting to invent.

**What it does not fix.** A critic that raises a genuine omission and *still* cannot quote the
paragraph is unchanged: it fails the lens, as it should. This raises the ceiling on how often the
lens completes; it does not guarantee completion. Nor does it touch the decision table — a
completeness lens that completes instead of failing changes which rule the controller reaches only
by supplying the counts rule 2 would otherwise have discarded.

**Audition cache.** `audition.prompt_hash()` covers `prompts.critic_user`, so this change correctly
invalidates any cached audition and every critic reads `stale` until `ra audition` is re-run. That
is the intended behavior — the hash exists because editing a lens prompt changes what was measured.
With the shipped `audition.enforce: false` a stale cache warns rather than failing startup.

**Invariants.** Untouched. Untrusted text still never reaches a generator as instruction: spans stay
verbatim-anchored and validated, defects still cross to the writer only as fenced data (RA-010/D-evidence-bearing-fields),
the fail-closed lens contract (RB-007) is unchanged, severity floors are not involved, and the
controller's inputs and rule ordering are not touched.
