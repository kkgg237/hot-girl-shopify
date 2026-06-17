"""The crop-runner argv contract — pins the exact subprocess invocation.

If this drifts (wrong interpreter, missing -m, wrong flags) the crop step breaks
silently, so it's worth a fast unit test that needs no subprocess.
"""

from pathlib import Path

from ecomm_pipeline.config import Config
from ecomm_pipeline.crop_runner import build_argv, normalized_input_name

SKU_PATTERN = r"[A-Za-z]+_\d+_\d+"


def test_underscore_shot_is_converted_to_dash():
    # The store's underscore convention → the crop engine's dash convention.
    assert normalized_input_name("BRU_2605_001_1.jpg", SKU_PATTERN) == "BRU_2605_001-1.jpg"
    assert normalized_input_name("BRU_2605_001_12.jpg", SKU_PATTERN) == "BRU_2605_001-12.jpg"
    assert normalized_input_name("ISS_2605_02_3.jpg", SKU_PATTERN) == "ISS_2605_02-3.jpg"


def test_bare_sku_safety_frame_is_left_alone():
    # No shot suffix → the unnumbered safety frame (crop reads it as shot 0).
    assert normalized_input_name("BRU_2605_001.jpg", SKU_PATTERN) == "BRU_2605_001.jpg"


def test_existing_space_or_dash_passes_through():
    assert normalized_input_name("BRU_2605_010 1.jpg", SKU_PATTERN) == "BRU_2605_010 1.jpg"
    assert normalized_input_name("BRU_2605_010-1.jpg", SKU_PATTERN) == "BRU_2605_010-1.jpg"
    assert normalized_input_name("random.jpg", SKU_PATTERN) == "random.jpg"


def _cfg() -> Config:
    return Config(shop="example.myshopify.com")


def test_build_argv_uses_crop_venv_and_template():
    cfg = _cfg()
    argv = build_argv(cfg, Path("/exports/may"), Path("/stage"))

    # Must invoke the crop pipeline's OWN venv interpreter, not ours.
    assert argv[0] == str(cfg.crop_venv_python)
    assert argv[0].endswith("ecomm-crop-pipeline/.venv/bin/python")
    # Module form so cwd=crop_repo resolves the package.
    assert argv[1:3] == ["-m", "crop_pipeline.cli"]
    # Input/output/template flags wired through.
    assert "--input" in argv and "/exports/may" in argv
    assert "--output" in argv and "/stage" in argv
    assert argv[argv.index("--template") + 1] == cfg.template_name
