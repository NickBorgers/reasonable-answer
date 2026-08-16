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
import secrets

from pydantic import ValidationError

from . import prompts, triage
from . import report as report_mod
from .llm import LLMClient, MalformedOutputError, ModelCallError
from .schemas import CritiqueOutput, IssueRepairs, LensResult
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

    def validate(output: CritiqueOutput) -> None:
        for index, issue in enumerate(output.issues):
            try:
                triage.validate_issue(lens, issue, structure, require_verbatim_spans)
            except triage.LensValidationError as exc:
                # `validate_issue` sees one issue and cannot know where it sat in the
                # response. Which issue of how many failed is what separates a critic
                # stuck on a single bad span from one whose whole response is unanchored,
                # and the patch schema needs the index to address the field at all.
                exc.at_issue(index, of=len(output.issues))
                raise

    user = prompts.critic_user(
        lens,
        question,
        rendered,
        sources,
        current_date=current_date,
        source_char_budget=source_char_budget,
    )

    try:
        # One budget governs both halves of a critic's repair, as isolation.md and
        # convergence.md say it does. `critic_repair_retries` is passed here for *schema*
        # violations — malformed JSON, a missing field — and again to the lens loop below.
        # Omitting it silently dropped the schema half to the generic `repair_retries`
        # (1 against 2), which is a budget change nobody asked for and no document
        # described. Lens validation is repaired below rather than here only because a
        # lens rejection is answered with a patch, which needs a different response schema
        # than this call's (D-repair-turn-context).
        output = client.structured(
            alias,
            system=prompts.CRITIC_SYSTEM,
            user=user,
            schema=CritiqueOutput,
            max_tokens=CRITIC_MAX_TOKENS,
            repair_retries=client.budgets.critic_repair_retries,
        )
        output = _repair_until_valid(
            client,
            alias,
            user=user,
            output=output,
            validate=validate,
            budget=client.budgets.critic_repair_retries,
        )
    except (ModelCallError, MalformedOutputError, ValidationError) as exc:
        reason = str(exc)[:400]
        log.warning(
            "lens %s failed on %s (critic %s): %s", lens.value, artifact_hash[:12], alias, reason
        )
        return base.model_copy(update={"failed": True, "failure_reason": reason})

    return base.model_copy(update={"issues": output.issues})


def _repair_until_valid(
    client: LLMClient,
    alias: str,
    *,
    user: str,
    output: CritiqueOutput,
    validate,
    budget: int,
) -> CritiqueOutput:
    """Validate, and on rejection ask the critic to patch the offending field.

    The loop lives here rather than inside `client.structured` because the repair asks
    for a *different schema* than the call it is repairing: `IssueRepairs`, not another
    `CritiqueOutput`. That is the whole of D-repair-turn-context — a critic re-asked for
    its entire review regenerates every field, and one production repair fixed the
    rejected span while breaking an unrelated category in the same response. A patch
    cannot: `triage.apply_repairs` carries everything unnamed across mechanically.

    Fail-closed is unchanged. The patched result is revalidated *whole*, no subset of
    issues is ever salvaged, and once the budget is spent the violation raises exactly as
    it did before — a lens that cannot be validated fails.
    """
    fingerprint_key = secrets.token_bytes(32)
    for attempt in range(budget + 1):
        try:
            validate(output)
            return output
        except triage.LensValidationError as exc:
            # D-repair-diagnostics still has to hold. That line was emitted by
            # `llm.structured`'s repair loop; lens validation no longer runs there, so it
            # is emitted here or it is lost — and the fingerprints are what turned "the
            # repair does not work" from a story into a measurement. Content-free by the
            # same contract: codes, indices, loci and a keyed hash, never a value.
            log.info(
                "lens rejection from %s (attempt %d): %s",
                alias,
                attempt + 1,
                " ".join(f"{k}={v}" for k, v in exc.diagnostics(fingerprint_key).items()),
            )
            if attempt == budget:
                raise MalformedOutputError(f"{alias}: schema violation after repair: {exc}") from exc
            hint = getattr(exc, "repair_hint", None)
            excerpt = getattr(exc, "repair_excerpt", None)
            rejected = getattr(exc, "rejected_text", None)
            try:
                repairs = client.structured(
                    alias,
                    system=prompts.CRITIC_SYSTEM,
                    user=prompts.critic_repair_turn(
                        user=user,
                        instruction="",
                        error=str(exc),
                        guidance=hint() if callable(hint) else "",
                        guidance_excerpt=excerpt() if callable(excerpt) else "",
                        rejected=rejected() if callable(rejected) else "",
                        issue_index=exc.issue_index,
                        issue_count=exc.issue_count,
                    ),
                    schema=IssueRepairs,
                    max_tokens=CRITIC_MAX_TOKENS,
                    repair_retries=client.budgets.critic_repair_retries,
                )
            except (MalformedOutputError, ValidationError) as repair_exc:
                raise MalformedOutputError(
                    f"{alias}: repair patch failed schema validation"
                ) from repair_exc
            output, applied = triage.apply_repairs(
                output,
                repairs,
                # The repair turn embeds report-derived text, so a patch is only trusted
                # to address what the validator rejected (RA-010). `issue_index` is set
                # by the validate closure's `at_issue` before the error reaches here; -1
                # is the defensive no-match value should that ever not hold — the schema
                # forbids negative indices, so every entry is then dropped, fail-closed.
                issue_index=exc.issue_index if exc.issue_index is not None else -1,
                field=exc.field,
            )
            # Content-free: indices and field names only, never a replacement value
            # (RA-016). An empty list is the honest answer when a critic could not anchor
            # the issue, and the next pass fails the lens on it.
            log.info("critic %s patched %s", alias, applied or "nothing")
    return output
