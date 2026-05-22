# PS Inventory — Cost Calculation & Pricing Specification

This document plus the accompanying code files contain everything needed to recreate the cost calculation and pricing system for a Shopify inventory tool supporting **BrandStreet** and **Buyee** invoice sources.

---

## 1. Architecture Overview

```
PDF → process_invoice.py → invoice_data dict
                                ↓
                       cost_calculator.py → List[InventoryItem]
                                ↓
                       data_formatter.py → Shopify CSV
```

Three files to implement:
- `process_invoice.py` — parses raw PDF text into structured invoice data
- `cost_calculator.py` — computes landed cost per item
- `data_formatter.py` — applies markup, generates titles, builds Shopify CSV

---

## 2. Invoice Sources

| Source | Detected by | Currency | Price structure |
|--------|-------------|----------|-----------------|
| **BrandStreet** | "Brand Street" in PDF text | USD | One price per item, all-in |
| **Buyee** | "Buyee" or "Shopping Site(ID)" in PDF | JPY | Item + domestic shipping + service fee; international shipping and customs split across all items |

Detection pattern (implement in `detect_and_parse(text)`):
```python
if 'Brand Street' in text or 'brand street' in text.lower():
    source = 'brandstreet'
elif 'Buyee' in text or 'Shopping Site(ID)' in text:
    source = 'buyee'
else:
    source = 'other'  # fallback: treat like brandstreet
```

---

## 3. InventoryItem Data Structure

```python
@dataclass
class InventoryItem:
    auction_id: str
    item_name: str                    # original (Japanese for Buyee)
    item_name_translated: str         # English
    quantity: int
    item_price_jpy: float
    domestic_shipping_jpy: float
    buyee_service_fee_jpy: float
    international_shipping_jpy: float
    customs_duty_jpy: float
    total_cost_jpy: float
    total_cost_usd: float
    unit_cost_usd: float
```

BrandStreet items leave all JPY fields as 0.

---

## 4. Exchange Rate

```
DEFAULT_RATE = 0.0067   # ≈149 JPY per $1 USD
USD = JPY × 0.0067
```

Pass as a constructor argument so it can be updated without code changes.

---

## 5. BrandStreet Cost Formula

```python
HANDLING_FACTOR   = 1.20   # handling/packaging
IMPORT_TAX_FACTOR = 1.15   # estimated 15% import tax

intrinsic_cost_usd = item_price_usd * HANDLING_FACTOR * IMPORT_TAX_FACTOR
unit_cost_usd      = intrinsic_cost_usd / quantity
```

No domestic shipping, service fee, or customs fields — they're baked into the purchase price.

---

## 6. Buyee Cost Formula

Shared costs (international shipping + customs) are split equally across all items on the invoice.

```python
# Fallback if international shipping not found in PDF
FALLBACK_INTL_SHIPPING_USD = 20.00

n_items = len(invoice_items)
intl_per_item = international_shipping_jpy / n_items
customs_per_item = customs_duty_jpy / n_items

for item in items:
    total_jpy = (
        item.item_price_jpy
        + item.domestic_shipping_jpy
        + item.buyee_service_fee_jpy    # typically ¥300/item
        + intl_per_item
        + customs_per_item
    )
    total_usd    = total_jpy * exchange_rate
    unit_cost    = total_usd / max(item.quantity, 1)
```

---

## 7. Lot Quantity Detection (Buyee)

Items sold as lots need their quantity parsed from the item name:

```python
# Japanese patterns
"2点セット" → qty = 2
"3点まとめ" → qty = 3
"３点セット" → qty = 3  (full-width digits)

# English patterns
"set of 2", "2-piece", "2 pieces", "2 pcs", "2pcs" → qty = 2

# Default
qty = 1
```

---

## 8. Price Rounding

All final prices round **UP** to the next psychological price point per $100 bracket.
Valid points: **25, 45, 75, 95**

```python
def round_price(price: float) -> int:
    price_points = [25, 45, 75, 95]
    base_hundred = int(price // 100) * 100
    remainder = price % 100
    next_point = next((p for p in price_points if p >= remainder), None)
    if next_point is None:
        return base_hundred + 100 + 25   # e.g. $198 → $225
    return max(base_hundred + next_point, 25)

# Examples
round_price(123.45) → 125
round_price(145.10) → 145
round_price(198.99) → 225
round_price(12.50)  → 25
```

---

## 9. BrandStreet Markup

### Linear Interpolation

```python
def _lerp_markup(cost, c_lo, c_hi, m_hi, m_lo):
    """Higher cost → lower multiplier (linear)."""
    t = max(0.0, min(1.0, (cost - c_lo) / max(c_hi - c_lo, 1)))
    return m_hi - t * (m_hi - m_lo)
```

### Markup Ranges by Item Type

