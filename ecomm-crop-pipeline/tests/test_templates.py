"""Invariants for the bundled listing templates.

Pins behavioral rules, not magic numbers — so re-tuning the framing values
across all full-body slots together doesn't break the suite, but tuning one
slot in isolation (which produces inconsistent subject placement) does.
"""

from __future__ import annotations

from crop_pipeline.templates import Template, load_template


FULL_BODY_SLOTS = {"01_hero", "02_three_quarter", "03_back"}
DETAIL_SLOT = "04_detail"


def _full_body_specs(template: Template):
    return [s for s in template.shots if s.slot in FULL_BODY_SLOTS and s.region_of_subject is None]


def test_listing_standard_full_body_slots_share_framing():
    """Hero, 3/4, and back must use the same subject_height_fraction and
    vertical_bias so the subject lands in the same spot across the set.

    Tuning any one of these in isolation makes the listing look like a
    grab-bag of unrelated shots — see RULES.md (2026-05-31, consistent
    placement)."""
    t = load_template("listing-standard")
    specs = _full_body_specs(t)
    assert len(specs) == 3, f"expected 3 full-body slots, got {len(specs)}"

    heights = {s.subject_height_fraction for s in specs}
    biases = {s.vertical_bias for s in specs}
    assert len(heights) == 1, f"full-body slots have differing subject_height_fraction: {heights}"
    assert len(biases) == 1, f"full-body slots have differing vertical_bias: {biases}"


def test_listing_standard_detail_slot_uses_region():
    """The detail slot is intentionally a zoom — it uses region_of_subject,
    not the shared full-body framing."""
    t = load_template("listing-standard")
    detail = next(s for s in t.shots if s.slot == DETAIL_SLOT)
    assert detail.region_of_subject is not None
    assert detail.subject_height_fraction is None


def test_listing_standard_has_no_side_slot():
    """Pure side profile was dropped (2026-05-31) — it faced the same
    direction as the 3/4 turn in the source shoot, making them redundant.
    If a future shoot captures a true opposite-direction 3/4, the side can
    come back as ``03_three_quarter_right``."""
    t = load_template("listing-standard")
    slot_names = {s.slot for s in t.shots}
    assert "03_side" not in slot_names


# --- listing-tops invariants ------------------------------------------------


def test_listing_tops_has_no_angled_slot():
    """Same redundancy reason as the side slot in listing-standard — shot 4
    faces the same direction as shot 1 in the current shoot, so the angled
    zoom reads as a duplicate of the front zoom. Re-add as
    ``03_top_three_quarter_angled`` if a future shoot captures a true
    opposite-direction 3/4 turn."""
    t = load_template("listing-tops")
    slot_names = {s.slot for s in t.shots}
    assert "03_top_three_quarter_angled" not in slot_names


def test_listing_tops_full_body_matches_listing_standard_framing():
    """Full-body framing must be identical across category templates so the
    subject lands in the same spot whether the listing is for a coat (uses
    listing-standard) or a top (uses listing-tops). Tune both templates'
    full-body slots together — see RULES.md (2026-05-31, consistent
    placement)."""
    tops = load_template("listing-tops")
    std = load_template("listing-standard")

    tops_full_body = next(s for s in tops.shots if s.slot == "01_full_body")
    std_hero = next(s for s in std.shots if s.slot == "01_hero")

    assert tops_full_body.subject_height_fraction == std_hero.subject_height_fraction, (
        f"tops full_body height={tops_full_body.subject_height_fraction} "
        f"!= standard hero height={std_hero.subject_height_fraction}"
    )
    assert tops_full_body.vertical_bias == std_hero.vertical_bias, (
        f"tops full_body bias={tops_full_body.vertical_bias} "
        f"!= standard hero bias={std_hero.vertical_bias}"
    )


def test_listing_tops_zoom_slots_share_region():
    """The two top-zoom slots (front, back) must use identical
    region_of_subject and region_fill so the pair reads as consistent crops
    of the same garment, not two unrelated framings."""
    t = load_template("listing-tops")
    zooms = [s for s in t.shots if s.region_of_subject is not None]
    assert len(zooms) == 2, f"expected 2 zoom slots, got {len(zooms)}"
    regions = {s.region_of_subject for s in zooms}
    fills = {s.region_fill for s in zooms}
    assert len(regions) == 1, f"zoom slots have differing region_of_subject: {regions}"
    assert len(fills) == 1, f"zoom slots have differing region_fill: {fills}"
