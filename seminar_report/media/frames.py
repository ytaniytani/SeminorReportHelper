"""フレーム抽出と、キャプチャに適した 1 枚の選抜。

「AI が重要と判断した時刻」の周辺から複数枚を取り出し、ぼけ・黒画面・
スライド切替途中のフレームを弾いて最良の 1 枚を選ぶ。既に採用した画像と
見た目が近いものは知覚ハッシュで除外する。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from seminar_report.media.audio import ffmpeg_path

# 基準時刻の前後どこを探すか。話者が言及した直後にスライドが確定することが
# 多いため、後ろ側を厚めに取る。
WINDOW_BEFORE = 2.0
WINDOW_AFTER = 6.0
CANDIDATES_PER_CAPTURE = 5

# dHash のハミング距離がこれ以下なら「ほぼ同じ絵」とみなす(64bit中)
DUPLICATE_DISTANCE = 8


@dataclass
class FrameMetrics:
    """1 枚のフレームの画質指標。"""

    path: Path
    time: float
    sharpness: float
    """Laplacian の分散。ぼけているほど小さい。"""
    std_luma: float
    """輝度の標準偏差。単色に近いほど小さく、内容が濃いほど大きい。"""
    mean_luma: float
    dhash: int

    @property
    def is_blank(self) -> bool:
        """黒画面・白飛び・単色フェードの判定。

        情報量の指標にヒストグラムのエントロピーは使わない。白背景に黒文字の
        スライドは輝度が二峰性でエントロピーが低く出るため、実際のスライドを
        空白と誤判定してしまう。ここでは「ほぼ一様かどうか」だけを見る。
        """
        return self.std_luma < 8.0 or self.mean_luma < 12 or self.mean_luma > 245


def _grayscale(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def laplacian_variance(gray: np.ndarray) -> float:
    """Laplacian フィルタの分散。ピント・エッジの鋭さの指標。

    scipy を持ち込まずに済むよう、スライスで畳み込みを直接書いている。
    """
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (
        4.0 * gray[1:-1, 1:-1]
        - gray[:-2, 1:-1]
        - gray[2:, 1:-1]
        - gray[1:-1, :-2]
        - gray[1:-1, 2:]
    )
    return float(lap.var())


def luma_std(gray: np.ndarray) -> float:
    """輝度の標準偏差。内容の濃さの指標。

    ぼかしても大きく下がらない一方、単色フレームでは 0 に近づくため、
    「絵として成立しているか」の判定に向く。
    """
    return float(gray.std())


def dhash(image: Image.Image, size: int = 8) -> int:
    """差分ハッシュ。隣接ピクセルの大小関係を 64bit に畳む。"""
    small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(small, dtype=np.int16)
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _extract_one(video: Path, time: float, dest: Path, width: int = 1280) -> bool:
    """指定時刻のフレームを 1 枚書き出す。成功したら True。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            # -i より前に置く入力シーク。長い動画でも高速で、かつ現行の
            # ffmpeg は直前のキーフレームからデコードするため精度も出る。
            "-ss",
            f"{max(time, 0):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2:flags=lanczos",
            "-q:v",
            "2",
            str(dest),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def extract_candidates(
    video: Path,
    time: float,
    out_dir: Path,
    prefix: str,
    duration: float | None = None,
    count: int = CANDIDATES_PER_CAPTURE,
) -> list[FrameMetrics]:
    """基準時刻の周辺から候補フレームを取り出し、画質指標を付けて返す。"""
    start = max(time - WINDOW_BEFORE, 0.0)
    end = time + WINDOW_AFTER
    if duration:
        end = min(end, max(duration - 0.5, 0.0))
    if end <= start:
        end = start

    step = (end - start) / max(count - 1, 1)
    metrics: list[FrameMetrics] = []
    for index in range(count):
        at = start + step * index
        dest = out_dir / f"{prefix}_c{index}.jpg"
        if not _extract_one(video, at, dest):
            continue
        try:
            with Image.open(dest) as image:
                image.load()
                gray = _grayscale(image)
                metrics.append(
                    FrameMetrics(
                        path=dest,
                        time=at,
                        sharpness=laplacian_variance(gray),
                        std_luma=luma_std(gray),
                        mean_luma=float(gray.mean()),
                        dhash=dhash(image),
                    )
                )
        except OSError:
            continue
    return metrics


def _rank_normalized(values: list[float]) -> list[float]:
    """順位に基づく 0〜1 の正規化。外れ値に強い。"""
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    order = sorted(range(len(values)), key=lambda i: values[i])
    scores = [0.0] * len(values)
    for rank, index in enumerate(order):
        scores[index] = rank / (len(values) - 1)
    return scores


def select_best(
    candidates: list[FrameMetrics],
    used_hashes: list[int] | None = None,
) -> FrameMetrics | None:
    """候補の中から採用する 1 枚を選ぶ。

    黒画面・単色フレームを除外したうえで、シャープネス(ピントの鋭さ)を主、
    輝度の標準偏差(内容の濃さ)を従として順位付けする。既出画像に近すぎる
    ものは飛ばす。
    """
    if not candidates:
        return None
    used_hashes = used_hashes or []

    usable = [c for c in candidates if not c.is_blank] or candidates
    sharp_scores = _rank_normalized([c.sharpness for c in usable])
    content_scores = _rank_normalized([c.std_luma for c in usable])
    ranked = sorted(
        zip(usable, sharp_scores, content_scores),
        key=lambda item: 0.7 * item[1] + 0.3 * item[2],
        reverse=True,
    )

    for candidate, _, _ in ranked:
        if all(
            hamming_distance(candidate.dhash, used) > DUPLICATE_DISTANCE
            for used in used_hashes
        ):
            return candidate
    return None
