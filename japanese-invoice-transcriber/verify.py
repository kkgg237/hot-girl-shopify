#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24", "pydantic>=2.0"]
# ///
"""Build a side-by-side verification PDF.

Three columns per page:
    ┌─────────────────────────┬──────────────────────┬───────────────┐
    │  Original invoice page  │  Items extracted,    │  Numbers:     │
    │  (rendered image)       │  with the full cost  │  per-fee-     │
    │                         │  breakdown from the  │  table totals │
    │                         │  four-table join     │  + reconcile  │
    └─────────────────────────┴──────────────────────┴───────────────┘

Each item shows every component that went into landed cost:
    price − coupon + commission + dom-ship + service + intl-share → landed

Usage:
    uv run verify.py samples/<name>.pdf output/<name>.json
    uv run verify.py <pdf> <json> [-o verify/<stem>_verification.pdf]
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import sys
from pathlib import Path

import pymupdf as fitz

from costs import HANDLING_RATE, IMPORT_TAX_RATE, Invoice, InvoiceView, fmt_money
from pricing import price_item


PAGE_W, PAGE_H = 792, 612
MARGIN = 12
HEADER_H = 34
COL_LEFT_FRAC = 0.44
COL_RIGHT_FRAC = 0.22
GUTTER = 8

COLOR_RULE = (0.75, 0.75, 0.75)
COLOR_HEADER_BG = (0.93, 0.93, 0.95)
COLOR_PANEL_BG = (0.98, 0.98, 0.98)

FONT_STACK = "'Helvetica', 'Arial', 'Hiragino Sans GB', 'Hiragino Sans', 'Arial Unicode MS', sans-serif"
MONO_STACK = "'Courier New', 'Courier', 'Hiragino Sans GB', monospace"

BASE_CSS = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: {FONT_STACK}; font-size: 7.5pt; color: #111; line-height: 1.25; }}
.label {{ color: #555; }}
.mono {{ font-family: {MONO_STACK}; }}
.small {{ font-size: 6.5pt; color: #555; }}
.orig {{ font-family: {MONO_STACK}; font-size: 6.5pt; color: #444; }}
.item {{ padding: 3pt 0; border-bottom: 0.3pt solid #d8d8d8; }}
.item:last-child {{ border-bottom: none; }}
.item .title {{ font-weight: bold; font-size: 7.8pt; }}
.brand-tag {{ background: #eef; padding: 0 2pt; border-radius: 2pt; font-size: 6.5pt; }}
.id-tag {{ background: #fef3d0; padding: 0 2pt; border-radius: 2pt; font-size: 6.2pt; font-family: {MONO_STACK}; }}
table {{ width: 100%; border-collapse: collapse; font-size: 7pt; }}
td {{ padding: 1.5pt 0; vertical-align: top; }}
td.v {{ font-family: {MONO_STACK}; text-align: right; white-space: nowrap; }}
.cost-table {{ margin-top: 2pt; font-size: 6.8pt; }}
.cost-table td {{ padding: 0.8pt 0; }}
.cost-table td.v {{ font-size: 6.8pt; color: #003366; }}
.cost-table tr.landed {{ border-top: 0.4pt solid #999; }}
.cost-table tr.landed td {{ padding-top: 2pt; font-weight: bold; }}
.price-table {{ margin-top: 2pt; font-size: 6.8pt; background: #fafcff; }}
.price-table td {{ padding: 0.8pt 0; }}
.price-table td.v {{ font-size: 6.8pt; color: #004d4d; }}
.price-table tr.final {{ border-top: 0.4pt solid #007777; }}
.price-table tr.final td {{ padding-top: 2pt; font-weight: bold; color: #006600; font-size: 8pt; }}
.warning-badge {{ background: #fff4e0; color: #cc6600; padding: 0 2pt; border-radius: 2pt; font-size: 5.5pt; }}
.section-head {{ font-weight: bold; font-size: 8pt; padding: 3pt 0 1pt; border-bottom: 0.4pt solid #bbb; margin-bottom: 2pt; }}
.rule {{ border-top: 0.4pt solid #bbb; margin: 3pt 0; }}
.ok {{ color: #1a8a1a; font-weight: bold; }}
.warn {{ color: #cc4010; font-weight: bold; }}
.footer {{ font-size: 5.5pt; color: #888; margin-top: 4pt; }}
"""


