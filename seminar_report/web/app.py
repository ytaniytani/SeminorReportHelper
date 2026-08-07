"""ローカル Web UI の FastAPI アプリ。"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from seminar_report.capture import swap_capture
from seminar_report.config import get_settings
from seminar_report.llm import MODEL_CHOICES, PROVIDERS
from seminar_report.models import DetailLevel, JobStatus, Report
from seminar_report.pipeline import PipelineOptions, export_zip, write_outputs
from seminar_report.report.detail import PRESETS
from seminar_report.web.jobs import Job, manager

STATIC_DIR = Path(__file__).parent / "static"

POLL_INTERVAL = 0.5
"""ジョブのイベント配列を見に行く間隔(秒)。"""

HEARTBEAT_INTERVAL = 15.0
"""イベントが無い間も接続維持のコメントを送る間隔(秒)。"""

app = FastAPI(title="Seminar Report Helper")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
async def config() -> dict:
    settings = get_settings()
    return {
        "providers": list(PROVIDERS),
        "current_provider": settings.llm_provider,
        "model_choices": MODEL_CHOICES,
        "current_models": {
            "claude": settings.claude_model,
            "openai": settings.openai_model,
            "ollama": settings.ollama_model,
        },
        "presets": [
            {
                "value": level.value,
                "label": spec.label,
                "target_chars": spec.target_chars,
                "max_captures": spec.max_captures,
            }
            for level, spec in PRESETS.items()
        ],
        "confluence_configured": settings.confluence_configured(),
        "confluence_space": settings.confluence_space_key,
        "whisper_model": settings.whisper_model,
    }


@app.post("/api/jobs")
async def create_job(
    video: UploadFile = File(...),
    detail: str = Form(DetailLevel.STANDARD.value),
    target_chars: str = Form(""),
    provider: str = Form(""),
    model: str = Form(""),
    whisper_model: str = Form(""),
    audio_language: str = Form(""),
    verify_captures: str = Form("false"),
    include_images: str = Form("true"),
    crop: str = Form(""),
) -> dict:
    settings = get_settings()
    if not video.filename:
        raise HTTPException(400, "ファイル名が不正です")

    job_root = settings.jobs_dir / Path(video.filename).stem
    job_root.mkdir(parents=True, exist_ok=True)
    video_path = job_root / Path(video.filename).name

    def save() -> None:
        with video_path.open("wb") as fh:
            # 既定の 64KB バッファでは GB 級の動画で syscall が嵩む。
            shutil.copyfileobj(video.file, fh, 1024 * 1024)

    # 30 分の動画は 1〜2GB になる。同期 I/O のままだとその間イベントループが
    # 止まり、進行中ジョブの SSE まで巻き添えで固まる。
    await asyncio.to_thread(save)

    chars = int(target_chars) if target_chars.strip().isdigit() else None

    capture_crop = None
    if crop.strip():
        from seminar_report.media.frames import parse_crop_box

        try:
            capture_crop = parse_crop_box(crop)
        except ValueError as exc:
            raise HTTPException(400, f"crop が不正です: {exc}") from exc

    options = PipelineOptions(
        detail=DetailLevel(detail),
        target_chars=chars,
        provider=provider or None,
        model=model or None,
        whisper_model=whisper_model or None,
        whisper_language=audio_language or None,
        verify_captures=verify_captures == "true",
        include_images=include_images == "true",
        capture_crop=capture_crop,
    )

    job = manager.create(video_path, job_root / "output", options)
    return {"job_id": job.id}


def _require_job(job_id: str) -> Job:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "ジョブが見つかりません")
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """進捗を SSE で流す。1時間動画では処理に数十分かかるため必須。"""
    job = _require_job(job_id)

    async def stream():
        sent = 0
        idle = 0.0
        while True:
            events = job.events_since(sent)
            for event in events:
                sent += 1
                yield f"data: {event.model_dump_json()}\n\n"
            if events:
                idle = 0.0

            if job.finished:
                payload = {
                    "status": job.status.value,
                    "error": job.error,
                    "traceback": job.traceback,
                }
                yield f"event: end\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            # モデルの初回 DL 中などはイベントが数分止まる。無通信のままだと
            # プロキシやセキュリティソフトに切断されるため、定期的に空コメント
            # (SSE のコメント行) を送って接続を維持する。
            await asyncio.sleep(POLL_INTERVAL)
            idle += POLL_INTERVAL
            if idle >= HEARTBEAT_INTERVAL:
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _report_payload(job: Job) -> dict:
    report = job.result.report if job.result else None
    return {
        "status": job.status.value,
        "error": job.error,
        "traceback": job.traceback,
        "report": _serialize_report(report, job.id) if report else None,
    }


def _serialize_report(report: Report, job_id: str) -> dict:
    return {
        "title": report.title,
        "overview": report.overview,
        "key_points": report.key_points,
        "duration": report.duration,
        "source_video": report.source_video,
        "char_count": report.body_char_count(),
        "sections": [
            {"title": s.title, "body": s.body, "start": s.start, "end": s.end}
            for s in report.sections
        ],
        "captures": [
            {
                "marker_id": c.marker_id,
                "caption": c.caption,
                "timestamp": c.timestamp,
                "included": c.included,
                "candidate_count": len(c.candidates),
                "url": (
                    f"/api/jobs/{job_id}/images/{c.filename}" if c.filename else None
                ),
            }
            for c in report.captures
        ],
    }


@app.get("/api/jobs")
async def list_jobs() -> dict:
    """新しい順のジョブ一覧。

    SSE が切れたりページを再読み込みしたりしても、走っている / 終わった
    ジョブに戻れるようにするための入口。
    """
    return {
        "jobs": [
            {
                "job_id": job.id,
                "status": job.status.value,
                "video": job.video_path.name,
                "title": job.result.report.title if job.result else None,
                "error": job.error,
            }
            for job in manager.recent()
        ]
    }


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str) -> dict:
    return _report_payload(_require_job(job_id))


class ReportPatch(BaseModel):
    title: str | None = None
    overview: str | None = None
    key_points: list[str] | None = None
    sections: list[dict] | None = None
    excluded_captures: list[str] | None = None


@app.patch("/api/jobs/{job_id}")
async def patch_report(job_id: str, patch: ReportPatch) -> dict:
    """プレビュー画面での編集内容を反映し、出力ファイルを書き直す。"""
    job = _require_job(job_id)
    if job.result is None:
        raise HTTPException(409, "まだ完了していません")

    report = job.result.report
    if patch.title is not None:
        report.title = patch.title
    if patch.overview is not None:
        report.overview = patch.overview
    if patch.key_points is not None:
        report.key_points = patch.key_points
    if patch.sections is not None:
        for section, update in zip(report.sections, patch.sections):
            section.title = update.get("title", section.title)
            section.body = update.get("body", section.body)
    if patch.excluded_captures is not None:
        excluded = set(patch.excluded_captures)
        for capture in report.captures:
            capture.included = capture.marker_id not in excluded and bool(capture.filename)

    job.result = write_outputs(report, job.result.transcript, job.output_dir)
    return _report_payload(job)


class SwapRequest(BaseModel):
    marker_id: str
    candidate_index: int


@app.post("/api/jobs/{job_id}/swap")
async def swap_image(job_id: str, request: SwapRequest) -> dict:
    job = _require_job(job_id)
    if job.result is None:
        raise HTTPException(409, "まだ完了していません")
    swapped = swap_capture(
        job.result.report,
        request.marker_id,
        request.candidate_index,
        job.output_dir / "images",
    )
    if not swapped:
        raise HTTPException(400, "差し替えできませんでした")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/images/{filename}")
async def job_image(job_id: str, filename: str) -> FileResponse:
    job = _require_job(job_id)
    # パストラバーサル防止のため basename だけを使う
    path = job.output_dir / "images" / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "画像が見つかりません")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/export")
async def export_job(job_id: str) -> FileResponse:
    job = _require_job(job_id)
    if job.result is None:
        raise HTTPException(409, "まだ完了していません")
    zip_path = export_zip(job.output_dir, job.result.report)
    return FileResponse(zip_path, filename=f"{job.result.report.title}.zip")


@app.get("/api/jobs/{job_id}/html")
async def job_html(job_id: str) -> FileResponse:
    """完了直後にブラウザで直接開くための report.html。

    ZIP をダウンロード→展開→開く、という手間を省くための経路。
    """
    job = _require_job(job_id)
    if job.result is None:
        raise HTTPException(409, "まだ完了していません")
    path = job.output_dir / "report.html"
    if not path.exists():
        raise HTTPException(404, "report.html が見つかりません")
    return FileResponse(path, media_type="text/html")


class PublishRequest(BaseModel):
    space_key: str | None = None
    title: str | None = None
    parent_id: str | None = None


@app.post("/api/jobs/{job_id}/publish")
async def publish_job(job_id: str, request: PublishRequest) -> dict:
    from seminar_report.publish.confluence import ConfluenceError, publish_report

    job = _require_job(job_id)
    if job.result is None or job.status is not JobStatus.DONE:
        raise HTTPException(409, "まだ完了していません")

    try:
        result = await asyncio.to_thread(
            publish_report,
            job.result.report,
            request.space_key,
            request.title,
            request.parent_id,
        )
    except ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {"url": result.url, "page_id": result.page_id, "attached": result.attached}
