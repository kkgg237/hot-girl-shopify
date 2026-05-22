"""Pricing module — implements SPEC.md §7–14.

Pure functions, no I/O. Input: an item + its cost. Output: a PricingResult
with markup, base price, band clamps, adjustments, and the final rounded price.

Call `price_item(item, view, demand=1.0)` to get a full breakdown.
Also exposes `compose_title()` for Shopify-format product titles per SPEC §15.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from costs import InvoiceView, LineItem


# ---------------------------------------------------------------------------
# Constants from SPEC.md §12
# ---------------------------------------------------------------------------

# Brand tier sets — source of truth: heuristics/rules.yaml (tier_brands).
# Fallbacks below mirror current state for resilience if YAML loader fails.
_LUXURY_BRANDS_FALLBACK = {
    "Dolce & Gabbana", "Yves Saint Laurent", "Prada", "Gucci", "Chanel",
    "Louis Vuitton", "Burberry", "Fendi", "Valentino", "Versace", "Hermès",
    "Dior", "Christian Dior", "Givenchy", "Balenciaga", "Bottega Veneta",
    "Celine", "Loewe", "Salvatore Ferragamo", "Moncler", "Miu Miu", "Saint Laurent",
}

_MID_TIER_BRANDS_FALLBACK = {
    "Sonia Rykiel", "Marella", "Missoni", "Max Mara", "Giorgio Armani",
    "Emporio Armani", "Ralph Lauren", "Marc Jacobs", "Coach", "Michael Kors",
    "Emanuel Ungaro", "Issey Miyake",
}

try:
    from heuristics import RULES as _RULES_TIER
    LUXURY_BRANDS = _RULES_TIER.luxury_brands() or _LUXURY_BRANDS_FALLBACK
    MID_TIER_BRANDS = _RULES_TIER.mid_tier_brands() or _MID_TIER_BRANDS_FALLBACK
except Exception as _e:  # pragma: no cover
    print(f"[pricing] heuristics load failed for tiers ({_e}); using fallbacks")
    LUXURY_BRANDS = _LUXURY_BRANDS_FALLBACK
    MID_TIER_BRANDS = _MID_TIER_BRANDS_FALLBACK

PREMIUM_FURS = {"Mink", "Fox Fur", "Weasel Fur", "Raccoon Fur", "Squirrel Fur", "Rabbit Fur"}


# ---------------------------------------------------------------------------
# Canonicalization — the LLM doesn't always match the spec's exact casing
# ---------------------------------------------------------------------------

# Canonicalization maps — source of truth: heuristics/rules.yaml (canonicalize).
# Fallbacks below for resilience if YAML loader fails. The LLM doesn't always
# match the spec's exact casing, so we normalize via these tables.
_BRAND_ALIASES_FALLBACK = {
    "chanel": "Chanel", "gucci": "Gucci", "prada": "Prada", "fendi": "Fendi",
    "celine": "Celine", "céline": "Celine", "burberry": "Burberry",
    "louis vuitton": "Louis Vuitton", "lv": "Louis Vuitton",
    "issey miyake": "Issey Miyake",
    "dolce & gabbana": "Dolce & Gabbana", "dolce&gabbana": "Dolce & Gabbana",
    "d&g": "Dolce & Gabbana", "dg": "Dolce & Gabbana",
    "yves saint laurent": "Yves Saint Laurent", "ysl": "Yves Saint Laurent",
    "saint laurent": "Saint Laurent",
    "christian dior": "Christian Dior", "dior": "Christian Dior",
    "valentino": "Valentino", "versace": "Versace",
    "hermès": "Hermès", "hermes": "Hermès",
    "givenchy": "Givenchy", "balenciaga": "Balenciaga",
    "bottega veneta": "Bottega Veneta", "loewe": "Loewe",
    "salvatore ferragamo": "Salvatore Ferragamo", "ferragamo": "Salvatore Ferragamo",
    "moncler": "Moncler", "miu miu": "Miu Miu",
    "sonia rykiel": "Sonia Rykiel", "marella": "Marella", "missoni": "Missoni",
    "max mara": "Max Mara", "giorgio armani": "Giorgio Armani",
    "emporio armani": "Emporio Armani", "armani": "Giorgio Armani",
    "ralph lauren": "Ralph Lauren", "marc jacobs": "Marc Jacobs",
    "coach": "Coach", "michael kors": "Michael Kors",
    "emanuel ungaro": "Emanuel Ungaro",
}

_TYPE_ALIASES_FALLBACK = {
    "handbag": "Handbag", "hand bag": "Handbag", "hand-bag": "Handbag",
    "shoulder bag": "Shoulder Bag", "shoulderbag": "Shoulder Bag",
    "clutch bag": "Clutch Bag", "clutch": "Clutch",
    "tote bag": "Tote Bag", "tote": "Tote Bag",
    "hobo bag": "Hobo Bag", "hobo": "Hobo Bag",
    "duffle bag": "Duffle Bag", "duffle": "Duffle Bag",
    "duffel bag": "Duffle Bag", "duffel": "Duffle Bag",
    "boston bag": "Boston Bag", "boston": "Boston Bag",
    "travel bag": "Travel Bag", "weekender": "Weekender",
    "weekender bag": "Weekender", "keepall": "Duffle Bag",
    "backpack": "Backpack",
    "crossbody": "Crossbody Bag", "crossbody bag": "Crossbody Bag",
    "pouch": "Pouch", "belt bag": "Belt Bag", "waist bag": "Belt Bag",
    "bag": "Bag", "handbag / pouch": "Handbag",
    "wallet": "Wallet", "long wallet": "Wallet", "coin purse": "Wallet",
    "card holder": "Card Holder", "card case": "Card Holder",
    "key case": "Key Holder", "key holder": "Key Holder",
    "sunglasses": "Sunglasses", "eyewear": "Sunglasses",
    "belt": "Belt", "scarf": "Scarf", "shawl": "Scarf", "stole": "Scarf",
    "coat": "Coat", "trench coat": "Trench Coat", "fur coat": "Fur Coat",
    "jacket": "Jacket", "leather jacket": "Leather Jacket", "blazer": "Blazer",
    "dress": "Dress", "one-piece": "Dress", "one piece": "Dress",
    "top": "Top", "shirt": "Shirt", "blouse": "Top", "t-shirt": "T-Shirt",
    "tshirt": "T-Shirt", "cardigan": "Cardigan", "sweater": "Sweater", "vest": "Vest",
    "skirt": "Skirt", "pants": "Pants", "trousers": "Pants", "jeans": "Pants",
    "ballet flats": "Ballet Flats", "ballerinas": "Ballet Flats",
    "flats": "Flats", "mules": "Mules", "slides": "Slides",
    "heels": "Heels", "pumps": "Pumps", "stilettos": "Heels",
    "boots": "Boots", "ankle boots": "Ankle Boots", "booties": "Ankle Boots",
    "sneakers": "Sneakers", "trainers": "Sneakers",
    "sandals": "Sandals", "loafers": "Loafers",
    "shoes": "Shoes",
}

try:
    from heuristics import RULES as _RULES_CANON
    _BRAND_ALIASES = _RULES_CANON.brand_aliases() or _BRAND_ALIASES_FALLBACK
    _TYPE_ALIASES = _RULES_CANON.type_aliases() or _TYPE_ALIASES_FALLBACK
except Exception as _e:  # pragma: no cover
    print(f"[pricing] heuristics load failed for canon ({_e}); using fallbacks")
    _BRAND_ALIASES = _BRAND_ALIASES_FALLBACK
    _TYPE_ALIASES = _TYPE_ALIASES_FALLBACK


def canon_brand(brand: Optional[str]) -> Optional[str]:
    """Return the canonical casing for a brand. Unknown brands are title-cased."""
    if not brand:
        return None
    b = brand.strip()
    if not b:
        return None
    hit = _BRAND_ALIASES.get(b.lower())
    if hit:
        return hit
    # Fallback: title-case (so "CHANEL" → "Chanel", "lisa conte" → "Lisa Conte")
    return " ".join(w[:1].upper() + w[1:].lower() for w in b.split())


def canon_type(ptype: Optional[str]) -> Optional[str]:
    if not ptype:
        return None
    return _TYPE_ALIASES.get(ptype.strip().lower(), ptype.strip().title())


def brand_tier(brand: Optional[str]) -> str:
    b = canon_brand(brand)
    if b in LUXURY_BRANDS:
        return "luxury"
    if b in MID_TIER_BRANDS:
        return "mid"
    return "standard"


# ---------------------------------------------------------------------------
# §8 — Price rounding: UP to 25 / 45 / 75 / 95 per $100 bracket
# ---------------------------------------------------------------------------

def round_price(price: float) -> int:
    if price <= 0:
        return 0
    price_points = (25, 45, 75, 95)
    base_hundred = int(price // 100) * 100
    remainder = price % 100
    next_point = next((p for p in price_points if p >= remainder), None)
    if next_point is None:
        # Spec: roll over to next hundred + 25, e.g. $198 → $225
        return base_hundred + 100 + 25
    return max(base_hundred + next_point, 25)


# ---------------------------------------------------------------------------
# §9 — BrandStreet markup: linear interpolation
# ---------------------------------------------------------------------------

def _lerp_markup(cost: float, c_lo: float, c_hi: float, m_hi: float, m_lo: float) -> float:
    """Higher cost → lower multiplier (linear interpolation)."""
    span = max(c_hi - c_lo, 1)
    t = max(0.0, min(1.0, (cost - c_lo) / span))
    return m_hi - t * (m_hi - m_lo)


BS_BAG_TYPES = {"Handbag", "Shoulder Bag", "Clutch Bag", "Clutch"}
BS_POUCH_TYPES = {"Pouch", "Wallet", "Bag"}

BS_BRAND_BANDS = {
    ("Louis Vuitton", "Handbag"):      (600, 900),
    ("Louis Vuitton", "Shoulder Bag"): (600, 900),
    ("Louis Vuitton", "Clutch Bag"):   (600, 900),
    ("Louis Vuitton", "Clutch"):       (600, 900),
    ("Fendi",         "Handbag"):      (500, 900),
    ("Fendi",         "Shoulder Bag"): (500, 900),
    ("Fendi",         "Clutch Bag"):   (500, 900),
    ("Fendi",         "Clutch"):       (500, 900),
    ("Prada",         "Handbag"):      (600, 900),
    ("Prada",         "Shoulder Bag"): (600, 900),
    ("Prada",         "Clutch Bag"):   (600, 900),
    ("Prada",         "Clutch"):       (600, 900),
    ("Gucci",         "Handbag"):      (600, 900),
    ("Gucci",         "Shoulder Bag"): (600, 900),
    ("Gucci",         "Clutch Bag"):   (600, 900),
    ("Gucci",         "Clutch"):       (600, 900),
}


def brandstreet_markup(cost: float, item_type: str, vendor: Optional[str]) -> float:
    """Spec §9. `cost` = unit_cost_usd (already includes 1.2×1.15)."""
    t = canon_type(item_type) or ""
    tier = brand_tier(vendor)

    if t == "Sunglasses":
        return _lerp_markup(cost, 100, 400, 2.5, 1.8)
    if t in BS_BAG_TYPES:
        if tier == "luxury":
            return _lerp_markup(cost, 150, 800, 2.2, 1.65)
        return _lerp_markup(cost, 150, 600, 2.3, 1.6)
    if t in BS_POUCH_TYPES:
        return _lerp_markup(cost, 100, 500, 2.0, 1.5)
    if tier == "luxury":
        return _lerp_markup(cost, 150, 600, 2.2, 1.5)
    if tier == "mid":
        return _lerp_markup(cost, 100, 500, 2.3, 1.6)
    return _lerp_markup(cost, 100, 500, 2.5, 1.7)


def brandstreet_band(price: float, item_type: str, vendor: Optional[str]) -> tuple[float, tuple[Optional[int], Optional[int]]]:
    """Apply BrandStreet floors/ceilings. Returns (clamped_price, (floor, ceil))."""
    t = canon_type(item_type) or ""
    v = canon_brand(vendor)
    if t == "Sunglasses":
        return (min(price, 495), (None, 495))
    key = (v, t)
    if key in BS_BRAND_BANDS:
        floor, ceil = BS_BRAND_BANDS[key]
        return (max(floor, min(ceil, price)), (floor, ceil))
    return (max(525, price), (525, None))  # global floor


# ---------------------------------------------------------------------------
# §10 — Buyee markup: additive
# ---------------------------------------------------------------------------

def buyee_markup(material: Optional[str], vendor: Optional[str], garment_length: Optional[str]) -> float:
    """Spec §10. Base 4.0 + material + brand tier + garment length."""
    m = material or ""
    base = 4.0
    if m in PREMIUM_FURS:
        base = 6.0
    elif m in ("Shearling", "Fur", "Sheepskin", "Lambskin"):
        base = 5.5
    elif m in ("Leather", "Suede", "Pony Hair", "Goat Leather"):
        base = 5.0
    elif m in ("Silk", "Satin", "Cashmere"):
        base = 4.5
    elif m in ("Denim", "Cotton"):
        base = 4.0
    # else default 4.0

    tier = brand_tier(vendor)
    if tier == "luxury":
        base += 1.0
    elif tier == "mid":
        base += 0.5

    if garment_length == "long":
        base += 0.5
    elif garment_length == "midi":
        base += 0.25

    return base


def buyee_band(price: float, item_type: str, material: Optional[str], vendor: Optional[str]) -> tuple[float, tuple[Optional[int], Optional[int]]]:
    """Spec §10 floors/ceilings per item_type × material × brand tier."""
    t = canon_type(item_type) or ""
    m = material or ""
    tier = brand_tier(vendor)

    if t == "Coat":
        if m in PREMIUM_FURS:
            floor, ceil = 400, 600
        elif m in ("Shearling", "Fur", "Sheepskin", "Lambskin"):
            floor, ceil = 350, 550
        elif m in ("Leather", "Suede", "Goat Leather"):
            floor, ceil = 300, 500
        else:
            floor, ceil = 250, 450
    elif t == "Jacket":
        if m in ("Shearling", "Fur", "Leather") or m in PREMIUM_FURS:
            floor, ceil = 250, 400
        else:
            floor, ceil = 150, 350
    elif t in ("Top", "Sweater", "Cardigan", "Blouse", "Vest"):
        floor, ceil = 80, 250
    elif t in ("Pants", "Skirt"):
        floor, ceil = 100, 350
    elif t == "Dress":
        floor, ceil = 150, 450
    elif t in ("Belt", "Scarf", "Stole", "Shawl"):
        floor, ceil = 80, 250
    elif t in ("Bag", "Handbag", "Clutch", "Pouch", "Shoulder Bag", "Tote Bag", "Hobo Bag", "Belt Bag"):
        if tier == "luxury":
            floor, ceil = 300, 800
        else:
            floor, ceil = 150, 400
    else:
        return (price, (None, None))  # no band

    return (max(floor, min(ceil, price)), (floor, ceil))


# ---------------------------------------------------------------------------
# §10 — Market adjustment multipliers
# ---------------------------------------------------------------------------

MARKET_ADJUSTMENTS: dict[tuple[str, str], float] = {
    ("Prada",              "Handbag"):  1.10,
    ("Prada",              "Clutch"):   1.08,
    ("Prada",              "Pouch"):    1.05,
    ("Miu Miu",            "Clutch"):   1.08,
    ("Miu Miu",            "Handbag"):  1.08,
    ("Hermès",             "Handbag"):  1.15,
    ("Hermès",             "Clutch"):   1.12,
    ("Hermès",             "Scarf"):    1.15,
    ("Hermès",             "Stole"):    1.12,
    ("Chanel",             "Handbag"):  1.12,
    ("Chanel",             "Clutch"):   1.10,
    ("Burberry",           "Scarf"):    0.80,
    ("Burberry",           "Stole"):    0.85,
    ("Issey Miyake",       "Skirt"):    1.15,
    ("Issey Miyake",       "Dress"):    1.15,
    ("Issey Miyake",       "Top"):      1.10,
    ("Issey Miyake",       "Pants"):    1.10,
    ("Issey Miyake",       "Coat"):     1.10,
    ("Yves Saint Laurent", "Sweater"):  1.05,
    ("Yves Saint Laurent", "Jacket"):   1.10,
    ("Yves Saint Laurent", "Coat"):     1.08,
    ("Yves Saint Laurent", "Handbag"):  1.10,
    ("Vintage",            "Coat"):     0.95,
    ("Vintage",            "Jacket"):   0.97,
    ("Fendi",              "Handbag"):  1.08,
    ("Fendi",              "Clutch"):   1.05,
    ("Giorgio Armani",     "Dress"):    1.05,
    ("Giorgio Armani",     "Jacket"):   1.05,
}


def market_adjustment(vendor: Optional[str], item_type: Optional[str]) -> float:
    v = canon_brand(vendor) or ""
    t = canon_type(item_type) or ""
    return MARKET_ADJUSTMENTS.get((v, t), 1.0)


# ---------------------------------------------------------------------------
# price_item — orchestrates it all
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Type → bracket category classifier
# ---------------------------------------------------------------------------
# Used to map canonical item_type → "apparel" / "bags" / "accessories",
# which is the second axis of the pricing_brackets table key
# (first axis is brand tier: luxury / mid / standard).

_BAG_TYPES = {
    "Bag", "Handbag", "Shoulder Bag", "Clutch Bag", "Clutch", "Tote Bag",
    "Hobo Bag", "Duffle Bag", "Duffel Bag", "Boston Bag", "Travel Bag",
    "Weekender", "Backpack", "Crossbody Bag", "Belt Bag",
}

_ACCESSORY_TYPES = {
    "Scarf", "Stole", "Shawl", "Belt", "Sunglasses", "Hat",
    "Pouch", "Wallet", "Card Holder", "Key Holder", "Key Case",
    "Coin Purse", "Tie", "Bow Tie", "Gloves",
}


def _categorize_type(item_type: Optional[str]) -> str:
    """Return 'bags' / 'accessories' / 'apparel' for a canonical item_type.

    Apparel is the default — clothing types (Jacket, Coat, Dress, Pants, Top,
    Skirt, Shirt, Vest, Set, etc.) all fall through to this bucket. Edit the
    explicit type sets above to reclassify.
    """
    if not item_type:
        return "apparel"
    if item_type in _BAG_TYPES:
        return "bags"
    if item_type in _ACCESSORY_TYPES:
        return "accessories"
    return "apparel"


def bracket_markup(
    cost: float, item_type: Optional[str], vendor: Optional[str], invoice_type: str,
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Look up the multiplier from `rules.yaml:pricing_brackets`.

    Args:
        cost: the value the multiplier will scale (landed USD).
        item_type: canonical item type (Jacket, Handbag, etc.).
        vendor: canonical brand name (used for tier lookup).
        invoice_type: "vendor_invoice" or "buyee" (matches YAML top-level key).

    Returns:
        (multiplier, target_margin, bracket_key) — first two are floats, third
        is the string key used (e.g. "luxury_apparel"), useful for UI display.
        All three are None if no matching bracket table exists — caller should
        fall back to the legacy markup function.
    """
    try:
        from heuristics import RULES as _R
    except Exception:
        return None, None, None

    if not getattr(_R, "pricing_brackets", None):
        return None, None, None

    tier = brand_tier(vendor)  # "luxury" / "mid" / "standard"
    category = _categorize_type(item_type)
    key = f"{tier}_{category}"

    bracket = _R.lookup_pricing_bracket(invoice_type, key, cost)
    if bracket is None:
        return None, None, None
    return bracket.multiplier, bracket.target_margin, key


