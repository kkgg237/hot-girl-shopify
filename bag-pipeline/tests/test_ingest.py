"""Tests for Stage 1 ingest against tests/fixtures/."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.ingest import (
    Bag,
    build_manifest,
    ingest,
    scan_folder,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ScanFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bags = scan_folder(FIXTURES)
        self.by_sku = {b.sku: b for b in self.bags}

    def test_groups_by_sku(self) -> None:
        self.assertEqual(
            set(self.by_sku),
            {"LOU_0226_817", "PRA_0001_001", "CHA_0042_222", "GUC_9999_555"},
        )

    def test_bags_sorted_by_sku(self) -> None:
        skus = [b.sku for b in self.bags]
        self.assertEqual(skus, sorted(skus))

    def test_multi_shot_bag_uses_01_as_hero(self) -> None:
        bag = self.by_sku["LOU_0226_817"]
        self.assertTrue(bag.hero.endswith("LOU_0226_817-01.jpg"))
        self.assertEqual(bag.flags, [])
        self.assertEqual(len(bag.shots), 5)

    def test_shots_are_in_numeric_order(self) -> None:
        bag = self.by_sku["LOU_0226_817"]
        self.assertEqual(
            [Path(p).name for p in bag.shots],
            [
                "LOU_0226_817-01.jpg",
                "LOU_0226_817-02.jpg",
                "LOU_0226_817-03.jpg",
                "LOU_0226_817-04.jpg",
                "LOU_0226_817-05.jpg",
            ],
        )

    def test_single_image_bag(self) -> None:
        bag = self.by_sku["PRA_0001_001"]
        self.assertEqual(len(bag.shots), 1)
        self.assertEqual(bag.hero, bag.shots[0])
        self.assertEqual(bag.flags, [])

    def test_missing_hero_is_flagged_and_falls_back_to_lowest(self) -> None:
        bag = self.by_sku["CHA_0042_222"]
        self.assertIn("missing_hero_shot_01", bag.flags)
        self.assertTrue(bag.hero.endswith("CHA_0042_222-02.jpg"))
        self.assertEqual(len(bag.shots), 3)

    def test_jpeg_and_uppercase_extensions_are_included(self) -> None:
        bag = self.by_sku["GUC_9999_555"]
        self.assertEqual(len(bag.shots), 2)
        self.assertTrue(bag.hero.endswith("GUC_9999_555-01.JPG"))

    def test_non_jpg_and_hidden_files_ignored(self) -> None:
        all_shots = [Path(p).name for b in self.bags for p in b.shots]
        for name in ("notes.txt", "random.jpg", ".DS_Store", ".hidden-01.jpg"):
            self.assertNotIn(name, all_shots)

    def test_random_jpg_without_shot_suffix_ignored(self) -> None:
        for bag in self.bags:
            for shot in bag.shots:
                self.assertNotEqual(Path(shot).name, "random.jpg")


class ManifestTests(unittest.TestCase):
    def test_build_manifest_has_expected_top_level_fields(self) -> None:
        manifest = build_manifest(FIXTURES, shoot_id="2026-05-11")
        data = manifest.to_dict()
        self.assertEqual(data["shoot_id"], "2026-05-11")
        self.assertEqual(data["source_folder"], str(FIXTURES.resolve()))
        self.assertIn("created_at", data)
        self.assertEqual(len(data["bags"]), 4)

    def test_manifest_bag_shape(self) -> None:
        manifest = build_manifest(FIXTURES, shoot_id="test")
        bag = next(b for b in manifest.bags if b.sku == "LOU_0226_817")
        self.assertIsInstance(bag, Bag)
        self.assertIn("hero", bag.__dict__)
        self.assertIn("shots", bag.__dict__)
        self.assertIn("flags", bag.__dict__)

    def test_ingest_writes_json_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            manifest_path = ingest(FIXTURES, "2026-05-11", output_dir)
            self.assertTrue(manifest_path.exists())
            self.assertEqual(manifest_path.name, "2026-05-11.json")

            data = json.loads(manifest_path.read_text())
            self.assertEqual(data["shoot_id"], "2026-05-11")
            self.assertEqual(len(data["bags"]), 4)
            cha = next(b for b in data["bags"] if b["sku"] == "CHA_0042_222")
            self.assertIn("missing_hero_shot_01", cha["flags"])

    def test_empty_folder_produces_empty_bag_list(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            manifest = build_manifest(Path(tmp), shoot_id="empty")
            self.assertEqual(manifest.bags, [])


if __name__ == "__main__":
    unittest.main()
