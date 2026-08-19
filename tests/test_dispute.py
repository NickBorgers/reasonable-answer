"""The dispute channel (D-writer-disputes): mechanical adjudication, arbiter eligibility,
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
from reasonable_answer import fetch, prompts, triage
from reasonable_answer.config import (
    Budgets,
    Config,
    DisputeConfig,
    Roster,
    validate_roster_health,
)
from reasonable_answer.fetch import FetchedSource
from reasonable_answer.graph import Runtime, _adjudicate, _critique, _generate, _triage, run
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
from reasonable_answer.store import RunStore
from reasonable_answer.taxonomy import Category, Lens, Severity

REPORT = """# Answer

The senator launched a re-election campaign in September 2025 [1].

## Sources

[1] https://example.com/campaign-launch
"""

SPAN = "The senator launched a re-election campaign in September 2025"

# `adjudicate_mechanical` takes a citation *set*, not report text (D-dispute-evidence-prior-draft):
# the set the draft a finding was RAISED against would produce. `REPORT` stands in for that
# prior draft throughout this file's mechanical-adjudication tests.
REPORT_SOURCES = fetch.extract_source_urls(REPORT)


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
    assert (
        dispute_mod.adjudicate_mechanical(make_dispute(), make_defect(), REPORT_SOURCES, fetcher)
        is True
    )


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
    assert dispute_mod.adjudicate_mechanical(dispute, defect, REPORT_SOURCES, fetcher) is None


def test_mechanical_is_inconclusive_for_a_url_only_the_writers_own_revision_added():
    """The whole point of D-dispute-evidence-prior-draft: a URL absent from the citation
    set passed in is never upheld, no matter how well the fetched page's text matches the
    evidence quote. The caller is responsible for passing the PRIOR draft's citations,
    never the disputing writer's own revision — this pins the function's contract."""
    fetcher = FakeFetcher(
        pages={"https://elsewhere.example/writer-added-source": GOOD_PAGE}
    )
    dispute = make_dispute(url="https://elsewhere.example/writer-added-source")
    assert (
        dispute_mod.adjudicate_mechanical(dispute, make_defect(), REPORT_SOURCES, fetcher)
        is None
    )
    # never even reached the fetch: the URL was excluded before any I/O happened
    assert fetcher.fetches == []


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
    """Byte-identity with a pre-D-writer-disputes build: the field appears only when true."""
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

    # A real MalformedOutputError message is built from schema-validation text that
    # echoes the REJECTED INPUT — the writer's dispute grounds and evidence quotes,
    # which are report-derived (private) content. This sentinel stands in for that
    # leaked span text: sec-audit-privacy-1 requires it never reach events.jsonl,
    # which `ra purge --content-only` intentionally RETAINS.
    LEAKED_SPAN = "SECRET-DISPUTE-GROUNDS-the-senator-privately-said-x"

    def broken(_alias, _user):
        raise MalformedOutputError(f"alias: schema violation after repair: {LEAKED_SPAN}")

    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=false_positive_critic,
        report_fn=lambda n: REPORT,
        dispute_fn=broken,
    )
    result = run(make_config(tmp_path), "Did the senator launch a campaign?", client=client)
    # no adjudication happened, so the false positive stands — the status quo
    assert result["terminal_status"] in ("needs_human_review", "exhausted_unresolved")
    raw_events = (tmp_path / "runs" / result["run_id"] / "events.jsonl").read_text()
    # The retained audit log must never carry the rejected-input text.
    assert LEAKED_SPAN not in raw_events
    events = [json.loads(line) for line in raw_events.splitlines()]
    failed = [e for e in events if e["kind"] == "dispute_pass_failed"]
    assert failed, "the failure must still be recorded as a signal"
    for e in failed:
        # Only a non-content-bearing reason: the exception type name, never str(exc).
        assert e.get("error_type") == "MalformedOutputError"
        assert "reason" not in e
        assert all(LEAKED_SPAN not in str(v) for v in e.values())


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


def test_the_arbiters_reason_is_persisted_to_the_dispute_audit_record(tmp_path):
    """docs/isolation.md says the arbiter's `reason` 'goes to the audit store' — this
    pins that it actually does (it previously did not: nothing consumed
    `ArbiterVerdict.reason` at all), in the purgeable content dir, never in
    events.jsonl (RA-016)."""
    defect = make_defect(category=Category.OVERSTATED_CLAIM)  # not mechanical: straight to arbiter
    dispute = make_dispute()
    reason_text = "the cited page does not corroborate the writer's claim"

    def arbiter(_alias, _user):
        return ArbiterVerdict(dispute_upheld=False, reason=reason_text)

    config = make_config(tmp_path)
    client = FakeClient(
        identities=IDENTITIES,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
        arbiter_fn=arbiter,
    )
    store = RunStore(config.runs_dir, "run-reason-persist")
    rt = Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=store,
    )
    state = {
        "question": "Did the senator launch a campaign?",
        "report": REPORT,
        "defect_citation_scope": fetch.extract_source_urls(REPORT),
        "pending_disputes": _pending(defect, dispute),
        "adjudications": [],
        "dispute_budget_remaining": 3,
        "defect_provenance": {},
        "round": 2,
        "author_identity": "vendor-a/model-a",
    }

    _adjudicate(state, rt)

    dispute_files = list((store.dir / "disputes").iterdir())
    assert len(dispute_files) == 1
    payload = json.loads(dispute_files[0].read_text())
    assert payload["ruling"] == {"verdict": "overruled", "reason": reason_text}
    # never in events.jsonl — that survives a content-only purge and must carry
    # closed-vocabulary signal only, never report-derived or arbiter-authored prose
    events_text = (store.dir / "events.jsonl").read_text()
    assert reason_text not in events_text


# ------------------------------------------------- citation scope (D-dispute-evidence-prior-draft)

#: No eligible arbiter once the disputer and the raising critic are excluded — isolates
#: the assertions below to the citation-membership gate itself. If it ever mechanically
#: upheld the wrong URL, or fetched it to feed an arbiter, there is no fallback path here
#: to obscure that; an unexpected arbiter call (no `arbiter_fn`) also fails loudly.
NO_ARBITER_ROSTER = Roster(
    writers=["writer-a"],
    critics={"logic": ["crit"], "evidence": ["crit"], "completeness": ["crit"]},
)
NO_ARBITER_IDENTITIES = {"writer-a": "vendor-a/model-a", "crit": "vendor-b/model-b"}

#: A URL that exists only in the disputing writer's own revision — never in the prior
#: draft the finding was raised against.
NEW_URL = "https://elsewhere.example/writer-added-source"
REVISED_WITH_NEW_SOURCE = REPORT.replace(
    "[1] https://example.com/campaign-launch\n",
    f"[1] https://example.com/campaign-launch\n[2] {NEW_URL}\n",
)


def _pending(defect: Defect, dispute: Dispute) -> list[dict]:
    return [{"defect": defect.model_dump(mode="json"), "dispute": dispute.model_dump(mode="json")}]


def _provenance_for(defect: Defect) -> dict[str, list[str]]:
    key = dispute_mod.registry_key(defect.category, defect.claim_span)
    return {f"{key[0]}|{key[1]}": ["vendor-b/model-b"]}


def _no_arbiter_runtime(tmp_path, fetcher) -> Runtime:
    config = Config(
        roster=NO_ARBITER_ROSTER,
        budgets=Budgets(min_ticks=2, hard_cap=6),
        disputes=DisputeConfig(enabled=True, budget=6),
        runs_dir=tmp_path / "runs",
    )
    client = FakeClient(
        identities=NO_ARBITER_IDENTITIES,
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=lambda n: REPORT,
        # no arbiter_fn given: an unexpected arbiter call raises, so a bug that reached
        # one here would fail the test rather than pass it silently.
    )
    return Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=RunStore(config.runs_dir, "run-citation-scope"),
        fetcher=fetcher,
    )


def test_a_dispute_citing_a_url_only_the_revision_added_is_rejected_mechanically(tmp_path):
    """The finding D-dispute-evidence-prior-draft closes: a writer's revision adds a
    brand-new URL to its own '## Sources' and disputes a fabricated-citation finding by
    citing that URL as evidence. Gating the mechanical check and the arbiter
    evidence-fetch on the CURRENT (post-revision) report would let the writer certify its
    own dispute at a page the critics who raised the finding never saw. Gating both on
    `defect_citation_scope` — captured at the triage that minted the finding, before this
    revision existed — rejects it outright, and the URL is never fetched: no eligible
    arbiter remains once the raising critic is excluded, so the dispute is dismissed
    without any I/O ever reaching the URL the writer just added."""
    fetcher = FakeFetcher(pages={NEW_URL: GOOD_PAGE})
    defect = make_defect()
    dispute = make_dispute(url=NEW_URL, quote="the campaign launched in September 2025")

    rt = _no_arbiter_runtime(tmp_path, fetcher)
    state = {
        "question": "Did the senator launch a campaign?",
        "report": REVISED_WITH_NEW_SOURCE,  # the disputing writer's own revision
        "defect_citation_scope": fetch.extract_source_urls(REPORT),  # the PRIOR draft
        "pending_disputes": _pending(defect, dispute),
        "adjudications": [],
        "dispute_budget_remaining": 3,
        "defect_provenance": _provenance_for(defect),
        "round": 2,
        "author_identity": "vendor-a/model-a",
    }

    result = _adjudicate(state, rt)

    records = [AdjudicationRecord.model_validate(r) for r in result["adjudications"]]
    assert len(records) == 1
    assert records[0].verdict == "dismissed" and records[0].method == "no_eligible_arbiter"
    # never fetched: the citation gate excluded the URL before any I/O, at both the
    # mechanical check and the arbiter evidence-fetch gate
    assert fetcher.fetches == []


def test_a_dispute_citing_a_url_the_prior_draft_cited_still_upholds_mechanically(tmp_path):
    """Control: same shape, but the evidence URL is the one the PRIOR draft (and so the
    raising critic) actually cited. The mechanical path still upholds it — the fix
    narrows which draft's citations count, it does not disable the channel."""
    fetcher = FakeFetcher(pages={GOOD_PAGE.url: GOOD_PAGE})
    defect = make_defect()
    dispute = make_dispute()  # defaults to https://example.com/campaign-launch

    rt = _no_arbiter_runtime(tmp_path, fetcher)
    state = {
        "question": "Did the senator launch a campaign?",
        "report": REPORT,  # unrevised in this fixture
        "defect_citation_scope": fetch.extract_source_urls(REPORT),
        "pending_disputes": _pending(defect, dispute),
        "adjudications": [],
        "dispute_budget_remaining": 3,
        "defect_provenance": _provenance_for(defect),
        "round": 2,
        "author_identity": "vendor-a/model-a",
    }

    result = _adjudicate(state, rt)

    records = [AdjudicationRecord.model_validate(r) for r in result["adjudications"]]
    assert len(records) == 1
    assert records[0].verdict == "upheld" and records[0].method == "mechanical"
    assert fetcher.fetches == [GOOD_PAGE.url]


