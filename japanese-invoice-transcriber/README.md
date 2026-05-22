# Japanese Invoice Transcriber

Ingests vintage-shop supplier invoices (Japanese or English, PDF or image),
calculates landed cost, applies markup and price bands per
[`ps_inventory_spec/SPEC.md`](../ps_inventory_spec/SPEC.md), and produces a
Shopify-ready inventory CSV with `Variant Price` populated.

```
PDFs  ──▶  transcribe.py  ──▶  output/*.json      ──┐
             │                                      ├──▶  to_shopify.py  ──▶  shopify.csv
             ▼                                      │
        verify.py  (cost + price)                   │
             ▲                                      │
             └────────────  price.py  ──▶  priced/*.json

                    ╭────────────────────────────────╮
                    │  app.py  (Streamlit UI)        │
                    │  one-click upload → download   │
                    ╰────────────────────────────────╯
```

Two invoice shapes handled:

| Type                | Source                        | Currency | Cost model |
|---------------------|-------------------------------|----------|------------|
| `buyee_breakdown`   | Buyee "Breakdown of Expenses" | JPY      | Per-item commission + domestic shipping + service fee (joined by `source_id`) + equal share of intl shipping and customs |
| `vendor_invoice`    | Brand Street Tokyo, similar   | USD      | `item_price × 1.20 (handling) × 1.15 (import tax)` — spec §5 |

## Setup

```bash
brew install uv
# add your key to ../.env (walked up from cwd):
echo "ANTHROPIC_API_KEY=sk-ant-..." >> ../.env
```

No venv, no `pip install` — every script has a `uv` PEP 723 header that handles deps.

## Usage — Web UI (recommended)

```bash
uv run app.py
```

Opens `http://localhost:8501` with a single-page editorial UI (Playfair Display + Red Hat Text, matching the Past Studies brand):

1. **Upload a PDF** (or pick an already-transcribed invoice)
2. Totals auto-computed: invoice total, Σ landed USD, Σ variant price, gross margin, avg markup
3. **Adjust the demand multiplier slider** (0.5×–1.5×) and exchange rate — prices recompute instantly
4. Scroll the items table to spot-check — each row shows brand, type, material, length, unit cost, markup, band, market adjustment, and rounded Variant Price
5. **Download Shopify CSV**

## Usage — CLI

```bash
# 1. Transcribe PDFs → JSON
uv run transcribe.py <file.pdf>
uv run transcribe.py --inbox inputs/ --archive inputs/processed/ --out output/

# 2. Price: apply markup / bands / adjustments / rounding
uv run price.py output/ --demand 1.0 --jpy-usd 0.0067 -o priced/

# 3. (optional) Human-readable verification PDF
uv run verify.py samples/foo.pdf priced/foo.json
# or verify straight from output/*.json (uses default demand=1.0)
uv run verify.py samples/foo.pdf output/foo.json

# 4. Shopify CSV
uv run to_shopify.py priced/ --strip-internal -o shopify.csv
```

## Pipeline details

### 1. Transcription (`transcribe.py`)

Feeds the PDF to Claude Opus 4.7 with a prompt that teaches it both invoice
shapes and asks for JSON. Pydantic validates. Four Buyee tables are extracted
independently: `items[]`, `commission_fees[]`, `domestic_shipping_fees[]`,
`service_fees[]`. Fee rows carry the Shopping Site(ID) string as `source_id`.

After the LLM pass, `extractors.py` runs regex fallbacks on
`description_original + description_english` to fill any `material` or
`garment_length` the LLM missed. Canonical values stay consistent with the
pricing rules (e.g. `Lambskin`, `Fox Fur`, `midi`, `long`).

### 2. Landed cost (`costs.py`)

Four-table join happens in Python, not in the LLM. `InvoiceView.breakdown(item)` returns every component:

```
Buyee:
  subtotal          = item_price × qty − coupon_discount
  + commission      (commission_fees_by_id[source_id])
  + domestic_ship   (domestic_shipping_fees_by_id[source_id])
  + service         (service_fees_by_id[source_id])
  + intl_share      = international_shipping / n_items       (equal split — spec §6)
  + customs_share   = customs_duty / n_items                 (equal split)
  ──────────────────
  = landed_native (JPY)
  × exchange_rate → landed_usd (goes in Shopify Cost per Item)

BrandStreet:
  subtotal          = item_price × qty − coupon
  + handling_uplift = subtotal × (1.20 × 1.15 − 1)           (spec §5)
  ──────────────────
  = landed_native (USD) = landed_usd
```

Fallback: if a Buyee invoice reports `international_shipping == 0`, the view
substitutes `$20 USD ÷ exchange_rate` (spec §6). Orphan fees (source_id in
a fee table but no matching item) are surfaced by `view.orphan_fees()`.

### 3. Pricing (`pricing.py`)

Pure functions. `price_item(item, view, demand=1.0)` returns a `PricingResult` with every step:

