from __future__ import annotations

from seminar_report.models import DetailLevel
from seminar_report.report.detail import PRESETS, resolve_detail


def test_presets_are_returned_as_is() -> None:
    spec = resolve_detail(DetailLevel.BRIEF)
    assert spec is PRESETS[DetailLevel.BRIEF]
    assert spec.target_chars == 800


def test_explicit_chars_override_preset() -> None:
    spec = resolve_detail(DetailLevel.BRIEF, target_chars=4000)
    assert spec.level is DetailLevel.CUSTOM
    assert spec.target_chars == 4000


def test_capture_count_scales_with_length() -> None:
    small = resolve_detail(DetailLevel.CUSTOM, target_chars=1000)
    large = resolve_detail(DetailLevel.CUSTOM, target_chars=8000)
    assert small.max_captures < large.max_captures


def test_target_chars_are_clamped() -> None:
    assert resolve_detail(DetailLevel.CUSTOM, target_chars=1).target_chars == 200
    assert resolve_detail(DetailLevel.CUSTOM, target_chars=10**9).target_chars == 30_000


def test_explicit_max_captures_wins() -> None:
    assert resolve_detail(DetailLevel.DETAILED, max_captures=0).max_captures == 0
    assert resolve_detail(DetailLevel.CUSTOM, target_chars=3000, max_captures=2).max_captures == 2


def test_section_budget_splits_body_chars() -> None:
    spec = resolve_detail(DetailLevel.STANDARD)
    per_section = spec.section_target_chars(5)
    assert 100 < per_section < spec.target_chars


def test_captures_per_section_is_not_split_evenly_across_sections() -> None:
    """章数で均等分割せず、全体の目安枚数をそのまま各章の上限として渡す。

    絞り込んだレポートのように重要な瞬間が特定の章に集中する場合でも
    足りなくならないようにするため。
    """
    spec = resolve_detail(DetailLevel.BRIEF)
    assert spec.captures_per_section(10) == spec.max_captures == 3
    assert resolve_detail(DetailLevel.DETAILED, max_captures=0).captures_per_section(5) == 0
