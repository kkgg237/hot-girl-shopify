"""Push priced invoices to Shopify as draft products via the Admin REST API.

Replaces the CSV download + manual-upload step. After running:
  - Each item becomes a draft product on your shop
  - Title, vendor, product type, SKU, price, cost, tags come from the same
    pipeline that drives the CSV export
  - First cached photo is uploaded inline as the product image
  - Item's `shopify_product_id` is saved back to the invoice JSON so subsequent
    runs SKIP already-published items (idempotent)

Architecture:
  - REST endpoint: POST /admin/api/2024-10/products.json
  - Image upload: base64 `attachment` inside the product create call
    (single round-trip per item — no staged-upload dance)
  - Auth: shopify_inventory.get_token() — Custom App token OR auto-refreshed
    client_credentials token (same token used for read inventory)
  - Status: products are created with `status="draft"` so they're invisible
    to shoppers until you flip them to active in Shopify admin

Required scopes:
  - read_products
  - write_products  ← if missing, re-install your Custom App / re-run OAuth

Cost: free (Shopify API has no per-call charge, just rate limits)
Speed: ~1-2 sec/product (Shopify rate limit: 2/sec for Basic, 4/sec for Plus)
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _dt_now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


PROJECT_ROOT = Path(__file__).parent

# Auto-load .env so SHOPIFY_* vars are available regardless of how invoked.
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Push ledger — sidecar file tracking which items have been pushed
# ---------------------------------------------------------------------------
#
# Each invoice has a sibling JSON `output/<stem>.shopify_pushed.json` that
# maps source_id → {product_id, sku, handle, pushed_at}. This is the source
# of truth for "has this item been published?" — independent of the
# edited-vs-original invoice JSON split (which previously broke idempotency:
# pushes wrote IDs to `edited_*` but reads came from the original).
# Re-pushing an already-pushed item requires explicit `force=True`.


def _ledger_path(invoice_path: Path) -> Path:
    """Sidecar path for the push ledger. Uses the stem without 'edited_' prefix
    so the original and its edited copy share the same ledger."""
    stem = invoice_path.stem
    if stem.startswith("edited_"):
        stem = stem[len("edited_"):]
    return invoice_path.parent / f"{stem}.shopify_pushed.json"


def load_push_ledger(invoice_path: Path) -> dict[str, dict]:
    """Return {source_id: entry} for items already published from this invoice.
    Empty dict if no ledger exists yet."""
    p = _ledger_path(invoice_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_push_ledger(invoice_path: Path, ledger: dict[str, dict]) -> None:
    p = _ledger_path(invoice_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_to_ledger(invoice_path: Path, source_id: str, entry: dict) -> None:
    """Atomic-ish: read, mutate, write. Safe for single-process use; if you
    publish in parallel from two processes you'd want a file lock."""
    ledger = load_push_ledger(invoice_path)
    ledger[source_id] = entry
    save_push_ledger(invoice_path, ledger)


# ---------------------------------------------------------------------------
# Duplicate detection — fetch live Shopify products and group by SKU prefix
# so a user who accidentally pushed twice can spot + clean up
# ---------------------------------------------------------------------------

