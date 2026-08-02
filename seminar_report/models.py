"""パイプライン全体で受け渡すデータ構造。"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


def format_timestamp(seconds: float) -> str:
    """秒数を HH:MM:SS 形式にする。"""
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_timestamp(text: str) -> float | None:
    """HH:MM:SS または MM:SS を秒数にする。解釈できなければ None。"""
    parts = text.strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    if len(parts) == 2:
        minutes, secs = values
        return minutes * 60 + secs
    hours, minutes, secs = values
    return hours * 3600 + minutes * 60 + secs


class Segment(BaseModel):
    """文字起こしの 1 セグメント。"""

    start: float
    end: float
    text: str

    @property
    def timestamp(self) -> str:
        return format_timestamp(self.start)


class Transcript(BaseModel):
    """動画 1 本ぶんの文字起こし。"""

    segments: list[Segment] = Field(default_factory=list)
    language: str | None = None
    duration: float = 0.0
    model: str | None = None

    @property
    def full_text(self) -> str:
        return "\n".join(s.text.strip() for s in self.segments if s.text.strip())

    def to_timestamped_text(self, start: float = 0.0, end: float | None = None) -> str:
        """LLM に渡す `[HH:MM:SS] 発言` 形式。時刻の引用元になる。"""
        lines = []
        for seg in self.segments:
            if seg.start < start:
                continue
            if end is not None and seg.start >= end:
                break
            text = seg.text.strip()
            if text:
                lines.append(f"[{seg.timestamp}] {text}")
        return "\n".join(lines)

    def char_count(self) -> int:
        return sum(len(s.text) for s in self.segments)


class Capture(BaseModel):
    """本文に挿入する 1 枚のキャプチャ画像。"""

    marker_id: str
    requested_time: float
    """LLM が指定した時刻(スナップ前)。"""
    resolved_time: float
    """実際にフレームを取り出した時刻。"""
    caption: str
    image_path: Path | None = None
    filename: str | None = None
    candidates: list[Path] = Field(default_factory=list)
    """差し替え用の候補フレーム。Web UI から選び直せる。"""
    included: bool = True
    """ユーザーが除外した画像は False。"""

    @property
    def timestamp(self) -> str:
        return format_timestamp(self.resolved_time)


class Section(BaseModel):
    """レポートの 1 セクション。"""

    title: str
    body: str
    """`[[capture:HH:MM:SS|キャプション]]` マーカーを含みうる Markdown。"""
    start: float = 0.0
    end: float = 0.0


class ChunkSummary(BaseModel):
    """map 段で作る、動画の一部分の要点。"""

    start: float
    end: float
    summary: str
    topics: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """生成されたレポート全体。"""

    title: str
    overview: str = ""
    sections: list[Section] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    captures: list[Capture] = Field(default_factory=list)
    source_video: str | None = None
    duration: float = 0.0

    def body_char_count(self) -> int:
        return len(self.overview) + sum(len(s.body) for s in self.sections)


class DetailLevel(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"
    CUSTOM = "custom"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobStep(str, Enum):
    AUDIO = "audio"
    TRANSCRIBE = "transcribe"
    SUMMARIZE = "summarize"
    OUTLINE = "outline"
    WRITE = "write"
    CAPTURE = "capture"
    RENDER = "render"


STEP_LABELS: dict[JobStep, str] = {
    JobStep.AUDIO: "音声を抽出しています",
    JobStep.TRANSCRIBE: "文字起こしをしています",
    JobStep.SUMMARIZE: "内容を要約しています",
    JobStep.OUTLINE: "章立てを設計しています",
    JobStep.WRITE: "レポートを執筆しています",
    JobStep.CAPTURE: "重要箇所をキャプチャしています",
    JobStep.RENDER: "出力を生成しています",
}


class Progress(BaseModel):
    """Web UI に SSE で流す進捗イベント。"""

    step: JobStep
    label: str = ""
    ratio: float = 0.0
    """0.0〜1.0。不明な場合は 0。"""
    detail: str = ""

    def model_post_init(self, _context: object) -> None:
        if not self.label:
            self.label = STEP_LABELS.get(self.step, self.step.value)