@dataclass
class PricingResult:
    # Inputs (canonicalized)
    vendor: Optional[str]
    item_type: Optional[str]
    material: Optional[str]
    garment_length: Optional[str]
    brand_tier: str

    # Cost inputs
    unit_cost_usd: float
    invoice_type: str

    # Pricing steps
    markup: float
    markup_applied_to: float        # Buyee: cost × 1.2; BS: cost
    base_price: float               # markup_applied_to × markup
    band_floor: Optional[int] = None
    band_ceil: Optional[int] = None
    after_band: float = 0.0
    market_adjustment: float = 1.0
    after_adjustment: float = 0.0
    demand_multiplier: float = 1.0
    after_demand: float = 0.0
    rounded_price: int = 0

    # Diagnostics — set when the YAML bracket table is active so the UI can
    # show "how was this priced?" without the user having to read code.
    target_margin: Optional[float] = None    # 0.0-1.0
    bracket_key: Optional[str] = None        # e.g. "luxury_apparel"
    pricing_source: str = "legacy"           # "yaml_bracket" | "legacy"

    # Floor / ceiling diagnostics — flipped True when the corresponding guard
    # in pricing_floors was binding for this item (UI uses these to mark rows
    # with a ↑ / ↓ glyph in the Margin column).
    min_profit_applied: bool = False         # min_dollar_profit floor bumped price up
    max_markup_applied: bool = False         # max_markup_multiple ceiling clamped price down

    # Diagnostics
    warnings: list[str] = field(default_factory=list)


