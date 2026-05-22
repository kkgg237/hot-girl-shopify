"""Tests for the Shopify export (HTML body + CSV)."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.export import (
    SHOPIFY_HEADERS,
    _format_grade,
    _handle,
    to_body_html,
    to_csv,
    to_csv_row,
)
from pipeline.schema import BagListing
from tests.test_analyze import SchemaTests as _SchemaTests


def _make_listing(**overrides) -> BagListing:
    data = dict(_SchemaTests.SAMPLE)
    data.update(overrides)
    return BagListing.model_validate(data)


class BodyHtmlTests(unittest.TestCase):
    def test_uses_user_dimensions_when_provided(self) -> None:
        listing = _make_listing(dimensions='10" L x 2" W x 6" H')
        html = to_body_html(listing)
        self.assertIn('<p><strong>DIMENSIONS:</strong><br>10" L x 2" W x 6" H</p>', html)

    def test_dimensions_falls_back_to_placeholder(self) -> None:
        listing = _make_listing()  # default
        html = to_body_html(listing)
        self.assertIn("[measure in hand]", html)

    def test_renders_details_as_ul_li(self) -> None:
        listing = _make_listing(details_bullets=["Foo bar", "Baz qux", "Hello", "World"])
        html = to_body_html(listing)
        self.assertIn("<p><strong>DETAILS:</strong></p>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li>Foo bar</li>", html)
        self.assertIn("<li>World</li>", html)
        self.assertIn("</ul>", html)

    def test_material_line(self) -> None:
        listing = _make_listing(material_line="Leather, Gold-Tone Hardware")
        html = to_body_html(listing)
        self.assertIn(
            "<p><strong>MATERIAL:</strong><br>Leather, Gold-Tone Hardware</p>",
            html,
        )

    def test_condition_uses_integer_for_whole_grade(self) -> None:
        listing = _make_listing(condition_grade=8.0, condition_text="Light wear.")
        html = to_body_html(listing)
        self.assertIn("8/10 \u2013 Light wear.", html)
        self.assertNotIn("8.0/10", html)

    def test_condition_keeps_half_point(self) -> None:
        listing = _make_listing(condition_grade=8.5)
        html = to_body_html(listing)
        self.assertIn("8.5/10", html)

    def test_escapes_html_in_user_text(self) -> None:
        listing = _make_listing(condition_text="Front <flap> with logo")
        html = to_body_html(listing)
        self.assertIn("&lt;flap&gt;", html)
        self.assertNotIn("<flap>", html)


class HandleTests(unittest.TestCase):
    def test_lowercases_and_hyphenates(self) -> None:
        self.assertEqual(_handle("LOU_0226_817"), "lou-0226-817")

    def test_strips_leading_and_trailing_separators(self) -> None:
        self.assertEqual(_handle("__bag__"), "bag")

    def test_collapses_runs(self) -> None:
        self.assertEqual(_handle("Bag  Name"), "bag-name")


class GradeFormatTests(unittest.TestCase):
    def test_whole_number_no_decimal(self) -> None:
        self.assertEqual(_format_grade(8.0), "8")
        self.assertEqual(_format_grade(10.0), "10")

    def test_half_point_preserves_decimal(self) -> None:
        self.assertEqual(_format_grade(8.5), "8.5")


class CsvTests(unittest.TestCase):
    def test_row_has_expected_columns(self) -> None:
        row = to_csv_row("LOU_0226_817", _make_listing())
        self.assertEqual(set(row), set(SHOPIFY_HEADERS))

    def test_row_field_values(self) -> None:
        listing = _make_listing(
            brand="Prada",
            silhouette="Shoulder Bag",
            era="00's",
            colorway="Metallic Gold",
            title="00's Prada Gold Shoulder Bag",
        )
        row = to_csv_row("LOU_0226_817", listing)
        self.assertEqual(row["Handle"], "lou-0226-817")
        self.assertEqual(row["Title"], "00's Prada Gold Shoulder Bag")
        self.assertEqual(row["Vendor"], "Prada")
        self.assertEqual(row["Type"], "Shoulder Bag")
        self.assertEqual(row["Tags"], "00's, Prada, Metallic Gold")
        self.assertEqual(row["Published"], "FALSE")
        self.assertEqual(row["Status"], "draft")
        self.assertEqual(row["Variant SKU"], "LOU_0226_817")
        self.assertIn("DIMENSIONS:", row["Body (HTML)"])

    def test_csv_is_round_trippable(self) -> None:
        listing = _make_listing()
        csv_text = to_csv([("A", listing), ("B_2", _make_listing(title="Other"))])
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Variant SKU"], "A")
        self.assertEqual(rows[1]["Variant SKU"], "B_2")
        self.assertEqual(rows[1]["Title"], "Other")


if __name__ == "__main__":
    unittest.main()
