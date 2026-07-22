"""Shared cost model — extracts the Buyee tables as separate lists, joins in
Python by Shopping Site(ID), and computes landed cost per spec rules.

Spec-compliant cost math (see PLAN.md §3):

    BrandStreet (vendor_invoice, USD):
        handling   = subtotal × 0.15  (time + fixed costs)
        import_tax = subtotal × 0.15  (estimated US import duty on declared value)
        landed_usd = subtotal + handling + import_tax       (= subtotal × 1.30)
        unit_cost  = landed_usd / quantity

    Buyee (buyee_breakdown, JPY):
        landed_jpy = item_price
                   + commission_fee   (joined by source_id)
                   + domestic_shipping (joined by source_id)
                   + service_fee       (joined by source_id)
                   + international_shipping / n_items      (equal split)
                   + customs_duty / n_items                (equal split)
        landed_usd = landed_jpy × exchange_rate
        unit_cost  = landed_usd / quantity

    Fallback: if Buyee invoice reports international_shipping == 0,
    substitute FALLBACK_INTL_USD (20 USD) converted to JPY at the rate.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# BrandStreet uplift — applied additively on subtotal (not compounded).
#   landed = subtotal + handling + import_tax = subtotal × (1 + HANDLING + IMPORT)
#
# Deviates from SPEC.md §5 which used multiplicative 1.20×1.15 = 1.38×.
# We use 15% + 15% = 30% uplift, broken out so each line is auditable.
HANDLING_RATE = 0.10            # BrandStreet: time + fixed costs
IMPORT_TAX_RATE = 0.15          # BrandStreet: estimated US import duty on declared value
DEFAULT_EXCHANGE_RATE = 0.0067  # JPY → USD (~149 JPY/USD)
FALLBACK_INTL_USD = 20.0        # Buyee: if intl shipping not in PDF


CURRENCY_SYMBOL = {"JPY": "\u00a5", "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3"}


def fmt_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOL.get(currency, currency + " ")
    if currency == "JPY":
        return f"{symbol}{int(round(amount)):,}"
    return f"{symbol}{amount:,.2f}"


# ---------------------------------------------------------------------------
# Models — one per source table
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    """One row from the 'Item Price' table."""
    source_platform: Optional[str] = Field(
        default=None,
        description="'JDirectItems Auction' / 'LuxeWholesale' / 'Brand Street Tokyo'",
    )
    source_id: str = Field(description="Auction code / auth code — THE JOIN KEY. Must be unique within an invoice.")
    description_original: str
    description_english: str
    detected_brand: Optional[str] = None
    product_type: Optional[str] = None
    condition_notes: Optional[str] = None
    # Phase 2: material + garment length for markup rules
    material: Optional[str] = Field(default=None, description="Leather / Suede / Silk / Fox Fur / Mink / Lambskin / Shearling / Denim / Cotton etc.")
    garment_length: Optional[str] = Field(default=None, description="'short' | 'midi' | 'long' (garments only)")
    # Fields used to compose strict Shopify titles
    era: Optional[str] = Field(default=None, description="Year (e.g. '1997') or decade (e.g. \"90's\", \"00's\", 'Y2K')")
    color: Optional[str] = Field(default=None, description="Single color like 'Black', 'Brown', 'Beige'; 'Multicolor' for 3+ colors")
    pattern: Optional[str] = Field(default=None, description="Signature pattern — 'Monogram', 'Damier', 'Matelasse', 'Zucca', 'Nova Check', 'GG Canvas'")
    origin: Optional[str] = Field(default=None, description="Where made, e.g. 'Made in USA', 'Made in Italy', 'Made in France'")
    # Model identity for luxury items — biggest SEO lever
    model_name: Optional[str] = Field(default=None, description="Specific model name — 'Speedy', 'Neverfull', 'Mamma Baguette', 'Classic Flap', 'Boy Bag', 'Birkin', 'Kelly', 'Jackie', 'Pochette Accessoires'")
    model_size: Optional[str] = Field(default=None, description="Size variant — 'MM', 'PM', 'GM', '25', '30', '35', '40'")
    # Style adjectives extracted from the translation — necklines, sleeves,
    # cuts, fabric details, silhouettes. Space-separated, ordered for title use.
    style_adjectives: Optional[str] = Field(default=None, description="Ordered style descriptors — 'Belted V-Neck Long Sleeve', 'Cache-Coeur', 'Cropped Mesh', etc.")
    quantity: int = 1
    currency: str = "JPY"
    item_price: float = Field(description="Unit price from Item Price column (positive)")
    coupon_discount: float = Field(default=0, description="Positive; 0 if none")
    # ----- Manual overrides — populated via the UI's data editors -----
    override_title: Optional[str] = Field(default=None, description="Manual Shopify title override")
    override_vendor: Optional[str] = Field(default=None, description="Manual Vendor column override")
    override_price: Optional[int] = Field(default=None, description="Manual Variant Price override — skips markup/band rules")


class FeeLine(BaseModel):
    source_id: str = Field(description="Must match a LineItem.source_id")
    amount: float
    note: Optional[str] = None


class Invoice(BaseModel):
    invoice_type: str = Field(description="'buyee_breakdown' or 'vendor_invoice'")
    vendor_name: str
    vendor_address: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(default=None, description="ISO YYYY-MM-DD")
    currency: str
    items: list[LineItem]
    commission_fees: list[FeeLine] = Field(default_factory=list)
    domestic_shipping_fees: list[FeeLine] = Field(default_factory=list)
    service_fees: list[FeeLine] = Field(default_factory=list)
    international_shipping: float = 0
    customs_duty: float = Field(default=0, description="Customs duty line if present on the Buyee invoice")
    # Lump-sum commission line on vendor invoices (e.g. DKC's "5% commission
    # ¥83,218" line). DISTINCT from commission_fees (per-item table). Split
    # equally across items in landed-cost math.
    commission_line: float = Field(
        default=0,
        description="Single-line commission charged on the whole invoice (e.g. 5% of subtotal). Distinct from per-item commission_fees.",
    )
    commission_line_rate: Optional[float] = Field(
        default=None,
        description="Rate as decimal (0.05 for 5%) — populated when invoice shows the percentage. Display only.",
    )
    other_fees: float = 0
    tax: float = 0
    grand_total: float
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Join indexes
# ---------------------------------------------------------------------------

def _index(fees: list[FeeLine]) -> dict[str, float]:
    idx: dict[str, float] = {}
    for f in fees:
        idx[f.source_id] = idx.get(f.source_id, 0.0) + f.amount
    return idx


def _is_kanagawa_with_commission(invoice: Invoice) -> bool:
    """True when a Kanagawa vendor invoice already includes commission.

    These invoices should not get additional assumed handling/import uplifts.
    """
    vendor_blob = " ".join(
        str(v or "")
        for v in (invoice.vendor_name, invoice.vendor_address, invoice.notes)
    ).lower()
    has_kanagawa = "kanagawa" in vendor_blob
    has_commission = bool(invoice.commission_line) or any(
        fee.amount for fee in invoice.commission_fees
    )
    return invoice.invoice_type == "vendor_invoice" and has_kanagawa and has_commission


class InvoiceView:
    """Cached indexes + per-item breakdown. Build once per invoice."""

    def __init__(
        self,
        invoice: Invoice | dict,
        exchange_rate: float = DEFAULT_EXCHANGE_RATE,
        import_tax_rate: float = IMPORT_TAX_RATE,
        handling_rate: float = HANDLING_RATE,
        extra_rate: float = 0.0,
        extra_flat: float = 0.0,
    ):
        if isinstance(invoice, dict):
            invoice = Invoice(**invoice)
        self.inv = invoice
        self.exchange_rate = exchange_rate
        # Per-invoice override of the assumed import tax / handling rates.
        # Defaults to the module-level constants. UI can pass user-adjusted
        # values for what-if analysis.
        self.assumed_uplifts_suppressed = _is_kanagawa_with_commission(invoice)
        if self.assumed_uplifts_suppressed:
            # Kanagawa invoices already carry an explicit commission fee. Do
            # not stack our assumed handling/import percentages on top.
            self.import_tax_rate = 0.0
            self.handling_rate = 0.0
        else:
            self.import_tax_rate = import_tax_rate
            self.handling_rate = handling_rate
        # Optional ad-hoc cost adjustments — anything not captured by the
        # standard fee fields. Two flavors:
        #   extra_rate: percentage of item subtotal (e.g. 0.05 = 5% surcharge)
        #   extra_flat: lump sum in USD, split equally per-item
        # Both default to 0 so adding them is opt-in and never affects existing
        # math unless the user explicitly sets them in the UI.
        self.extra_rate = extra_rate
        self.extra_flat = extra_flat
        self.commission_idx = _index(invoice.commission_fees)
        self.domestic_idx = _index(invoice.domestic_shipping_fees)
        self.service_idx = _index(invoice.service_fees)
        self.n_items = len(invoice.items) or 1

        # Apply fallback intl shipping if missing on a Buyee invoice
        self.effective_intl = invoice.international_shipping
        self.intl_fallback_applied = False
        if invoice.invoice_type == "buyee_breakdown" and invoice.international_shipping == 0:
            if invoice.currency == "JPY" and exchange_rate > 0:
                self.effective_intl = FALLBACK_INTL_USD / exchange_rate
                self.intl_fallback_applied = True

    @staticmethod
    def _subtotal(item: LineItem) -> float:
        return item.item_price * item.quantity - item.coupon_discount

    def breakdown(self, item: LineItem) -> dict:
        """Full per-item breakdown. All in invoice currency unless noted."""
        sid = item.source_id
        subtotal = self._subtotal(item)
        commission = self.commission_idx.get(sid, 0.0)
        domestic = self.domestic_idx.get(sid, 0.0)
        service = self.service_idx.get(sid, 0.0)

        # Equal split for invoice-wide shared costs (spec §6).
        # commission_line: single lump-sum commission (e.g. DKC's "5% ¥83,218")
        # other_fees: catch-all for unrecognized fees (kept as fallback).
        # Both split equally across items so every item carries its share.
        intl_per_item = self.effective_intl / self.n_items
        customs_per_item = self.inv.customs_duty / self.n_items
        commission_line_per_item = self.inv.commission_line / self.n_items
        other_per_item = self.inv.other_fees / self.n_items
        tax_per_item = self.inv.tax / self.n_items

        # Handling + import uplift — applied to vendor invoices (BrandStreet,
        # DKC, etc.) where these aren't on the invoice but we know to expect
        # them on landed cost. Rates are per-instance so the UI can adjust.
        handling_amount = 0.0
        import_amount = 0.0
        is_vendor_invoice = self.inv.invoice_type == "vendor_invoice"
        if is_vendor_invoice:
            handling_amount = subtotal * self.handling_rate
            import_amount = subtotal * self.import_tax_rate

        # Ad-hoc extras (independent of invoice type — applies to any source).
        # extra_rate is a per-item percentage of subtotal.
        # extra_flat is a USD lump sum split equally across items, then
        # converted back to native currency for `landed_native` math.
        extra_pct_amount = subtotal * self.extra_rate
        extra_flat_usd_per_item = self.extra_flat / self.n_items
        extra_flat_per_item = (
            extra_flat_usd_per_item
            if self.inv.currency == "USD"
            else extra_flat_usd_per_item / self.exchange_rate
            if self.exchange_rate > 0 else 0.0
        )

        landed_native = (
            subtotal
            + commission + domestic + service
            + intl_per_item + customs_per_item
            + commission_line_per_item + other_per_item + tax_per_item
            + handling_amount + import_amount
            + extra_pct_amount + extra_flat_per_item
        )

        # Convert to USD for Shopify Cost per Item
        if self.inv.currency == "USD":
            landed_usd = landed_native
        else:
            landed_usd = landed_native * self.exchange_rate
        unit_cost_usd = landed_usd / max(item.quantity, 1)

        return {
            "subtotal": subtotal,
            "item_price": item.item_price * item.quantity,
            "coupon": item.coupon_discount,
            "commission": commission,
            "domestic_shipping": domestic,
            "service": service,
            "intl_share": intl_per_item,
            "customs_share": customs_per_item,
            "commission_line_share": commission_line_per_item,
            "other_share": other_per_item,
            "tax_share": tax_per_item,
            "handling_amount": handling_amount,
            "import_amount": import_amount,
            "extra_pct_amount": extra_pct_amount,
            "extra_flat_per_item": extra_flat_per_item,
            "extra_flat_usd_per_item": extra_flat_usd_per_item,
            # Backward-compat alias — some callers still read `handling_uplift`
            "handling_uplift": handling_amount + import_amount,
            "landed_native": landed_native,
            "landed_usd": landed_usd,
            "unit_cost_usd": unit_cost_usd,
        }

    def landed(self, item: LineItem) -> float:
        """Landed cost in invoice currency."""
        return self.breakdown(item)["landed_native"]

    def landed_usd(self, item: LineItem) -> float:
        return self.breakdown(item)["landed_usd"]

    def unit_cost_usd(self, item: LineItem) -> float:
        return self.breakdown(item)["unit_cost_usd"]

    # --- Sanity checks ---

    def orphan_fees(self) -> dict[str, list[str]]:
        """source_ids that appear in a fee table but not in items."""
        known = {i.source_id for i in self.inv.items}
        return {
            "commission": [s for s in self.commission_idx if s not in known],
            "domestic_shipping": [s for s in self.domestic_idx if s not in known],
            "service": [s for s in self.service_idx if s not in known],
        }

    def fee_table_totals(self) -> dict[str, float]:
        return {
            "commission": sum(self.commission_idx.values()),
            "domestic_shipping": sum(self.domestic_idx.values()),
            "service": sum(self.service_idx.values()),
        }

    def reconciliation(self) -> dict:
        """Invoice-level math for auditing.

        For Buyee: sum of subtotals + fee-table totals + intl + customs + other + tax
                   should equal the reported grand_total.
        For BrandStreet: sum of item_price × qty should equal grand_total (no fees).
                         The handling/import uplift is computed cost-side only — NOT
                         reconciled against grand_total (it's our money, not theirs).
        """
        items_sub = sum(self._subtotal(i) for i in self.inv.items)
        fees = self.fee_table_totals()
        computed = (
            items_sub
            + fees["commission"]
            + fees["domestic_shipping"]
            + fees["service"]
            + self.inv.international_shipping
            + self.inv.customs_duty
            + self.inv.commission_line
            + self.inv.other_fees
            + self.inv.tax
        )
        delta = self.inv.grand_total - computed

        # Cost-basis totals (what goes in Shopify Cost per Item column)
        landed_usd_sum = sum(self.breakdown(i)["landed_usd"] for i in self.inv.items)

        return {
            "items_subtotal": items_sub,
            **fees,
            "international_shipping": self.inv.international_shipping,
            "customs_duty": self.inv.customs_duty,
            "commission_line": self.inv.commission_line,
            "commission_line_rate": self.inv.commission_line_rate,
            "other_fees": self.inv.other_fees,
            "tax": self.inv.tax,
            "computed": computed,
            "invoice_total": self.inv.grand_total,
            "delta": delta,
            "reconciled": abs(delta) < 1,
            "intl_fallback_applied": self.intl_fallback_applied,
            "landed_usd_sum": landed_usd_sum,
        }