def price_item(item: LineItem, view: InvoiceView, demand: float = 1.0) -> PricingResult:
    """Compute the full pricing pipeline for one item.

    If `item.override_price` is set (manual UI edit), it's used as-is and the
    markup/band/adjustment pipeline is skipped — but all the diagnostic fields
    are still populated for the UI to show what *would* have been computed.
    """
    unit_cost_usd = view.unit_cost_usd(item)
    vendor = canon_brand(getattr(item, "override_vendor", None) or item.detected_brand)
    item_type = canon_type(item.product_type)
    tier = brand_tier(vendor)

    result = PricingResult(
        vendor=vendor,
        item_type=item_type,
        material=item.material,
        garment_length=item.garment_length,
        brand_tier=tier,
        unit_cost_usd=unit_cost_usd,
        invoice_type=view.inv.invoice_type,
        markup=0.0,
        markup_applied_to=0.0,
        base_price=0.0,
    )

    is_buyee = view.inv.invoice_type == "buyee_breakdown"

    # --- Markup ---
    # The base cost the multiplier scales differs by invoice type:
    #   BrandStreet → cost already includes 1.2×1.15 from costs.py
    #   Buyee §10  → multiplier applies to cost × 1.2 (actual_cost)
    if is_buyee:
        result.markup_applied_to = unit_cost_usd * 1.2
        invoice_key = "buyee"
    else:
        result.markup_applied_to = unit_cost_usd
        invoice_key = "vendor_invoice"

    # Try the YAML bracket table first (cost-function pricing per
    # rules.yaml:pricing_brackets). Falls back to the legacy lerp curves
    # (brandstreet_markup / buyee_markup) if no bracket is defined for this
    # (tier, category) — useful during rollout and as a safety net.
    bracket_mult, bracket_margin, bracket_key = bracket_markup(
        result.markup_applied_to, item_type, vendor, invoice_key,
    )

    if bracket_mult is not None:
        result.markup = bracket_mult
        result.target_margin = bracket_margin
        result.bracket_key = bracket_key
        result.pricing_source = "yaml_bracket"
    else:
        if is_buyee:
            result.markup = buyee_markup(item.material, vendor, item.garment_length)
        else:
            result.markup = brandstreet_markup(unit_cost_usd, item_type or "", vendor)
        result.pricing_source = "legacy"

    result.base_price = result.markup_applied_to * result.markup

    # --- Bands (DISABLED) ---
    # Bands (floor/ceiling) used to clamp the markup output, but the per-type
    # tables were misaligned with reality (binding floor on cheap apparel,
    # binding ceiling on designer pieces). Pricing is now purely
    # cost × markup × adjustment × demand → round. The band functions
    # (buyee_band / brandstreet_band) are intentionally left in the module so
    # they can be re-enabled later; PricingResult still carries band_floor /
    # band_ceil fields set to None for backward-compatible UI rendering.
    result.band_floor = None
    result.band_ceil = None
    result.after_band = result.base_price

    # --- Market adjustment ---
    result.market_adjustment = market_adjustment(vendor, item_type)
    result.after_adjustment = result.after_band * result.market_adjustment

    # --- Demand multiplier ---
    result.demand_multiplier = demand
    result.after_demand = result.after_adjustment * demand

    # --- Cost-relative floors / ceilings (rules.yaml:pricing_floors) ---
    # Applied AFTER demand but BEFORE rounding so:
    #   1. round_price still snaps cleanly to 25/45/75/95
    #   2. demand multiplier doesn't get cancelled by the floor
    #   3. the guards respect cost realistically (min_dollar_profit is
    #      always relative to the actual landed cost, not the markup output)
    try:
        from heuristics import RULES as _R
        floors = _R.pricing_floors.get(invoice_key) if _R.pricing_floors else None
    except Exception:
        floors = None

    if floors:
        cost = unit_cost_usd  # landed cost the floors compare against

        # Min dollar profit — bump UP if profit too small
        if floors.min_dollar_profit and cost > 0:
            min_target = cost + floors.min_dollar_profit
            if result.after_demand < min_target:
                result.after_demand = min_target
                result.min_profit_applied = True
                result.warnings.append(
                    f"Bumped UP to enforce ${floors.min_dollar_profit:.0f} "
                    f"min profit floor"
                )

        # Max markup multiple — clamp DOWN if too aggressive
        if floors.max_markup_multiple and cost > 0:
            max_target = cost * floors.max_markup_multiple
            if result.after_demand > max_target:
                result.after_demand = max_target
                result.max_markup_applied = True
                result.warnings.append(
                    f"Capped at {floors.max_markup_multiple}× cost ceiling"
                )

    # --- Round ---
    result.rounded_price = round_price(result.after_demand)

    # --- Manual price override (UI edit) wins everything ---
    override = getattr(item, "override_price", None)
    if override:
        result.warnings.append(f"Manual price override: ${override} (computed was ${result.rounded_price})")
        result.rounded_price = int(override)

    # Missing material / length warnings for garments where they matter
    if is_buyee and item_type in ("Coat", "Jacket") and not item.material:
        result.warnings.append("Missing material — markup may be undervalued")
    if is_buyee and item_type in ("Coat", "Dress", "Skirt") and not item.garment_length:
        result.warnings.append("Missing garment length")
    if not vendor:
        result.warnings.append("No brand detected")

    return result


