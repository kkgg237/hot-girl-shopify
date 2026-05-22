"""Shopify export: build the body-HTML template and the product CSV."""

from __future__ import annotations

import csv
import html
import io
import re
from typing import Iterable

from pipeline.schema import BagListing


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _format_grade(grade: float) -> str:
    if grade == int(grade):
        return str(int(grade))
    return f"{grade:.1f}"


def to_body_html(listing: BagListing) -> str:
    """Produce the body HTML used in Shopify product descriptions."""
    bullets = "\n".join(f"<li>{_esc(b)}</li>" for b in listing.details_bullets)
    grade = _format_grade(listing.condition_grade)
    return "\n".join(
        [
            f"<p><strong>DIMENSIONS:</strong><br>{_esc(listing.dimensions)}</p>",
            "<p><strong>DETAILS:</strong></p>",
            f"<ul>\n{bullets}\n</ul>",
            f"<p><strong>MATERIAL:</strong><br>{_esc(listing.material_line)}</p>",
            (
                f"<p><strong>CONDITION:</strong><br>"
                f"{grade}/10 \u2013 {_esc(listing.condition_text)}</p>"
            ),
        ]
    )


def _handle(sku: str) -> str:
    h = re.sub(r"[^a-z0-9-]+", "-", sku.lower()).strip("-")
    return h or sku.lower()


SHOPIFY_HEADERS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Variant SKU",
    "Status",
]


def to_csv_row(sku: str, listing: BagListing) -> dict:
    tags = ", ".join(t for t in (listing.era, listing.brand, listing.colorway) if t)
    return {
        "Handle": _handle(sku),
        "Title": listing.title,
        "Body (HTML)": to_body_html(listing),
        "Vendor": listing.brand,
        "Type": listing.silhouette,
        "Tags": tags,
        "Published": "FALSE",
        "Variant SKU": sku,
        "Status": "draft",
    }


def to_csv(rows: Iterable[tuple[str, BagListing]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SHOPIFY_HEADERS)
    writer.writeheader()
    for sku, listing in rows:
        writer.writerow(to_csv_row(sku, listing))
    return buf.getvalue()
