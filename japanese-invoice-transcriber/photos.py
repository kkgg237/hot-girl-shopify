"""Extract embedded thumbnails from invoice PDFs and associate them with items.

Strategy:
  1. Walk every page of the PDF, get embedded images with their bounding boxes
  2. Walk text spans on the same page, find positions of known source_ids
  3. For each image, find the nearest source_id by Y-coordinate (same row).
  4. Save extracted image to output/photos/<invoice_stem>/<source_id>.<ext>
  5. Resize to a small thumbnail too (max ~140px wide) for fast Streamlit render

This is "best-effort" — invoice layouts vary. We log unmatched images and
unmatched source_ids so the user can see coverage.

Cost: pure local CPU, no API calls. ~50-200ms per invoice.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Optional

import pymupdf  # PyMuPDF

PROJECT_ROOT = Path(__file__).parent
PHOTOS_DIR = PROJECT_ROOT / "output" / "photos"
THUMB_MAX_WIDTH = 280  # full thumbnail


def extract_invoice_photos(
    pdf_path: Path,
    source_ids: list[str],
    out_dir: Optional[Path] = None,
) -> dict[str, Path]:
    """Extract images from a PDF and return {source_id: path_to_image}.

    out_dir defaults to output/photos/<pdf_stem>/.
    Skips items where no image could be matched.
    """
    if out_dir is None:
        out_dir = PHOTOS_DIR / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(str(pdf_path))
    sid_set = set(source_ids)
    matched: dict[str, Path] = {}

    try:
        for page_num, page in enumerate(doc):
            # 1. Find source_id positions on this page
            sid_positions: list[tuple[str, float, float]] = []  # (sid, cx, cy)
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        bbox = span.get("bbox", (0, 0, 0, 0))
                        for sid in sid_set:
                            if sid in text:
                                cx = (bbox[0] + bbox[2]) / 2
                                cy = (bbox[1] + bbox[3]) / 2
                                sid_positions.append((sid, cx, cy))
                                break  # one match per span

            # 2. Find image positions + extract
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    continue
                if not rects:
                    continue

                for rect in rects:
                    img_cx = (rect.x0 + rect.x1) / 2
                    img_cy = (rect.y0 + rect.y1) / 2
                    img_w = rect.x1 - rect.x0

                    # Skip likely non-product images: tiny icons (logos, decorations)
                    if img_w < 30:
                        continue

                    # Find nearest unmatched source_id, weighting Y heavily
                    # (same-row match is what we want)
                    best_sid: Optional[str] = None
                    best_score = float("inf")
                    for sid, sx, sy in sid_positions:
                        if sid in matched:
                            continue
                        dy = abs(img_cy - sy)
                        dx = abs(img_cx - sx)
                        # Penalty for being too far vertically (different row)
                        score = dy * 3 + dx
                        if score < best_score:
                            best_score = score
                            best_sid = sid

                    if best_sid is None:
                        continue

                    # Extract image bytes
                    try:
                        img_data = doc.extract_image(xref)
                    except Exception:
                        continue
                    ext = (img_data.get("ext") or "jpg").lower()
                    if ext == "jpeg":
                        ext = "jpg"
                    out_path = out_dir / f"{best_sid}.{ext}"
                    out_path.write_bytes(img_data["image"])
                    matched[best_sid] = out_path
                    break  # one matched rect per image
    finally:
        doc.close()

    # Write a manifest so the UI can quickly check what's available
    manifest = {
        "pdf": str(pdf_path),
        "matched": {sid: str(p.relative_to(PROJECT_ROOT)) for sid, p in matched.items()},
        "unmatched_source_ids": sorted(sid_set - set(matched.keys())),
    }
    (out_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return matched


def photo_for(invoice_stem: str, source_id: str) -> Optional[Path]:
    """Look up a cached photo for an item. Tries common filename extensions."""
    photos_dir = PHOTOS_DIR / invoice_stem
    if not photos_dir.exists():
        return None
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        candidate = photos_dir / f"{source_id}.{ext}"
        if candidate.exists():
            return candidate
    return None


def photo_data_uri(path: Path, max_width: int = THUMB_MAX_WIDTH) -> Optional[str]:
    """Return a base64 data: URI for embedding in HTML.

    Resizes to at most max_width pixels using Pillow (already a transitive
    Streamlit dep). Encodes as JPEG for size.
    """
    try:
        from PIL import Image
        import io as _io
    except ImportError:
        # Fall back to raw bytes if Pillow isn't available
        data = path.read_bytes()
        mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    try:
        img = Image.open(path)
        img.load()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"[photos] Resize failed for {path}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# CLI for backfilling existing invoices
# ---------------------------------------------------------------------------

def main():
    """Backfill photos for invoices already transcribed in output/.

    Usage:
        uv run --with pymupdf --with pillow --with pydantic python photos.py
        uv run --with pymupdf --with pillow --with pydantic python photos.py <invoice.json>
    """
    import argparse
    parser = argparse.ArgumentParser(description="Extract photos from invoice PDFs.")
    parser.add_argument("invoice", nargs="?", help="Path to a transcribed JSON. Default: all in output/")
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "output"
    if args.invoice:
        targets = [Path(args.invoice)]
    else:
        targets = sorted(output_dir.glob("*.json"))

    for json_path in targets:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"✗ {json_path.name}: failed to read JSON: {e}")
            continue
        sids = [it["source_id"] for it in data.get("items", []) if it.get("source_id")]
        if not sids:
            continue

        # Locate the source PDF — try inputs/ subtrees and samples/
        stem = json_path.stem
        # Strip Telegram timestamp prefix if present (YYYY-MM-DD_HHMMSS_)
        bare_stem = stem
        import re as _re
        m = _re.match(r"^\d{4}-\d{2}-\d{2}_\d{6}_(.+)$", stem)
        if m:
            bare_stem = m.group(1)

        candidates = [
            PROJECT_ROOT / "samples" / f"{stem}.pdf",
            PROJECT_ROOT / "samples" / f"{bare_stem}.pdf",
            PROJECT_ROOT / "inputs" / f"{stem}.pdf",
            PROJECT_ROOT / "inputs" / f"{bare_stem}.pdf",
            PROJECT_ROOT / "inputs" / "telegram" / f"{stem}.pdf",
            PROJECT_ROOT / "inputs" / "buyee" / f"buyee_{data.get('invoice_number', '')}.pdf",
        ]
        pdf_path = next((p for p in candidates if p.exists()), None)
        if not pdf_path:
            print(f"⏭ {json_path.name}: no source PDF found (tried {len(candidates)} paths)")
            continue

        print(f"→ {json_path.name}: extracting from {pdf_path.name} ({len(sids)} items)")
        try:
            matched = extract_invoice_photos(pdf_path, sids)
        except Exception as e:
            print(f"  ✗ extraction failed: {e}")
            continue
        print(f"  ✓ matched {len(matched)}/{len(sids)} items")
        if len(matched) < len(sids):
            missing = sorted(set(sids) - set(matched.keys()))
            print(f"  ⓘ unmatched: {missing[:8]}{'...' if len(missing) > 8 else ''}")


if __name__ == "__main__":
    main()
