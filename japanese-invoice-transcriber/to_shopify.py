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
# SKU generation — {BRAND_3}_{YYMM}_{3-digit random integer}
# ---------------------------------------------------------------------------
#
# Format (per user preference 2026-06):
#   {brand-prefix 2-3 letters}_{YYMM}_{NNN}
#   e.g.  LV_2606_417   CHA_2606_032   ISM_2605_881   UNK_2606_205
#
# - Brand prefix: 2-3 uppercase letters from the canonical brand name, with
#   well-known overrides (LV, DG, DIO, YSL, BV, ISM, …) so multi-word brands
#   collapse to the obvious initialism instead of the first 3 letters.
# - YYMM: month of the invoice. Lets you eyeball "from which buy batch?"
# - Last 3: random integer (NOT derived from source-id letters). Stable per
#   source_id within an invoice so the Pricing tab's data_editor doesn't
#   thrash across reruns (see _stable_suffix_for note below).

BRAND_PREFIX_OVERRIDES = {
    "Louis Vuitton": "LV", "Dolce & Gabbana": "DG",
    "Christian Dior": "DIO", "Yves Saint Laurent": "YSL",
    "Saint Laurent": "SL", "Bottega Veneta": "BV",
    "Miu Miu": "MIU", "Issey Miyake": "ISM",
}


def brand_prefix(brand: str | None) -> str:
    """Return the 2-3 letter SKU prefix for a brand.

    - Canonicalize via canon_brand so "louis vuitton" → "Louis Vuitton" hits
      the LV override.
    - Fall back to the first 3 alpha chars uppercased (e.g. "Burberry" → "BUR").
    - "UNK" when no brand is detected (Vintage items, etc.).
    """
    if not brand:
        return "UNK"
    b = canon_brand(brand) or brand
    if b in BRAND_PREFIX_OVERRIDES:
        return BRAND_PREFIX_OVERRIDES[b]
    letters = "".join(c for c in b if c.isalpha())[:3].upper()
    return letters or "UNK"


def _stable_suffix_for(source_id: str) -> int:
    """Hash source_id → stable integer in [0, 999].

    "Random" to a human eye but the SAME value on every render for the same
    source_id. Why deterministic matters: the Pricing tab rebuilds its
    DataFrame on every Streamlit rerun (cell focus, button click, etc.).
    If the SKU column shifted between renders, st.data_editor would notice
    "underlying data changed" and silently drop the user's in-flight cell
    edit — so typing a new Variant Price and tabbing out would visibly
    reset the cell.

    BLAKE2b chosen over Python's hash() because hash() varies across
    Python sessions (PYTHONHASHSEED randomization), which would make SKUs
    flicker across restarts.
    """
    import hashlib
    h = hashlib.blake2b(source_id.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "big") % 1000


def random_suffix(source_id: str | None, _used: set[str]) -> str:
    """Deprecated — make_sku() builds suffixes inline via _stable_suffix_for.

    Kept as a thin shim so any external caller doesn't break. Returns a
    deterministic suffix when source_id is given; otherwise sequential.
    """
    if source_id:
        return f"{_stable_suffix_for(source_id):03d}"
    n = 0
    while f"{n:03d}" in _used:
        n += 1
    return f"{n:03d}"


def make_sku(
    brand: str | None,
    invoice_date: str | None,
    source_id: str | None,
    used: set[str],
    blocked: set[str] | None = None,
) -> tuple[str, str | None]:
    """Generate a stable {PREFIX}_{YYMM}_{NNN} SKU. Returns (sku, original_proposal).

    NNN is a hash-derived random-looking 3-digit integer (NOT letters from
    source_id). Same source_id → same SKU on every call within a session,
    so the Pricing-tab data_editor doesn't reset cell edits across reruns.

    Collision handling:
      - If the deterministic candidate collides with `used` or `blocked`,
        walk forward through 000-999 until a free slot is found.
      - Falls back to 4-digit suffix if all 1000 3-digit slots are taken
        for this brand+month (1000 SKUs from one brand in one month is
        unusual; the escalation keeps us collision-safe regardless).

    `original_proposal` is non-None only when a collision-walk happened —
    used by the audit log to record "would have been X, became Y".

    `used` is mutated (the chosen SKU is added).
    `blocked` is read-only — pass live Shopify SKUs here to avoid them.
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
    seed = source_id or "_anonymous"
    base = _stable_suffix_for(seed)

    # 1) Try the deterministic 3-digit slot; collision-walk forward through 000-999
    proposal: str | None = None
    for offset in range(1000):
        n = (base + offset) % 1000
        candidate = f"{prefix}_{yymm}_{n:03d}"
        if candidate not in blocked_all:
            if offset > 0:
                # First-pick collided — capture for the audit log
                proposal = f"{prefix}_{yymm}_{base:03d}"
            used.add(candidate)
            return candidate, proposal

    # 2) All 1000 3-digit slots taken — escalate to 4-digit
    proposal = f"{prefix}_{yymm}_{base:03d}"
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
    templates: list | None = None,
    taxonomy: list | None = None,
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
    canon_t = canon_type(item.get("product_type")) or ""

    # Category: prefer the live Shopify taxonomy suggester (matches what the
    # Step 1 audit recommends). Falls back to the legacy SHOPIFY_CATEGORY
    # dict when the suggester can't resolve a leaf (no fashion noun in title).
    category = ""
    if taxonomy:
        try:
            from shopify_taxonomy import suggest_category_for_product
            hit = suggest_category_for_product(
                title=item.get("description_english") or "",
                product_type=item.get("product_type") or "",
                tags="",
                taxonomy=taxonomy,
            )
            if hit:
                category = hit.get("full_name", "")
        except Exception:
            pass
    if not category:
        category = shopify_category(item.get("product_type"))

    # Body (HTML): use the matching description template's blank skeleton so
    # the exported CSV passes the description audit on re-import. Falls back
    # to empty string when no template covers this category.
    body_html = ""
    if templates and category:
        try:
            from heuristics import find_template_for_category
            tpl = find_template_for_category(category, templates)
            if tpl and tpl.template:
                body_html = tpl.template
        except Exception:
            pass
    vendor = (
        item.get("override_vendor")
        or canon_brand(item.get("detected_brand"))
        or "Vintage"
    )

    rows = []
    existing_handles_lower = {h.lower() for h in existing_handles}

    for unit_idx in range(qty):
        is_lot_unit = qty > 1

        # Generate SKU — make_sku now returns a random 7-digit integer
        # (e.g. "4827193"), collision-checked against used_skus + existing_skus.
        # `original_proposal` is always None under the new scheme (random
        # picks don't have a "would have been" alternative), so the rename
        # branch below is effectively dead. Kept as-is in case the SKU rule
        # ever swings back toward deterministic suffixes.
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
            "Body (HTML)": body_html,
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