def _api_get(
    shop: str,
    token: str,
    path: str,
    timeout: int = 60,
    retries: int = 3,
    backoff_s: float = 1.5,
) -> tuple[int, dict, dict]:
    """GET helper. Returns (status, parsed_json, headers_dict).

    Robust to transient connection failures:
      - urllib.error.HTTPError → returns (status_code, parsed_body, {}) once
        (Shopify HTTP errors are usually not worth retrying except for 429/5xx,
        which we DO retry below)
      - urllib.error.URLError + socket.timeout + OSError → retried up to
        `retries` times with exponential backoff. On final failure, returns
        (0, {"error": "<reason>"}, {}).

    Why this matters: Streamlit's main thread blocks during the call, and a
    catalogue scan can hit 4-20 paginated GETs. A single transient timeout
    used to crash the whole tab. Now we retry, and worst case the scan
    returns whatever it has so far with an error annotation.
    """
    import socket as _socket

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}{path}"
    req = urllib.request.Request(
        url, headers={
            "X-Shopify-Access-Token": token,
            "Accept": "application/json",
        },
    )

    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, json.loads(resp.read().decode("utf-8")), headers
        except urllib.error.HTTPError as e:
            # 429 (rate limit) and 5xx are retryable; everything else returns
            # immediately so callers see the actual error code (404, 401, etc.)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff_s * (2 ** attempt))
                last_err = e
                continue
            try:
                return e.code, json.loads(e.read().decode("utf-8", errors="replace")), {}
            except Exception:
                return e.code, {}, {}
        except (urllib.error.URLError, _socket.timeout, OSError) as e:
            # Connection-level failure: timeout, DNS, refused, etc. Retry.
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff_s * (2 ** attempt))
                continue

    reason = str(last_err) if last_err else "unknown network error"
    return 0, {"error": f"network error after {retries} attempts: {reason}"}, {}


