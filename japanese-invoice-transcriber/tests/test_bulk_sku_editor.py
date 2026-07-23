from __future__ import annotations

import bulk_sku_editor
from bulk_sku_editor import (
    ProductSkuRecord,
    build_update_plan,
    lookup_products_by_skus,
    parse_sku_terms,
    rows_to_apply,
)


def _record(sku: str = "ABC123") -> ProductSkuRecord:
    return ProductSkuRecord(
        sku=sku,
        product_id=111,
        variant_id=222,
        title="Old Title",
        price="88.00",
        tags="dress, archive",
        status="draft",
        admin_url="https://paststudies.myshopify.com/admin/products/111",
    )


def test_parse_sku_terms_accepts_spaces_commas_and_newlines_and_tracks_duplicates():
    parsed = parse_sku_terms(" ABC123 DEF456\nABC123, GHI789  ")

    assert parsed.terms == ["ABC123", "DEF456", "GHI789"]
    assert parsed.duplicates == ["ABC123"]


def test_build_update_plan_preserves_input_order_and_reports_not_found():
    records = {
        "DEF456": _record("DEF456"),
        "ABC123": _record("ABC123"),
    }

    plan = build_update_plan(["ABC123", "MISSING", "DEF456"], lambda terms: records)

    assert [r.sku for r in plan.records] == ["ABC123", "DEF456"]
    assert plan.not_found == ["MISSING"]


def test_lookup_products_by_skus_matches_shopify_sku_case_insensitively(monkeypatch):
    def fake_gql(_query, variables):
        assert variables == {"q": 'sku:"abc123"'}
        return {
            "productVariants": {
                "nodes": [
                    {
                        "legacyResourceId": "222",
                        "sku": "ABC123",
                        "price": "88.00",
                        "product": {
                            "legacyResourceId": "111",
                            "title": "Old Title",
                            "tags": ["dress", "archive"],
                            "status": "DRAFT",
                        },
                    }
                ]
            }
        }

    monkeypatch.setattr(bulk_sku_editor, "_gql", fake_gql)

    found = lookup_products_by_skus(["abc123"], shop="paststudies.myshopify.com")

    assert found["abc123"].sku == "ABC123"
    assert found["abc123"].product_id == 111
    assert found["abc123"].variant_id == 222


def test_rows_to_apply_only_keeps_changed_valid_rows():
    base = _record()
    edited_rows = [
        {
            "Keep": True,
            "SKU": "ABC123",
            "Product ID": 111,
            "Variant ID": 222,
            "Current title": "Old Title",
            "Current price": "88.00",
            "Current tags": "dress, archive",
            "Current status": "draft",
            "New title": "New Title",
            "New price": "120",
            "New tags": "dress, editorial",
            "New status": "active",
        },
        {
            "Keep": True,
            "SKU": "NOCHANGE",
            "Product ID": 333,
            "Variant ID": 444,
            "Current title": "Same",
            "Current price": "50.00",
            "Current tags": "tag",
            "Current status": "draft",
            "New title": "Same",
            "New price": "50.00",
            "New tags": "tag",
            "New status": "draft",
        },
    ]

    plans, errors = rows_to_apply(edited_rows)

    assert errors == []
    assert len(plans) == 1
    assert plans[0].sku == base.sku
    assert plans[0].product_id == 111
    assert plans[0].variant_id == 222
    assert plans[0].product_updates == {
        "title": "New Title",
        "tags": "dress, editorial",
        "status": "active",
    }
    assert plans[0].variant_updates == {"price": "120.00"}


def test_rows_to_apply_accepts_single_editable_field_columns():
    rows = [
        {
            "Keep": True,
            "SKU": "ABC123",
            "Product ID": 111,
            "Variant ID": 222,
            "Original title": "Old Title",
            "Original price": "88.00",
            "Original tags": "dress, archive",
            "Original status": "draft",
            "Title": "Old Title",
            "Price": "95",
            "Tags": "dress, archive, sale",
            "Status": "draft",
        }
    ]

    plans, errors = rows_to_apply(rows)

    assert errors == []
    assert len(plans) == 1
    assert plans[0].product_updates == {"tags": "dress, archive, sale"}
    assert plans[0].variant_updates == {"price": "95.00"}


def test_rows_to_apply_blocks_bad_price_status_and_missing_ids():
    rows = [
        {
            "Keep": True,
            "SKU": "BAD",
            "Product ID": "",
            "Variant ID": "",
            "Current title": "Old",
            "Current price": "88.00",
            "Current tags": "tag",
            "Current status": "draft",
            "New title": "Old",
            "New price": "free",
            "New tags": "tag",
            "New status": "live",
        }
    ]

    plans, errors = rows_to_apply(rows)

    assert plans == []
    assert errors
    assert "missing product ID" in errors[0]
    assert "missing variant ID" in errors[0]
    assert "invalid price" in errors[0]
    assert "invalid status" in errors[0]
