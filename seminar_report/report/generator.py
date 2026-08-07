"""文字起こしからレポートを生成する map → outline → write の 3 段構成。

1 時間程度なら全文が 1 コンテキストに収まるが、プロバイダ(特に Ollama)や
長尺動画では収まらない。最初から分割前提にしておくことで、プロバイダを
差し替えても長さが変わっても同じ経路で動くようにしている。
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from seminar_report.config import get_settings
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
from seminar_report.report.markers import resolve_markers, snap_to_segment

ProgressFn = Callable[[JobStep, float, str], None]

def _concurrency(count: int) -> int:
    """並列に投げる LLM リクエスト数。設定値と対象数の小さい方。"""
    return max(1, min(get_settings().llm_concurrency, count))


def _map_ordered(func: Callable, items: list, on_done: Callable[[int], None] | None = None) -> list:
    """items を並列に処理し、結果を元の順序で返す。

    LLM 呼び出しはネットワーク待ちが大半なのでスレッドで十分に効く。
    進捗は「完了した数」で報告する(完了順は入力順と一致しないため)。
    """
    if not items:
        return []
    if len(items) == 1:
        result = [func(0, items[0])]
        if on_done:
            on_done(1)
        return result

    results: list = [None] * len(items)
    with ThreadPoolExecutor(max_workers=_concurrency(len(items))) as executor:
        futures = {
            executor.submit(func, index, item): index for index, item in enumerate(items)
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            results[futures[future]] = future.result()
            if on_done:
                on_done(completed)
    return results


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

    # 中身が空の区間は投げる前に落とす(無音の末尾など)
    targets = [
        (start, end, text)
        for start, end in chunks
        if (text := transcript.to_timestamped_text(start, end)).strip()
    ]
    if not targets:
        return []

    total = len(targets)

    def summarize_one(index: int, target: tuple[float, float, str]) -> ChunkSummary:
        start, end, text = target
        prompt = prompts.chunk_summary_prompt(_truncate(text, limit), index, total)
        try:
            data = provider.complete_json(prompt, system=prompts.SYSTEM, max_tokens=1500)
        except LLMError:
            # 1 区間の失敗で全体を落とさない。素のテキスト要約にフォールバック。
            data = {"summary": provider.complete(prompt, system=prompts.SYSTEM), "topics": []}
        return ChunkSummary(
            start=start,
            end=end,
            summary=str(data.get("summary", "")).strip(),
            topics=[str(t) for t in data.get("topics", []) if str(t).strip()],
        )

    def report(done: int) -> None:
        if on_progress:
            on_progress(JobStep.SUMMARIZE, done / total, f"{done}/{total} 区間")

    # 区間同士は独立しているので並列に投げられる。結果は入力順に戻る。
    return _map_ordered(summarize_one, targets, report)


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
    user_request: str = "",
) -> dict:
    """outline 段。章立ては固定テンプレートではなく AI に設計させる。"""
    duration_label = format_timestamp(transcript.duration)
    prompt = prompts.outline_prompt(
        summaries_text=_summaries_text(summaries),
        duration_label=duration_label,
        target_chars=spec.target_chars,
        section_range=spec.section_range,
        language=language,
        user_request=user_request,
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
    user_request: str = "",
) -> tuple[list[Section], list[Capture]]:
    """write 段。本文を書き、同時にキャプチャ位置が決まる。"""
    raw_sections = outline["sections"]
    count = len(raw_sections)
    section_chars = spec.section_target_chars(count)
    per_section_captures = spec.captures_per_section(count)
    limit = int(provider.context_chars * INPUT_BUDGET_RATIO)
    duration = transcript.duration

    # 章題は outline 段で出揃っているため、直前までの流れを事前に組み立てられる。
    # これにより各セクションの執筆が互いに独立し、並列に投げられる。
    titles = [
        str(raw.get("title") or f"セクション {index + 1}").strip()
        for index, raw in enumerate(raw_sections)
    ]

    def write_one(index: int, raw: dict) -> tuple[Section, list[Capture]]:
        title = titles[index]
        start, end = _section_bounds(raw, duration)
        excerpt = transcript.to_timestamped_text(start, end)
        if not excerpt.strip():
            excerpt = transcript.to_timestamped_text()

        body = provider.complete(
            prompts.section_write_prompt(
                section_title=title,
                focus=str(raw.get("focus", "")),
                timestamped_text=_truncate(excerpt, limit),
                target_chars=section_chars,
                max_captures=per_section_captures,
                language=language,
                context_note=" → ".join(titles[max(index - 3, 0) : index]),
                user_request=user_request,
            ),
            system=prompts.SYSTEM,
            max_tokens=max(1500, section_chars * 3),
        ).strip()

        body = _adjust_length(body, section_chars, provider, language)
        body, section_captures = resolve_markers(
            body, transcript, id_prefix=f"s{index}", max_captures=per_section_captures
        )

        # プロンプトで指示していても、LLM が結局 1 個もマーカーを置かないことが
        # ある。見出しごとに最低 1 枚は入れたいので、その場合はセクション中央
        # 付近の時刻を機械的に採用する(キャプションは見出しを流用)。
        if not section_captures and per_section_captures >= 1:
            midpoint = (start + end) / 2 if end > start else start
            resolved = snap_to_segment(midpoint, transcript)
            if resolved is not None:
                marker_id = f"s{index}_0"
                body = (body.rstrip() + f"\n\n[[capture:{marker_id}]]").strip()
                section_captures = [
                    Capture(
                        marker_id=marker_id,
                        requested_time=midpoint,
                        resolved_time=resolved,
                        caption=title,
                    )
                ]

        return Section(title=title, body=body, start=start, end=end), section_captures

    def report(done: int) -> None:
        if on_progress:
            on_progress(JobStep.WRITE, done / count, f"{done}/{count} セクション")

    written = _map_ordered(write_one, list(raw_sections), report)

    # 画像点数が多いこと自体は問題ではないため、章をまたいだ全体予算での
    # 切り詰めは行わない。各章の枚数は resolve_markers 側の目安上限
    # (captures_per_section)と、見出しごとに最低1枚の保証だけで決まる。
    sections: list[Section] = []
    captures: list[Capture] = []
    for section, section_captures in written:
        sections.append(section)
        captures.extend(section_captures)

    return sections, captures


def generate_report(
    transcript: Transcript,
    provider: LLMProvider,
    spec: DetailSpec,
    language: str = "日本語",
    on_progress: ProgressFn | None = None,
    user_request: str = "",
) -> Report:
    """文字起こしからレポート本体を組み立てる(画像の抽出は別工程)。"""
    summaries = summarize_chunks(transcript, provider, on_progress)

    if on_progress:
        on_progress(JobStep.OUTLINE, 0.0, "章立てを設計中")
    outline = build_outline(summaries, transcript, provider, spec, language, user_request)
    if on_progress:
        on_progress(JobStep.OUTLINE, 1.0, f"{len(outline['sections'])} セクション")

    sections, captures = write_sections(
        outline, transcript, provider, spec, language, on_progress, user_request
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
