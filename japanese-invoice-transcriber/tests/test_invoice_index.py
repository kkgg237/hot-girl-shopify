from __future__ import annotations

from pathlib import Path

import invoice_index as ii


def test_canonical_order_key_collapses_stem_variants():
    for name in (
        "W2605289159.json",
        "buyee_W2605289159.json",
        "edited_buyee_W2605289159.json",
        "buyee_W2605289159.shopify_pushed.json",
    ):
        assert ii.canonical_order_key(name) == "W2605289159", name


def test_canonical_order_key_vendor_and_manual_invoices():
    assert (
        ii.canonical_order_key("edited_260503-DKC-Past Studies_INVOICE.json")
        == "260503-DKC-Past Studies_INVOICE"
    )
    assert (
        ii.canonical_order_key("manual_invoice_2026-05-07_EUR.json")
        == "manual_invoice_2026-05-07_EUR"
    )
    # No leading-W numeric id must not be mistaken for an order id.
    assert ii.canonical_order_key("Invoice - 1000263795118.json") == "Invoice - 1000263795118"


def test_group_invoice_files_dedupes_order_and_prefers_edited():
    # mtime: the plain raw stem is NEWEST, but the edited copy must still win.
    order = {
        "W2605289159.json": 300,               # newest raw
        "buyee_W2605289159.json": 100,
        "edited_buyee_W2605289159.json": 200,  # edited (older than plain raw)
        "buyee_W2605289159.shopify_pushed.json": 250,
    }
    paths = [Path("output") / n for n in order]
    groups = ii.group_invoice_files(paths, mtime=lambda p: order[p.name])

    assert len(groups) == 1
    g = groups[0]
    assert g.order_key == "W2605289159"
    assert g.load_path.name == "edited_buyee_W2605289159.json"  # edits win over newer raw
    assert g.has_edits and g.is_buyee and g.is_pushed
    # The pushed sidecar is not itself a loadable variant.
    assert all(not v.name.endswith(".shopify_pushed.json") for v in g.variants)
    assert len(g.variants) == 3


def test_group_invoice_files_raw_only_and_exclusions():
    order = {
        "W2603199548.json": 10,
        "cleanup_report_2026-07-09.json": 20,               # excluded (prefix)
        "commercial_invoice_header.json": 30,                # excluded (exact)
        "manual_invoice_EUR.comps.json": 35,                 # excluded (comps sidecar)
        "edited_manual_invoice_EUR.json": 40,
    }
    paths = [Path("output") / n for n in order]
    groups = {g.order_key: g for g in ii.group_invoice_files(paths, mtime=lambda p: order[p.name])}

    assert set(groups) == {"W2603199548", "manual_invoice_EUR"}
    raw = groups["W2603199548"]
    assert raw.load_path.name == "W2603199548.json"
    assert raw.has_edits is False and raw.is_buyee is True and raw.is_pushed is False
    assert groups["manual_invoice_EUR"].is_buyee is False


def test_from_location_extracts_city_country_or_falls_back():
    assert ii.from_location(
        vendor_address="1-19-9 Tsutsumi-dori, Sumida-ku, Tokyo, 131-0034, JAPAN"
    ) == "Tokyo, Japan"
    assert ii.from_location(vendor_name="Buyee", invoice_type="buyee_breakdown") == "Japan (Buyee)"
    assert ii.from_location(currency="JPY") == "Japan"
    assert ii.from_location(vendor_name="Manual Entry", currency="EUR") == "Europe"
