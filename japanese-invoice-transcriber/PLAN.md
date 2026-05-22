# Plan: Aligning Our Tool With `ps_inventory_spec/SPEC.md`

The original spec is the source of truth. Our current tool covers **transcription
and cost attribution** well — but it's only 30–40% of what the spec describes.
The spec's endgame is a **priced** Shopify CSV, with markup rules, floors/ceilings,
market adjustments, demand multipliers, and psychological price rounding.
We currently stop at cost and leave `Variant Price` blank.

This plan closes that gap in phases, keeps what's working (LLM vision extraction,
four-table join, verification PDFs), and replaces the spec's manual steps (PDF
text regex, translation, OCR debug passes) with deterministic code + the LLM.

---

## 1. What the spec says (condensed)

Pipeline:

```
PDF ──▶ process_invoice.py  ──▶ invoice_data dict (items + fees)
             │
             ▼
        cost_calculator.py  ──▶ List[InventoryItem]  (landed cost USD)
             │
             ▼
        data_formatter.py   ──▶ Shopify CSV with Variant Price
```

Key spec rules:

1. **BrandStreet cost** = `item_price × 1.20 (handling) × 1.15 (import tax)`. Single-factor USD uplift. No fee tables.
2. **Buyee cost** = `item_price + domestic_shipping + buyee_service_fee + intl/n + customs/n` (JPY), converted at 0.0067 default rate. Intl and customs **split equally** across items.
3. **Fallback intl shipping** = $20 USD if not found in PDF.
4. **Lot detection**: regex on item name (`2点セット`, `set of 2`, `2-piece`…) → quantity > 1.
5. **Price rounding**: round UP to 25 / 45 / 75 / 95 per $100 bracket. `$123 → $125`, `$145 → $145`, `$198 → $225`.
6. **BrandStreet markup**: linear interpolation by cost; different ranges per `(item_type, vendor_tier)`. Sunglasses cap at $495, luxury bags $600–$900, global floor $525.
7. **Buyee markup**: additive. Start at 4.0, bump by material (fur=6.0, leather=5.0…), by brand tier (luxury +1.0, mid +0.5), by garment length (long +0.5, midi +0.25). Apply to `unit_cost × 1.2`.
8. **Buyee floors/ceilings**: by item type × material.
9. **Market adjustment multipliers**: ~30 specific `(vendor, item_type)` pairs, e.g. Chanel Handbag ×1.12, Burberry Scarf ×0.80.
10. **Demand multiplier**: global UI-exposed knob (default 1.0) applied last.
11. **Shopify CSV schema**: full Shopify taxonomy paths, SKU format `{VENDOR_3}_{YYMM}_{3_DIGITS}`, lot expansion (qty>1 → one row per unit with `[REVIEW]`), internal `_Markup` / `_Base Price` / `_source_file` columns stripped on export.
12. **Brand tier lists**: 22 luxury brands, 12 mid-tier brands.
13. **Product categories**: full Shopify taxonomy paths, e.g. `Apparel & Accessories > Handbags, Wallets & Cases > Handbags > Shoulder Bags`.

---

## 2. Gap analysis — spec vs what we have now