# ---------------------------------------------------------------------------
# Shopify title composition (SPEC §15)
# ---------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Model-era database (spec suggestion #4)
# Known signature-era correlations for luxury models. Used to backfill `era`
# when the LLM/regex couldn't determine it from the description alone.
# Keys are (canon_brand, canon_model_substring) — lookup uses contains-match
# on the item's canonical brand + model_name.
# -----------------------------------------------------------------------------

# Source of truth: heuristics/rules.yaml (model_era section).
# We load via heuristics.loader so user edits to the YAML take effect on next
# Python start. The inline _MODEL_ERA_FALLBACK below mirrors the YAML and is
# only used if the YAML fails to load (missing file, parse error, etc.) — this
# keeps the pipeline running even when heuristics is broken.
_MODEL_ERA_FALLBACK: dict[tuple[str, str], str] = {
    # Louis Vuitton
    ("Louis Vuitton", "Monogram Vernis"):           "00's",
    ("Louis Vuitton", "Multicolor"):                "00's",
    ("Louis Vuitton", "Murakami"):                  "00's",
    ("Louis Vuitton", "Denim Speedy"):              "00's",
    ("Louis Vuitton", "Pochette Accessoires"):      "90's",
    ("Louis Vuitton", "Mini Pochette"):             "00's",
    ("Louis Vuitton", "Pochette Metis"):            "10's",
    # Fendi
    ("Fendi", "Mamma Baguette"):                    "90's",
    ("Fendi", "Baguette"):                          "90's",
    ("Fendi", "Zucca"):                             "90's",
    ("Fendi", "Zucchino"):                          "00's",
    ("Fendi", "Peekaboo"):                          "10's",
    # Chanel
    ("Chanel", "Choco Bar"):                        "00's",
    ("Chanel", "Cambon"):                           "00's",
    ("Chanel", "2.55"):                             "90's",
    ("Chanel", "Boy Bag"):                          "10's",
    # Dior
    ("Christian Dior", "Saddle"):                   "90's",
    ("Christian Dior", "Trotter"):                  "00's",
    ("Christian Dior", "Lady Dior"):                "00's",
    # Gucci
    ("Gucci", "Bamboo"):                            "00's",
    ("Gucci", "Jackie"):                            "00's",
    ("Gucci", "Sherry Line"):                       "00's",
    # Prada
    ("Prada", "Galleria"):                          "10's",
    ("Prada", "Nylon"):                             "90's",
    # Vivienne Tam
    ("Vivienne Tam", "Flower Mesh"):                "00's",
    ("Vivienne Tam", "Mesh"):                       "Y2K",
    # Celine
    ("Celine", "Macadam"):                          "00's",
    ("Celine", "Boogie"):                           "00's",
}