def test_a_missing_citation_scope_fails_closed_never_treated_as_everything_cited(tmp_path):
    """`defect_citation_scope` is read with `state.get(...) or []` in `_adjudicate` — the
    shape a checkpoint written before this field existed would resume with. That fallback
    must fail CLOSED: an empty scope must never be read as "every URL is cited", it must
    read as "no URL is cited" — otherwise a resumed old run would be *more* permissive
    than a fresh one, upholding disputes a fresh run correctly rejects. Same evidence URL
    as the control test above (one the report genuinely cites) to isolate exactly this:
    the only difference from that passing control is the missing key, and here it must
    fall through to inconclusive rather than raise or auto-uphold."""
    fetcher = FakeFetcher(pages={GOOD_PAGE.url: GOOD_PAGE})
    defect = make_defect()
    dispute = make_dispute()  # cites https://example.com/campaign-launch — genuinely valid

    rt = _no_arbiter_runtime(tmp_path, fetcher)
    state = {
        "question": "Did the senator launch a campaign?",
        "report": REPORT,
        # no "defect_citation_scope" key at all
        "pending_disputes": _pending(defect, dispute),
        "adjudications": [],
        "dispute_budget_remaining": 3,
        "defect_provenance": _provenance_for(defect),
        "round": 2,
        "author_identity": "vendor-a/model-a",
    }

    result = _adjudicate(state, rt)  # must not raise

    records = [AdjudicationRecord.model_validate(r) for r in result["adjudications"]]
    assert len(records) == 1
    # inconclusive, not mechanically upheld, and not silently swallowed either — it falls
    # all the way through to the (absent, in this fixture) arbiter and is dismissed
    assert records[0].method != "mechanical"
    assert records[0].verdict != "upheld"
    assert records[0].verdict == "dismissed" and records[0].method == "no_eligible_arbiter"
    # never fetched: an empty/missing scope excludes every URL, including a genuinely
    # cited one — "missing" is never "everything is cited"
    assert fetcher.fetches == []


