"""D-ops-revision: a revision as operations on labelled paragraphs, spliced by code.

The parser is lenient about the things writers were seen to vary and drops nothing it
can still read; the splice enforces what the patch prompt could only ask for. Every
count the two report is an integer on the `generate` event, so each is pinned here.
"""

from __future__ import annotations

import pytest

from reasonable_answer import ops, report
from reasonable_answer.excerpt import citation_census
from reasonable_answer.report import revision_scope
from reasonable_answer.schemas import StructuralRef

REPORT = """## Conclusion

Water boils at 100 degrees Celsius at sea level [1].

## Key findings

- The first finding cites the boiling point [1].
- The second finding cites the freezing point [2].

## Body

The boiling point paragraph restates the claim about boiling [1].

The freezing point paragraph says water freezes at zero degrees [2].

## Sources

[1] Boiling source. https://example.org/boil

[2] Freezing source. https://example.org/freeze
"""

#: Sections: 1 Conclusion (P1), 2 Key findings (P1, the list), 3 Body (P1, P2),
#: 4 Sources (P1, P2).

THREE_BLOCKS = """@@ replace S3.P2 tasks=T1,T4
The freezing point paragraph now says water freezes at zero degrees Celsius [2].
@@ end

@@ delete S3.P1 tasks=T2
@@ end

@@ insert-after S1.P1 tasks=T1
A new paragraph after the conclusion.
@@ end
"""


def ref(section: int, paragraph: int) -> StructuralRef:
    return StructuralRef(section=section, paragraph=paragraph)


def op(kind: str, section: int, paragraph: int, text: str = "", tasks=("T1",)) -> ops.Op:
    return ops.Op(kind, ref(section, paragraph), tuple(tasks), text)


def paragraphs(text: str) -> list[str]:
    return [p.text for p in report.parse(text).paragraphs]


# ------------------------------------------------------------------------ parser


def test_the_three_block_forms_parse_with_kind_locus_tasks_and_text():
    parsed = ops.parse_ops(THREE_BLOCKS)
    assert [o.kind for o in parsed.ops] == ["replace", "delete", "insert-after"]
    assert [o.locus for o in parsed.ops] == [ref(3, 2), ref(3, 1), ref(1, 1)]
    assert parsed.ops[0].tasks == ("T1", "T4")
    assert parsed.ops[0].text.startswith("The freezing point paragraph now")
    assert parsed.ops[1].text == ""
    assert parsed.ops[2].text == "A new paragraph after the conclusion."
    assert parsed.fields == {
        "ops_fenced": 0,
        "ops_bad_headers": 0,
        "ops_stray_lines": 0,
        "ops_unterminated": 0,
        "ops_without_task": 0,
    }


def test_a_code_fence_around_the_whole_reply_is_removed_and_counted():
    parsed = ops.parse_ops(f"```\n{THREE_BLOCKS}```")
    assert len(parsed.ops) == 3
    assert parsed.fields["ops_fenced"] == 1
    assert parsed.fields["ops_stray_lines"] == 0
    parsed = ops.parse_ops(f"```markdown\n{THREE_BLOCKS}\n```\n")
    assert len(parsed.ops) == 3
    assert parsed.fields["ops_fenced"] == 1


@pytest.mark.parametrize(
    ("header", "kind", "tasks"),
    [
        ("@@ insert_after S2.P1 task=T1", "insert-after", ("T1",)),
        ("@@ INSERTAFTER [S2.P1] tasks: T1 T2", "insert-after", ("T1", "T2")),
        ("@@ Replace [S2.P1] tasks=t1, t4", "replace", ("T1", "T4")),
        ("@@replace S2.P1", "replace", ()),
        ("  @@ delete  S2.P1   tasks = T3 ", "delete", ("T3",)),
    ],
)
def test_header_variants_writers_were_seen_to_use_are_read(header, kind, tasks):
    parsed = ops.parse_ops(f"{header}\nbody\n@@ end")
    assert len(parsed.ops) == 1
    assert parsed.ops[0].kind == kind
    assert parsed.ops[0].locus == ref(2, 1)
    assert parsed.ops[0].tasks == tasks


