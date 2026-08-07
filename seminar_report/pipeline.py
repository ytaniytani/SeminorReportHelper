"""パイプライン全体のオーケストレーション。

Web UI も CLI もここだけを呼ぶ。UI 層にロジックを置かないことで、
両者の振る舞いが食い違わないようにしている。
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from seminar_report.capture import build_captures
from seminar_report.config import get_settings
from seminar_report.llm import get_provider
from seminar_report.media.audio import probe_duration
from seminar_report.media.frames import CropBox
from seminar_report.models import DetailLevel, JobStep, Report, Transcript
from seminar_report.render.confluence_storage import render_storage
from seminar_report.render.html import render_html
from seminar_report.render.markdown import render_markdown
from seminar_report.report.detail import resolve_detail
from seminar_report.report.generator import generate_report
from seminar_report.transcribe.whisper import transcribe

ProgressFn = Callable[[JobStep, float, str], None]


@dataclass
class PipelineOptions:
    """1 回の実行に対する指定。"""

    detail: DetailLevel = DetailLevel.STANDARD
    target_chars: int | None = None
    max_captures: int | None = None
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    whisper_model: str | None = None
    whisper_language: str | None = None
    verify_captures: bool = False
    use_cache: bool = True
    include_images: bool = True
    capture_crop: CropBox | None = None
    """キャプチャ画像の切り出し矩形 (left, top, right, bottom)。0〜1 の割合。
    登壇者映像やロゴを含む画面からスライド部分だけを切り出したい場合に使う。"""
    user_request: str | None = None
    """依頼者からの要望(例: 「特に○○の箇所を重視した内容にして」)。
    章立ての設計・本文の重み付け・画像キャプチャの選定に反映される。"""


@dataclass
class PipelineResult:
    report: Report
    transcript: Transcript
    output_dir: Path
    markdown_path: Path
    storage_path: Path
    html_path: Path
    report_json_path: Path
    transcript_path: Path
    files: list[Path] = field(default_factory=list)


def run_pipeline(
    video: Path,
    output_dir: Path,
    options: PipelineOptions | None = None,
    on_progress: ProgressFn | None = None,
) -> PipelineResult:
    """動画 1 本を最後まで処理する。"""
    options = options or PipelineOptions()
    settings = get_settings()
    language = options.language or settings.report_language
    output_dir.mkdir(parents=True, exist_ok=True)

    def progress(step: JobStep, ratio: float = 0.0, detail: str = "") -> None:
        if on_progress:
            on_progress(step, ratio, detail)

    # 1-2. 音声抽出と文字起こし(抽出は transcribe の内部で行う)
    progress(JobStep.AUDIO, 0.0, "動画を読み込み中")
    duration = probe_duration(video)
    progress(JobStep.AUDIO, 1.0, f"長さ {duration / 60:.0f} 分")

    transcript = transcribe(
        video,
        model_size=options.whisper_model,
        language=options.whisper_language,
        on_progress=lambda ratio, detail: progress(JobStep.TRANSCRIBE, ratio, detail),
        use_cache=options.use_cache,
        on_status=lambda message: progress(JobStep.MODEL, 0.0, message),
    )
    if not transcript.segments:
        raise RuntimeError("文字起こしの結果が空でした。音声が含まれているか確認してください。")

    # 3-5. レポート生成
    provider_kwargs = {"model": options.model} if options.model else {}
    provider = get_provider(options.provider, **provider_kwargs)
    spec = resolve_detail(options.detail, options.target_chars, options.max_captures)
    report = generate_report(
        transcript, provider, spec, language, on_progress, options.user_request or ""
    )
    report.source_video = video.name
    report.duration = transcript.duration or duration

    # 6. キャプチャ
    if options.include_images and report.captures:
        build_captures(
            report,
            video,
            output_dir,
            provider=provider,
            verify=options.verify_captures,
            on_progress=on_progress,
            crop=options.capture_crop,
        )
    else:
        for capture in report.captures:
            capture.included = False

    # 7. 出力
    progress(JobStep.RENDER, 0.0, "ファイルを書き出し中")
    result = write_outputs(report, transcript, output_dir)
    progress(JobStep.RENDER, 1.0, "完了")
    return result


def write_outputs(report: Report, transcript: Transcript, output_dir: Path) -> PipelineResult:
    """Markdown / storage format / HTML / JSON を書き出す。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = output_dir / "report.md"
    storage_path = output_dir / "report.confluence.xml"
    html_path = output_dir / "report.html"
    report_json_path = output_dir / "report.json"
    transcript_path = output_dir / "transcript.json"

    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    storage_path.write_text(render_storage(report), encoding="utf-8")
    # 画像を base64 埋め込みにするため、キャプチャがファイルに書き出された後に呼ぶこと。
    html_path.write_text(render_html(report), encoding="utf-8")
    report_json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    files = [markdown_path, storage_path, html_path, report_json_path, transcript_path]
    files += [
        c.image_path for c in report.captures if c.included and c.image_path and c.image_path.exists()
    ]

    return PipelineResult(
        report=report,
        transcript=transcript,
        output_dir=output_dir,
        markdown_path=markdown_path,
        storage_path=storage_path,
        html_path=html_path,
        report_json_path=report_json_path,
        transcript_path=transcript_path,
        files=files,
    )


def export_zip(output_dir: Path, report: Report, dest: Path | None = None) -> Path:
    """Confluence に手で貼るための一式を ZIP にまとめる。"""
    dest = dest or output_dir / "report_bundle.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("report.md", "report.confluence.xml", "report.html", "report.json"):
            path = output_dir / name
            if path.exists():
                archive.write(path, arcname=name)
        for capture in report.captures:
            if capture.included and capture.image_path and capture.image_path.exists():
                archive.write(capture.image_path, arcname=f"images/{capture.filename}")
    return dest
