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
    ) -> dict:
        """JSON オブジェクトを期待して生成する。

        モデルによっては指示を無視して思考過程や前置きを書き始め、JSON を
        一度も出力しないまま終わることがある(特に推論指向のモデルで起きやすい)。
        また、JSON 自体は出力していても配列や文字列など辞書以外の形で返して
        しまうことがある(呼び出し側はすべて辞書を前提に `.get()` している)。
        どちらの場合も「JSON オブジェクトのみを出力せよ」と念押しして
        1 回だけ再試行する。
        """
        text = self.complete(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        data = self._extract_json_object(text)
        if data is not None:
            return data

        retry_prompt = (
            f"{prompt}\n\n"
            "厳守: 出力は JSON オブジェクトのみ。説明・前置き・思考過程・"
            "コードフェンスは一切書かないこと。JSON の前後に文字を置かないこと。"
        )
        text = self.complete(
            retry_prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )
        data = self._extract_json_object(text)
        if data is not None:
            return data
        raise LLMError(f"JSON オブジェクトとして解釈できない応答です: {text[:200]}")

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        try:
            data = extract_json(text)
        except LLMError:
            return None
        # 配列や文字列がそのまま返ってくることがある。呼び出し側は辞書前提
        # なので、それ以外は「JSON として解釈できなかった」のと同じ扱いにする。
        return data if isinstance(data, dict) else None

    def describe_image(self, image: Path, prompt: str, max_tokens: int = 300) -> str:
        """画像を説明する。Vision 非対応なら空文字を返す。"""
        return ""
