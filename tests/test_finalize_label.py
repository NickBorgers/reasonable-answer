"""The finalize label states the run's actual sourcing posture (D22).

The label in `final.json` is a user- and audit-facing claim about how the report's
citations were grounded — the recalled-vs-retrieved-vs-verified distinction that
docs/convergence.md exists to protect. Each arm of the conditional is pinned here,
including the precedence of `verify_sources` over `search_enabled`, so a regression
that mislabels the posture fails a test instead of shipping a false claim.
"""

from __future__ import annotations

import json

from fakes import FakeClient

import reasonable_answer.graph as graph

REPORT = "# Answer\n\nA claim [1].\n\n## Sources\n\n[1] A source.\n"


def _finalize_summary(config, identities, run_id, *, searcher=None, fetcher=None) -> dict:
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
    state = {"run_id": run_id, "terminal_status": "accepted", "report": REPORT}
    graph._finalize(state, rt)
    return json.loads((config.runs_dir / run_id / "final.json").read_text())


def test_no_retrieval_labels_in_artifact_sourcing(config, identities):
    summary = _finalize_summary(config, identities, "run-label-plain")
    assert summary["label"] == "consensus-reviewed with in-artifact sourcing (no external retrieval)"


def test_search_without_verification_labels_retrieved_sourcing(config, identities):
    summary = _finalize_summary(config, identities, "run-label-search", searcher=object())
    assert summary["label"] == "consensus-reviewed with retrieved sourcing"


def test_source_verification_labels_verified_sourcing(config, identities):
    summary = _finalize_summary(config, identities, "run-label-verify", fetcher=object())
    assert summary["label"] == "consensus-reviewed with verified sourcing"


def test_verification_outranks_retrieval_in_the_label(config, identities):
    """Both on — the shipped posture. Verification is the stronger claim and must win."""
    summary = _finalize_summary(
        config, identities, "run-label-both", searcher=object(), fetcher=object()
    )
    assert summary["label"] == "consensus-reviewed with verified sourcing"
