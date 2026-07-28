"""A critic's quoting slip is repaired in place, not paid for with a critique attempt.

Two production runs (`run-3b4fe4760289`, `run-5d4b1d9cb08b`) aborted the same way: a
critic returned schema-valid issues whose `claim_span` was not really in the paragraph
it cited, `critique_once` failed the whole lens, and controller rule 2 spent one of the
12 `critique_attempts` asking a fresh model the *identical* question. One of them burned
all 12 on a single artifact and shipped nothing. The fix is not more attempts — it is
telling the critic what was wrong, which is what these tests pin.
"""

from __future__ import annotations

from fakes import FakeClient

from reasonable_answer import critique as critique_mod
from reasonable_answer.schemas import CritiqueOutput, RawIssue, StructuralRef
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """# Title

Fluoridation reduces tooth decay by about 25% in children and adolescents.

## Risks

At concentrations well above the recommended level, dental fluorosis occurs.
"""

VERBATIM = "reduces tooth decay by about 25% in children and adolescents"
INVENTED = "reduces tooth decay by roughly a quarter in kids"


def _issue(claim_span: str) -> RawIssue:
    return RawIssue(
        category=Category.OMITTED_COUNTERARGUMENT,
        severity=Severity.MAJOR,
        locus=StructuralRef(section=1, paragraph=1),
        claim_span=claim_span,
        rationale="the opposing position is not stated",
        instruction="state the strongest counterargument or note that none was found",
    )


def _client(spans: list[str], *, repairs: int) -> FakeClient:
    """A critic that emits `spans` in order, one per call."""
    seq = iter(spans)

    def critique_fn(_alias: str, _user: str) -> CritiqueOutput:
        return CritiqueOutput(issues=[_issue(next(seq))])

    return FakeClient(
        identities={"critic": "vendor-x/critic"},
        critique_fn=critique_fn,
        report_fn=lambda _n: REPORT,
        critic_repair_retries=repairs,
    )


def _run(client: FakeClient):
    return critique_mod.critique_once(
        client,
        "critic",
        "vendor-x/critic",
        Lens.COMPLETENESS,
        "What are the health effects of fluoridating water?",
        REPORT,
        "h" * 64,
        "vendor-a/author",
    )


def test_a_misquoted_span_is_repaired_rather_than_failing_the_lens():
    client = _client([INVENTED, VERBATIM], repairs=2)

    result = _run(client)

    assert not result.failed
    assert result.issues[0].claim_span == VERBATIM
    # Two calls to the *same* critic, not one call each to two critics: the cost of the
    # slip is a repair, not one of the run's 12 critique attempts.
    assert len(client.calls) == 2


def test_the_repair_prompt_carries_the_text_that_should_have_been_quoted():
    """A critic told only "that is not a verbatim quote" knows no more than it did the
    first time. The paragraph itself is what makes the second attempt different."""
    client = _client([INVENTED, VERBATIM], repairs=2)

    _run(client)

    repair_prompt = client.calls[1].user
    assert "not a verbatim quote" in repair_prompt
    assert VERBATIM in repair_prompt
    assert "character-for-character" in repair_prompt


def test_a_critic_that_never_quotes_correctly_still_fails_the_lens_closed():
    """Fail-closed is unchanged: repair is bounded, and a lens that cannot be validated
    fails rather than passing with unanchored issues."""
    client = _client([INVENTED, INVENTED, INVENTED], repairs=2)

    result = _run(client)

    assert result.failed
    assert "verbatim quote" in (result.failure_reason or "")
    assert len(client.calls) == 3  # the budget, and not one call more


def test_one_bad_issue_still_fails_the_whole_lens():
    """Nothing is silently dropped: a response is validated as a unit, so a good issue
    beside a bad one does not smuggle the bad one's counts through."""
    seq = iter([[_issue(VERBATIM), _issue(INVENTED)], [_issue(VERBATIM), _issue(INVENTED)]])
    client = FakeClient(
        identities={"critic": "vendor-x/critic"},
        critique_fn=lambda _a, _u: CritiqueOutput(issues=next(seq)),
        report_fn=lambda _n: REPORT,
        critic_repair_retries=1,
    )

    result = _run(client)

    assert result.failed
    assert result.issues == []


def test_a_typographic_quote_is_not_a_misquote():
    """The report carries a curly apostrophe; a model retyping the span emits a straight
    one. That difference is invisible to a reader and must not fail a lens."""
    report = "# Title\n\nThe agency’s 2015 recommendation — 0.7 mg/L — still stands.\n"
    client = FakeClient(
        identities={"critic": "vendor-x/critic"},
        critique_fn=lambda _a, _u: CritiqueOutput(
            issues=[_issue("The agency's 2015 recommendation - 0.7 mg/L - still stands.")]
        ),
        report_fn=lambda _n: report,
        critic_repair_retries=0,
    )

    result = critique_mod.critique_once(
        client,
        "critic",
        "vendor-x/critic",
        Lens.COMPLETENESS,
        "q",
        report,
        "h" * 64,
        "vendor-a/author",
    )

    assert not result.failed
