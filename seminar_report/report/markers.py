"""`[[capture:HH:MM:SS|キャプション]]` マーカーの解析と検証。

LLM が出した時刻はそのままでは信用できない。ここで文字起こしのセグメント
境界に吸着させ、実在しない時刻を指すマーカーは破棄する。この検証が
「本文の記述と挿入画像が対応している」ことを担保する要になっている。
"""

from __future__ import annotations

import re

from seminar_report.models import Capture, Transcript, parse_timestamp

MARKER_RE = re.compile(
    r"^[ \t]*\[\[\s*capture\s*:\s*([0-9:]+)\s*(?:\|\s*(.*?))?\s*\]\][ \t]*$",
    re.MULTILINE,
)

RESOLVED_RE = re.compile(r"^[ \t]*\[\[capture:(?P<id>[A-Za-z0-9_-]+)\]\][ \t]*$", re.MULTILINE)

# 指定時刻からこの秒数以内にセグメントが無ければ、作話とみなして破棄する。
SNAP_TOLERANCE = 30.0

# セグメント開始ちょうどではなく少し後を撮る。言及した瞬間はスライドが
# 切り替わり途中のことが多いため。
CAPTURE_OFFSET = 2.0


def snap_to_segment(time: float, transcript: Transcript) -> float | None:
    """指定時刻を最も近いセグメント開始に吸着させ、+2秒した時刻を返す。

    許容範囲内にセグメントが無ければ None(=そのマーカーは破棄)。
    """
    if not transcript.segments:
        return None

    nearest = min(transcript.segments, key=lambda s: abs(s.start - time))
    if abs(nearest.start - time) > SNAP_TOLERANCE:
        return None

    resolved = nearest.start + CAPTURE_OFFSET
    if transcript.duration:
        resolved = min(resolved, max(transcript.duration - 0.5, 0.0))
    return max(resolved, 0.0)


def resolve_markers(
    body: str,
    transcript: Transcript,
    id_prefix: str,
    max_captures: int | None = None,
) -> tuple[str, list[Capture]]:
    """本文中のマーカーを検証し、ID 付きの解決済みマーカーに置き換える。

    戻り値は (置換後の本文, Capture のリスト)。破棄されたマーカーの行は
    本文から取り除かれる。
    """
    captures: list[Capture] = []
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        raw_time, caption = match.group(1), (match.group(2) or "").strip()

        requested = parse_timestamp(raw_time)
        if requested is None:
            return ""

        resolved = snap_to_segment(requested, transcript)
        if resolved is None:
            return ""

        if max_captures is not None and counter >= max_captures:
            return ""

        marker_id = f"{id_prefix}_{counter}"
        counter += 1
        captures.append(
            Capture(
                marker_id=marker_id,
                requested_time=requested,
                resolved_time=resolved,
                caption=caption or "セミナー資料",
            )
        )
        return f"[[capture:{marker_id}]]"

    new_body = MARKER_RE.sub(replace, body)
    return _tidy_blank_lines(new_body), captures


def strip_markers(body: str) -> str:
    """マーカーを全て取り除く(画像なしで本文だけ欲しい場合)。"""
    return _tidy_blank_lines(RESOLVED_RE.sub("", MARKER_RE.sub("", body)))


def _tidy_blank_lines(text: str) -> str:
    """マーカー削除で生じた連続空行を整理する。"""
    return re.sub(r"\n{3,}", "\n\n", text).strip()