def find_duplicates(sku_prefix: Optional[str] = None) -> dict:
    """Walk Shopify products and report groups with duplicate handles/SKUs.

    Optional `sku_prefix` filters to e.g. "LOU_" / "FEN_" / etc. Helpful when
    investigating a specific batch.

    Returns:
        {
          "by_handle":  {handle: [list of products]},   # handle-level dupes
          "by_sku":     {sku: [list of variants]},      # SKU-level dupes
          "fetched":    int,
          "duplicate_groups": int,
        }
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return {"error": "Shopify not configured", "fetched": 0}

    products: list[dict] = []
    url_path = "/products.json?limit=250&fields=id,handle,title,vendor,variants,created_at"
    while url_path:
        status, data, headers = _api_get(shop, token, url_path)
        if status != 200:
            return {"error": f"HTTP {status}", "fetched": len(products)}
        products.extend(data.get("products", []))
        # Parse next-page from Link header
        link = headers.get("link", "")
        next_url = None
        import re as _re
        for u, rel in _re.findall(r'<([^>]+)>;\s*rel="(\w+)"', link):
            if rel == "next":
                next_url = u
                break
        if not next_url:
            break
        # Convert full URL → path
        url_path = next_url.split(f"/admin/api/{DEFAULT_API_VERSION}", 1)[-1]
        time.sleep(0.5)  # polite

    # Filter by SKU prefix if requested
    def matches_filter(product: dict) -> bool:
        if not sku_prefix:
            return True
        for v in product.get("variants", []) or []:
            sku = (v.get("sku") or "")
            if sku.startswith(sku_prefix):
                return True
        return False

    filtered = [p for p in products if matches_filter(p)]

    # Group by handle (case-insensitive)
    by_handle: dict[str, list[dict]] = {}
    by_sku: dict[str, list[dict]] = {}
    for p in filtered:
        h = (p.get("handle") or "").lower()
        if h:
            by_handle.setdefault(h, []).append(p)
        for v in p.get("variants", []) or []:
            sku = (v.get("sku") or "").strip()
            if sku:
                by_sku.setdefault(sku, []).append({
                    "product_id": p.get("id"),
                    "product_title": p.get("title"),
                    "variant_id": v.get("id"),
                    "sku": sku,
                    "created_at": p.get("created_at"),
                })

    handle_dupes = {h: ps for h, ps in by_handle.items() if len(ps) > 1}
    sku_dupes = {s: vs for s, vs in by_sku.items() if len(vs) > 1}

    return {
        "fetched": len(products),
        "filtered": len(filtered),
        "by_handle": handle_dupes,
        "by_sku": sku_dupes,
        "duplicate_groups": len(handle_dupes) + len(sku_dupes),
    }


# ---------------------------------------------------------------------------
# Catalogue audit — scan live listings for common data hygiene issues
# ---------------------------------------------------------------------------
#
# The two issues that bite us most often:
#   1. Products published with no images at all (placeholder or aborted upload)
#   2. Vendor field set to the store name ("Past Studies" / "paststudies")
#      instead of the actual brand (e.g. "Louis Vuitton", "Maison Margiela",
#      "Vintage" for unbranded). When vendor is the store name, the storefront
#      shows the store name as the brand, which looks broken to shoppers.
#
# This scan walks the full catalogue once and buckets products into issue
# categories. It's read-only — the UI presents results + admin links so the
# user can fix manually or trigger bulk vendor updates separately.


# Vendors that are *always* wrong (they're the store name, never a real brand).
# Case-insensitive match. The UI can extend this list.
DEFAULT_BAD_VENDORS = ("past studies", "paststudies", "past-studies")


def _brand_corpus() -> list[tuple[str, str]]:
    """Return [(pattern_lowercase, canonical_brand), ...] sorted longest-first.

    Built from heuristics/rules.yaml:
      - `tier_brands.luxury` + `tier_brands.mid` (canonical names)
      - `canonicalize.brands` (aliases like 'lv' → 'Louis Vuitton')

    Longest-first ordering means "Saint Laurent" beats "Laurent" and "Dolce &
    Gabbana" beats "Gabbana" when scanning a title.

    Cached on first call — the corpus is small (~50 entries) and rules.yaml
    is only re-loaded when the user hits the Rules tab.
    """
    cached = getattr(_brand_corpus, "_cache", None)
    if cached is not None:
        return cached
    try:
        from heuristics import RULES as _RULES
        tier = _RULES.tier_brands if hasattr(_RULES, "tier_brands") else {}
        aliases = _RULES.canonicalize.get("brands", {}) if hasattr(_RULES, "canonicalize") else {}
    except Exception:
        tier, aliases = {}, {}

    pairs: dict[str, str] = {}  # lowercase pattern → canonical
    # Tier brands — both the lower-cased canonical AND the canonical itself
    for canonical in list(tier.get("luxury", [])) + list(tier.get("mid", [])):
        if canonical:
            pairs.setdefault(canonical.lower(), canonical)
    # Alias map — lowercase key → canonical value
    for k, v in aliases.items():
        if k and v:
            pairs.setdefault(str(k).lower(), str(v))

    out = sorted(pairs.items(), key=lambda kv: -len(kv[0]))
    _brand_corpus._cache = out  # type: ignore[attr-defined]
    return out


def detect_brand_from_product(
    title: str,
    tags: Optional[str | list[str]] = None,
    product_type: Optional[str] = None,
) -> Optional[str]:
    """Best-effort brand detection from a Shopify product's title/tags/type.

    Strategy: substring-match the brand corpus (canonical names + aliases) against
    the lowercase title first (highest signal), then tags. Longest match wins,
    so "Saint Laurent" beats "Laurent" and "Dolce & Gabbana" beats "Gabbana".

    Returns the canonical brand string, or None if nothing matched.
    """
    corpus = _brand_corpus()
    if not corpus:
        return None

    haystacks: list[str] = []
    if title:
        haystacks.append(title.lower())
    if tags:
        if isinstance(tags, str):
            haystacks.append(tags.lower())
        else:
            haystacks.append(", ".join(str(t) for t in tags).lower())
    if product_type:
        haystacks.append(product_type.lower())

    if not haystacks:
        return None

    # Word-boundary-ish matching: require pattern to be surrounded by
    # non-letter chars (so "lv" doesn't match inside "valve"). Use a simple
    # check rather than regex compilation per call.
    import re as _re

    for pattern, canonical in corpus:
        # Escape regex specials in pattern; require non-letter boundaries
        esc = _re.escape(pattern)
        rx = _re.compile(rf"(?:^|[^a-z]){esc}(?:[^a-z]|$)", _re.IGNORECASE)
        for h in haystacks:
            if rx.search(h):
                return canonical
    return None


def _admin_product_url(shop: str, product_id: int) -> str:
    """Build the Shopify admin URL for editing a product."""
    # shop is e.g. "paststudies.myshopify.com" → admin lives at /admin/products/<id>
    return f"https://{shop}/admin/products/{product_id}"


def scan_catalogue_issues(
    bad_vendors: Optional[list[str]] = None,
    scope: str = "live",
) -> dict:
    """Walk the full Shopify catalogue and report data-hygiene issues.

    Issues detected per product:
      - `no_photos`: empty images array (the listing has zero pictures)
      - `wrong_vendor`: vendor matches `bad_vendors` (case-insensitive). Defaults
        to DEFAULT_BAD_VENDORS — i.e. products where the store name leaked into
        the vendor column.

    Args:
        bad_vendors: extra vendor strings to flag (merged with DEFAULT_BAD_VENDORS).
        scope: which products to actually flag —
            - "live" (default): only products LIVE on the website. Requires
              `status == "active"` AND `published_at != None` (i.e. published
              to the Online Store sales channel). This is the most useful
              default — a draft with no photo doesn't matter, only listings
              that customers can actually see do.
            - "active": all products with status="active", regardless of
              whether they're published to Online Store.
            - "all": every product including drafts + archived.

    Returns:
        {
          "fetched": int,                       # total products walked
          "scanned": int,                       # subset matching scope
          "scope": str,                         # echoed back for UI display
          "no_photos": [product_dict, ...],
          "wrong_vendor": [product_dict, ...],
          "by_vendor": {vendor: count, ...},    # frequency table for sanity check
          "bad_vendor_list": [str, ...],        # which vendors were treated as bad
          "error": str | None,
        }

    Each product_dict has: id, title, handle, vendor, status, product_type,
    image_count, admin_url, created_at, published_at, live_on_website.
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return {"error": "Shopify not configured", "fetched": 0}

    # Normalize the bad-vendor list (lowercase, stripped, deduped)
    extra = [v.strip().lower() for v in (bad_vendors or []) if v and v.strip()]
    bad_set = {v for v in (list(DEFAULT_BAD_VENDORS) + extra) if v}

    products: list[dict] = []
    partial_error: Optional[str] = None
    # fields= keeps the response small. `image` is the featured image; `images`
    # is the full array — we only need count, but Shopify gives us both cheaply.
    # `published_at` (timestamp) is set when the product is published to the
    # Online Store sales channel; None means "not live on website".
    url_path = (
        "/products.json?limit=250"
        "&fields=id,title,handle,vendor,product_type,status,images,tags,"
        "created_at,published_at,published_scope"
    )
    page = 0
    while url_path:
        page += 1
        status, data, headers = _api_get(shop, token, url_path)
        if status != 200:
            # If we got NOTHING, hard fail. Otherwise return a partial result
            # so the user can see the bulk of their catalogue + a note about
            # what failed.
            err_msg = (data.get("error") if isinstance(data, dict) else None) \
                      or f"HTTP {status}"
            if not products:
                return {
                    "error": f"{err_msg} on page {page}",
                    "fetched": 0,
                    "scanned": 0,
                    "no_photos": [],
                    "wrong_vendor": [],
                    "by_vendor": {},
                    "bad_vendor_list": sorted(bad_set),
                    "scope": scope,
                }
            partial_error = (
                f"⚠️ Stopped after page {page-1} ({len(products)} products fetched) "
                f"because: {err_msg}. Re-run for a complete scan."
            )
            break
        products.extend(data.get("products", []))
        link = headers.get("link", "")
        next_url = None
        import re as _re
        for u, rel in _re.findall(r'<([^>]+)>;\s*rel="(\w+)"', link):
            if rel == "next":
                next_url = u
                break
        if not next_url:
            break
        url_path = next_url.split(f"/admin/api/{DEFAULT_API_VERSION}", 1)[-1]
        time.sleep(0.5)

    no_photos: list[dict] = []
    wrong_vendor: list[dict] = []
    by_vendor: dict[str, int] = {}
    scanned = 0

    scope_norm = (scope or "live").lower()
    if scope_norm not in ("live", "active", "all"):
        scope_norm = "live"

    for p in products:
        status_val = (p.get("status") or "").lower()
        published_at = p.get("published_at")  # ISO string when live on Online Store
        live_on_website = bool(published_at) and status_val == "active"

        # Apply scope filter
        if scope_norm == "live" and not live_on_website:
            continue
        if scope_norm == "active" and status_val != "active":
            continue
        # scope == "all" — no filter

        scanned += 1

        vendor = (p.get("vendor") or "").strip()
        images = p.get("images") or []
        image_count = len(images)

        vendor_key = vendor or "(empty)"
        by_vendor[vendor_key] = by_vendor.get(vendor_key, 0) + 1

        row = {
            "id": p.get("id"),
            "title": p.get("title", ""),
            "handle": p.get("handle", ""),
            "vendor": vendor,
            "status": status_val,
            "product_type": p.get("product_type", ""),
            "tags": p.get("tags", ""),
            "image_count": image_count,
            "admin_url": _admin_product_url(shop, p.get("id")),
            "created_at": (p.get("created_at") or "")[:10],
            "published_at": (published_at or "")[:10],
            "live_on_website": live_on_website,
        }

        if image_count == 0:
            no_photos.append(row)
        if vendor.lower() in bad_set:
            # Only compute brand detection for the wrong-vendor rows (where
            # the user actually needs the suggestion). Cheap enough to do
            # inline — corpus is cached.
            row["detected_brand"] = detect_brand_from_product(
                row["title"], tags=row["tags"], product_type=row["product_type"],
            )
            wrong_vendor.append(row)

    # Sort newest-first so recent damage is at the top
    no_photos.sort(key=lambda r: r["created_at"], reverse=True)
    wrong_vendor.sort(key=lambda r: r["created_at"], reverse=True)

    return {
        "fetched": len(products),
        "scanned": scanned,
        "scope": scope_norm,
        "no_photos": no_photos,
        "wrong_vendor": wrong_vendor,
        "by_vendor": dict(sorted(by_vendor.items(), key=lambda kv: -kv[1])),
        "bad_vendor_list": sorted(bad_set),
        "error": None,
        "partial_error": partial_error,
    }