```python
# actual_cost = unit_cost_usd (already includes 1.2×1.15 from cost calc)

if item_type == 'Sunglasses':
    base_markup = _lerp_markup(cost, 100, 400, 2.5, 1.8)

elif item_type in ('Handbag', 'Shoulder Bag', 'Clutch Bag', 'Clutch'):
    if vendor in luxury_brands:
        base_markup = _lerp_markup(cost, 150, 800, 2.2, 1.65)
    else:
        base_markup = _lerp_markup(cost, 150, 600, 2.3, 1.6)

elif item_type in ('Pouch', 'Wallet', 'Bag'):
    base_markup = _lerp_markup(cost, 100, 500, 2.0, 1.5)

elif vendor in luxury_brands:
    base_markup = _lerp_markup(cost, 150, 600, 2.2, 1.5)

elif vendor in mid_tier_brands:
    base_markup = _lerp_markup(cost, 100, 500, 2.3, 1.6)

else:
    base_markup = _lerp_markup(cost, 100, 500, 2.5, 1.7)

calculated_price = actual_cost * base_markup
```

### BrandStreet Price Bands (floors & ceilings)

```python
brand_bands = {
    ('Louis Vuitton', 'Handbag'):      (600, 900),
    ('Louis Vuitton', 'Shoulder Bag'): (600, 900),
    ('Louis Vuitton', 'Clutch Bag'):   (600, 900),
    ('Louis Vuitton', 'Clutch'):       (600, 900),
    ('Fendi',         'Handbag'):      (500, 900),
    ('Fendi',         'Shoulder Bag'): (500, 900),
    ('Fendi',         'Clutch Bag'):   (500, 900),
    ('Fendi',         'Clutch'):       (500, 900),
    ('Prada',         'Handbag'):      (600, 900),
    ('Prada',         'Shoulder Bag'): (600, 900),
    ('Prada',         'Clutch Bag'):   (600, 900),
    ('Prada',         'Clutch'):       (600, 900),
    ('Gucci',         'Handbag'):      (600, 900),
    ('Gucci',         'Shoulder Bag'): (600, 900),
    ('Gucci',         'Clutch Bag'):   (600, 900),
    ('Gucci',         'Clutch'):       (600, 900),
}

if item_type == 'Sunglasses':
    calculated_price = min(calculated_price, 495)       # hard cap
elif (vendor, item_type) in brand_bands:
    floor, ceil = brand_bands[(vendor, item_type)]
    calculated_price = max(floor, min(ceil, calculated_price))
else:
    calculated_price = max(525, calculated_price)       # global floor
```

After rounding, enforce ceiling again (rounding can push past it).

---

## 10. Buyee Markup

### Additive Multiplier System

```python
base_markup = 4.0

# Step 1 — Material
premium_furs = ['Mink', 'Fox Fur', 'Weasel Fur', 'Raccoon Fur', 'Squirrel Fur', 'Rabbit Fur']
if material in premium_furs:              base_markup = 6.0
elif material in ['Shearling', 'Fur', 'Sheepskin', 'Lambskin']:   base_markup = 5.5
elif material in ['Leather', 'Suede', 'Pony Hair', 'Goat Leather']: base_markup = 5.0
elif material in ['Silk', 'Satin', 'Cashmere']:                    base_markup = 4.5
elif material in ['Denim', 'Cotton']:                              base_markup = 4.0

# Step 2 — Brand tier
if vendor in luxury_brands:    base_markup += 1.0
elif vendor in mid_tier_brands: base_markup += 0.5

# Step 3 — Garment length
if is_long:      base_markup += 0.5
elif is_midi:    base_markup += 0.25

# Apply
actual_cost = unit_cost_usd * 1.2
calculated_price = actual_cost * base_markup
```

### Buyee Price Floors & Ceilings by Type

```python
if item_type == 'Coat':
    if material in premium_furs:
        calculated_price = max(400, min(600, calculated_price))
    elif material in ['Shearling', 'Fur', 'Sheepskin', 'Lambskin']:
        calculated_price = max(350, min(550, calculated_price))
    elif material in ['Leather', 'Suede', 'Goat Leather']:
        calculated_price = max(300, min(500, calculated_price))
    else:
        calculated_price = max(250, min(450, calculated_price))

elif item_type == 'Jacket':
    if material in ['Shearling', 'Fur', 'Leather'] + premium_furs:
        calculated_price = max(250, min(400, calculated_price))
    else:
        calculated_price = max(150, min(350, calculated_price))

elif item_type in ['Top', 'Sweater', 'Cardigan', 'Blouse', 'Vest']:
    calculated_price = max(80, min(250, calculated_price))

elif item_type in ['Pants', 'Skirt']:
    calculated_price = max(100, min(350, calculated_price))

elif item_type == 'Dress':
    calculated_price = max(150, min(450, calculated_price))

elif item_type in ['Belt', 'Scarf', 'Stole', 'Shawl']:
    calculated_price = max(80, min(250, calculated_price))

elif item_type in ['Bag', 'Handbag', 'Clutch', 'Pouch']:
    if vendor in luxury_brands:
        calculated_price = max(300, min(800, calculated_price))
    else:
        calculated_price = max(150, min(400, calculated_price))
```

