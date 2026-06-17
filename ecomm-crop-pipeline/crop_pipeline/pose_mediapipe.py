"""MediaPipe Pose backend (Tasks API).

Runs Google's MediaPipe Pose Landmarker to extract body landmarks, then
classifies orientation from face-landmark visibility + shoulder geometry.
Local, free, no network — needs the ``pose_landmarker_full.task`` model
file (~9 MB) under ``.cache/mediapipe/`` (downloaded at setup time).

Classification logic (see ``_classify`` for the full rules):
- BACK: face landmarks (eyes, mouth) are all low-visibility → seeing back
- FRONT: both eyes high-visibility AND nose centered between shoulders
- SIDE: one ear visible / one not, OR shoulders very close in x
- THREE_QUARTER: face visible with strong asymmetry, but not pure profile
- UNKNOWN: no person detected, or unclassifiable
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from crop_pipeline.pose import Pose, PoseResult


_BACKEND = "mediapipe"

# Path to the downloaded model file. Resolved relative to the package root
# so it works regardless of cwd when called from scripts/.
_PKG_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL = _PKG_ROOT / ".cache" / "mediapipe" / "pose_landmarker_full.task"


def make_detector(model_path: Optional[Path] = None):
    """Build a PoseLandmarker. Returns an object you can pass to ``detect``
    so the model isn't reloaded per-frame."""
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    mpath = model_path or _DEFAULT_MODEL
    if not mpath.is_file():
        raise FileNotFoundError(
            f"MediaPipe model not found at {mpath}. "
            f"Download with: curl -sSL -o {mpath} https://storage.googleapis.com/"
            f"mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/"
            f"pose_landmarker_full.task"
        )

    base_options = mp_python.BaseOptions(model_asset_path=str(mpath))
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    return vision.PoseLandmarker.create_from_options(options)


# MediaPipe pose landmark indices (33 landmarks).
_NOSE = 0
_LEFT_EYE_INNER = 1
_LEFT_EYE = 2
_LEFT_EYE_OUTER = 3
_RIGHT_EYE_INNER = 4
_RIGHT_EYE = 5
_RIGHT_EYE_OUTER = 6
_LEFT_EAR = 7
_RIGHT_EAR = 8
_MOUTH_LEFT = 9
_MOUTH_RIGHT = 10
_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12


def detect(image_path: Path, detector: Optional[object] = None) -> PoseResult:
    """Classify one image. Returns ``PoseResult(pose, confidence, backend, notes)``."""
    import mediapipe as mp

    if detector is None:
        detector = make_detector()

    mp_image = mp.Image.create_from_file(str(image_path))
    result = detector.detect(mp_image)

    if not result.pose_landmarks:
        return PoseResult(pose=Pose.UNKNOWN, confidence=0.0, backend=_BACKEND,
                          notes="no person detected")

    landmarks = result.pose_landmarks[0]
    pose_label, conf, notes = _classify_from_landmarks(landmarks)
    return PoseResult(pose=pose_label, confidence=conf, backend=_BACKEND, notes=notes)


def _classify_from_landmarks(landmarks) -> Tuple[Pose, float, str]:
    """Apply heuristic rules to MediaPipe landmark visibility + geometry."""
    def vis(i: int) -> float:
        return float(landmarks[i].visibility)

    def x(i: int) -> float:
        return float(landmarks[i].x)

    eye_l, eye_r = vis(_LEFT_EYE), vis(_RIGHT_EYE)
    ear_l, ear_r = vis(_LEFT_EAR), vis(_RIGHT_EAR)
    mouth_avg = (vis(_MOUTH_LEFT) + vis(_MOUTH_RIGHT)) / 2
    eye_avg = (eye_l + eye_r) / 2

    sx_l, sx_r = x(_LEFT_SHOULDER), x(_RIGHT_SHOULDER)
    shoulder_dx = abs(sx_l - sx_r)
    nose_x = x(_NOSE)
    nose_offset = abs(nose_x - (sx_l + sx_r) / 2) / max(shoulder_dx, 0.01)

    # 1. BACK — face landmarks heavily occluded (back of head), but
    # shoulders are still there.
    if mouth_avg < 0.3 and eye_avg < 0.4:
        return Pose.BACK, max(0.0, 1.0 - mouth_avg), (
            f"back: mouth_vis={mouth_avg:.2f} eye_vis={eye_avg:.2f}"
        )

    # 2. SIDE — narrow shoulder span (one shoulder behind the other from
    # camera POV) OR strong ear asymmetry.
    if shoulder_dx < 0.05 or (max(ear_l, ear_r) > 0.7 and min(ear_l, ear_r) < 0.2):
        conf = min(0.95, 1.0 - shoulder_dx * 5)
        return Pose.SIDE, conf, (
            f"side: shoulder_dx={shoulder_dx:.3f} ears=({ear_l:.2f},{ear_r:.2f})"
        )

    # 3. FRONT — both eyes clearly visible AND nose between shoulders.
    if eye_l > 0.7 and eye_r > 0.7 and nose_offset < 0.25:
        conf = min(0.95, (eye_l + eye_r) / 2)
        return Pose.FRONT, conf, (
            f"front: eye_vis=({eye_l:.2f},{eye_r:.2f}) nose_offset={nose_offset:.2f}"
        )

    # 4. THREE_QUARTER — partial face / asymmetric eyes / off-center nose,
    # but not yet pure side.
    eye_asym = abs(eye_l - eye_r)
    if eye_avg > 0.4 or eye_asym > 0.2 or nose_offset > 0.25:
        conf = min(0.9, 0.6 + eye_asym + nose_offset / 2)
        return Pose.THREE_QUARTER, conf, (
            f"three_quarter: eye_asym={eye_asym:.2f} nose_offset={nose_offset:.2f}"
        )

    return Pose.UNKNOWN, 0.0, (
        f"unclassified: eye_avg={eye_avg:.2f} mouth_avg={mouth_avg:.2f} "
        f"shoulder_dx={shoulder_dx:.3f}"
    )
