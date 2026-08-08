"""Markdown 出力。"""

from __future__ import annotations

import re

from seminar_report.models import Capture, Report
from seminar_report.report.markers import RESOLVED_RE, strip_markers


def _capture_map(report: Report) -> dict[str, Capture]:
    return {
        c.marker_id: c
        for c in report.captures
        if c.included and c.filename
    }


def _substitute(body: str, captures: dict[str, Capture], image_prefix: str) -> str:
    def replace(match) -> str:
        capture = captures.get(match.group("id"))
        if capture is None:
            return ""
        return (
            f"![{capture.caption}]({image_prefix}{capture.filename})\n"
            f"*{capture.caption}（{capture.timestamp}）*"
        )

    # 不採用になったマーカーの行が空行として残らないようにする
    return re.sub(r"\n{3,}", "\n\n", RESOLVED_RE.sub(replace, body)).strip()


def render_markdown(report: Report, image_prefix: str = "images/") -> str:
    """レポートを Markdown 文字列にする。"""
    captures = _capture_map(report)
    lines: list[str] = [f"# {report.title}", ""]

    if report.overview:
        lines += ["# 概要", "", report.overview.strip(), ""]

    if report.key_points:
        lines += ["## 要点", ""]
        lines += [f"- {point}" for point in report.key_points]
        lines.append("")

    for section in report.sections:
        body = _substitute(section.body, captures, image_prefix).strip()
        lines += [f"# {section.title}", "", body, ""]

    return "\n".join(lines).rstrip() + "\n"


def render_plain_markdown(report: Report) -> str:
    """画像を含まないテキストのみの Markdown。"""
    lines: list[str] = [f"# {report.title}", ""]
    if report.overview:
        lines += ["# 概要", "", report.overview.strip(), ""]
    if report.key_points:
        lines += ["## 要点", ""] + [f"- {p}" for p in report.key_points] + [""]
    for section in report.sections:
        lines += [f"# {section.title}", "", strip_markers(section.body), ""]
    return "\n".join(lines).rstrip() + "\n"
