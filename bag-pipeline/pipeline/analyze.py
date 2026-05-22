"""Stage 2: send a hero image to Claude vision and return a drafted listing."""

from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image
from anthropic import Anthropic

from pipeline.prompts import SYSTEM_PROMPT, USER_TEXT
from pipeline.schema import BagListing


MAX_LONG_EDGE_PX = 1568
DEFAULT_MODEL = "claude-opus-4-7"
JPEG_QUALITY = 88


def _preprocess(image_path: Path, max_long_edge: int = MAX_LONG_EDGE_PX) -> bytes:
    """Resize so the longest edge is <= max_long_edge, return jpeg bytes."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    """Pull the first JSON object out of the model's text response.

    The prompt asks for raw JSON, but the model occasionally wraps it in
    a ```json fence or adds a leading sentence. Strip those.
    """
    stripped = _JSON_FENCE_RE.sub("", text).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in model output: {text!r}")
    return stripped[start : end + 1]


def analyze_hero(
    image_path: Path,
    client: Anthropic | None = None,
    model: str | None = None,
) -> BagListing:
    """Send the hero image to Claude and return the parsed listing."""
    client = client or Anthropic()
    model = model or os.environ.get("BAG_PIPELINE_MODEL", DEFAULT_MODEL)

    jpeg_bytes = _preprocess(Path(image_path))
    b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")

    schema_json = json.dumps(BagListing.model_json_schema(), indent=2)
    system_with_schema = (
        SYSTEM_PROMPT
        + "\n\n# Output schema\n\nRespond with a single JSON object matching this schema:\n\n"
        + schema_json
    )

    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system_with_schema,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": USER_TEXT},
                ],
            }
        ],
    )

    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text

    raw_json = _extract_json(text)
    return BagListing.model_validate_json(raw_json)
