"""LLM プロバイダのファクトリ。"""

from __future__ import annotations

from seminar_report.config import get_settings
from seminar_report.llm.base import LLMError, LLMProvider, extract_json

PROVIDERS = ("claude", "openai", "ollama")


def get_provider(name: str | None = None, **kwargs) -> LLMProvider:
    """名前からプロバイダを生成する。未指定なら設定値を使う。"""
    name = (name or get_settings().llm_provider).lower()

    if name == "claude":
        from seminar_report.llm.claude import ClaudeProvider

        return ClaudeProvider(**kwargs)
    if name == "openai":
        from seminar_report.llm.openai import OpenAIProvider

        return OpenAIProvider(**kwargs)
    if name == "ollama":
        from seminar_report.llm.ollama import OllamaProvider

        return OllamaProvider(**kwargs)

    raise LLMError(f"未知の LLM プロバイダです: {name} (利用可能: {', '.join(PROVIDERS)})")


__all__ = ["LLMError", "LLMProvider", "PROVIDERS", "extract_json", "get_provider"]
