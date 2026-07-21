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
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .config import Config, ConfigError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: (tool_name, raw_json_arguments) -> the string handed back to the model as the
#: tool result. Implementations own fencing it as untrusted data (RA-010).
ToolHandler = Callable[[str, str], str]

#: strongest first
MODES = ("json_schema", "json_object", "prompt")


class ModelCallError(RuntimeError):
    """Transport/API failure — retryable within budget."""


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
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = OpenAI(
            base_url=config.proxy.base_url,
            api_key=config.proxy.api_key,
            timeout=config.budgets.timeout_seconds,
            max_retries=0,  # retries are ours, so they stay inside the budget
        )
        self._identities: dict[str, str] = {}
        self._modes: dict[str, str] = {}
        self._tool_capable: dict[str, bool] = {}

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
        """Pin `alias` to the strongest structured-output mode it actually supports."""
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
            except Exception as exc:
                log.debug("alias %s does not support mode %s: %s", alias, mode, exc)
                continue
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
    ) -> Completion:
        """One chat completion, retried within the call budget.

        When `tools` and `tool_handler` are both supplied the call becomes an agentic
        loop: the model may emit tool calls, which are executed and fed back, until it
        answers in prose or `max_tool_rounds` is reached. Tool *results* are untrusted
        third-party text (RA-010) — the handler is responsible for fencing them.
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
            reply = self._create(alias, {**base, "messages": messages})
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
            # instruction every provider in the roster honours identically.
            exhausted = round_no == max_tool_rounds
            kwargs = {**base, "messages": messages}
            if not exhausted:
                kwargs["tools"] = tools
            reply = self._create(alias, kwargs)
            prompt_tokens += reply.prompt_tokens
            completion_tokens += reply.completion_tokens

            calls = _tool_calls(reply.message)
            if not calls or exhausted:
                return Completion(
                    text=(reply.message.get("content") or "").strip(),
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
        raise ModelCallError(f"{alias}: tool loop did not terminate")  # pragma: no cover

    def _create(self, alias: str, kwargs: dict[str, Any]) -> _Reply:
        """One request, retried within the call budget."""
        last: Exception | None = None
        for attempt in range(self._config.budgets.call_retries + 1):
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # transport / provider error
                last = exc
                log.warning("call to %s failed (attempt %d): %s", alias, attempt + 1, exc)
                continue
            usage = resp.usage
            reported = getattr(resp, "model", None) or alias
            # "No silent fallback to a duplicate" (RA-017): if the proxy served this
            # request from a different model than the alias we pinned at startup,
            # every downstream identity claim — author exclusion, distinct-reviewer
            # counting — is false. Fail closed rather than believe the alias map.
            if not _identity_matches(reported, alias, self._identities.get(alias)):
                raise ModelCallError(
                    f"identity mismatch: alias '{alias}' was served by '{reported}'"
                )
            message = _message_dict(resp.choices[0].message)
            # A 200 carrying neither prose nor a tool call is a failed call that
            # forgot to say so — small models in the roster do this intermittently.
            # It costs a caller its whole run if it escapes as "success", so it is
            # retried on the same budget as a transport error rather than returned.
            if not (message.get("content") or "").strip() and not _tool_calls(message):
                last = ModelCallError(f"{alias}: empty completion")
                log.warning("call to %s returned empty content (attempt %d)", alias, attempt + 1)
                continue
            return _Reply(
                message=message,
                reported=reported,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            )
        raise ModelCallError(f"{alias}: exhausted call retries ({last})")

    def probe_tool_calling(self, alias: str) -> bool:
        """Can `alias` actually emit a tool call? Probed once, like structured output.

        The roster mixes frontier models with small open ones, and several of the
        latter accept a `tools` parameter and then ignore it. Search that silently
        never happens is the failure mode this exists to catch: the writer prompt
        still demands a '## Sources' section, so an un-searched draft comes back
        with invented citations that look exactly like verified ones.
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
        except Exception as exc:
            log.debug("alias %s failed the tool-calling probe: %s", alias, exc)
            self._tool_capable[alias] = False
            return False
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
    ) -> T:
        """A completion validated against a closed schema. Bounded repair, then raise."""
        mode = mode or self.mode_for(alias)
        repair_retries = (
            self._config.budgets.repair_retries if repair_retries is None else repair_retries
        )
        json_schema = schema.model_json_schema()
        response_format = _response_format(mode, schema.__name__, json_schema)
        instruction = _schema_instruction(json_schema)

        attempt_user = f"{user}\n\n{instruction}"
        last_err = ""
        for attempt in range(repair_retries + 1):
            completion = self.complete(
                alias,
                system=system,
                user=attempt_user,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            try:
                return schema.model_validate(_extract_json(completion.text))
            except (ValidationError, ValueError) as exc:
                last_err = str(exc)[:800]
                log.info("schema violation from %s (attempt %d): %s", alias, attempt + 1, last_err)
                attempt_user = (
                    f"{user}\n\n{instruction}\n\n"
                    f"Your previous response was rejected by the schema validator:\n"
                    f"{last_err}\n"
                    f"Return corrected JSON only. No prose, no code fence."
                )
        raise MalformedOutputError(f"{alias}: schema violation after repair: {last_err}")


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
