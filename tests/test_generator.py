from __future__ import annotations

from conftest import ScriptedProvider

from seminar_report.llm.base import extract_json
from seminar_report.models import DetailLevel, Transcript
from seminar_report.report.detail import resolve_detail
from seminar_report.report.generator import generate_report, plan_chunks


def test_extract_json_handles_code_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_handles_surrounding_prose() -> None:
    assert extract_json('以下が結果です。\n{"a": 1}\nご確認ください。') == {"a": 1}


def test_plan_chunks_covers_whole_duration(transcript: Transcript) -> None:
    chunks = plan_chunks(transcript, ScriptedProvider())
    assert chunks[0][0] == 0.0
    assert chunks[-1][1] == transcript.duration


def test_plan_chunks_shrinks_for_small_context(transcript: Transcript) -> None:
    provider = ScriptedProvider()
    provider.context_chars = 200
    long_transcript = transcript.model_copy(update={"duration": 7200.0})
    assert len(plan_chunks(long_transcript, provider)) > 1


def test_generate_report_builds_sections_and_captures(transcript: Transcript) -> None:
    provider = ScriptedProvider()
    spec = resolve_detail(DetailLevel.STANDARD)
    report = generate_report(transcript, provider, spec, "日本語")

    assert report.title == "社内AI活用セミナー"
    assert report.key_points == ["導入効果は明確", "運用体制が課題"]
    assert [s.title for s in report.sections] == ["アーキテクチャ", "導入効果"]
    assert report.overview

    # 各セクションのマーカーが解決され、ID が振られている
    assert len(report.captures) == 2
    assert {c.marker_id for c in report.captures} == {"s0_0", "s1_0"}
    for capture in report.captures:
        assert capture.resolved_time == 27.0
        assert capture.caption == "処理件数の推移"
    for section in report.sections:
        assert "[[capture:s" in section.body


def test_hallucinated_timestamp_produces_no_capture(transcript: Transcript) -> None:
    """動画に存在しない時刻を LLM が返しても、画像は挿入されない。"""
    provider = ScriptedProvider(capture_time="02:00:00")
    report = generate_report(transcript, provider, resolve_detail(DetailLevel.BRIEF), "日本語")

    assert report.captures == []
    assert all("capture" not in s.body for s in report.sections)


def test_capture_budget_is_respected(transcript: Transcript) -> None:
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=1)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")
    assert len(report.captures) <= 1


def test_no_images_mode_yields_no_captures(transcript: Transcript) -> None:
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=0)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")
    assert report.captures == []


def test_all_pipeline_stages_are_invoked(transcript: Transcript) -> None:
    provider = ScriptedProvider()
    generate_report(transcript, provider, resolve_detail(DetailLevel.STANDARD), "日本語")
    assert {"summary", "outline", "section", "overview"} <= set(provider.calls)
