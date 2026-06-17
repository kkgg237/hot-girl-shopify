"""Crop region math.

Given the subject bounding box and the desired output canvas, compute the
crop rectangle in source coordinates so that:
  * the result has the target aspect ratio
  * the subject's full height fits and fills ``subject_height_fraction`` of
    the output height
  * the subject is centered horizontally
  * the crop stays within the source image (sliding inward as needed)
"""

from __future__ import annotations

from typing import Tuple

from crop_pipeline.subject import SubjectBox


def compute_crop_box(
    source_size: Tuple[int, int],
    subject: SubjectBox,
    output_size: Tuple[int, int],
    subject_height_fraction: float,
    vertical_bias: float = 0.0,
) -> Tuple[int, int, int, int]:
    """Return (left, top, right, bottom) integer crop box for the source.

    The returned box has the same aspect ratio as ``output_size``.
    """
    src_w, src_h = source_size
    out_w, out_h = output_size
    aspect = out_w / out_h  # width / height

    # Crop height needed so subject fills the requested fraction of output
    # height after resizing.
    crop_h = subject.height / max(subject_height_fraction, 1e-3)
    crop_w = crop_h * aspect

    # If the desired crop is wider than the source, shrink to fit and let the
    # subject get smaller in the output (we never upscale beyond source).
    if crop_w > src_w:
        crop_w = float(src_w)
        crop_h = crop_w / aspect
    if crop_h > src_h:
        crop_h = float(src_h)
        crop_w = crop_h * aspect

    cx, cy = subject.center
    # Apply vertical bias as a fraction of the crop height.
    cy += vertical_bias * crop_h

    left = cx - crop_w / 2.0
    top = cy - crop_h / 2.0
    right = left + crop_w
    bottom = top + crop_h

    # Slide the window so it lies within the source rather than clipping.
    if left < 0:
        right -= left
        left = 0.0
    if top < 0:
        bottom -= top
        top = 0.0
    if right > src_w:
        diff = right - src_w
        left -= diff
        right = float(src_w)
    if bottom > src_h:
        diff = bottom - src_h
        top -= diff
        bottom = float(src_h)

    # Final clamp in case the crop is larger than the source on one axis.
    left = max(0.0, left)
    top = max(0.0, top)
    right = min(float(src_w), right)
    bottom = min(float(src_h), bottom)

    return (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))


def compute_region_crop_box(
    source_size: Tuple[int, int],
    subject: SubjectBox,
    output_size: Tuple[int, int],
    region_of_subject: Tuple[float, float],
    region_fill: float = 0.92,
) -> Tuple[int, int, int, int]:
    """Crop into a vertical slice of the detected subject (a 'detail' shot).

    ``region_of_subject`` is a ``(top_frac, bottom_frac)`` pair where each is
    a fraction of the subject's height (0.0 = head, 1.0 = feet). The slice is
    sized to fill ``region_fill`` of the output height, then the crop window
    expands to the output aspect ratio centered on the slice.
    """
    src_w, src_h = source_size
    out_w, out_h = output_size
    aspect = out_w / out_h

    top_frac, bot_frac = region_of_subject
    if not (top_frac < bot_frac):
        raise ValueError(f"region_of_subject top must be less than bottom, got {region_of_subject!r}")
    # Negative top values extend the crop above the subject's head (adds
    # headroom). Values > 1.0 extend below the feet. The crop math clamps
    # the final box to the source image either way.

    region_top = subject.top + top_frac * subject.height
    region_bot = subject.top + bot_frac * subject.height
    region_h = region_bot - region_top
    region_cy = (region_top + region_bot) / 2.0

    crop_h = region_h / max(region_fill, 1e-3)
    crop_w = crop_h * aspect

    if crop_w > src_w:
        crop_w = float(src_w)
        crop_h = crop_w / aspect
    if crop_h > src_h:
        crop_h = float(src_h)
        crop_w = crop_h * aspect

    cx = (subject.left + subject.right) / 2.0
    left = cx - crop_w / 2.0
    top = region_cy - crop_h / 2.0
    right = left + crop_w
    bottom = top + crop_h

    if left < 0:
        right -= left; left = 0.0
    if top < 0:
        bottom -= top; top = 0.0
    if right > src_w:
        diff = right - src_w; left -= diff; right = float(src_w)
    if bottom > src_h:
        diff = bottom - src_h; top -= diff; bottom = float(src_h)

    left = max(0.0, left); top = max(0.0, top)
    right = min(float(src_w), right); bottom = min(float(src_h), bottom)

    return (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))


def centered_fallback_box(source_size: Tuple[int, int], output_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    """Largest centered crop with the target aspect ratio."""
    src_w, src_h = source_size
    out_w, out_h = output_size
    aspect = out_w / out_h
    if src_w / src_h > aspect:
        crop_h = src_h
        crop_w = int(round(crop_h * aspect))
    else:
        crop_w = src_w
        crop_h = int(round(crop_w / aspect))
    left = (src_w - crop_w) // 2
    top = (src_h - crop_h) // 2
    return (left, top, left + crop_w, top + crop_h)
