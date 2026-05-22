"""Tests for Stage 2 analyze: image preprocessing + JSON extraction.

The real Claude call is NOT exercised here — that requires an API key and
costs money. The web-endpoint test in test_web.py covers wiring with a
mock; this file covers the pieces that don't need a network round-trip.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from pipeline.analyze import _extract_json, _preprocess
from pipeline.schema import BagListing


def _write_image(path: Path, size: tuple[int, int]) -> None:
    img = Image.new("RGB", size, color=(128, 64, 200))
    img.save(path, format="JPEG", quality=80)


class PreprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(__file__).resolve().parent / "_tmp_images"
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_resizes_large_image_to_1568_long_edge(self) -> None:
        path = self.tmp_dir / "big.jpg"
        _write_image(path, (4000, 3000))
        jpeg_bytes = _preprocess(path)
        out = Image.open(io.BytesIO(jpeg_bytes))
        self.assertEqual(max(out.size), 1568)
        self.assertEqual(out.size, (1568, 1176))

    def test_leaves_small_image_alone(self) -> None:
        path = self.tmp_dir / "small.jpg"
        _write_image(path, (800, 600))
        jpeg_bytes = _preprocess(path)
        out = Image.open(io.BytesIO(jpeg_bytes))
        self.assertEqual(out.size, (800, 600))

    def test_handles_portrait_orientation(self) -> None:
        path = self.tmp_dir / "tall.jpg"
        _write_image(path, (2000, 4000))
        jpeg_bytes = _preprocess(path)
        out = Image.open(io.BytesIO(jpeg_bytes))
        self.assertEqual(max(out.size), 1568)
        self.assertEqual(out.size, (784, 1568))


class JsonExtractionTests(unittest.TestCase):
    def test_extracts_plain_json(self) -> None:
        text = '{"brand": "Prada", "x": 1}'
        self.assertEqual(_extract_json(text), text)

    def test_extracts_json_from_code_fence(self) -> None:
        text = '```json\n{"brand": "Prada"}\n```'
        self.assertEqual(_extract_json(text), '{"brand": "Prada"}')

    def test_extracts_json_with_leading_prose(self) -> None:
        text = 'Here is the listing:\n{"brand": "Prada"}'
        self.assertEqual(_extract_json(text), '{"brand": "Prada"}')

    def test_raises_when_no_json(self) -> None:
        with self.assertRaises(ValueError):
            _extract_json("no braces here")


class SchemaTests(unittest.TestCase):
    SAMPLE = {
        "brand": "Prada",
        "brand_confidence": "high",
        "model": "Sound Lock shoulder bag",
        "model_confidence": "medium",
        "model_candidates": ["Sound Lock", "Easy"],
        "era": "00's",
        "colorway": "Metallic Gold",
        "material_primary": "Pebbled Leather",
        "silhouette": "Shoulder Bag",
        "title": "00's Prada Gold Metallic Pebbled Leather Shoulder Bag",
        "details_bullets": ["a", "b", "c", "d"],
        "material_line": "Leather, Gold-Tone Hardware",
        "condition_grade": 8.0,
        "condition_notes": {
            "exterior": "minor wear",
            "hardware": "light scratches",
            "stitching": "intact",
            "strap": "even patina",
        },
        "condition_unverifiable": ["interior", "base"],
        "condition_text": "Exterior is light wear. Hardware retains sheen. Stitching intact.",
    }

    def test_valid_payload_parses(self) -> None:
        listing = BagListing.model_validate(self.SAMPLE)
        self.assertEqual(listing.brand, "Prada")
        self.assertEqual(listing.condition_grade, 8.0)

    def test_accepts_three_bullets(self) -> None:
        ok = {**self.SAMPLE, "details_bullets": ["one", "two", "three"]}
        listing = BagListing.model_validate(ok)
        self.assertEqual(len(listing.details_bullets), 3)

    def test_rejects_two_bullets(self) -> None:
        bad = {**self.SAMPLE, "details_bullets": ["only", "two"]}
        with self.assertRaises(Exception):
            BagListing.model_validate(bad)

    def test_rejects_more_than_six_bullets(self) -> None:
        bad = {**self.SAMPLE, "details_bullets": ["1", "2", "3", "4", "5", "6", "7"]}
        with self.assertRaises(Exception):
            BagListing.model_validate(bad)

    def test_rejects_out_of_range_grade(self) -> None:
        bad = {**self.SAMPLE, "condition_grade": 11.0}
        with self.assertRaises(Exception):
            BagListing.model_validate(bad)


if __name__ == "__main__":
    unittest.main()
