"""Invariants for the composite_on_color_with_shadow drop-shadow synthesis.

We pin the *invariant* (a darker band sits just below the subject's feet),
not the magic opacity/blur/squash numbers — so re-tuning the look in
``CropOptions`` doesn't break the suite.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from crop_pipeline.compose import composite_on_color, composite_on_color_with_shadow


def _synthetic_rgba(size=(400, 600), subject_box=(120, 100, 280, 500)):
    """Build a solid-color subject silhouette inside a transparent canvas.

    Returns the rgba image plus the bounding-box ``y_bottom`` (feet line).
    """
    w, h = size
    rgba = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(rgba)
    draw.rectangle(subject_box, fill=(128, 96, 64, 255))
    return rgba, subject_box[3]


def _column_means(im: Image.Image) -> np.ndarray:
    """Per-row mean luminance across the full image width."""
    arr = np.asarray(im.convert("L"), dtype=np.float32)
    return arr.mean(axis=1)


def test_no_shadow_when_opacity_zero():
    rgba, _ = _synthetic_rgba()
    bg = (248, 242, 242)
    no_shadow = composite_on_color(rgba, bg)
    with_zero = composite_on_color_with_shadow(rgba, bg, shadow_opacity=0.0)
    assert np.array_equal(np.asarray(no_shadow), np.asarray(with_zero))


def test_shadow_darkens_band_below_feet():
    rgba, feet_y = _synthetic_rgba()
    bg = (248, 242, 242)
    plain = composite_on_color(rgba, bg)
    with_shadow = composite_on_color_with_shadow(
        rgba, bg, shadow_opacity=0.25, shadow_blur=12.0, shadow_squash=0.10
    )
    # Sample a 30px-tall band immediately below the feet, across the full
    # subject width. The shadow output should be measurably darker than the
    # plain background there.
    band = slice(feet_y + 5, feet_y + 35)
    plain_lum = _column_means(plain)[band].mean()
    shadow_lum = _column_means(with_shadow)[band].mean()
    assert shadow_lum < plain_lum - 5, (
        f"shadow band not darker enough: plain={plain_lum:.1f} shadow={shadow_lum:.1f}"
    )


def test_shadow_does_not_overlap_subject_pixels():
    """The subject is pasted on top of the shadow — its own pixels must be
    untouched by the shadow synthesis."""
    rgba, _ = _synthetic_rgba()
    bg = (248, 242, 242)
    plain = composite_on_color(rgba, bg)
    with_shadow = composite_on_color_with_shadow(rgba, bg, shadow_opacity=0.5)
    # Sample the center of the subject (well away from the alpha edge so
    # rembg-style anti-aliasing isn't a factor in this synthetic test).
    cx, cy = 200, 300
    assert np.array_equal(
        np.asarray(plain)[cy, cx],
        np.asarray(with_shadow)[cy, cx],
    )


def test_shadow_falls_back_when_alpha_empty():
    """An empty alpha (no subject detected) must not crash — it composites
    plainly onto the background color."""
    empty = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    bg = (248, 242, 242)
    out = composite_on_color_with_shadow(empty, bg, shadow_opacity=0.3)
    arr = np.asarray(out)
    # Every pixel should be the background color.
    assert (arr == np.array(bg)).all()
