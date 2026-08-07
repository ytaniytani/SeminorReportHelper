"""Claude (Anthropic API) プロバイダ。既定。"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from seminar_report.config import get_settings
from seminar_report.llm.base import LLMError, LLMProvider


class ClaudeProvider(LLMProvider):
    name = "claude"
    supports_vision = True
    # 200k トークン相当。日本語は 1 トークン ≒ 1 文字前後なので余裕を見て設定。
    context_chars = 300_000

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.claude_model
        key = api_key or settings.anthropic_api_key
        if not key:
            raise LLMError(
                "ANTHROPIC_API_KEY が設定されていません。.env に設定してください。"
            )
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "anthropic が入っていません。"
                "`uv pip install 'seminar-report-helper[llm]'` を実行してください。"
            ) from exc
        self._client = anthropic.Anthropic(api_key=key)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        # temperature / top_p / top_k は Claude Opus 4.7 以降と Sonnet 5 で廃止され、
        # 送ると 400 (`temperature` is deprecated for this model) になる。
        # 既定モデルがこの世代なので送らない。出力の振れ幅はプロンプト側で制御する。
        # 引数は openai / ollama と共通のインターフェースのため残している。
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    def describe_image(self, image: Path, prompt: str, max_tokens: int = 300) -> str:
        media_type = mimetypes.guess_type(image.name)[0] or "image/jpeg"
        data = base64.standard_b64encode(image.read_bytes()).decode("ascii")
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
