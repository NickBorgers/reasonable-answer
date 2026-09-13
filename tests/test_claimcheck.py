"""Claim-level source verification (D-claim-level-verification).

Every sentence that cites a fetched page is checked against that page in its own
context, under the evidence critic's slot. These tests pin the four things the design
rests on: the pairing is the report's own structure; a verdict becomes a finding only
on the two conditions the decision names (contradicted, or absent from a page shown
whole); every failure leaves a pair unchecked and mints nothing; and with the checker
off the evidence lens is byte-identical to a build without it.
"""

from __future__ import annotations

import json

import pytest
from fakes import FakeClient
from pydantic import ValidationError

from reasonable_answer import claimcheck, triage
from reasonable_answer.config import ClaimCheckConfig, Config, ConfigError, SearchConfig
from reasonable_answer.fetch import FetchedSource
from reasonable_answer.graph import Runtime, _critique_one
from reasonable_answer.llm import ModelCallError, ProviderAccountError
from reasonable_answer.schemas import ClaimVerdict, CritiqueOutput, RawIssue, StructuralRef
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """## Conclusion

Steam catapults convert about 5% of their energy into launch work [1].
EMALS is projected to reach roughly 90% [1].

## Key findings

The carrier fleet numbers eleven ships [2]. Launch variability is lower on EMALS [1][2].

## Sources

[1] https://example.test/emals
[2] https://example.test/fleet
"""

EMALS_PAGE = (
    "EMALS overview. Steam catapults are inefficient, converting roughly 4-6% of steam "
    "energy into useful launch work. The electromagnetic system's projected end-to-end "
    "efficiency is about 60%, not the 90% sometimes quoted. Launch-to-launch variability "
    "is lower with EMALS."
)
FLEET_PAGE = "The United States Navy operates eleven aircraft carriers as of this writing."


def page(url: str, text: str) -> FetchedSource:
    return FetchedSource(url=url, text=text, status=200)


class _Pages:
    def __init__(self, pages):
        self.pages = pages

    def fetch_all(self, urls):
        return [self.pages[u] for u in urls if u in self.pages]


# ------------------------------------------------------------------ pairing


def test_pairs_are_every_citing_sentence_with_its_page():
    kept, dropped = claimcheck.pairs(REPORT)
    assert dropped == 0
    rows = [(str(p.locus), p.number, p.sentence[:22]) for p in kept]
    assert rows == [
        ("S1.P1", 1, "Steam catapults conver"),
        ("S1.P1", 1, "EMALS is projected to "),
        ("S2.P1", 2, "The carrier fleet numb"),
        ("S2.P1", 1, "Launch variability is "),
        ("S2.P1", 2, "Launch variability is "),
    ]
    # The paragraph travels with the pair as context; the URL is the bibliography's.
    assert kept[0].paragraph.startswith("Steam catapults convert")
    assert kept[2].url == "https://example.test/fleet"


def test_the_sources_section_and_unlisted_numbers_pair_with_nothing():
    report = REPORT.replace("eleven ships [2]", "eleven ships [7]")
    kept, _ = claimcheck.pairs(report)
    assert all(p.number != 7 for p in kept)
    assert all(p.locus.section != 3 for p in kept)


def test_pairs_past_the_cap_are_counted_not_checked():
    kept, dropped = claimcheck.pairs(REPORT, limit=2)
    assert (len(kept), dropped) == (2, 3)


# ------------------------------------------------------------------ verdicts -> findings


def _verdict(pair, verdict, span=None, complete=True):
    return claimcheck.PairVerdict(pair, verdict, span, "because", complete=complete)


def test_contradicted_mints_misrepresented_source_at_the_floor():
    kept, _ = claimcheck.pairs(REPORT)
    emals = kept[1]
    check = claimcheck.ClaimCheck(
        verdicts=[_verdict(emals, "contradicted", "efficiency is about 60%")]
    )
    [issue] = claimcheck.issues_from(check)
    assert issue.category is Category.MISREPRESENTED_SOURCE
    assert issue.severity is Severity.MAJOR
    assert issue.locus == StructuralRef(section=1, paragraph=1)
    assert issue.claim_span == emals.sentence
    assert issue.related_span == "efficiency is about 60%"
    assert issue.citation_id == "1"
    # The span is verbatim in the paragraph, so the finding passes the same check a
    # critic's would.
    from reasonable_answer import report as report_mod

    triage.validate_issue(Lens.EVIDENCE, issue, report_mod.parse(REPORT), True)