### Buyee Market Adjustment Multipliers

Applied after floors/ceilings:

```python
market_adjustments = {
    ('Prada',              'Handbag'):  1.10,
    ('Prada',              'Clutch'):   1.08,
    ('Prada',              'Pouch'):    1.05,
    ('Miu Miu',            'Clutch'):   1.08,
    ('Miu Miu',            'Handbag'):  1.08,
    ('Hermès',             'Handbag'):  1.15,
    ('Hermès',             'Clutch'):   1.12,
    ('Hermès',             'Scarf'):    1.15,
    ('Hermès',             'Stole'):    1.12,
    ('Chanel',             'Handbag'):  1.12,
    ('Chanel',             'Clutch'):   1.10,
    ('Burberry',           'Scarf'):    0.80,
    ('Burberry',           'Stole'):    0.85,
    ('Issey Miyake',       'Skirt'):    1.15,
    ('Issey Miyake',       'Dress'):    1.15,
    ('Issey Miyake',       'Top'):      1.10,
    ('Issey Miyake',       'Pants'):    1.10,
    ('Issey Miyake',       'Coat'):     1.10,
    ('Yves Saint Laurent', 'Sweater'):  1.05,
    ('Yves Saint Laurent', 'Jacket'):   1.10,
    ('Yves Saint Laurent', 'Coat'):     1.08,
    ('Yves Saint Laurent', 'Handbag'):  1.10,
    ('Vintage',            'Coat'):     0.95,
    ('Vintage',            'Jacket'):   0.97,
    ('Fendi',              'Handbag'):  1.08,
    ('Fendi',              'Clutch'):   1.05,
    ('Giorgio Armani',     'Dress'):    1.05,
    ('Giorgio Armani',     'Jacket'):   1.05,
}

adj = market_adjustments.get((vendor, item_type), 1.0)
calculated_price *= adj
```

---

## 11. Demand Multiplier (UI)

A global multiplier applied on top of all per-item pricing:

```python
final_price = round_price(calculated_price * demand_multiplier)
```

Default: **1.0**. Expose as a number input in the UI. Store in session state so it persists between uploads. Provide a "Recalculate Prices" button to re-apply without reprocessing.

---

## 12. Brand Tier Lists

```python
luxury_brands = [
    'Dolce & Gabbana', 'Yves Saint Laurent', 'Prada', 'Gucci', 'Chanel',
    'Louis Vuitton', 'Burberry', 'Fendi', 'Valentino', 'Versace', 'Hermès',
    'Dior', 'Christian Dior', 'Givenchy', 'Balenciaga', 'Bottega Veneta',
    'Celine', 'Loewe', 'Salvatore Ferragamo', 'Moncler', 'Miu Miu', 'Saint Laurent',
]

mid_tier_brands = [
    'Sonia Rykiel', 'Marella', 'Missoni', 'Max Mara', 'Giorgio Armani',
    'Emporio Armani', 'Ralph Lauren', 'Marc Jacobs', 'Coach', 'Michael Kors',
    'Emanuel Ungaro', 'Issey Miyake',
]
```

---

## 13. Shopify CSV Schema

Required output columns (no underscore prefix = exported):

| Column | Value |
|--------|-------|
| `Title` | Generated product title |
| `Body (HTML)` | `""` (empty) |
| `Vendor` | Brand name |
| `Product Category` | Full Shopify taxonomy path (see below) |
| `Tags` | Delivery date as `YYYY_MM_DD` |
| `Variant Inventory Tracker` | `"shopify"` |
| `Cost per Item` | Landed cost USD (2 decimal places) |
| `Inventory quantity` | `1` (one row per unit) |
| `Variant Price` | Final rounded price |
| `Status` | `"draft"` |
| `SKU` | `{VENDOR_3}_{YYMM}_{3_RANDOM_DIGITS}` e.g. `LOU_2603_427` |
| `Option1 Name` | `""` |
| `Option1 Value` | `""` |
| `Option1 Linked To` | `""` |
| `Published` | `""` |

Internal columns (prefix `_`, strip before export):

| Column | Purpose |
|--------|---------|
| `_Markup` | Actual markup multiplier applied |
| `_Base Price` | Price before demand multiplier |
| `_source_file` | Which CSV file this row belongs to |

**Lot expansion:** If `quantity > 1`, create one row per unit. Append `[REVIEW]` to title and generate a new SKU for each unit.

---

## 14. Shopify Product Category Taxonomy Paths

