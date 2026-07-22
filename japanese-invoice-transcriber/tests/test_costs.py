from __future__ import annotations

import pytest

from costs import FeeLine, Invoice, InvoiceView, LineItem


def _item(source_id: str = "A1", price: float = 1000, qty: int = 1) -> LineItem:
    return LineItem(
        source_platform="Kanagawa Auction",
        source_id=source_id,
        description_original="バッグ",
        description_english="bag",
        quantity=qty,
        currency="JPY",
        item_price=price,
    )


def _vendor_invoice(**overrides) -> Invoice:
    data = {
        "invoice_type": "vendor_invoice",
        "vendor_name": "Kanagawa Vintage Co.",
        "currency": "JPY",
        "items": [_item()],
        "commission_line": 50,
        "commission_line_rate": 0.05,
        "grand_total": 1050,
    }
    data.update(overrides)
    return Invoice(**data)


def test_kanagawa_with_existing_commission_suppresses_assumed_uplifts():
    inv = _vendor_invoice()
    view = InvoiceView(inv, exchange_rate=0.01, handling_rate=0.10, import_tax_rate=0.15)

    b = view.breakdown(inv.items[0])

    assert view.assumed_uplifts_suppressed is True
    assert view.handling_rate == 0
    assert view.import_tax_rate == 0
    assert b["handling_amount"] == 0
    assert b["import_amount"] == 0
    assert b["landed_native"] == pytest.approx(1050)
    assert b["landed_usd"] == pytest.approx(10.50)


def test_kanagawa_without_commission_keeps_assumed_vendor_uplifts():
    inv = _vendor_invoice(commission_line=0, commission_line_rate=None, grand_total=1000)
    view = InvoiceView(inv, exchange_rate=0.01, handling_rate=0.10, import_tax_rate=0.15)

    b = view.breakdown(inv.items[0])

    assert view.assumed_uplifts_suppressed is False
    assert b["handling_amount"] == pytest.approx(100)
    assert b["import_amount"] == pytest.approx(150)
    assert b["landed_native"] == pytest.approx(1250)


def test_extra_flat_is_usd_even_for_jpy_invoices():
    inv = _vendor_invoice(vendor_name="Brand Street Tokyo", commission_line=0, commission_line_rate=None, grand_total=1000)
    view = InvoiceView(
        inv,
        exchange_rate=0.01,
        handling_rate=0,
        import_tax_rate=0,
        extra_flat=20,
    )

    b = view.breakdown(inv.items[0])

    assert b["extra_flat_usd_per_item"] == pytest.approx(20)
    assert b["extra_flat_per_item"] == pytest.approx(2000)
    assert b["landed_native"] == pytest.approx(3000)
    assert b["landed_usd"] == pytest.approx(30)


def test_kanagawa_per_item_commission_also_suppresses_assumed_uplifts():
    inv = _vendor_invoice(
        commission_line=0,
        commission_line_rate=None,
        commission_fees=[FeeLine(source_id="A1", amount=50)],
        grand_total=1050,
    )
    view = InvoiceView(inv, exchange_rate=0.01, handling_rate=0.10, import_tax_rate=0.15)

    b = view.breakdown(inv.items[0])

    assert view.assumed_uplifts_suppressed is True
    assert b["handling_amount"] == 0
    assert b["import_amount"] == 0
    assert b["landed_native"] == pytest.approx(1050)