| Spec rule                                     | Our tool                                      | Status       |
|-----------------------------------------------|-----------------------------------------------|--------------|
| PDF parsing (regex + OCR)                     | Claude Vision                                 | ✅ better      |
| Source detection (BrandStreet / Buyee)        | `invoice_type` field from LLM                 | ✅            |
| Japanese translation                          | LLM `description_english`                     | ✅            |
| Lot qty detection                             | LLM reads qty from description                | ⚠️ works but untested; add regex sanity check |
| Item Price extraction                         | `items[].item_price`                          | ✅            |
| Domestic shipping per item                    | `domestic_shipping_fees[]` joined by `source_id` | ✅ (stronger than spec — spec hardcodes ¥900 default) |
| Buyee Service Fee per item                    | `service_fees[]` joined                        | ✅            |
| International shipping (invoice-wide)         | `international_shipping` field                | ✅            |
| **Customs duty (invoice-wide)**               | **not extracted**                             | ❌ add        |
| **Commission fee per item** (Luxe Wholesale)  | `commission_fees[]` joined                    | ⚠️ spec is silent; our sample invoice has ¥11,011 of commission — can't drop this |
| **BrandStreet cost uplift (×1.20 × 1.15)**    | raw item_price used                           | ❌ add        |
| **Fallback intl shipping ($20 USD)**          | no fallback                                   | ❌ add        |
| **Cost split method**                         | pro-rated by item subtotal                    | ❌ spec says equal split across items |
| Exchange rate                                 | `--jpy-usd` CLI arg (no default)              | ⚠️ add 0.0067 default  |
| **Price rounding (25/45/75/95)**              | not implemented                               | ❌ add        |
| **BrandStreet markup (linear interp)**        | not implemented                               | ❌ add        |
| **Buyee markup (additive)**                   | not implemented                               | ❌ add        |
| **Material detection**                        | not extracted                                 | ❌ add field  |
| **Garment length (long/midi)**                | not extracted                                 | ❌ add field  |
| **Brand tier lists (luxury / mid-tier)**      | only brand→SKU prefix                         | ❌ add        |
| **Price bands (floors/ceilings)**             | not implemented                               | ❌ add        |
| **Market adjustments**                        | not implemented                               | ❌ add        |
| **Demand multiplier**                         | not implemented                               | ❌ add CLI flag |
| **Shopify Product Category (full taxonomy)**  | simplified "Clothing/Bags/Accessories"        | ❌ swap to spec's map |
| **SKU format `{VND3}_{YYMM}_{3DIG}`**         | auction-code suffix or counter                | ⚠️ spec uses random 3 digits; ours uses the auction tail — more traceable, propose keeping ours |
| **Lot expansion (qty>1 → N rows, `[REVIEW]`)** | single row with qty                          | ❌ add        |
| **Internal `_Markup` / `_Base Price` cols**   | not implemented                               | ❌ add        |
| Tags                                          | `YYYY_MM_DD, platform, type`                  | ✅ (ours extra info; spec says just date)    |

---

## 3. Decisions (with rationale)

**The user said: "rely on the original spec as the source of truth for now."**
So where spec and reality conflict, spec wins unless dropping a feature would
silently lose money. Specific calls:

1. **Cost split: switch to equal-split per the spec.**
   Our pro-rating was an unrequested improvement. Revert.

2. **Keep our commission_fees field.** The spec doesn't mention it, but our
   Buyee sample has ¥11,011 of commission. Silently dropping it would
   understate landed cost by ~3.5% on that invoice. Treat this as a spec gap
   we're noting upward, not overriding.

3. **Keep four-table extraction + Python join.** Strictly stronger than the
   spec's regex that hardcodes `domestic_shipping_fee: 900` as a default. We
   get every fee from the invoice with source_id traceability.

4. **Add BrandStreet × 1.20 × 1.15 uplift.** This is THE BrandStreet cost
   rule in the spec. Omitting it would make BrandStreet costs ~38% too low,
   cascading through all pricing math.

5. **Add customs_duty field and $20-USD intl fallback.** Spec-mandated.

6. **Build pricing as a separate `pricing.py` module + `price.py` script.**
   Don't bake it into `to_shopify.py`. Reasons:
   - Pricing is a separate concern (cost ≠ price)
   - Iterating on markup/bands without re-transcribing is cheap
   - QA gate between "cost is right" and "price is right"

7. **Keep our SKU format** (`{BRAND3}_{YYMM}_{auction_suffix}`) instead of
   random digits. Traceable back to the source auction — useful for audit.
   If the user wants random-digit SKUs, one-line change.

8. **Adopt the spec's Shopify category taxonomy** verbatim — it's the
   official Shopify GCP path that their store already uses.

---

## 4. Open questions (answer before Phase 3)

