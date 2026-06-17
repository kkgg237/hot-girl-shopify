"""Invariants for SKU recovery from crop filenames.

The one subtlety: the SKU itself contains underscores, so recovery must strip a
KNOWN slot suffix, never split on '_'. These tests pin that.
"""

from pathlib import Path

from ecomm_pipeline.sku import group_crops_by_sku, sku_from_crop_name, slot_of


def test_strips_known_slot_suffix():
    assert sku_from_crop_name("BRU_2605_001_01_hero.jpg") == "BRU_2605_001"
    assert sku_from_crop_name("BRU_2605_001_02_three_quarter.jpg") == "BRU_2605_001"
    assert sku_from_crop_name("BRU_2605_001_03_back.jpg") == "BRU_2605_001"
    assert sku_from_crop_name("BRU_2605_001_04_detail.jpg") == "BRU_2605_001"


def test_preserves_underscores_in_sku():
    # SKU with many underscores must survive intact — no naive '_' split.
    assert sku_from_crop_name("A_B_C_D_03_back.jpg") == "A_B_C_D"


def test_unknown_slot_returns_none():
    # Stray files that don't end in a known slot are ignored, not mis-parsed.
    assert sku_from_crop_name("BRU_2605_001_99_weird.jpg") is None
    assert sku_from_crop_name("random_file.jpg") is None


def test_slot_of():
    assert slot_of("BRU_2605_001_01_hero.jpg") == "01_hero"
    assert slot_of("BRU_2605_001_04_detail.jpg") == "04_detail"
    assert slot_of("nope.jpg") is None


def test_group_crops_orders_by_slot(tmp_path: Path):
    # Create crops out of slot order; grouping must return them hero→detail.
    for name in (
        "SKU_1_04_detail.jpg",
        "SKU_1_01_hero.jpg",
        "SKU_1_03_back.jpg",
        "SKU_2_01_hero.jpg",
        "ignore_me.jpg",  # no slot suffix → skipped
    ):
        (tmp_path / name).write_bytes(b"x")

    grouped = group_crops_by_sku(tmp_path)

    assert set(grouped) == {"SKU_1", "SKU_2"}
    assert [slot for slot, _ in grouped["SKU_1"]] == ["01_hero", "03_back", "04_detail"]
    assert [slot for slot, _ in grouped["SKU_2"]] == ["01_hero"]
