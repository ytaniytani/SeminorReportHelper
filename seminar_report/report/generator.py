"""文字起こしからレポートを生成する map → outline → write の 3 段構成。

1 時間程度なら全文が 1 コンテキストに収まるが、プロバイダ(特に Ollama)や
長尺動画では収まらない。最初から分割前提にしておくことで、プロバイダを
差し替えても長さが変わっても同じ経路で動くようにしている。
"""

from __future__ import annotations

from collections.abc import Callable

from seminar_report.llm import prompts
from seminar_report.llm.base import LLMError, LLMProvider
from seminar_report.models import (
    Capture,
    ChunkSummary,
    JobStep,
    Report,
    Section,
    Transcript,
    format_timestamp,
    parse_timestamp,
)
from seminar_report.report.detail import DetailSpec
from seminar_report.report.markers import resolve_markers

ProgressFn = Callable[[JobStep, float, str], None]

DEFAULT_CHUNK_SECONDS = 15 * 60
# 入力に使ってよいコンテキストの割合。残りは出力とプロンプト本体のために空ける。
INPUT_BUDGET_RATIO = 0.6
# 目標文字数からこの比率を超えて外れたら 1 度だけリライトする。
LENGTH_TOLERANCE = 0.3


def _chars_per_second(transcript: Transcript) -> float:
    if transcript.duration <= 0:
        return 3.0
    return max(transcript.char_count() / transcript.duration, 0.5)


def plan_chunks(transcript: Transcript, provider: LLMProvider) -> list[tuple[float, float]]:
    """文字起こしを (開始秒, 終了秒) の区間に割る。"""
    duration = transcript.duration or (
        transcript.segments[-1].end if transcript.segments else 0.0
    )
    if duration <= 0:
        return [(0.0, 0.0)]

    budget_chars = provider.context_chars * INPUT_BUDGET_RATIO
    seconds_by_context = budget_chars / _chars_per_second(transcript)
    chunk_seconds = max(120.0, min(DEFAULT_CHUNK_SECONDS, seconds_by_context))

    chunks: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk_seconds, duration)
        chunks.append((start, end))
        start = end
    return chunks or [(0.0, duration)]


def _truncate(text: str, limit: int) -> str:
    """コンテキストに収まらない場合に中間を省略する。前後は情報が濃い。"""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n\n…(中略)…\n\n{text[-tail:]}"


def summarize_chunks(
    transcript: Transcript,
    provider: LLMProvider,
    on_progress: ProgressFn | None = None,
) -> list[ChunkSummary]:
    """map 段。各区間の要点を抽出する。"""
    chunks = plan_chunks(transcript, provider)
    limit = int(provider.context_chars * INPUT_BUDGET_RATIO)
    summaries: list[ChunkSummary] = []

    for index, (start, end) in enumerate(chunks):
        text = transcript.to_timestamped_text(start, end)
        if not text.strip():
            continue
        prompt = prompts.chunk_summary_prompt(_truncate(text, limit), index, len(chunks))
        try:
            data = provider.complete_json(prompt, system=prompts.SYSTEM, max_tokens=1500)
        except LLMError:
            # 1 区間の失敗で全体を落とさない。素のテキスト要約にフォールバック。
            data = {"summary": provider.complete(prompt, system=prompts.SYSTEM), "topics": []}

        summaries.append(
            ChunkSummary(
                start=start,
                end=end,
                summary=str(data.get("summary", "")).strip(),
                topics=[str(t) for t in data.get("topics", []) if str(t).strip()],
            )
        )
        if on_progress:
            on_progress(
                JobStep.SUMMARIZE,
                (index + 1) / len(chunks),
                f"{index + 1}/{len(chunks)} 区間",
            )

    return summaries


def _summaries_text(summaries: list[ChunkSummary]) -> str:
    blocks = []
    for summary in summaries:
        topics = "\n".join(f"  - {t}" for t in summary.topics)
        blocks.append(
            f"### {format_timestamp(summary.start)} 〜 {format_timestamp(summary.end)}\n"
            f"{summary.summary}\n"
            f"{topics}"
        )
    return "\n\n".join(blocks)


def build_outline(
    summaries: list[ChunkSummary],
    transcript: Transcript,
    provider: LLMProvider,
    spec: DetailSpec,
    language: str,
) -> dict:
    """outline 段。章立ては固定テンプレートではなく AI に設計させる。"""
    duration_label = format_timestamp(transcript.duration)
    prompt = prompts.outline_prompt(
        summaries_text=_summaries_text(summaries),
        duration_label=duration_label,
        target_chars=spec.target_chars,
        section_range=spec.section_range,
        language=language,
    )
    data = provider.complete_json(prompt, system=prompts.SYSTEM, max_tokens=3000)

    sections = data.get("sections") or []
    if not sections:
        # 章立てが取れなかった場合は区間そのままを章にする。
        sections = [
            {
                "title": f"{format_timestamp(s.start)} 以降の内容",
                "start": format_timestamp(s.start),
                "end": format_timestamp(s.end),
                "focus": s.summary[:200],
            }
            for s in summaries
        ]
    return {
        "title": str(data.get("title") or "セミナーレポート").strip(),
        "key_points": [str(k) for k in data.get("key_points", []) if str(k).strip()],
        "sections": sections,
    }