def test_absent_is_a_finding_only_on_a_page_shown_whole():
    kept, _ = claimcheck.pairs(REPORT)
    whole = claimcheck.ClaimCheck(verdicts=[_verdict(kept[0], "absent", complete=True)])
    partial = claimcheck.ClaimCheck(verdicts=[_verdict(kept[0], "absent", complete=False)])
    assert len(claimcheck.issues_from(whole)) == 1
    assert claimcheck.issues_from(partial) == []
    assert partial.counts()["absent_partial"] == 1
    assert whole.counts()["absent"] == 1


def test_supported_unreadable_and_unchecked_mint_nothing():
    kept, _ = claimcheck.pairs(REPORT)
    check = claimcheck.ClaimCheck(
        verdicts=[
            _verdict(kept[0], "supported", "roughly 4-6%"),
            _verdict(kept[1], "unreadable"),
            claimcheck.PairVerdict(kept[2], "unchecked", unchecked_reason="call_failed"),
        ],
        over_cap=1,
    )
    assert claimcheck.issues_from(check) == []
    assert check.counts() == {
        "pairs": 4,
        "checked": 2,
        "supported": 1,
        "contradicted": 0,
        "absent": 0,
        "absent_partial": 0,
        "unreadable": 1,
        "unchecked": 2,
        "aborted": 0,
        "cached": 0,
    }


def test_one_sentence_citing_two_sources_is_one_finding_per_source_not_per_span():
    kept, _ = claimcheck.pairs(REPORT)
    both = [p for p in kept if p.sentence.startswith("Launch variability")]
    check = claimcheck.ClaimCheck(
        verdicts=[_verdict(both[0], "contradicted", "x"), _verdict(both[1], "contradicted", "y")]
    )
    # Same locus, same span: `triage.distinct_issues` would collapse these anyway, so
    # the minter collapses them first and keeps the first source's evidence.
    assert len(claimcheck.issues_from(check)) == 1


def test_a_long_sentence_is_clipped_to_a_verbatim_prefix():
    long = "word " * 120
    report = REPORT.replace("The carrier fleet numbers eleven ships [2].", f"{long.strip()} [2].")
    kept, _ = claimcheck.pairs(report)
    fleet = next(p for p in kept if p.number == 2)
    [issue] = claimcheck.issues_from(
        claimcheck.ClaimCheck(verdicts=[_verdict(fleet, "contradicted", "eleven")])
    )
    assert len(issue.claim_span) <= 400
    assert issue.claim_span in fleet.paragraph


# ------------------------------------------------------------------ reconcile


def _critic_issue(
    section, paragraph, span, citation_id=None, category=Category.MISREPRESENTED_SOURCE
):
    return RawIssue(
        category=category,
        severity=Severity.MAJOR,
        locus=StructuralRef(section=section, paragraph=paragraph),
        claim_span=span,
        rationale="r",
        instruction="i",
        citation_id=citation_id,
    )


def test_a_checked_pair_supersedes_the_critics_own_misrepresentation_finding():
    kept, _ = claimcheck.pairs(REPORT)
    check = claimcheck.ClaimCheck(verdicts=[_verdict(kept[1], "supported", "about 60%")])
    critic = [
        _critic_issue(1, 1, "projected to reach roughly 90%", citation_id="1"),
        # Same sentence, no citation id: matched by span containment.
        _critic_issue(1, 1, "EMALS is projected to reach roughly 90% [1]."),
        # A different sentence at the same locus, not checked: the critic's call stands.
        _critic_issue(1, 1, "convert about 5% of their energy", citation_id="1"),
        # Another category passes through untouched.
        _critic_issue(1, 1, "projected to reach roughly 90%", category=Category.UNCITED_CLAIM),
    ]
    kept_issues = claimcheck.reconcile(critic, check)
    assert [i.claim_span for i in kept_issues] == [
        "convert about 5% of their energy",
        "projected to reach roughly 90%",
    ]
    assert kept_issues[1].category is Category.UNCITED_CLAIM


