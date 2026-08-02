"""マーカー解決の検証。本文と画像の対応が崩れないことがこの機能の要。"""

from __future__ import annotations

from seminar_report.models import Transcript
from seminar_report.report.markers import (
    CAPTURE_OFFSET,
    resolve_markers,
    snap_to_segment,
    strip_markers,
)


def test_snap_uses_nearest_segment_with_offset(transcript: Transcript) -> None:
    # 26 秒は 25.0 開始のセグメントに吸着し、+2 秒される
    assert snap_to_segment(26.0, transcript) == 25.0 + CAPTURE_OFFSET


def test_snap_rejects_hallucinated_time(transcript: Transcript) -> None:
    # 動画は 70 秒しかない。10 分の指定は作話なので破棄する
    assert snap_to_segment(600.0, transcript) is None


def test_snap_clamps_to_duration(transcript: Transcript) -> None:
    resolved = snap_to_segment(70.0, transcript)
    assert resolved is not None
    assert resolved <= transcript.duration


def test_resolve_extracts_caption_and_rewrites_marker(transcript: Transcript) -> None:
    body = "冒頭の説明です。\n\n[[capture:00:00:25|処理件数の推移]]\n\n続きの説明です。"
    new_body, captures = resolve_markers(body, transcript, "s0")

    assert len(captures) == 1
    assert captures[0].caption == "処理件数の推移"
    assert captures[0].marker_id == "s0_0"
    assert captures[0].resolved_time == 27.0
    assert "[[capture:s0_0]]" in new_body
    assert "00:00:25" not in new_body


def test_resolve_drops_out_of_range_markers(transcript: Transcript) -> None:
    body = "説明。\n[[capture:01:30:00|存在しない時刻]]\n続き。"
    new_body, captures = resolve_markers(body, transcript, "s0")

    assert captures == []
    assert "capture" not in new_body
    assert "説明。" in new_body and "続き。" in new_body


def test_resolve_respects_max_captures(transcript: Transcript) -> None:
    body = "\n".join(
        [
            "[[capture:00:00:00|1枚目]]",
            "本文",
            "[[capture:00:00:10|2枚目]]",
            "本文",
            "[[capture:00:00:25|3枚目]]",
        ]
    )
    _, captures = resolve_markers(body, transcript, "s0", max_captures=2)
    assert len(captures) == 2


def test_marker_without_caption_gets_default(transcript: Transcript) -> None:
    _, captures = resolve_markers("[[capture:00:00:10]]", transcript, "s1")
    assert captures[0].caption == "セミナー資料"


def test_mmss_format_is_accepted(transcript: Transcript) -> None:
    _, captures = resolve_markers("[[capture:00:25|図]]", transcript, "s0")
    assert len(captures) == 1
    assert captures[0].requested_time == 25.0


def test_inline_marker_is_not_matched(transcript: Transcript) -> None:
    """行の途中に現れた文字列は誤検出しない。"""
    body = "説明の中で [[capture:00:00:10|図]] と書かれた場合。"
    _, captures = resolve_markers(body, transcript, "s0")
    assert captures == []


def test_strip_markers_removes_both_forms(transcript: Transcript) -> None:
    body = "A\n[[capture:00:00:10|図]]\nB"
    resolved, _ = resolve_markers(body, transcript, "s0")
    assert "capture" not in strip_markers(resolved)
    assert "capture" not in strip_markers(body)
