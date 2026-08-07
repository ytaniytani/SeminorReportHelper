"""単体で開ける HTML レポート出力。

Confluence の「マークダウン」マクロが画像を自動リンクしてくれない・
「マークアップ」(storage format)マクロが使えない環境向けの代替経路。
report.html をブラウザで開いて全選択コピーし、Confluence の編集画面に
そのまま貼り付けると、実際に表示された画像として貼り付けられることが多い
(Confluence が貼り付け時に画像を自動でアップロード・添付する)。

画像は base64 で埋め込む。report.html 1 ファイルだけで完結させ、
images/ フォルダの有無やコピー先のパスに依存させないため。
"""

from __future__ import annotations

import base64
import mimetypes
from xml.sax.saxutils import escape

from seminar_report.models import Capture, Report
from seminar_report.render.blocks import blocks_to_xhtml, inline

_STYLE = """
body { font-family: -apple-system, "Hiragino Kaku Gothic ProN", "Yu Gothic", \
sans-serif; line-height: 1.8; max-width: 860px; margin: 40px auto; padding: 0 20px; \
color: #1f2328; }
h1 { font-size: 1.6em; border-bottom: 1px solid #d9dde3; padding-bottom: 4px; \
margin-top: 2em; }
h1.doc-title { font-size: 1.9em; border-bottom: none; margin-top: 0; padding-bottom: 0; }
h2 { font-size: 1.2em; margin-top: 1.6em; }
figure { text-align: center; margin: 1.5em 0; }
figure img { max-width: 100%; border: 1px solid #d9dde3; border-radius: 6px; }
figcaption { color: #6b7280; font-size: .9em; margin-top: 6px; }
code { background: #f0f1f3; padding: 1px 5px; border-radius: 4px; }
"""


def _image_html(capture: Capture) -> str:
    caption = escape(capture.caption)
    src = _data_uri(capture)
    return (
        "<figure>"
        f'<img src="{src}" alt="{caption}" />'
        f"<figcaption>{caption}（{capture.timestamp}）</figcaption>"
        "</figure>"
    )


def _data_uri(capture: Capture) -> str:
    """画像を base64 の data URI にする。読めない場合は相対パスにフォールバック。"""
    if capture.image_path is not None:
        try:
            media_type = mimetypes.guess_type(capture.image_path.name)[0] or "image/jpeg"
            data = base64.standard_b64encode(capture.image_path.read_bytes()).decode("ascii")
            return f"data:{media_type};base64,{data}"
        except OSError:
            pass
    return f"images/{capture.filename or ''}"


def render_html(report: Report) -> str:
    """レポート全体を単体の HTML ドキュメントにする。"""
    captures = {c.marker_id: c for c in report.captures if c.included and c.filename}

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ja"><head><meta charset="utf-8" />',
        f"<title>{escape(report.title)}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f'<h1 class="doc-title">{escape(report.title)}</h1>',
    ]

    if report.overview:
        parts.append("<h1>概要</h1>")
        parts.append(blocks_to_xhtml(report.overview, captures, _image_html))

    if report.key_points:
        parts.append("<h2>要点</h2>")
        items = "".join(f"<li>{inline(p)}</li>" for p in report.key_points)
        parts.append(f"<ul>{items}</ul>")

    for section in report.sections:
        parts.append(f"<h1>{inline(section.title)}</h1>")
        parts.append(blocks_to_xhtml(section.body, captures, _image_html))

    if report.source_video:
        parts.append(f"<hr /><p><em>元動画: {escape(report.source_video)}</em></p>")

    parts.append("</body></html>")
    return "\n".join(parts)
