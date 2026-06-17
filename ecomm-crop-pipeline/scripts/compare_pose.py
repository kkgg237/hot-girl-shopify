"""Side-by-side comparison of the Claude and MediaPipe pose backends.

Usage:
    source .venv/bin/activate
    python scripts/compare_pose.py ../test-content/before/

Outputs:
- stdout: per-frame table + summary
- ``out/pose_compare.json``: raw verdicts (used as the re-run cache)
- ``out/pose_compare.html``: visual report with image thumbnails

Caches both backends' results in ``out/pose_compare.json`` keyed by
``(filename, mtime, size)`` so re-runs don't hit the Claude API again
unless source files change.

Ground truth is positional (canonical Capture One shoot order). Only SKUs
known to follow that order get scored vs ground truth — BRU_2605_010 and
ISS_2605_011 after normalization.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional


# --- env loading -----------------------------------------------------------

def _load_env_files() -> None:
    here = Path(__file__).resolve().parent.parent.parent  # repo root
    for candidate in (here / ".env", here / "bag-pipeline" / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not os.environ.get(k):
                os.environ[k] = v


_load_env_files()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from crop_pipeline.grouping import group_by_sku  # noqa: E402
from crop_pipeline.pose import Pose, PoseResult  # noqa: E402
from crop_pipeline import pose_claude, pose_mediapipe  # noqa: E402


_POSITIONAL_GROUND_TRUTH = {
    0: Pose.FRONT,
    1: Pose.FRONT,
    2: Pose.FRONT,
    3: Pose.SIDE,
    4: Pose.THREE_QUARTER,
    5: Pose.THREE_QUARTER,
    6: Pose.BACK,
    7: Pose.BACK,
    8: Pose.BACK,
}
_GROUND_TRUTH_SKUS = {"BRU_2605_010", "ISS_2605_011"}


def _gt(sku: str, shot: int) -> Optional[Pose]:
    if sku not in _GROUND_TRUTH_SKUS:
        return None
    return _POSITIONAL_GROUND_TRUTH.get(shot)


# --- cache layer -----------------------------------------------------------

_OUT_DIR = Path(__file__).resolve().parent.parent / "out"
_CACHE_PATH = _OUT_DIR / "pose_compare.json"
_HTML_PATH = _OUT_DIR / "pose_compare.html"


def _cache_key(path: Path) -> str:
    st = path.stat()
    return f"{path.name}|{int(st.st_mtime)}|{st.st_size}"


def _load_cache() -> dict:
    if not _CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _cached_or_run(cache: dict, backend: str, path: Path, run) -> PoseResult:
    key = _cache_key(path)
    bucket = cache.setdefault(backend, {})
    if key in bucket:
        d = bucket[key]
        return PoseResult(pose=Pose(d["pose"]), confidence=d["confidence"],
                          backend=backend, notes=d.get("notes", ""))
    result = run(path)
    bucket[key] = asdict(result) | {"pose": result.pose.value}
    return result


# --- thumbnails for the HTML report ---------------------------------------

def _thumb_data_uri(path: Path, max_edge: int = 320) -> str:
    """Return a base64 data URI for a small thumbnail of ``path``."""
    with Image.open(path) as im:
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / longest
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=75, optimize=True)
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


# --- main flow -------------------------------------------------------------

def _fmt(r: PoseResult) -> str:
    return f"{r.pose.value:14s} ({r.confidence:.2f})"


def main(input_dir: Path) -> None:
    groups = group_by_sku(input_dir, (".jpg", ".jpeg", ".png"))
    if not groups:
        print(f"no images found in {input_dir}", file=sys.stderr)
        return

    cache = _load_cache()

    mp_detector = pose_mediapipe.make_detector()
    from anthropic import Anthropic
    claude = Anthropic()

    print(f"{'file':40s} {'gt':14s} {'claude':22s} {'mediapipe':22s} {'agree':6s}")
    print("-" * 110)

    rows = []
    cl_total = mp_total = 0.0
    cl_calls = mp_calls = 0  # only counts non-cached calls

    for sku, items in groups.items():
        for parsed in items:
            gt = _gt(sku, parsed.shot)

            t0 = time.perf_counter()
            was_cached_cl = _cache_key(parsed.source) in cache.get("claude", {})
            cl = _cached_or_run(cache, "claude", parsed.source,
                                lambda p: pose_claude.detect(p, client=claude))
            if not was_cached_cl:
                cl_total += time.perf_counter() - t0
                cl_calls += 1

            t0 = time.perf_counter()
            was_cached_mp = _cache_key(parsed.source) in cache.get("mediapipe", {})
            mpr = _cached_or_run(cache, "mediapipe", parsed.source,
                                 lambda p: pose_mediapipe.detect(p, detector=mp_detector))
            if not was_cached_mp:
                mp_total += time.perf_counter() - t0
                mp_calls += 1

            agree = "yes" if cl.pose == mpr.pose else "no"
            gt_s = gt.value if gt else "-"
            print(
                f"{parsed.source.name:40s} {gt_s:14s} "
                f"{_fmt(cl):22s} {_fmt(mpr):22s} {agree:6s}"
            )
            rows.append({"sku": sku, "shot": parsed.shot, "source": parsed.source,
                         "gt": gt, "claude": cl, "mediapipe": mpr})

    _save_cache(cache)

    print()
    _print_summary(rows, cl_total, mp_total, cl_calls, mp_calls)
    _write_html(rows, _HTML_PATH)
    print(f"\nReport: {_HTML_PATH}")
    print(f"Cache : {_CACHE_PATH}  (delete to force re-run)")


def _print_summary(rows, cl_total, mp_total, cl_calls, mp_calls) -> None:
    n = len(rows)
    agree = sum(1 for r in rows if r["claude"].pose == r["mediapipe"].pose)
    with_gt = [(r["claude"], r["mediapipe"], r["gt"]) for r in rows if r["gt"] is not None]
    cl_correct = sum(1 for cl, _, gt in with_gt if cl.pose == gt)
    mp_correct = sum(1 for _, mpr, gt in with_gt if mpr.pose == gt)

    print(f"AGREEMENT          : {agree}/{n} ({agree/n:.0%})")
    if with_gt:
        print(f"CLAUDE vs ground   : {cl_correct}/{len(with_gt)} ({cl_correct/len(with_gt):.0%})")
        print(f"MEDIAPIPE vs ground: {mp_correct}/{len(with_gt)} ({mp_correct/len(with_gt):.0%})")
    if cl_calls:
        print(f"CLAUDE wall-clock  : {cl_total:.1f}s over {cl_calls} fresh calls  ({cl_total/cl_calls*1000:.0f} ms/frame)")
    else:
        print("CLAUDE wall-clock  : all cached")
    if mp_calls:
        print(f"MP wall-clock      : {mp_total:.1f}s over {mp_calls} fresh calls  ({mp_total/mp_calls*1000:.0f} ms/frame)")
    else:
        print("MP wall-clock      : all cached")


# --- HTML report -----------------------------------------------------------

_HTML_STYLE = """
body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }
h1 { margin-bottom: 0.2em; }
.summary { background: #f3f3ef; padding: 1em 1.2em; border-radius: 6px; margin-bottom: 1.5em; }
.summary code { background: #e6e6e0; padding: 0.05em 0.4em; border-radius: 3px; }
table { border-collapse: collapse; width: 100%; }
td, th { padding: 0.6em 0.8em; vertical-align: top; border-bottom: 1px solid #e5e5e1; text-align: left; }
th { background: #fafaf7; font-weight: 600; font-size: 0.9em; }
img { display: block; width: 160px; height: auto; border-radius: 4px; }
.pose { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.85em; }
.gt    { color: #555; }
.right { color: #2a7a2a; }
.wrong { color: #b03030; }
.disagree { background: #fffaf0; }
.filename { font-family: ui-monospace, monospace; font-size: 0.8em; color: #666; }
.conf { color: #888; font-size: 0.85em; }
.notes { color: #888; font-size: 0.75em; font-family: ui-monospace, monospace; max-width: 32em; }
"""


def _verdict_html(verdict: PoseResult, gt) -> str:
    cls = ""
    if gt is not None:
        cls = "right" if verdict.pose == gt else "wrong"
    return (
        f'<div class="pose {cls}">{verdict.pose.value} '
        f'<span class="conf">({verdict.confidence:.2f})</span></div>'
        f'<div class="notes">{verdict.notes}</div>'
    )


def _write_html(rows, out_path: Path) -> None:
    n = len(rows)
    agree = sum(1 for r in rows if r["claude"].pose == r["mediapipe"].pose)
    with_gt = [r for r in rows if r["gt"] is not None]
    cl_correct = sum(1 for r in with_gt if r["claude"].pose == r["gt"])
    mp_correct = sum(1 for r in with_gt if r["mediapipe"].pose == r["gt"])

    parts = ["<!doctype html><html><head><meta charset='utf-8'>"]
    parts.append("<title>Pose backend comparison</title>")
    parts.append(f"<style>{_HTML_STYLE}</style></head><body>")
    parts.append("<h1>Pose backend comparison</h1>")
    parts.append("<div class='summary'>")
    parts.append(f"<div><b>Backends compared:</b> <code>claude (opus)</code> vs <code>mediapipe (full)</code></div>")
    parts.append(f"<div><b>Frames:</b> {n}</div>")
    parts.append(f"<div><b>Agreement:</b> {agree}/{n} ({agree/n:.0%})</div>")
    if with_gt:
        parts.append(f"<div><b>Claude vs ground truth:</b> {cl_correct}/{len(with_gt)} ({cl_correct/len(with_gt):.0%})</div>")
        parts.append(f"<div><b>MediaPipe vs ground truth:</b> {mp_correct}/{len(with_gt)} ({mp_correct/len(with_gt):.0%})</div>")
    parts.append("<div style='margin-top:0.6em; color:#666; font-size:0.9em'>")
    parts.append("Rows highlighted in yellow are disagreements between the two backends. ")
    parts.append("Green = matches ground truth; red = disagrees with ground truth. ")
    parts.append("Ground truth is positional only — derived from canonical Capture One shoot order ")
    parts.append("(may itself be wrong for slots 3/4/5 where the shoot doesn't actually capture distinct angles).")
    parts.append("</div></div>")

    parts.append("<table>")
    parts.append("<tr><th>image</th><th>file</th><th>ground truth</th>"
                 "<th>claude</th><th>mediapipe</th></tr>")
    for r in rows:
        disagree_cls = "disagree" if r["claude"].pose != r["mediapipe"].pose else ""
        thumb = _thumb_data_uri(r["source"])
        gt_html = f"<span class='pose gt'>{r['gt'].value if r['gt'] else '—'}</span>"
        parts.append(f"<tr class='{disagree_cls}'>")
        parts.append(f"<td><img src='{thumb}' alt='thumbnail'></td>")
        parts.append(f"<td><div class='filename'>{r['source'].name}</div>"
                     f"<div class='filename'>{r['sku']} shot {r['shot']}</div></td>")
        parts.append(f"<td>{gt_html}</td>")
        parts.append(f"<td>{_verdict_html(r['claude'], r['gt'])}</td>")
        parts.append(f"<td>{_verdict_html(r['mediapipe'], r['gt'])}</td>")
        parts.append("</tr>")
    parts.append("</table></body></html>")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(parts))


if __name__ == "__main__":
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../test-content/before")
    main(in_path)
