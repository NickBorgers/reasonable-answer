"""LiteLLM-proxy client: one OpenAI-compatible endpoint for every model.

Two things this module owns:

1. **Resolved identity** (RA-017). The proxy's ``/model/info`` maps an alias to the
   underlying ``provider/model``. Model distinctness — the thing a strong `accepted`
   rests on — is enforced against *that*, never the alias.
2. **Structured output with a capability ladder.** The roster mixes frontier models
   (native json_schema) with small local/open models (nothing but prompting). Each
   alias is probed once at startup and pinned to the strongest mode it supports;
   if none works, the run fails closed rather than degrading silently.
"""

from __future__ import annotations

import json
import logging
import random
import re
import secrets
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from .config import Budgets, Config, ConfigError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: (tool_name, raw_json_arguments) -> the string handed back to the model as the
#: tool result. Implementations own fencing it as untrusted data (RA-010).
ToolHandler = Callable[[str, str], str]

#: strongest first
MODES = ("json_schema", "json_object", "prompt")


#: Tool-call syntax as it arrives when the proxy failed to parse it and handed the raw
#: text back as content: DeepSeek's fullwidth-bar `<｜DSML｜invoke ...>` and
#: `<｜tool▁calls▁begin｜>`, the ASCII `<|...|>` control-token family, and the
#: `<tool_call>` form several open models use.
_TOOL_CALL_SPAN = re.compile(r"<\s*/?\s*[｜|][^>]{0,300}?>|</?\s*tool_call\s*>", re.IGNORECASE)
_TOOL_CALL_OPENERS = ("<｜", "<|", "<tool_call>")
#: Share of a response that must be markup before it is called a failed call rather than
#: prose that merely mentions the syntax. A report *about* tool calling quotes the token
#: once in thousands of characters; a response that *is* an unparsed tool call is mostly
#: tags.
_TOOL_CALL_MARKUP_SHARE = 0.3


def _unparsed_tool_call(content: str) -> bool:
    """Is this "prose" actually a tool call the proxy failed to parse?"""
    text = content.strip()
    spans = _TOOL_CALL_SPAN.findall(text)
    if not spans:
        return False
    # Nothing legitimate opens with the token; past that, go by how much of the
    # response is markup, so quoting the syntax in a report stays safe.
    if text.lower().startswith(_TOOL_CALL_OPENERS):
        return True
    return sum(len(s) for s in spans) >= len(text) * _TOOL_CALL_MARKUP_SHARE


class ModelCallError(RuntimeError):
    """Transport/API failure — retryable within budget.

    `failure_class` is a short, stable token naming *how* the call failed, carried
    so a caller can record it as a countable field instead of matching on the
    message text. `graph._generate` writes it onto every `generate_failed` event;
    without it, "this model keeps failing" is an anecdote nobody can check, and a
    provider-level defect is indistinguishable from a model-level one.
    """

    def __init__(self, message: str, *, failure_class: str = "call_failed") -> None:
        super().__init__(message)
        self.failure_class = failure_class


class PermanentCallError(ModelCallError):
    """The request itself is wrong; asking again cannot help.

    A malformed request, a rejected credential, or a model the proxy does not serve
    answers identically however many times it is sent. Retrying one burns the whole
    call budget — and, for a writer, the run's only eligible author — on a verdict
    that was already final. Still a `ModelCallError` so every existing `except`
    clause keeps catching it; the subclass only tells `_create` not to sleep and
    try again.
    """

    def __init__(self, message: str, *, failure_class: str = "permanent") -> None:
        super().__init__(message, failure_class=failure_class)


#: HTTP statuses where the fault is in the request, not the moment. 408 (timeout) and
#: 429 (rate limit) are deliberately absent — those are exactly what backoff is for,
#: and so is every 5xx.
_PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 413, 422})

#: Ceiling on a server-supplied `Retry-After`. A provider asking us to wait ten minutes
#: has effectively failed the call; the caller's own rotation is the better move.
_MAX_RETRY_AFTER_SECONDS = 120.0