def test_a_missing_final_end_closes_the_block_and_is_counted():
    parsed = ops.parse_ops("@@ replace S3.P2 tasks=T1\nnew text")
    assert len(parsed.ops) == 1
    assert parsed.ops[0].text == "new text"
    assert parsed.fields["ops_unterminated"] == 1


def test_a_header_while_a_block_is_open_closes_it_and_keeps_both():
    parsed = ops.parse_ops("@@ replace S3.P2 tasks=T1\nfirst\n@@ replace S3.P1 tasks=T1\nsecond\n@@ end")
    assert [o.text for o in parsed.ops] == ["first", "second"]
    assert parsed.fields["ops_unterminated"] == 1


def test_stray_text_and_unknown_protocol_lines_are_counted_not_fatal():
    reply = (
        "Here are my operations:\n\n"
        "@@ bogus S3.P2\n"
        "@@ replace S3.P2 tasks=T1\nnew text\n@@ end\n"
        "@@ end\n"
        "Done.\n"
    )
    parsed = ops.parse_ops(reply)
    assert len(parsed.ops) == 1
    assert parsed.fields["ops_stray_lines"] == 2
    # `@@ bogus` and the second `@@ end`, which closes nothing.
    assert parsed.fields["ops_bad_headers"] == 2


def test_a_delete_block_body_is_ignored():
    parsed = ops.parse_ops("@@ delete S3.P1 tasks=T2\nthis text is ignored\n@@ end")
    assert parsed.ops[0].kind == "delete"
    assert parsed.ops[0].text == ""


def test_an_empty_or_prose_only_reply_has_no_operations():
    assert ops.parse_ops("").ops == ()
    assert ops.parse_ops("I have revised the report as requested.").ops == ()


def test_operations_without_a_task_id_are_counted():
    parsed = ops.parse_ops("@@ replace S3.P2\nx\n@@ end\n@@ delete S3.P1 tasks=nope\n@@ end")
    assert [o.tasks for o in parsed.ops] == [(), ()]
    assert parsed.fields["ops_without_task"] == 2


def test_a_label_outside_the_schema_bounds_is_a_bad_header():
    parsed = ops.parse_ops("@@ replace S1000.P1 tasks=T1\nx\n@@ end")
    assert parsed.ops == ()
    # The header, and the `@@ end` that then closes nothing.
    assert parsed.fields["ops_bad_headers"] == 2
    assert parsed.fields["ops_stray_lines"] == 1


# ------------------------------------------------------------------ block model


NON_CANONICAL = (
    "\n\n## Conclusion\nGlued to its heading.  \n\n\n\nA second paragraph.\n\n\n"
    "## Sources\n\n[1] S.\n\n\n"
)


def test_blocks_and_parse_agree_on_every_fixture():
    from test_isolation import CLEAN_REPORT
    from test_revision_scope import BEFORE

    for text in (REPORT, NON_CANONICAL, CLEAN_REPORT, BEFORE):
        structure = report.parse(text)
        bs = report.blocks(text)
        assert [(b.section, b.paragraph, b.text) for b in bs if b.kind == "paragraph"] == [
            (p.section, p.paragraph, p.text) for p in structure.paragraphs
        ]
        headings = [b for b in bs if b.kind == "heading"]
        assert [b.paragraph for b in headings] == [0] * len(headings)
        assert len(headings) == len(structure.section_titles) - 1
        assert all(b.text.startswith("#") for b in headings)


def test_canonical_is_a_fixed_point_and_the_identity_splice():
    canon = report.canonical(NON_CANONICAL)
    assert canon == "## Conclusion\n\nGlued to its heading.\n\nA second paragraph.\n\n## Sources\n\n[1] S."
    assert report.canonical(canon) == canon
    assert ops.splice(NON_CANONICAL, []).text == canon
    assert ops.splice(canon, []).text == canon
    # The scope measurement sees nothing changed: only block ends were stripped.
    assert revision_scope(NON_CANONICAL, canon, []).changed == ()
    assert ops.splice(NON_CANONICAL, []).fields["ops_applied"] == 0


