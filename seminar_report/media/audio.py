"""ffmpeg の解決と、動画からの音声抽出。"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """使用する ffmpeg のパス。

    システムに入っていればそれを使い、無ければ imageio-ffmpeg 同梱の
    静的バイナリにフォールバックする。ユーザー側で追加インストールを
    不要にするための二段構え。
    """
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - 依存が入っていれば通らない
        raise FFmpegError(
            "ffmpeg が見つかりません。システムに ffmpeg を入れるか "
            "`pip install imageio-ffmpeg` を実行してください。"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise FFmpegError("ffmpeg の実行に失敗しました:\n" + "\n".join(tail))
    return proc


def probe_duration(video: Path) -> float:
    """動画の長さ(秒)。ffprobe が無い環境でも ffmpeg の出力から拾う。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = _run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video),
            ]
        )
        try:
            return float(json.loads(proc.stdout)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass

    # ffprobe が無い場合は ffmpeg の stderr から "Duration: HH:MM:SS.ss" を読む
    proc = subprocess.run(
        [ffmpeg_path(), "-i", str(video)], capture_output=True, text=True
    )
    match = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", proc.stderr or "")
    if not match:
        raise FFmpegError(f"動画の長さを取得できませんでした: {video}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def file_digest(path: Path, chunk_size: int = 1 << 20) -> str:
    """ファイルの SHA256。文字起こしキャッシュのキーに使う。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def extract_audio(video: Path, dest: Path) -> Path:
    """Whisper が扱いやすい 16kHz モノラル WAV を書き出す。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )
    return dest