def _status_of(exc: Exception) -> int | None:
    """The HTTP status the SDK carries, wherever it hung it. Never read the message."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _permanent(exc: Exception) -> bool:
    """Is this failure final? Read the status code the SDK carries, never the message."""
    return _status_of(exc) in _PERMANENT_STATUSES


def _failure_class(exc: Exception) -> str:
    """A short, stable token naming how `exc` failed.

    Errors this module raised already carry one; a transport exception from the SDK
    does not, so it is classified here — by exception type and status code, never by
    message text, for the same reason `_permanent` reads the status.
    """
    carried = getattr(exc, "failure_class", None)
    if isinstance(carried, str) and carried:
        return carried
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection"
    status = _status_of(exc)
    if status is not None:
        return f"http_{status}"
    return "call_failed"


def _retry_after(exc: Exception) -> float | None:
    """The provider's own `Retry-After`, in seconds, when it sent a usable one.

    Only the delta-seconds form is honoured. The HTTP-date form would need a trusted
    clock comparison against a header we already treat as advisory, and every provider
    in this roster sends seconds.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:  # pragma: no cover - defensive: header maps vary by SDK version
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


class MalformedOutputError(RuntimeError):
    """The model answered, but not in the closed schema. Repairable, then fail-closed."""


@dataclass(frozen=True)
class Completion:
    text: str
    model_reported: str
    prompt_tokens: int
    completion_tokens: int
    #: how many tool calls the model made producing this text; 0 when no tools were
    #: offered. Recorded in the audit trail so a run can be asked, afterwards,
    #: whether its citations were actually looked up.
    tool_calls: int = 0


@dataclass(frozen=True)
class _Reply:
    """One raw round-trip: the assistant message plus what the proxy said it was."""

    message: dict[str, Any]
    reported: str
    prompt_tokens: int
    completion_tokens: int


