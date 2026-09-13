"""Observable-category taxonomy, lenses, and mechanical severity floors.

See docs/convergence.md. Severity floors are mechanical: a critic may escalate a
severity but triage clamps it *up* to the category floor — never down.
"""

from __future__ import annotations

from enum import Enum


class Lens(str, Enum):
    LOGIC = "logic"
    EVIDENCE = "evidence"
    COMPLETENESS = "completeness"


LENSES: tuple[Lens, ...] = (Lens.LOGIC, Lens.EVIDENCE, Lens.COMPLETENESS)


class Severity(str, Enum):
    BLOCKING = "blocking"
    MAJOR = "major"
    MINOR = "minor"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.MINOR: 0,
    Severity.MAJOR: 1,
    Severity.BLOCKING: 2,
}


class Category(str, Enum):
    # evidence
    FABRICATED_CITATION = "fabricated_citation"
    MISREPRESENTED_SOURCE = "misrepresented_source"
    UNCITED_CLAIM = "uncited_claim"
    ONE_SIDED_SOURCING = "one_sided_sourcing"
    # logic
    CONTRADICTED_CLAIM = "contradicted_claim"
    INVALID_INFERENCE = "invalid_inference"
    OVERSTATED_CLAIM = "overstated_claim"
    CONCEPTUAL_CONFLATION = "conceptual_conflation"
    LOADED_LANGUAGE = "loaded_language"
    # completeness
    INCOMPLETE_ANSWER = "incomplete_answer"
    OMITTED_COUNTERARGUMENT = "omitted_counterargument"
    UNCLEAR_STRUCTURE = "unclear_structure"
    UNEXAMINED_PRESUPPOSITION = "unexamined_presupposition"
    # any lens
    STYLISTIC = "stylistic"


#: category -> mechanical severity floor (triage clamps up to this)
SEVERITY_FLOOR: dict[Category, Severity] = {
    Category.FABRICATED_CITATION: Severity.BLOCKING,
    Category.MISREPRESENTED_SOURCE: Severity.MAJOR,
    Category.UNCITED_CLAIM: Severity.MAJOR,
    Category.ONE_SIDED_SOURCING: Severity.MAJOR,
    Category.CONTRADICTED_CLAIM: Severity.BLOCKING,
    Category.INVALID_INFERENCE: Severity.MAJOR,
    Category.OVERSTATED_CLAIM: Severity.MAJOR,
    # `invalid_inference`'s sibling and floored with it (D-conceptual-conflation): the
    # substitution is what carries the inference, so keeping the two concepts apart is
    # what the conclusion would have to survive. Not blocking — unlike a contradiction,
    # nothing in the report is thereby shown false, and the fix (draw the distinction,
    # or restrict the claim to the concept the evidence covers) is always in-report.
    Category.CONCEPTUAL_CONFLATION: Severity.MAJOR,
    # Deliberately minor (D-social-bias): the most judgment-laden bias category; a material
    # floor would let a noisy critic force revisions round after round. A critic
    # that finds pervasive, verdict-carrying framing may propose `major` and the
    # clamp keeps it — escalation is allowed, only downgrades are not (RC-005).
    Category.LOADED_LANGUAGE: Severity.MINOR,
    Category.INCOMPLETE_ANSWER: Severity.MAJOR,
    Category.OMITTED_COUNTERARGUMENT: Severity.MAJOR,
    Category.UNCLEAR_STRUCTURE: Severity.MINOR,
    Category.UNEXAMINED_PRESUPPOSITION: Severity.MAJOR,
    Category.STYLISTIC: Severity.MINOR,
}

#: lens -> the categories that lens is allowed to raise. `stylistic` is allowed
#: everywhere but is ignored for convergence.
LENS_CATEGORIES: dict[Lens, tuple[Category, ...]] = {
    Lens.LOGIC: (
        Category.CONTRADICTED_CLAIM,
        Category.INVALID_INFERENCE,
        Category.OVERSTATED_CLAIM,
        Category.CONCEPTUAL_CONFLATION,
        Category.LOADED_LANGUAGE,
        Category.STYLISTIC,
    ),
    Lens.EVIDENCE: (
        Category.FABRICATED_CITATION,
        Category.MISREPRESENTED_SOURCE,
        Category.UNCITED_CLAIM,
        Category.ONE_SIDED_SOURCING,
        Category.STYLISTIC,
    ),
    Lens.COMPLETENESS: (
        Category.INCOMPLETE_ANSWER,
        Category.OMITTED_COUNTERARGUMENT,
        Category.UNCLEAR_STRUCTURE,
        Category.UNEXAMINED_PRESUPPOSITION,
        Category.STYLISTIC,
    ),
}

#: Categories that count toward a lens's clean record. `stylistic` never blocks,
#: so a lens is clean when it raises no category at or above the material floor.
MATERIAL_FLOOR: Severity = Severity.MAJOR