# ------------------------------------------------------------------------ splice


def test_replace_delete_and_insert_after_produce_the_expected_text():
    spliced = ops.splice(REPORT, ops.parse_ops(THREE_BLOCKS).ops)
    assert spliced.fields["ops_applied"] == 3
    assert spliced.fields["ops_replace"] == 1
    assert spliced.fields["ops_delete"] == 1
    assert spliced.fields["ops_insert"] == 1
    assert paragraphs(spliced.text) == [
        "Water boils at 100 degrees Celsius at sea level [1].",
        "A new paragraph after the conclusion.",
        "- The first finding cites the boiling point [1].\n"
        "- The second finding cites the freezing point [2].",
        "The freezing point paragraph now says water freezes at zero degrees Celsius [2].",
        "[1] Boiling source. https://example.org/boil",
        "[2] Freezing source. https://example.org/freeze",
    ]
    assert report.parse(spliced.text).section_titles == (
        "(preamble)", "Conclusion", "Key findings", "Body", "Sources",
    )
    assert not spliced.text.endswith("\n")


def test_the_scope_measurement_counts_a_splice_as_in_scope_where_a_task_named_it():
    spliced = ops.splice(REPORT, [op("replace", 3, 2, "Water freezes at zero degrees [2].")])
    scope = revision_scope(REPORT, spliced.text, [ref(3, 2)])
    assert scope.as_event_fields() == {
        "changed_paragraphs": 1,
        "in_scope": 1,
        "restated": 0,
        "out_of_scope": 0,
        "additive_only": 0,
        "defect_loci_untouched": 0,
    }
    # And an untouched paragraph is byte-identical, not merely "equivalent".
    assert paragraphs(spliced.text)[0] == paragraphs(REPORT)[0]


def test_an_unknown_locus_and_a_heading_locus_are_refused():
    spliced = ops.splice(REPORT, [op("replace", 9, 1, "x"), op("replace", 3, 0, "x"), op("delete", 0, 1)])
    assert spliced.fields["ops_refused_locus"] == 3
    assert spliced.fields["ops_applied"] == 0
    assert spliced.text == report.canonical(REPORT)


def test_a_second_replace_or_delete_on_one_locus_is_refused_and_the_first_wins():
    spliced = ops.splice(
        REPORT,
        [op("replace", 3, 2, "first wins [2]."), op("delete", 3, 2), op("replace", 3, 2, "third [2].")],
    )
    assert spliced.fields["ops_refused_duplicate"] == 2
    assert spliced.fields["ops_applied"] == 1
    assert "first wins [2]." in paragraphs(spliced.text)


def test_several_inserts_on_one_locus_are_kept_in_order_and_an_insert_may_follow_a_delete():
    spliced = ops.splice(
        REPORT,
        [op("insert-after", 3, 1, "one"), op("delete", 3, 1), op("insert-after", 3, 1, "two")],
    )
    assert spliced.fields["ops_applied"] == 3
    body = paragraphs(spliced.text)
    assert body[2:5] == ["one", "two", "The freezing point paragraph says water freezes at zero degrees [2]."]


def test_an_echoed_label_is_stripped_from_a_replacement_and_from_an_insert():
    spliced = ops.splice(
        REPORT,
        [
            op("replace", 3, 2, "[S3.P2] Water freezes at zero [2]."),
            op("insert-after", 3, 2, "[S3.P3] A new paragraph."),
        ],
    )
    assert spliced.fields["ops_label_echo"] == 2
    assert "Water freezes at zero [2]." in paragraphs(spliced.text)
    assert "A new paragraph." in paragraphs(spliced.text)
    assert "[S3." not in spliced.text