| Step                 | Spec |
|----------------------|------|
| Markup               | §9 BS linear interp per `(type, tier)`; §10 Buyee additive (material + brand tier + garment length) |
| Bands                | §9 BS brand bands (LV/Fendi/Prada/Gucci bags); §10 Buyee bands per `(type, material)` |
| Market adjustment    | §10 ~30 `(vendor, item_type)` pairs — e.g. Chanel Handbag ×1.12, Burberry Scarf ×0.80 |
| Demand               | §11 — global multiplier, 1.0 default |
| Round                | §8 — UP to 25 / 45 / 75 / 95 per $100 bracket |
| Re-clamp to ceiling  | §9 footer — rounding can push past the band ceiling; step back down |

Canonicalization lives in `pricing.py` too — `canon_brand()` and `canon_type()`
normalize the LLM's loose casing (`"hand bag"` → `"Handbag"`) so the rule
tables match by exact string.

Validated against spec §16 worked examples:
- **BrandStreet LV Neverfull** ($280 raw) → cost $386.40 → base $772.79 → band $600–$900 → round $775 ✓
- **Buyee Chanel Lambskin Handbag** (¥45,000 + fees → $308.87 USD) → markup 6.5× → base $2,409 → ceiling clamp $800 → market adj ×1.12 → re-clamp → round $795 ✓

### 4. Shopify CSV (`to_shopify.py`)

- Full Shopify taxonomy paths (spec §14): e.g. `Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shoulder Bags`
- SKU format `{BRAND_PREFIX}_{YYMM}_{3_CHARS}` — defaults to the auction-code tail for traceability, falls back to random 3 digits
- **Lot expansion**: `quantity > 1` emits N rows, each with `[REVIEW]` prefix and a new SKU
- Internal columns `_Markup`, `_Base Price`, `_source_file` — strip with `--strip-internal` for the version you actually upload

### 5. Verification PDF (`verify.py`)

Three columns per page:
- **Left**: original invoice page (rendered image)
- **Middle**: every item in that chunk showing **both** cost breakdown *and* pricing breakdown in separate tables
- **Right**: per-page subtotals + full-invoice reconciliation + invoice-wide rollup (Σ Landed USD, Σ Variant Price, gross margin, avg markup)

This is the QA artifact. Open it, scan for green ✓ reconciled in the header, then spot-check that prices land in sensible ranges.

## Files

```
app.py           — Streamlit web UI (Playfair Display + Red Hat Text)
transcribe.py    — PDF/image → JSON (Claude Vision). Batch or single-file.
extractors.py    — Regex fallback for material / garment_length.
costs.py         — Models + InvoiceView (join + breakdown + reconciliation).
pricing.py       — Markup, bands, market adjustments, rounding. Pure functions.
price.py         — CLI that applies pricing.py to transcribed JSONs.
to_shopify.py    — Priced JSON → Shopify CSV.
verify.py        — PDF + JSON → side-by-side verification PDF.
PLAN.md          — Spec gap analysis and implementation phases.
TODO.md          — Ingestion plan (Gmail, Buyee scraper research).
samples/         — Reference invoices + Shopify inventory CSV template.
inputs/          — Drop zone for new PDFs (created on first run).
output/          — Transcribed JSON (one per invoice).
priced/          — Priced JSON (output of price.py).
verify/          — Verification PDFs.
```

## QA workflow

Per invoice:

1. Drop PDF in `inputs/` (or upload via the UI).
2. Transcribe → JSON lands in `output/`.
3. Price → priced JSON lands in `priced/`.
4. `uv run verify.py samples/<pdf> priced/<json>` → open the verification PDF.
5. **QA gates on the verification PDF:**
   - Header shows green ✓ reconciled (cost matches invoice total)
   - Right panel: Σ Landed USD, Σ Variant Price, avg markup all in sensible ranges
   - No yellow warning badges on more than ~20% of items
   - Spot-check 5 random items: brand detected, material sensible, price band matches intuition
6. `uv run to_shopify.py priced/ --strip-internal -o outputs/pending_upload/shopify_<date>.csv`.
7. Upload to Shopify.
8. Move CSV from `pending_upload/` → `uploaded/` for audit trail.

Regression anchor: re-run the two sample PDFs and confirm the LV Neverfull and Chanel Lambskin worked-examples still hit $775 and $795 respectively.

## Known gotchas

- **Streamlit first launch**: `uv run app.py` takes ~30 seconds the first time to install streamlit + pandas into the uv environment. Subsequent launches are instant.
- **Transcription time**: Buyee invoices with 20–30 items take ~60 seconds; BST invoices with 50+ items take ~90 seconds. Use streaming via the Anthropic SDK (already configured, 180s timeout, 2 retries).
- **Missing material / garment length**: the LLM fills them when the invoice text is explicit; the regex fallback catches common Japanese/English cues. If both miss, a `Missing material` warning shows on the item. Open `priced/*.json` and edit `material: null → "Leather"` by hand if you want to override — then re-run `to_shopify.py`.
- **Opus 4.7 vs Sonnet 4.6**: both work. Opus gives cleaner translations and better product_type classification. Swap `MODEL = "claude-opus-4-7"` in `transcribe.py` to downgrade.
- **Schema too complex**: we use plain `messages.create()` + JSON instruction, not `messages.parse()`. The latter returned "Schema too complex" 400s on the Invoice model.
- **Dotenv override**: `load_dotenv(..., override=True)` is intentional — some sandboxes preset `ANTHROPIC_API_KEY=""` and the non-override default would silently preserve the empty string.
