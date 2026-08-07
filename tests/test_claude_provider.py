"""Claude プロバイダのリクエスト組み立て。

Claude Opus 4.7 以降と Sonnet 5 では temperature / top_p / top_k が廃止され、
送ると 400 (`temperature` is deprecated for this model) でレポート生成が失敗する。
既定モデルがこの世代のため、送っていないことを保証する。
"""

from __future__ import annotations

import sys
import types

import pytest

from seminar_report.llm import MODEL_CHOICES
from seminar_report.llm.base import LLMError


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = types.SimpleNamespace(type="text", text="  ok  ")
        return types.SimpleNamespace(content=[block])


class _FakeClient:
    def __init__(self, **_: object) -> None:
        self.messages = _FakeMessages()


@pytest.fixture
def provider(monkeypatch):
    """anthropic SDK を差し替えた ClaudeProvider。"""
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    from seminar_report.llm.claude import ClaudeProvider

    return ClaudeProvider(model="claude-sonnet-5", api_key="test-key")


def test_complete_omits_deprecated_sampling_params(provider) -> None:
    provider.complete("こんにちは", system="あなたは要約者です", max_tokens=1500)

    sent = provider._client.messages.calls[-1]
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "top_k" not in sent


def test_complete_still_sends_required_fields(provider) -> None:
    """temperature を外したことで他のパラメータが欠けていないこと。"""
    provider.complete("こんにちは", system="あなたは要約者です", max_tokens=1500)

    sent = provider._client.messages.calls[-1]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["max_tokens"] == 1500
    assert sent["system"] == "あなたは要約者です"
    assert sent["messages"] == [{"role": "user", "content": "こんにちは"}]


def test_system_omitted_when_not_given(provider) -> None:
    provider.complete("こんにちは")

    assert "system" not in provider._client.messages.calls[-1]


def test_complete_returns_stripped_text(provider) -> None:
    assert provider.complete("こんにちは") == "ok"


def test_temperature_argument_is_accepted_but_not_forwarded(provider) -> None:
    """呼び出し側(complete_json 等)は temperature を渡してくるが、無視されること。"""
    provider.complete("こんにちは", temperature=0.9)

    assert "temperature" not in provider._client.messages.calls[-1]


def test_missing_api_key_is_reported_clearly(monkeypatch) -> None:
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeClient
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    from seminar_report.llm.claude import ClaudeProvider

    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider(model="claude-sonnet-5", api_key="")


def test_offered_models_are_current_generation() -> None:
    """UI の選択肢が temperature 廃止世代であること(この前提でパラメータを外している)。"""
    assert "claude-sonnet-5" in MODEL_CHOICES["claude"]
    assert "claude-opus-5" in MODEL_CHOICES["claude"]