def update_product_status(product_id: int, new_status: str) -> tuple[int, dict]:
    """PUT a status change to a single product. Returns (status_code, body).

    Valid statuses: "active", "draft", "archived".

    Used by the catalogue audit "move to draft" bulk action to un-publish
    broken-customer-experience listings (no-photo items that are LIVE on
    Online Store). Setting status="draft" removes the product from all
    storefronts and sales channels until manually re-activated.

    200 = success.
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, {"error": "Shopify not configured"}

    valid = {"active", "draft", "archived"}
    if new_status not in valid:
        return 0, {"error": f"Invalid status {new_status!r}; expected one of {sorted(valid)}"}

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/products/{product_id}.json"
    body = {"product": {"id": product_id, "status": new_status}}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body_text) if body_text else {}
        except Exception:
            parsed = {"_raw_error": body_text if "body_text" in locals() else ""}
        return e.code, parsed


def update_product_vendor(product_id: int, new_vendor: str) -> tuple[int, dict]:
    """PUT a vendor change to a single product. Returns (status_code, body).

    Used by the catalogue audit "fix vendor" action. 200 = success.
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, {"error": "Shopify not configured"}

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/products/{product_id}.json"
    body = {"product": {"id": product_id, "vendor": new_vendor}}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body_text) if body_text else {}
        except Exception:
            parsed = {"_raw_error": body_text if "body_text" in locals() else ""}
        return e.code, parsed


