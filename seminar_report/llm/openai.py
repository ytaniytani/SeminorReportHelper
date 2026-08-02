"""OpenAI プロバイダ。"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from seminar_report.config import get_settings
from seminar_report.llm.base import LLMError, LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"
    supports_vision = True
    context_chars = 200_000

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.openai_model
        key = api_key or settings.openai_api_key
        if not key:
            raise LLMError(
                "OPENAI_API_KEY が設定されていません。.env に設定してください。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMError(
                "openai が入っていません。"
                "`uv pip install 'seminar-report-helper[llm]'` を実行してください。"
            ) from exc
        self._client = OpenAI(api_key=key)

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()

    def describe_image(self, image: Path, prompt: str, max_tokens: int = 300) -> str:
        media_type = mimetypes.guess_type(image.name)[0] or "image/jpeg"
        data = base64.standard_b64encode(image.read_bytes()).decode("ascii")
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        },
                    ],
                }
            ],
        )
        return (response.choices[0].message.content or "").strip()
