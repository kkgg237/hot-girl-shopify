# Progress — Japanese Invoice Transcriber

Working reference for anyone (human or Claude) picking this up later.
Captures what's built, why, what's brittle, what the user has pushed back on,
and what to do next.

---

## Goal

**Near-term (Kat's vintage shop):**
Take supplier invoices (Buyee breakdowns in JPY, Brand Street Tokyo in USD,
eventually others) and produce a Shopify inventory CSV with accurate landed
cost, algorithmic pricing, and search-optimized product titles — plus a QA
loop that a human can trust.

**Long-term:**
Turn this into a platform that other vintage/resale operators can use.
Multi-tenant, auth, shared brand lists, pluggable invoice adapters, learned
pricing from actual sell-through, learned title corrections from manual edits.

---

## Architecture

```
PDFs  ──▶  transcribe.py  ──▶  output/*.json      ──▶  to_shopify.py  ──▶  shopify.csv
             │                   │                 │
             ├── Opus pass        │                 ├── price.py   ──▶ priced/*.json  (offline)
             ├── regex backfill   │                 │
             └── Haiku backfill   │                 └── app.py     (Streamlit UI, 3 tabs)
                                  │
                                  ▼
                             verify.py
                             (print QA PDF)
```

**Modules (Python, self-contained via `uv` PEP 723 headers):**

| File | Purpose |
|---|---|
| `costs.py` | Pydantic models (`Invoice`, `LineItem`, `FeeLine`) + `InvoiceView` that joins the four Buyee tables and computes landed cost. Constants: handling/import rates, exchange rate, fallback intl. 19 structured fields on `LineItem`. |
| `extractors.py` | Regex fallbacks for every structured field the LLM might miss. Material, garment_length, color, era, origin, pattern, model_name, model_size, style_adjectives, plus brand-archetype defaults. |
| `enrichers.py` | Two-pass Haiku backfill — catches weak fields after the main Opus transcription pass. |
| `transcribe.py` | PDF → JSON via Claude Opus 4.7 vision. Strong prompt, plus regex + Haiku enrichment. |
| `pricing.py` | Pure pricing rules (SPEC §7–14): markup curves, price bands, market adjustments, rounding, brand tiers. `compose_title()` with per-category templates. Model-era DB. Canonicalization helpers. |
| `price.py` | CLI that applies `pricing.py` to transcribed JSONs. |
| `to_shopify.py` | JSON → Shopify inventory CSV. Lot expansion, internal columns, full Shopify taxonomy paths. |
| `verify.py` | PDF + JSON → side-by-side verification PDF (original + transcription + cost/price math + reconciliation). |
| `app.py` | Streamlit web UI. Three-tab flow: **Cost review** → **Pricing & QA** → **Export**. Editable tables, save-back-to-JSON, Arial Nova editorial aesthetic. |
| `title_corrections.jsonl` | Auto-generated log of every manual title override — feeds future prompt few-shots. |
| `SPEC.md` (`../ps_inventory_spec/`) | **Source of truth** — pre-existing pricing spec. |
| `PLAN.md` | Gap analysis vs spec + phased roadmap. |
| `TODO.md` | Ingestion automation ideas (Gmail, Buyee scraping). |

**External dependencies:**
- Anthropic API — Claude Opus 4.7 (main pass) + Claude Haiku 4.5 (backfill)
- `pydantic`, `pymupdf`, `streamlit`, `pandas`, `python-dotenv`, `anthropic`

---

## What's built (status)

✅ **Transcription** — Claude Vision reads any PDF or image, returns structured JSON. Handles Japanese & English, extracts four Buyee tables independently, joined in Python by `source_id`.

✅ **Multi-pass extraction pipeline**:
  1. **Opus vision** — primary structural extraction (all 4 tables + per-item fields)
  2. **Regex fallback** (`extractors.py`) — fills material/color/era/pattern/origin/model/style when terse
  3. **Haiku backfill** (`enrichers.py`) — second-pass LLM for items still weak after regex
  4. **Brand archetype defaults** — Burberry trench → Nova Check + Cotton + Beige, LV bag → Monogram, Chanel bag → Lambskin, Fendi shirt → Cotton

✅ **Cost math (spec-compliant w/ spec deviation)** — Buyee: subtotal + per-item commission + domestic ship + service fee (all joined by source_id) + equal-split intl + equal-split customs. BrandStreet: subtotal + **10% handling** + **15% import**, additive (not compounded). Fallback $20 USD intl if missing. Customs duty extracted if present.

✅ **Pricing pipeline** — linear interp markup (BS), additive markup (Buyee), brand tiers (luxury / mid / standard), price bands with re-clamp, market adjustments for ~30 (vendor, type) pairs, demand multiplier, psychological rounding (25/45/75/95). Manual price override via `override_price`.

✅ **Title composition** — `compose_title()` produces Shopify titles in a strict structured format:
```
[4-digit year only] Brand [Model] [Size] [Color] [Style Adjectives] [Pattern] [Material] [Origin] [Length] Category
```
Per-category templates (BAG / SUNGLASSES / OUTERWEAR / DRESS / SHOES / DEFAULT).
Title case with acronym preservation (CC, LV, GG, 2WAY).
Hyphen/slash capitalization (Black/Silver, Cache-Coeur → Wrap).
Multi-dedup for compound types (Hand Bag ≈ Handbag, Flower Mesh ⊃ Mesh).
Pure structured concatenation — NEVER leaks description text.
Respects `override_title`.

✅ **Structured fields on LineItem** (all extractable, regex-backfilled, editable):
- `detected_brand`, `product_type`, `material`, `garment_length`
- `era` (year or decade — decade not shown in title), `color`, `pattern`, `origin`
- `model_name` (Speedy, Neverfull, Mamma Baguette, Classic Flap, Pochette Accessoires, etc.)
- `model_size` (MM/PM/GM/BB or numeric after bag-model keyword)
- `style_adjectives` — ordered string with 4 buckets: silhouette, neckline, fabric-detail (up to 3 hits), sleeve. Categorical silhouettes (Wrap, A-Line, Sheath) move to the end to read as compounds with the type.

✅ **Override infrastructure** — `override_title`, `override_vendor`, `override_price` on `LineItem`. UI edits save back to JSON. Override price bypasses the pricing pipeline with a warning. Vendor defaults to `Vintage` when no brand detected.

✅ **Model-era database** — 30 signature brand-model pairs mapped to decades (e.g. Fendi Mamma Baguette → 90's, Celine Boogie → 00's, LV Monogram Vernis → 00's). Auto-fills era when the description doesn't state a year.

✅ **Shopify CSV** — exact template match, full taxonomy paths (including the specific types: Trench Coat, Ballet Flats, Heels, T-Shirt, etc.), lot expansion with `[REVIEW]` tag, internal `_Markup`/`_Base Price`/`_source_file` columns strippable. Tags column currently blank per user request.

✅ **Verification PDF** — three-column layout: original invoice page | transcription + cost/pricing breakdown per item | reconciliation sidebar. CJK font fallback via Hiragino Sans GB.

✅ **Web UI (Streamlit)** — Arial Nova editorial aesthetic, three-stage tabs:
  1. **Cost review** — reconciliation banner, shared-input cards (items, handling%, import%, invoice total, total landed), per-item cost input table (fully editable), totals strip
  2. **Pricing & QA** — demand slider, hero metrics (cost basis / expected revenue / gross margin / invoice total), alerts panel, item table with Shopify-bound + QA-only columns, editable Title/Vendor/Type/Variant Price
  3. **Export** — summary, pre-upload checklist, download CSV button

✅ **Editable tables with save-back** — Cost tab edits persist brand/title/model/era/color/style/pattern/material/origin/type/length/qty. Pricing tab edits persist title/vendor/type/variant-price. Save button writes diff to JSON, logs title corrections to `title_corrections.jsonl`.

✅ **Override logging** — every manual title override captured as `(computed_title, override_title, context)` pair for future prompt few-shots.

---

## User feedback received + how we addressed it

Cataloged chronologically so future sessions know what landed and why:

### Aesthetic / UI
| Feedback | Response |
|---|---|
| "I hate the formatting. Think like a UI/UX designer. Prioritize QA access." | Full redesign: 3-tab flow, hero metrics, alerts panel surfaced front-and-center, items sorted risk-first (was: dense table with buried warnings). |
| "Use Arial Nova font" | Swapped both Playfair Display + Red Hat Text for Arial Nova with Arial fallback. |
| "uploadupload button is broken" | Streamlit 1.35+ Material Symbols icons rendered as literal text because we never imported the font. Added defensive CSS to hide icon ligatures globally (`[data-testid*="Icon"]`, `.material-symbols`). |
| "Hover is broken on the Browse button" | `font-size: 0` on button + `::after` for the label. Hover state also got explicit focus/focus-visible rules. |
| "Arrow_down arrow_down overlap on expander" | Same Material Symbols issue — already covered by the global hide rule. |
| "I don't need the tags as editable columns — I like the editable columns as before" | Misread by Claude initially. Editable columns restored; Tags stays blank in the Shopify CSV. |

### Cost math
| Feedback | Response |
|---|---|
| "Break out handling and import. Assume import 15%, put on dashboard. Handling 15% to cover my time + fixed costs. Show total landed next to invoice total." | Split `HANDLING_FACTOR × IMPORT_TAX_FACTOR` (1.20 × 1.15 compounded) into `HANDLING_RATE = 0.15` + `IMPORT_TAX_RATE = 0.15` additive. Added "Total landed" + "Handling (assumed)" + "Import tax (assumed)" cards to the Cost tab headline strip. |
| "Make BrandStreet handling 10% actually" | `HANDLING_RATE = 0.10`. All UI labels now read the constants dynamically. |
| "Rely on SPEC as source of truth" | Adopted spec's equal-split cost math (was: pro-rating by subtotal). Kept commission fees because real money even though spec doesn't mention them. Added BrandStreet 1.20 × 1.15 uplift, then later user-adjusted to 1.10 × 1.15 additive. |
| "Can we add an intermediate QA step for cost inputs in a table" | Built the Cost Review tab. Every column that feeds landed cost in one place. Reconciliation banner as go/no-go gate. |

### Titles
| Feedback | Response |
|---|---|
| "Strict format: [year/era] + Brand + Color (or Multicolor) + Adjectives (Material, Made in USA) + Category. This should be really strict." | Rewrote `compose_title()` as pure structured concatenation. Never leaks description text. Added fields for era, color, pattern, origin. |
| "Brands should be first letter capital + title case" | `canon_brand()` handles it — aliases for known brands, title-case fallback for unknowns. Applied everywhere brands display. |
| "If no brand, default to 'Vintage'" | Hard-coded default. Also applied in `to_shopify.py` so the Shopify Vendor column never shows "Buyee" or "Brand Street Tokyo". |
| "I like 1, 2, 4, 5, 7, 8 — not 3 (niche colors), don't do 6 (condition adjective)" | Implemented model_name (#1), model_size (#2), model-era DB (#4), Haiku backfill (#5), per-category templates (#7), override logging (#8). |
| "Spot-check: Chanel ballet flats (l1215704505) should include 'Ballet Flats' not 'Shoes'" | Split footwear into specific types (Ballet Flats, Heels, Pumps, Boots, Sneakers, Sandals, Loafers, Mules, Slides). All roll up to `Apparel & Accessories > Shoes` in the Shopify GCP path. |
| "Spot-check: Burberry trench / Fendi Zucca shirt / Mamma Baguette need more detail, no 'late 90's'" | Added brand-archetype defaults (Burberry Trench → Beige + Nova Check + Cotton). Promoted `Trench Coat` / `Shirt` / `T-Shirt` to distinct types. Stripped all `late` / `early` / `mid-` / `pre-` era qualifiers. Size `40` on clothing no longer mis-extracted as bag `model_size`. |
| "Add more adjectives from the translation" | Built `style_adjectives` system — 4 regex buckets (silhouette, neckline, fabric-detail, sleeve). Fabric-detail bucket allows up to 3 hits (for Mesh + Corsage + Embroidered layering). |
| "Only numbers for eras, no Y2K" | Y2K regex still matches but canonicalizes to `"00's"`. No letter-based era values anywhere. |
| "Where is Cache-Coeur coming from? Should be 'Wrap'. Examples: 'Flower Mesh Corsage Wrap Top' and 'Belted V Neck Mesh Long Sleeve Top'" | `Cache-Coeur → Wrap` (was the French translation of カシュクール). `V-Neck → V Neck` (space not hyphen). Added `Flower Mesh` as higher-priority fabric match. Removed `Power Net` from vocab. `Blouse → Top`. Categorical silhouettes (Wrap, A-Line, Sheath) moved to end of style chain so `Wrap Top` reads as a compound. Dedup so `Mesh` is skipped when `Flower Mesh` already matched. |
| "Era: decades don't belong in titles" | `compose_title()` only includes era in the output if it's a 4-digit year. Decade labels (`90's`, `00's`) stay in the JSON as metadata but don't appear in titles. |

### Data quality
| Feedback | Response |
|---|---|
| "QC the Buyee invoice — step through cost calculation in a table" | Generated a wide stepped audit table showing every input column per item + pricing pipeline step. Surfaces ceiling hits, thin-margin warnings, brand tier gaps. |
| "Add a column for proposed Shopify title" | `compose_title()` column in the Cost review table. Single source of truth used by UI display + CSV export. |
| "Pricing tab should be table format matching Shopify CSV" | Rebuilt the Pricing tab as a table with both QA columns (left, greyed) and Shopify-bound columns (right). Matches the actual CSV export. |

### Feature scope
| Feedback | Response |
|---|---|
| "I don't want to be using tags in Shopify yet" | `make_tags()` returns empty string. Infrastructure (`search_keywords()` in extractors.py) preserved for later re-enable. |
| "Allow the tables to be edited and also saved as such" | `st.data_editor` for both Cost + Pricing tabs with Save-edits buttons. Diffs by source_id, persists mutations to `output/<stem>.json`. Scoped per-invoice keys to prevent stale edits across invoice switches. |

---

## Key design decisions (updated)

### 1. LLM vision > regex parsing
Primary extraction via Claude Opus 4.7. Regex + Haiku fallbacks catch misses.

### 2. Three-stage extraction pipeline
1. **Opus** — structural pass (all tables, per-item base fields)
2. **Regex** (`fill_missing_fields`) — fills any null structured fields from description text
3. **Haiku** (`backfill_via_haiku`) — cheap second-pass LLM for items still weak
4. **Brand archetype defaults** — fills canonical brand signatures (Burberry Nova Check, LV Monogram, Chanel Lambskin) when item is blank

### 3. Four-table join in Python, not in the LLM
Auditable. Each fee table total must equal the invoice's reported aggregate. Orphan detection for transcription bugs.

### 4. Cost and pricing are separate concerns
Separate modules, separate CLIs, separate UI tabs. Iterate on each without reprocessing the other.

### 5. Spec is source of truth; deviations documented
Spec: 1.20 × 1.15 compounded. We use 1.10 + 1.15 additive per user direction. Noted in `costs.py` comments and PLAN.md.

### 6. Single `compose_title()` drives all title surfaces
UI Cost tab → Pricing tab → CSV export. One function, one source of truth. Respects `override_title`.

### 7. Override pattern for manual edits
Parallel `override_*` fields on LineItem preserve overrides across re-transcriptions. Override logging captures the diff for future few-shot training.

### 8. Strict structured title composition — no description leak
Pure structured concatenation. Missing fields skipped. Per-category templates. Categorical silhouettes read as compound types (Wrap Top, A-Line Dress).

### 9. Era in title only if 4-digit year
Decades are metadata, not SEO. `90's` / `00's` stay in the JSON for tags, `1997` goes in titles.

### 10. Brand archetypes fill null fields only
Explicit extractions always win. Archetypes backstop terse descriptions without overriding real data.

---

## Regression anchors (updated)

**Sample 1 — Buyee (JPY, 23 items):** `04_08_BuyeeTest_2.pdf`
- Grand Total ¥313,486, computed = invoice ✓
- Σ Landed USD ≈ $2,100 at rate 0.0067
- Fee totals: Commission ¥11,011 · Domestic ¥14,820 · Service ¥6,900 (all match summary)
- Known-good titles:
  - `l1215704505` → `Chanel Pink Lambskin Ballet Flats`
  - `o1193898519` → `Vivienne Tam Black Flower Mesh Corsage Wrap Top`
  - `g1139158210` → `Vivienne Tam Black Belted V Neck Mesh Long Sleeve Top`
  - `c1221895009` → `Burberry Denim Long Skirt` (no `40` leak)
  - `V26031300015` → `Burberry Beige Nova Check Cotton Trench Coat` (brand archetype)
  - `V26031000017` → `90's Fendi Mamma Baguette Handbag` — wait this has decade. Per latest feedback decade shouldn't appear in title. Re-verify: 4-digit-only gate means this title is now just `Fendi Mamma Baguette Handbag`. *See "next time to fix" below.*

**Sample 2 — Brand Street Tokyo (USD, 59 items):** `Invoice - 1000263793726 2.pdf`
- Invoice Total $15,796.00
- Σ Landed USD $19,745 with 10% + 15% additive uplift
- Reconciled ✓
- Known-good titles include `Chanel Brown Tortoise CC Sunglasses`, `Louis Vuitton Monogram Pochette Accessoires Pouch`, `Fendi Mamma Baguette Black Nylon Shoulder Bag`

**Sample 3 — SPEC §16 worked examples (synthetic):**
- BrandStreet LV Neverfull ($280 raw) → final $775
- Buyee Chanel Lambskin Handbag ($308.87 landed) → final $795

When pricing rules change, verify all three.

---

## Newer lessons / gotchas

### Cache-Coeur vs Wrap
カシュクール (Japanese loan-word) = French *cache-coeur* = English *wrap*. Resale buyers search "Wrap Top" — canonicalize Japanese / French loans to the English term. Same pattern applies for other style vocab.

### Categorical vs modifier silhouettes
Silhouettes like `Wrap`, `A-Line`, `Sheath`, `Shift` read as compound types ("Wrap Top") — place at the end of the style chain, right before the category. Silhouettes like `Belted`, `Pleated`, `Cropped`, `Oversized` are modifiers — place at the start.

### Multi-match in fabric bucket
Items often layer multiple fabric details (Y2K mesh pieces typically have Mesh + Corsage or Mesh + Embroidered). Allow up to 3 matches in the fabric-detail bucket. Silhouette/neckline/sleeve stay at one match each (only one applies per garment).

### Substring dedup
`Mesh` should not be added when `Flower Mesh` already matched — check `if name in existing_hit for existing in hits`. Applies within a bucket.

### `model_size` collision with clothing size
Regex must not match raw `40` / `30` / `25` when they refer to clothing sizes (e.g. "Size 40" on a skirt). Numeric model sizes only match when they follow a known bag-model keyword (`Speedy 25`, `Birkin 35`). MM/PM/GM/BB match anywhere since they're unambiguous.

### Era: late / early / mid / pre / post → strip
User explicitly requested no qualifiers. Regex patterns accept `(late |early |mid[- ])?` prefix but capture group returns only the bare decade. MODEL_ERA values updated: `late 90's → 90's`, `pre-2010 → 00's`, etc.

### Era in title ≠ era as metadata
Decade labels (`90's`, `00's`) are useful metadata (for tags, for era inference from model DB) but don't belong in product titles. Only 4-digit years go in titles. Gate via `re.fullmatch(r"\d{4}", era)`.

### Blouse, Shirt, Top, T-Shirt — pick one category
Blouse → Top (broader, more searchable). Shirt stays distinct (button-up connotation). T-Shirt distinct too. Same GCP node under the hood.

### Brand archetypes fill but don't override
Apply AFTER extraction passes, only to null fields. An explicit "Black Burberry Trench" stays Black — archetype wouldn't overwrite to Beige.

### Editable tables need per-invoice keys
Streamlit's `st.data_editor` keeps state via `key=` arg. Scope it per-invoice (`key=f"cost_editor_{source_file}"`) so switching invoices doesn't carry stale edits.

### Streamlit Material Symbols icons render as literal text
We never imported the Material Symbols font. Ligatures like `upload`, `arrow_drop_down`, `check` render as raw text and overlap other UI. Global CSS hide covers it.

### Override title normalization
When user edits a title to something that matches what `compose_title()` would produce, clear `override_title` (don't persist it). Only save when genuinely different.

### Title correction logging
Every override event appends to `title_corrections.jsonl`. Feeds future prompt few-shots. Don't log no-ops (computed == override).

---

## Known weak spots / next to fix

1. **V26031000017 (Fendi Mamma Baguette) currently outputs `Fendi Mamma Baguette Handbag`** after the 4-digit-year gate dropped the era. If the user wants decades back in titles for some categories (especially bags where `90's Mamma Baguette` is a searchable term), add a category allowlist.

2. **Pricing tab table doesn't re-compute on save** — user must refresh the page. Either `st.rerun()` on save, or display a banner that refresh is needed.

3. **`style_adjectives` order for mixed cases** — works for the two Vivienne Tam examples but the general rule (categorical silhouettes last) might deviate for other items. Capture via override log.

4. **Haiku backfill not verified end-to-end** — plumbing is in place but hasn't been tested on a fresh transcription of either sample. Need a fresh run to confirm coverage numbers.

5. **Override fields don't show in the CSV diagnostic columns** — user can't tell at a glance which rows have overrides. Consider an `_override_flags` internal column.

6. **No undo for edits** — if a Save action persists a wrong override, the user has to manually revert. An "Edit history" pane in the UI would help.

---

## Next actions (short-list — see PROGRESS.md §Next actions for full brainstorm)

**Immediate polish:**
- Fix Mamma Baguette decade-in-title regression (#1 above)
- `st.rerun()` on save so prices recompute without manual refresh
- "Edit history" / override indicator column in the Pricing tab

**Next sprint:**
- Gmail auto-ingestion (removes the biggest manual step — see TODO.md)
- Shopify API integration (replace CSV upload with direct draft products)
- Snapshot tests locking in current sample outputs

**Strategic:**
- Multi-tenancy prep (per-shop brand lists, per-shop Shopify creds)
- Learned pricing from sell-through data
- Invoice adapter framework (YAML per vendor)