```python
_shopify_category = {
    'Handbag':        'Apparel & Accessories > Handbags, Wallets & Cases > Handbags',
    'Shoulder Bag':   'Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shoulder Bags',
    'Clutch Bag':     'Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Clutch Bags',
    'Tote Bag':       'Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shopper Bags',
    'Hobo Bag':       'Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Hobo Bags',
    'Pouch':          'Apparel & Accessories > Handbags, Wallets & Cases > Handbags',
    'Belt Bag':       'Apparel & Accessories > Handbags, Wallets & Cases > Handbags',
    'Bag':            'Apparel & Accessories > Handbags, Wallets & Cases > Handbags',
    'Clutch':         'Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Clutch Bags',
    'Wallet':         'Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Wallets',
    'Card Holder':    'Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Card Cases',
    'Key Holder':     'Apparel & Accessories > Handbags, Wallets & Cases > Wallets & Money Clips > Key Cases',
    'Sunglasses':     'Apparel & Accessories > Clothing Accessories > Sunglasses',
    'Belt':           'Apparel & Accessories > Clothing Accessories > Belts',
    'Scarf':          'Apparel & Accessories > Clothing Accessories > Scarves & Shawls',
    'Coat':           'Apparel & Accessories > Clothing > Outerwear > Coats & Jackets',
    'Jacket':         'Apparel & Accessories > Clothing > Outerwear > Coats & Jackets',
    'Blazer':         'Apparel & Accessories > Clothing > Outerwear > Coats & Jackets > Sport Jackets',
    'Dress':          'Apparel & Accessories > Clothing > Dresses',
    'Top':            'Apparel & Accessories > Clothing > Clothing Tops',
    'Sweater':        'Apparel & Accessories > Clothing > Clothing Tops > Sweaters',
    'Cardigan':       'Apparel & Accessories > Clothing > Clothing Tops > Cardigans',
    'Skirt':          'Apparel & Accessories > Clothing > Skirts',
    'Pants':          'Apparel & Accessories > Clothing > Pants',
    'Clothing':       'Apparel & Accessories > Clothing',
    # ... fallback
}
# Default: 'Apparel & Accessories > Clothing'
```

---

## 15. Title Format

**BrandStreet (with Claude AI):**
```
Brand [Color] [Pattern] [Model] [Material] [CC if Chanel] Type
Examples:
  "Louis Vuitton Monogram Speedy 25 Handbag"
  "Chanel White Matelasse Lambskin CC Shoulder Bag"
  "Bottega Veneta Yellow Intrecciato Leather Hobo Bag"
  "Louis Vuitton Silver Monogram Gamecube Metal Paperweight"
```

**BrandStreet (keyword-only) or Buyee:**
```
[Era] Brand [Color] [Pattern] [Cut] [Material] Type
Examples:
  "Burberry Beige Nova Check Belted Trench Coat"
  "90s Giorgio Armani Navy Tailored Wool Blazer"
  "Chanel Black Matelasse Lambskin Shoulder Bag"
```

---

## 16. Worked Pricing Examples

**BrandStreet — Louis Vuitton Monogram Neverfull MM ($280 item price):**
```
1. unit_cost = $280 × 1.20 × 1.15 = $386.40
2. type = Handbag, vendor = Louis Vuitton (luxury)
3. markup = lerp($386, $150, $800, 2.2, 1.65)
           = 2.2 - ((386-150)/(800-150)) × 0.55
           = 2.2 - 0.199 = 2.001×
4. price  = $386.40 × 2.001 = $773.19
5. band   = LV Handbag → ($600, $900) → stays $773.19
6. round  = $775
7. demand × 1.0 = $775
Final: $775
```

**Buyee — Chanel Lambskin Handbag (¥45,000 item, +¥500 domestic, +¥300 service, ¥3,000 intl/10 items, ¥0 customs):**
```
1. total_jpy = 45,000 + 500 + 300 + 300 = 46,100
2. unit_cost_usd = 46,100 × 0.0067 = $308.87
3. material = Lambskin → base 5.5×
4. brand = Chanel (luxury) → +1.0 → 6.5×
5. actual_cost = $308.87 × 1.2 = $370.64
6. price = $370.64 × 6.5 = $2,409
7. ceiling (luxury bag) = $800 → clamp to $800
8. market adj (Chanel, Handbag) = ×1.12 → $896
9. ceiling check → $800 (re-clamp post-adj)
10. round → $795 (within $800 ceiling)
Final: $795
```

---

## 17. Files in This Package

| File | Purpose |
|------|---------|
| `SPEC.md` | This document — full specification |
| `code/cost_calculator.py` | Complete cost calculation implementation |
| `code/process_invoice.py` | PDF text parser for both invoice types |
| `code/learned_keywords.json` | Auto-discovered brand names (can start empty `{"brands": {}}`) |
