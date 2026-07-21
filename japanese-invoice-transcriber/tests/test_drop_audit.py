from __future__ import annotations

import drop_audit as da


def test_parse_skus_dedupes_and_accepts_commas():
    assert da.parse_skus(" ABC-1\nDEF-2, ABC-1\n  ghi-3 ") == ["ABC-1", "DEF-2", "ghi-3"]


def test_bag_accessory_eligibility_uses_product_context():
    eligible = da.DropAuditProduct(
        sku="BAG1",
        title="Chanel Black Caviar Flap Bag",
        product_type="Handbags",
        tags=["Bags", "Designer"],
    )
    assert da.is_bag_or_accessory(eligible)

    not_eligible = da.DropAuditProduct(
        sku="TOP1",
        title="Comme des Garcons Black Wool Blazer",
        product_type="Jackets",
        tags=["Apparel"],
    )
    assert not da.is_bag_or_accessory(not_eligible)


def test_plan_products_selects_only_blank_eligible_with_image():
    products = [
        da.DropAuditProduct(sku="A", title="Prada Nylon Shoulder Bag", product_type="Bags", image_url="https://img", description_html=""),
        da.DropAuditProduct(sku="B", title="Gucci Wallet", product_type="Accessories", image_url="https://img", description_html="<p>Done</p>"),
        da.DropAuditProduct(sku="C", title="Margiela Dress", product_type="Dresses", image_url="https://img", description_html=""),
        da.DropAuditProduct(sku="D", title="Fendi Baguette", product_type="Bags", image_url="", description_html=""),
    ]

    rows = da.plan_products(products)

    assert [(r.sku, r.status, r.selected_by_default) for r in rows] == [
        ("A", "Ready to generate", True),
        ("B", "Has description", False),
        ("C", "Not eligible", False),
        ("D", "Missing image", False),
    ]


def test_bag_model_words_are_eligible_without_the_word_bag():
    for title in (
        "Chanel Black Double Flap Half Moon 24K Hardware",
        "Gucci Black Sherry Canvas Pouch",
        "Gucci Brown Red Monogram Pochette",
        "Louis Vuitton Monogram Sac Plat",
        "Prada Nylon Wristlet",
    ):
        assert da.is_bag_or_accessory(da.DropAuditProduct(sku="X", title=title)), title


def test_plan_products_force_eligible_and_not_eligible_with_description():
    products = [
        da.DropAuditProduct(sku="A", title="Weird Named Item", product_type="", image_url="https://img"),
        da.DropAuditProduct(sku="B", title="Margiela Dress", product_type="Dresses", image_url="https://img", description_html="<p>Done</p>"),
    ]

    plain = da.plan_products(products)
    assert [(r.sku, r.status) for r in plain] == [("A", "Not eligible"), ("B", "Not eligible")]

    forced = da.plan_products(products, force_eligible={"A", "B"})
    assert [(r.sku, r.status) for r in forced] == [("A", "Ready to generate"), ("B", "Has description")]


def test_render_shopify_description_limits_details_and_needs_review_dimensions():
    draft = da.DescriptionDraft(
        dimensions=None,
        details=[
            "Black quilted leather exterior",
            "Front flap with turn-lock closure",
            "Chain-link shoulder strap",
            "Interior compartment with slip pocket",
            "Extra line should be dropped",
        ],
        material="Leather, Fabric Lining, Gold-Tone Hardware",
        condition_notes="8/10 – Light surface wear throughout. Minor corner wear. Interior appears clean.",
    )

    text = da.render_shopify_description(draft)

    assert "DIMENSIONS:\nNeeds review" in text
    assert text.count("\n", text.index("DETAILS:"), text.index("\n\nMATERIAL:")) == 4
    assert "Extra line should be dropped" not in text
    assert "perfect for" not in text.lower()


def test_shopify_description_html_bullets_details_and_bolds_headers():
    text = """DIMENSIONS:
10" L x 3" W x 6" H

DETAILS:
Black quilted leather exterior
Front flap with turn-lock closure
Chain-link shoulder strap

MATERIAL:
Leather, Fabric Lining & Gold-Tone Hardware

CONDITION NOTES:
8/10 – Light surface wear. Interior clean.
"""
    out = da.shopify_description_html(text)

    assert '<p><strong>DIMENSIONS:</strong><br>10&quot; L x 3&quot; W x 6&quot; H</p>' in out
    assert "<p><strong>DETAILS:</strong></p>" in out
    assert (
        "<ul><li>Black quilted leather exterior</li>"
        "<li>Front flap with turn-lock closure</li>"
        "<li>Chain-link shoulder strap</li></ul>"
    ) in out
    assert "<p><strong>MATERIAL:</strong><br>Leather, Fabric Lining &amp; Gold-Tone Hardware</p>" in out
    assert "<p><strong>CONDITION NOTES:</strong><br>8/10 – Light surface wear. Interior clean.</p>" in out
    assert "<br>Black quilted" not in out  # details must not fall back to <br> lines


def test_shopify_description_html_strips_manual_bullet_prefixes():
    out = da.shopify_description_html("DETAILS:\n- One thing\n• Another thing\n")
    assert "<li>One thing</li><li>Another thing</li>" in out


