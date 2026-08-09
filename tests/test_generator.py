from __future__ import annotations

from conftest import MockProvider, ScriptedProvider

from seminar_report.llm.base import extract_json
from seminar_report.models import ChunkSummary, DetailLevel, Transcript
from seminar_report.report.detail import resolve_detail
from seminar_report.report.generator import (
    build_outline,
    generate_report,
    plan_chunks,
    write_sections,
)


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


def test_hallucinated_timestamp_is_never_used(transcript: Transcript) -> None:
    """動画に存在しない時刻を LLM が返しても、その時刻は採用されない。

    見出しごとに最低1枚保証する挙動があるため、セクション自体には
    (作話の時刻ではなく)実在する時刻へのフォールバック画像が入りうる。
    """
    provider = ScriptedProvider(capture_time="02:00:00")
    report = generate_report(transcript, provider, resolve_detail(DetailLevel.BRIEF), "日本語")

    assert all(c.requested_time != 7200.0 for c in report.captures)
    assert all(c.resolved_time <= transcript.duration for c in report.captures)


def test_capture_budget_is_respected_when_it_covers_every_section(transcript: Transcript) -> None:
    """予算が見出し数以上あれば、素直にその範囲に収まること。"""
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=5)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")
    assert len(report.captures) <= 5


def test_every_section_gets_at_least_one_capture(transcript: Transcript) -> None:
    """見出しごとに最低1枚は入る(要望どおりの保証)。"""
    report = generate_report(
        transcript, ScriptedProvider(), resolve_detail(DetailLevel.STANDARD), "日本語"
    )
    for section in report.sections:
        assert "[[capture:" in section.body


def test_no_images_mode_yields_no_captures(transcript: Transcript) -> None:
    spec = resolve_detail(DetailLevel.STANDARD, max_captures=0)
    report = generate_report(transcript, ScriptedProvider(), spec, "日本語")
    assert report.captures == []


def test_fallback_capture_when_llm_places_no_marker_at_all(transcript: Transcript) -> None:
    """本文に一つもマーカーが無い場合でも、機械的にフォールバックが入る。

    プロンプトで「必ず1個以上」と指示しても LLM が守らないことがあるため、
    write_sections 側にも保険を持たせている。
    """
    provider = MockProvider(default="本文のみでマーカーは一切ありません。")
    outline = {
        "sections": [
            {"title": "セクションA", "start": "00:00:00", "end": "00:00:40", "focus": "説明"},
        ]
    }
    spec = resolve_detail(DetailLevel.STANDARD)

    sections, captures = write_sections(outline, transcript, provider, spec, "日本語")

    assert len(captures) == 1
    assert captures[0].marker_id == "s0_0"
    assert captures[0].caption == "セクションA"
    assert captures[0].resolved_time <= transcript.duration
    assert "[[capture:s0_0]]" in sections[0].body


def test_build_outline_falls_back_when_model_never_returns_a_json_object(
    transcript: Transcript,
) -> None:
    """章立ての JSON 化に(再試行しても)失敗しても、区間そのままを章にして続行する。

    以前はここで AttributeError ('list' object has no attribute 'get') が
    そのまま送出され、ジョブが原因不明のまま落ちていた。
    """
    provider = MockProvider(responses=["説明その1", "説明その2"])
    summaries = [
        ChunkSummary(start=0.0, end=40.0, summary="前半の要約", topics=[]),
        ChunkSummary(start=40.0, end=70.0, summary="後半の要約", topics=[]),
    ]
    spec = resolve_detail(DetailLevel.STANDARD)

    outline = build_outline(summaries, transcript, provider, spec, "日本語")

    assert outline["title"] == "セミナーレポート"
    assert outline["key_points"] == []
    assert [s["focus"] for s in outline["sections"]] == ["前半の要約", "後半の要約"]


def test_all_pipeline_stages_are_invoked(transcript: Transcript) -> None:
    provider = ScriptedProvider()
    generate_report(transcript, provider, resolve_detail(DetailLevel.STANDARD), "日本語")
    assert {"summary", "outline", "section", "overview"} <= set(provider.calls)


def test_user_request_is_forwarded_to_outline_and_section_prompts(
    transcript: Transcript,
) -> None:
    """依頼者からの要望が、章立て・本文執筆の両プロンプトに渡っていること。"""
    provider = MockProvider(
        responses=[
            # summarize_chunks (map段、1区間)
            '{"summary": "要約", "topics": []}',
            # build_outline
            (
                '{"title": "T", "key_points": ["p"], "sections": '
                '[{"title": "S", "start": "00:00:00", "end": "00:00:40", "focus": "f"}]}'
            ),
            # write_sections (1セクション)
            "本文です。",
            # overview
            "概要です。",
        ]
    )
    report = generate_report(
        transcript,
        provider,
        resolve_detail(DetailLevel.STANDARD),
        "日本語",
        user_request="特に価格の話を重視して",
    )

    assert report.title == "T"
    prompts_with_request = [p for p in provider.prompts if "特に価格の話を重視して" in p]
    # outline_prompt と section_write_prompt の両方に渡っている
    assert len(prompts_with_request) == 2
