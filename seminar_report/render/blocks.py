"""Markdown ライクな本文記法を XHTML に変換する共通ロジック。

レポート生成が実際に使う記法(段落・箇条書き・強調・インラインコード・
小見出し・画像マーカー)だけを扱う軽量変換。完全な Markdown 変換ではない。
Confluence storage format と HTML エクスポートの両方から使う — 画像1枚を
どう埋め込むか(添付参照 or data URI)だけが異なるため、そこだけ
`render_image` コールバックとして差し替え可能にしている。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from xml.sax.saxutils import escape

from seminar_report.models import Capture
from seminar_report.report.markers import RESOLVED_RE

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*(#{3,6})\s+(.*)$")

ImageRenderer = Callable[[Capture], str]
"""1 枚のキャプチャを XHTML 断片にする関数。"""


def inline(text: str) -> str:
    """インライン記法を XHTML に変換する。エスケープしてから記法を復元する。"""
    out = escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return out


def blocks_to_xhtml(
    body: str, captures: dict[str, Capture], render_image: ImageRenderer
) -> str:
    """本文を段落・リスト・画像のブロックに分解して XHTML にする。"""
    parts: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{inline(i)}</li>" for i in list_items)
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
                parts.append(render_image(capture))
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
            parts.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
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