try:
    from heuristics import RULES as _RULES
    MODEL_ERA: dict[tuple[str, str], str] = _RULES.model_era_pairs() or _MODEL_ERA_FALLBACK
except Exception as _e:  # pragma: no cover — defensive: never break the pipeline
    print(f"[pricing] heuristics load failed ({_e}); using fallback MODEL_ERA")
    MODEL_ERA = _MODEL_ERA_FALLBACK


def lookup_model_era(brand: Optional[str], model_name: Optional[str]) -> Optional[str]:
    """Return a guessed era from the model-era DB. Match is brand-exact +
    model-substring (case-insensitive)."""
    if not brand or not model_name:
        return None
    cb = canon_brand(brand)
    m_low = model_name.lower()
    for (db_brand, db_model), era in MODEL_ERA.items():
        if cb == db_brand and db_model.lower() in m_low:
            return era
    return None


# Acronyms and short codes that should stay ALL-CAPS when title-casing.
# Source of truth: heuristics/rules.yaml (titles.acronyms_uppercase). Fallback below.
_ACRONYMS_FALLBACK = {
    "CC", "LV", "GG", "GC", "DG", "YSL", "MM", "II", "III", "IV", "V",
    "NY", "LA", "UV", "TV", "XS", "S", "M", "L", "XL", "XXL", "XXS", "MCM",
    "2WAY", "Y2K", "USA", "US", "UK", "JP", "EU", "F40", "FW", "SS",
    "CM", "MM2", "OS",
}
try:
    from heuristics import RULES as _RULES_AC
    _ACRONYMS = _RULES_AC.acronyms() or _ACRONYMS_FALLBACK
