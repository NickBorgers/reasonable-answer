"""The finalize label states what the run *observed*, not what was configured
(D-run-date-grounding, D-observed-source-coverage).

The label in `final.json` is a user- and audit-facing claim about how the report's
citations were grounded — the recalled-vs-retrieved-vs-verified distinction that
docs/convergence.md exists to protect. Each arm is pinned here so a regression that
mislabels the posture fails a test instead of shipping a false claim.

The verification arm no longer names a posture at all. `verify_sources: true` used to
produce "consensus-reviewed with verified sourcing" for a run that could address three of
fifteen cited entries; it now states the coverage it measured on the draft it shipped, so
the pinned strings below are the arithmetic, not an adjective.
"""

from __future__ import annotations

import json

from fakes import FakeClient

import reasonable_answer.graph as graph

REPORT = "# Answer\n\nA claim [1].\n\n## Sources\n\n[1] A source.\n"


def _finalize_summary(
    config, identities, run_id, *, searcher=None, fetcher=None, state=None
) -> dict:
    client = FakeClient(
        identities=identities,
        critique_fn=lambda a, u: None,
        report_fn=lambda n: REPORT,
    )
    rt = graph.Runtime(
        config=config,
        client=client,
        identities=client.resolve_identities(config.roster.all_aliases),
        store=graph.RunStore(config.runs_dir, run_id),
        searcher=searcher,
        fetcher=fetcher,
    )
    graph._finalize(
        {"run_id": run_id, "terminal_status": "accepted", "report": REPORT, **(state or {})}, rt
    )
    return json.loads((config.runs_dir / run_id / "final.json").read_text())


def test_no_retrieval_labels_in_artifact_sourcing(config, identities):
    summary = _finalize_summary(config, identities, "run-label-plain")
    assert summary["label"] == "consensus-reviewed with in-artifact sourcing (no external retrieval)"


def test_search_without_verification_labels_retrieved_sourcing(config, identities):
    summary = _finalize_summary(config, identities, "run-label-search", searcher=object())
    assert summary["label"] == "consensus-reviewed with retrieved sourcing"


def test_verification_label_states_the_measured_coverage(config, identities):
    """The issue's own worked example: fifteen cited, three of them checkable."""
    coverage = {
        "cited": 15,
        "addressable": 3,
        "not_addressable": 12,
        "attempted": 3,
        "not_attempted": 0,
        "bodies_read": 3,
        "metadata_only": 0,
        "blocked_or_unreadable": 0,
        "not_found": 0,
        "budget_exhausted": 0,
        "existence_confirmed": 3,
        "not_independently_checked": 12,
        "verification_enabled": True,
    }
    summary = _finalize_summary(
        config,
        identities,
        "run-label-verify",
        fetcher=object(),
        state={"source_coverage": {graph.report_mod.artifact_hash(REPORT): coverage}},
    )
    assert summary["label"] == (
        "consensus-reviewed — source review: 15 cited; 3 addressable; "
        "3 existence confirmed; 3 bodies read; 12 not independently checked"
    )
    assert "verified sourcing" not in summary["label"]
    assert summary["source_coverage"] == coverage


def test_verification_with_no_measurement_says_so(config, identities):
    """An absent measurement must never read as a passing one — so no fallback to the
    old categorical label when verification ran but recorded nothing."""
    summary = _finalize_summary(config, identities, "run-label-unmeasured", fetcher=object())
    assert summary["label"] == (
        "consensus-reviewed; source verification was enabled but no coverage was "
        "recorded for the shipped draft"
    )
    assert summary["source_coverage"] is None


def test_verification_outranks_retrieval_in_the_label(config, identities):
    """Both on — the shipped posture. Verification is the stronger claim and must win,
    and with verification on the label is always the observed coverage."""
    summary = _finalize_summary(
        config, identities, "run-label-both", searcher=object(), fetcher=object()
    )
    assert summary["label"].startswith("consensus-reviewed;")
    assert "retrieved sourcing" not in summary["label"]