def test_product_from_sku_result_collects_all_image_urls():
    data = {
        "productVariants": {
            "nodes": [
                {
                    "legacyResourceId": "22",
                    "sku": "BAG1",
                    "price": "450.00",
                    "product": {
                        "legacyResourceId": "11",
                        "title": "Chanel Flap Bag",
                        "status": "ACTIVE",
                        "productType": "Handbags",
                        "tags": ["Bags"],
                        "descriptionHtml": "",
                        "featuredImage": {"url": "https://img/1.jpg"},
                        "media": {
                            "nodes": [
                                {"image": {"url": "https://img/1.jpg"}},
                                {"image": {"url": "https://img/2.jpg"}},
                                {},  # non-image media (e.g. video)
                                {"image": {"url": "https://img/3.jpg"}},
                            ]
                        },
                    },
                }
            ]
        }
    }
    p = da._product_from_sku_result("BAG1", data, shop="x.myshopify.com")
    assert p.image_url == "https://img/1.jpg"
    assert p.image_urls == ["https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg"]


def test_lookup_products_by_tags_dedupes_and_reports_empty_tags():
    node = {
        "legacyResourceId": "11",
        "title": "Chanel Flap Bag",
        "status": "ACTIVE",
        "productType": "Handbags",
        "tags": ["drop-7", "bags"],
        "descriptionHtml": "",
        "featuredImage": {"url": "https://img/1.jpg"},
        "media": {"nodes": [{"image": {"url": "https://img/1.jpg"}}]},
        "variants": {"nodes": [{"legacyResourceId": "22", "sku": "BAG1", "price": "450.00"}]},
    }

    def fake_gql(query, variables):
        q = variables["q"]
        if q in ('tag:"drop-7"', 'tag:"bags"'):
            return {"products": {"nodes": [node]}}
        return {"products": {"nodes": []}}

    products = da.lookup_products_by_tags(["drop-7", "bags", "nope"], gql_fn=fake_gql, shop="x.myshopify.com")

    assert [(p.sku, p.found) for p in products] == [("BAG1", True), ("tag:nope", False)]
    assert products[0].title == "Chanel Flap Bag"
    assert products[0].price == "450.00"
    assert products[0].product_id == 11
    assert products[0].variant_id == 22
    assert products[0].image_urls == ["https://img/1.jpg"]
    assert 'No products tagged "nope"' in products[1].error


def test_shopify_image_url_resizes_only_shopify_cdn():
    assert da.shopify_image_url("https://cdn.shopify.com/s/files/1/x.jpg", 800) == "https://cdn.shopify.com/s/files/1/x.jpg?width=800"
    assert da.shopify_image_url("https://cdn.shopify.com/s/files/1/x.jpg?v=123", 240) == "https://cdn.shopify.com/s/files/1/x.jpg?v=123&width=240"
    assert da.shopify_image_url("https://example.com/x.jpg", 800) == "https://example.com/x.jpg"
    assert da.shopify_image_url("", 800) == ""


def test_parse_json_object_extracts_json_from_prose():
    text = 'I searched the web.\n\n{"dimensions": "10\\" L x 3\\" W x 6\\" H", "confidence": "high", "sources": ["https://a"], "notes": "ok"}'
    data = da.parse_json_object(text)
    assert data["confidence"] == "high"


def test_parse_json_object_repairs_unescaped_inch_marks():
    text = '{"dimensions": "10" L x 3" W x 6" H", "confidence": "high", "sources": [], "notes": "price was 450"}'
    data = da.parse_json_object(text)
    assert data["dimensions"] == '10" L x 3" W x 6" H'
    assert data["notes"] == "price was 450"


def test_search_link_urls():
    lens = da.reverse_image_search_url("https://cdn.shopify.com/s/files/1/x.jpg?width=800")
    assert lens.startswith("https://lens.google.com/uploadbyurl?url=")
    assert "https%3A%2F%2Fcdn.shopify.com" in lens

    listing = da.title_listing_search_url("90's Chanel Red Egg Clutch")
    assert listing.startswith("https://www.google.com/search?q=")
    assert "Chanel" in listing and "dimensions" in listing


def test_audit_generated_description_rejects_flowery_copy_and_missing_sections():
    bad = "A timeless statement piece perfect for elevating any wardrobe."
    audit = da.audit_generated_description(bad)
    assert not audit.passed
    assert any("missing DIMENSIONS" in issue for issue in audit.issues)
    assert any("banned phrase" in issue for issue in audit.issues)

    good = """DIMENSIONS:
Needs review

DETAILS:
Black leather exterior
Front flap closure
Interior slip pocket

MATERIAL:
Leather, Fabric Lining, Gold-Tone Hardware

CONDITION NOTES:
8/10 – Light surface wear throughout. Interior appears clean.
"""
    assert da.audit_generated_description(good).passed


def test_audit_allows_long_tiered_condition_notes():
    ok = """DIMENSIONS:
Needs review

DETAILS:
Black leather exterior
Front flap closure
Interior slip pocket

MATERIAL:
Leather, Fabric Lining, Gold-Tone Hardware

CONDITION NOTES:
7/10 (Good) – Moderate signs of wear. Worn corners and scratches on the front and back leather. Interior lining shows light marks. Hardware functional. Stitching intact.
"""
    assert da.audit_generated_description(ok).passed