def delete_product(product_id: int) -> tuple[int, dict]:
    """DELETE a product by ID. Use with caution — destructive.

    Returns (status_code, response_body). 200 = success.
    """
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, {"error": "Shopify not configured"}

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/products/{product_id}.json"
    req = urllib.request.Request(
        url, method="DELETE",
        headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, {"deleted": True}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        return e.code, body


# ---------------------------------------------------------------------------
# Low-level REST helpers
# ---------------------------------------------------------------------------

DEFAULT_API_VERSION = "2024-10"


def _api_post(shop: str, token: str, path: str, body: dict, timeout: int = 30) -> tuple[int, dict]:
    """POST to a Shopify Admin REST endpoint. Returns (status_code, parsed_json)."""
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body_text) if body_text else {}
        except Exception:
            parsed = {"_raw_error": body_text if "body_text" in locals() else ""}
        return e.code, parsed


# ---------------------------------------------------------------------------
# Product payload builder
# ---------------------------------------------------------------------------

def _read_image_b64(path: Path) -> Optional[tuple[str, str]]:
    """Read an image file as (base64_data, filename). None if unreadable.

    Shopify accepts JPG/PNG/GIF/WEBP. Max file size 20MB. Our cached
    thumbnails from photo_scraper are ~5-15 KB, well under.
    """
    try:
        data = path.read_bytes()
        if not data:
            return None
        b64 = base64.b64encode(data).decode("ascii")
        return b64, path.name
    except Exception:
        return None


