"""Confluence storage format (XHTML) 出力。

Confluence にそのまま貼れる形にする。画像は添付ファイル参照
(`<ac:image><ri:attachment/></ac:image>`) として書き出すため、API 投稿時は
先に添付を済ませておく必要がある(publish/confluence.py 参照)。

Markdown の完全な変換は目的ではないので、レポート生成で実際に使う記法
(段落・箇条書き・強調・インラインコード・小見出し)だけを扱う軽量変換に
とどめている。
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from seminar_report.models import Capture, Report
from seminar_report.report.markers import RESOLVED_RE

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*(#{3,6})\s+(.*)$")


def _inline(text: str) -> str:
    """インライン記法を XHTML に変換する。エスケープしてから記法を復元する。"""
    out = escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return out


def _image_xhtml(capture: Capture) -> str:
    caption = escape(capture.caption)
    return (
        '<p style="text-align: center;">'
        f'<ac:image ac:align="center" ac:alt="{caption}">'
        f'<ri:attachment ri:filename="{escape(capture.filename or "")}" />'
        "</ac:image></p>"
        f'<p style="text-align: center;"><em>{caption}（{capture.timestamp}）</em></p>'
    )


def _blocks_to_xhtml(body: str, captures: dict[str, Capture]) -> str:
    """本文を段落・リスト・画像のブロックに分解して XHTML にする。"""
    parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{_inline(i)}</li>" for i in list_items)
            parts.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
        list_tag = None

    for line in body.splitlines():
        stripped = line.strip()

        marker = RESOLVED_RE.match(line)
        if marker:
            flush_paragraph()
            flush_list()
            capture = captures.get(marker.group("id"))
            if capture is not None:
                parts.append(_image_xhtml(capture))
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)) + 1, 6)
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        bullet = _BULLET_RE.match(line)
        ordered = _ORDERED_RE.match(line)
        if bullet or ordered:
            flush_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            list_items.append((bullet or ordered).group(1))
            continue

        flush_list()
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    return "".join(parts)


def render_storage(report: Report, include_toc: bool = True) -> str:
    """Confluence storage format の本文を組み立てる。"""
    captures = {c.marker_id: c for c in report.captures if c.included and c.filename}
    parts: list[str] = []

    if include_toc:
        parts.append(
            '<ac:structured-macro ac:name="toc" ac:schema-version="1">'
            '<ac:parameter ac:name="maxLevel">2</ac:parameter>'
            "</ac:structured-macro>"
        )

    if report.overview:
        parts.append("<h2>概要</h2>")
        parts.append(_blocks_to_xhtml(report.overview, captures))

    if report.key_points:
        parts.append("<h2>要点</h2>")
        items = "".join(f"<li>{_inline(p)}</li>" for p in report.key_points)
        parts.append(f"<ul>{items}</ul>")

    for section in report.sections:
        parts.append(f"<h2>{_inline(section.title)}</h2>")
        parts.append(_blocks_to_xhtml(section.body, captures))

    if report.source_video:
        parts.append(f"<hr /><p><em>元動画: {escape(report.source_video)}</em></p>")

    return "".join(parts)


def render_storage_without_images(report: Report) -> str:
    """画像添付前の 1 回目のページ作成に使う、画像参照を含まない本文。"""
    stripped = report.model_copy(deep=True)
    for capture in stripped.captures:
        capture.included = False
    return render_storage(stripped)


NAMESPACES = (
    'xmlns:ac="http://atlassian.com/content" '
    'xmlns:ri="http://atlassian.com/resource/identifier"'
)


def wrap_for_validation(storage_xhtml: str) -> str:
    """`ac:`/`ri:` 名前空間を宣言した形にする。テストでのパース用。"""
    return f"<root {NAMESPACES}>{storage_xhtml}</root>"