def is_material(severity: Severity) -> bool:
    return SEVERITY_RANK[severity] >= SEVERITY_RANK[MATERIAL_FLOOR]


def clamp_to_floor(category: Category, proposed: Severity) -> Severity:
    """Escalate `proposed` up to the category floor. Critics can only escalate."""
    floor = SEVERITY_FLOOR[category]
    return proposed if SEVERITY_RANK[proposed] > SEVERITY_RANK[floor] else floor


def counts_for_convergence(category: Category, proposed: Severity) -> bool:
    """Whether triage would count this finding as a material issue.

    The order of the two rules is the whole content of this function. `stylistic` is
    out **unconditionally**, before severity is read: escalation is doctrine (RC-005)
    and `validate_issue` checks category scope, locus and spans but never severity, so
    a critic may legally file a `stylistic` issue at `major`. Testing severity first
    would let that nitpick read as material for a category every other consumer
    ignores. Everything else counts once the mechanical floor has been applied.

    One definition, because two callers have to agree: triage, which decides what a run
    converges on, and the audition grader, which measures what a critic would contribute
    to a run and misreports it whenever the two drift (D-audition-stylistic-parity).
    """
    if category is Category.STYLISTIC:
        return False
    return is_material(clamp_to_floor(category, proposed))


LENS_BRIEF: dict[Lens, str] = {
    Lens.LOGIC: (
        "Assess only the internal logic of the report: whether claims contradict "
        "each other or a source the report itself cites, whether conclusions follow "
        "from their stated premises, whether a stated derivation actually yields the "
        "number it reports, whether any claim is stated more strongly "
        "than the support offered for it, whether the argument turns on treating two "
        "materially distinct things as interchangeable, and whether wording carries an "
        "evaluative verdict the stated support does not establish.\n\n"
        "Two of those need their triggers stated, because both have a wide "
        "false-positive surface.\n"
        "- Distinctness: a formal rule, the mechanism that implements it and the "
        "outcome observed downstream are three different propositions; so are the "
        "units actually measured and the wider population a claim is made about; so "
        "are groups that reach the same outcome by different mechanisms. Raise "
        "`conceptual_conflation` only when the report substitutes one for another AND "
        "the substitution is what carries an inference or a conclusion. It is NOT a "
        "different word for the same thing, NOT the absence of a subgroup breakdown, "
        "and NOT a distinction that makes no difference here because one mechanism or "
        "one body of evidence genuinely covers both — nor is an aggregation the report "
        "draws explicitly and defends.\n"
        "- Empirical anchoring: where a claim turns on magnitude, prevalence, timing "
        "or change, a thematic assertion offered in place of a concrete figure or a "
        "primary source that states it is support weaker than the claim — raise it as "
        "`overstated_claim`. A claim about kind, mechanism or character needs no "
        "number, and neither does one already qualified to the cases its support "
        "covers. Never demand a specific dataset or document as the only fix: "
        "qualifying the claim to what the support establishes is always acceptable.\n\n"
        "Three further rules say where to look (D-decisive-quantities). Each names a "
        "defect that sits in plain sight in the text you are given and is routinely "
        "walked past.\n"
        "- Arithmetic and units: where the report states a derivation — a product, a "
        "ratio, a share of a total, a unit conversion, a range computed from stated "
        "inputs — reproduce it from the inputs the report itself states. A result that "
        "does not follow from those inputs, a unit that changes between premise and "
        "result, or a scenario label that does not match the range attached to it, is "
        "`invalid_inference`; put the recomputed value in the rationale. Two "
        "narrowings: a figure stated to fewer significant figures than its inputs is not "
        "a defect, and an input the report never states is not a defect of the "
        "derivation — a claim "
        "resting on an unstated input is `overstated_claim` under the rule above.\n"
        "- Distant contradictions are expected: a claim contradicted by the "
        "conclusion, by a key finding, or by a figure stated elsewhere in the report is "
        "`contradicted_claim` however many sections apart the two passages sit — put "
        "the other passage in `related_span`. Two figures for the same quantity that "
        "differ by more than their stated precision are a contradiction wherever in "
        "the report they appear.\n"
        "- Absence of evidence is not evidence of absence: \"no evidence of X at level "
        "L\", \"insufficient data to determine\" and \"not established\" do not mean "
        "\"no X at L\", and a conclusion that carries the second while its support says "
        "only the first is `invalid_inference`. Do not over-fire: a report that states "
        "the evidence is insufficient and concludes accordingly has read it correctly, "
        "and that is not this defect."
    ),
    Lens.EVIDENCE: (
        "Assess only the sourcing of the report: whether material claims carry a "
        "citation, whether any citation is implausible or cannot be what it claims "
        "to be on its face, whether a cited source is described as supporting "
        "something it plainly would not support, and whether, on a contested "
        "question, the sourcing is drawn so narrowly from one outlet, organization "
        "or aligned cluster that the report inherits a single viewpoint.\n\n"
        "Support is not word-matching. Of every cited claim whose page you have in "
        "hand, ask two questions (D-source-fidelity-direction-and-scope):\n"
        "- Direction: does the page assert this, in this direction? A page can contain "
        "the report's own words and still be evidence against the proposition they are "
        "used for — when the finding, conclusion or headline result the page reports "
        "cuts the other way; when the page records that data were insufficient to "
        "determine something and the report renders that as a determination; or when "
        "the report takes the page's caveat for its conclusion.\n"
        "- Scope: is the page's scope the claim's scope? A finding stays attached to "
        "the population, product, category, system boundary, dose or wavelength band, "
        "and period it was measured on. A real source about a different one of those, "
        "restated as if it were about the question's, does not support the claim.\n"
        "Both are `misrepresented_source`: the defect is what the page is evidence "
        "for, not how the report reasons from it.\n"
        "Three exclusions keep this narrow, and they are load-bearing. It is NOT a "
        "stylistic mismatch of wording where the substance matches. It is NOT a source "
        "merely BROADER than the claim when it genuinely covers the claim's case — "
        "only one whose case is not the claim's. And it is NOT a demand for a source "
        "the writer cannot get: the resolvable fixes are re-attributing the claim to a "
        "source the report already carries, restricting the claim to the scope the "
        "source covers, or removing the attribution.\n"
        "So the instruction must say what the source actually says — quote or "
        "paraphrase the passage you relied on — or an editor cannot re-attribute or "
        "restrict. Appending \"this is unverified\" to a claim that keeps the citation "
        "is never an acceptable fix and must never be the instruction.\n\n"
        "A body you were not shown licenses no finding about what a page contains. "
        "Where a source is shown as BLOCKED, COULD NOT READ, NO READABLE TEXT, NOT "
        "ATTEMPTED, COULD NOT RESOLVE, FETCHED, TEXT WITHHELD, or as registry metadata "
        "only — or where what was fetched is plainly not the article (navigation, a "
        "cookie notice, a menu, a paywall teaser) — you have not read it, and you may "
        "not assert what it does or does not contain. And a claim that carries a "
        "citation marker is never `uncited_claim`: its problem, if any, is what that "
        "citation supports, and where you cannot check that, the honest finding is "
        "none."
    ),
    Lens.COMPLETENESS: (
        "Assess only coverage and organization: whether every explicit, material part "
        "of the question is answered rather than replaced with an adjacent question; "
        "whether a material opposing view or counterargument that a careful reader "
        "would expect is absent, or the purported opposing case is an easier objection "
        "that does not challenge a load-bearing conclusion; whether "
        "the organization of the report impedes evaluating its argument, and "
        "whether the report adopts a contested presupposition of the question, or "
        "of its own framing, as settled fact without examining it. An omission "
        "must be fixable within the report itself: adding the missing perspective, "
        "weakening the affected claim, or stating the limitation explicitly are each "
        "acceptable resolutions. Never demand a specific external document, dataset, "
        "or record as the only acceptable fix. Do not invent an unstated goal, a "
        "question behind the question, or an optional angle and call it unanswered.\n\n"
        "The question to hold in mind is what the asker would do with this answer and "
        "which input to that decision is missing — not whether every item on a topic "
        "list has been mentioned. Three triggers follow from it "
        "(D-decisive-quantities).\n"
        "- Magnitude: where the question asks which of two things is larger, better or "
        "more, or asks how much, an answer that puts no magnitude on either side — no "
        "figure, no order-of-magnitude estimate, no break-even — is "
        "`incomplete_answer`, but only where the report's own cited material, or "
        "ordinary arithmetic from facts the report states, would supply one. This is "
        "not a demand for precision: an order of magnitude, or the break-even point, "
        "is a complete answer. A question about kind, mechanism or character needs no "
        "magnitude at all.\n"
        "- The decisive consideration: where one argument settles the comparison — a "
        "term common to both sides cancels, a cost is already sunk, a stated dose sits "
        "against a published limit, one option repeats a production cycle the other "
        "does not — and the report argues its way past it without ever stating it, that "
        "is "
        "`incomplete_answer`; name the consideration in the rationale. Only where the "
        "consideration follows from facts the report itself states or cites, so the fix "
        "is available inside the report.\n"
        "- Readings of the question: where the question's wording admits more than one "
        "reading — a causal boundary (\"alone\", \"impact\", \"adequately\"), an "
        "undefined tier (\"mid-tier\") — and the report answers one of them without "
        "saying which, that is `unexamined_presupposition`. The fix is to state the "
        "reading taken and, where the answer would change under another reading, to "
        "say so."
    ),
}