def test_an_echoed_section_heading_is_stripped_but_a_new_heading_is_refused():
    echoed = ops.splice(REPORT, [op("replace", 3, 1, "## Body\n\nThe boiling point paragraph, revised [1].")])
    assert echoed.fields["ops_heading_echo"] == 1
    assert echoed.fields["ops_applied"] == 1
    assert "The boiling point paragraph, revised [1]." in paragraphs(echoed.text)
    assert report.parse(echoed.text).section_titles == report.parse(REPORT).section_titles

    new_section = ops.splice(REPORT, [op("replace", 3, 1, "## Method\n\nA new section.")])
    assert new_section.fields["ops_refused_heading"] == 1
    assert new_section.fields["ops_applied"] == 0
    assert new_section.text == report.canonical(REPORT)

    buried = ops.splice(REPORT, [op("replace", 3, 1, "Fine text.\n\n### Sub-heading\n\nMore.")])
    assert buried.fields["ops_refused_heading"] == 1


def test_an_empty_replacement_is_refused_rather_than_read_as_a_delete():
    spliced = ops.splice(REPORT, [op("replace", 3, 2, "   \n  "), op("insert-after", 3, 2, "")])
    assert spliced.fields["ops_refused_empty"] == 2
    assert spliced.fields["ops_applied"] == 0
    assert spliced.text == report.canonical(REPORT)


def test_a_replacement_with_blank_lines_becomes_several_paragraphs_in_place():
    spliced = ops.splice(REPORT, [op("replace", 3, 2, "Split one [2].\n\n\nSplit two.")])
    body = paragraphs(spliced.text)
    assert body[3:5] == ["Split one [2].", "Split two."]
    assert body[5] == "[1] Boiling source. https://example.org/boil"


def test_a_list_under_one_label_is_replaced_whole():
    new_list = "- The first finding cites the boiling point [1].\n- The second finding is gone."
    spliced = ops.splice(REPORT, [op("replace", 2, 1, new_list)])
    assert spliced.fields["ops_applied"] == 1
    assert paragraphs(spliced.text)[1] == new_list


# ------------------------------------------------------------- Sources guard


def test_a_sources_entry_is_replaced_and_a_new_one_inserted_after_the_last():
    spliced = ops.splice(
        REPORT,
        [
            op("replace", 4, 2, "[2] A better freezing source. https://example.org/freeze2"),
            op("insert-after", 4, 2, "[3] A third source. https://example.org/three"),
        ],
    )
    assert spliced.fields["ops_applied"] == 2
    assert spliced.fields["ops_refused_dangling"] == 0
    census = citation_census(spliced.text)
    assert census["source_entries"] == 3
    assert census["dangling_markers"] == 0


def test_deleting_an_entry_a_kept_paragraph_still_cites_is_refused():
    spliced = ops.splice(REPORT, [op("delete", 4, 2)])
    assert spliced.fields["ops_refused_dangling"] == 1
    assert spliced.fields["ops_applied"] == 0
    assert citation_census(spliced.text)["dangling_markers"] == 0
    assert "[2] Freezing source. https://example.org/freeze" in paragraphs(spliced.text)


def test_deleting_an_uncited_entry_is_applied():
    with_orphan = REPORT + "\n[3] Nobody cites this. https://example.org/orphan\n"
    spliced = ops.splice(with_orphan, [op("delete", 4, 3)])
    assert spliced.fields["ops_applied"] == 1
    assert spliced.fields["ops_refused_dangling"] == 0
    assert citation_census(spliced.text)["source_entries"] == 2


def test_a_delete_is_accepted_when_the_same_reply_removes_every_marker_citing_it():
    spliced = ops.splice(
        REPORT,
        [
            op("replace", 2, 1, "- The first finding cites the boiling point [1]."),
            op("replace", 3, 2, "The freezing point paragraph now makes no sourced claim."),
            op("delete", 4, 2),
        ],
    )
    assert spliced.fields["ops_applied"] == 3
    assert spliced.fields["ops_refused_dangling"] == 0
    census = citation_census(spliced.text)
    assert census["source_entries"] == 1
    assert census["dangling_markers"] == 0


