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

import logging

from pydantic import ValidationError

from . import prompts, triage
from . import report as report_mod
from .llm import LLMClient, MalformedOutputError, ModelCallError
from .schemas import CritiqueOutput, LensResult
from .taxonomy import Lens

log = logging.getLogger(__name__)

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
    current_date: str | None = None,
    source_char_budget: int | None = None,
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

    def repair_turn(*, user: str, instruction: str, error: str, exc: Exception) -> str:
        """Compose the critic's re-ask (D-repair-turn-context).

        The duck-typing is deliberate and lives here rather than in `llm`: `triage` is
        LLM-free and must not import the client, and the client must not import `triage`
        to name these. `critique` already depends on both, so it is the one place that can
        read the error and hand plain strings to `prompts`.
        """
        hint = getattr(exc, "repair_hint", None)
        rejected = getattr(exc, "rejected_text", None)
        return prompts.critic_repair_turn(
            user=user,
            instruction=instruction,
            error=error,
            guidance=hint() if callable(hint) else "",
            rejected=rejected() if callable(rejected) else "",
        )

    def validate(output: CritiqueOutput) -> None:
        for index, issue in enumerate(output.issues):
            try:
                triage.validate_issue(lens, issue, structure, require_verbatim_spans)
            except triage.LensValidationError as exc:
                # `validate_issue` sees one issue and cannot know where it sat in the
                # response. Which issue of how many failed is what separates a critic
                # stuck on a single bad span from one whose whole response is unanchored.
                exc.at_issue(index, of=len(output.issues))
                raise

    try:
        output = client.structured(
            alias,
            system=prompts.CRITIC_SYSTEM,
            user=prompts.critic_user(
                lens,
                question,
                rendered,
                sources,
                current_date=current_date,
                source_char_budget=source_char_budget,
            ),
            schema=CritiqueOutput,
            max_tokens=CRITIC_MAX_TOKENS,
            # Validated *inside* the call so a quoting slip is repaired against the
            # paragraph it misquoted, rather than failing the lens and costing a whole
            # critique attempt. The fail-closed contract is unchanged: once the repair
            # budget is gone the violation still fails the lens below, and one bad field
            # still fails the whole lens — nothing is silently dropped.
            repair_retries=client.budgets.critic_repair_retries,
            validate=validate,
            repair_prompt=repair_turn,
        )
    except (ModelCallError, MalformedOutputError, ValidationError) as exc:
        reason = str(exc)[:400]
        log.warning(
            "lens %s failed on %s (critic %s): %s", lens.value, artifact_hash[:12], alias, reason
        )
        return base.model_copy(update={"failed": True, "failure_reason": reason})

    return base.model_copy(update={"issues": output.issues})
