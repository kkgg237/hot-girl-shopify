"""Body-orientation classification for studio photos.

Given a photo of a model, classify which way they're facing the camera:
FRONT, THREE_QUARTER, SIDE, or BACK. Used by a future pose-aware template
to pick the right source frame for each slot regardless of filename
ordering.

Two backends share one interface here so they can be compared:
- ``pose_claude.detect`` — Claude Vision API (high accuracy, costs $$, network)
- ``pose_mediapipe.detect`` — MediaPipe Pose landmarks + heuristic (local, free)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class Pose(str, Enum):
    FRONT = "FRONT"
    THREE_QUARTER = "THREE_QUARTER"
    SIDE = "SIDE"
    BACK = "BACK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PoseResult:
    pose: Pose
    confidence: float  # 0.0–1.0; backend-specific semantics but always normalized
    backend: str
    notes: str = ""


# Type alias for any backend's detect function. Keeps the comparison harness
# (and a future template engine) backend-agnostic.
PoseDetector = Callable[[Path], PoseResult]
