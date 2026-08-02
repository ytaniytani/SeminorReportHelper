from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from seminar_report.llm.base import LLMProvider
from seminar_report.media.audio import ffmpeg_path
from seminar_report.models import Segment, Transcript


class MockProvider(LLMProvider):
    """決められた応答を順に返す単純なテスト用プロバイダ。"""

    name = "mock"
    supports_vision = False
    context_chars = 50_000

    def __init__(self, responses: list[str] | None = None, default: str = "本文です。") -> None:
        self.responses = list(responses or [])
        self.default = default
        self.prompts: list[str] = []

    def complete(self, prompt, system=None, max_tokens=4096, temperature=0.3) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else self.default


class ScriptedProvider(LLMProvider):
    """プロンプトの種類を見分けて、それらしい応答を返すテスト用プロバイダ。

    呼び出し順に依存しないので、生成ロジックを変えてもテストが壊れにくい。
    """

    name = "scripted"
    supports_vision = False
    context_chars = 50_000

    def __init__(self, capture_time: str = "00:00:25") -> None:
        self.capture_time = capture_time
        self.calls: list[str] = []

    def complete(self, prompt, system=None, max_tokens=4096, temperature=0.3) -> str:
        if "分割中の" in prompt:
            self.calls.append("summary")
            return json.dumps(
                {
                    "summary": "アーキテクチャと導入効果について説明された。",
                    "topics": ["[00:00:10] アーキテクチャ", "[00:00:25] 処理件数の推移"],
                },
                ensure_ascii=False,
            )

        if "レポートの構成を設計してください" in prompt:
            self.calls.append("outline")
            return "```json\n" + json.dumps(
                {
                    "title": "社内AI活用セミナー",
                    "key_points": ["導入効果は明確", "運用体制が課題"],
                    "sections": [
                        {
                            "title": "アーキテクチャ",
                            "start": "00:00:00",
                            "end": "00:00:40",
                            "focus": "全体構成を説明する",
                        },
                        {
                            "title": "導入効果",
                            "start": "00:00:40",
                            "end": "00:01:10",
                            "focus": "効果と課題をまとめる",
                        },
                    ],
                },
                ensure_ascii=False,
            ) + "\n```"

        if "冒頭に置く「概要」" in prompt:
            self.calls.append("overview")
            return "本セミナーでは社内 AI 活用の事例が共有された。"

        if "目標は約" in prompt:
            self.calls.append("rewrite")
            # マーカーを保持したまま返す(実際の LLM に課している制約と同じ)
            return prompt.split("--- 対象の文章 ---")[-1].strip()

        self.calls.append("section")
        return (
            "セクションの本文です。要点を整理して記述しています。\n\n"
            f"[[capture:{self.capture_time}|処理件数の推移]]\n\n"
            "続きの説明が入ります。"
        )


@pytest.fixture
def transcript() -> Transcript:
    segments = [
        Segment(start=0.0, end=10.0, text="本日はセミナーにご参加ありがとうございます。"),
        Segment(start=10.0, end=25.0, text="まず全体のアーキテクチャをご覧ください。"),
        Segment(start=25.0, end=48.0, text="このグラフが処理件数の推移を示しています。"),
        Segment(start=48.0, end=70.0, text="以上をまとめると、導入効果は明確でした。"),
    ]
    return Transcript(segments=segments, language="ja", duration=70.0, model="tiny")


@pytest.fixture
def scripted() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """テスト用の 70 秒動画を合成する。

    時刻によって絵が変わるパターンを使い、フレーム抽出が実際に指定時刻の
    絵を取れているか確認できるようにしている。
    """
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    result = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=10:duration=70",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=70",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"テスト動画を生成できませんでした: {result.stderr[-300:]}")
    return path
