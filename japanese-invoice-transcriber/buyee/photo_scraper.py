"""Fetch the FIRST product photo from each item's Buyee auction page,
compress to a small thumbnail, save locally.

Architecture:
  - Only works for items whose source_id is a Yahoo Auctions ID (lowercase
    letter + digits, e.g. c1221895009). V-prefix wholesale items have no
    auction URL on Buyee and are skipped.
  - Reuses the existing Playwright persistent profile from auth.py — same
    cookies, same Cloudflare clearance, no extra setup.
  - Pillow resizes to ≤200px max edge, JPEG quality 60. Each thumbnail
    ends up ~5-15 KB.
  - Cached by source_id under output/photos/<invoice_stem>/<source_id>.jpg.
    Idempotent — re-running skips items that already have a thumbnail.

Cost: $0 (no API calls). Speed: ~3-5 sec per item.
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path
from typing import Optional

from .auth import with_session


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
PHOTOS_DIR = PROJECT_ROOT / "output" / "photos"

# Lowercase letter + 8+ digits = Yahoo Auctions ID
AUCTION_PATTERN = re.compile(r"^[a-z]\d{8,}$")

# JS that runs in the page context to find the main product image URL.
# Tries og:image first (most reliable), falls back to known DOM selectors.
_FIND_FIRST_IMG = """() => {
    const og = document.querySelector('meta[property="og:image"]');
    if (og && og.content) return og.content;
    const candidates = [
        'img[itemprop="image"]',
        '.g-thumbnail__image',
        '.g-itemDetail__main img',
        '.itemImg img',
        '.itemDetail-image img',
        '#image_now',
        '.swiper-slide img',
        'img.lazyload[src]',
    ];
    for (const sel of candidates) {
        const el = document.querySelector(sel);
        if (el && (el.src || el.dataset.src)) {
            return el.src || el.dataset.src;
        }
    }
    return null;
}"""


def fetch_first_photo(
    page,
    auction_id: str,
    out_path: Path,
    max_size: int = 200,
    quality: int = 60,
    timeout_ms: int = 20000,
) -> tuple[bool, str]:
    """Navigate to a Buyee auction page, grab the first photo, save thumbnail.

    Returns (success, message). Message is for logging — describes what
    happened (URL fetched, image dimensions, errors, etc.)
    """
    url = f"https://buyee.jp/item/jdirectitems/auction/{auction_id}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:
        return False, f"navigate failed: {e}"

    # Some auctions are expired and redirect to a "sorry, no longer available"
    # page. We can still grab the photo if it's on the og:image.
    final_url = page.url
    if "search" in final_url.lower() and "auction" not in final_url.lower():
        return False, f"redirected away: {final_url}"

    try:
        img_url = page.evaluate(_FIND_FIRST_IMG)
    except Exception as e:
        return False, f"page eval failed: {e}"

    if not img_url:
        return False, "no image found on page"
    if img_url.startswith("//"):
        img_url = "https:" + img_url
    elif img_url.startswith("/"):
        img_url = "https://buyee.jp" + img_url

    try:
        resp = page.context.request.get(img_url, timeout=timeout_ms)
        if resp.status >= 400:
            return False, f"image fetch HTTP {resp.status}"
        img_bytes = resp.body()
    except Exception as e:
        return False, f"image download failed: {e}"

    if not img_bytes or len(img_bytes) < 200:
        return False, f"image too small ({len(img_bytes)} bytes)"

    # Resize + compress
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        orig_w, orig_h = img.size
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "JPEG", quality=quality, optimize=True)
        size_kb = out_path.stat().st_size / 1024
        return True, f"{orig_w}x{orig_h} → {img.width}x{img.height}, {size_kb:.1f} KB"
    except ImportError:
        # No Pillow — save raw, oh well
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(img_bytes)
        return True, f"raw {len(img_bytes)} bytes (no Pillow → no resize)"
    except Exception as e:
        return False, f"resize/save failed: {e}"


def is_eligible(source_id: Optional[str]) -> bool:
    """True if this source_id has a fetchable Buyee auction page."""
    return bool(source_id and AUCTION_PATTERN.match(source_id))


def fetch_invoice_photos(
    invoice_path: Path,
    only_ids: Optional[list[str]] = None,
    overwrite: bool = False,
    polite_delay_s: float = 0.5,
) -> dict:
    """Fetch first-photo thumbnails for every eligible item in an invoice.

    Eligibility = lowercase-prefix Yahoo Auctions ID. V-prefix items skipped.
    Cached results (existing files) are skipped unless overwrite=True.

    Returns stats dict.
    """
    data = json.loads(invoice_path.read_text(encoding="utf-8"))
    items = data.get("items", [])

    out_dir = PHOTOS_DIR / invoice_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "total_items": len(items),
        "eligible": 0,
        "downloaded": 0,
        "skipped_existing": 0,
        "skipped_ineligible": 0,
        "errors": 0,
    }
    log: list[str] = []

    eligible_items = []
    for it in items:
        sid = it.get("source_id")
        if only_ids is not None and sid not in only_ids:
            continue
        if not is_eligible(sid):
            stats["skipped_ineligible"] += 1
            continue
        eligible_items.append(it)
    stats["eligible"] = len(eligible_items)

    if not eligible_items:
        return {**stats, "log": ["No eligible items (no Yahoo Auctions IDs)."]}

    print(f"Fetching photos for {len(eligible_items)} eligible item(s)...")

    with with_session(headless=True) as (pw, ctx, page):
        for it in eligible_items:
            sid = it["source_id"]
            out_path = out_dir / f"{sid}.jpg"
            if out_path.exists() and not overwrite:
                stats["skipped_existing"] += 1
                print(f"  · {sid}: already cached")
                continue

            ok, msg = fetch_first_photo(page, sid, out_path)
            if ok:
                stats["downloaded"] += 1
                print(f"  ✓ {sid}: {msg}")
                log.append(f"{sid}: {msg}")
            else:
                stats["errors"] += 1
                print(f"  ✗ {sid}: {msg}")
                log.append(f"{sid}: ERROR {msg}")

            if polite_delay_s:
                time.sleep(polite_delay_s)

    # Drop a manifest for the UI to scan quickly
    manifest = {
        "invoice": str(invoice_path),
        "stats": stats,
        "log": log,
    }
    (out_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return stats


def photo_for(invoice_stem: str, source_id: str) -> Optional[Path]:
    """Return the cached thumbnail path for an item, or None."""
    if not invoice_stem or not source_id:
        return None
    p = PHOTOS_DIR / invoice_stem / f"{source_id}.jpg"
    return p if p.exists() else None