def test_adjudicate_mechanical_treats_a_missing_scope_as_nothing_cited(tmp_path):
    """The same fallback, pinned at the function `_adjudicate` delegates to: an empty
    collection (what `state.get("defect_citation_scope") or []` produces for a resumed
    old checkpoint) upholds nothing, for any URL, mechanical category or not."""
    fetcher = FakeFetcher(pages={GOOD_PAGE.url: GOOD_PAGE})
    assert (
        dispute_mod.adjudicate_mechanical(make_dispute(), make_defect(), [], fetcher) is None
    )
    assert fetcher.fetches == []


# ----------------------------------------------- _triage captures the citing draft's scope


def _triage_runtime(tmp_path, report_fn) -> Runtime:
    config = make_config(tmp_path)
    client = FakeClient(
        identities=IDENTITIES,
        # Empty on purpose: this is about `defect_citation_scope`, which `_triage` sets
        # unconditionally from `state["report"]`, independent of whether any issue was
        # raised. Keeping critique clean also keeps `_elicit_disputes` a no-op on the
        # next `_generate` (it requires non-empty `defects`), so nothing here reaches for
        # the dispute channel this section is not testing.
        critique_fn=lambda a, u: CritiqueOutput(issues=[]),
        report_fn=report_fn,
    )
    return Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=RunStore(config.runs_dir, "run-triage-scope"),
    )


