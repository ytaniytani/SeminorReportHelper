"""詳細度プリセットと、そこから派生する生成パラメータ。"""

from __future__ import annotations

from dataclasses import dataclass

from seminar_report.models import DetailLevel


@dataclass(frozen=True)
class DetailSpec:
    """1 回の生成に使う分量の指定。"""

    level: DetailLevel
    label: str
    target_chars: int
    section_range: tuple[int, int]
    max_captures: int

    @property
    def overview_chars(self) -> int:
        """概要文の目標文字数。全体の 1 割強、ただし 150〜600 字に収める。"""
        return max(150, min(600, self.target_chars // 8))

    def section_target_chars(self, section_count: int) -> int:
        """1 セクションあたりの目標文字数。"""
        body_chars = max(self.target_chars - self.overview_chars, 200)
        return max(150, body_chars // max(section_count, 1))

    def captures_per_section(self, section_count: int) -> int:
        """1 セクションあたりに許可するキャプチャ数の上限。

        全体の予算を割り振ったうえで、必ず 1 以上は許可する(重要な図が
        1 枚も入らない事故を防ぐ)。実際に入る枚数は LLM の判断次第。
        """
        if self.max_captures <= 0:
            return 0
        return max(1, -(-self.max_captures // max(section_count, 1)))


PRESETS: dict[DetailLevel, DetailSpec] = {
    DetailLevel.BRIEF: DetailSpec(
        level=DetailLevel.BRIEF,
        label="簡潔",
        target_chars=800,
        section_range=(3, 4),
        max_captures=3,
    ),
    DetailLevel.STANDARD: DetailSpec(
        level=DetailLevel.STANDARD,
        label="標準",
        target_chars=2500,
        section_range=(5, 7),
        max_captures=6,
    ),
    DetailLevel.DETAILED: DetailSpec(
        level=DetailLevel.DETAILED,
        label="詳細",
        target_chars=6000,
        section_range=(8, 12),
        max_captures=12,
    ),
}

MIN_TARGET_CHARS = 200
MAX_TARGET_CHARS = 30_000


def resolve_detail(
    level: DetailLevel | str = DetailLevel.STANDARD,
    target_chars: int | None = None,
    max_captures: int | None = None,
) -> DetailSpec:
    """プリセットと任意指定から最終的な DetailSpec を作る。

    文字数を明示した場合はプリセットより優先し、セクション数と画像枚数を
    その文字数に見合う値へ自動で引き直す。
    """
    level = DetailLevel(level)

    if level is not DetailLevel.CUSTOM and target_chars is None:
        spec = PRESETS[level]
        if max_captures is None:
            return spec
        return DetailSpec(
            level=spec.level,
            label=spec.label,
            target_chars=spec.target_chars,
            section_range=spec.section_range,
            max_captures=max(0, max_captures),
        )

    chars = target_chars if target_chars is not None else PRESETS[DetailLevel.STANDARD].target_chars
    chars = max(MIN_TARGET_CHARS, min(MAX_TARGET_CHARS, chars))

    # 目安として 400 字に 1 セクション、500 字に 1 枚の画像。
    sections = max(3, min(15, round(chars / 400)))
    captures = max_captures if max_captures is not None else max(2, chars // 500)

    return DetailSpec(
        level=DetailLevel.CUSTOM,
        label=f"カスタム({chars}字)",
        target_chars=chars,
        section_range=(max(2, sections - 1), sections + 2),
        max_captures=max(0, captures),
    )
