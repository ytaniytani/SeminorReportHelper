"""Confluence storage format (XHTML) 出力。

Confluence にそのまま貼れる形にする。画像は添付ファイル参照
(`<ac:image><ri:attachment/></ac:image>`) として書き出すため、API 投稿時は
先に添付を済ませておく必要がある(publish/confluence.py 参照)。

本文の Markdown ライクな記法(段落・箇条書き・強調・インラインコード・
小見出し)の変換ロジックは render/blocks.py に共通化してある。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from seminar_report.models import Capture, Report
from seminar_report.render.blocks import blocks_to_xhtml, inline


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
    return blocks_to_xhtml(body, captures, _image_xhtml)


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
        parts.append("<h1>概要</h1>")
        parts.append(_blocks_to_xhtml(report.overview, captures))

    if report.key_points:
        parts.append("<h2>要点</h2>")
        items = "".join(f"<li>{inline(p)}</li>" for p in report.key_points)
        parts.append(f"<ul>{items}</ul>")

    for section in report.sections:
        parts.append(f"<h1>{inline(section.title)}</h1>")
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