def esc(s: str | None) -> str:
    return html_mod.escape(s) if s else ""


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def build_header_html(invoice: Invoice, recon: dict) -> str:
    meta_bits = [
        esc(invoice.vendor_name),
        esc(invoice.invoice_number or ""),
        esc(invoice.invoice_date or ""),
        esc(invoice.invoice_type),
    ]
    meta = " / ".join(b for b in meta_bits if b)
    grand = fmt_money(recon["invoice_total"], invoice.currency)
    if recon["reconciled"]:
        status_html = '<span class="ok">\u2713 reconciled</span>'
    else:
        status_html = f'<span class="warn">\u0394 {esc(fmt_money(abs(recon["delta"]), invoice.currency))}</span>'
    return f"""<html><head><style>{BASE_CSS}
.header {{ display: flex; justify-content: space-between; align-items: center;
           padding: 6pt 10pt; background: #eeeef5; }}
.header .meta {{ font-size: 10pt; font-weight: bold; }}
.header .total {{ font-size: 10pt; text-align: right; }}
</style></head><body>
<div class="header">
  <div class="meta">{meta}</div>
  <div class="total">Total {esc(grand)} &nbsp;\u00b7&nbsp; {status_html}</div>
</div>
</body></html>"""


def build_items_html(items_chunk: list, view: InvoiceView, start_idx: int, total_items: int, demand: float) -> str:
    inv = view.inv
    ccy = inv.currency
    fmt = lambda v: esc(fmt_money(v, ccy))
    rows = []
    for i, item in enumerate(items_chunk, start=start_idx + 1):
        brand = item.detected_brand or "\u2014"
        title = f'{i}. <span class="brand-tag">{esc(brand)}</span> <span class="id-tag">{esc(item.source_id)}</span> {esc(item.description_english)}'

        meta_bits = []
        if item.source_platform:
            meta_bits.append(esc(item.source_platform))
        if item.product_type:
            meta_bits.append(esc(item.product_type))
        if item.condition_notes:
            meta_bits.append(esc(item.condition_notes))
        meta = " \u00b7 ".join(meta_bits)
        orig = esc(item.description_original)

        b = view.breakdown(item)
        # Cost table — show each component that's nonzero
        cost_rows = [f'<tr><td>price × qty</td><td class="v">{fmt(b["item_price"])}</td></tr>']
        if b["coupon"]:
            cost_rows.append(f'<tr><td>\u2212 coupon</td><td class="v">\u2212 {fmt(b["coupon"])}</td></tr>')
        if b["commission"]:
            cost_rows.append(f'<tr><td>+ commission</td><td class="v">{fmt(b["commission"])}</td></tr>')
        if b["domestic_shipping"]:
            cost_rows.append(f'<tr><td>+ domestic ship</td><td class="v">{fmt(b["domestic_shipping"])}</td></tr>')
        if b["service"]:
            cost_rows.append(f'<tr><td>+ service fee</td><td class="v">{fmt(b["service"])}</td></tr>')
        if b["intl_share"]:
            cost_rows.append(f'<tr><td>+ intl ship (\u00f7{view.n_items})</td><td class="v">{fmt(b["intl_share"])}</td></tr>')
        if b["customs_share"]:
            cost_rows.append(f'<tr><td>+ customs (\u00f7{view.n_items})</td><td class="v">{fmt(b["customs_share"])}</td></tr>')
        if b["handling_amount"]:
            cost_rows.append(f'<tr><td>+ handling ({HANDLING_RATE*100:.0f}%)</td><td class="v">{fmt(b["handling_amount"])}</td></tr>')
        if b["import_amount"]:
            cost_rows.append(f'<tr><td>+ import tax ({IMPORT_TAX_RATE*100:.0f}%)</td><td class="v">{fmt(b["import_amount"])}</td></tr>')
        cost_rows.append(f'<tr class="landed"><td>landed</td><td class="v">{fmt(b["landed_native"])}</td></tr>')
        if inv.currency != "USD":
            cost_rows.append(f'<tr><td>(in USD)</td><td class="v">${b["landed_usd"]:,.2f}</td></tr>')

        # Pricing
        pr = price_item(item, view, demand=demand)
        price_rows = [
            f'<tr><td>unit cost (USD)</td><td class="v">${pr.unit_cost_usd:,.2f}</td></tr>',
        ]
        if view.inv.invoice_type == "buyee_breakdown":
            price_rows.append(f'<tr><td>× 1.2 handling</td><td class="v">${pr.markup_applied_to:,.2f}</td></tr>')
        price_rows.append(f'<tr><td>× markup</td><td class="v">{pr.markup:.2f}× = ${pr.base_price:,.2f}</td></tr>')
        if pr.band_floor is not None or pr.band_ceil is not None:
            band_str = f"[${pr.band_floor or 0}–${pr.band_ceil or '∞'}]"
            price_rows.append(f'<tr><td>band {band_str}</td><td class="v">${pr.after_band:,.2f}</td></tr>')
        if pr.market_adjustment != 1.0:
            price_rows.append(f'<tr><td>× market adj ({pr.market_adjustment})</td><td class="v">${pr.after_adjustment:,.2f}</td></tr>')
        if demand != 1.0:
            price_rows.append(f'<tr><td>× demand ({demand})</td><td class="v">${pr.after_demand:,.2f}</td></tr>')
        price_rows.append(f'<tr class="final"><td>Variant Price</td><td class="v">${pr.rounded_price}</td></tr>')

        warnings_html = ""
        if pr.warnings:
            bits = [f'<span class="warning-badge">{esc(w)}</span>' for w in pr.warnings]
            warnings_html = f'<div class="small" style="margin-top:1pt">{" ".join(bits)}</div>'

        rows.append(f"""
<div class="item">
  <div class="title">{title}</div>
  <div class="orig">{orig}</div>
  {f'<div class="small">{meta}</div>' if meta else ''}
  <table class="cost-table">{''.join(cost_rows)}</table>
  <table class="price-table">{''.join(price_rows)}</table>
  {warnings_html}
</div>
""")

    chunk_end = start_idx + len(items_chunk)
    return f"""<html><head><style>{BASE_CSS}</style></head><body>
<div class="section-head">Items {start_idx + 1}\u2013{chunk_end} of {total_items}</div>
{''.join(rows)}
</body></html>"""