except Exception:  # pragma: no cover
    _ACRONYMS = _ACRONYMS_FALLBACK

# Words that stay lowercase in the middle of a title (articles, prepositions, conjunctions)
_MINOR_WORDS = {"a", "an", "the", "and", "or", "for", "in", "on", "at", "to", "of", "with", "from", "by"}

# Fragments we strip from descriptions — auth codes, model numbers, leftover tags
_STRIP_PATTERNS = [
    re.compile(r'\s+Auth\s+\w+\s*$', re.IGNORECASE),   # trailing "Auth XXXXX"
    re.compile(r'\s+LV\s+Auth\s+\w+\s*$', re.IGNORECASE),
    re.compile(r'\s+\[[A-Z0-9_-]{3,}\]\s*$'),           # trailing bracketed code
    re.compile(r'\s+■[A-Z0-9]+\s*$'),                   # trailing ■-prefix code
    re.compile(r'\s+\/[A-Z]{1,3}\s*$'),                 # trailing /XY code
    re.compile(r'\s+▼[A-Z0-9]+\s*$'),                   # trailing ▼-prefix code
]


def smart_title(s: str) -> str:
    """Title-case a string, preserving acronyms, digit-tokens, and hyphenated halves."""
    if not s:
        return s

    def cap_word(w: str, is_first: bool = False) -> str:
        stripped = w.strip(",./()[]")
        if stripped.upper() in _ACRONYMS:
            return w.upper()
        if any(c.isdigit() for c in stripped):
            return w
        if (not is_first) and stripped.lower() in _MINOR_WORDS:
            return w.lower()
        return w[:1].upper() + w[1:].lower()

    def split_compound(w: str) -> str:
        """Capitalize hyphen/slash-separated compounds segment by segment."""
        # Split on either - or / but keep the separators
        parts = re.split(r'([-/])', w)
        out_parts = []
        for j, p in enumerate(parts):
            if p in ("-", "/"):
                out_parts.append(p)
            else:
                out_parts.append(cap_word(p, is_first=True))
        return "".join(out_parts)

    out = []
    for i, w in enumerate(s.split()):
        if "-" in w or "/" in w:
            # First segment obeys position-in-sentence rule, rest get capitalized
            first_sep = min((w.index(c) for c in "-/" if c in w))
            head = w[:first_sep]
            rest = w[first_sep:]
            head_cased = cap_word(head, is_first=(i == 0))
            # Cap each subsequent segment
            sub_parts = re.split(r'([-/])', rest)
            sub_out = []
            for p in sub_parts:
                if p in ("-", "/"):
                    sub_out.append(p)
                elif p:
                    sub_out.append(cap_word(p, is_first=True))
            out.append(head_cased + "".join(sub_out))
        else:
            out.append(cap_word(w, is_first=(i == 0)))
    return " ".join(out)


