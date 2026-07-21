"""Commercial invoice generator: free-text item descriptions → customs line
items (via Claude) → a CSV that mirrors the manual spreadsheet, ready to email.

Backs the "Commercial invoice" tab in app.py.
"""

from __future__ import annotations

import csv
import io
import json

import anthropic
from pydantic import BaseModel, Field

from transcribe import MODEL, _extract_json

# Curated HS6 product types (Blackship-style dropdown), limited to what the
# shop actually ships: bags, footwear, clothing (incl. leather/fur), and
# accessories. Codes follow the HS6 standard used on customs declarations.
HS_CODES: list[tuple[str, str]] = [
    # Bags & small leather goods
    ("4202.21", "Handbag, leather"),
    ("4202.22", "Handbag, textile / canvas / plastic"),
    ("4202.29", "Handbag, other materials"),
    ("4202.31", "Wallet / small leather goods, leather"),
    ("4202.32", "Wallet / pouch, textile / plastic"),
    ("4202.91", "Travel bag / backpack, leather"),
    ("4202.92", "Travel bag / backpack, textile / plastic"),
    # Leather & fur apparel
    ("4203.10", "Clothing, leather"),
    ("4203.30", "Belt, leather"),
    ("4303.10", "Clothing & accessories, fur"),
    # Knitted clothing (ch. 61)
    ("6104.43", "Dress, knit, synthetic"),
    ("6109.10", "T-shirt, cotton, knit"),
    ("6110.11", "Sweater / pullover, wool, knit"),
    ("6110.20", "Sweater / pullover, cotton, knit"),
    ("6110.30", "Sweater / pullover, man-made fibres, knit"),
    # Woven clothing (ch. 62)
    ("6202.20", "Coat / jacket, women's, wool, woven"),
    ("6202.30", "Coat / jacket, women's, cotton, woven"),
    ("6202.40", "Coat / jacket, women's, man-made fibres, woven"),
    ("6204.42", "Dress, cotton, woven"),
    ("6204.43", "Dress, synthetic, woven"),
    ("6204.52", "Skirt, cotton, woven"),
    ("6204.53", "Skirt, synthetic, woven"),
    ("6204.62", "Trousers / jeans, women's, cotton"),
    ("6204.63", "Trousers, women's, synthetic"),
    ("6206.30", "Blouse / shirt, women's, cotton"),
    ("6206.40", "Blouse / shirt, women's, man-made fibres"),
    ("6214.10", "Scarf / shawl, silk"),
    ("6214.20", "Scarf / shawl, wool"),
    ("6214.30", "Scarf / shawl, synthetic"),
    # Footwear
    ("6402.99", "Footwear, rubber / plastic upper"),
    ("6403.91", "Boots, leather upper"),
    ("6403.99", "Footwear, leather upper (pumps, sandals, loafers)"),
    ("6404.19", "Footwear, textile upper (canvas, ballerinas)"),
    # Accessories
    ("6505.00", "Hat / cap, textile or knitted"),
    ("7117.19", "Costume jewelry, base metal"),
    ("7117.90", "Costume jewelry, other"),
    ("9004.10", "Sunglasses"),
]

HS_OPTIONS = [f"{code} — {label}" for code, label in HS_CODES]
_HS_OPTION_BY_CODE = {code: opt for (code, _), opt in zip(HS_CODES, HS_OPTIONS)}


def hs_option_for(code: str) -> str:
    """Dropdown option string for a bare HS code ('4202.21' → '4202.21 — Handbag, leather').

    Unknown codes pass through unchanged so nothing is silently dropped.
    """
    return _HS_OPTION_BY_CODE.get(code.strip(), code.strip())


def hs_code_only(value: str) -> str:
    """Bare HS code from either a dropdown option string or a raw code."""
    return value.split("—")[0].strip()


_HS_PROMPT_TABLE = "\n".join(f"- {code}: {label}" for code, label in HS_CODES)

SYSTEM_PROMPT = f"""\
You turn casual descriptions of second-hand goods into customs-declaration
line items for a US import from Japan (a vintage resale shop importing
handbags, shoes, clothing, and accessories).

The user writes items in plain words, usually one per line, e.g.:

    06-220 leather shoulder bag 5248 yen
    black rubber sandals size 37, item 16127, 787
    3x wool coat @ 4200

Produce one line item per distinct product. If the same product appears more
than once at the same unit price (or with a count like "3x" or "three"),
combine it into ONE line item with the total quantity — never repeat
identical lines.

# Field rules

description
- Format: "{{item code}} {{Product Description}} (Second hand)".
- Keep the user's item/auction code verbatim at the front when given
  (e.g. "06-220", "B123-7", "16127"). If none is given, omit the code.
- Title Case the product words, e.g. "Leather Shoulder Bag", "Canvas Handbag",
  "Wool Coat", "Bucket Handbag", "Waist Pouch".
- Footwear includes gender and size when known, e.g.
  "16127 Sandals Black Size 37 Women's (Second hand)",
  "B123-7 Pumps Black Size 35.5 (Second hand)".
- Never name brands in the description (customs descriptions stay generic):
  "Chanel double chain bag" becomes "Double Chain Bag (Second hand)".
- Always end with "(Second hand)" — everything this shop imports is second hand.

material_content
- One or two words naming the dominant material: Leather, Textile, Rubber,
  Fur, Wool, Silk, Cotton, Denim.
- Canvas, nylon, polyester, satin map to Textile.
- Suede, lambskin, pony hair, patent map to Leather.

hs_code
- Pick the single best match from this product-type list and output the CODE
  ONLY (e.g. "4202.21"). Never invent a code that is not on the list.

{_HS_PROMPT_TABLE}

- Knit vs woven matters for clothing: sweaters, tees, and jersey are knit
  (ch. 61); coats, jeans, blouses, and most dresses/skirts are woven (ch. 62).
  When the user doesn't say, use the garment's usual construction.

quantity
- Default 1. Use the stated count for "2x", "two pairs", etc., and the
  combined count when merging identical items.

unit_value
- The price PER INDIVIDUAL ITEM in the invoice currency (the invoice
  multiplies by quantity). The user message states the currency (JPY or EUR);
  bare numbers are in that currency unless the user explicitly says otherwise.
  If a price is given for a multi-quantity line, treat it as the per-unit
  price unless the user says "total" — in that case divide by the quantity.
- If the user gives no price for an item, use 0 so it stands out for manual
  entry — never invent a value.

Return ONLY one JSON object (no markdown fence, no preamble) matching the
provided schema.
"""


