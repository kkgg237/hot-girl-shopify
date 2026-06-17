"""CSV → Invoice adapter.

Lets the app ingest a Shopify-format product CSV as if it were an Invoice,
so it flows through the same pricing → review → Shopify push pipeline as
PDF invoices. Cost is treated as already in USD; no exchange rate is
applied. The existing BrandStreet-style handling/import-tax uplift (~30%)
DOES still get applied via the standard InvoiceView math — if your CSV
costs are already landed, drop those rates to 0 in the Cost controls.

Accepted columns (case-insensitive, trimmed). Title is the only required:

    Title                       (required) — product title
    Vendor                      — copied to detected_brand
    Cost per Item / Cost        — unit cost in USD
    Variant Inventory Qty / Qty — unit qty (defaults to 1)
    Tags                        — copied verbatim into invoice.notes for context

Any other Shopify columns (Handle, Variant Price, Status, …) are ignored.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from costs import Invoice, LineItem


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------
#
# Shopify and other tools sometimes vary the column-name casing or wording.
# Pick the first column header whose normalized form matches one of the
# aliases below. This keeps the ingest forgiving without forcing the user
# to rename anything in their spreadsheet.

_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "title":  ("title", "name", "product title"),
    "vendor": ("vendor", "brand"),
    "cost":   ("cost per item", "cost", "unit cost", "landed cost"),
    "qty":    ("variant inventory qty", "qty", "quantity", "inventory qty"),
    "tags":   ("tags",),
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _resolve_columns(fieldnames: list[str]) -> dict[str, Optional[str]]:
    """Return {logical_name: actual_column} for as many aliases as we find."""
    by_norm = {_norm(f): f for f in fieldnames}
    out: dict[str, Optional[str]] = {}
    for logical, aliases in _COL_ALIASES.items():
        out[logical] = next((by_norm[a] for a in aliases if a in by_norm), None)
    return out


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------

def _slugify(s: str, max_len: int = 60) -> str:
    """Lowercase hyphen-separated source_id from a title. Stable per-title so
    re-uploading the same CSV yields the same source_ids."""
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (s or "item")[:max_len]


def _parse_float(s: str) -> float:
    if not s:
        return 0.0
    s = s.replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_int(s: str, default: int = 1) -> int:
    if not s:
        return default
    try:
        return int(float(s.strip()))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preview_csv_rows(path: Path) -> list[dict]:
    """Lightweight pre-ingest parse — returns one dict per CSV row with
    `row_index` plus the recognized columns. Lets the UI render a
    preview/edit step where the user can uncheck rows before they become
    LineItems via `extract_from_csv`.

    `row_index` is 0-based and matches the position the caller can pass
    back via `extract_from_csv`'s `skip_indices` to exclude that row.
    """
    if not path.exists():
        raise ValueError(f"CSV not found: {path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        cols = _resolve_columns(fieldnames)
        if not cols["title"]:
            raise ValueError(
                "CSV must have a Title column (found: "
                + ", ".join(fieldnames) + ")"
            )
        for idx, raw in enumerate(reader):
            title = (raw.get(cols["title"]) or "").strip()
            if not title:
                continue
            rows.append({
                "row_index": idx,
                "title": title,
                "vendor": ((raw.get(cols["vendor"]) or "").strip()
                           if cols["vendor"] else ""),
                "cost": (_parse_float(raw.get(cols["cost"]) or "")
                         if cols["cost"] else 0.0),
                "qty": (_parse_int(raw.get(cols["qty"]) or "", default=1)
                        if cols["qty"] else 1),
                "tags": ((raw.get(cols["tags"]) or "").strip()
                         if cols["tags"] else ""),
            })
    return rows


def extract_from_csv(path: Path, skip_indices: Optional[set[int]] = None) -> Invoice:
    """Read a Shopify-shaped product CSV and return an Invoice.

    Raises ValueError if the CSV is unreadable or has no Title column.
    Items with an empty Title row are skipped silently. Duplicate titles
    get a numeric suffix on their source_id so each row is unique.
    """
    if not path.exists():
        raise ValueError(f"CSV not found: {path}")

    skip = set(skip_indices) if skip_indices else set()

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        cols = _resolve_columns(fieldnames)
        if not cols["title"]:
            raise ValueError(
                "CSV must have a Title column (found: "
                + ", ".join(fieldnames) + ")"
            )

        items: list[LineItem] = []
        seen_ids: set[str] = set()
        all_tags: set[str] = set()

        for idx, row in enumerate(reader):
            if idx in skip:
                continue
            title = (row.get(cols["title"]) or "").strip()
            if not title:
                continue

            vendor = (
                (row.get(cols["vendor"]) or "").strip() if cols["vendor"] else ""
            ) or None
            cost = (
                _parse_float(row.get(cols["cost"]) or "") if cols["cost"] else 0.0
            )
            qty = (
                _parse_int(row.get(cols["qty"]) or "", default=1)
                if cols["qty"] else 1
            )
            if cols["tags"]:
                tag_field = (row.get(cols["tags"]) or "").strip()
                if tag_field:
                    for t in tag_field.split(","):
                        t = t.strip()
                        if t:
                            all_tags.add(t)

            base = _slugify(title)
            sid = base
            n = 2
            while sid in seen_ids:
                sid = f"{base}-{n}"
                n += 1
            seen_ids.add(sid)

            items.append(LineItem(
                source_id=sid,
                description_original=title,
                description_english=title,
                detected_brand=vendor,
                item_price=cost,
                currency="USD",
                quantity=qty,
                # Use the CSV's Title verbatim as the Shopify title. The CSV
                # owner has already curated this; compose_title would just
                # collapse it to the brand because none of the structured
                # fields (era / color / pattern / material / product_type)
                # are populated from a CSV row.
                override_title=title,
            ))

    if not items:
        raise ValueError(
            f"No rows with a Title found in {path.name}. "
            f"Check the CSV's first row contains column headers."
        )

    # NOTE: deliberately NOT auto-estimating cost for rows that came in at
    # zero. Earlier this function called cost_estimator to backfill from
    # past-invoice averages, but the user prefers to leave $0 as $0 — a
    # blank cost is a signal to fill it manually, not a hole to paper over
    # with a vendor average that may be wildly off.
    grand_total = sum(i.item_price * i.quantity for i in items)
    notes = (
        f"CSV import · {len(items)} items · "
        f"tags: {', '.join(sorted(all_tags)) or '(none)'}"
    )

    return Invoice(
        invoice_type="vendor_invoice",
        vendor_name=f"CSV import · {path.stem}",
        invoice_number=path.stem,
        currency="USD",
        items=items,
        grand_total=grand_total,
        notes=notes,
    )
