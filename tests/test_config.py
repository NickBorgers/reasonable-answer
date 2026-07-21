"""Proxy configuration resolution — D21's env override for `base_url`.

Fully offline: nothing here touches the proxy. It asserts the precedence
env value > roster file value > built-in default, and that the override is
byte-for-byte what every downstream reader (`LLMClient`) sees.
"""

from __future__ import annotations

import pytest

from reasonable_answer.config import ProxyConfig

_DEFAULT = "https://llm.featherback-mermaid.ts.net/v1"


def test_base_url_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("RA_PROXY_BASE_URL", raising=False)
    assert ProxyConfig().base_url == _DEFAULT


def test_env_overrides_the_file_value(monkeypatch):
    monkeypatch.setenv("RA_PROXY_BASE_URL", "http://litellm-proxy:4000/v1")
    proxy = ProxyConfig(base_url="http://from-file:9000/v1")
    assert proxy.base_url == "http://litellm-proxy:4000/v1"


def test_file_value_wins_when_env_unset(monkeypatch):
    monkeypatch.delenv("RA_PROXY_BASE_URL", raising=False)
    assert ProxyConfig(base_url="http://from-file:9000/v1").base_url == "http://from-file:9000/v1"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_env_does_not_override(monkeypatch, blank):
    # An exported-but-empty variable is the common "unset" shape in a .env / compose
    # file and must not clobber the file value with an empty base_url.
    monkeypatch.setenv("RA_PROXY_BASE_URL", blank)
    proxy = ProxyConfig(base_url="http://from-file:9000/v1")
    assert proxy.base_url == "http://from-file:9000/v1"


def test_env_var_name_is_configurable(monkeypatch):
    monkeypatch.delenv("RA_PROXY_BASE_URL", raising=False)
    monkeypatch.setenv("MY_PROXY", "http://custom:1234/v1")
    proxy = ProxyConfig(base_url_env="MY_PROXY", base_url="http://from-file:9000/v1")
    assert proxy.base_url == "http://custom:1234/v1"


def test_override_reaches_the_llm_client(monkeypatch):
    # The whole point of the issue: the resolved URL is what the client and its
    # /model/info probe address, not the baked file value.
    from reasonable_answer.config import Budgets, Config, Roster
    from reasonable_answer.llm import LLMClient

    monkeypatch.setenv("RA_PROXY_BASE_URL", "http://litellm-proxy:4000/v1")
    config = Config(
        proxy=ProxyConfig(base_url="http://from-file:9000/v1"),
        roster=Roster(
            writers=["writer-a"],
            critics={
                "logic": ["logic-spec"],
                "evidence": ["evidence-spec"],
                "completeness": ["completeness-spec"],
            },
        ),
        budgets=Budgets(min_ticks=1, hard_cap=3),
    )
    client = LLMClient(config)
    assert str(client._client.base_url).rstrip("/") == "http://litellm-proxy:4000/v1"
