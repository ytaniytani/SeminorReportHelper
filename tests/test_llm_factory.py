"""LLM プロバイダ/モデル名の解決(resolve_provider_and_model)のテスト。

UI に「今どのモデルで動いているか」を表示するために、実際に使われる
provider/model を get_provider() を呼ばず(=クライアント初期化やAPIキー
確認を伴わず)に解決できることを確認する。
"""

from __future__ import annotations

from seminar_report.config import get_settings
from seminar_report.llm import resolve_provider_and_model


def test_resolves_to_settings_default_when_nothing_specified(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "claude")
    monkeypatch.setattr(settings, "claude_model", "claude-sonnet-5")

    provider, model = resolve_provider_and_model(None, None)

    assert provider == "claude"
    assert model == "claude-sonnet-5"


def test_provider_specified_uses_that_providers_default_model(monkeypatch) -> None:
    """provider だけ指定した場合、settings.llm_provider ではなく指定した provider の既定モデルを使う。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "claude")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o")

    provider, model = resolve_provider_and_model("openai", None)

    assert provider == "openai"
    assert model == "gpt-4o"


def test_explicit_model_is_kept_as_is() -> None:
    provider, model = resolve_provider_and_model("openai", "gpt-4o-mini")
    assert provider == "openai"
    assert model == "gpt-4o-mini"


def test_provider_name_is_lowercased() -> None:
    provider, _ = resolve_provider_and_model("Claude", "x")
    assert provider == "claude"
