"""faster-whisper によるローカル文字起こし。音声は外部に送信しない。

パイプライン中で最も重い工程なので、動画のハッシュとモデル名をキーに
結果をキャッシュする。詳細度だけ変えて再生成する場合はここを丸ごと飛ばせる。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from seminar_report.config import get_settings
from seminar_report.media.audio import extract_audio, file_digest, probe_duration
from seminar_report.models import Segment, Transcript

ProgressFn = Callable[[float, str], None]
"""(進捗率, 詳細) を受け取る。"""

StatusFn = Callable[[str], None]
"""文字起こし開始前の準備状況を伝える。モデルの初回 DL は数分かかるため、
無言の時間を作らないよう別チャネルにしている。"""


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


def cuda_device_count() -> int:
    """利用可能な CUDA デバイス数。

    faster-whisper が使うのは torch ではなく CTranslate2 なので、GPU の有無は
    CTranslate2 に直接聞く。torch の有無で判定すると、torch を入れていない
    (かつ本来不要な) 環境で GPU が見えず、常に CPU に落ちてしまう。
    """
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - 判定失敗は「GPU 無し」と同義に扱う
        return 0


def _resolve_device(device: str) -> tuple[str, str]:
    """(device, compute_type) を決める。GPU があれば使う。"""
    if device == "auto":
        device = "cuda" if cuda_device_count() > 0 else "cpu"
    return (device, "float16" if device == "cuda" else "int8")


def _resolve_beam_size(device: str) -> int:
    """探索幅。CPU では実行時間に直結するため既定を下げる。

    GPU は探索を広げても十分速いので、精度側に振ったままにする。
    """
    configured = get_settings().whisper_beam_size
    if configured and configured > 0:
        return configured
    return 5 if device == "cuda" else 1


def _resolve_cpu_threads() -> int:
    """CTranslate2 に渡すスレッド数。既定の 4 では多コア機を使い切れない。"""
    configured = get_settings().whisper_cpu_threads
    if configured and configured > 0:
        return configured
    return os.cpu_count() or 4


def _load_model(model_size: str, device: str, compute_type: str, cpu_threads: int):
    """WhisperModel を組み立てる。CUDA が使えなければ CPU に落として続行する。

    CUDA デバイスが見えていても cuBLAS / cuDNN が無ければここで例外になる。
    その場合に処理ごと失敗させると、GPU 判定を直したことでかえって動かなく
    なるため、必ず CPU へ縮退させる。戻り値は (model, 実際に使った device)。
    """
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(
            model_size, device=device, compute_type=compute_type, cpu_threads=cpu_threads
        )
        return model, device, None
    except Exception as exc:  # noqa: BLE001 - CUDA 環境の不備を握って縮退する
        if device != "cuda":
            raise
        model = WhisperModel(
            model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads
        )
        return model, "cpu", str(exc)


def transcribe(
    video: Path,
    model_size: str | None = None,
    language: str | None = None,
    on_progress: ProgressFn | None = None,
    use_cache: bool = True,
    on_status: StatusFn | None = None,
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
        import faster_whisper  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper が入っていません。"
            "`uv pip install 'seminar-report-helper[asr]'` を実行してください。"
        ) from exc

    duration = probe_duration(video)

    def status(message: str) -> None:
        if on_status:
            on_status(message)

    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_audio(video, Path(tmp) / "audio.wav")

        device, compute_type = _resolve_device(settings.whisper_device)
        cpu_threads = _resolve_cpu_threads()

        # モデルの初回ロードは HuggingFace からの DL を伴い、medium で 1.5GB・
        # 数分かかる。ここで無言になると UI 上は停止に見えるため必ず知らせる。
        status(
            f"{model_size} モデルを読み込んでいます"
            f"（{'GPU' if device == 'cuda' else 'CPU'}／初回はダウンロードに数分かかります）"
        )
        model, device, cuda_error = _load_model(
            model_size, device, compute_type, cpu_threads
        )
        if cuda_error:
            status(f"GPU を使えなかったため CPU で続行します: {cuda_error}")

        beam_size = _resolve_beam_size(device)
        status(
            f"{model_size} / {device} / beam={beam_size}"
            + (f" / {cpu_threads} スレッド" if device == "cpu" else "")
        )

        raw_segments, info = model.transcribe(
            str(wav),
            language=language,
            vad_filter=True,
            beam_size=beam_size,
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