ONE_PARAGRAPH_SOURCES = REPORT.replace(
    "[1] Boiling source. https://example.org/boil\n\n[2] Freezing source. https://example.org/freeze",
    "1. Boiling source. https://example.org/boil\n2. Freezing source. https://example.org/freeze",
)


def test_replacing_a_one_paragraph_sources_list_that_drops_a_cited_entry_is_refused():
    assert len([p for p in report.parse(ONE_PARAGRAPH_SOURCES).paragraphs if p.section == 4]) == 1
    dropped = ops.splice(ONE_PARAGRAPH_SOURCES, [op("replace", 4, 1, "1. Boiling source. https://example.org/boil")])
    assert dropped.fields["ops_refused_dangling"] == 1
    assert dropped.fields["ops_applied"] == 0
    assert citation_census(dropped.text)["dangling_markers"] == 0

    grown = ops.splice(
        ONE_PARAGRAPH_SOURCES,
        [
            op(
                "replace",
                4,
                1,
                "1. Boiling source. https://example.org/boil\n"
                "2. Freezing source. https://example.org/freeze\n"
                "3. A third. https://example.org/three",
            )
        ],
    )
    assert grown.fields["ops_applied"] == 1
    assert citation_census(grown.text)["source_entries"] == 3


def test_a_body_edit_that_cites_a_new_entry_inserted_in_the_same_reply_is_not_dangling():
    spliced = ops.splice(
        REPORT,
        [
            op("replace", 3, 2, "The freezing point paragraph now cites a third source [3]."),
            op("insert-after", 4, 2, "[3] A third source. https://example.org/three"),
        ],
    )
    assert spliced.fields["ops_applied"] == 2
    assert citation_census(spliced.text)["dangling_markers"] == 0


# ------------------------------------------------------------------------ revise


def test_revise_returns_none_when_nothing_parsed_or_nothing_applied():
    assert ops.revise(REPORT, "") is None
    assert ops.revise(REPORT, "I revised it.") is None
    assert ops.revise(REPORT, "@@ replace S9.P1 tasks=T1\nx\n@@ end") is None
    assert ops.revise(REPORT, "@@ delete S4.P2 tasks=T1\n@@ end") is None


def test_revise_carries_every_count_as_an_integer_and_a_partial_reply_still_ships():
    spliced = ops.revise(REPORT, THREE_BLOCKS + "@@ replace S9.P9 tasks=T1\nnowhere\n@@ end")
    assert spliced is not None
    assert set(spliced.fields) == set(ops.PARSE_FIELDS) | set(ops.SPLICE_FIELDS)
    assert all(isinstance(v, int) for v in spliced.fields.values())
    assert spliced.fields["ops_total"] == 4
    assert spliced.fields["ops_applied"] == 3
    assert spliced.fields["ops_refused_locus"] == 1
    assert spliced.text == ops.splice(REPORT, ops.parse_ops(THREE_BLOCKS).ops).text


# ------------------------------------------------- the two heading shapes ops leans on


def test_the_sources_section_is_found_by_the_extractors_own_heading_rule():
    """The dangling-marker guard defers exactly the section `fetch._SOURCES_HEADING`
    names — any depth, any case, the word alone — and no other. Pinned here because a
    change to that regex would silently move which operations the guard checks."""
    for heading in ("## Sources", "# SOURCES", "### sources", "##   Sources  "):
        text = REPORT.replace("## Sources", heading)
        assert ops.splice(text, [op("delete", 4, 2)]).fields["ops_refused_dangling"] == 1, heading
    # A heading that only starts with the word is not the Sources section: its
    # paragraphs are body paragraphs, deleted freely and never deferred.
    text = REPORT.replace("## Sources", "## Sources and further reading")
    spliced = ops.splice(text, [op("delete", 4, 2)])
    assert spliced.fields["ops_refused_dangling"] == 0
    assert spliced.fields["ops_applied"] == 1


