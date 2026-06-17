"""High-level pipeline: a folder of Capture One exports → cleaned ecomm crops."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from PIL import Image

from crop_pipeline.compose import composite_on_color, composite_on_color_with_shadow, crop_and_resize
from crop_pipeline.crop import centered_fallback_box, compute_crop_box, compute_region_crop_box
from crop_pipeline.grouping import ParsedName, group_by_sku
from crop_pipeline.options import CropOptions
from crop_pipeline.subject import SubjectBox, extract_alpha, mask_to_box
from crop_pipeline.templates import ShotSpec, Template


@dataclass
class ImageResult:
    source: Path
    output: Path
    subject_box: Optional[SubjectBox]
    crop_box: tuple[int, int, int, int]
    used_fallback: bool


def process_image(
    source: Path,
    output: Path,
    opts: CropOptions | None = None,
) -> ImageResult:
    """Crop, background-replace, and save one image.

    Returns an ImageResult describing what happened. The output is written to
    ``output`` as JPEG.
    """
    opts = opts or CropOptions()

    image = Image.open(source)
    image.load()
    src_size = image.size  # (W, H)

    # Always run subject detection so framing works the same with or without
    # background removal. The mask is only applied to the output pixels when
    # opts.remove_background is True.
    rgba = extract_alpha(image, opts.rembg_model)
    subject_box: Optional[SubjectBox] = mask_to_box(rgba)
    used_fallback = False

    # Sanity-check the detected subject. If implausible, fall back to a
    # centered crop on the original image.
    if subject_box is not None:
        fraction = subject_box.height / max(src_size[1], 1)
        if fraction < opts.min_subject_fraction:
            subject_box = None

    if subject_box is None:
        used_fallback = True
        crop_box = centered_fallback_box(src_size, opts.output_size)
    else:
        crop_box = compute_crop_box(
            source_size=src_size,
            subject=subject_box,
            output_size=opts.output_size,
            subject_height_fraction=opts.subject_height_fraction,
            vertical_bias=opts.vertical_bias,
        )

    composed = _composed(image, rgba, opts)

    final = crop_and_resize(composed, crop_box, opts.output_size, allow_upscale=opts.allow_upscale)

    output.parent.mkdir(parents=True, exist_ok=True)
    final.save(output, format="JPEG", quality=opts.jpeg_quality, optimize=True)

    return ImageResult(
        source=source,
        output=output,
        subject_box=subject_box,
        crop_box=crop_box,
        used_fallback=used_fallback,
    )


def process_folder(
    input_dir: Path,
    output_dir: Path,
    opts: CropOptions | None = None,
    on_progress: Callable[[str], None] | None = None,
    output_name: Callable[[ParsedName], str] | None = None,
) -> List[ImageResult]:
    """Process all images in ``input_dir`` and write outputs to ``output_dir``.

    Files are grouped by SKU and processed in shot order. Output filenames
    default to ``{SKU}_{shot}.jpg``; pass ``output_name`` to override.
    """
    opts = opts or CropOptions()

    def _warn_dupe(sku: str, shot: int, kept: Path, dropped: Path) -> None:
        if on_progress:
            on_progress(f"  warning: {sku} shot {shot} has duplicate {dropped.name}; using {kept.name}")

    def _warn_norm(sku: str, mapping: Dict[int, int]) -> None:
        if on_progress:
            pairs = ", ".join(f"{k}->{v}" for k, v in mapping.items())
            on_progress(f"  normalized {sku} shot indices (counter offset): {pairs}")

    groups = group_by_sku(input_dir, opts.image_extensions, on_duplicate=_warn_dupe, on_normalize=_warn_norm)
    if not groups:
        return []

    def default_name(parsed: ParsedName) -> str:
        return f"{parsed.sku}_{parsed.shot}.jpg"

    name_fn = output_name or default_name
    results: List[ImageResult] = []
    for sku, items in groups.items():
        for parsed in items:
            out_path = output_dir / name_fn(parsed)
            if on_progress:
                on_progress(f"{parsed.source.name} -> {out_path.name}")
            result = process_image(parsed.source, out_path, opts)
            results.append(result)
    return results


@dataclass
class TemplateResult:
    sku: str
    slot: str
    source: Optional[Path]
    output: Path
    skipped_reason: Optional[str] = None


def _process_one_slot(
    image: Image.Image,
    rgba: Image.Image,
    subject_box: Optional[SubjectBox],
    spec: ShotSpec,
    template: Template,
    opts: CropOptions,
) -> Image.Image:
    src_size = image.size
    if subject_box is None:
        crop_box = centered_fallback_box(src_size, template.output_size)
    elif spec.region_of_subject is not None:
        crop_box = compute_region_crop_box(
            source_size=src_size,
            subject=subject_box,
            output_size=template.output_size,
            region_of_subject=spec.region_of_subject,
            region_fill=spec.region_fill,
        )
    else:
        subj_h = spec.subject_height_fraction or opts.subject_height_fraction
        crop_box = compute_crop_box(
            source_size=src_size,
            subject=subject_box,
            output_size=template.output_size,
            subject_height_fraction=subj_h,
            vertical_bias=spec.vertical_bias,
        )

    composed = _composed(image, rgba, opts)

    return crop_and_resize(composed, crop_box, template.output_size, allow_upscale=opts.allow_upscale)


def process_with_template(
    input_dir: Path,
    output_dir: Path,
    template: Template,
    opts: CropOptions | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> List[TemplateResult]:
    """Apply ``template`` to every SKU in ``input_dir``.

    For each SKU we detect the subject once on the hero shot (and per-shot
    as needed) and then emit exactly the slots declared by the template.
    Output filenames are ``{SKU}_{slot}.jpg``.
    """
    opts = opts or CropOptions()

    def _warn_dupe(sku: str, shot: int, kept: Path, dropped: Path) -> None:
        if on_progress:
            on_progress(f"  warning: {sku} shot {shot} has duplicate {dropped.name}; using {kept.name}")

    def _warn_norm(sku: str, mapping: Dict[int, int]) -> None:
        if on_progress:
            pairs = ", ".join(f"{k}->{v}" for k, v in mapping.items())
            on_progress(f"  normalized {sku} shot indices (counter offset): {pairs}")

    groups = group_by_sku(input_dir, opts.image_extensions, on_duplicate=_warn_dupe, on_normalize=_warn_norm)
    results: List[TemplateResult] = []

    for sku, items in groups.items():
        # group_by_sku guarantees one ParsedName per (sku, shot) now, so this
        # dict comp is safe — no silent winner.
        by_shot = {p.shot: p for p in items}
        for spec in template.shots:
            out_path = output_dir / f"{sku}_{spec.slot}.jpg"
            parsed = by_shot.get(spec.source_shot)
            if parsed is None:
                if on_progress:
                    on_progress(f"{sku}: missing input for slot {spec.slot} (shot {spec.source_shot}) — skipped")
                results.append(TemplateResult(sku=sku, slot=spec.slot, source=None,
                                              output=out_path, skipped_reason="missing input"))
                continue

            if on_progress:
                on_progress(f"{parsed.source.name} -> {out_path.name}")

            image = Image.open(parsed.source); image.load()
            rgba = extract_alpha(image, opts.rembg_model)
            subject_box = mask_to_box(rgba)
            if subject_box is not None:
                if subject_box.height / max(image.size[1], 1) < opts.min_subject_fraction:
                    subject_box = None

            final = _process_one_slot(image, rgba, subject_box, spec, template, opts)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            final.save(out_path, format="JPEG", quality=opts.jpeg_quality, optimize=True)
            results.append(TemplateResult(sku=sku, slot=spec.slot, source=parsed.source, output=out_path))

    return results


def _composed(image: Image.Image, rgba: Image.Image, opts: CropOptions) -> Image.Image:
    """Apply background removal + optional drop shadow, or pass through."""
    if not opts.remove_background:
        return image.convert("RGB")
    if opts.shadow_opacity > 0.0:
        return composite_on_color_with_shadow(
            rgba,
            opts.background_color,
            shadow_opacity=opts.shadow_opacity,
            shadow_blur=opts.shadow_blur,
            shadow_squash=opts.shadow_squash,
            shadow_offset=opts.shadow_offset,
        )
    return composite_on_color(rgba, opts.background_color)


def _emit(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
