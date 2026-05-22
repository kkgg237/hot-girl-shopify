#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.0", "pyyaml>=6.0"]
# ///
"""Apply pricing rules (markup, bands, adjustments, demand, rounding) to
transcribed invoice JSONs. Emits priced JSONs with a `pricing_result` block
attached to each item — the original data is preserved.

Pricing logic lives in pricing.py (pure functions).

Usage:
    uv run price.py output/                      # price every *.json in folder
    uv run price.py output/invoice.json          # single file
    uv run price.py output/ --demand 1.05        # 5% demand bump
    uv run price.py output/ -o priced/           # custom output dir
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from costs import DEFAULT_EXCHANGE_RATE, Invoice, InvoiceView
from pricing import price_item


def price_invoice(
    invoice: Invoice,
    exchange_rate: float,
    demand: float,
    handling_rate: float | None = None,
    import_tax_rate: float | None = None,
    extra_rate: float | None = None,
    extra_flat: float | None = None,
) -> dict:
    """Return the invoice as a dict with `pricing_result` added to each item.

    All rate/extras overrides are forwarded to InvoiceView so callers (e.g.
    the Streamlit Export tab) can pass user-adjusted values that match what
    the Cost Review tab is using.
    """
    from costs import HANDLING_RATE as _DEF_H, IMPORT_TAX_RATE as _DEF_I
    view = InvoiceView(
        invoice,
        exchange_rate=exchange_rate,
        handling_rate=handling_rate if handling_rate is not None else _DEF_H,
        import_tax_rate=import_tax_rate if import_tax_rate is not None else _DEF_I,
        extra_rate=extra_rate if extra_rate is not None else 0.0,
        extra_flat=extra_flat if extra_flat is not None else 0.0,
    )
    data = invoice.model_dump()
    data["exchange_rate"] = exchange_rate
    data["demand_multiplier"] = demand

    for item_data, item in zip(data["items"], invoice.items):
        result = price_item(item, view, demand=demand)
        breakdown = view.breakdown(item)
        item_data["pricing_result"] = asdict(result)
        item_data["cost_breakdown"] = breakdown
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="JSON file or folder")
    parser.add_argument("-o", "--out", type=Path, default=Path("priced"), help="Output folder (default: priced/)")
    parser.add_argument("--demand", type=float, default=1.0, help="Global demand multiplier (default 1.0)")
    parser.add_argument("--jpy-usd", type=float, default=DEFAULT_EXCHANGE_RATE,
                        help=f"JPY→USD exchange rate (default {DEFAULT_EXCHANGE_RATE})")
    args = parser.parse_args()

    if args.source.is_file():
        sources = [args.source]
    elif args.source.is_dir():
        sources = sorted(args.source.glob("*.json"))
    else:
        print(f"Not a JSON file or folder: {args.source}", file=sys.stderr)
        return 1

    if not sources:
        print("No JSON files to price.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    total_items = 0
    total_warnings = 0
    for src in sources:
        data = json.loads(src.read_text(encoding="utf-8"))
        invoice = Invoice(**data)
        priced = price_invoice(invoice, args.jpy_usd, args.demand)

        out_path = args.out / src.name
        out_path.write_text(
            json.dumps(priced, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        n = len(priced["items"])
        warn = sum(len(i["pricing_result"]["warnings"]) for i in priced["items"])
        total_items += n
        total_warnings += warn
        print(f"  {src.name}: priced {n} items ({warn} warnings)", file=sys.stderr)

    print(
        f"\nTotal: {total_items} items priced from {len(sources)} invoice(s), "
        f"{total_warnings} warnings, demand={args.demand}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
