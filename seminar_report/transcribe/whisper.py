"""faster-whisper によるローカル文字起こし。音声は外部に送信しない。

パイプライン中で最も重い工程なので、動画のハッシュとモデル名をキーに
結果をキャッシュする。詳細度だけ変えて再生成する場合はここを丸ごと飛ばせる。
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from seminar_report.config import get_settings
from seminar_report.media.audio import extract_audio, file_digest, probe_duration
from seminar_report.models import Segment, Transcript

ProgressFn = Callable[[float, str], None]


def cache_path(video: Path, model: str) -> Path:
    settings = get_settings()
    return settings.cache_dir / file_digest(video) / model / "transcript.json"


def load_cached(video: Path, model: str) -> Transcript | None:
    path = cache_path(video, model)
    if not path.exists():
        return None
    try:
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _resolve_device(device: str) -> tuple[str, str]:
    """(device, compute_type) を決める。GPU があれば使う。"""
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    return (device, "float16" if device == "cuda" else "int8")


def transcribe(
    video: Path,
    model_size: str | None = None,
    language: str | None = None,
    on_progress: ProgressFn | None = None,
    use_cache: bool = True,
) -> Transcript:
    """動画を文字起こしする。"""
    settings = get_settings()
    model_size = model_size or settings.whisper_model
    language = language or settings.whisper_language or None

    if use_cache:
        cached = load_cached(video, model_size)
        if cached is not None:
            if on_progress:
                on_progress(1.0, "キャッシュを利用しました")
            return cached

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper が入っていません。"
            "`uv pip install 'seminar-report-helper[asr]'` を実行してください。"
        ) from exc

    duration = probe_duration(video)

    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_audio(video, Path(tmp) / "audio.wav")

        device, compute_type = _resolve_device(settings.whisper_device)
        model = WhisperModel(model_size, device=device, compute_type=compute_type)

        raw_segments, info = model.transcribe(
            str(wav),
            language=language,
            vad_filter=True,
            beam_size=5,
        )

        segments: list[Segment] = []
        # faster-whisper はジェネレータを返す。消費しながら進捗を出す。
        for seg in raw_segments:
            text = (seg.text or "").strip()
            if text:
                segments.append(Segment(start=seg.start, end=seg.end, text=text))
            if on_progress and duration > 0:
                on_progress(
                    min(seg.end / duration, 1.0),
                    f"{len(segments)} セグメント",
                )

    transcript = Transcript(
        segments=segments,
        language=getattr(info, "language", None) or language,
        duration=duration,
        model=model_size,
    )

    path = cache_path(video, model_size)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    if on_progress:
        on_progress(1.0, f"{len(segments)} セグメント")
    return transcript
