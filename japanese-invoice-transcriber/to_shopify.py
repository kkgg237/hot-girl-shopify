#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.0"]
# ///
"""Convert priced invoice JSON(s) into Shopify inventory CSV rows.

Reads the output of `price.py` (priced/*.json) — NOT raw transcription JSONs.
The pricing pipeline runs in `price.py`; this script only formats.

Matches the header in samples/2026_January_Inventory_Template.csv and applies
spec §13 rules: full Shopify taxonomy paths, SKU format, lot expansion,
optional internal `_Markup` / `_Base Price` / `_source_file` columns.

Usage:
    uv run to_shopify.py priced/                             # every *.json
    uv run to_shopify.py priced/invoice.json                 # single file
    uv run to_shopify.py priced/ --strip-internal -o out.csv # no debug cols
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from pricing import canon_brand, canon_type, compose_title
from costs import LineItem
from extractors import search_keywords


# ---------------------------------------------------------------------------
# Shopify taxonomy paths (SPEC §14)
# ---------------------------------------------------------------------------

SHOPIFY_CATEGORY = {
    "Handbag":       "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Shoulder Bag":  "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shoulder Bags",
    "Clutch Bag":    "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Clutch Bags",
    "Tote Bag":      "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shopper Bags",
    "Hobo Bag":      "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Hobo Bags",
    "Duffle Bag":    "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Duffel Bag":    "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Boston Bag":    "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Travel Bag":    "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Weekender":     "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Backpack":      "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Backpacks",
    "Crossbody Bag": "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shoulder Bags",
    "Pouch":         "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Belt Bag":      "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Bag":           "Apparel & Accessories > Handbags, Wallets & Cases > Handbags",
    "Clutch":        "Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Clutch Bags",
    "Wallet":        "Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Wallets",
    "Card Holder":   "Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Card Cases",
    "Key Holder":    "Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Key Cases",
    "Sunglasses":    "Apparel & Accessories > Clothing Accessories > Sunglasses",
    "Belt":          "Apparel & Accessories > Clothing Accessories > Belts",
    "Scarf":         "Apparel & Accessories > Clothing Accessories > Scarves & Shawls",
    "Coat":          "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Trench Coat":   "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Fur Coat":      "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Jacket":        "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Leather Jacket":"Apparel & Accessories > Clothing > Outerwear > Coats & Jackets",
    "Blazer":        "Apparel & Accessories > Clothing > Outerwear > Coats & Jackets > Sport Jackets",
    "Dress":         "Apparel & Accessories > Clothing > Dresses",
    "Top":           "Apparel & Accessories > Clothing > Clothing Tops",
    "Shirt":         "Apparel & Accessories > Clothing > Clothing Tops > Shirts",
    "Blouse":        "Apparel & Accessories > Clothing > Clothing Tops > Shirts",
    "T-Shirt":       "Apparel & Accessories > Clothing > Clothing Tops > T-Shirts",
    "Vest":          "Apparel & Accessories > Clothing > Clothing Tops",
    "Sweater":       "Apparel & Accessories > Clothing > Clothing Tops > Sweaters",
    "Cardigan":      "Apparel & Accessories > Clothing > Clothing Tops > Cardigans",
    "Skirt":         "Apparel & Accessories > Clothing > Skirts",
    "Pants":         "Apparel & Accessories > Clothing > Pants",
    # Footwear — all roll up to the same Shopify GCP node
    "Shoes":         "Apparel & Accessories > Shoes",
    "Ballet Flats":  "Apparel & Accessories > Shoes",
    "Flats":         "Apparel & Accessories > Shoes",
    "Mules":         "Apparel & Accessories > Shoes",
    "Slides":        "Apparel & Accessories > Shoes",
    "Heels":         "Apparel & Accessories > Shoes",
    "Pumps":         "Apparel & Accessories > Shoes",
    "Boots":         "Apparel & Accessories > Shoes",
    "Ankle Boots":   "Apparel & Accessories > Shoes",
    "Sneakers":      "Apparel & Accessories > Shoes",
    "Sandals":       "Apparel & Accessories > Shoes",
    "Loafers":       "Apparel & Accessories > Shoes",
    "Clothing":      "Apparel & Accessories > Clothing",
}
DEFAULT_CATEGORY = "Apparel & Accessories > Clothing"


def shopify_category(product_type: str | None) -> str:
    t = canon_type(product_type)
    return SHOPIFY_CATEGORY.get(t or "", DEFAULT_CATEGORY)


# ---------------------------------------------------------------------------
# SKU generation — {BRAND_3}_{YYMM}_{3_DIGITS}
# ---------------------------------------------------------------------------

BRAND_PREFIX_OVERRIDES = {
    "Louis Vuitton": "LV", "Dolce & Gabbana": "DG",
    "Christian Dior": "DIO", "Yves Saint Laurent": "YSL",
    "Saint Laurent": "SL", "Bottega Veneta": "BV",
    "Miu Miu": "MIU", "Issey Miyake": "ISM",
}


def brand_prefix(brand: str | None) -> str:
    if not brand:
        return "UNK"
    b = canon_brand(brand) or brand
    if b in BRAND_PREFIX_OVERRIDES:
        return BRAND_PREFIX_OVERRIDES[b]
    letters = "".join(c for c in b if c.isalpha())[:3].upper()
    return letters or "UNK"


def random_suffix(source_id: str | None, _used: set[str]) -> str:
    """Prefer the last 3 alphanumerics of source_id for traceability; else random.

    Note: kept for backward compatibility, but make_sku() no longer relies on
    this for collision avoidance — it does its own walk through 000-999.
    """
    if source_id:
        s = "".join(c for c in source_id if c.isalnum())[-3:].upper()
        if s and len(s) == 3:
            return s
    while True:
        n = f"{random.randint(0, 999):03d}"
        if n not in _used:
            return n


def _source_id_suffix(source_id: str | None) -> str | None:
    """Last 3 alphanumeric chars of source_id, uppercase. None if unavailable."""
    if not source_id:
        return None
    s = "".join(c for c in source_id if c.isalnum())[-3:].upper()
    return s if len(s) == 3 else None


def make_sku(
    brand: str | None,
    invoice_date: str | None,
    source_id: str | None,
    used: set[str],
    blocked: set[str] | None = None,
) -> tuple[str, str | None]:
    """Generate a unique SKU. Returns (sku, original_proposal).

    - Tries the source_id-derived 3-digit suffix first (preserves traceability)
    - On collision (with `used` or `blocked`), walks 000-999 sequentially and
      picks the first slot that's free. Fresh 3-digit number — no dash suffix.
    - Falls back to 4-digit if all 1000 3-digit slots are taken (very unlikely)

    `used` is mutated (the chosen SKU is added).
    `blocked` is read-only — pass existing Shopify SKUs here so we avoid them.

    The returned `original_proposal` is non-None only when the source-id-derived
    SKU collided, indicating a rename happened. Used for the collision log.
    """
    prefix = brand_prefix(brand)
    if invoice_date:
        try:
            yymm = datetime.fromisoformat(invoice_date).strftime("%y%m")
        except ValueError:
            yymm = datetime.now().strftime("%y%m")
    else:
        yymm = datetime.now().strftime("%y%m")

    blocked_all = used | (blocked or set())

    # 1) Try source-id-derived suffix
    proposal: str | None = None
    sid_suffix = _source_id_suffix(source_id)
    if sid_suffix:
        proposal = f"{prefix}_{yymm}_{sid_suffix}"
        if proposal not in blocked_all:
            used.add(proposal)
            return proposal, None  # no rename

    # 2) Walk 000-999 for the first free 3-digit slot
    for n in range(1000):
        candidate = f"{prefix}_{yymm}_{n:03d}"
        if candidate not in blocked_all:
            used.add(candidate)
            return candidate, proposal  # proposal is the would-have-been SKU

    # 3) Escalate to 4-digit (1000 SKUs in one brand+month is unusual)
    for n in range(1000, 10000):
        candidate = f"{prefix}_{yymm}_{n}"
        if candidate not in blocked_all:
            used.add(candidate)
            return candidate, proposal

    raise RuntimeError(
        f"All SKU slots exhausted for {prefix}_{yymm}_*. "
        f"This shouldn't happen — check for broken state."
    )


def make_handle(title: str, sku: str) -> str:
    """Build a unique, Shopify-compatible Handle.

    Strategy: slugified title for SEO + the SKU appended for uniqueness.
    Same title across multiple items → still distinct handles.
    Shopify handle rules: lowercase, alphanumerics + hyphens only, max 255 chars.

    Examples:
      title='80s Burberry Beige Trench Coat', sku='BURB_2604_X9K'
        → '80s-burberry-beige-trench-coat-burb-2604-x9k'
    """
    import re
    # 1. Lowercase + replace any non-alphanumeric run with a single hyphen
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    sku_slug = re.sub(r"[^a-z0-9]+", "-", (sku or "").lower()).strip("-")
    handle = f"{base}-{sku_slug}" if base and sku_slug else (sku_slug or base or "untitled")
    # Shopify max handle length is 255; truncate at the last hyphen below that
    if len(handle) > 255:
        handle = handle[:255].rsplit("-", 1)[0]
    return handle


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

HEADER = [
    # Handle FIRST — Shopify treats this as the product-grouping key. Two rows
    # with the same handle become variants of one product. For 1-of-1 vintage
    # inventory we need a unique handle per row, otherwise titles like
    # "80's Burberry Beige ... Trench Coat" appearing on multiple items get
    # silently merged on import.
    "Handle",
    "Title", "Body (HTML)", "Vendor", "Product Category", "Type", "Tags",
    "Variant Inventory Tracker", "Cost per Item", "Inventory quantity",
    "Variant Price", "Status", "SKU", "Option1 Name", "Option1 Value",
    "Option1 Linked To", "Published",
    # Internal — stripped with --strip-internal
    "_Markup", "_Base Price", "_source_file",
]


def make_title(item: dict, is_lot_unit: bool = False) -> str:
    """Build Shopify title using the canonical compose_title() helper."""
    # Coerce dict → LineItem so compose_title can read typed fields
    # (item dict may have extra keys like pricing_result, which pydantic ignores).
    allowed = LineItem.model_fields.keys()
    li = LineItem(**{k: v for k, v in item.items() if k in allowed})
    title = compose_title(li)
    if is_lot_unit:
        title = f"[REVIEW] {title}"
    return title


def make_tags(invoice: dict, item: dict) -> str:
    """Shopify Tags column — intentionally blank for now.

    We have `search_keywords()` in extractors.py that builds a full SEO tag
    set from an item's structured fields. Re-enable when you want on-site
    filtering / SEO — just replace this with a call to it.
    """
    return ""


def item_to_rows(
    item: dict,
    invoice: dict,
    source_file: str,
    used_skus: set[str],
    used_handles: set[str] | None = None,
    existing_skus: set[str] | None = None,
    existing_handles: set[str] | None = None,
    collision_log: list | None = None,
) -> list[dict]:
    """Expand one item into one row per unit (spec §13: lot expansion).

    Collision-aware:
      - existing_skus / existing_handles: snapshot of what's already in
        Shopify (from shopify_inventory.refresh_inventory). When provided,
        we disambiguate any new SKU/handle that collides.
      - used_skus / used_handles: per-export sets, mutated as we go so we
        also avoid intra-export collisions.
      - collision_log: optional list — we append a dict per disambiguation
        event so the caller can show the user what was renamed.
    """
    from shopify_inventory import disambiguate

    if used_handles is None:
        used_handles = set()
    if existing_skus is None:
        existing_skus = set()
    if existing_handles is None:
        existing_handles = set()

    pricing = item.get("pricing_result", {})
    cost_per_unit = item.get("cost_breakdown", {}).get("unit_cost_usd", 0)
    qty = max(int(item.get("quantity", 1)), 1)
    category = shopify_category(item.get("product_type"))
    canon_t = canon_type(item.get("product_type")) or ""
    vendor = (
        item.get("override_vendor")
        or canon_brand(item.get("detected_brand"))
        or "Vintage"
    )

    rows = []
    existing_handles_lower = {h.lower() for h in existing_handles}

    for unit_idx in range(qty):
        is_lot_unit = qty > 1

        # Generate SKU — make_sku now picks a fresh 3-digit from the start
        # if the source-id-derived suffix would collide with anything in
        # used_skus or existing_skus. Returns (final_sku, original_proposal)
        # where original_proposal is non-None only when a rename happened.
        sku, original_proposal = make_sku(
            vendor, invoice.get("invoice_date"), item.get("source_id"),
            used_skus, blocked=existing_skus,
        )
        if original_proposal and original_proposal != sku and collision_log is not None:
            collision_log.append({
                "kind": "sku",
                "source_id": item.get("source_id"),
                "proposed": original_proposal,
                "renamed_to": sku,
                "reason": "already in Shopify" if original_proposal in existing_skus
                          else "duplicate within export",
            })

        # Handle is derived from title + final SKU — since SKU is now unique
        # against live + intra-export, the handle inherits that uniqueness.
        # Keep the disambiguator as a defensive fallback (e.g. against a
        # hand-created Shopify product with the exact same handle).
        title = make_title(item, is_lot_unit=is_lot_unit)
        proposed_handle = make_handle(title, sku).lower()
        handle = disambiguate(proposed_handle, used_handles | existing_handles_lower)
        if handle != proposed_handle and collision_log is not None:
            collision_log.append({
                "kind": "handle",
                "source_id": item.get("source_id"),
                "proposed": proposed_handle,
                "renamed_to": handle,
                "reason": "already in Shopify" if proposed_handle in existing_handles_lower
                          else "duplicate within export",
            })

        rows.append({
            "Handle": handle,
            "Title": title,
            "Body (HTML)": "",
            "Vendor": vendor,
            "Product Category": category,
            "Type": canon_t,
            "Tags": make_tags(invoice, item),
            "Variant Inventory Tracker": "shopify",
            "Cost per Item": f"{cost_per_unit:.2f}" if cost_per_unit else "",
            "Inventory quantity": 1,
            "Variant Price": pricing.get("rounded_price", "") or "",
            "Status": "draft",
            "SKU": sku,
            "Option1 Name": "",
            "Option1 Value": "",
            "Option1 Linked To": "",
            "Published": "",
            "_Markup": f"{pricing.get('markup', 0):.3f}",
            "_Base Price": f"{pricing.get('base_price', 0):.2f}",
            "_source_file": source_file,
        })
    return rows


def load_priced(path: Path) -> list[dict]:
    """Load a priced JSON (dict) along with its source filename."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["__source_file"] = path.name
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Priced JSON file or folder")
    parser.add_argument("-o", "--out", type=Path, default=Path("shopify.csv"))
    parser.add_argument("--strip-internal", action="store_true",
                        help="Drop `_Markup` / `_Base Price` / `_source_file` columns")
    args = parser.parse_args()

    # Collect sources
    if args.source.is_file():
        sources = [args.source]
    elif args.source.is_dir():
        sources = sorted(args.source.glob("*.json"))
    else:
        print(f"Not a JSON file or directory: {args.source}", file=sys.stderr)
        return 1
    if not sources:
        print("No priced JSONs found. Did you run price.py first?", file=sys.stderr)
        return 1

    used_skus: set[str] = set()
    all_rows: list[dict] = []
    for src in sources:
        data = load_priced(src)
        if "pricing_result" not in (data.get("items", [{}])[0] or {}):
            print(f"⚠  {src.name} doesn't look priced (no pricing_result on first item). Run price.py first.", file=sys.stderr)
            continue
        for item in data["items"]:
            all_rows.extend(item_to_rows(item, data, data["__source_file"], used_skus))

    header = HEADER.copy()
    if args.strip_internal:
        header = [h for h in header if not h.startswith("_")]

    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows from {len(sources)} priced invoice(s) → {args.out}", file=sys.stderr)
    if args.strip_internal:
        print("  (internal _columns stripped)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