- **Material & garment length** — spec uses these for Buyee markup. Should we
  (a) ask the LLM to extract them as new fields on LineItem, or
  (b) run a regex pass after transcription on `description_original`? (a) is
  faster to implement and more robust to new phrasings; (b) is deterministic.
  **Proposal: (a), with a fallback regex for common cases.**

- **Demand multiplier** — spec says "expose as UI number input, persist in
  session state." We have no UI. CLI flag `--demand 1.05` on `price.py` is the
  right MVP.

- **Brand / item_type normalization** — pricing rules are keyed on exact
  strings like `"Louis Vuitton"` + `"Handbag"`. Our LLM might output
  `"LOUIS VUITTON"` + `"handbag"` or `"hand bag"`. Need a canonicalization
  layer before the rules hit. Proposal: normalize in `pricing.py` at entry.

- **QA dataset** — right now we have 2 sample invoices. Do we have a
  "known-good" expected Shopify CSV to diff against? If not, Phase 5 should
  hand-build one from the Buyee sample and use it as the regression anchor.

---

## 5. Implementation phases

Each phase ends with a QA checkpoint. Verification PDFs + CSV diffs are the
artifacts.

### Phase 1 — Fix cost math (spec compliance)  ← *start here*

Files touched: `costs.py`, `transcribe.py` (prompt), `verify.py`, `to_shopify.py`

- [ ] Add `customs_duty: float = 0` to Invoice model.
- [ ] Update prompt to extract Customs Duty line from invoice if present.
- [ ] Replace pro-rated intl-share with **equal split**: `intl_per_item = intl / n_items`, same for customs.
- [ ] Add `FALLBACK_INTL_USD = 20` constant; if `international_shipping == 0`, set to `20 / rate` (in JPY).
- [ ] Add `BrandStreet uplift`: for `vendor_invoice`, `unit_cost_usd = item_price × 1.20 × 1.15 / qty`.
- [ ] Update `InvoiceView.breakdown` to return: `item_price × qty`, `coupon`, `commission`, `dom_ship`, `service`, `intl_per_item`, `customs_per_item`, `handling_uplift` (BS only), `landed_usd`, `unit_cost_usd`.
- [ ] Update `verify.py` to show the new fields in the per-item cost table.
- [ ] Re-run both samples → check reconciliation still passes.

**QA gate**: Verification PDFs for both samples show correct cost lines. The BST total in USD should be `$15,796 × 1.20 × 1.15 = $21,798.48` (landed), NOT `$15,796` (raw).

### Phase 2 — Add material + garment-length extraction

Files touched: `costs.py` (LineItem schema), `transcribe.py` (prompt).

- [ ] Add `LineItem.material: str | None` — one of a whitelist (Leather, Suede, Silk, Denim, Cotton, Wool, Shearling, Fur, Fox Fur, Mink, Lambskin, Cashmere, Polyester, …). Null if unidentifiable.
- [ ] Add `LineItem.garment_length: Literal["short", "midi", "long"] | None`. Null for non-garments.
- [ ] Update prompt with examples: `ハーフコート → "midi"`, `ロング → "long"`, `mini / ショート → "short"`.
- [ ] Re-run samples, spot-check in verify PDF.

**QA gate**: Eyeball 5 items from each sample, confirm material and length are sane.

### Phase 3 — Build `pricing.py` module

New file: `pricing.py` (no script header, imported by `price.py`).

- [ ] `luxury_brands`, `mid_tier_brands` lists (verbatim from spec).
- [ ] `premium_furs` list.
- [ ] `round_price(float) → int` — 25/45/75/95 ladder.
- [ ] `brandstreet_markup(cost, item_type, vendor) → float` — linear interp rules.
- [ ] `buyee_markup(cost, material, vendor, is_long, is_midi) → float` — additive.
- [ ] `brandstreet_bands`, `buyee_bands` — dicts keyed on `(vendor, item_type)` or `item_type`.
- [ ] `market_adjustments` dict.
- [ ] `price_item(item, invoice, demand=1.0) → PricingResult` — pure function returning `{cost, markup, base_price, bands_applied, adjustment, demand, final_price, rounded_price}`.
- [ ] Brand / item_type canonicalization: case-insensitive, alias map (e.g. `"hand bag" → "Handbag"`).

