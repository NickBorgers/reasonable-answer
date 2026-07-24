"""A scriptable stand-in for the LiteLLM proxy, so the whole graph runs offline.

It also records every call, which is how the isolation tests assert what a given
role could *possibly* have seen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from reasonable_answer.llm import Completion
from reasonable_answer.schemas import (
    ArbiterVerdict,
    CritiqueOutput,
    OrchestratorRecommendation,
    WriterDisputes,
)


@dataclass
class Call:
    alias: str
    system: str
    user: str
    schema: str | None = None
    tools: list[str] = field(default_factory=list)


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
    #: alias -> can it emit tool calls; absent means yes
    tool_capable: dict[str, bool] = field(default_factory=dict)
    #: every tool-result string the fake handed back to a "model"
    tool_results: list[str] = field(default_factory=list)
    #: callable(alias, user) -> WriterDisputes; None means "no disputes raised"
    dispute_fn: Any | None = None
    #: callable(alias, user) -> ArbiterVerdict; None means an arbiter call is a
    #: test error (the run under test was not expected to reach one)
    arbiter_fn: Any | None = None

    # ---- the LLMClient surface the graph uses -----------------------------

    def resolve_identities(self, aliases: list[str]) -> dict[str, str]:
        return {a: self.identities[a] for a in aliases}

    def identity(self, alias: str) -> str:
        return self.identities[alias]

    def probe_structured_output(self, alias: str) -> str:
        return self.modes.get(alias, "json_schema")

    def mode_for(self, alias: str) -> str:
        return self.probe_structured_output(alias)

    def probe_tool_calling(self, alias: str) -> bool:
        return self.tool_capable.get(alias, True)

    def tool_capable_for(self, alias: str) -> bool:
        return self.probe_tool_calling(alias)

    def complete(self, alias: str, *, system: str, user: str, **kwargs: Any) -> Completion:
        self.calls.append(
            Call(alias, system, user, tools=[
                t["function"]["name"] for t in (kwargs.get("tools") or [])
            ])
        )
        self.generations += 1
        # Drive the handler once when one is supplied, so tests can assert on what a
        # tool result actually looks like by the time it reaches a model.
        handler = kwargs.get("tool_handler")
        if handler is not None:
            self.tool_results.append(handler("web_search", '{"query": "probe"}'))
        return Completion(
            text=self.report_fn(self.generations),
            model_reported=alias,
            prompt_tokens=0,
            completion_tokens=0,
            tool_calls=1 if handler is not None else 0,
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
        if schema is WriterDisputes:
            if self.dispute_fn is None:
                return WriterDisputes(disputes=[])
            return self.dispute_fn(alias, user)
        if schema is ArbiterVerdict:
            if self.arbiter_fn is None:
                raise AssertionError("unexpected arbiter call")
            return self.arbiter_fn(alias, user)
        raise AssertionError(f"unexpected schema {schema}")


def http_stub(body: bytes | str, *, ctype: str = "text/html", status: int = 200):
    """A stand-in for an opened http(s) response, for monkeypatching
    `urllib.request.OpenerDirector.open`.

    The network is stubbed at the opener rather than with an HTTP mock library so the
    real `fetch._http_only_opener` and `_BoundedRedirects` stay on the path under test.
    Shared by the citation-fetch and seed-ingest tests, which need the same shape.
    """
    raw = body.encode() if isinstance(body, str) else body

    class _Resp(BytesIO):
        headers = {"Content-Type": ctype}

        def __init__(self):
            super().__init__(raw)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp()