@pytest.mark.parametrize(
    "verdict,complete,kept_by_critic",
    [
        ("supported", True, False),
        ("contradicted", False, False),
        ("absent", True, False),
        # Not readings of the page: the checker was shown no body, or only part of one.
        # Neither mints a finding, so neither may retire the critic's
        # (D-claim-check-inconclusive-verdicts).
        ("absent", False, True),
        ("unreadable", False, True),
    ],
)
def test_only_a_settled_verdict_retires_the_critics_own_finding(verdict, complete, kept_by_critic):
    kept, _ = claimcheck.pairs(REPORT)
    span = "x" if verdict in ("supported", "contradicted") else None
    check = claimcheck.ClaimCheck(verdicts=[_verdict(kept[1], verdict, span, complete=complete)])
    critic = [_critic_issue(1, 1, "projected to reach roughly 90%", citation_id="1")]
    assert (claimcheck.reconcile(critic, check) == critic) is kept_by_critic
    # Whichever way it goes, the sentence is never counted under two spans and never
    # under none while a critic stood behind a finding: dropped means minted, or the
    # verdict was `supported`.
    minted = claimcheck.issues_from(check)
    if not kept_by_critic and verdict != "supported":
        assert len(minted) == 1
    if kept_by_critic:
        assert minted == []


def test_nothing_checked_means_the_critic_is_untouched():
    kept, _ = claimcheck.pairs(REPORT)
    check = claimcheck.ClaimCheck(
        verdicts=[claimcheck.PairVerdict(kept[1], "unchecked", unchecked_reason="call_failed")]
    )
    critic = [_critic_issue(1, 1, "projected to reach roughly 90%", citation_id="1")]
    assert claimcheck.reconcile(critic, check) == critic


# ------------------------------------------------------------------ the call


def _client(identities, claim_fn, critique_fn=None):
    return FakeClient(
        identities=identities,
        critique_fn=critique_fn or (lambda a, u: CritiqueOutput(issues=[])),
        report_fn=lambda n: REPORT,
        claim_fn=claim_fn,
    )


def _sources():
    return [
        page("https://example.test/emals", EMALS_PAGE),
        page("https://example.test/fleet", FLEET_PAGE),
    ]


def _on_fleet_page(user: str) -> bool:
    """Which page this checker call holds — read from the fenced source header, not from
    the paragraph text, which mentions the fleet in a sentence that cites [1]."""
    return "AS FETCHED FROM https://example.test/fleet" in user


def _supported(user: str) -> ClaimVerdict:
    span = "eleven aircraft carriers" if _on_fleet_page(user) else "roughly 4-6%"
    return ClaimVerdict(verdict="supported", support_span=span, reason="ok")


