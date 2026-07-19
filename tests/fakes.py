"""A scriptable stand-in for the LiteLLM proxy, so the whole graph runs offline.

It also records every call, which is how the isolation tests assert what a given
role could *possibly* have seen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reasonable_answer.llm import Completion
from reasonable_answer.schemas import CritiqueOutput, OrchestratorRecommendation


@dataclass
class Call:
    alias: str
    system: str
    user: str
    schema: str | None = None


@dataclass
class FakeClient:
    """`critique_script` maps alias -> callable(user_prompt) -> CritiqueOutput.
    `reports` is an iterator of report bodies handed out by successive generations."""

    identities: dict[str, str]
    critique_fn: Callable[[str, str], CritiqueOutput]
    report_fn: Callable[[int], str]
    polish_recommended: bool = False
    calls: list[Call] = field(default_factory=list)
    modes: dict[str, str] = field(default_factory=dict)
    generations: int = 0

    # ---- the LLMClient surface the graph uses -----------------------------

    def resolve_identities(self, aliases: list[str]) -> dict[str, str]:
        return {a: self.identities[a] for a in aliases}

    def identity(self, alias: str) -> str:
        return self.identities[alias]

    def probe_structured_output(self, alias: str) -> str:
        return self.modes.get(alias, "json_schema")

    def mode_for(self, alias: str) -> str:
        return self.probe_structured_output(alias)

    def complete(self, alias: str, *, system: str, user: str, **kwargs: Any) -> Completion:
        self.calls.append(Call(alias, system, user))
        self.generations += 1
        return Completion(
            text=self.report_fn(self.generations),
            model_reported=alias,
            prompt_tokens=0,
            completion_tokens=0,
        )

    def structured(self, alias: str, *, system: str, user: str, schema: type, **kwargs: Any):
        self.calls.append(Call(alias, system, user, schema.__name__))
        if schema is OrchestratorRecommendation:
            return OrchestratorRecommendation(
                polish_recommended=self.polish_recommended,
                reason_code="minor_issues_worth_polishing"
                if self.polish_recommended
                else "clean",
            )
        if schema is CritiqueOutput:
            return self.critique_fn(alias, user)
        raise AssertionError(f"unexpected schema {schema}")
