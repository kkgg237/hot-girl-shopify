#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "anthropic>=0.40.0",
#   "pydantic>=2.0",
#   "python-dotenv>=1.0",
#   "pyyaml>=6.0",
# ]
# ///
"""Transcribe vintage-shop invoices into structured JSON.

Extracts four independent tables from Buyee breakdowns:
  1. Item Price               → items[]
  2. Commission fee           → commission_fees[]
  3. Domestic Shipping Fee    → domestic_shipping_fees[]
  4. Buyee Service Fee        → service_fees[]

Fee tables carry source_id (Shopping Site(ID)). The join to items happens in
Python (see costs.py), not inside the LLM — more auditable, surfaces orphans.

Simple vendor invoices (Brand Street Tokyo etc.) populate items[] only and
leave the three fee lists empty; landed cost degrades to item subtotal.

Usage:
    uv run transcribe.py <file.pdf>                       # one file
    uv run transcribe.py --inbox inbox/ --out output/     # batch
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from costs import Invoice, InvoiceView, fmt_money
from enrichers import backfill_via_haiku
from extractors import fill_missing_fields


MODEL = "claude-opus-4-7"


SYSTEM_PROMPT = """You transcribe invoices for a vintage clothing & luxury goods reseller.

Two invoice shapes you'll see:

1. **Buyee "Breakdown of Expenses"** (JPY). Extract FOUR independent tables:

   a) **Item Price** table (top of invoice) — one row per purchased item:
      Shopping Site(ID) / Item Name / Quantity / Item Price / Coupon discount / Total Amount
      → goes into `items[]`. The Shopping Site(ID) value (the part in parentheses, e.g.
      "b1221334014", "V26031100112") is the `source_id` — it is THE JOIN KEY.

   b) **Commission fee** breakdown table (in the Breakdown of Other Service Fees section):
      Shopping Site(ID) / Item Name / Price → `commission_fees[]`
      One row per item that had a commission fee. Items without a commission fee
      simply don't appear in this table (do NOT invent zero-entries).

   c) **Domestic Shipping Fee** breakdown table (seller → Buyee):
      Shopping Site(ID) / Item Name / Price → `domestic_shipping_fees[]`

   d) **Buyee Service Fee** breakdown table:
      Shopping Site(ID) / Item Name / Price → `service_fees[]`

   Plus:
   - **Shipping Expenses** section (Domestic Shipping Fee from Buyee to buyer's
     address) → `international_shipping` (single number, invoice-wide).
   - **Customs Duty** line if present (e.g. "関税" / "Customs Duty" / "Import Duty")
     → `customs_duty`. Most Buyee invoices won't have this; set to 0 if absent.

   Set `invoice_type="buyee_breakdown"`.

2. **Simple vendor invoice** (USD etc., e.g. Brand Street Tokyo, DKC, etc.).
   Populate `items[]`; the line/auth code is the source_id. Leave the three
   fee lists as []. Set `invoice_type="vendor_invoice"`.

   **Commission line:** if the invoice has a single commission/markup line at
   the bottom (e.g. "Commission 5% ¥83,218" / "手数料 5%" / "Brokerage fee"),
   populate `commission_line` with the JPY/USD amount AND `commission_line_rate`
   with the rate as a decimal (0.05 for 5%). Do NOT put it in `other_fees` —
   that's the catch-all for unrecognized fees. Per-item commission tables
   (Buyee-style) still go in `commission_fees[]`.

   `other_fees` is for genuinely-uncategorized fees only. If you can identify
   what a fee is for, use the dedicated field.

CRITICAL: source_id values in the fee tables MUST match a source_id in items[] verbatim.
Don't translate them. Don't add whitespace. Don't change case. The Python-side join
depends on exact string equality.

Per-item metadata on LineItem:
- `description_original`: exactly as printed. Japanese stays Japanese.
- `description_english`: clean, short, resale-listing-quality English. Keep brand
  names canonical (CHANEL, Louis Vuitton, Comme des Garçons, Issey Miyake).
- `detected_brand`: top-level brand. Null only if truly unbranded.
- `product_type`: short generic — 'coat', 'dress', 'shoulder bag', 'wallet', 'sunglasses', 'pouch'.
- `condition_notes`: translate 美品=mint, 極上=excellent, デッドストック=deadstock,
  中古品=used. Include size (F40, US S/JP M, 37.5cm).
