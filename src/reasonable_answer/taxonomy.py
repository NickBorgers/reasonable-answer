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
    # logic
    CONTRADICTED_CLAIM = "contradicted_claim"
    INVALID_INFERENCE = "invalid_inference"
    OVERSTATED_CLAIM = "overstated_claim"
    # completeness
    OMITTED_COUNTERARGUMENT = "omitted_counterargument"
    UNCLEAR_STRUCTURE = "unclear_structure"
    # any lens
    STYLISTIC = "stylistic"


#: category -> mechanical severity floor (triage clamps up to this)
SEVERITY_FLOOR: dict[Category, Severity] = {
    Category.FABRICATED_CITATION: Severity.BLOCKING,
    Category.MISREPRESENTED_SOURCE: Severity.MAJOR,
    Category.UNCITED_CLAIM: Severity.MAJOR,
    Category.CONTRADICTED_CLAIM: Severity.BLOCKING,
    Category.INVALID_INFERENCE: Severity.MAJOR,
    Category.OVERSTATED_CLAIM: Severity.MAJOR,
    Category.OMITTED_COUNTERARGUMENT: Severity.MAJOR,
    Category.UNCLEAR_STRUCTURE: Severity.MINOR,
    Category.STYLISTIC: Severity.MINOR,
}

#: lens -> the categories that lens is allowed to raise. `stylistic` is allowed
#: everywhere but is ignored for convergence.
LENS_CATEGORIES: dict[Lens, tuple[Category, ...]] = {
    Lens.LOGIC: (
        Category.CONTRADICTED_CLAIM,
        Category.INVALID_INFERENCE,
        Category.OVERSTATED_CLAIM,
        Category.STYLISTIC,
    ),
    Lens.EVIDENCE: (
        Category.FABRICATED_CITATION,
        Category.MISREPRESENTED_SOURCE,
        Category.UNCITED_CLAIM,
        Category.STYLISTIC,
    ),
    Lens.COMPLETENESS: (
        Category.OMITTED_COUNTERARGUMENT,
        Category.UNCLEAR_STRUCTURE,
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


LENS_BRIEF: dict[Lens, str] = {
    Lens.LOGIC: (
        "Assess only the internal logic of the report: whether claims contradict "
        "each other or a source the report itself cites, whether conclusions follow "
        "from their stated premises, and whether any claim is stated more strongly "
        "than the support offered for it."
    ),
    Lens.EVIDENCE: (
        "Assess only the sourcing of the report: whether material claims carry a "
        "citation, whether any citation is implausible or cannot be what it claims "
        "to be on its face, and whether a cited source is described as supporting "
        "something it plainly would not support."
    ),
    Lens.COMPLETENESS: (
        "Assess only coverage and organization: whether a material opposing view or "
        "counterargument that a careful reader would expect is absent, and whether "
        "the organization of the report impedes evaluating its argument. An omission "
        "must be fixable within the report itself: adding the missing perspective, "
        "weakening the affected claim, or stating the limitation explicitly are each "
        "acceptable resolutions. Never demand a specific external document, dataset, "
        "or record as the only acceptable fix."
    ),
}
