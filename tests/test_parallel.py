"""LLM 呼び出しの並列化。

並列にしても「出力順が入力順と一致する」「キャプチャ予算を超えない」という
2 つの不変条件が崩れないことを確認する。ここが崩れると、章の順序が入れ替わったり
プリセットより多い画像が入ったりする。
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from conftest import ScriptedProvider

from seminar_report.llm.base import LLMProvider
from seminar_report.models import DetailLevel, Transcript
from seminar_report.report.detail import resolve_detail
from seminar_report.report.generator import _map_ordered, generate_report


def test_map_ordered_preserves_input_order() -> None:
    """完了順がばらけても、結果は必ず入力順に戻ること。"""
    # 後ろの要素ほど速く終わるようにして、完了順を意図的に逆転させる
    def work(index: int, item: int) -> int:
        time.sleep((10 - item) * 0.01)
        return item * 2

    assert _map_ordered(work, list(range(10))) == [i * 2 for i in range(10)]


def test_map_ordered_reports_monotonic_progress() -> None:
    seen: list[int] = []
    _map_ordered(lambda i, x: x, list(range(8)), seen.append)

    assert seen == sorted(seen)
    assert seen[-1] == 8


def test_map_ordered_handles_empty_and_single() -> None:
    assert _map_ordered(lambda i, x: x, []) == []
    assert _map_ordered(lambda i, x: x * 3, [7]) == [21]


def test_map_ordered_propagates_errors() -> None:
    def explode(index: int, item: int) -> int:
        if item == 3:
            raise ValueError("失敗")
        return item

    with pytest.raises(ValueError, match="失敗"):
        _map_ordered(explode, list(range(6)))


class ConcurrencyProbe(ScriptedProvider):
    """同時に走った本数を記録するプロバイダ。"""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def complete(self, prompt, system=None, max_tokens=4096, temperature=0.3) -> str:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            time.sleep(0.02)
            return super().complete(prompt, system, max_tokens, temperature)
        finally:
            with self._lock:
                self._active -= 1


def test_sections_are_written_concurrently(transcript: Transcript) -> None:
    """執筆が実際に並列化されていること(直列なら peak は 1 のまま)。"""
    provider = ConcurrencyProbe()
    generate_report(transcript, provider, resolve_detail(DetailLevel.STANDARD), "日本語")

    assert provider.peak > 1


def test_section_order_matches_outline(transcript: Transcript) -> None:
    """並列化後も章の順序が outline の指定どおりであること。"""
    report = generate_report(
        transcript, ScriptedProvider(), resolve_detail(DetailLevel.STANDARD), "日本語"
    )
    assert [s.title for s in report.sections] == ["アーキテクチャ", "導入効果"]


@pytest.mark.parametrize("budget", [0, 1, 2, 3])
def test_capture_budget_is_never_exceeded(transcript: Transcript, budget: int) -> None:
    """予算を各セクションに先に配る方式でも、合計が上限を超えないこと。"""
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=budget)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")

    assert len(report.captures) <= budget


def test_dropped_markers_are_removed_from_body(transcript: Transcript) -> None:
    """予算超過で落としたマーカーが本文に残らないこと。

    残っていると、画像の無いマーカー行がそのままレポートに出てしまう。
    """
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=1)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")

    kept = {c.marker_id for c in report.captures}
    for section in report.sections:
        for line in section.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("[[capture:"):
                marker_id = stripped.removeprefix("[[capture:").removesuffix("]]")
                assert marker_id in kept, f"落としたはずのマーカーが残っている: {stripped}"


class MultiCaptureProvider(LLMProvider):
    """1 セクションに複数のマーカーを埋めるプロバイダ。予算の切り詰めを試す。"""

    name = "multi"
    supports_vision = False
    context_chars = 50_000

    def complete(self, prompt, system=None, max_tokens=4096, temperature=0.3) -> str:
        if "分割中の" in prompt:
            return json.dumps({"summary": "要約", "topics": []}, ensure_ascii=False)
        if "レポートの構成を設計してください" in prompt:
            return json.dumps(
                {
                    "title": "テスト",
                    "key_points": ["要点"],
                    "sections": [
                        {"title": f"章{i}", "start": "00:00:00", "end": "00:01:10", "focus": ""}
                        for i in range(4)
                    ],
                },
                ensure_ascii=False,
            )
        if "冒頭に置く「概要」" in prompt:
            return "概要です。"
        if "目標は約" in prompt:
            return prompt.split("--- 対象の文章 ---")[-1].strip()
        return (
            "本文です。\n\n"
            "[[capture:00:00:10|図1]]\n\n"
            "続きます。\n\n"
            "[[capture:00:00:25|図2]]\n\n"
            "終わりです。"
        )


def test_budget_trims_across_sections(transcript: Transcript) -> None:
    """4 セクション × 各 2 枚を要求しても、全体上限で切り詰められること。"""
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=3)
    report = generate_report(transcript, MultiCaptureProvider(), spec, "日本語")

    assert len(report.captures) == 3
    # marker_id が重複していない(切り詰めで取り違えていない)
    assert len({c.marker_id for c in report.captures}) == 3