class LLMClient:
    def __init__(
        self,
        config: Config,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.0),  # noqa: S311 - not crypto
    ) -> None:
        self._config = config
        self._client = OpenAI(
            base_url=config.proxy.base_url,
            api_key=config.proxy.api_key,
            timeout=config.budgets.timeout_seconds,
            max_retries=0,  # retries are ours, so they stay inside the budget
        )
        # Injected for the same reason `BraveSearch` injects its clock: the offline
        # suite must be able to assert the wait without serving it.
        self._sleep = sleep
        self._jitter = jitter
        self._identities: dict[str, str] = {}
        self._modes: dict[str, str] = {}
        self._tool_capable: dict[str, bool] = {}

    def _backoff(self, attempt: int, retry_after: float | None = None) -> None:
        """Wait before retry number `attempt` (1-based). Server hint beats our guess.

        Exponential with jitter, because the failures this exists for are correlated
        in time — a provider that just returned an empty completion is mid-something,
        and three requests inside five seconds are three samples of the same bad
        moment (D-provider-retry).
        """
        budgets = self._config.budgets
        if retry_after is not None:
            delay = retry_after
        else:
            if budgets.retry_backoff_seconds <= 0:
                return
            uncapped = budgets.retry_backoff_seconds * (2 ** (attempt - 1))
            delay = min(uncapped, budgets.retry_backoff_max_seconds) * self._jitter()
        if delay > 0:
            self._sleep(delay)

    @property
    def budgets(self) -> Budgets:
        """The budgets this client was built with, for callers that need to size their
        own retry against the same config the client uses (e.g. `critique_once`)."""
        return self._config.budgets

    def backoff_between_writer_attempts(self, attempt: int) -> None:
        """Serve the same wait `_create` uses, for a caller retrying at its own layer.

        `graph._generate` re-asks the writer pool after a whole call has failed. That
        retry needs a pause for the same reason the in-call one does, and routing it
        here keeps a single injectable clock — a test that stubs `sleep` stubs both.
        """
        self._backoff(attempt)

    # ------------------------------------------------------------------ identity

    def resolve_identities(self, aliases: list[str]) -> dict[str, str]:
        """alias -> 'provider/model' as the proxy resolves it. Fails closed."""
        info = self._fetch_model_info()
        out: dict[str, str] = {}
        for alias in aliases:
            resolved = info.get(alias)
            if not resolved:
                raise ConfigError(
                    f"fail closed: alias '{alias}' is not served by the proxy at "
                    f"{self._config.proxy.base_url}"
                )
            out[alias] = resolved
        self._identities = out
        return out

    def identity(self, alias: str) -> str:
        return self._identities.get(alias, alias)

    def _fetch_model_info(self) -> dict[str, str]:
        url = self._config.proxy.base_url.rstrip("/").removesuffix("/v1") + "/model/info"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._config.proxy.api_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed proxy URL
                payload = json.loads(resp.read())
        except Exception as exc:  # pragma: no cover - network
            raise ConfigError(f"fail closed: cannot reach the LiteLLM proxy: {exc}") from exc
        return {
            entry["model_name"]: entry.get("litellm_params", {}).get("model", entry["model_name"])
            for entry in payload.get("data", [])
        }

    # ---------------------------------------------------------------- capability

    def probe_structured_output(self, alias: str) -> str:
        """Pin `alias` to the strongest structured-output mode it actually supports.

        Only *capability* evidence demotes to the next mode: `MalformedOutputError`
        (the model answered, but not inside the closed schema) or a
        `PermanentCallError` whose `failure_class` is `http_400`/`http_422` — how a
        provider says "I do not support this `response_format`" (that request shape
        is exactly what changes between modes). Everything else — `http_429`, a
        timeout, a connection failure, any 5xx, and a rejected credential
        (`http_401`/`403`/`404`/`413`) — is an *availability* fact about the moment,
        not a capability fact about the alias, and `_create` has already retried it
        within `budgets.call_retries` before giving up. Demoting on it would silently
        pin the alias to a weaker mode for the rest of the process on nothing more
        than a bad moment on the wire: `ra doctor` did exactly that to
        `nemotron-3-ultra` on 2026-08-11, pinned to `json_object` after three
        DeepInfra 429s during the `json_schema` probe (D-probe-capability-evidence).
        So an availability failure aborts the probe by raising, rather than falling
        through to a weaker mode under a false "unsupported" verdict.
        """
        if alias in self._modes:
            return self._modes[alias]

        class _Probe(BaseModel):
            ok: bool

        for mode in MODES:
            try:
                self.structured(
                    alias,
                    system="You return JSON only.",
                    user='Return {"ok": true}.',
                    schema=_Probe,
                    mode=mode,
                    max_tokens=3000,
                    repair_retries=0,
                )
            except MalformedOutputError as exc:
                log.debug("alias %s does not support mode %s: %s", alias, mode, exc)
                continue
            except PermanentCallError as exc:
                if exc.failure_class in ("http_400", "http_422"):
                    log.debug("alias %s does not support mode %s: %s", alias, mode, exc)
                    continue
                raise ConfigError(
                    f"fail closed: probe of alias '{alias}' for mode '{mode}' could "
                    f"not be completed ({exc}); its structured-output mode is "
                    f"unknown, not unsupported — retry once the proxy/provider "
                    f"recovers"
                ) from exc
            except Exception as exc:
                raise ConfigError(
                    f"fail closed: probe of alias '{alias}' for mode '{mode}' could "
                    f"not be completed ({exc}); its structured-output mode is "
                    f"unknown, not unsupported — retry once the proxy/provider "
                    f"recovers"
                ) from exc
            self._modes[alias] = mode
            return mode
        raise ConfigError(
            f"fail closed: alias '{alias}' cannot produce parseable structured output"
        )

    def mode_for(self, alias: str) -> str:
        return self._modes.get(alias, "prompt")

    # -------------------------------------------------------------------- calls

    def complete(
        self,
        alias: str,
        *,
        system: str,
        user: str,
        max_tokens: int = 16000,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
        max_tool_rounds: int = 6,
        timeout: float | None = None,
        should_offer_tools: Callable[[], bool] | None = None,
    ) -> Completion:
        """One chat completion, retried within the call budget.

        When `tools` and `tool_handler` are both supplied the call becomes an agentic
        loop: the model may emit tool calls, which are executed and fed back, until it
        answers in prose or `max_tool_rounds` is reached. Tool *results* are untrusted
        third-party text (RA-010) — the handler is responsible for fencing them.

        `should_offer_tools`, when given, is consulted before every round: a false
        answer withdraws `tools` for the rest of the call. It exists because a tool
        can run out of budget mid-loop — the search handler starts returning "budget
        exhausted" text — and a model handed a tool that cannot work will keep calling
        it until the rounds are gone (D-provider-retry). Withdrawing it forces the answer instead.

        `timeout`, when given, overrides the client-wide `budgets.timeout_seconds`
        for this call only (passed straight through to the OpenAI SDK's per-request
        `timeout` kwarg). It bounds **client occupancy** — the connection is closed
        and the concurrency permit governing this call may be released once the SDK
        gives up waiting — not upstream generation: whether the backend actually
        stops computing on disconnect is a LiteLLM/backend property this bound does
        not assume (docs/question-refinement.md's honest layering). `None` (the
        default) leaves every existing caller byte-identical.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        base: dict[str, Any] = {"model": alias, "max_tokens": max_tokens}
        if temperature is not None:
            base["temperature"] = temperature
        if response_format is not None:
            base["response_format"] = response_format

        if not (tools and tool_handler):
            reply = self._invoke_create(alias, {**base, "messages": messages}, timeout)
            return Completion(
                text=(reply.message.get("content") or "").strip(),
                model_reported=reply.reported,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
            )

        prompt_tokens = completion_tokens = 0
        tool_calls_made = 0
        for round_no in range(max_tool_rounds + 1):
            # The final round drops `tools` entirely: a model that keeps calling tools
            # forever must still produce an answer, and removing the tool is the only
            # instruction every provider in the roster honours identically. A tool
            # whose own budget has run out is withdrawn the same way, as soon as that
            # happens, rather than being offered for rounds it can no longer serve.
            exhausted = round_no == max_tool_rounds
            offering = not exhausted and (should_offer_tools is None or should_offer_tools())
            kwargs = {**base, "messages": messages}
            if offering:
                kwargs["tools"] = tools
            reply = self._invoke_create(alias, kwargs, timeout)
            prompt_tokens += reply.prompt_tokens
            completion_tokens += reply.completion_tokens

            calls = _tool_calls(reply.message)
            if not calls or not offering:
                text = (reply.message.get("content") or "").strip()
                if not text:
                    # The model spent its last round on another tool call instead of
                    # an answer. `_create`'s empty-content guard let that through —
                    # correctly, since a message carrying tool calls is not an empty
                    # completion — and this loop used to return it as a successful
                    # `text=""`, which the caller could only read as "the model wrote
                    # nothing". Two production runs aborted on exactly that (D-provider-retry).
                    # One more round, asked in words, is far cheaper than discarding a
                    # writer call that has already spent its whole search budget.
                    reply = self._answer_now(alias, base, messages, timeout)
                    prompt_tokens += reply.prompt_tokens
                    completion_tokens += reply.completion_tokens
                    text = (reply.message.get("content") or "").strip()
                if not text:
                    raise ModelCallError(
                        f"{alias}: tool loop ended without an answer",
                        failure_class="tool_loop_no_answer",
                    )
                return Completion(
                    text=text,
                    model_reported=reply.reported,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    tool_calls=tool_calls_made,
                )

            messages.append(reply.message)
            for call in calls:
                tool_calls_made += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": tool_handler(
                            call.get("function", {}).get("name", ""),
                            call.get("function", {}).get("arguments", "") or "{}",
                        ),
                    }
                )
        raise ModelCallError(  # pragma: no cover
            f"{alias}: tool loop did not terminate", failure_class="tool_loop_no_end"
        )

    #: The one instruction sent when a model ends its tool loop without prose. Kept
    #: free of any run material — it says nothing about the question, the draft, or the
    #: tool results already in the conversation.
    _ANSWER_NOW = (
        "You have no tools available and no further tool calls are possible. "
        "Write your complete final answer now, as prose, using what you already have."
    )

    def _answer_now(
        self,
        alias: str,
        base: dict[str, Any],
        messages: list[dict[str, Any]],
        timeout: float | None,
    ) -> _Reply:
        """One last toolless round asking, in words, for the answer.

        The unanswered assistant message is deliberately NOT appended: it carries tool
        calls with no matching `role: tool` replies, and several providers reject a
        conversation shaped like that. Nudging from the last complete state is both
        valid and closer to what the model was asked in the first place.
        """
        nudged = [*messages, {"role": "user", "content": self._ANSWER_NOW}]
        log.warning("call to %s ended its tool loop without prose; asking for the answer", alias)
        return self._invoke_create(alias, {**base, "messages": nudged}, timeout)

    def _invoke_create(self, alias: str, kwargs: dict[str, Any], timeout: float | None) -> _Reply:
        """Calls `_create` with a `timeout` kwarg only when one was actually given.

        Several tests monkeypatch `_create` itself with a 2-positional-argument
        stand-in (`tests/test_llm_tools.py`); always forwarding `timeout=None` would
        break every one of them for no behavioural gain, since `None` and "omitted"
        already mean the same thing to `_create`.
        """
        if timeout is not None:
            return self._create(alias, kwargs, timeout=timeout)
        return self._create(alias, kwargs)

    def _create(self, alias: str, kwargs: dict[str, Any], *, timeout: float | None = None) -> _Reply:
        """One request, retried within the call budget."""
        if timeout is not None:
            kwargs = {**kwargs, "timeout": timeout}
        last: Exception | None = None
        for attempt in range(self._config.budgets.call_retries + 1):
            # Before every attempt but the first. Placed at the top of the loop rather
            # than at each `continue` so no retry path can be added later that forgets
            # to wait.
            if attempt > 0:
                self._backoff(attempt, _retry_after(last) if last else None)
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # transport / provider error
                last = exc
                log.warning("call to %s failed (attempt %d): %s", alias, attempt + 1, exc)
                if _permanent(exc):
                    # Spending the rest of the budget re-sending a request the provider
                    # has already judged malformed just delays the caller's rotation to
                    # a model that might work.
                    raise PermanentCallError(
                        f"{alias}: {exc}", failure_class=f"http_{_status_of(exc)}"
                    ) from exc
                continue
            usage = resp.usage
            reported = getattr(resp, "model", None) or alias
            # "No silent fallback to a duplicate" (RA-017): if the proxy served this
            # request from a different model than the alias we pinned at startup,
            # every downstream identity claim — author exclusion, distinct-reviewer
            # counting — is false. Fail closed rather than believe the alias map.
            if not _identity_matches(reported, alias, self._identities.get(alias)):
                raise ModelCallError(
                    f"identity mismatch: alias '{alias}' was served by '{reported}'",
                    failure_class="identity_mismatch",
                )
            message = _message_dict(resp.choices[0].message)
            # A 200 carrying neither prose nor a tool call is a failed call that
            # forgot to say so — small models in the roster do this intermittently.
            # It costs a caller its whole run if it escapes as "success", so it is
            # retried on the same budget as a transport error rather than returned.
            content = (message.get("content") or "").strip()
            if not content and not _tool_calls(message):
                last = ModelCallError(
                    f"{alias}: empty completion", failure_class="empty_completion"
                )
                log.warning("call to %s returned empty content (attempt %d)", alias, attempt + 1)
                continue
            # The same failure wearing prose's clothes: the model emitted its tool call
            # as *text* and the proxy did not parse it, so `tool_calls` is empty and the
            # "content" is markup. It reads as success, and one production run shipped a
            # final answer that was nothing but a `<｜DSML｜tool_calls>` block. Fail it on
            # the same budget — and if the retries do not shake it loose, the writer
            # rotation in `graph._generate` moves to a different model.
            if not _tool_calls(message) and _unparsed_tool_call(content):
                last = ModelCallError(
                    f"{alias}: emitted unparsed tool-call markup as content",
                    failure_class="unparsed_tool_markup",
                )
                log.warning(
                    "call to %s returned unparsed tool-call markup (attempt %d)",
                    alias,
                    attempt + 1,
                )
                continue
            return _Reply(
                message=message,
                reported=reported,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
        # `from last` preserves the original exception as `__cause__` (lost once the
        # loop moves past its `except` block otherwise) so a caller with its own
        # timeout classification — `web.refine.RefinementService`'s orphan-linger
        # logic — can distinguish a provider-level timeout from any other transport
        # failure without this module needing to know that policy exists.
        # The class is the *cause's* class, not "exhausted": what a reader needs from
        # a run that spent its budget is which defect it spent it on — three unparsed
        # tool-call blocks and three timeouts are the same event today and want
        # different fixes. The message still says the budget was exhausted.
        raise ModelCallError(
            f"{alias}: exhausted call retries ({last})",
            failure_class=_failure_class(last) if last else "exhausted_retries",
        ) from last

    def probe_tool_calling(self, alias: str) -> bool:
        """Can `alias` actually emit a tool call? Probed once, like structured output.

        The roster mixes frontier models with small open ones, and several of the
        latter accept a `tools` parameter and then ignore it. Search that silently
        never happens is the failure mode this exists to catch: the writer prompt
        still demands a '## Sources' section, so an un-searched draft comes back
        with invented citations that look exactly like verified ones.

        Only capability evidence marks the alias incapable: the call succeeds and the
        model simply never calls the tool (below — no exception at all), or a
        `PermanentCallError` whose `failure_class` is `http_400`/`http_422` — the
        provider rejected the `tools` parameter's shape outright. Every other failure
        — `http_429`, a timeout, a connection failure, any 5xx, a rejected credential
        — is an availability fact, not a capability one, and used to be swallowed the
        same way `probe_structured_output` swallowed one (D-probe-capability-evidence):
        raise instead of silently pinning the alias tool-incapable for the process.
        """
        if alias in self._tool_capable:
            return self._tool_capable[alias]

        probe = {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Return the string 'pong'. Call this to answer.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        try:
            reply = self._create(
                alias,
                {
                    "model": alias,
                    "messages": [
                        {"role": "system", "content": "You call tools when told to."},
                        {"role": "user", "content": "Call the ping tool with value='ping'."},
                    ],
                    "max_tokens": 3000,
                    "tools": [probe],
                },
            )
        except PermanentCallError as exc:
            if exc.failure_class in ("http_400", "http_422"):
                log.debug("alias %s failed the tool-calling probe: %s", alias, exc)
                self._tool_capable[alias] = False
                return False
            raise ConfigError(
                f"fail closed: probe of alias '{alias}' for tool-calling could not be "
                f"completed ({exc}); its tool-calling capability is unknown, not "
                f"absent — retry once the proxy/provider recovers"
            ) from exc
        except Exception as exc:
            raise ConfigError(
                f"fail closed: probe of alias '{alias}' for tool-calling could not be "
                f"completed ({exc}); its tool-calling capability is unknown, not "
                f"absent — retry once the proxy/provider recovers"
            ) from exc
        capable = bool(_tool_calls(reply.message))
        self._tool_capable[alias] = capable
        return capable

    def tool_capable(self, alias: str) -> bool:
        return self._tool_capable.get(alias, False)

    def structured(
        self,
        alias: str,
        *,
        system: str,
        user: str,
        schema: type[T],
        mode: str | None = None,
        max_tokens: int = 16000,
        repair_retries: int | None = None,
        timeout: float | None = None,
        validate: Callable[[T], None] | None = None,
    ) -> T:
        """A completion validated against a closed schema. Bounded repair, then raise.

        `timeout` (see `complete`) overrides `budgets.timeout_seconds` for every
        attempt in this call, repairs included — callers with their own, tighter
        per-call deadline (e.g. `web.refine.RefinementService`) want that deadline to
        apply to the repair attempt too, not just the first.

        `validate` runs *after* the schema parses and rejects by raising `ValueError`.
        It exists so that checks the schema cannot express — a critic's `claim_span`
        having to be real text from the paragraph it cites — are repaired on this loop
        rather than outside it. A caller that validates after `structured()` returns
        has only one move left when it fails, which is to throw the whole response away
        and ask a fresh model the identical question; that is a retry that cannot
        converge, and it is what aborted two production runs. If the raised error
        offers a `repair_hint()`, its text is handed to the model alongside the error,
        so the second attempt knows what the first got wrong.
        """
        mode = mode or self.mode_for(alias)
        repair_retries = (
            self._config.budgets.repair_retries if repair_retries is None else repair_retries
        )
        json_schema = schema.model_json_schema()
        response_format = _response_format(mode, schema.__name__, json_schema)
        instruction = _schema_instruction(json_schema)

        attempt_user = f"{user}\n\n{instruction}"
        last_err = ""
        diagnostic_key = secrets.token_bytes(32)
        for attempt in range(repair_retries + 1):
            completion = self.complete(
                alias,
                system=system,
                user=attempt_user,
                max_tokens=max_tokens,
                response_format=response_format,
                timeout=timeout,
            )
            try:
                parsed = schema.model_validate(_extract_json(completion.text))
                if validate is not None:
                    validate(parsed)
                return parsed
            except (ValidationError, ValueError) as exc:
                last_err = str(exc)[:800]
                # RA-016: `last_err` can carry model output or report-derived validation
                # input — a critic's `claim_span`, a rejected field value, the rationale a
                # validator quotes back. It feeds the repair prompt below, which stays
                # inside the run, but must never reach an ordinary log: `RA_LOG_LEVEL=INFO`
                # is the container default (D-provider-retry), and stdout/log aggregation lives outside
                # the 0700 `runs/<id>/` tree. Log the bounded, content-free class, plus
                # whatever equally bounded diagnostics the validator offers about its own
                # rejection — never `last_err`, and never a rejected value.
                log.info(
                    "schema violation from %s (attempt %d): %s%s",
                    alias,
                    attempt + 1,
                    exc.__class__.__name__,
                    _diagnostics_suffix(exc, fingerprint_key=diagnostic_key),
                )
                # Duck-typed rather than a shared base class: the validators that carry
                # guidance live in `triage`, which is deliberately LLM-free and must not
                # import this module to say so.
                hint = getattr(exc, "repair_hint", None)
                guidance = hint() if callable(hint) else ""
                attempt_user = (
                    f"{user}\n\n{instruction}\n\n"
                    f"Your previous response was rejected by the schema validator:\n"
                    f"{last_err}\n"
                    f"{guidance}\n"
                    f"Return corrected JSON only. No prose, no code fence."
                )
        raise MalformedOutputError(f"{alias}: schema violation after repair: {last_err}")


def _diagnostics_suffix(exc: Exception, *, fingerprint_key: bytes) -> str:
    """Bounded detail a validator offers about its own rejection, rendered for a log.

    Duck-typed for the same reason `repair_hint` is: the validators that carry this live
    in `triage`, which is deliberately LLM-free and must not import this module to say
    so. A validator that offers nothing (a plain pydantic `ValidationError`) yields "",
    leaving the line exactly as it was.

    The contract on anything reaching this is RA-016: closed-enum labels, structural
    references, integers and hashes only. A validator that returned report text here
    would put it on stdout, which is outside the 0700 run tree.
    """
    diagnostics = getattr(exc, "diagnostics", None)
    if not callable(diagnostics):
        return ""
    fields = diagnostics(fingerprint_key)
    if not fields:
        return ""
    return " [" + " ".join(f"{key}={value}" for key, value in fields.items()) + "]"


def _message_dict(message: Any) -> dict[str, Any]:
    """Normalise an SDK message object to the plain dict the wire format expects, so
    it can be appended straight back onto `messages` for the next tool round."""
    if isinstance(message, dict):
        raw = message
    elif hasattr(message, "model_dump"):
        raw = message.model_dump(exclude_none=True)
    else:  # pragma: no cover - defensive
        raw = {
            "role": getattr(message, "role", "assistant"),
            "content": getattr(message, "content", ""),
        }
    out: dict[str, Any] = {"role": raw.get("role") or "assistant"}
    # `content` must survive as an explicit null when there are tool calls; several
    # providers reject an assistant message that omits the key entirely.
    out["content"] = raw.get("content")
    if raw.get("tool_calls"):
        out["tool_calls"] = raw["tool_calls"]
    return out


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    return [c for c in calls if isinstance(c, dict)]


def _identity_matches(reported: str, alias: str, resolved: str | None) -> bool:
    """Proxies echo back either the alias or the fully-qualified model id; accept
    exactly those two, case-folded, and nothing else.

    Matching on the bare basename would accept `provider-b/model-x` for a pinned
    `provider-a/model-x` — a different model behind the same short name, which is
    precisely the silent fallback this check exists to catch.
    """
    value = (reported or "").strip().casefold()
    accepted = {alias.strip().casefold()}
    if resolved:
        accepted.add(resolved.strip().casefold())
    return value in accepted


def _response_format(mode: str, name: str, json_schema: dict[str, Any]) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": _strictify(json_schema),
                "strict": True,
            },
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict mode: every object needs additionalProperties:false and a full
    `required` list. Optional fields are expressed as nullable, which our schemas
    already are (`X | None`)."""
    if not isinstance(schema, dict):
        return schema
    out = {k: _strictify(v) if isinstance(v, dict) else v for k, v in schema.items()}
    for key in ("properties", "$defs", "definitions"):
        if key in out and isinstance(out[key], dict):
            out[key] = {k: _strictify(v) for k, v in out[key].items()}
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = _strictify(out["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        if key in out and isinstance(out[key], list):
            out[key] = [_strictify(v) for v in out[key]]
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        props = out.get("properties", {})
        out["required"] = list(props.keys())
    return out


def _schema_instruction(json_schema: dict[str, Any]) -> str:
    return (
        "Respond with a single JSON object and nothing else — no prose, no markdown "
        "fence, no explanation. It must validate against this JSON Schema:\n"
        f"{json.dumps(json_schema, separators=(',', ':'))}"
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Tolerant extraction: raw JSON, fenced JSON, or the first balanced object."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ValueError(f"no JSON object found in response: {text[:200]!r}")