def build_product_payload(
    item: dict,
    title: str,
    vendor: str,
    product_type: str,
    sku: str,
    price: float,
    cost_usd: float,
    photo_path: Optional[Path] = None,
    handle: Optional[str] = None,
    tags: Optional[list[str]] = None,
    body_html: str = "",
) -> dict:
    """Assemble the product dict for POST /admin/api/.../products.json.

    Status is `draft` so the product is hidden from shoppers until you
    explicitly activate it in Shopify admin. Inventory tracking is on,
    quantity 1 (each item is 1-of-1 vintage).
    """
    product = {
        "title": title,
        "vendor": vendor,
        "product_type": product_type,
        "status": "draft",
        "body_html": body_html,
        "tags": tags or [],
        "variants": [{
            "price": f"{price:.2f}",
            "sku": sku,
            "inventory_management": "shopify",
            "inventory_quantity": 1,
            "requires_shipping": True,
            "taxable": True,
            "cost": f"{cost_usd:.2f}" if cost_usd > 0 else None,
        }],
    }
    if handle:
        product["handle"] = handle
    if photo_path and photo_path.exists():
        img = _read_image_b64(photo_path)
        if img:
            b64, fname = img
            product["images"] = [{"attachment": b64, "filename": fname}]
    # Strip None variant fields (Shopify is picky)
    product["variants"][0] = {k: v for k, v in product["variants"][0].items() if v is not None}
    return product


# ---------------------------------------------------------------------------
# Orchestrator — walk an invoice's items, create products, persist IDs back
# ---------------------------------------------------------------------------