def compose_title(item: LineItem) -> str:
    """Build a Shopify-ready product title using per-category templates (#7).

    Templates (all include model_name + size if present — biggest SEO lever):

      Bags        [Era] Brand [Model] [Size] [Color] [Pattern] [Material] Handbag-type
      Sunglasses  [Era] Brand [Model] [Frame color] [Pattern] Sunglasses
      Coats       [Era] Brand [Color] [Pattern] [Material] [Length] [Origin] Coat
      Dresses     [Era] Brand [Color] [Pattern] [Material] [Length] [Origin] Dress
      Shoes       [Era] Brand [Model] [Color] [Material] Shoes
      Default     [Era] Brand [Model] [Color] [Pattern] [Material] [Origin] [Length] Category

    Fills `era` from the MODEL_ERA DB if we have brand + model but no era yet.
    Pure structured concatenation — never leaks description text.

    Respects `item.override_title` if the user edited it in the UI.
    """
    # Manual override wins
    if getattr(item, "override_title", None):
        return item.override_title

    # Resolve canonical fields
    brand = canon_brand(item.detected_brand) or "Vintage"
    ptype = canon_type(item.product_type)
    era = (getattr(item, "era", None) or "").strip()
    color = (getattr(item, "color", None) or "").strip()
    pattern = (getattr(item, "pattern", None) or "").strip()
    material = (item.material or "").strip()
    origin = (getattr(item, "origin", None) or "").strip()
    length = (item.garment_length or "").strip()
    model = (getattr(item, "model_name", None) or "").strip()
    size = (getattr(item, "model_size", None) or "").strip()
    style = (getattr(item, "style_adjectives", None) or "").strip()

    # Era backfill from model DB (#4) — only if not explicitly set
    if not era and model and item.detected_brand:
        era = lookup_model_era(item.detected_brand, model) or ""

    # Title era gate. Two paths:
    #   1) 4-digit specific year ("1997") — always allowed in titles for any
    #      category. These are unambiguous and SEO-strong.
    #   2) Decade label ("90's", "00's") — allowed only for categories listed
    #      in rules.yaml:titles.era_policy.allow_decades_for. Searchers look
    #      up "90s Mamma Baguette" but rarely "90s top", so we gate this.
    #      Y2K and other non-numeric labels ('Vintage' etc.) are still dropped
    #      per user feedback (use numbers only).
    era_for_title = ""
    if era:
        try:
            from heuristics import RULES as _RULES_TITLE
            year_regex = _RULES_TITLE.titles.era_policy.allow_in_title_regex
            decade_categories = set(_RULES_TITLE.titles.era_policy.allow_decades_for)
        except Exception:
            year_regex = r"^\d{4}$"
            decade_categories = set()

        if re.fullmatch(year_regex, era):
            era_for_title = era
        elif ptype in decade_categories and re.fullmatch(r"\d\d's", era):
            era_for_title = era

    # Material gate: only "deluxe" materials (leather/exotic/fur/silk/cashmere/
    # etc.) appear in titles. Utility fabrics (cotton, polyester, nylon, denim,
    # generic wool, etc.) are stored on the item but dropped from the rendered
    # title — they don't add SEO/luxury value. Edit the allowlist in
    # heuristics/rules.yaml:titles.deluxe_materials to tune.
    if material:
        try:
            from heuristics import RULES as _RULES_MAT
            deluxe = _RULES_MAT.deluxe_materials()
        except Exception:
            deluxe = set()
        if deluxe and material.strip().lower() not in deluxe:
            material = ""

    # Template selection keyed on canonical type
    if ptype in {"Handbag", "Shoulder Bag", "Clutch Bag", "Tote Bag", "Hobo Bag",
                 "Duffle Bag", "Duffel Bag", "Boston Bag", "Travel Bag",
                 "Weekender", "Backpack", "Crossbody Bag",
                 "Pouch", "Belt Bag", "Bag", "Clutch"}:
        template = "BAG"
    elif ptype == "Sunglasses":
        template = "SUNGLASSES"
    elif ptype in {"Coat", "Trench Coat", "Fur Coat", "Jacket", "Leather Jacket", "Blazer"}:
        template = "OUTERWEAR"
    elif ptype == "Dress":
        template = "DRESS"
    elif ptype in {"Shoes", "Ballet Flats", "Flats", "Mules", "Slides",
                    "Heels", "Pumps", "Boots", "Ankle Boots", "Sneakers",
                    "Sandals", "Loafers"}:
        template = "SHOES"
    else:
        template = "DEFAULT"

    # Build ordered segment list per template — None/empty entries skipped
    segments: list[Optional[str]] = []

    if template == "BAG":
        segments = [
            era_for_title, brand,
            smart_title(model) if model else None,
            size.upper() if size else None,
            smart_title(color) if color else None,
            style if style else None,
            smart_title(pattern) if pattern else None,
            smart_title(material) if material else None,
            ptype,
        ]
    elif template == "SUNGLASSES":
        segments = [
            era_for_title, brand,
            smart_title(model) if model else None,
            smart_title(color) if color else None,
            smart_title(pattern) if pattern else None,
            ptype,
        ]
    elif template == "OUTERWEAR":
        segments = [
            era_for_title, brand,
            smart_title(color) if color else None,
            style if style else None,
            smart_title(pattern) if pattern else None,
            smart_title(material) if material else None,
            length.capitalize() if length else None,
            smart_title(origin) if origin else None,
            ptype,
        ]
    elif template == "DRESS":
        segments = [
            era_for_title, brand,
            smart_title(color) if color else None,
            style if style else None,
            smart_title(pattern) if pattern else None,
            smart_title(material) if material else None,
            length.capitalize() if length else None,
            smart_title(origin) if origin else None,
            ptype,
        ]
    elif template == "SHOES":
        segments = [
            era_for_title, brand,
            smart_title(model) if model else None,
            smart_title(color) if color else None,
            style if style else None,
            smart_title(material) if material else None,
            ptype,
        ]
    else:  # DEFAULT — covers Top, Shirt, Blouse, T-Shirt, Skirt, Pants, etc.
        segments = [
            era_for_title, brand,
            smart_title(model) if model else None,
            size.upper() if size else None,
            smart_title(color) if color else None,
            style if style else None,
            smart_title(pattern) if pattern else None,
            smart_title(material) if material else None,
            smart_title(origin) if origin else None,
            length.capitalize() if length else None,
            ptype,
        ]

    title = " ".join(s for s in segments if s)
    # Product-type tokens are sacred — never dedup them away. "Ballet Flats"
    # would otherwise lose "Ballet" if the title contains "Ballerina" earlier.
    ptype_tokens = len(ptype.split()) if ptype else 0
    return _dedup_title_tokens(title, protected_tail=ptype_tokens)