def _section_bounds(raw: dict, fallback_end: float) -> tuple[float, float]:
    start = parse_timestamp(str(raw.get("start", ""))) or 0.0
    end = parse_timestamp(str(raw.get("end", "")))
    if end is None or end <= start:
        end = fallback_end
    return start, end


def _adjust_length(
    body: str, target: int, provider: LLMProvider, language: str
) -> str:
    """目標文字数から大きく外れたときだけ 1 回リライトする。"""
    current = len(body)
    if target <= 0 or abs(current - target) / target <= LENGTH_TOLERANCE:
        return body
    try:
        return provider.complete(
            prompts.rewrite_length_prompt(body, target, current, language),
            system=prompts.SYSTEM,
            max_tokens=max(1000, target * 2),
        ).strip()
    except LLMError:
        return body


def write_sections(
    outline: dict,
    transcript: Transcript,
    provider: LLMProvider,
    spec: DetailSpec,
    language: str,
    on_progress: ProgressFn | None = None,
) -> tuple[list[Section], list[Capture]]:
    """write 段。本文を書き、同時にキャプチャ位置が決まる。"""
    raw_sections = outline["sections"]
    count = len(raw_sections)
    section_chars = spec.section_target_chars(count)
    per_section_captures = spec.captures_per_section(count)
    limit = int(provider.context_chars * INPUT_BUDGET_RATIO)
    duration = transcript.duration

    sections: list[Section] = []
    captures: list[Capture] = []
    remaining_budget = spec.max_captures
    previous_titles: list[str] = []

    for index, raw in enumerate(raw_sections):
        title = str(raw.get("title") or f"セクション {index + 1}").strip()
        start, end = _section_bounds(raw, duration)
        excerpt = transcript.to_timestamped_text(start, end)
        if not excerpt.strip():
            excerpt = transcript.to_timestamped_text()

        allowed = min(per_section_captures, max(remaining_budget, 0))
        body = provider.complete(
            prompts.section_write_prompt(
                section_title=title,
                focus=str(raw.get("focus", "")),
                timestamped_text=_truncate(excerpt, limit),
                target_chars=section_chars,
                max_captures=allowed,
                language=language,
                context_note=" → ".join(previous_titles[-3:]),
            ),
            system=prompts.SYSTEM,
            max_tokens=max(1500, section_chars * 3),
        ).strip()

        body = _adjust_length(body, section_chars, provider, language)
        body, section_captures = resolve_markers(
            body, transcript, id_prefix=f"s{index}", max_captures=allowed
        )

        remaining_budget -= len(section_captures)
        captures.extend(section_captures)
        sections.append(Section(title=title, body=body, start=start, end=end))
        previous_titles.append(title)

        if on_progress:
            on_progress(JobStep.WRITE, (index + 1) / count, f"{index + 1}/{count} セクション")

    return sections, captures


def generate_report(
    transcript: Transcript,
    provider: LLMProvider,
    spec: DetailSpec,
    language: str = "日本語",
    on_progress: ProgressFn | None = None,
) -> Report:
    """文字起こしからレポート本体を組み立てる(画像の抽出は別工程)。"""
    summaries = summarize_chunks(transcript, provider, on_progress)

    if on_progress:
        on_progress(JobStep.OUTLINE, 0.0, "章立てを設計中")
    outline = build_outline(summaries, transcript, provider, spec, language)
    if on_progress:
        on_progress(JobStep.OUTLINE, 1.0, f"{len(outline['sections'])} セクション")

    sections, captures = write_sections(
        outline, transcript, provider, spec, language, on_progress
    )

    key_points_text = "\n".join(f"- {k}" for k in outline["key_points"])
    sections_text = "\n".join(f"- {s.title}" for s in sections)
    try:
        overview = provider.complete(
            prompts.overview_prompt(
                title=outline["title"],
                key_points_text=key_points_text,
                sections_text=sections_text,
                language=language,
                target_chars=spec.overview_chars,
            ),
            system=prompts.SYSTEM,
            max_tokens=1500,
        ).strip()
    except LLMError:
        overview = ""

    return Report(
        title=outline["title"],
        overview=overview,
        key_points=outline["key_points"],
        sections=sections,
        captures=captures,
        duration=transcript.duration,
    )