def publish_invoice_to_shopify(
    invoice_path: Path,
    rate: float,
    demand: float = 1.0,
    handling_rate: Optional[float] = None,
    import_tax_rate: Optional[float] = None,
    extra_rate: Optional[float] = None,
    extra_flat: Optional[float] = None,
    only_ids: Optional[list[str]] = None,
    dry_run: bool = False,
    force: bool = False,
    rate_limit_s: float = 0.6,
) -> dict:
    """Create draft products in Shopify for every item in this invoice.

    Idempotent: items recorded in the push ledger
    (`output/<stem>.shopify_pushed.json`) are skipped UNLESS `force=True`.
    The ledger is the source of truth — independent of edited/original
    invoice splits — so a double-click can't accidentally create duplicates.

    Per-item failures don't stop the batch — each item gets its own
    try/except and the failure goes into the result log.

    Returns stats dict + per-item log.
    """
    from costs import Invoice, InvoiceView
    from price import price_invoice
    from to_shopify import (
        make_sku, make_handle, make_title, shopify_category, canon_brand, canon_type,
    )
    from shopify_inventory import get_token, get_shop, refresh_inventory

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return {"ok": False, "error": "Shopify not configured. See Export tab setup."}

    # Load + price the invoice
    data = json.loads(invoice_path.read_text(encoding="utf-8"))
    invoice = Invoice(**{k: v for k, v in data.items() if not k.startswith("_")})
    priced = price_invoice(
        invoice, rate, demand,
        handling_rate=handling_rate, import_tax_rate=import_tax_rate,
        extra_rate=extra_rate, extra_flat=extra_flat,
    )

    # Pull live Shopify inventory for collision check
    inventory = refresh_inventory()
    existing_skus = set(inventory.skus) if inventory and inventory.is_loaded else set()
    existing_handles = set(inventory.handles) if inventory and inventory.is_loaded else set()

    # Load the push ledger — the source of truth for "what's been published".
    # Reads/writes a sidecar JSON next to the invoice, independent of the
    # edited/original split. Survives any reload / accidental double-click.
    ledger = load_push_ledger(invoice_path)

    # We track used SKUs / handles across items in this batch so generation
    # picks fresh 3-digit suffixes when collisions occur (same logic as CSV path).
    # disambiguate() lives in shopify_inventory — make_sku handles its own
    # 3-digit collision logic internally now via the `blocked` parameter.
    from shopify_inventory import disambiguate as inv_disambiguate
    used_skus: set[str] = set()
    used_handles: set[str] = set()

    stats = {
        "ok": True,
        "items_total": len(priced["items"]),
        "items_attempted": 0,
        "items_published": 0,
        "items_skipped_existing": 0,
        "items_failed": 0,
        "log": [],  # list of {source_id, status, message, product_id?}
    }

    # Photo lookup helper
    invoice_stem = invoice_path.stem
    photos_dir = PROJECT_ROOT / "output" / "photos" / invoice_stem

    def _photo_for(source_id: str) -> Optional[Path]:
        if not source_id:
            return None
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = photos_dir / f"{source_id}.{ext}"
            if p.exists():
                return p
        return None

    for item in priced["items"]:
        source_id = item.get("source_id")
        if only_ids and source_id not in only_ids:
            continue

        # Skip if the ledger already records this item as published. The
        # ledger is the canonical "what's been pushed" record — it survives
        # the edited-vs-original invoice split that previously broke
        # idempotency. Override with force=True.
        if not force and source_id in ledger:
            entry = ledger[source_id]
            stats["items_skipped_existing"] += 1
            stats["log"].append({
                "source_id": source_id,
                "status": "skipped",
                "message": f"already pushed as product {entry.get('product_id')} "
                           f"on {entry.get('pushed_at','?')[:10]}",
            })
            continue
        # Belt-and-suspenders: in-memory item also tracks shopify_product_id.
        # (This catches the rare case where the ledger got deleted but the
        # invoice JSON still has the id.)
        if not force and item.get("shopify_product_id"):
            stats["items_skipped_existing"] += 1
            stats["log"].append({
                "source_id": source_id,
                "status": "skipped",
                "message": f"invoice JSON already records product {item['shopify_product_id']}",
            })
            continue

        stats["items_attempted"] += 1

        # Build the same fields the CSV builder would build
        vendor = (
            item.get("override_vendor")
            or canon_brand(item.get("detected_brand"))
            or "Vintage"
        )
        product_type = canon_type(item.get("product_type")) or ""
        category = shopify_category(item.get("product_type"))  # full GCP path (used elsewhere)
        # SKU + handle: use the collision-aware make_sku that picks a fresh
        # 3-digit suffix when the source-id-derived one is taken.
        proposed_sku, _ = make_sku(
            vendor, invoice.invoice_date, source_id, used_skus, blocked=existing_skus,
        )
        sku = proposed_sku
        # Title from the canonical compose_title
        title = make_title(item, is_lot_unit=item.get("quantity", 1) > 1)
        # Handle from title + sku (collision-disambiguated)
        proposed_handle = make_handle(title, sku).lower()
        handle = inv_disambiguate(
            proposed_handle,
            used_handles | {h.lower() for h in existing_handles},
        )

        # Pricing — pull from the priced invoice
        pricing = item.get("pricing_result", {})
        cost_per_unit = item.get("cost_breakdown", {}).get("unit_cost_usd", 0)
        price_val = pricing.get("rounded_price") or 0
        if not price_val:
            stats["items_failed"] += 1
            stats["log"].append({
                "source_id": source_id,
                "status": "failed",
                "message": "no rounded_price computed",
            })
            continue

        photo_path = _photo_for(source_id)
        payload = build_product_payload(
            item, title=title, vendor=vendor, product_type=product_type,
            sku=sku, price=float(price_val), cost_usd=float(cost_per_unit),
            photo_path=photo_path, handle=handle,
        )

        if dry_run:
            stats["log"].append({
                "source_id": source_id,
                "status": "dry_run",
                "message": f"would create '{title}' · SKU {sku} · ${price_val} · "
                           f"photo={'yes' if photo_path else 'no'}",
            })
            continue

        # POST to Shopify
        try:
            status_code, response = _api_post(shop, token, "/products.json", {"product": payload})
        except Exception as e:
            stats["items_failed"] += 1
            stats["log"].append({
                "source_id": source_id, "status": "failed",
                "message": f"request crashed: {e}",
            })
            continue

        if status_code == 201 and "product" in response:
            product_id = response["product"].get("id")
            shopify_handle = response["product"].get("handle")
            now_iso = _dt_now_iso()
            # 1. Append to the ledger IMMEDIATELY — before any other items are
            # processed — so a crash/retry mid-batch won't double-create THIS item.
            _append_to_ledger(invoice_path, source_id, {
                "product_id": product_id,
                "sku": sku,
                "handle": shopify_handle,
                "pushed_at": now_iso,
            })
            # 2. Also stamp the in-memory invoice dict (for the JSON writeback below)
            for raw_item in data["items"]:
                if raw_item.get("source_id") == source_id:
                    raw_item["shopify_product_id"] = product_id
                    raw_item["shopify_sku"] = sku
                    raw_item["shopify_handle"] = shopify_handle
                    raw_item["shopify_pushed_at"] = now_iso
                    break
            stats["items_published"] += 1
            stats["log"].append({
                "source_id": source_id,
                "status": "published",
                "product_id": product_id,
                "message": f"created · {shopify_handle}",
            })
        else:
            # Surface Shopify's error message — usually very specific
            err_msg = ""
            if isinstance(response, dict):
                if "errors" in response:
                    err_msg = json.dumps(response["errors"])[:300]
                elif "_raw_error" in response:
                    err_msg = response["_raw_error"][:300]
            stats["items_failed"] += 1
            stats["log"].append({
                "source_id": source_id, "status": "failed",
                "message": f"HTTP {status_code}: {err_msg or 'unknown error'}",
            })

        if rate_limit_s:
            time.sleep(rate_limit_s)

    # Save the updated invoice JSON with shopify_product_ids attached
    if not dry_run and stats["items_published"] > 0:
        target = invoice_path
        if not target.name.startswith("edited_"):
            target = invoice_path.parent / f"edited_{invoice_path.name}"
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        stats["invoice_saved_to"] = str(target)

    return stats