def _dedup_title_tokens(title: str, protected_tail: int = 0) -> str:
    """Collapse adjacent duplicate tokens and overlapping length-like tokens.

    Examples:
      "Pleated Pleated Denim"          → "Pleated Denim"
      "Knee-Length Fox Fur Knee Coat"  → "Knee-Length Fox Fur Coat"
      "Flower Mesh Floral"             → "Flower Mesh"
      "Chanel Ballerina ... Ballet Flats" with protected_tail=2 → preserved

    Strategy:
      1. Drop adjacent identical tokens (case-insensitive)
      2. Drop a later token whose lowercase form is a substring of an earlier
         token's lowercase form (handles "Knee" after "Knee-Length")
      3. Drop a later token if an earlier token shares its stem
         (handles "Floral" after "Flower")
      4. The last `protected_tail` tokens (the product type) are NEVER dropped
    """
    if not title:
        return title
    tokens = title.split()
    if len(tokens) < 2:
        return title

    n = len(tokens)
    protect_from_idx = n - protected_tail if protected_tail else n  # ≥ this index = protected

    out: list[str] = []
    seen_roots: set[str] = set()

    for i, tok in enumerate(tokens):
        low = tok.lower().rstrip(",.")
        if not low:
            continue
        is_protected = i >= protect_from_idx

        if not is_protected:
            # Skip exact-duplicate adjacent
            if out and out[-1].lower().rstrip(",.") == low:
                continue
            # Skip if substring of any earlier title token
            is_substring_of_earlier = any(
                low != prev.lower().rstrip(",.") and low in prev.lower().rstrip(",.").replace("-", " ").split()
                or low in prev.lower().split("-")
                for prev in out
            )
            if is_substring_of_earlier:
                continue
            # Skip if same word-root as earlier token (Flower → Floral)
            root = re.sub(r"(al|ed|ing|s)$", "", low)[:5]
            if root and len(root) >= 4 and root in seen_roots:
                continue
            seen_roots.add(root)

        out.append(tok)

    return " ".join(out)