def test_a_heading_is_refused_at_every_depth_and_only_when_it_is_one():
    """The heading-echo strip and `ops_refused_heading` follow `report._HEADING`: one
    to six `#` followed by a space. A `#` glued to a word is prose, not a heading."""
    for depth in range(1, 7):
        text = f"{'#' * depth} New section\n\nBody."
        assert ops.splice(REPORT, [op("replace", 3, 1, text)]).fields["ops_refused_heading"] == 1, depth
    spliced = ops.splice(REPORT, [op("replace", 3, 1, "#hashtag is not a heading [1].")])
    assert spliced.fields["ops_refused_heading"] == 0
    assert spliced.fields["ops_applied"] == 1
    assert "#hashtag is not a heading [1]." in paragraphs(spliced.text)


# ------------------------------------------------- hardening after the first review


def test_a_sources_swap_that_orphans_one_entry_while_curing_another_is_refused():
    """The guard compares the *set* of orphaned entry numbers, not the count. Body cites
    [1] and [9]; only [1] is listed, so [9] is already dangling. Replacing the [1] entry
    with a [9] entry keeps the count at one and orphans [1] — refused."""
    draft = "## Body\n\nA claim [1] and another [9].\n\n## Sources\n\n[1] One. https://example.org/one"
    assert citation_census(draft)["dangling_markers"] == 1
    spliced = ops.splice(draft, [op("replace", 2, 1, "[9] Nine. https://example.org/nine")])
    assert spliced.fields["ops_refused_dangling"] == 1
    assert spliced.fields["ops_applied"] == 0
    # Curing the orphan without creating one is applied: the set shrinks.
    grown = ops.splice(
        draft, [op("replace", 2, 1, "[1] One. https://example.org/one\n\n[9] Nine. https://example.org/nine")]
    )
    assert grown.fields["ops_applied"] == 1
    assert citation_census(grown.text)["dangling_markers"] == 0


def test_a_heading_line_in_the_middle_of_a_block_is_refused():
    """`report.blocks` only reads the first line of a block, so a heading glued under a
    sentence creates no locus — but it still renders as a heading. Every line is checked."""
    spliced = ops.splice(REPORT, [op("replace", 3, 1, "kept line [1]\n## Sneaky heading\nmore text")])
    assert spliced.fields["ops_refused_heading"] == 1
    assert spliced.fields["ops_applied"] == 0


def test_operations_that_leave_no_paragraph_are_malformed():
    """The empty-reply guard reads the reply, which under ops is operations; the
    spliced result is where "the model answered with nothing" has to be judged."""
    one = "Just one paragraph, nothing else."
    assert ops.revise(one, "@@ delete S0.P1 tasks=T1\n@@ end") is None
    loci = ((1, 1), (2, 1), (3, 1), (3, 2), (4, 1), (4, 2))
    everything = "".join(f"@@ delete S{sec}.P{par} tasks=T1\n@@ end\n" for sec, par in loci)
    assert ops.revise(REPORT, everything) is None
    # One paragraph left is a report, however short.
    almost = everything.replace("@@ delete S1.P1 tasks=T1\n@@ end\n", "")
    assert ops.revise(REPORT, almost) is not None


def test_a_second_replace_on_a_sources_locus_is_refused_and_the_first_wins():
    spliced = ops.splice(
        REPORT,
        [
            op("replace", 4, 2, "[2] First freezing source. https://example.org/freeze"),
            op("replace", 4, 2, "[2] Second freezing source. https://example.org/freeze2"),
        ],
    )
    assert spliced.fields["ops_refused_duplicate"] == 1
    assert spliced.fields["ops_applied"] == 1
    assert "[2] First freezing source. https://example.org/freeze" in paragraphs(spliced.text)


def test_repeated_task_ids_are_deduplicated():
    parsed = ops.parse_ops("@@ replace S3.P2 tasks=T1,T1,t1 T2\nx\n@@ end")
    assert parsed.ops[0].tasks == ("T1", "T2")