- `material`: the primary material if identifiable. Use canonical values so
  downstream markup logic can match them:
    Fox Fur, Mink, Weasel Fur, Raccoon Fur, Squirrel Fur, Rabbit Fur,
    Shearling, Fur, Sheepskin, Lambskin,
    Leather, Suede, Pony Hair, Goat Leather,
    Silk, Satin, Cashmere, Wool,
    Denim, Cotton, Polyester, Nylon.
  Translation cues: 毛皮=Fur, フォックス=Fox Fur, ミンク=Mink, ラム=Lambskin,
  シープスキン=Sheepskin, レザー/革=Leather, スエード=Suede, シルク/絹=Silk,
  カシミヤ=Cashmere, デニム=Denim, コットン=Cotton, ウール=Wool, ナイロン=Nylon.
  Null if not stated.
- `garment_length`: one of 'short', 'midi', 'long' for garments (coats, dresses,
  skirts). Cues: ハーフ=midi, ロング=long, ミニ=short, マキシ=long, ひざ丈=midi.
  Null for non-garments (bags, wallets, accessories).
- `color`: ONE primary color (canonical English) — 'Black', 'White', 'Red',
  'Blue', 'Green', 'Brown', 'Beige', 'Grey', 'Pink', 'Purple', 'Yellow',
  'Orange', 'Silver', 'Gold', 'Burgundy', 'Navy'. If 3+ colors present,
  use 'Multicolor'. Translate 黒=Black, 白=White, 赤=Red, 青/ネイビー=Blue or Navy,
  緑=Green, 茶=Brown, ベージュ=Beige, グレー=Grey, ピンク/マゼンタ=Pink, 黄=Yellow.
- `pattern`: signature pattern name if visible — 'Monogram', 'Damier',
  'Matelasse', 'Zucca', 'Nova Check', 'GG Canvas', 'Sherry Line',
  'Intrecciato', 'Tortoise', 'Floral', 'Striped', 'Plaid'. Null if plain.
- `era`: year or decade if determinable. Use '1997' (4-digit year) or
  \"90's\", \"00's\", 'Y2K' (decade). Cues: 'Y2K archive', '90年代',
  '1990's', explicit year in description. Null if unknown.
- `origin`: canonical 'Made in X' if explicitly stated. 'Made in USA',
  'Made in Italy', 'Made in France', 'Made in Japan', 'Made in UK'.
  Cues: アメリカ製/米国製=Made in USA, イタリア製=Made in Italy, フランス製=Made in France,
  日本製=Made in Japan. Null if not stated.
- `model_name`: specific luxury-house model name if recognizable. This is the
  single biggest SEO lever — buyers search for "Speedy 25", "Neverfull", "Birkin",
  "Mamma Baguette", "Classic Flap", "Boy Bag", "Jackie", "Saddle". Extract it
  verbatim when present.
  Common: LV ("Speedy", "Neverfull", "Pochette Accessoires", "Pochette Metis",
  "Alma", "Noé", "Keepall", "Saint Louis", "Agenda", "Lexington"); Chanel
  ("Classic Flap", "2.55", "Boy Bag", "Coco Handle", "Gabrielle", "Cambon",
  "Choco Bar"); Fendi ("Baguette", "Mamma Baguette", "Peekaboo", "Zucca",
  "Zucchino"); Gucci ("Jackie", "Bamboo", "Sherry Line", "GG Marmont",
  "Horsebit"); Dior ("Saddle", "Lady Dior", "Trotter", "Book Tote"); Hermès
  ("Birkin", "Kelly", "Constance", "Evelyne"); Celine ("Luggage", "Boogie",
  "Macadam", "Triomphe"); Prada ("Galleria", "Cahier", "Re-Edition"). Null if
  unrecognizable.
- `model_size`: size variant if present — canonical values: "MM", "PM", "GM",
  "BB", "25", "30", "35", "40", "45". Cues: "Speedy 25" → "25", "Neverfull MM"
  → "MM", "Birkin 35" → "35", "Pochette PM" → "PM". Null if not stated.
