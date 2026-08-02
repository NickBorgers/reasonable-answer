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
        "from their stated premises, whether any claim is stated more strongly "
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
        "qualifying the claim to what the support establishes is always acceptable."
    ),
    Lens.EVIDENCE: (
        "Assess only the sourcing of the report: whether material claims carry a "
        "citation, whether any citation is implausible or cannot be what it claims "
        "to be on its face, whether a cited source is described as supporting "
        "something it plainly would not support, and whether, on a contested "
        "question, the sourcing is drawn so narrowly from one outlet, organization "
        "or aligned cluster that the report inherits a single viewpoint."
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
        "question behind the question, or an optional angle and call it unanswered."
    ),
}