def build_numbers_html(view: InvoiceView, items_chunk: list, demand: float) -> str:
    inv = view.inv
    ccy = inv.currency
    fmt = lambda v: esc(fmt_money(v, ccy))

    # Chunk rollup
    chunk_breakdowns = [view.breakdown(i) for i in items_chunk]
    chunk_sub = sum(b["subtotal"] for b in chunk_breakdowns)
    chunk_comm = sum(b["commission"] for b in chunk_breakdowns)
    chunk_dom = sum(b["domestic_shipping"] for b in chunk_breakdowns)
    chunk_svc = sum(b["service"] for b in chunk_breakdowns)
    chunk_intl = sum(b["intl_share"] for b in chunk_breakdowns)
    chunk_customs = sum(b["customs_share"] for b in chunk_breakdowns)
    chunk_handling = sum(b["handling_amount"] for b in chunk_breakdowns)
    chunk_import = sum(b["import_amount"] for b in chunk_breakdowns)
    chunk_landed = sum(b["landed_native"] for b in chunk_breakdowns)
    chunk_landed_usd = sum(b["landed_usd"] for b in chunk_breakdowns)

    r = view.reconciliation()

    # Invoice-wide pricing stats
    all_prices = [price_item(i, view, demand=demand) for i in inv.items]
    total_variant_price = sum(p.rounded_price for p in all_prices)
    avg_markup = sum(p.markup for p in all_prices) / max(len(all_prices), 1)

    def row(label: str, value: str, cls: str = "") -> str:
        return f'<tr class="{cls}"><td>{label}</td><td class="v">{value}</td></tr>'

    chunk_rows = [row("Item subtotal", fmt(chunk_sub))]
    if chunk_comm:
        chunk_rows.append(row("Commission", fmt(chunk_comm)))
    if chunk_dom:
        chunk_rows.append(row("Dom. shipping", fmt(chunk_dom)))
    if chunk_svc:
        chunk_rows.append(row("Service fee", fmt(chunk_svc)))
    if chunk_intl:
        chunk_rows.append(row("Intl ship (\u00f7n)", fmt(chunk_intl)))
    if chunk_customs:
        chunk_rows.append(row("Customs (\u00f7n)", fmt(chunk_customs)))
    if chunk_handling:
        chunk_rows.append(row(f"Handling {HANDLING_RATE*100:.0f}%", fmt(chunk_handling)))
    if chunk_import:
        chunk_rows.append(row(f"Import {IMPORT_TAX_RATE*100:.0f}%", fmt(chunk_import)))
    chunk_rows.append(row("<b>Landed \u03a3</b>", f"<b>{fmt(chunk_landed)}</b>"))
    if inv.currency != "USD":
        chunk_rows.append(row("Landed \u03a3 (USD)", f"${chunk_landed_usd:,.2f}"))

    full_rows = [row(f"Items ({len(inv.items)})", fmt(r["items_subtotal"]))]
    if r["commission"]:
        full_rows.append(row(f'Commission ({len(inv.commission_fees)} rows)', fmt(r["commission"])))
    if r["domestic_shipping"]:
        full_rows.append(row(f'Dom. shipping ({len(inv.domestic_shipping_fees)})', fmt(r["domestic_shipping"])))
    if r["service"]:
        full_rows.append(row(f'Service fee ({len(inv.service_fees)})', fmt(r["service"])))
    if r["international_shipping"]:
        full_rows.append(row("Intl shipping", fmt(r["international_shipping"])))
    elif r["intl_fallback_applied"]:
        full_rows.append(row('<span class="warn">Intl (fallback $20)</span>', fmt(view.effective_intl)))
    if r["customs_duty"]:
        full_rows.append(row("Customs duty", fmt(r["customs_duty"])))
    if r["other_fees"]:
        full_rows.append(row("Other fees", fmt(r["other_fees"])))
    if r["tax"]:
        full_rows.append(row("Tax", fmt(r["tax"])))

    if r["reconciled"]:
        status_row = f'<tr class="ok"><td>Reconciled</td><td class="v">\u2713</td></tr>'
    else:
        status_row = f'<tr class="warn"><td>\u0394 vs invoice</td><td class="v">{fmt(r["delta"])}</td></tr>'

    # Orphan warnings
    orph = view.orphan_fees()
    orphan_html = ""
    orphan_lines = []
    for table, sids in orph.items():
        if sids:
            orphan_lines.append(f'<div class="warn">\u26a0 {len(sids)} orphan {table.replace("_", " ")} fee(s)</div>')
    if orphan_lines:
        orphan_html = '<div class="section-head">Join issues</div>' + "".join(orphan_lines)

    return f"""<html><head><style>{BASE_CSS}</style></head><body>
<div class="section-head">This page ({len(items_chunk)} items)</div>
<table>{''.join(chunk_rows)}</table>

<div class="section-head">Full invoice</div>
<table>{''.join(full_rows)}</table>

<div class="rule"></div>
<table>
  <tr><td><b>Computed</b></td><td class="v"><b>{fmt(r["computed"])}</b></td></tr>
  <tr><td><b>Invoice total</b></td><td class="v"><b>{fmt(r["invoice_total"])}</b></td></tr>
  {status_row}
</table>

<div class="rule"></div>
<table>
  <tr><td><b>Σ Landed USD</b><br><span class="small">(Shopify Cost per Item)</span></td>
      <td class="v"><b>${r["landed_usd_sum"]:,.2f}</b></td></tr>
  <tr><td><b>Σ Variant Price</b><br><span class="small">(demand × {demand})</span></td>
      <td class="v"><b>${total_variant_price:,.0f}</b></td></tr>
  <tr><td><b>Gross margin</b></td>
      <td class="v"><b>${total_variant_price - r["landed_usd_sum"]:,.0f}</b></td></tr>
  <tr><td>Avg markup</td>
      <td class="v">{avg_markup:.2f}×</td></tr>
</table>

{orphan_html}

<div class="footer">Buyee landed = subtotal + commission + dom-ship + service + intl/n + customs/n<br>
BrandStreet landed = subtotal + handling ({HANDLING_RATE*100:.0f}%) + import tax ({IMPORT_TAX_RATE*100:.0f}%)  — both additive</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def draw_original_page(page, src_page, col_rect: fitz.Rect):
    src_rect = src_page.rect
    scale = min(col_rect.width / src_rect.width, col_rect.height / src_rect.height)
    target_w = src_rect.width * scale
    target_h = src_rect.height * scale
    x0 = col_rect.x0 + (col_rect.width - target_w) / 2
    y0 = col_rect.y0 + (col_rect.height - target_h) / 2
    target = fitz.Rect(x0, y0, x0 + target_w, y0 + target_h)
    mat = fitz.Matrix(scale * 2, scale * 2)
    pix = src_page.get_pixmap(matrix=mat, alpha=False)
    page.insert_image(target, pixmap=pix)
    page.draw_rect(target, color=COLOR_RULE, width=0.4)


def build(pdf_path: Path, json_path: Path, output_path: Path, demand: float = 1.0, exchange_rate: float | None = None) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Strip pricing_result/cost_breakdown if present (from price.py) — we recompute
    invoice_data = {k: v for k, v in data.items() if not k.startswith("__")}
    for it in invoice_data.get("items", []):
        it.pop("pricing_result", None)
        it.pop("cost_breakdown", None)
    invoice = Invoice(**invoice_data)
    # Use JSON-provided rate if price.py already set one
    rate = exchange_rate or data.get("exchange_rate")
    if rate:
        view = InvoiceView(invoice, exchange_rate=rate)
    else:
        view = InvoiceView(invoice)
    # Use provided demand or the one stored in the priced JSON
    if demand == 1.0 and "demand_multiplier" in data:
        demand = data["demand_multiplier"]
    recon = view.reconciliation()
    items = invoice.items

    with fitz.open(str(pdf_path)) as src:
        n_src = len(src)
        items_per_page_cap = 5  # lower — more room per item for cost table
        n_out = max(n_src, (len(items) + items_per_page_cap - 1) // items_per_page_cap)
        per_page = (len(items) + n_out - 1) // n_out

        out = fitz.open()
        header_html = build_header_html(invoice, recon)

        for i in range(n_out):
            dst = out.new_page(width=PAGE_W, height=PAGE_H)
            header_rect = fitz.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, MARGIN + HEADER_H)
            dst.insert_htmlbox(header_rect, header_html)

            body_y = MARGIN + HEADER_H + 6
            body_h = PAGE_H - body_y - MARGIN
            left_w = (PAGE_W - 2 * MARGIN) * COL_LEFT_FRAC
            right_w = (PAGE_W - 2 * MARGIN) * COL_RIGHT_FRAC
            middle_w = (PAGE_W - 2 * MARGIN) - left_w - right_w - 2 * GUTTER

            left_rect = fitz.Rect(MARGIN, body_y, MARGIN + left_w, body_y + body_h)
            mid_rect = fitz.Rect(left_rect.x1 + GUTTER, body_y,
                                 left_rect.x1 + GUTTER + middle_w, body_y + body_h)
            right_rect = fitz.Rect(mid_rect.x1 + GUTTER, body_y,
                                   mid_rect.x1 + GUTTER + right_w, body_y + body_h)

            dst.draw_rect(mid_rect, color=COLOR_RULE, fill=COLOR_PANEL_BG, width=0.4)
            dst.draw_rect(right_rect, color=COLOR_RULE, fill=COLOR_PANEL_BG, width=0.4)

            src_idx = min(i, n_src - 1)
            draw_original_page(dst, src[src_idx], left_rect)

            chunk_start = i * per_page
            chunk = items[chunk_start:chunk_start + per_page]

            dst.insert_htmlbox(mid_rect + (6, 6, -6, -6),
                               build_items_html(chunk, view, chunk_start, len(items), demand))
            dst.insert_htmlbox(right_rect + (6, 6, -6, -6),
                               build_numbers_html(view, chunk, demand))

        out.save(str(output_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="Original invoice PDF")
    parser.add_argument("json_file", type=Path, help="Transcribed or priced JSON")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Output path (default: verify/<stem>_verification.pdf)")
    parser.add_argument("--demand", type=float, default=1.0, help="Demand multiplier to show in pricing (default 1.0)")
    parser.add_argument("--jpy-usd", type=float, default=None, help="Override exchange rate")
    args = parser.parse_args()

    for p in (args.pdf, args.json_file):
        if not p.exists():
            print(f"Not found: {p}", file=sys.stderr)
            return 1

    out = args.out or Path("verify") / f"{args.pdf.stem}_verification.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(args.pdf, args.json_file, out, demand=args.demand, exchange_rate=args.jpy_usd)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
