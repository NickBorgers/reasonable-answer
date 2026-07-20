"""One lens, one artifact, one critic — the single call both the graph and the
audition harness go through.

This exists so there is exactly one place where a critic's prompt is built and its
output is validated. The audition harness measures whether a model can perform a lens;
that measurement is only meaningful if the harness exercises the *production* prompt
and the *production* fail-closed validation. A second, parallel implementation would
drift, and the drift would show up as a model that auditions well and reviews badly.

The caller supplies the alias. Roster eligibility (`roles.pick_critic`, author
exclusion) and source fetching stay with the caller, because the harness deliberately
does neither: it pins the critic under test and gives every model the same input.
"""

from __future__ import annotations

from pydantic import ValidationError

from . import prompts, triage
from . import report as report_mod
from .llm import LLMClient, MalformedOutputError, ModelCallError
from .schemas import CritiqueOutput, LensResult
from .taxonomy import Lens

#: Matches the graph's historical budget. A critic that needs more than this on a
#: report-sized artifact is not being truncated, it is looping.
CRITIC_MAX_TOKENS = 16000


def critique_once(
    client: LLMClient,
    alias: str,
    identity: str,
    lens: Lens,
    question: str,
    report_text: str,
    artifact_hash: str,
    author_identity: str,
    *,
    sources: list | None = None,
    require_verbatim_spans: bool = True,
    attempt: int = 1,
) -> LensResult:
    """Run one lens in a fresh context and return an audit-side `LensResult`.

    Failure is recorded as a **failed lens**, never as "no issues found" — a failed
    review can never manufacture a clean record. That distinction is what the harness
    reads to separate "this model cannot emit the schema" from "this model looked and
    saw nothing", which are different problems with different fixes.
    """
    base = LensResult(
        lens=lens,
        artifact_hash=artifact_hash,
        critic_alias=alias,
        critic_identity=identity,
        artifact_author_identity=author_identity,
        attempt=attempt,
    )

    rendered = report_mod.render_with_loci(report_text)
    structure = report_mod.parse(report_text)

    try:
        output = client.structured(
            alias,
            system=prompts.CRITIC_SYSTEM,
            user=prompts.critic_user(lens, question, rendered, sources),
            schema=CritiqueOutput,
            max_tokens=CRITIC_MAX_TOKENS,
        )
    except (ModelCallError, MalformedOutputError, ValidationError) as exc:
        return base.model_copy(update={"failed": True, "failure_reason": str(exc)[:400]})

    try:
        for issue in output.issues:
            triage.validate_issue(lens, issue, structure, require_verbatim_spans)
    except triage.LensValidationError as exc:
        # Fail-closed: one bad field fails the whole lens; nothing is silently dropped.
        return base.model_copy(update={"failed": True, "failure_reason": str(exc)[:400]})

    return base.model_copy(update={"issues": output.issues})