- `style_adjectives`: ordered garment descriptors pulled from the description.
  Buckets, in this order — one match per bucket max:
    1. Silhouette / cut: Wrap, Cache-Coeur, Belted, A-Line, Sheath, Pleated,
       Ruched, Draped, Cropped, Oversized, Tailored, Reversible
    2. Neckline: V-Neck, Crew Neck, Scoop Neck, Turtleneck, Halter,
       Off-Shoulder, Mock Neck, Boat Neck, Sweetheart, Mandarin Collar
    3. Sleeve: Long Sleeve, Short Sleeve, 3/4 Sleeve, Cap Sleeve, Puff Sleeve,
       Bell Sleeve, Sleeveless
    4. Fabric detail / embellishment (distinct from material): Mesh, Power Net,
       Lace, Sheer, Ribbed, Knit, Quilted, Corsage, Beaded, Sequined,
       Embroidered, Embellished, Water-Repellent
  Combine matches with single spaces, ordered by bucket. Example:
  "Belted V-Neck Long Sleeve Mesh" or "Cache-Coeur Corsage". Null if no cues.
  Japanese cues: カシュクール=Cache-Coeur, Vネック=V-Neck, 長袖=Long Sleeve,
  半袖=Short Sleeve, ノースリーブ=Sleeveless, メッシュ=Mesh, パワーネット=Power Net,
  ベルテッド=Belted, プリーツ=Pleated, コサージュ=Corsage, 撥水=Water-Repellent.

Numeric rules: JPY integers, USD two decimals. All amounts POSITIVE — coupon_discount
is a positive number we subtract downstream. If a field is missing, use 0 or null.
Never invent numbers. Japanese era dates (令和/平成) → Gregorian ISO.
"""


JSON_INSTRUCTION = """
Return ONLY one JSON object (no markdown fence, no preamble) matching:

{
  "invoice_type": "buyee_breakdown" | "vendor_invoice",
  "vendor_name": str, "vendor_address": str|null,
  "invoice_number": str|null, "invoice_date": "YYYY-MM-DD"|null,
  "currency": "JPY"|"USD"|"EUR"|"GBP",
  "items": [
    {
      "source_platform": str|null,
      "source_id": str,               // JOIN KEY — the (paren) code from Shopping Site(ID)
      "description_original": str, "description_english": str,
      "detected_brand": str|null, "product_type": str|null, "condition_notes": str|null,
      "material": str|null,           // e.g. "Lambskin", "Fox Fur", "Denim". Use canonical values.
      "garment_length": "short"|"midi"|"long"|null,
      "color": str|null,              // canonical single color or "Multicolor"
      "pattern": str|null,            // signature pattern — "Monogram", "Zucca", "Nova Check", etc.
      "era": str|null,                // "1997" or "90's" or "Y2K" — null if unknown
      "origin": str|null,             // "Made in USA", "Made in Italy", etc. — null if unstated
      "model_name": str|null,         // "Speedy", "Neverfull", "Mamma Baguette", "Classic Flap", etc.
      "model_size": str|null,         // "MM", "PM", "25", "30", "35" — null if unstated
      "style_adjectives": str|null,   // ordered descriptors — "Belted V-Neck Long Sleeve Mesh", "Cache-Coeur Corsage"
      "quantity": int, "currency": "JPY"|"USD"|...,
      "item_price": number, "coupon_discount": number
    }
  ],
  "commission_fees":        [ { "source_id": str, "amount": number, "note": str|null } ],
  "domestic_shipping_fees": [ { "source_id": str, "amount": number, "note": str|null } ],
  "service_fees":           [ { "source_id": str, "amount": number, "note": str|null } ],
  "international_shipping": number,
  "customs_duty": number,             // 0 if no customs duty line on invoice
  "commission_line": number,          // single lump-sum commission (e.g. 5% on a vendor invoice). 0 if not present.
  "commission_line_rate": number|null, // 0.05 for 5%; null if not stated as a percentage
  "other_fees": number,               // ONLY for unrecognized fees — prefer dedicated fields
  "tax": number,
  "grand_total": number,
  "notes": str|null
}

