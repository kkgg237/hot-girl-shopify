"""CLI entry point: ``python -m crop_pipeline.cli``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from crop_pipeline.options import CropOptions
from crop_pipeline.pipeline import process_folder, process_image, process_with_template, _emit
from crop_pipeline.templates import load_template


def _parse_size(value: str) -> tuple[int, int]:
    if "x" not in value.lower():
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT, e.g. 1536x2048")
    w_str, h_str = value.lower().split("x", 1)
    return int(w_str), int(h_str)


def _parse_color(value: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected R,G,B (each 0-255)")
    r, g, b = (int(p) for p in parts)
    return r, g, b


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crop-pipeline",
        description="Crop Capture One studio exports into clean ecomm listing images.",
    )
    p.add_argument("--input", required=True, help="File or folder of source images")
    p.add_argument("--output", required=True, help="File or folder for cropped output")
    p.add_argument("--template", help="Template name (e.g. 'listing-standard') or path to a YAML file. "
                                       "When set, produces the fixed shot list per SKU instead of 1:1 crops.")
    p.add_argument("--output-size", type=_parse_size, default=(1536, 2048),
                   help="Output dimensions, e.g. 1536x2048 (default)")
    p.add_argument("--subject-height", type=float, default=0.82,
                   help="Fraction of output height the subject should fill (default 0.82, calibrated from reference)")
    p.add_argument("--vertical-bias", type=float, default=-0.04,
                   help="Shift framing as a fraction of crop height. Negative = subject sits lower in frame (default -0.04)")
    p.add_argument("--remove-background", action="store_true",
                   help="Composite the subject onto a solid color. Off by default; framing matches with or without this flag.")
    p.add_argument("--background-color", type=_parse_color, default=(255, 255, 255),
                   help="Background RGB for --remove-background, e.g. 255,255,255 (default white)")
    p.add_argument("--drop-shadow", action="store_true",
                   help="Synthesize a soft cast shadow under the subject's feet (requires --remove-background).")
    p.add_argument("--shadow-opacity", type=float, default=0.18,
                   help="Drop-shadow opacity 0.0–1.0 when --drop-shadow is set (default 0.18).")
    p.add_argument("--match-reference", action="store_true",
                   help="Shortcut: enables --remove-background, sets --background-color to the warm cyc tone (248,242,242), "
                        "and --drop-shadow with default opacity. Matches the reference shoot's editing.")
    p.add_argument("--rembg-model", default="isnet-general-use",
                   help="rembg model name (u2net, u2netp, isnet-general-use, ...)")
    p.add_argument("--jpeg-quality", type=int, default=92)
    p.add_argument("--allow-upscale", action="store_true",
                   help="Allow upsampling when the source crop is smaller than --output-size. "
                        "Default: emit at the crop's native dimensions so every pixel is real.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --match-reference is a convenience preset; explicit flags still win when
    # the user passes both (argparse already resolved them above).
    remove_bg = args.remove_background or args.match_reference
    bg_color = tuple(args.background_color)
    if args.match_reference and args.background_color == (255, 255, 255):
        bg_color = (248, 242, 242)  # warm off-white sampled from the reference cyc
    shadow_opacity = args.shadow_opacity if (args.drop_shadow or args.match_reference) else 0.0

    opts = CropOptions(
        output_size=tuple(args.output_size),
        subject_height_fraction=args.subject_height,
        vertical_bias=args.vertical_bias,
        remove_background=remove_bg,
        background_color=bg_color,
        rembg_model=args.rembg_model,
        jpeg_quality=args.jpeg_quality,
        allow_upscale=args.allow_upscale,
        shadow_opacity=shadow_opacity,
    )

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        print(f"error: input not found: {in_path}", file=sys.stderr)
        return 2

    if args.template:
        if not in_path.is_dir():
            print("error: --template requires --input to be a folder of SKU-grouped images", file=sys.stderr)
            return 2
        template = load_template(args.template)
        _emit(f"template: {template.name} ({len(template.shots)} shots per SKU)")
        results = process_with_template(in_path, out_path, template, opts, on_progress=_emit)
        ok = [r for r in results if r.skipped_reason is None]
        skipped = [r for r in results if r.skipped_reason]
        _emit(f"done: {len(ok)} image(s) written, {len(skipped)} slot(s) skipped")
        for r in skipped:
            _emit(f"  skipped {r.sku}/{r.slot}: {r.skipped_reason}")
        return 0

    if in_path.is_file():
        if out_path.is_dir() or args.output.endswith("/"):
            out_path = out_path / f"{in_path.stem}.jpg"
        _emit(f"{in_path.name} -> {out_path}")
        result = process_image(in_path, out_path, opts)
        if result.used_fallback:
            _emit(f"  warning: fell back to centered crop ({in_path.name})")
        return 0

    results = process_folder(in_path, out_path, opts, on_progress=_emit)
    fallbacks = sum(1 for r in results if r.used_fallback)
    _emit(f"done: {len(results)} image(s), {fallbacks} centered-crop fallback(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