class CommercialParty(BaseModel):
    """One side of the invoice header (supplier or importer of record)."""

    name: str
    address: str
    city: str
    country: str


CURRENCIES = ("JPY", "EUR")
CURRENCY_SYMBOLS = {"JPY": "¥", "EUR": "€"}
# Sensible starting exchange rates (units of invoice currency per 1 USD);
# the UI persists whatever the user last entered per currency.
DEFAULT_RATES = {"JPY": 165.0, "EUR": 0.92}


class CommercialLineItem(BaseModel):
    """One row of the customs line-item table. Values are per unit, in the
    invoice currency."""

    quantity: int = Field(default=1, ge=1)
    description: str
    material_content: str
    hs_code: str
    unit_value: float = Field(default=0, ge=0)


class GeneratedItems(BaseModel):
    """Claude's output: line items parsed from a free-text description."""

    items: list[CommercialLineItem]


class CommercialInvoice(BaseModel):
    """Everything needed to render one commercial invoice CSV."""

    invoice_number: str
    invoice_date: str
    supplier: CommercialParty
    importer: CommercialParty
    country_of_origin: str = "Japan"
    currency: str = "JPY"
    rate_per_usd: float = Field(..., gt=0, description="Invoice currency units per 1 USD")
    items: list[CommercialLineItem]


def line_total_usd(item: CommercialLineItem, rate_per_usd: float) -> int:
    """USD line total, rounded to whole dollars like the manual invoices."""
    return round(item.quantity * item.unit_value / rate_per_usd)


def invoice_total_usd(invoice: CommercialInvoice) -> int:
    return sum(line_total_usd(item, invoice.rate_per_usd) for item in invoice.items)


def generate_items(
    description: str,
    client: anthropic.Anthropic,
    model: str = MODEL,
    currency: str = "JPY",
) -> list[CommercialLineItem]:
    """Send the free-text description to Claude and return parsed line items."""
    schema_json = json.dumps(GeneratedItems.model_json_schema(), indent=2)
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT + "\n\n# Output schema\n\n" + schema_json,
        messages=[
            {
                "role": "user",
                "content": f"Prices below are in {currency}. "
                "Generate the invoice line items for the following items:\n\n"
                + description,
            }
        ],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    return GeneratedItems.model_validate(_extract_json(text)).items


def item_headers(currency: str) -> list[str]:
    return [
        "PO #",
        "Quantity",
        "Product Description",
        "Material Content",
        "HS code",
        f"Unit Value ({currency})",
        "Total Value (USD)",
    ]


def _fmt_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def to_invoice_csv(invoice: CommercialInvoice) -> str:
    """CSV in the manual spreadsheet's layout (header block, items, totals)."""
    buf = io.StringIO()
    w = csv.writer(buf)

    w.writerow(["", "", "Commercial INVOICE"])
    w.writerow([])
    w.writerow(["Invoice Number:", invoice.invoice_number])
    w.writerow(["Invoice Date:", invoice.invoice_date])
    w.writerow([])
    w.writerow(["Supplier Information", "", "Importer of Record"])
    w.writerow(["Name:", invoice.supplier.name, "Company:", invoice.importer.name])
    w.writerow(["Address:", invoice.supplier.address, "Address:", invoice.importer.address])
    w.writerow(["City:", invoice.supplier.city, "City:", invoice.importer.city])
    w.writerow(["Country:", invoice.supplier.country, "Country:", invoice.importer.country])
    w.writerow([])
    w.writerow(item_headers(invoice.currency))
    for po, item in enumerate(invoice.items, start=1):
        w.writerow(
            [
                po,
                item.quantity,
                item.description,
                item.material_content,
                hs_code_only(item.hs_code),
                _fmt_value(item.unit_value),
                line_total_usd(item, invoice.rate_per_usd),
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "Country of origin:",
            invoice.country_of_origin,
            "",
            "",
            "Estimated Total Value of all goods (USD)",
            invoice_total_usd(invoice),
        ]
    )
    return buf.getvalue()
