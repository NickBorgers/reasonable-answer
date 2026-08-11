"""`_dereference` and its wiring into `structured()` (D-dereferenced-schema).

Anthropic's native structured-output API rejects `$ref`; a schema pydantic emits for
any nested model or enum carries one, and LiteLLM's fallback for a schema it cannot
pass natively mangles the payload under a junk envelope key. `_dereference` inlines
every `$ref` against `$defs` before the schema reaches either `response_format` or the
prompt-mode instruction. These tests are offline throughout — a fake proxy layer
(`_create`), never a real one.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from reasonable_answer.config import Budgets, Config, ProxyConfig, Roster
from reasonable_answer.llm import LLMClient, _dereference, _Reply, _strictify
from reasonable_answer.schemas import MAX_SPAN, CritiqueOutput
from reasonable_answer.taxonomy import Category


@pytest.fixture
def client(tmp_path) -> LLMClient:
    config = Config(
        proxy=ProxyConfig(),
        roster=Roster(
            writers=["writer-a"],
            critics={
                "logic": ["logic-spec"],
                "evidence": ["evidence-spec"],
                "completeness": ["completeness-spec"],
            },
        ),
        budgets=Budgets(min_ticks=1, hard_cap=3, retry_backoff_seconds=0.0),
        runs_dir=tmp_path / "runs",
    )
    return LLMClient(config)


def _walk(node: Any):
    """Every dict/list reachable from `node`, `node` included."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _assert_ref_free(schema: dict) -> None:
    for node in _walk(schema):
        if isinstance(node, dict):
            assert "$ref" not in node, node
            assert "$defs" not in node, node


# --------------------------------------------------------------- structural fidelity


def test_dereferenced_critique_output_has_no_ref_or_defs_at_any_depth():
    original = CritiqueOutput.model_json_schema()
    assert "$defs" in original  # the finding presupposes this; guard the fixture itself

    deref = _dereference(original)
    _assert_ref_free(deref)


def test_dereferencing_preserves_the_category_enum_members():
    deref = _dereference(CritiqueOutput.model_json_schema())
    category_schema = deref["properties"]["issues"]["items"]["properties"]["category"]
    assert category_schema["enum"] == [c.value for c in Category]


def test_dereferencing_preserves_claim_span_length_bounds():
    deref = _dereference(CritiqueOutput.model_json_schema())
    claim_span = deref["properties"]["issues"]["items"]["properties"]["claim_span"]
    assert claim_span["minLength"] == 1
    assert claim_span["maxLength"] == MAX_SPAN


def test_dereferencing_preserves_additional_properties_false_on_every_object():
    deref = _dereference(CritiqueOutput.model_json_schema())
    assert deref["additionalProperties"] is False
    issue_schema = deref["properties"]["issues"]["items"]
    assert issue_schema["additionalProperties"] is False
    locus_schema = issue_schema["properties"]["locus"]
    assert locus_schema["additionalProperties"] is False


def test_strictify_still_closes_every_object_after_dereferencing():
    """`_strictify` must keep working on the dereferenced result, not just the raw one
    `tests/test_report_store_llm.py::test_strictify_closes_every_object` already covers."""
    strict = _strictify(_dereference(CritiqueOutput.model_json_schema()))

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(strict)


# ------------------------------------------------------------- semantic equivalence


def _validates(payload: Any, schema: dict) -> bool:
    """A minimal, total JSON-Schema-subset checker covering exactly the shapes this
    repo's schemas use: object/array/string/integer/boolean/null, `properties`,
    `required`, `additionalProperties`, `items`, `enum`, `anyOf`, `minLength`/
    `maxLength`, `minimum`/`maximum`. Not a general validator — just enough to prove
    dereferencing did not change what a payload validates against.
    """
    if "enum" in schema:
        return payload in schema["enum"]
    if "anyOf" in schema:
        return any(_validates(payload, sub) for sub in schema["anyOf"])
    kind = schema.get("type")
    if kind == "null":
        return payload is None
    if kind == "boolean":
        return isinstance(payload, bool)
    if kind == "integer":
        if not isinstance(payload, int) or isinstance(payload, bool):
            return False
        if "minimum" in schema and payload < schema["minimum"]:
            return False
        return not ("maximum" in schema and payload > schema["maximum"])
    if kind == "string":
        if not isinstance(payload, str):
            return False
        if "minLength" in schema and len(payload) < schema["minLength"]:
            return False
        return not ("maxLength" in schema and len(payload) > schema["maxLength"])
    if kind == "array":
        if not isinstance(payload, list):
            return False
        return all(_validates(item, schema["items"]) for item in payload)
    if kind == "object" or "properties" in schema:
        if not isinstance(payload, dict):
            return False
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in payload:
                return False
        if schema.get("additionalProperties") is False and not set(payload).issubset(properties):
            return False
        return all(
            _validates(payload[key], properties[key]) for key in payload if key in properties
        )
    raise AssertionError(f"unsupported schema shape in test helper: {schema!r}")  # pragma: no cover


_VALID_PAYLOAD = {
    "issues": [
        {
            "category": "fabricated_citation",
            "severity": "blocking",
            "locus": {"section": 1, "paragraph": 2},
            "claim_span": "x",
            "rationale": "y",
            "instruction": "z",
            "related_span": None,
            "citation_id": None,
            "expected_support": None,
        }
    ]
}


