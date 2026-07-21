"""Tests for the commercial invoice generator (schema math + CSV layout)."""

from __future__ import annotations

import re

from commercial_invoice import (
    HS_CODES,
    HS_OPTIONS,
    SYSTEM_PROMPT,
    CommercialInvoice,
    CommercialLineItem,
    CommercialParty,
    hs_code_only,
    hs_option_for,
    invoice_total_usd,
    line_total_usd,
    to_invoice_csv,
)

SAMPLE_ITEMS = [
    CommercialLineItem(
        description="06-220 Leather Shoulder Bag (Second hand)",
        material_content="Leather",
        hs_code="4202.21",
        unit_value=5248,
    ),
    CommercialLineItem(
        description="16127 Sandals Black Size 37 Women's (Second hand)",
        material_content="Rubber",
        hs_code="6402.99",
        unit_value=787,
    ),
    CommercialLineItem(
        quantity=2,
        description="B086-6 Canvas Handbag (Second hand)",
        material_content="Textile",
        hs_code="4202.21",
        unit_value=1604,
    ),
]


def sample_invoice() -> CommercialInvoice:
    return CommercialInvoice(
        invoice_number="83",
        invoice_date="2026-06-25",
        supplier=CommercialParty(
            name="Kosuke Yamamoto",
            address="4-22-35-12 Nango, Chigasaki",
            city="Kanagawa",
            country="Japan",
        ),
        importer=CommercialParty(
            name="Past Studies",
            address="213 N Morgan unit 2G, 60607",
            city="Chicago IL",
            country="U.S.",
        ),
        rate_per_usd=169.0,
        items=SAMPLE_ITEMS,
    )


def test_line_total_rounds_to_whole_dollars():
    assert line_total_usd(SAMPLE_ITEMS[0], 169.0) == 31  # 5248/169 = 31.05


def test_line_total_multiplies_quantity():
    assert line_total_usd(SAMPLE_ITEMS[2], 169.0) == 19  # 2*1604/169 = 18.98


def test_invoice_total_is_sum_of_rounded_lines():
    inv = sample_invoice()
    assert invoice_total_usd(inv) == sum(
        line_total_usd(i, inv.rate_per_usd) for i in inv.items
    )


def test_csv_layout_matches_manual_invoice():
    lines = to_invoice_csv(sample_invoice()).splitlines()
    assert lines[0] == ",,Commercial INVOICE"
    assert "Invoice Number:,83" in lines
    assert "Invoice Date:,2026-06-25" in lines
    assert "Supplier Information,,Importer of Record" in lines
    assert "Name:,Kosuke Yamamoto,Company:,Past Studies" in lines
    assert (
        "PO #,Quantity,Product Description,Material Content,HS code,"
        "Unit Value (JPY),Total Value (USD)"
    ) in lines


def test_csv_line_items_numbered_and_converted():
    lines = to_invoice_csv(sample_invoice()).splitlines()
    assert "1,1,06-220 Leather Shoulder Bag (Second hand),Leather,4202.21,5248,31" in lines
    assert "3,2,B086-6 Canvas Handbag (Second hand),Textile,4202.21,1604,19" in lines


def test_csv_footer_has_origin_and_grand_total():
    inv = sample_invoice()
    lines = to_invoice_csv(inv).splitlines()
    total = invoice_total_usd(inv)
    assert lines[-1] == (
        f"Country of origin:,Japan,,,Estimated Total Value of all goods (USD),{total}"
    )


def test_address_commas_are_quoted():
    assert '"4-22-35-12 Nango, Chigasaki"' in to_invoice_csv(sample_invoice())


def test_hs_codes_are_unique_hs6():
    codes = [code for code, _ in HS_CODES]
    assert len(codes) == len(set(codes))
    for code in codes:
        assert re.fullmatch(r"\d{4}\.\d{2}", code), code


def test_hs_option_round_trip():
    for code, label in HS_CODES:
        opt = hs_option_for(code)
        assert opt == f"{code} — {label}"
        assert hs_code_only(opt) == code
    # Unknown codes pass through both directions untouched.
    assert hs_option_for("9999.99") == "9999.99"
    assert hs_code_only("9999.99") == "9999.99"


def test_hs_options_match_codes():
    assert HS_OPTIONS == [f"{c} — {l}" for c, l in HS_CODES]


def test_prompt_lists_every_hs_code_and_merge_rule():
    for code, _ in HS_CODES:
        assert code in SYSTEM_PROMPT
    assert "combine it into ONE line item" in SYSTEM_PROMPT


def test_csv_strips_dropdown_label_to_bare_code():
    inv = sample_invoice()
    inv.items = [
        CommercialLineItem(
            quantity=3,
            description="Wool Coat (Second hand)",
            material_content="Wool",
            hs_code="6202.20 — Coat / jacket, women's, wool, woven",
            unit_value=4200,
        )
    ]
    lines = to_invoice_csv(inv).splitlines()
    assert "1,3,Wool Coat (Second hand),Wool,6202.20,4200,75" in lines


def test_eur_invoice_header_and_conversion():
    inv = sample_invoice()
    inv.currency = "EUR"
    inv.rate_per_usd = 0.92
    inv.items = [
        CommercialLineItem(
            description="Wool Coat (Second hand)",
            material_content="Wool",
            hs_code="6202.20",
            unit_value=100,
        )
    ]
    lines = to_invoice_csv(inv).splitlines()
    assert (
        "PO #,Quantity,Product Description,Material Content,HS code,"
        "Unit Value (EUR),Total Value (USD)"
    ) in lines
    # 100 EUR / 0.92 = 108.7 → 109, and cents survive formatting
    assert "1,1,Wool Coat (Second hand),Wool,6202.20,100,109" in lines
    assert lines[-1].endswith("Estimated Total Value of all goods (USD),109")


def test_eur_unit_values_keep_cents():
    inv = sample_invoice()
    inv.currency = "EUR"
    inv.rate_per_usd = 0.92
    inv.items = [
        CommercialLineItem(
            description="Silk Scarf (Second hand)",
            material_content="Silk",
            hs_code="6214.10",
            unit_value=45.5,
        )
    ]
    lines = to_invoice_csv(inv).splitlines()
    assert "1,1,Silk Scarf (Second hand),Silk,6214.10,45.50,49" in lines
