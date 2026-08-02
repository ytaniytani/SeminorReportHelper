"""LLM プロバイダのファクトリ。"""

from __future__ import annotations

from seminar_report.config import get_settings
from seminar_report.llm.base import LLMError, LLMProvider, extract_json

PROVIDERS = ("claude", "openai", "ollama")

# ウェブUI のプルダウン表示用。実際にはどんなモデル名でも指定可能(自由入力欄も残す)。
MODEL_CHOICES: dict[str, list[str]] = {
    "claude": [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "o3", "o3-mini"],
    "ollama": ["qwen2.5:14b", "llama3.1:8b", "gemma2:9b"],
}


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


__all__ = ["LLMError", "LLMProvider", "MODEL_CHOICES", "PROVIDERS", "extract_json", "get_provider"]
