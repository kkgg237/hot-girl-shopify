"""Run the crop pipeline as a subprocess against its own Python 3.9.6 venv.

This is the ONLY place ecomm-pipeline touches the crop engine. We never import
``crop_pipeline`` (it drags rembg/onnxruntime + a ~170 MB model and is pinned to
3.9.6); instead we shell out to its CLI, which writes ``{SKU}_{slot}.jpg`` into a
staging folder. The contract between the two stages is files on disk, nothing
more.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ecomm_pipeline.config import Config

# Extensions the crop engine accepts (mirrors CropOptions.image_extensions).
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


class CropError(RuntimeError):
    """The crop subprocess failed (nonzero exit). Carries trimmed stderr."""


def normalized_input_name(name: str, sku_pattern: str) -> str:
    """Convert an underscore-separated export name to the crop engine's dash form.

    The crop engine splits SKU from shot on space/dash only (underscores are
    SKU-internal). Capture One here exports ``{SKU}_{shot}.jpg`` with an
    underscore, so we rewrite ``BRU_2605_001_1.jpg`` → ``BRU_2605_001-1.jpg``
    using the known SKU shape. Names that don't match (a bare-SKU safety frame
    ``BRU_2605_001.jpg``, or already space/dash ``BRU_2605_001 1.jpg``) pass
    through unchanged.

    >>> normalized_input_name("BRU_2605_001_1.jpg", r"[A-Za-z]+_\\d+_\\d+")
    'BRU_2605_001-1.jpg'
    >>> normalized_input_name("BRU_2605_001.jpg", r"[A-Za-z]+_\\d+_\\d+")
    'BRU_2605_001.jpg'
    """
    p = Path(name)
    m = re.fullmatch(rf"(?P<sku>{sku_pattern})_(?P<shot>\d+)", p.stem)
    if m:
        return f"{m.group('sku')}-{m.group('shot')}{p.suffix}"
    return name


def build_argv(cfg: Config, export_dir: Path, staging_dir: Path) -> list[str]:
    """Construct the crop CLI invocation. Factored out so it's unit-testable."""
    return [
        str(cfg.crop_venv_python),
        "-m",
        "crop_pipeline.cli",
        "--input",
        str(export_dir),
        "--output",
        str(staging_dir),
        "--template",
        cfg.template_name,
    ]


def _clear_staging(staging_dir: Path) -> None:
    """Remove prior ``*.jpg`` so grouping never sees stale crops from another run."""
    if staging_dir.exists():
        for jpg in staging_dir.glob("*.jpg"):
            jpg.unlink()
    staging_dir.mkdir(parents=True, exist_ok=True)


def _prepare_input(export_dir: Path, sku_pattern: str) -> tuple[Path, Optional[Path], int]:
    """Return an input dir the crop engine can parse, normalizing names if needed.

    If any export uses the underscore-shot convention, we build a temp dir of
    symlinks with dash-normalized names and return it (the caller must clean it
    up). If nothing needs converting, we return ``export_dir`` untouched. The
    third value is the count of files that were renamed (for logging).
    """
    files = [
        f
        for f in sorted(export_dir.iterdir())
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS and not f.name.startswith(".")
    ]
    renames = {f: normalized_input_name(f.name, sku_pattern) for f in files}
    changed = sum(1 for f, new in renames.items() if new != f.name)
    if changed == 0:
        return export_dir, None, 0

    tmp = Path(tempfile.mkdtemp(prefix="ecomm_crop_in_"))
    for f, new in renames.items():
        (tmp / new).symlink_to(f.resolve())
    return tmp, tmp, changed


def run_crops(
    cfg: Config,
    export_dir: Path,
    staging_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> str:
    """Crop every SKU in ``export_dir`` into ``staging_dir``; return crop stdout.

    Raises ConfigError (via validate) if the crop toolchain is missing, or
    CropError if the subprocess exits nonzero. The crop CLI must run with
    ``cwd`` = the crop repo so ``python -m crop_pipeline.cli`` resolves the
    package and ``load_template`` finds ``templates/``.
    """
    cfg.validate_crop_toolchain()
    if not export_dir.exists():
        raise CropError(f"export folder not found: {export_dir}")

    staging_dir = staging_dir or cfg.staging_dir
    _clear_staging(staging_dir)

    input_dir, tmp_dir, renamed = _prepare_input(export_dir, cfg.sku_pattern)
    if renamed and on_progress:
        on_progress(f"normalized {renamed} filename(s) underscore→dash for the crop parser")

    try:
        argv = build_argv(cfg, input_dir, staging_dir)
        proc = subprocess.run(
            argv,
            cwd=str(cfg.crop_repo_dir),
            capture_output=True,
            text=True,
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # The crop CLI logs progress to stderr (its ``_emit`` writes there).
    output = (proc.stdout or "") + (proc.stderr or "")
    if on_progress:
        for line in output.splitlines():
            if line.strip():
                on_progress(line.rstrip())

    if proc.returncode != 0:
        raise CropError(
            f"crop pipeline exited {proc.returncode}.\n{(proc.stderr or '')[-1500:]}"
        )
    return output