def test_triage_captures_the_citation_scope_of_the_draft_it_just_critiqued(tmp_path):
    """Direct coverage for `_triage`'s new line: drives real `_generate` -> `_critique` ->
    `_triage` calls for two rounds, so `defect_citation_scope` comes from the actual node
    rather than being hand-set on a fixture. Round 2's report adds a URL round 1's did
    not; the assertion is that each round's captured scope tracks the draft *that round's
    triage actually critiqued* — round 1's never sees the URL round 2 adds, and round 2's
    does — which is exactly what `_adjudicate`'s prior-draft gate depends on
    (D-dispute-evidence-prior-draft)."""
    report_by_round = {1: REPORT, 2: REVISED_WITH_NEW_SOURCE}
    rt = _triage_runtime(tmp_path, lambda n: report_by_round.get(n, REVISED_WITH_NEW_SOURCE))

    state: dict = {"question": "Did the senator launch a campaign?", "round": 0}

    # round 1: a real generate -> critique -> triage cycle over REPORT
    # (`_generate` strips the completion, so compare stripped — the writer text is
    # otherwise unchanged)
    state = {**state, **_generate(state, rt)}
    assert state["report"] == REPORT.strip()
    state = {**state, **_critique(state, rt)}
    triage1 = _triage(state, rt)
    assert triage1["defect_citation_scope"] == fetch.extract_source_urls(REPORT)
    assert NEW_URL not in triage1["defect_citation_scope"]
    state = {**state, **triage1}

    # round 2: another real cycle, now over the revision that adds NEW_URL
    state = {**state, **_generate(state, rt)}
    assert state["report"] == REVISED_WITH_NEW_SOURCE.strip()
    state = {**state, **_critique(state, rt)}
    triage2 = _triage(state, rt)
    assert triage2["defect_citation_scope"] == fetch.extract_source_urls(REVISED_WITH_NEW_SOURCE)
    assert NEW_URL in triage2["defect_citation_scope"]

    # the two scopes differ — round 1's is not silently carried forward or stale
    assert triage1["defect_citation_scope"] != triage2["defect_citation_scope"]
