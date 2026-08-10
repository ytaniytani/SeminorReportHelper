"""合成動画を使った端から端までの結合テスト。

Whisper と LLM は実行時間とコストの都合でモックし、それ以外(ffmpeg による
フレーム抽出、マーカー解決、キャプチャ選抜、レンダリング、ZIP 出力)は
実物を通す。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from conftest import ScriptedProvider

from seminar_report import pipeline as pipeline_module
from seminar_report.models import DetailLevel, Transcript
from seminar_report.pipeline import PipelineOptions, export_zip, run_pipeline
from seminar_report.render.confluence_storage import wrap_for_validation


@pytest.fixture
def patched(monkeypatch, transcript: Transcript) -> ScriptedProvider:
    """文字起こしと LLM を差し替える。"""
    provider = ScriptedProvider()
    monkeypatch.setattr(pipeline_module, "transcribe", lambda *a, **k: transcript)
    monkeypatch.setattr(pipeline_module, "get_provider", lambda *a, **k: provider)
    return provider


def test_model_preparation_is_reported(
    monkeypatch, sample_video: Path, tmp_path: Path, transcript: Transcript
) -> None:
    """モデル準備中の状況が進捗に流れること。

    初回はモデルの DL に数分かかる。ここで無言になると UI 上は停止に見え、
    利用者に「タイムアウトした」と判断されてしまう。
    """
    def fake_transcribe(video, on_status=None, **kwargs):
        if on_status:
            on_status("medium モデルを読み込んでいます")
        return transcript

    monkeypatch.setattr(pipeline_module, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline_module, "get_provider", lambda *a, **k: ScriptedProvider())

    events: list[tuple[str, str]] = []
    run_pipeline(
        sample_video,
        tmp_path / "out",
        PipelineOptions(detail=DetailLevel.BRIEF),
        on_progress=lambda step, ratio, detail: events.append((step.value, detail)),
    )

    model_events = [detail for step, detail in events if step == "model"]
    assert model_events == ["medium モデルを読み込んでいます"]


def test_pipeline_produces_report_with_images(
    sample_video: Path, tmp_path: Path, patched: ScriptedProvider
) -> None:
    steps: list[str] = []
    result = run_pipeline(
        sample_video,
        tmp_path / "out",
        PipelineOptions(detail=DetailLevel.STANDARD),
        on_progress=lambda step, ratio, detail: steps.append(step.value),
    )

    report = result.report
    # 本文の見出し1(タイトル)は元動画のファイル名(拡張子なし)にする
    assert report.title == "sample"
    assert report.source_video == "sample.mp4"
    assert len(report.sections) == 2

    # マーカーが実画像に解決されている
    included = [c for c in report.captures if c.included]
    assert included, "画像が 1 枚も採用されなかった"
    for capture in included:
        assert capture.image_path is not None and capture.image_path.exists()
        assert capture.image_path.stat().st_size > 0
        assert capture.candidates, "差し替え候補が残っていない"

    # 出力ファイル一式
    assert result.markdown_path.exists()
    assert result.storage_path.exists()
    assert result.report_json_path.exists()

    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "# sample" in markdown
    assert "![" in markdown
    assert "元動画" not in markdown

    storage = result.storage_path.read_text(encoding="utf-8")
    ET.fromstring(wrap_for_validation(storage))
    assert "ri:attachment" in storage

    # 進捗が各段階から報告されている
    assert {"audio", "summarize", "outline", "write", "capture", "render"} <= set(steps)


def test_export_zip_bundles_everything(
    sample_video: Path, tmp_path: Path, patched: ScriptedProvider
) -> None:
    out = tmp_path / "out"
    result = run_pipeline(sample_video, out, PipelineOptions(detail=DetailLevel.BRIEF))
    zip_path = export_zip(out, result.report)

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()

    assert "report.md" in names
    assert "report.confluence.xml" in names
    assert "report.html" in names
    assert any(name.startswith("images/") for name in names)

    html = (out / "report.html").read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in html


def test_no_images_option_skips_capture(
    sample_video: Path, tmp_path: Path, patched: ScriptedProvider
) -> None:
    result = run_pipeline(
        sample_video,
        tmp_path / "out",
        PipelineOptions(detail=DetailLevel.BRIEF, include_images=False),
    )
    assert all(not c.included for c in result.report.captures)
    assert "ri:attachment" not in result.storage_path.read_text(encoding="utf-8")


def test_captures_are_visually_distinct(
    sample_video: Path, tmp_path: Path, patched: ScriptedProvider
) -> None:
    """同一時刻を指す 2 つのマーカーがあっても、同じ絵は 2 度採用しない。"""
    result = run_pipeline(sample_video, tmp_path / "out", PipelineOptions())
    included = [c for c in result.report.captures if c.included]
    # 両セクションとも 00:00:25 を指しているため、採用されるのは 1 枚のはず
    assert len(included) <= 1
