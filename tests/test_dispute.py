"""The dispute channel (D25): mechanical adjudication, arbiter eligibility,
registry semantics, suppression, and the end-to-end loop behaviour.

The load-bearing properties: adjudication fails closed toward the finding on
every inconclusive path; the arbiter never learns an identity, a lens, or a
round; nothing is ever suppressed without an explicit `upheld` record; and with
the channel off the pipeline is byte-identical to a build without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fakes import FakeClient
from pydantic import ValidationError

from reasonable_answer import dispute as dispute_mod
from reasonable_answer import prompts, triage
from reasonable_answer.config import (
    Budgets,
    Config,
    DisputeConfig,
    Roster,
    validate_roster_health,
)
from reasonable_answer.fetch import FetchedSource
from reasonable_answer.graph import run
from reasonable_answer.schemas import (
    AdjudicationRecord,
    ArbiterVerdict,
    CritiqueOutput,
    Defect,
    Dispute,
    LensResult,
    RawIssue,
    StructuralRef,
    WriterDisputes,
)
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """# Answer

The senator launched a re-election campaign in September 2025 [1].

## Sources

[1] https://example.com/campaign-launch
"""

SPAN = "The senator launched a re-election campaign in September 2025"


def make_defect(category=Category.FABRICATED_CITATION, span=SPAN, adjudicated=False) -> Defect:
    return Defect(
        locus=StructuralRef(section=1, paragraph=1),
        category=category,
        severity=Severity.BLOCKING,
        claim_span=span,
        rationale="the cited launch date is in the future",
        instruction="correct the date to a factual historical date",
        adjudicated=adjudicated,
    )


def make_dispute(
    index=0,
    url="https://example.com/campaign-launch",
    quote="the campaign launched in September 2025",
) -> Dispute:
    return Dispute(
        task_index=index,
        grounds="The date is correct; the cited page states it.",
        evidence_url=url,
        evidence_quote=quote,
    )


@dataclass
class FakeFetcher:
    """`pages` maps url -> FetchedSource; anything else fails to fetch."""

    pages: dict[str, FetchedSource] = field(default_factory=dict)
    fetches: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> FetchedSource:
        self.fetches.append(url)
        return self.pages.get(url, FetchedSource(url=url, error="connection refused"))

    def fetch_all(self, urls):
        return [self.fetch(u) for u in urls]


GOOD_PAGE = FetchedSource(
    url="https://example.com/campaign-launch",
    title="Campaign launch",
    text="Confirmed: the campaign launched in September 2025 to a large crowd.",
)


# ------------------------------------------------------- mechanical adjudication


def test_mechanical_upholds_when_the_cited_page_contains_the_quote():
    fetcher = FakeFetcher(pages={GOOD_PAGE.url: GOOD_PAGE})
    assert dispute_mod.adjudicate_mechanical(make_dispute(), make_defect(), REPORT, fetcher) is True


@pytest.mark.parametrize(
    "dispute,defect,fetcher",
    [
        # no fetcher at all
        (make_dispute(), make_defect(), None),
        # not a mechanical category
        (make_dispute(), make_defect(category=Category.OVERSTATED_CLAIM), FakeFetcher()),
        # no evidence url
        (make_dispute(url=None), make_defect(), FakeFetcher()),
        # no evidence quote
        (make_dispute(quote=None), make_defect(), FakeFetcher()),
        # url is not one the report cites — a writer cannot point at an arbitrary page
        (
            make_dispute(url="https://elsewhere.example/corroboration"),
            make_defect(),
            FakeFetcher(
                pages={"https://elsewhere.example/corroboration": GOOD_PAGE}
            ),
        ),
        # fetch fails
        (make_dispute(), make_defect(), FakeFetcher()),
        # page fetched but the quote is absent (truncation means this is NOT refutation)
        (
            make_dispute(quote="a sentence the page does not contain"),
            make_defect(),
            FakeFetcher(pages={GOOD_PAGE.url: GOOD_PAGE}),
        ),
    ],
)
def test_mechanical_is_inconclusive_never_refuting(dispute, defect, fetcher):
    """Every non-upheld path returns None (fall through to the arbiter) — never
    False. Absence of evidence in a truncated page is not evidence of absence."""
    assert dispute_mod.adjudicate_mechanical(dispute, defect, REPORT, fetcher) is None


# ------------------------------------------------------------ dispute validation


def test_validate_disputes_drops_bad_indices_and_duplicates_and_clamps():
    defects = [make_defect(), make_defect(category=Category.UNCITED_CLAIM, span=SPAN)]
    raw = WriterDisputes(
        disputes=[
            make_dispute(index=0),
            make_dispute(index=0),  # duplicate
            make_dispute(index=7),  # out of range
            make_dispute(index=1),
        ]
    )
    accepted = dispute_mod.validate_disputes(raw, defects, max_per_pass=1)
    assert len(accepted) == 1
    assert accepted[0][1] is defects[0]


def test_validate_disputes_refuses_a_re_dispute_of_an_adjudicated_task():
    defects = [make_defect(adjudicated=True)]
    accepted = dispute_mod.validate_disputes(
        WriterDisputes(disputes=[make_dispute(index=0)]), defects, max_per_pass=3
    )
    assert accepted == []


# ---------------------------------------------------------- arbiter eligibility


def test_eligible_arbiters_excludes_disputer_and_raisers_at_identity_level():
    roster = Roster(
        writers=["writer-a", "writer-b"],
        critics={
            "logic": ["logic-spec"],
            "evidence": ["evidence-spec"],
            "completeness": ["completeness-spec"],
        },
    )
    identities = {
        "writer-a": "vendor-a/model-a",
        "writer-b": "vendor-b/model-b",
        "logic-spec": "vendor-c/logic",
        "evidence-spec": "vendor-d/evidence",
        "completeness-spec": "vendor-e/completeness",
    }
    arbiters = dispute_mod.eligible_arbiters(
        roster, identities, "vendor-a/model-a", {"vendor-d/evidence"}
    )
    picked = {identities[a] for a in arbiters}
    assert "vendor-a/model-a" not in picked
    assert "vendor-d/evidence" not in picked
    # critic-only specialists come first: they have no authorship stake anywhere
    assert identities[arbiters[0]] in {"vendor-c/logic", "vendor-e/completeness"}


def test_eligible_arbiters_dedupes_aliases_resolving_to_one_identity():
    roster = Roster(
        writers=["writer-a"],
        critics={
            "logic": ["crit-1", "crit-2"],
            "evidence": ["crit-1"],
            "completeness": ["crit-1"],
        },
    )
    # two aliases, one underlying model: they are one arbiter, not two
    identities = {
        "writer-a": "vendor-a/model-a",
        "crit-1": "vendor-x/same-model",
        "crit-2": "vendor-x/same-model",
    }
    arbiters = dispute_mod.eligible_arbiters(roster, identities, "vendor-a/model-a", set())
    assert len(arbiters) == 1


def test_no_eligible_arbiter_when_the_pair_covers_the_roster():
    roster = Roster(
        writers=["writer-a"],
        critics={"logic": ["crit"], "evidence": ["crit"], "completeness": ["crit"]},
    )
    identities = {"writer-a": "vendor-a/model-a", "crit": "vendor-b/model-b"}
    assert (
        dispute_mod.eligible_arbiters(
            roster, identities, "vendor-a/model-a", {"vendor-b/model-b"}
        )
        == []
    )


# ------------------------------------------------------- registry + suppression


def record(verdict: str, span=SPAN, category=Category.FABRICATED_CITATION) -> AdjudicationRecord:
    return AdjudicationRecord(
        category=category, claim_span=span, verdict=verdict, method="arbiter", round=2
    )


def lens_result(issues, lens=Lens.EVIDENCE, failed=False) -> LensResult:
    return LensResult(
        lens=lens,
        artifact_hash="h" * 64,
        critic_alias="critic",
        critic_identity="vendor-d/evidence",
        artifact_author_identity="vendor-a/model-a",
        failed=failed,
        issues=issues,
    )


def raw_issue(span=SPAN, category=Category.FABRICATED_CITATION) -> RawIssue:
    return RawIssue(
        category=category,
        severity=Severity.BLOCKING,
        locus=StructuralRef(section=1, paragraph=1),
        claim_span=span,
        rationale="the cited launch date is in the future",
        instruction="correct the date",
    )


def test_only_upheld_records_suppress():
    keys = dispute_mod.suppression_keys(
        [record("upheld"), record("overruled", span="other text"), record("dismissed", span="more")]
    )
    assert keys == {dispute_mod.registry_key(Category.FABRICATED_CITATION, SPAN)}


def test_suppression_is_consistent_across_tally_defects_and_clean_records():
    results = [lens_result([raw_issue()])]
    keys = dispute_mod.suppression_keys([record("upheld")])
    filtered, logged = triage.suppress(results, keys)

    _, totals = triage.tally(filtered)
    assert totals.blocking == 0 and totals.major == 0
    assert triage.to_defects(filtered) == []
    # the lens now minted a clean record: the suppressed finding no longer withholds it
    assert len(triage.clean_records(filtered)) == 1
    assert logged and logged[0]["category"] == "fabricated_citation"
    assert triage.signal_signature(triage.tally(filtered)[0]) == ()


def test_suppression_matching_survives_reformatting():
    """The registry key uses triage's quote normalization: markdown emphasis and
    whitespace changes must not re-open an adjudicated fact."""
    reformatted = raw_issue(span="The  senator launched a *re-election* campaign in September 2025")
    filtered, logged = triage.suppress(
        [lens_result([reformatted])],
        dispute_mod.suppression_keys([record("upheld")]),
    )
    assert filtered[0].issues == [] and len(logged) == 1


def test_suppression_never_touches_a_failed_lens():
    failed = lens_result([raw_issue()], failed=True)
    filtered, logged = triage.suppress([failed], dispute_mod.suppression_keys([record("upheld")]))
    assert filtered[0].failed and filtered[0].issues == failed.issues
    assert logged == []


def test_overruled_defects_come_back_marked_adjudicated():
    results = [lens_result([raw_issue()])]
    overruled = dispute_mod.overruled_keys([record("overruled")])
    defects = triage.to_defects(results, overruled)
    assert len(defects) == 1 and defects[0].adjudicated is True
    # ...and an unrelated defect is not marked
    other = triage.to_defects([lens_result([raw_issue(span="September 2025")])], overruled)
    assert other[0].adjudicated is False


def test_defect_provenance_maps_material_issues_to_raising_identities():
    prov = triage.defect_provenance([lens_result([raw_issue()])])
    key = dispute_mod.registry_key(Category.FABRICATED_CITATION, SPAN)
    assert prov == {f"{key[0]}|{key[1]}": ["vendor-d/evidence"]}


# ----------------------------------------------------------------- config layer


def test_dispute_config_bounds():
    with pytest.raises(ValidationError):
        DisputeConfig(budget=-1)
    with pytest.raises(ValidationError):
        DisputeConfig(max_per_pass=0)
    assert DisputeConfig().enabled is False


def test_unadjudicated_tasks_carry_no_adjudicated_key_at_all():
    """Byte-identity with a pre-D25 build: the field appears only when true."""
    prompt = prompts.writer_revision("q", REPORT, [make_defect()], polish=False)
    assert "adjudicated" not in prompt


def test_roster_health_warns_when_no_arbiter_can_exist(tmp_path):
    config = Config(
        roster=Roster(
            writers=["writer-a"],
            critics={"logic": ["crit"], "evidence": ["crit"], "completeness": ["crit"]},
        ),
        budgets=Budgets(min_ticks=2, hard_cap=5),
        disputes=DisputeConfig(enabled=True),
        runs_dir=tmp_path / "runs",
    )
    identities = {"writer-a": "vendor-a/model-a", "crit": "vendor-b/model-b"}
    warnings = validate_roster_health(config, identities)
    assert any("no arbiter identity" in w for w in warnings)
    # fail-open: the same roster with disputes off warns about nothing new
    config_off = config.model_copy(update={"disputes": DisputeConfig(enabled=False)})
    assert not any("arbiter" in w for w in validate_roster_health(config_off, identities))


# ------------------------------------------------------------------ off-by-default


def test_disputes_off_means_no_dispute_prompt_text_and_no_extra_calls():
    assert prompts.WRITER_DISPUTE_ADDENDUM not in prompts.writer_revision(
        "q", REPORT, [make_defect()], polish=False, disputes_enabled=False
    )
    assert prompts.WRITER_DISPUTE_ADDENDUM in prompts.writer_revision(
        "q", REPORT, [make_defect()], polish=False, disputes_enabled=True
    )
    # polish passes never invite disputes, even with the channel on
    assert prompts.WRITER_DISPUTE_ADDENDUM not in prompts.writer_revision(
        "q", REPORT, [make_defect()], polish=True, disputes_enabled=True
    )


# ------------------------------------------------------------------- end to end


ROSTER = Roster(
    writers=["writer-a", "writer-b"],
    critics={
        "logic": ["logic-spec", "writer-a", "writer-b"],
        "evidence": ["evidence-spec", "writer-a", "writer-b"],
        "completeness": ["completeness-spec", "writer-a", "writer-b"],
    },
)

IDENTITIES = {
    "writer-a": "vendor-a/model-a",
    "writer-b": "vendor-b/model-b",
    "logic-spec": "vendor-c/logic",
    "evidence-spec": "vendor-d/evidence",
    "completeness-spec": "vendor-e/completeness",
}


def make_config(tmp_path, enabled=True, budget=6) -> Config:
    return Config(
        roster=ROSTER,
        budgets=Budgets(min_ticks=2, hard_cap=6),
        disputes=DisputeConfig(enabled=enabled, budget=budget),
        runs_dir=tmp_path / "runs",
    )


def false_positive_critic(_alias, user) -> CritiqueOutput:
    """The run-75eb136b9bfb shape: the evidence lens re-raises the same blocking
    'future-dated fabrication' on every draft; the other lenses are clean."""
    if "YOUR DIMENSION: evidence" in user and SPAN in user:
        return CritiqueOutput(issues=[raw_issue()])
    return CritiqueOutput(issues=[])


def dispute_once(_alias, _user) -> WriterDisputes:
    return WriterDisputes(disputes=[make_dispute()])


def test_an_upheld_dispute_turns_a_stagnating_false_positive_into_acceptance(tmp_path):
    """The regression the channel exists for: without it this scenario stagnates to
    needs_human_review on a critic false positive; with it, one upheld dispute
    suppresses the re-raised finding and the run converges."""
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=dispute_once,
        arbiter_fn=lambda alias, user: ArbiterVerdict(
            dispute_upheld=True, reason="the cited page confirms the date"
        ),
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    assert result["terminal_status"] == "accepted"
    # the registry ruled exactly once; later identical disputes were free duplicates
    arbiter_calls = [c for c in client.calls if c.schema == "ArbiterVerdict"]
    assert len(arbiter_calls) == 1


def test_the_same_scenario_without_disputes_is_the_status_quo_failure(tmp_path):
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
    )
    result = run(make_config(tmp_path, enabled=False), "Did the senator launch a campaign?", client=client)
    assert result["terminal_status"] in ("needs_human_review", "exhausted_unresolved")


def test_an_overruled_dispute_marks_the_defect_and_the_writer_complies(tmp_path):
    fixed = REPORT.replace("September 2025", "September 2024")

    def report_fn(n: int) -> str:
        # drafts 1-2 keep the disputed text (each draft distinct, so the cycle
        # detector stays out of the way); from 3 on the writer complies
        if n == 1:
            return REPORT
        if n == 2:
            return REPORT + "\n\nMinor wording adjusted."
        return fixed

    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=report_fn,
        dispute_fn=dispute_once,
        arbiter_fn=lambda alias, user: ArbiterVerdict(
            dispute_upheld=False, reason="the evidence does not establish the date"
        ),
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    assert result["terminal_status"] == "accepted"
    # the round after the overruling, the writer saw the task marked adjudicated
    revisions = [c.user for c in client.calls if "FIX TASKS" in c.user and c.schema is None]
    assert any('"adjudicated": true' in u for u in revisions)


def test_a_malformed_dispute_pass_degrades_to_no_disputes(tmp_path):
    from reasonable_answer.llm import MalformedOutputError

    def broken(_alias, _user):
        raise MalformedOutputError("nonsense")

    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=broken,
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    # no adjudication happened, so the false positive stands — the status quo
    assert result["terminal_status"] in ("needs_human_review", "exhausted_unresolved")
    events = [
        json.loads(line)
        for line in (
            (tmp_path / "runs" / result["run_id"] / "events.jsonl").read_text().splitlines()
        )
    ]
    assert any(e["kind"] == "dispute_pass_failed" for e in events)


def test_a_failed_arbiter_leaves_the_finding_standing(tmp_path):
    from reasonable_answer.llm import ModelCallError

    def down(_alias, _user):
        raise ModelCallError("arbiter unavailable")

    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=dispute_once,
        arbiter_fn=down,
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    assert result["terminal_status"] in ("needs_human_review", "exhausted_unresolved")
    events = [
        json.loads(line)
        for line in (
            (tmp_path / "runs" / result["run_id"] / "events.jsonl").read_text().splitlines()
        )
    ]
    adjudications = [e for e in events if e["kind"] == "adjudication"]
    assert adjudications and all(
        e["verdict"] == "dismissed" and e["method"] == "arbiter_failed" for e in adjudications
    )


def test_the_dispute_budget_bounds_adjudication_spend(tmp_path):
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=dispute_once,
        arbiter_fn=lambda alias, user: ArbiterVerdict(
            dispute_upheld=False, reason="not refuted"
        ),
    )
    run(make_config(tmp_path, budget=0), "Did the senator launch a campaign?", client=client)
    # budget 0: every dispute dismissed before any arbiter call
    assert [c for c in client.calls if c.schema == "ArbiterVerdict"] == []


# ------------------------------------------------------------ arbiter isolation


def test_the_arbiter_prompt_carries_no_identity_lens_or_round(tmp_path):
    seen: list[tuple[str, str]] = []

    def spy_arbiter(alias, user):
        seen.append((alias, user))
        return ArbiterVerdict(dispute_upheld=True, reason="confirmed")

    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=dispute_once,
        arbiter_fn=spy_arbiter,
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    assert seen, "the scenario must actually reach an arbiter"
    for _alias, user in seen:
        for secret in (
            *IDENTITIES.values(),
            *IDENTITIES.keys(),
            "YOUR DIMENSION",
            result["run_id"],
            '"round"',
            "artifact_hash",
        ):
            assert secret not in user, f"arbiter prompt leaked {secret!r}"
        # the arbiter is never the disputing writer nor the raising critic
        assert IDENTITIES[_alias] not in ("vendor-d/evidence",)
        assert _alias not in ("evidence-spec",)


def test_arbiter_system_prompt_defaults_to_the_finding():
    assert "resolved in favor of the finding" in prompts.ARBITER_SYSTEM


# ------------------------------------------------------------------- the store


def test_dispute_content_lives_in_a_purgeable_dir(tmp_path):
    from reasonable_answer.store import RunStore, purge

    store = RunStore(tmp_path, "run-x")
    assert (store.dir / "disputes").stat().st_mode & 0o777 == 0o700
    store.dispute(2, 1, {"defect": {"claim_span": "secret text"}, "dispute": {"grounds": "g"}})
    store.event("adjudication", category="fabricated_citation", verdict="upheld", method="arbiter")

    purge(tmp_path, "run-x", content_only=True)
    # content-only purge empties and recreates the dir, same as reports/critiques
    assert list((store.dir / "disputes").iterdir()) == []
    # the signal record survives, and it never carried span text
    events = (store.dir / "events.jsonl").read_text()
    assert "adjudication" in events and "secret text" not in events
