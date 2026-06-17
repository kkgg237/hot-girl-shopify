# ecomm-crop-pipeline

Turn Capture One studio exports into clean ecommerce listing crops.

You take photos in Capture One that are edited but uncropped. Filenames look
like `BRU_2605_001-1.jpg` (SKU + shot index) or `BRU_2605_002 3.jpg`. This
pipeline:

1. Groups source images by SKU.
2. Detects the model in each shot (rembg / U-2-Net).
3. Crops to a target aspect ratio centered on the model.
4. Removes the background and composites onto solid white.
5. Resizes to a consistent output size.
6. Writes JPEGs with a clean `{SKU}_{shot}.jpg` naming pattern.

Designed to be importable as a library so other tools in this repo (e.g. a
listing-builder UI) can reuse the cropping logic without shelling out.

## Setup

```sh
./setup.sh                              # creates .venv and installs deps
source .venv/bin/activate
```

The first run downloads the rembg model (~170 MB) and caches it under
`~/.u2net/`.

## Standard listing template

For batch work (hundreds of items), use the template:

```sh
python -m crop_pipeline.cli \
  --input  ../test-content/before \
  --output ../test-content/after-test \
  --template listing-standard
```

This produces a fixed 4-shot set per SKU:

| Slot | Source shot | Framing |
|---|---|---|
| `01_hero` | shot 1 (front, eyes open) | full body, centered |
| `02_three_quarter` | shot 4 (3/4 turn) | full body, centered |
| `03_back` | shot 7 (back facing) | full body, centered |
| `04_detail` | shot 1 (front) | chest/torso zoom showing fabric |

Pure side profile (formerly `03_side`) was dropped because the source shoot
turns the same direction across shots 3/4/5 — having both side and 3/4 reads
redundant. See `RULES.md`.

Output names are `{SKU}_{slot}.jpg` so Shopify uploads in the correct order.

The template assumes a fixed shoot order (defined at the top of
`templates/listing-standard.yaml`). To change the shot list, edit that file
or pass `--template path/to/your.yaml`.

## Free-form CLI

```sh
python -m crop_pipeline.cli \
  --input  ../test-content/before \
  --output ../test-content/after-test
```

Without `--template`, the pipeline crops every input 1:1 — useful when you
want every shot, not just the listing slots.

Useful flags:

| Flag | Default | Notes |
|---|---|---|
| `--output-size WxH` | `1536x2048` | Final canvas (matches the reference shots) |
| `--subject-height` | `0.90` | Fraction of output height the model fills |
| `--vertical-bias`  | `0.0`  | Shift framing up (negative) or down (positive) |
| `--background-color R,G,B` | `255,255,255` | Composite color |
| `--rembg-model` | `isnet-general-use` | also `u2net`, `u2netp`, `birefnet-general` |
| `--no-background-removal` | off | Skip rembg, just crop |

## Library

```python
from pathlib import Path
from crop_pipeline import (
    CropOptions, process_folder, process_image,
    load_template, process_with_template,
)

# 5-shot standard listing for every SKU in input/
process_with_template(
    Path("input/"),
    Path("output/"),
    load_template("listing-standard"),
)

# Or batch every input 1:1
process_folder(Path("input/"), Path("output/"), CropOptions())

# Or one image at a time (good for hooking into another tool)
process_image(Path("input/BRU_2605_002 1.jpg"), Path("out/hero.jpg"), CropOptions())
```

## Tests

```sh
python -m pytest tests/
```

The `tests/` suite covers filename parsing and crop math without needing
rembg or any model files.