def test_each_pair_is_one_fresh_call_holding_one_sentence_and_one_page(identities):
    seen = []

    def claim_fn(alias, user):
        seen.append(user)
        return ClaimVerdict(verdict="absent", reason="nothing on it")

    client = _client(identities, claim_fn)
    result = claimcheck.check(
        client,
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        _sources(),
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    assert result.counts()["checked"] == 5
    assert len(seen) == 5
    first = seen[0]
    assert "Steam catapults convert about 5%" in first
    assert "EMALS overview" in first
    # Isolation: the other page, the question and the rest of the report stay out.
    assert FLEET_PAGE not in first
    assert "## Key findings" not in first
    assert "[1], AS FETCHED FROM https://example.test/emals" in first
    # The fleet claim is checked against the fleet page, not the EMALS one.
    fleet_call = seen[2]
    assert FLEET_PAGE in fleet_call and "EMALS overview" not in fleet_call


def test_a_verdict_must_quote_the_page_or_the_pair_is_unchecked(identities):
    def claim_fn(alias, user):
        return ClaimVerdict(verdict="contradicted", support_span="not in the page", reason="r")

    client = _client(identities, claim_fn)
    result = claimcheck.check(
        client,
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        _sources(),
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    assert result.counts()["unchecked"] == 5
    assert {v.unchecked_reason for v in result.verdicts} == {"schema_violation"}
    assert claimcheck.issues_from(result) == []
    # The rejection reached the repair loop like any other validation failure.
    assert client.validation_errors


def test_a_verdict_that_quotes_the_page_is_accepted_case_and_space_insensitively(identities):
    def claim_fn(alias, user):
        return ClaimVerdict(
            verdict="contradicted",
            support_span="Projected  end-to-end efficiency is about 60%",
            reason="the page says 60%",
        )

    client = _client(identities, claim_fn)
    result = claimcheck.check(
        client,
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        [page("https://example.test/emals", EMALS_PAGE)],
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    counts = result.counts()
    # Three pairs cite [1]; the two [2] pairs have no page and are unchecked.
    assert (counts["contradicted"], counts["unchecked"]) == (3, 2)
    assert {v.unchecked_reason for v in result.verdicts if not v.checked} == {"page_not_read"}


def test_a_failed_call_leaves_the_pair_unchecked_and_an_account_refusal_propagates(identities):
    def boom(alias, user):
        raise ModelCallError("down", failure_class="timeout")

    result = claimcheck.check(
        _client(identities, boom),
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        _sources(),
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    assert result.counts()["unchecked"] == 5
    assert {v.unchecked_reason for v in result.verdicts} == {"timeout"}

    def refused(alias, user):
        raise ProviderAccountError("402")

    with pytest.raises(ProviderAccountError):
        claimcheck.check(
            _client(identities, refused),
            "evidence-spec",
            identities["evidence-spec"],
            REPORT,
            _sources(),
            page_max_chars=30_000,
            max_pairs=200,
            max_tokens=800,
            repair_retries=0,
        )


def test_consecutive_failures_abort_the_pass_instead_of_timing_out_per_pair(identities):
    """One dead proxy must cost a few timeouts inside the critic's slot, not `max_pairs`
    of them each with its own retry budget (D-claim-check-inconclusive-verdicts). A
    verdict in between resets the streak, because a streak is the signal and a lone
    failure is not."""
    calls: list[str] = []
    script = iter(["fail", "ok", "fail", "fail", "never reached"])

    def flaky(alias, user):
        step = next(script)
        calls.append(step)
        if step == "fail":
            raise ModelCallError("down", failure_class="timeout")
        return _supported(user)

    result = claimcheck.check(
        _client(identities, flaky),
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        _sources(),
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
        max_consecutive_failures=2,
    )
    assert calls == ["fail", "ok", "fail", "fail"]
    reasons = [v.unchecked_reason for v in result.verdicts]
    assert reasons == ["timeout", None, "timeout", "timeout", "aborted"]
    counts = result.counts()
    assert (counts["checked"], counts["unchecked"], counts["aborted"]) == (1, 4, 1)
    assert claimcheck.issues_from(result) == []


def test_a_page_cut_at_the_fetch_cap_is_never_shown_whole(identities):
    """A body that fits `page_max_chars` is not the page when the fetch stopped at its
    byte cap or a PDF at its page cap. Absence from what survived is not absence from
    the page, so `absent` mints nothing — and the checker is told the page goes on."""
    seen: list[str] = []

    def absent(alias, user):
        seen.append(user)
        return ClaimVerdict(verdict="absent", reason="not in what I was shown")

    cut = FetchedSource(url="https://example.test/emals", text=EMALS_PAGE, status=200, truncated=True)
    result = claimcheck.check(
        _client(identities, absent),
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        [cut, page("https://example.test/fleet", FLEET_PAGE)],
        page_max_chars=30_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    emals = [v for v in result.verdicts if v.pair.url == cut.url]
    assert emals and all(v.verdict == "absent" and not v.complete for v in emals)
    fleet = [v for v in result.verdicts if v.pair.url != cut.url]
    assert fleet and all(v.complete for v in fleet)
    # The whole fleet page mints; the cut EMALS page mints nothing.
    assert {i.citation_id for i in claimcheck.issues_from(result)} == {"2"}
    assert result.counts()["absent_partial"] == len(emals)
    assert any("continues past the last character retained" in u for u in seen)


def test_the_cache_returns_an_unchanged_pair_without_a_call_and_is_keyed_on_identity(identities):
    calls = []

    def claim_fn(alias, user):
        calls.append(alias)
        return _supported(user)

    cache = claimcheck.VerdictCache()
    kwargs = dict(page_max_chars=30_000, max_pairs=200, max_tokens=800, repair_retries=0, cache=cache)
    client = _client(identities, claim_fn)
    first = claimcheck.check(client, "evidence-spec", "vendor-d/evidence", REPORT, _sources(), **kwargs)
    second = claimcheck.check(client, "evidence-spec", "vendor-d/evidence", REPORT, _sources(), **kwargs)
    assert first.counts()["cached"] == 0
    assert second.counts()["cached"] == 5
    assert len(calls) == 5
    # A different identity forms its own view (QP2): nothing is shared across families.
    other = claimcheck.check(client, "writer-b", "vendor-b/model-b", REPORT, _sources(), **kwargs)
    assert other.counts()["cached"] == 0
    assert len(calls) == 10
    # A changed sentence also changes the prompt context of its paragraph peer.
    changed = REPORT.replace("roughly 90%", "roughly 95%")
    third = claimcheck.check(client, "evidence-spec", "vendor-d/evidence", changed, _sources(), **kwargs)
    assert third.counts()["cached"] == 3


def test_the_cache_misses_when_only_the_surrounding_paragraph_changes(identities):
    calls = []

    def claim_fn(alias, user):
        calls.append(alias)
        return _supported(user)

    cache = claimcheck.VerdictCache()
    kwargs = dict(page_max_chars=30_000, max_pairs=200, max_tokens=800, repair_retries=0, cache=cache)
    client = _client(identities, claim_fn)
    claimcheck.check(client, "evidence-spec", "vendor-d/evidence", REPORT, _sources(), **kwargs)
    changed = REPORT.replace(
        "## Conclusion\n\n", "## Conclusion\n\nThe comparison scope is unchanged. "
    )
    second = claimcheck.check(client, "evidence-spec", "vendor-d/evidence", changed, _sources(), **kwargs)
    assert second.counts()["cached"] == 3
    assert len(calls) == 7


def test_a_page_shown_in_part_never_makes_absence_a_finding(identities):
    long_page = page("https://example.test/emals", ("filler sentence. " * 400) + EMALS_PAGE)
    result = claimcheck.check(
        _client(identities, lambda a, u: ClaimVerdict(verdict="absent", reason="not here")),
        "evidence-spec",
        identities["evidence-spec"],
        REPORT,
        [long_page],
        page_max_chars=2_000,
        max_pairs=200,
        max_tokens=800,
        repair_retries=0,
    )
    assert result.counts()["absent_partial"] == 3
    assert claimcheck.issues_from(result) == []


# ------------------------------------------------------------------ config


def test_the_checker_is_off_by_default_and_requires_verification(roster):
    assert Config(roster=roster).claim_check.enabled is False
    with pytest.raises(ConfigError, match="claim_check.enabled requires search.verify_sources"):
        Config(roster=roster, claim_check={"enabled": True})
    cfg = Config(
        roster=roster,
        search={"enabled": False, "verify_sources": True},
        claim_check={"enabled": True},
    )
    assert cfg.claim_check.enabled and cfg.search.verify_sources


@pytest.mark.parametrize(
    "field,value",
    [("page_max_chars", 1_000), ("max_pairs", 0), ("max_tokens", 100)],
)
def test_claim_check_config_bounds(field, value):
    with pytest.raises(ValidationError):
        ClaimCheckConfig(**{field: value})


# ------------------------------------------------------------------ the lens


def _runtime(tmp_path, identities, config, client, fetcher):
    return Runtime(
        config=config,
        client=client,
        identities=identities,
        store=RunStore(tmp_path, "run-claims"),
        fetcher=fetcher,
    )


def _enabled(config):
    return config.model_copy(
        update={
            "search": SearchConfig(enabled=False, verify_sources=True),
            "claim_check": config.claim_check.model_copy(update={"enabled": True}),
        }
    )


def _events(tmp_path):
    lines = (tmp_path / "run-claims" / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_the_evidence_lens_carries_the_checkers_findings_and_reports_counts(tmp_path, identities, config):
    def claim_fn(alias, user):
        if "roughly 90%" in user.split("THE PARAGRAPH")[0]:
            return ClaimVerdict(verdict="contradicted", support_span="is about 60%", reason="page says 60%")
        return _supported(user)

    def critic(alias, user):
        # The critic's own misrepresentation finding on the checked sentence is superseded;
        # its uncited finding elsewhere stands.
        return CritiqueOutput(
            issues=[
                _critic_issue(1, 1, "projected to reach roughly 90%", citation_id="1"),
                _critic_issue(2, 1, "Launch variability is lower on EMALS", category=Category.UNCITED_CLAIM),
            ]
        )

    client = _client(identities, claim_fn, critic)
    pages = {s.url: s for s in _sources()}
    rt = _runtime(tmp_path, identities, _enabled(config), client, _Pages(pages))
    result = _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", REPORT, "h" * 64, "vendor-a/model-a", attempt=1
    )
    assert not result.failed
    by_cat = sorted((i.category.value, i.claim_span[:20]) for i in result.issues)
    assert by_cat == [
        ("misrepresented_source", "EMALS is projected t"),
        ("uncited_claim", "Launch variability i"),
    ]
    minted = next(i for i in result.issues if i.category is Category.MISREPRESENTED_SOURCE)
    assert minted.related_span == "is about 60%"
    assert triage.clean_records([result]) == []

    [event] = [e for e in _events(tmp_path) if e["kind"] == "claim_check"]
    assert event["critic"] == "vendor-d/evidence"
    assert (event["pairs"], event["checked"], event["contradicted"], event["supported"]) == (5, 5, 1, 4)
    # Counts only on the trail; the sentences and spans live in the critiques directory.
    assert "sentence" not in json.dumps(event)
    [record] = list((tmp_path / "run-claims" / "critiques").glob("*-claims-*.json"))
    assert "is about 60%" in record.read_text()
    # The checker's calls are fresh contexts: one per pair, before the critic's review.
    kinds = [c.schema for c in client.calls]
    assert kinds == ["ClaimVerdict"] * 5 + ["CritiqueOutput"]


def test_a_supported_bibliography_and_a_clean_critic_clear_the_lens(tmp_path, identities, config):
    client = _client(identities, lambda a, u: _supported(u))
    rt = _runtime(tmp_path, identities, _enabled(config), client, _Pages({s.url: s for s in _sources()}))
    result = _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", REPORT, "h" * 64, "vendor-a/model-a", attempt=1
    )
    assert result.issues == []
    assert len(triage.clean_records([result])) == 1


def test_an_account_refusal_during_checking_fails_the_lens_with_the_402_class(tmp_path, identities, config):
    def refused(alias, user):
        raise ProviderAccountError("402")

    client = _client(identities, refused)
    rt = _runtime(tmp_path, identities, _enabled(config), client, _Pages({s.url: s for s in _sources()}))
    result = _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", REPORT, "h" * 64, "vendor-a/model-a", attempt=1
    )
    assert result.failed and result.failure_class == "http_402"
    # The larger review call was never spent.
    assert [c.schema for c in client.calls] == ["ClaimVerdict"]


def test_off_by_default_the_lens_makes_no_checker_call_and_writes_no_event(tmp_path, identities, config):
    client = _client(identities, claim_fn=None)
    cfg = config.model_copy(update={"search": SearchConfig(enabled=False, verify_sources=True)})
    rt = _runtime(tmp_path, identities, cfg, client, _Pages({s.url: s for s in _sources()}))
    result = _critique_one(
        rt, Lens.EVIDENCE, "evidence-spec", "q?", REPORT, "h" * 64, "vendor-a/model-a", attempt=1
    )
    assert not result.failed
    assert [c.schema for c in client.calls] == ["CritiqueOutput"]
    assert not [e for e in _events(tmp_path) if e["kind"] == "claim_check"]


def test_only_the_evidence_lens_checks_claims(tmp_path, identities, config):
    client = _client(identities, claim_fn=None)
    rt = _runtime(tmp_path, identities, _enabled(config), client, _Pages({s.url: s for s in _sources()}))
    result = _critique_one(
        rt, Lens.LOGIC, "logic-spec", "q?", REPORT, "h" * 64, "vendor-a/model-a", attempt=1
    )
    assert not result.failed
    assert [c.schema for c in client.calls] == ["CritiqueOutput"]