### Phase 4 — Build `price.py` CLI + update `to_shopify.py`

- [ ] New `price.py`:
  ```
  uv run price.py output/ --demand 1.0 --jpy-usd 0.0067 -o priced/
  ```
  Reads `output/*.json`, writes `priced/*.json` with `pricing_result` embedded per item.
- [ ] `to_shopify.py` reads from `priced/` instead of `output/`. Uses `pricing_result.rounded_price` for `Variant Price`.
- [ ] Add internal columns `_Markup`, `_Base Price`, `_source_file` (no underscore, strip before export if `--strip-internal`).
- [ ] Swap `CATEGORY_MAP` for the spec's full taxonomy paths.
- [ ] Lot expansion: if `quantity > 1`, emit N rows, each with `[REVIEW]` in Title and a new SKU tail.

**QA gate**: Run worked examples from SPEC §16 through the pipeline; confirm LV Neverfull → $775 (or within $5) and Chanel Lambskin Handbag → $795.

### Phase 5 — Extend `verify.py` for pricing

- [ ] Show pricing fields in the per-item cost table: markup tier, base price, bands applied, market adjustment, demand, final rounded price.
- [ ] Right panel: rollup of `Σ Cost per Item` vs `Σ Variant Price` and average markup.

**QA gate**: Verification PDFs become the single artifact a human needs to review before uploading the CSV.

### Phase 6 — Directory & workflow

Align folder layout with spec for mental consistency:

```
japanese-invoice-transcriber/
├── inputs/          ← drop PDFs here (renamed from inbox/)
├── inputs/processed/← transcribed & moved (renamed from archive/)
├── outputs/
│   ├── json/        ← transcribe.py output (renamed from output/)
│   ├── priced/      ← price.py output (new)
│   ├── verify/      ← verify.py output
│   ├── pending_upload/ ← to_shopify.py CSV output
│   └── uploaded/    ← manual move after Shopify upload (audit trail)
├── costs.py, pricing.py, transcribe.py, price.py, verify.py, to_shopify.py
└── ...
```

---

## 6. QA workflow

Per invoice:

1. **Drop PDF** in `inputs/`.
2. **Transcribe**: `uv run transcribe.py --inbox inputs/ --archive inputs/processed/ --out outputs/json/`.
3. **Cost verify**: `uv run verify.py inputs/processed/<name>.pdf outputs/json/<stem>.json --mode cost`. Open PDF. ✓ if header shows `reconciled` and sample items look right.
4. **Price**: `uv run price.py outputs/json/ --demand 1.0 --jpy-usd 0.0067 -o outputs/priced/`.
5. **Price verify**: `uv run verify.py ... --mode price`. Open PDF. ✓ if markup tiers look right, bands fire where expected, and no items blew past their ceilings.
6. **Shopify CSV**: `uv run to_shopify.py outputs/priced/ --strip-internal -o outputs/pending_upload/shopify_<date>.csv`.
7. **Upload to Shopify** (manual).
8. **Move CSV** `pending_upload/ → uploaded/<YYYY_MM_DD>_shopify.csv` — audit trail.

Three QA gates: cost verify (step 3), price verify (step 5), and visual Shopify preview before upload (step 7).

Regression anchor: SPEC §16 worked examples. Both the Louis Vuitton Neverfull
and Chanel Lambskin Handbag pricing results should be reproducible end-to-end.

---

## 7. What ships in v1 vs v2

**v1 (spec-compliant cost + price on both samples)** — Phases 1, 3, 4, 5.
Skip material + garment length (Phase 2) and leave Buyee markup using a
conservative default tier. Only impacts Buyee markup accuracy, not BrandStreet.

**v2 (full spec)** — add material + garment length extraction (Phase 2),
polish verify PDFs, directory renames (Phase 6).

**v3 (beyond the spec)** — Gmail auto-ingest (already scoped in TODO.md),
Buyee Playwright downloader (deferred in TODO.md), demand-multiplier UI.