For vendor_invoice, the three fee lists are [].
source_id values in fee lists MUST match an item's source_id verbatim.
Output nothing but the JSON.
"""


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def load_source(path: Path) -> dict:
    mime, _ = mimetypes.guess_type(path.name)
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    if mime == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    if mime and mime.startswith("image/"):
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    raise ValueError(f"Unsupported file type: {path.name} (detected {mime!r}). Use an image or PDF.")


def _extract_json(text: str) -> dict:
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", s, re.DOTALL)
    if m:
        s = m.group(1)
    return json.loads(s)


def transcribe(path: Path, client: anthropic.Anthropic) -> Invoice:
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT + JSON_INSTRUCTION,
        messages=[
            {
                "role": "user",
                "content": [
                    load_source(path),
                    {"type": "text", "text": f"Transcribe this invoice. Source filename: {path.name}"},
                ],
            }
        ],
    ) as stream:
        message = stream.get_final_message()
    text = next((b.text for b in message.content if b.type == "text"), "")
    data = _extract_json(text)
    try:
        invoice = Invoice(**data)
    except ValidationError as e:
        raise RuntimeError(f"JSON didn't match Invoice schema:\n{e}\n\nRaw:\n{text[:500]}") from e

    # Regex fallback: fill material / garment_length / color / era / origin / pattern / model
    stats = fill_missing_fields(invoice)
    filled = {k: v for k, v in stats.items() if v}
    if filled:
        print(f"    regex fallback filled: {filled}", file=sys.stderr)

    # Two-pass LLM enrichment — targeted Haiku call for items where the primary
    # pass didn't pick up color/pattern/era/model. Skips invoices where nothing
    # is weak (saves the API call).
    try:
        hstats = backfill_via_haiku(invoice, client=client)
        if hstats.get("candidates"):
            noise_keys = {"candidates", "error", "parse_error", "raw"}
            filled_haiku = {k: v for k, v in hstats.items() if k not in noise_keys and v}
            if filled_haiku:
                print(f"    haiku backfill filled: {filled_haiku}", file=sys.stderr)
    except Exception as e:
        print(f"    haiku backfill skipped: {e}", file=sys.stderr)

    return invoice


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_text(invoice: Invoice) -> str:
    view = InvoiceView(invoice)
    ccy = invoice.currency
    lines = []
    lines.append("=" * 78)
    lines.append(f"Vendor: {invoice.vendor_name}  ({invoice.invoice_type})")
    if invoice.invoice_number:
        lines.append(f"Invoice #: {invoice.invoice_number}")
    if invoice.invoice_date:
        lines.append(f"Date: {invoice.invoice_date}")
    lines.append(f"Currency: {ccy}")
    lines.append("-" * 78)

    for i, item in enumerate(invoice.items, 1):
        brand = item.detected_brand or "—"
        lines.append(f"{i:3}. [{brand}] {item.description_english}")
        if item.description_original != item.description_english:
            lines.append(f"     原文: {item.description_original[:80]}")
        meta = " · ".join(filter(None, [
            (item.source_platform + f"({item.source_id})") if item.source_platform else item.source_id,
            item.product_type,
            item.material,
            item.garment_length,
            item.condition_notes,
        ]))
        if meta:
            lines.append(f"     {meta}")
        b = view.breakdown(item)
        parts = [f"price {fmt_money(b['item_price'], ccy)}"]
        if b["coupon"]:
            parts.append(f"− coupon {fmt_money(b['coupon'], ccy)}")
        if b["commission"]:
            parts.append(f"+ comm {fmt_money(b['commission'], ccy)}")
        if b["domestic_shipping"]:
            parts.append(f"+ dom-ship {fmt_money(b['domestic_shipping'], ccy)}")
        if b["service"]:
            parts.append(f"+ svc {fmt_money(b['service'], ccy)}")
        if b["intl_share"]:
            parts.append(f"+ intl {fmt_money(b['intl_share'], ccy)}")
        if b["customs_share"]:
            parts.append(f"+ customs {fmt_money(b['customs_share'], ccy)}")
        if b["handling_amount"]:
            parts.append(f"+ handling {fmt_money(b['handling_amount'], ccy)}")
        if b["import_amount"]:
            parts.append(f"+ import {fmt_money(b['import_amount'], ccy)}")
        parts.append(f"→ landed {fmt_money(b['landed_native'], ccy)}")
        parts.append(f"(\u2248 ${b['landed_usd']:,.2f})")
        lines.append(f"     {' '.join(parts)}")

    lines.append("-" * 78)
    r = view.reconciliation()
    fmt = lambda v: fmt_money(v, ccy)
    lines.append(f"Item subtotals:             {fmt(r['items_subtotal'])}")
    if r["commission"]:
        lines.append(f"Commission fees (join):     {fmt(r['commission'])}")
    if r["domestic_shipping"]:
        lines.append(f"Domestic shipping (join):   {fmt(r['domestic_shipping'])}")
    if r["service"]:
        lines.append(f"Service fees (join):        {fmt(r['service'])}")
    if r["international_shipping"]:
        lines.append(f"International shipping:     {fmt(r['international_shipping'])}")
    elif r["intl_fallback_applied"]:
        lines.append(f"Intl shipping (fallback):   {fmt(view.effective_intl)}  [$20 USD default]")
    if r["customs_duty"]:
        lines.append(f"Customs duty:               {fmt(r['customs_duty'])}")
    if r["other_fees"]:
        lines.append(f"Other fees:                 {fmt(r['other_fees'])}")
    if r["tax"]:
        lines.append(f"Tax:                        {fmt(r['tax'])}")
    lines.append(f"Computed total:             {fmt(r['computed'])}")
    lines.append(f"INVOICE GRAND TOTAL:        {fmt(r['invoice_total'])}")
    lines.append(f"Σ Landed cost (USD):        ${r['landed_usd_sum']:,.2f}   ← goes into Shopify Cost per Item column")
    if r["reconciled"]:
        lines.append("Reconciled: ✓")
    else:
        lines.append(f"Reconciled: ⚠  Δ {fmt(r['delta'])}")

    # Orphan warnings
    orph = view.orphan_fees()
    for table, sids in orph.items():
        if sids:
            lines.append(f"⚠  {len(sids)} orphan {table} fee(s) not joined to items: {sids}")

    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def process_file(path: Path, client: anthropic.Anthropic, out_dir: Optional[Path]) -> Invoice:
    invoice = transcribe(path, client)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{path.stem}.json"
        json_path.write_text(invoice.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    return invoice


def main() -> int:
    load_dotenv(find_dotenv(usecwd=True), override=True)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", type=Path, help="Single image or PDF")
    parser.add_argument("--inbox", type=Path, help="Process all PDFs/images in this folder")
    parser.add_argument("--out", type=Path, default=Path("output"), help="Output directory for JSON")
    parser.add_argument("--archive", type=Path, help="Move processed files from --inbox here")
    parser.add_argument("--format", choices=["json", "text", "both"], default="both")
    args = parser.parse_args()

    if not args.file and not args.inbox:
        parser.error("Provide a file or --inbox folder")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set (check .env)", file=sys.stderr)
        return 1

    client = anthropic.Anthropic(timeout=180.0, max_retries=2)

    if args.inbox:
        if not args.inbox.is_dir():
            print(f"--inbox folder not found: {args.inbox}", file=sys.stderr)
            return 1
        targets = sorted(p for p in args.inbox.iterdir() if p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"})
        if not targets:
            print(f"No PDFs/images found in {args.inbox}", file=sys.stderr)
            return 0
    else:
        if not args.file.exists():
            print(f"File not found: {args.file}", file=sys.stderr)
            return 1
        targets = [args.file]

    for path in targets:
        print(f"\n>>> {path.name}", file=sys.stderr)
        try:
            invoice = process_file(path, client, args.out if args.format in ("json", "both") else None)
        except anthropic.APIError as e:
            print(f"    API error: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"    Failed: {e}", file=sys.stderr)
            continue

        if args.format in ("text", "both"):
            print(format_text(invoice))

        if args.archive and args.inbox:
            args.archive.mkdir(parents=True, exist_ok=True)
            date_prefix = invoice.invoice_date or datetime.now().strftime("%Y-%m-%d")
            shutil.move(str(path), str(args.archive / f"{date_prefix}_{path.name}"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
