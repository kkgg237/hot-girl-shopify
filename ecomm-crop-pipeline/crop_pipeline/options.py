"""Crop pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class CropOptions:
    # Output canvas in pixels (width, height). Default is the reference 3:4 size.
    output_size: Tuple[int, int] = (1536, 2048)

    # Fraction of the output height the subject should fill (head-to-toe).
    # 0.82 is calibrated from the reference shots (which average ~83% across
    # front/side/back poses).
    subject_height_fraction: float = 0.82

    # Shift framing vertically as a fraction of crop height. Negative values
    # leave more headroom (subject sits lower in the frame); positive values
    # tuck the subject higher. -0.04 matches the reference's slight downward
    # bias.
    vertical_bias: float = -0.04

    # If True, replace the cropped pixels with the rembg foreground composited
    # onto ``background_color``. Subject detection (used for framing) runs
    # regardless of this setting.
    remove_background: bool = False

    # Background color (RGB) used when remove_background is True.
    background_color: Tuple[int, int, int] = (255, 255, 255)

    # rembg model name. "u2net" is the default general-purpose model;
    # "u2netp" is smaller/faster; "isnet-general-use" tends to give cleaner
    # edges on people.
    rembg_model: str = "isnet-general-use"

    # JPEG quality for outputs.
    jpeg_quality: int = 92

    # If False (default) and the crop region is smaller than ``output_size``,
    # the output is written at the crop's own dimensions instead of being
    # upsampled. Every output pixel comes from real source pixels — the
    # tradeoff is that output dimensions vary per image when the source is
    # smaller than the target.
    allow_upscale: bool = False

    # If the auto-detected subject box ends up implausible (e.g. <5% of frame
    # height) we fall back to a centered crop. This is the minimum acceptable
    # subject-to-frame height ratio before falling back.
    min_subject_fraction: float = 0.05

    # File extensions treated as input images.
    image_extensions: Tuple[str, ...] = field(default_factory=lambda: (".jpg", ".jpeg", ".png", ".tif", ".tiff"))

    # Soft drop-shadow under the subject's feet, synthesized from the alpha
    # mask. Only applied when ``remove_background`` is True (otherwise the
    # original cyc shadow is already present in the source pixels).
    #
    # ``shadow_opacity`` of 0.0 disables the shadow entirely (default). 0.18
    # matches the reference shoot's subtle anchor shadow without competing
    # with the subject.
    shadow_opacity: float = 0.0
    # Gaussian blur radius in pixels — softens the shadow edge. Reads well at
    # the default 1536x2048 canvas; should scale with output size if you push
    # higher resolutions.
    shadow_blur: float = 36.0
    # Vertical squash factor as a fraction of the subject's bounding-box
    # height. 0.04 = a thin band right at the feet line; the heavy blur
    # diffuses it into a soft anchor shadow rather than a defined silhouette.
    shadow_squash: float = 0.04
    # Vertical offset of the shadow's center relative to the feet, as a
    # fraction of the squashed shadow height. 0.0 centers the squashed band on
    # the feet line; positive values push it further below.
    shadow_offset: float = 0.0
