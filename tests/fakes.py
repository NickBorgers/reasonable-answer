"""A scriptable stand-in for the LiteLLM proxy, so the whole graph runs offline.

It also records every call, which is how the isolation tests assert what a given
role could *possibly* have seen.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from pydantic import ValidationError

from reasonable_answer.config import Budgets
from reasonable_answer.llm import Completion, MalformedOutputError
from reasonable_answer.schemas import (
    ArbiterVerdict,
    CritiqueOutput,
    OrchestratorRecommendation,
    WriterDisputes,
)


def structured_with_repair(
    alias: str,
    user: str,
    produce: Callable[[str], Any],
    validate: Callable[[Any], None] | None,
    repair_retries: int | None,
) -> Any:
    """Mirror `LLMClient.structured`'s repair loop for a test double.

    Every stand-in for the client has to run the caller's `validate=` and re-ask within
    budget before failing closed. A double that ignored it would let `critique_once`'s
    fail-closed validation pass unrun, and every test asserting a bad critique fails its
    lens would pass for the wrong reason. Shared so the doubles cannot drift apart.
    """
    attempts = (repair_retries if repair_retries is not None else 0) + 1
    attempt_user = user
    last_err = ""
    for attempt in range(attempts):
        result = produce(attempt_user)
        if validate is None:
            return result
        try:
            validate(result)
            return result
        except (ValidationError, ValueError) as exc:
            last_err = str(exc)[:800]
            if attempt == attempts - 1:
                break
            # The re-ask carries the rejection and whatever guidance the error offers,
            # exactly as the real client composes it — a double that re-sent the
            # original prompt would make a repair look like a re-roll.
            hint = getattr(exc, "repair_hint", None)
            guidance = hint() if callable(hint) else ""
            attempt_user = (
                f"{user}\n\nYour previous response was rejected by the schema "
                f"validator:\n{last_err}\n{guidance}"
            )
    raise MalformedOutputError(f"{alias}: schema violation after repair: {last_err}")


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
    #: repairs offered to a critic whose issues fail validation. 0 keeps the historical
    #: one-call-per-critique behaviour every existing test was written against.
    critic_repair_retries: int = 0

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

    @property
    def budgets(self) -> Budgets:
        """Only the fields `LLMClient.budgets` is consulted for. Repairs default to 0 so
        a test that scripts a rejecting critic sees one call, not several; the tests that
        exercise repair set it explicitly."""
        return Budgets(critic_repair_retries=self.critic_repair_retries)

    def structured(
        self,
        alias: str,
        *,
        system: str,
        user: str,
        schema: type,
        validate: Callable[[Any], None] | None = None,
        repair_retries: int | None = None,
        **kwargs: Any,
    ):
        def produce(attempt_user: str):
            self.calls.append(Call(alias, system, attempt_user, schema.__name__))
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

        return structured_with_repair(alias, user, produce, validate, repair_retries)


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


def json_stub(payload, *, status: int = 200):
    """`http_stub` for a registry response. Same opener seam, JSON content type."""
    return http_stub(json.dumps(payload), ctype="application/json", status=status)


class http_sequence:  # noqa: N801 - a factory used exactly like `http_stub`
    """Successive responses for successive opens, and a record of what was asked.

    The resolver ladder turns one logical fetch into several HTTP calls — the cited URL,
    then a registry, then another registry, then an open-access mirror — so the thing
    worth asserting is the *sequence*: which hosts were consulted, in what order, and
    that a tier which should have been skipped was never reached. A single-response stub
    cannot express any of that.

    Items are either responses (from `http_stub`/`json_stub`, as zero-argument callables
    or plain values) or exceptions, which are raised in place — that is how a 404 from a
    registry is expressed, since `http_get` reads it from `urllib.error.HTTPError`.
    """

    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, *args, **kwargs):
        # Assigned onto `OpenerDirector.open`, this instance is not a descriptor, so it
        # receives no `self` — unlike the lambdas the older fetch tests patch in, which
        # do. Picking the request out of the positional arguments works either way.
        request = next(a for a in args if isinstance(a, str) or hasattr(a, "full_url"))
        url = request if isinstance(request, str) else request.full_url
        self.urls.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected request {len(self.urls)} to {url}")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item() if callable(item) else item

    @property
    def exhausted(self) -> bool:
        return not self._responses


def minimal_pdf(*lines: str) -> bytes:
    """A hand-built single-page PDF, so the test needs no fixture binary.

    Shared by the seed-ingest and citation-fetch tests: both need a real PDF and
    neither should carry a checked-in binary to get one.
    """
    drawn = " 0 -20 Td ".join(f"({line}) Tj" for line in lines)
    content = f"BT /F1 12 Tf 72 720 Td {drawn} ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
