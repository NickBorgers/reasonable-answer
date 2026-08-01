"""The revision scope measurement (D-scoped-revision).

`report.revision_scope` answers one question about a revision: did it edit the
paragraphs the fix tasks named, or did it re-roll text nobody complained about? It is
warn-only — nothing rejects a draft on its verdict — so these tests pin the *counting*,
which is the whole product.

The case that motivates the design is `test_an_inserted_paragraph_does_not_renumber_the_tail`:
comparing `S2.P1` in the old report against `S2.P1` in the new one is the obvious
implementation and it is wrong, because adding a paragraph shifts every locus after it
and fix tasks routinely ask for one.
"""

from __future__ import annotations

from reasonable_answer.report import revision_scope
from reasonable_answer.schemas import StructuralRef

BEFORE = """## Conclusion

Tunnelling all home traffic to a VPN moves trust, it does not remove it.

## Key findings

The ISP stops seeing destinations; the VPN operator starts seeing them.

Leak vectors can expose the real IP address despite the tunnel.

## Sources

[1] https://ssd.eff.org/module/choosing-vpn
"""


def ref(section: int, paragraph: int) -> StructuralRef:
    return StructuralRef(section=section, paragraph=paragraph)


def test_an_in_scope_edit_is_counted_in_scope():
    after = BEFORE.replace(
        "Leak vectors can expose the real IP address despite the tunnel.",
        "Leak vectors — DNS, IPv6, WebRTC — can expose the real IP address [1].",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    assert scope.as_event_fields() == {
        "changed_paragraphs": 1,
        "in_scope": 1,
        "out_of_scope": 0,
        "defect_loci_untouched": 0,
    }


def test_rewording_a_paragraph_no_task_named_is_out_of_scope():
    """The behavior the whole decision exists to measure: a writer handed one fix task
    that re-renders a paragraph nobody complained about."""
    after = BEFORE.replace(
        "The ISP stops seeing destinations; the VPN operator starts seeing them.",
        "Your ISP no longer observes destinations; instead the VPN provider does.",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    fields = scope.as_event_fields()
    assert fields["out_of_scope"] == 1
    assert fields["in_scope"] == 0
    assert scope.out_of_scope == (ref(2, 1),)
    # The task's own locus came back untouched, which is its own signal.
    assert scope.untouched_defect_loci == (ref(2, 2),)


def test_an_inserted_paragraph_does_not_renumber_the_tail():
    """Insertion shifts every later locus. Content-based diffing must not read that as
    the rest of the document being rewritten."""
    after = BEFORE.replace(
        "Leak vectors can expose the real IP address despite the tunnel.",
        "Leak vectors can expose the real IP address despite the tunnel.\n\n"
        "Traffic analysis can infer destinations from timing and volume alone.",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    fields = scope.as_event_fields()
    assert fields["out_of_scope"] == 0, scope.out_of_scope
    # One insertion, attributed to the flagged paragraph it sits beside.
    assert fields["changed_paragraphs"] == 1
    assert fields["in_scope"] == 1


def test_an_insertion_far_from_every_flagged_paragraph_is_out_of_scope():
    after = BEFORE.replace(
        "## Key findings\n",
        "## Key findings\n\nA short unrequested preamble nobody asked for.\n",
    )
    scope = revision_scope(BEFORE, after, [ref(3, 1)])
    assert scope.as_event_fields()["out_of_scope"] == 1


def test_a_whole_document_rewrite_reads_as_almost_entirely_out_of_scope():
    after = """## Conclusion

A consumer VPN relocates trust from the access network to the provider.

## Key findings

Destination visibility transfers rather than disappears.

## Sources

[1] https://ssd.eff.org/module/choosing-vpn
"""
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    assert scope.as_event_fields()["out_of_scope"] >= 2


def test_rewrapping_a_paragraph_is_not_an_edit():
    """A writer that re-wraps at a different column has not changed anything. Counting
    that as an out-of-scope edit would bury the signal under formatting noise."""
    after = BEFORE.replace(
        "The ISP stops seeing destinations; the VPN operator starts seeing them.",
        "The ISP stops seeing destinations;\nthe VPN operator starts seeing them.",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    assert scope.as_event_fields()["changed_paragraphs"] == 0


def test_case_and_emphasis_changes_are_edits():
    """Only whitespace is folded. Emphasis and case are things a revision genuinely
    changes, and folding them would hide real edits."""
    after = BEFORE.replace(
        "The ISP stops seeing destinations; the VPN operator starts seeing them.",
        "The ISP stops seeing destinations; the **VPN operator** starts seeing them.",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 2)])
    assert scope.as_event_fields()["out_of_scope"] == 1


def test_an_unchanged_document_reports_nothing_changed():
    scope = revision_scope(BEFORE, BEFORE, [ref(2, 2)])
    assert scope.as_event_fields() == {
        "changed_paragraphs": 0,
        "in_scope": 0,
        "out_of_scope": 0,
        "defect_loci_untouched": 1,
    }


def test_the_shipped_defaults_are_patch_warn_and_one_rewrite():
    """Patch is the doctrine, not an opt-in affordance: shipping it off by default would
    mean the suite never exercises the path a deployment actually gets."""
    from reasonable_answer.config import Budgets, RevisionConfig

    assert (RevisionConfig().mode, RevisionConfig().scope_check) == ("patch", "warn")
    assert Budgets().rewrite_cap == 1
    # Both arms of the A/B are reachable from configuration alone.
    assert RevisionConfig(mode="rewrite", scope_check="off").mode == "rewrite"


def test_several_tasks_each_resolved_in_place():
    after = BEFORE.replace(
        "The ISP stops seeing destinations; the VPN operator starts seeing them.",
        "The ISP stops seeing destinations; the VPN operator starts seeing them [1].",
    ).replace(
        "Leak vectors can expose the real IP address despite the tunnel.",
        "Leak vectors can expose the real IP address despite the tunnel [1].",
    )
    scope = revision_scope(BEFORE, after, [ref(2, 1), ref(2, 2)])
    assert scope.as_event_fields() == {
        "changed_paragraphs": 2,
        "in_scope": 2,
        "out_of_scope": 0,
        "defect_loci_untouched": 0,
    }
