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
