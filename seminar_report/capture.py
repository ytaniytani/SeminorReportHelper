"""レポート本文のマーカーを実際の画像に解決する工程。

「AI が重要と判断した時刻」を起点にフレームを取り出すため、シーン検出で
機械的に集めるやり方と違って、画像枚数と本文の重要度が自然に一致する。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from seminar_report.llm.base import LLMError, LLMProvider
from seminar_report.llm.prompts import CAPTION_VERIFY_PROMPT
from seminar_report.media.frames import extract_candidates, select_best
from seminar_report.models import JobStep, Report

ProgressFn = Callable[[JobStep, float, str], None]


def _verify(
    provider: LLMProvider, image: Path, caption: str
) -> tuple[bool, str | None]:
    """Vision で画像の有用性を確認し、必要ならキャプションを直す。

    判定に失敗した場合は「有用」とみなす(消極的に落とさない)。
    """
    try:
        from seminar_report.llm.base import extract_json

        raw = provider.describe_image(image, CAPTION_VERIFY_PROMPT.format(caption=caption))
        if not raw:
            return True, None
        data = extract_json(raw)
    except (LLMError, OSError):
        return True, None

    useful = bool(data.get("useful", True))
    new_caption = str(data.get("caption") or "").strip() or None
    return useful, new_caption


def build_captures(
    report: Report,
    video: Path,
    out_dir: Path,
    provider: LLMProvider | None = None,
    verify: bool = False,
    on_progress: ProgressFn | None = None,
) -> Report:
    """各マーカーについてフレームを抽出し、最良の 1 枚を採用する。"""
    if not report.captures:
        return report

    images_dir = out_dir / "images"
    work_dir = out_dir / "_candidates"
    images_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    use_vision = verify and provider is not None and provider.supports_vision
    used_hashes: list[int] = []
    total = len(report.captures)

    for index, capture in enumerate(report.captures):
        candidates = extract_candidates(
            video,
            capture.resolved_time,
            work_dir,
            prefix=capture.marker_id,
            duration=report.duration,
        )
        # 自動選抜が失敗しても、UI から手で選び直せるよう候補は常に残す
        capture.candidates = [c.path for c in candidates]

        best = select_best(candidates, used_hashes)
        if best is None:
            capture.included = False

        if best is not None and use_vision:
            useful, new_caption = _verify(provider, best.path, capture.caption)
            if not useful:
                capture.included = False
            elif new_caption:
                capture.caption = new_caption

        if best is not None and capture.included:
            filename = f"{capture.marker_id}.jpg"
            destination = images_dir / filename
            shutil.copyfile(best.path, destination)
            capture.image_path = destination
            capture.filename = filename
            capture.resolved_time = best.time
            used_hashes.append(best.dhash)

        if on_progress:
            on_progress(JobStep.CAPTURE, (index + 1) / total, f"{index + 1}/{total} 枚")

    return report


def swap_capture(report: Report, marker_id: str, candidate_index: int, images_dir: Path) -> bool:
    """Web UI から候補フレームへ差し替える。

    自動選抜が失敗して画像が無いキャプチャでも、候補を選べば採用できる。
    """
    for capture in report.captures:
        if capture.marker_id != marker_id:
            continue
        if not 0 <= candidate_index < len(capture.candidates):
            return False

        source = capture.candidates[candidate_index]
        if not source.exists():
            return False

        if capture.image_path is None:
            images_dir.mkdir(parents=True, exist_ok=True)
            capture.filename = f"{capture.marker_id}.jpg"
            capture.image_path = images_dir / capture.filename
            capture.included = True

        shutil.copyfile(source, capture.image_path)
        return True
    return False
