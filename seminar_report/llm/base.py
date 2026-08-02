"""LLM プロバイダの共通インターフェース。

レポート生成の中身はこの抽象に対してだけ書き、Claude / OpenAI / Ollama を
差し替え可能にする。Vision に対応しないプロバイダでは画像検証を自動で飛ばす。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """LLM の応答から JSON を取り出す。

    コードフェンスや前後の説明文が混ざることがあるため、素の json.loads に
    失敗したら最初の `{`/`[` から最後の `}`/`]` までを切り出して再挑戦する。
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"JSON として解釈できない応答です: {text[:200]}")


class LLMProvider(ABC):
    """テキスト生成と(任意で)画像理解を提供する。"""

    name: str = "base"
    supports_vision: bool = False
    context_chars: int = 400_000
    """1 リクエストに投入してよい入力文字数の目安。チャンク分割幅の決定に使う。"""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """テキストを生成する。"""

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> Any:
        """JSON を期待して生成する。"""
        return extract_json(
            self.complete(
                prompt, system=system, max_tokens=max_tokens, temperature=temperature
            )
        )

    def describe_image(self, image: Path, prompt: str, max_tokens: int = 300) -> str:
        """画像を説明する。Vision 非対応なら空文字を返す。"""
        return ""
