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
#
# Strategy in priority order:
#   1. btob product photo — looks like cdn.buyee.jp/btob/images/auction/item/.../1.jpg
#   2. Yahoo Auctions product photo — itemprop=image / known selectors
#   3. og:image meta — works for most Yahoo Auctions pages
#
# Logo-blocklist: explicitly reject Buyee branding images that show up on
# many pages (especially btob, where og:image is the generic logo). Without
# this filter, every btob item would download the exact same 22KB logo.
_FIND_FIRST_IMG = r"""() => {
    const LOGO_PAT = /(buyee_logo|logo_buyee|googlelogo|gsi\/style)/i;
    const isProductPhoto = (src) => src && !LOGO_PAT.test(src);

    // 1. btob CDN pattern — these are the real high-res product shots
    const btobImgs = Array.from(document.querySelectorAll('img'))
        .map(i => i.src || i.dataset.src || '')
        .filter(src => /cdn\.buyee\.jp\/btob\/images\/auction\/item\//.test(src));
    if (btobImgs.length) return btobImgs[0];

    // 2. Yahoo Auctions DOM selectors
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
        const src = el && (el.src || el.dataset.src);
        if (isProductPhoto(src)) return src;
    }

    // 3. og:image fallback (filtered against the logo blocklist)
    const og = document.querySelector('meta[property="og:image"]');
    if (og && isProductPhoto(og.content)) return og.content;

    // 4. Last resort: any <img> whose src isn't on the logo blocklist
    for (const img of document.querySelectorAll('img')) {
        const src = img.src || img.dataset.src;
        if (isProductPhoto(src) && img.naturalWidth > 200) return src;
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
    """Backward-compat wrapper — accepts a Yahoo Auctions ID, constructs
    the jdirectitems URL, then delegates to `fetch_first_photo_from_url`.
    """
    url = f"https://buyee.jp/item/jdirectitems/auction/{auction_id}"
    return fetch_first_photo_from_url(page, url, out_path,
                                       max_size=max_size, quality=quality,
                                       timeout_ms=timeout_ms)


def fetch_first_photo_from_url(
    page,
    url: str,
    out_path: Path,
    max_size: int = 200,
    quality: int = 60,
    timeout_ms: int = 20000,
) -> tuple[bool, str]:
    """Navigate to ANY Buyee item page (Yahoo Auctions OR btob), grab the
    first photo (og:image preferred), save as a compressed thumbnail.

    Works for both URL families because the og:image meta tag is present
    on every Buyee item-detail page regardless of source namespace. The
    DOM-selector fallbacks in `_FIND_FIRST_IMG` cover edge cases where
    og:image is missing.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as e:
        return False, f"navigate failed: {e}"

    # Yahoo Auctions: expired auctions redirect to a search page.
    # btob: expired/removed items 404 to the same generic "Page not found".
    # Either way, we can still try the og:image (sometimes preserved on
    # the error page); if that fails, no harm done.
    final_url = page.url
    if final_url.endswith("/signup/login") or "/signup/login?" in final_url:
        return False, f"session expired (redirected to login)"

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


def _url_for_source_id(source_id: Optional[str]) -> Optional[str]:
    """Return the Buyee item URL for a source_id, or None if unfetchable.

    Two namespaces:
      - Yahoo Auctions (lowercase letter + 8+ digits): URL is DETERMINISTIC,
        constructed inline (no Buyee call required).
      - LuxeWholesale (V-prefix + digits): URL is OPAQUE, looked up in the
        btob-URL cache (`buyee/state/item_urls.json`) populated by
        `scraper.scrape_item_urls()`. Returns None if not yet cached.

    Everything else (CSV-imported, BrandStreet auth codes, etc.) → None.
    """
    if not source_id:
        return None
    if AUCTION_PATTERN.match(source_id):
        return f"https://buyee.jp/item/jdirectitems/auction/{source_id}"
    if source_id.startswith("V") and source_id[1:].isdigit():
        # Lazy import to avoid a circular dep at module-load time
        try:
            from .scraper import get_item_url
        except ImportError:
            return None
        return get_item_url(source_id)
    return None


def is_eligible(source_id: Optional[str]) -> bool:
    """True if we have (or can construct) a Buyee URL for this item.

    Now covers BOTH Yahoo Auctions IDs (always fetchable) AND V-prefix
    LuxeWholesale items whose btob URL has been cached via
    `python -m buyee scrape-urls`. V-prefix items without a cached URL
    return False — run scrape-urls to populate them.
    """
    return _url_for_source_id(source_id) is not None


def fetch_invoice_photos(
    invoice_path: Path,
    only_ids: Optional[list[str]] = None,
    overwrite: bool = False,
    polite_delay_s: float = 0.5,
) -> dict:
    """Fetch first-photo thumbnails for every eligible item in an invoice.

    Eligibility = either a Yahoo Auctions ID (URL constructed deterministically)
    OR a V-prefix LuxeWholesale ID whose btob URL is in the scraped cache.
    Cached results (existing thumbnail files) are skipped unless overwrite=True.

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

    # Pair each eligible item with its resolved URL up-front so we have one
    # clean spot to short-circuit ineligibles and avoid two-pass eligibility
    # logic. Items without a URL (no Yahoo match, no cache hit) get counted
    # as ineligible — the bot can suggest running scrape-urls when this
    # number is high.
    eligible: list[tuple[dict, str]] = []
    for it in items:
        sid = it.get("source_id")
        if only_ids is not None and sid not in only_ids:
            continue
        url = _url_for_source_id(sid)
        if not url:
            stats["skipped_ineligible"] += 1
            continue
        eligible.append((it, url))
    stats["eligible"] = len(eligible)

    if not eligible:
        return {**stats, "log": [
            "No eligible items. Run `python -m buyee scrape-urls` to cache "
            "btob URLs for V-prefix items, then retry."
        ]}

    print(f"Fetching photos for {len(eligible)} eligible item(s)...")

    with with_session(headless=True) as (pw, ctx, page):
        for it, url in eligible:
            sid = it["source_id"]
            out_path = out_dir / f"{sid}.jpg"
            if out_path.exists() and not overwrite:
                stats["skipped_existing"] += 1
                print(f"  · {sid}: already cached")
                continue

            ok, msg = fetch_first_photo_from_url(page, url, out_path)
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
