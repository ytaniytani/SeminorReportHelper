"""レンダリング結果の検証。Confluence に貼れる XHTML であることを保証する。"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from seminar_report.models import Capture, Report, Section
from seminar_report.render.confluence_storage import (
    render_storage,
    render_storage_without_images,
    wrap_for_validation,
)
from seminar_report.render.html import render_html
from seminar_report.render.markdown import render_markdown


@pytest.fixture
def report() -> Report:
    return Report(
        title="社内AI活用セミナー",
        overview="本セミナーでは導入事例を扱った。",
        key_points=["効果は明確", "課題は運用体制"],
        sections=[
            Section(
                title="アーキテクチャ<解説>",
                body=(
                    "全体像は次のとおりです。\n\n"
                    "[[capture:s0_0]]\n\n"
                    "- **重要** な点\n"
                    "- `code` を含む点\n\n"
                    "### 補足\n"
                    "詳細は後述します。"
                ),
            )
        ],
        captures=[
            Capture(
                marker_id="s0_0",
                requested_time=25.0,
                resolved_time=27.0,
                caption="全体構成図 & 凡例",
                filename="s0_0.jpg",
                image_path=Path("images/s0_0.jpg"),
            )
        ],
        duration=70.0,
    )


def test_storage_is_well_formed_xml(report: Report) -> None:
    ET.fromstring(wrap_for_validation(render_storage(report)))


def test_storage_escapes_special_characters(report: Report) -> None:
    xhtml = render_storage(report)
    assert "&lt;解説&gt;" in xhtml
    assert "&amp;" in xhtml


def test_storage_embeds_attachment_reference(report: Report) -> None:
    xhtml = render_storage(report)
    assert 'ri:filename="s0_0.jpg"' in xhtml
    assert "<ac:image" in xhtml


def test_storage_converts_markdown_constructs(report: Report) -> None:
    xhtml = render_storage(report)
    assert "<strong>重要</strong>" in xhtml
    assert "<code>code</code>" in xhtml
    assert "<ul><li>" in xhtml
    assert "<h4>補足</h4>" in xhtml


def test_first_pass_body_has_no_image_reference(report: Report) -> None:
    """画像添付前のページ作成に使う本文には添付参照が含まれてはいけない。"""
    xhtml = render_storage_without_images(report)
    assert "ri:attachment" not in xhtml
    assert "アーキテクチャ" in xhtml
    ET.fromstring(wrap_for_validation(xhtml))


def test_excluded_capture_is_not_rendered(report: Report) -> None:
    report.captures[0].included = False
    assert "ri:attachment" not in render_storage(report)

    markdown = render_markdown(report)
    assert "s0_0.jpg" not in markdown
    # 不採用のマーカー跡が空行として残らないこと
    assert "\n\n\n" not in markdown


def test_markdown_contains_image_and_caption(report: Report) -> None:
    markdown = render_markdown(report)
    assert "![全体構成図 & 凡例](images/s0_0.jpg)" in markdown
    assert "00:00:27" in markdown
    assert "## アーキテクチャ<解説>" in markdown


# ---- HTML エクスポート ----


def test_html_is_self_contained_document(report: Report) -> None:
    html = render_html(report)
    assert html.startswith("<!doctype html>")
    assert "<html" in html and "</html>" in html
    assert f"<title>{report.title}</title>" in html


def test_html_escapes_special_characters(report: Report) -> None:
    html = render_html(report)
    assert "&lt;解説&gt;" in html


def test_html_converts_markdown_constructs(report: Report) -> None:
    html = render_html(report)
    assert "<strong>重要</strong>" in html
    assert "<code>code</code>" in html
    assert "<ul><li>" in html
    assert "<h4>補足</h4>" in html


def test_html_embeds_image_as_base64(report: Report, tmp_path: Path) -> None:
    """画像ファイルが実在する場合、data URI として埋め込まれること。"""
    image_bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    image_path = tmp_path / "s0_0.jpg"
    image_path.write_bytes(image_bytes)
    report.captures[0].image_path = image_path

    html = render_html(report)

    expected = base64.standard_b64encode(image_bytes).decode("ascii")
    assert f"data:image/jpeg;base64,{expected}" in html
    assert "全体構成図 &amp; 凡例" in html
    assert "00:00:27" in html


def test_html_falls_back_to_relative_path_when_image_missing(report: Report) -> None:
    """画像ファイルが実在しない場合、data URI にできないので相対パスに逃がす。"""
    html = render_html(report)
    assert "src=\"images/s0_0.jpg\"" in html


def test_html_excludes_excluded_capture(report: Report) -> None:
    report.captures[0].included = False
    html = render_html(report)
    assert "data:image" not in html
    assert "<img" not in html
