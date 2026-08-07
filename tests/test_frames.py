"""フレーム選抜ロジックの検証。合成画像で順序が意図どおりか確かめる。"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageFilter

from seminar_report.media.frames import (
    FrameMetrics,
    dhash,
    extract_candidates,
    hamming_distance,
    laplacian_variance,
    luma_std,
    parse_crop_box,
    select_best,
)


def _slide_image(seed: int = 0) -> Image.Image:
    """文字と図形が載ったスライド風の画像。"""
    rng = np.random.default_rng(seed)
    array = np.full((240, 320), 240, dtype=np.uint8)
    for _ in range(30):
        y, x = rng.integers(10, 200), rng.integers(10, 280)
        array[y : y + 8, x : x + 30] = 20
    return Image.fromarray(array, mode="L")


def _metrics(image: Image.Image, time: float, tmp_path, name: str) -> FrameMetrics:
    path = tmp_path / f"{name}.png"
    image.save(path)
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    return FrameMetrics(
        path=path,
        time=time,
        sharpness=laplacian_variance(gray),
        std_luma=luma_std(gray),
        mean_luma=float(gray.mean()),
        dhash=dhash(image),
    )


def test_sharp_image_scores_higher_than_blurred() -> None:
    sharp = np.asarray(_slide_image(), dtype=np.float64)
    blurred = np.asarray(_slide_image().filter(ImageFilter.GaussianBlur(4)), dtype=np.float64)
    assert laplacian_variance(sharp) > laplacian_variance(blurred)


def test_blank_frame_is_detected(tmp_path) -> None:
    black = _metrics(Image.new("L", (320, 240), 0), 0.0, tmp_path, "black")
    slide = _metrics(_slide_image(), 1.0, tmp_path, "slide")
    assert black.is_blank
    assert not slide.is_blank


def test_select_best_prefers_sharp_slide_over_blank(tmp_path) -> None:
    candidates = [
        _metrics(Image.new("L", (320, 240), 0), 0.0, tmp_path, "black"),
        _metrics(_slide_image().filter(ImageFilter.GaussianBlur(5)), 1.0, tmp_path, "blur"),
        _metrics(_slide_image(), 2.0, tmp_path, "sharp"),
    ]
    best = select_best(candidates)
    assert best is not None
    assert best.path.name == "sharp.png"


def test_select_best_skips_near_duplicates(tmp_path) -> None:
    first = _metrics(_slide_image(seed=1), 0.0, tmp_path, "a")
    different = _metrics(_slide_image(seed=99), 1.0, tmp_path, "b")

    # 1 枚目と同じ絵しか無ければ、既出扱いで採用されない
    assert select_best([first], used_hashes=[first.dhash]) is None
    # 別の絵があればそちらが選ばれる
    chosen = select_best([first, different], used_hashes=[first.dhash])
    assert chosen is not None and chosen.path.name == "b.png"


def test_select_best_returns_none_for_empty() -> None:
    assert select_best([]) is None


def test_all_blank_candidates_still_return_something(tmp_path) -> None:
    """全部が黒でも、呼び出し側が判断できるよう 1 枚は返す。"""
    candidates = [
        _metrics(Image.new("L", (320, 240), 0), 0.0, tmp_path, "b1"),
        _metrics(Image.new("L", (320, 240), 2), 1.0, tmp_path, "b2"),
    ]
    assert select_best(candidates) is not None


def test_hamming_distance_of_identical_hash_is_zero() -> None:
    image = _slide_image()
    assert hamming_distance(dhash(image), dhash(image)) == 0


@pytest.mark.parametrize("value", [0, 128, 255])
def test_uniform_image_has_zero_std(value: int) -> None:
    gray = np.full((64, 64), value, dtype=np.float64)
    assert luma_std(gray) == pytest.approx(0.0)


def test_white_slide_with_black_text_is_not_blank(tmp_path) -> None:
    """白背景に黒文字のスライドを空白と誤判定しないこと。

    輝度ヒストグラムのエントロピーで判定すると、この典型的なスライドが
    二峰性ゆえに低エントロピーとなり除外されてしまっていた。
    """
    slide = _metrics(_slide_image(), 0.0, tmp_path, "slide")
    assert not slide.is_blank


def test_blurred_frame_does_not_outrank_sharp_one(tmp_path) -> None:
    """ぼけたフレームが「情報量が多い」と誤って評価されないこと。"""
    candidates = [
        _metrics(_slide_image().filter(ImageFilter.GaussianBlur(5)), 0.0, tmp_path, "blur"),
        _metrics(_slide_image(), 1.0, tmp_path, "sharp"),
    ]
    best = select_best(candidates)
    assert best is not None and best.path.name == "sharp.png"


# ---- crop ----


@pytest.mark.parametrize(
    "text",
    ["0.1,0.1,0.9", "a,b,c,d", "0.1,0.1,0.9,1.5", "0.5,0.1,0.4,0.9", "0.1,0.5,0.9,0.4"],
)
def test_parse_crop_box_rejects_invalid_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_crop_box(text)


def test_parse_crop_box_accepts_valid_fractions() -> None:
    assert parse_crop_box("0.02,0.13,0.76,0.87") == (0.02, 0.13, 0.76, 0.87)


def test_parse_crop_box_strips_whitespace() -> None:
    assert parse_crop_box(" 0.0 , 0.0 , 1.0 , 1.0 ") == (0.0, 0.0, 1.0, 1.0)


def test_extract_candidates_applies_crop(sample_video, tmp_path) -> None:
    """crop 指定時、実際にアスペクト比が変わった画像が出ること。

    テスト動画は 640x360(16:9)。左半分だけ・縦は全体を切り出すと
    320x360(8:9)になり、幅 1280 に拡大した高さは無指定時と明確に変わる
    ので、crop が実際に効いているかを検出できる。
    """
    crop = (0.0, 0.0, 0.5, 1.0)
    candidates = extract_candidates(
        sample_video, 5.0, tmp_path, prefix="crop-test", duration=70.0, count=1, crop=crop
    )
    assert len(candidates) == 1
    with Image.open(candidates[0].path) as image:
        width, height = image.size
    assert width == 1280
    assert height == pytest.approx(1280 * 360 / 320, abs=2)


def test_extract_candidates_without_crop_uses_full_frame(sample_video, tmp_path) -> None:
    candidates = extract_candidates(
        sample_video, 5.0, tmp_path, prefix="nocrop-test", duration=70.0, count=1
    )
    assert len(candidates) == 1
    with Image.open(candidates[0].path) as image:
        width, height = image.size
    assert width == 1280
    assert height == pytest.approx(1280 * 360 / 640, abs=2)
