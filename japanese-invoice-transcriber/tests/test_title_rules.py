from __future__ import annotations

import json
from types import SimpleNamespace

import pricing
import title_learning


def _item(**kw):
    base = dict(
        detected_brand=None, product_type=None, material="", garment_length="",
        color="", pattern="", era="", model_name="", model_size="",
        style_adjectives="", origin="", override_title=None,
        condition_notes="", description_english="", description_original="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_shoe_title_appends_eu_size_from_condition_notes():
    # Unidentifiable "Chanel Shoes" gets a distinguishing size when the invoice
    # records one. EU size wins over the cm foot-length in the same note.
    shoes = _item(detected_brand="Chanel", product_type="Shoes",
                  condition_notes="EU 37, 23.5cm")
    assert pricing.compose_title(shoes) == "Chanel Shoes Size 37"

    sandals = _item(detected_brand="Chanel", product_type="Sandals",
                    color="Black", material="Leather",
                    condition_notes="size 36.5")
    assert pricing.compose_title(sandals) == "Chanel Black Leather Sandals Size 36.5"


def test_shoe_title_no_size_when_absent_and_never_uses_cm():
    # No size in the invoice → no suffix (don't invent one).
    assert pricing.compose_title(
        _item(detected_brand="Chanel", product_type="Shoes")
    ) == "Chanel Shoes"
    # A bare cm foot-length must never be read as an EU size.
    assert pricing.compose_title(
        _item(detected_brand="Chanel", product_type="Pumps", color="Black",
              condition_notes="24.5cm")
    ) == "Chanel Black Pumps"


def test_shoe_half_size_notation_normalizes_to_point_five():
    # "36 1/2" and "36½" both mean EU 36.5.
    for notation in ("size 36 1/2", "size 36½"):
        shoes = _item(detected_brand="Chanel", product_type="Pumps",
                      condition_notes=notation)
        assert pricing.compose_title(shoes) == "Chanel Pumps Size 36.5", notation


def test_shoe_size_never_reads_cm_footlength():
    # The cm foot-length must never be emitted — even when it's the only number
    # and even when it's (wrongly) labeled "size".
    for notes in ("24.5cm", "size 24.5cm", "JP 24.5cm"):
        shoes = _item(detected_brand="Chanel", product_type="Shoes",
                      condition_notes=notes)
        assert pricing.compose_title(shoes) == "Chanel Shoes", notes
    # EU size wins when both EU and cm are present.
    both = _item(detected_brand="Chanel", product_type="Ballet Flats",
                 color="Beige", condition_notes="EU 38.5, 24.5cm")
    assert pricing.compose_title(both) == "Chanel Beige Ballet Flats Size 38.5"


def test_size_suffix_only_applies_to_shoes():
    # A dress with a tagged size in its notes must NOT gain a "Size N" suffix —
    # the rule is shoe-scoped.
    dress = _item(detected_brand="Fendi", product_type="Dress", color="Grey",
                  condition_notes="Size 40")
    assert pricing.compose_title(dress) == "Fendi Grey Dress"


def test_backbone_complete_for_bag_with_brand_color_model():
    item = _item(detected_brand="Chanel", product_type="Handbag",
                 color="Black", model_name="Classic Flap")
    assert pricing.title_backbone_issues(item) == []


def test_backbone_flags_missing_fields():
    # No brand at all (compose_title would fall back to "Vintage"), a bag with
    # no model, and no color.
    item = _item(detected_brand=None, product_type="Handbag")
    issues = pricing.title_backbone_issues(item)
    assert "brand" in issues and "color" in issues and "model" in issues


def test_backbone_model_only_required_for_bags():
    top = _item(detected_brand="Chanel", product_type="Top", color="Black")
    assert pricing.title_backbone_issues(top) == []  # tops don't need a model


def test_backbone_never_flags_a_manual_override():
    item = _item(override_title="Chanel Whatever Bag")  # everything else blank
    assert pricing.title_backbone_issues(item) == []


def test_learned_replay_overrides_a_previously_corrected_title():
    item = _item(detected_brand="Chanel", product_type="Handbag",
                 color="Black", material="Lambskin")
    built = pricing.compose_title(item)
    try:
        pricing.set_learned_titles({built: "Chanel Black Quilted Lambskin Flap Bag"})
        assert pricing.compose_title(item) == "Chanel Black Quilted Lambskin Flap Bag"
    finally:
        pricing.set_learned_titles({})
    # Once cleared, we fall back to the composed title.
    assert pricing.compose_title(item) == built


def test_build_learned_titles_keeps_only_rich_unambiguous(tmp_path):
    p = tmp_path / "corr.jsonl"
    lines = [
        {"computed_title": "Chanel Black Lambskin Bag",       # rich, 1:1 -> kept
         "override_title": "Chanel Black Quilted Lambskin Flap Bag"},
        {"computed_title": "Chanel Pumps",                     # sparse (<3 tokens) -> dropped
         "override_title": "Chanel Black Leather Pumps"},
        {"computed_title": "Gucci Brown Shoulder Bag", "override_title": "A"},   # ambiguous
        {"computed_title": "Gucci Brown Shoulder Bag", "override_title": "B"},   # -> dropped
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    learned = title_learning.build_learned_titles(p)
    assert learned == {"Chanel Black Lambskin Bag": "Chanel Black Quilted Lambskin Flap Bag"}
    assert title_learning.corrected_computed_titles(p) == {
        "Chanel Black Lambskin Bag", "Chanel Pumps", "Gucci Brown Shoulder Bag",
    }
