"""Claude Vision pose-orientation backend.

Sends one image per call to the Anthropic messages API and asks the model
to classify the subject's orientation. Costs roughly $0.003 per frame at
current pricing (Sonnet tier). Use with caching at the call site so
re-runs are free.

Reads the ANTHROPIC_API_KEY from the environment (or a passed-in client).
"""

from __future__ import annotations

import base64
import io
import os
import re
from pathlib import Path
from typing import Optional

from PIL import Image

from crop_pipeline.pose import Pose, PoseResult


# Anthropic API caps base64-encoded image payloads at 10 MB. Studio source
# JPEGs are often 20-30 MB at full resolution. Downscale to this longest-edge
# pixel count before encoding — orientation classification doesn't need
# full-res pixels, and this is well below the cap with headroom for JPEG
# quality variance.
_MAX_EDGE = 1024
_JPEG_QUALITY = 85


_MODEL = "claude-opus-4-7"
_BACKEND = "claude"

_SYSTEM = (
    "You classify ecommerce studio photos by the model's body orientation "
    "relative to the camera. Reply with exactly one label and a confidence "
    "score (0.0–1.0). Format: LABEL CONFIDENCE — for example: 'FRONT 0.95'. "
    "Labels: FRONT (facing camera directly, full face visible), "
    "THREE_QUARTER (turned 25–65° from camera, face partially visible), "
    "SIDE (profile, perpendicular to camera, one side of face only), "
    "BACK (facing away from camera, back of head visible), "
    "UNKNOWN (cannot determine — extreme crop, occlusion, etc). "
    "No other text in your reply."
)

_USER = "Classify the model's orientation in this photo."

_PARSE_RE = re.compile(
    r"^\s*(?P<label>FRONT|THREE_QUARTER|SIDE|BACK|UNKNOWN)\s+"
    r"(?P<conf>0?\.\d+|1\.0+|1|0)\s*$",
    re.IGNORECASE,
)


def detect(image_path: Path, client: Optional[object] = None) -> PoseResult:
    """Classify one image. Returns ``PoseResult(pose, confidence, backend, notes)``."""
    # Lazy import so the rest of crop_pipeline doesn't pull anthropic on import.
    if client is None:
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        client = Anthropic(api_key=api_key)

    image_bytes = _downscaled_jpeg_bytes(image_path)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    media_type = "image/jpeg"

    resp = client.messages.create(
        model=_MODEL,
        max_tokens=32,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": _USER},
                ],
            }
        ],
    )

    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text").strip()
    m = _PARSE_RE.match(text)
    if not m:
        return PoseResult(pose=Pose.UNKNOWN, confidence=0.0, backend=_BACKEND,
                          notes=f"unparseable reply: {text!r}")
    label = m.group("label").upper()
    conf = float(m.group("conf"))
    return PoseResult(pose=Pose(label), confidence=conf, backend=_BACKEND, notes=text)


def _downscaled_jpeg_bytes(image_path: Path) -> bytes:
    """Read ``image_path`` and return JPEG bytes with the longest edge no
    larger than ``_MAX_EDGE`` pixels. Pose detection doesn't need full
    studio-shot resolution; this keeps payloads well below the 10 MB API cap."""
    with Image.open(image_path) as im:
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > _MAX_EDGE:
            scale = _MAX_EDGE / longest
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buf.getvalue()
