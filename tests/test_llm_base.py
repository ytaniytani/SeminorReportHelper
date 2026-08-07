"""complete_json の JSON 抽出・再試行ロジック。"""

from __future__ import annotations

import pytest
from conftest import MockProvider

from seminar_report.llm.base import LLMError, extract_json


def test_extract_json_handles_plain_json() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_raises_when_no_braces_at_all() -> None:
    with pytest.raises(LLMError):
        extract_json("説明だけで JSON がありません。")


def test_complete_json_retries_when_model_writes_reasoning_instead_of_json() -> None:
    """推論指向のモデルが説明・思考過程だけ書いて JSON を出さないことがある。

    その場合、1 回だけ「JSON のみ出力せよ」と念押しして再試行すること。
    """
    provider = MockProvider(
        responses=[
            "Let's think about this step by step. まず要件を整理すると…",
            '{"title": "T", "key_points": [], "sections": []}',
        ]
    )

    result = provider.complete_json("章立てを設計してください")

    assert result == {"title": "T", "key_points": [], "sections": []}
    assert len(provider.prompts) == 2
    assert "JSON オブジェクトのみ" in provider.prompts[1]


def test_complete_json_raises_when_retry_also_fails() -> None:
    provider = MockProvider(
        responses=["説明その1です。", "説明その2です。"],
    )

    with pytest.raises(LLMError):
        provider.complete_json("章立てを設計してください")
