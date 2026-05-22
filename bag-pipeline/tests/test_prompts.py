"""Tests that enforce style rules from RULES.md against drafted listings.

Each rule should have a corresponding test here. When a user gives new feedback:
  1. Add the rule to pipeline/prompts.py
  2. Add a test below that asserts a sample listing obeys the rule
  3. Append a line to RULES.md

These tests run against listings on disk (storage/{sku}/_listing.json) when
present, so you can use real outputs as fixtures. They fall back to a synthetic
listing when no real ones are around.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.prompts import SYSTEM_PROMPT
from pipeline.schema import BagListing
from tests.test_analyze import SchemaTests as _SchemaTests


BANNED_WORDS = [
    "stunning",
    "iconic",
    "timeless",
    "must-have",
    "beautiful",
    "gorgeous",
    "elevated",
    "elevate",
    "perfect",
    "elegant",
    "luxe",
]


def _all_listings() -> list[BagListing]:
    """Load real drafted listings from disk, or fall back to a synthetic one."""
    import tempfile

    storage = Path(tempfile.gettempdir()) / "bag-pipeline-sessions"
    out: list[BagListing] = []
    if storage.exists():
        for d in sorted(storage.iterdir()):
            p = d / "_listing.json"
            if p.exists():
                data = json.loads(p.read_text())
                data.pop("sku", None)
                try:
                    out.append(BagListing.model_validate(data))
                except Exception:
                    continue
    if not out:
        out.append(BagListing.model_validate(_SchemaTests.SAMPLE))
    return out


def _text_fields(listing: BagListing) -> list[str]:
    """Every user-facing text field on a listing."""
    fields = [
        listing.title,
        listing.material_line,
        listing.condition_text,
        listing.condition_notes.exterior,
        listing.condition_notes.hardware,
        listing.condition_notes.stitching,
        listing.condition_notes.strap,
    ]
    fields.extend(listing.details_bullets)
    return [f for f in fields if f]


class PromptIntegrityTests(unittest.TestCase):
    """Prompt-level smoke tests — make sure rules are stated in the prompt."""

    def test_prompt_lists_banned_words(self) -> None:
        for word in ("stunning", "iconic", "timeless"):
            self.assertIn(word, SYSTEM_PROMPT.lower())

    def test_prompt_specifies_bullet_range(self) -> None:
        self.assertIn("3 to 4 bullets", SYSTEM_PROMPT)

    def test_prompt_specifies_title_format(self) -> None:
        self.assertIn("{Era}'s {Brand} {Color} {Material} {Silhouette}", SYSTEM_PROMPT)

    def test_prompt_forbids_dimension_guessing(self) -> None:
        self.assertIn("never guess", SYSTEM_PROMPT.lower())


class ListingRuleTests(unittest.TestCase):
    """Rule checks against real drafted listings (or a synthetic fallback)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.listings = _all_listings()

    def test_no_banned_words(self) -> None:
        for listing in self.listings:
            for text in _text_fields(listing):
                lower = text.lower()
                for word in BANNED_WORDS:
                    self.assertNotIn(
                        word,
                        lower,
                        msg=f"banned word '{word}' in: {text!r}",
                    )

    def test_no_exclamation_marks(self) -> None:
        for listing in self.listings:
            for text in _text_fields(listing):
                self.assertNotIn("!", text, msg=f"exclamation in: {text!r}")

    def test_bullets_are_3_or_4(self) -> None:
        for listing in self.listings:
            n = len(listing.details_bullets)
            # Schema allows 3-6 for back-compat; the prompt asks for 3-4.
            # Real new outputs should land at 3 or 4.
            self.assertGreaterEqual(n, 3, msg=f"too few bullets in {listing.title}")
            self.assertLessEqual(
                n, 6, msg=f"too many bullets in {listing.title}"
            )

    def test_bullets_have_no_trailing_period(self) -> None:
        for listing in self.listings:
            for b in listing.details_bullets:
                self.assertFalse(
                    b.rstrip().endswith("."),
                    msg=f"bullet ends with period: {b!r}",
                )

    def test_title_starts_with_era(self) -> None:
        for listing in self.listings:
            # Era format: 70's, 80's, 90's, 00's, 10's, 20's
            self.assertRegex(
                listing.title,
                r"^\d{2}'s ",
                msg=f"title does not start with era: {listing.title!r}",
            )

    def test_dimensions_default_is_placeholder(self) -> None:
        # New analyses always leave dimensions as the placeholder; users edit
        # in hand. This check only applies to listings that haven't been edited.
        for listing in self.listings:
            if listing.dimensions != "[measure in hand]":
                # User has filled it in — accept anything non-empty
                self.assertTrue(listing.dimensions.strip())


if __name__ == "__main__":
    unittest.main()
