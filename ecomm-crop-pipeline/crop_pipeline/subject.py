"""Subject detection.

We run rembg to get an alpha mask of the foreground subject, then derive the
tight bounding box from the mask. The rembg session is cached at module level
so batch runs reuse the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Tuple

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class SubjectBox:
    # Bounding box in source-image coordinates: (left, top, right, bottom).
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


@lru_cache(maxsize=4)
def _session(model_name: str):
    # Imported lazily so the module can be imported without rembg installed
    # (e.g. for `grouping` unit tests that don't touch image ops).
    from rembg import new_session

    return new_session(model_name)


def extract_alpha(image: Image.Image, model_name: str) -> Image.Image:
    """Return an RGBA Image where the alpha channel is the subject mask."""
    from rembg import remove

    session = _session(model_name)
    return remove(image.convert("RGB"), session=session)


def mask_to_box(mask: Image.Image, threshold: int = 32) -> SubjectBox | None:
    """Tight bbox of pixels above ``threshold`` in the alpha channel."""
    if mask.mode != "L":
        if mask.mode == "RGBA":
            mask = mask.split()[3]
        else:
            mask = mask.convert("L")
    arr = np.asarray(mask)
    ys, xs = np.where(arr > threshold)
    if xs.size == 0 or ys.size == 0:
        return None
    return SubjectBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