def test_a_payload_that_validates_against_the_original_schema_still_validates_dereferenced():
    # It validates against the real pydantic model — the ground truth the schema is
    # generated from — so the dereferenced JSON Schema must accept it too.
    CritiqueOutput.model_validate(_VALID_PAYLOAD)
    assert _validates(_VALID_PAYLOAD, _dereference(CritiqueOutput.model_json_schema()))


@pytest.mark.parametrize(
    "broken",
    [
        {**_VALID_PAYLOAD, "extra_top_level_key": True},
        {"issues": [{**_VALID_PAYLOAD["issues"][0], "category": "not_a_real_category"}]},
        {"issues": [{**_VALID_PAYLOAD["issues"][0], "claim_span": ""}]},  # below minLength
    ],
)
def test_a_payload_pydantic_rejects_is_also_rejected_by_the_dereferenced_schema(broken):
    with pytest.raises(Exception):  # noqa: B017 - ValidationError, deliberately broad
        CritiqueOutput.model_validate(broken)
    assert not _validates(broken, _dereference(CritiqueOutput.model_json_schema()))


# ------------------------------------------------------------------------ the guard


def test_a_recursive_ref_cycle_raises_instead_of_hanging():
    """Timeout-independent by construction: the guard is a path-sensitive stack that
    raises the moment a `$defs` name recurs into itself, so this either raises
    immediately or the guard itself is broken — nothing here can hang."""
    recursive_schema = {
        "$ref": "#/$defs/Node",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}},
            }
        },
    }
    with pytest.raises(ValueError, match="recursive"):
        _dereference(recursive_schema)


def test_a_ref_to_an_undefined_defs_entry_raises():
    with pytest.raises(ValueError, match="undefined"):
        _dereference({"$ref": "#/$defs/Missing", "$defs": {}})


def test_an_unsupported_ref_form_raises_rather_than_passing_through_unresolved():
    with pytest.raises(ValueError, match="cannot dereference"):
        _dereference({"$ref": "external.json#/Thing"})


def test_repeated_use_of_the_same_defs_entry_is_not_mistaken_for_a_cycle():
    """Two sibling fields sharing a `$defs` type is completely ordinary — pydantic does
    exactly this for `CritiqueOutput`, where every `RawIssue.locus` reuses `StructuralRef`
    and every issue reuses `RawIssue` itself across the whole `issues` list. The guard
    tracks the in-progress resolution path, not every name ever seen."""
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/Leaf"}, "b": {"$ref": "#/$defs/Leaf"}},
        "$defs": {"Leaf": {"type": "string"}},
    }
    deref = _dereference(schema)
    assert deref["properties"]["a"] == {"type": "string"}
    assert deref["properties"]["b"] == {"type": "string"}


# ------------------------------------------------------- wired into `structured()`


class _Nested(BaseModel):
    value: str


class _WithRef(BaseModel):
    inner: _Nested


def _sdk_scripted(client: LLMClient, contents: list, record: list | None = None):
    seq = iter(contents)

    def fake_create(alias, kwargs):
        if record is not None:
            record.append(kwargs)
        return _Reply(message={"role": "assistant", "content": next(seq)}, reported=alias,
                      prompt_tokens=1, completion_tokens=1)

    client._create = fake_create  # type: ignore[method-assign]


def test_structured_sends_a_ref_free_request_body(client):
    """The outgoing request — not just the schema in isolation — carries no `$ref`.
    Mirrors the monkeypatch-`_create` pattern in `tests/test_llm_tools.py`."""
    calls: list[dict] = []
    _sdk_scripted(client, ['{"inner": {"value": "ok"}}'], record=calls)

    result = client.structured(
        "writer-a", system="s", user="u", schema=_WithRef, mode="json_schema"
    )

    assert result.inner.value == "ok"
    body = calls[0]
    _assert_ref_free(body["response_format"])
    assert "$ref" not in body["messages"][-1]["content"]  # the instruction text too


def test_structured_prompt_mode_instruction_is_also_ref_free(client):
    """`prompt` mode has no `response_format` at all — the schema only reaches the
    model through the rendered instruction text, so that is the one place this mode
    needs checked."""
    calls: list[dict] = []
    _sdk_scripted(client, ['{"inner": {"value": "ok"}}'], record=calls)

    client.structured("writer-a", system="s", user="u", schema=_WithRef, mode="prompt")

    instruction = calls[0]["messages"][-1]["content"]
    assert "$ref" not in instruction
    assert "$defs" not in instruction


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
def test_dereferencing_is_behaviour_preserving_across_every_mode(client, mode):
    """`$ref`-accepting providers (OpenAI, the OpenRouter-served open models) must see
    unchanged behaviour: a dereferenced schema is semantically identical, so the same
    valid payload still parses and validates in every mode, not just the Anthropic
    fallback path this fix targets."""
    calls: list[dict] = []
    _sdk_scripted(client, ['{"inner": {"value": "ok"}}'], record=calls)

    result = client.structured("writer-a", system="s", user="u", schema=_WithRef, mode=mode)

    assert result.inner.value == "ok"
