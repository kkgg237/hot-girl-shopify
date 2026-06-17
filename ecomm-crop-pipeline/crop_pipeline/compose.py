"""Compose the final image: crop, background-replace, and resize."""

from __future__ import annotations

from typing import Tuple

from PIL import Image, ImageFilter


def composite_on_color(rgba: Image.Image, color: Tuple[int, int, int]) -> Image.Image:
    """Flatten an RGBA image onto a solid background."""
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    bg = Image.new("RGB", rgba.size, color)
    bg.paste(rgba, mask=rgba.split()[3])
    return bg


def composite_on_color_with_shadow(
    rgba: Image.Image,
    color: Tuple[int, int, int],
    *,
    shadow_opacity: float = 0.18,
    shadow_blur: float = 24.0,
    shadow_squash: float = 0.08,
    shadow_offset: float = 0.0,
) -> Image.Image:
    """Flatten ``rgba`` onto a solid background with a soft drop shadow.

    The shadow is derived from the subject's alpha mask: cropped to the
    subject's bounding box, squashed vertically to ``shadow_squash * subject_h``,
    blurred, dimmed to ``shadow_opacity``, and centered on the feet line. The
    subject itself is then pasted on top — so the shadow can never bleed over
    the subject's own pixels.

    When the alpha mask is empty (no subject detected) this falls back to the
    plain ``composite_on_color`` behavior.
    """
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    w, h = rgba.size
    alpha = rgba.split()[3]
    bbox = alpha.getbbox()
    if bbox is None or shadow_opacity <= 0.0:
        return composite_on_color(rgba, color)

    _, top, _, bottom = bbox
    subj_h = max(bottom - top, 1)
    band_h = max(int(round(subj_h * shadow_squash)), 2)

    # Squash the subject's alpha (full-width slice so silhouette stays
    # centered) down to ``band_h``.
    subj_strip = alpha.crop((0, top, w, bottom)).resize((w, band_h), Image.LANCZOS)

    # Place the squashed band centered on the feet line. ``shadow_offset`` is
    # measured in shadow-band heights so the same value reads the same across
    # output sizes.
    paste_y = int(round(bottom - band_h / 2 + shadow_offset * band_h))
    shadow_mask = Image.new("L", (w, h), 0)
    shadow_mask.paste(subj_strip, (0, paste_y))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=shadow_blur))

    # Dim to target opacity.
    opacity = max(0.0, min(1.0, shadow_opacity))
    shadow_mask = shadow_mask.point(lambda p: int(p * opacity))

    # Composite: black where shadow_mask says so, otherwise the bg color.
    bg = Image.new("RGB", (w, h), color)
    black = Image.new("RGB", (w, h), (0, 0, 0))
    bg = Image.composite(black, bg, shadow_mask)

    # Finally paste the subject on top so the shadow never overlaps the body.
    bg.paste(rgba, mask=alpha)
    return bg


def crop_and_resize(
    image: Image.Image,
    box: Tuple[int, int, int, int],
    output_size: Tuple[int, int],
    allow_upscale: bool = True,
) -> Image.Image:
    """Crop ``image`` to ``box`` and resize to ``output_size``.

    When ``allow_upscale`` is False and the crop is smaller than the target on
    either axis, the cropped pixels are returned at their native size — no
    interpolation. The aspect ratio of ``box`` should already match
    ``output_size`` (the crop math guarantees this).
    """
    cropped = image.crop(box)
    cw, ch = cropped.size
    ow, oh = output_size
    if cw == ow and ch == oh:
        return cropped
    if not allow_upscale and (cw < ow or ch < oh):
        return cropped
    return cropped.resize(output_size, Image.LANCZOS)
