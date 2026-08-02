"""Ollama プロバイダ。完全ローカル運用のための選択肢。

コンテキスト長が小さいモデルが多いため context_chars を控えめにしてあり、
レポート生成側が自動的に細かくチャンク分割する。
"""

from __future__ import annotations

import httpx

from seminar_report.config import get_settings
from seminar_report.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"
    supports_vision = False
    context_chars = 20_000

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.ollama_model
        self.host = (host or settings.ollama_host).rstrip("/")

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        try:
            response = httpx.post(
                f"{self.host}/api/generate", json=payload, timeout=600.0
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama への接続に失敗しました: {exc}") from exc
        return (response.json().get("response") or "").strip()
