"""Anchor regression tests — user-confirmed titles never silently change.

For each entry in rules.yaml:regression_anchors with a populated
expected_title, this test loads the matching invoice item and asserts that
compose_title() still produces exactly that string.

If a rule change legitimately changes a confirmed title, the user must
explicitly update the expected_title in rules.yaml — these tests do not
auto-update under UPDATE_SNAPSHOTS.

Anchors with empty expected_title are skipped (TODO markers).
"""
from __future__ import annotations

import pytest

from heuristics import load_rules

from .conftest import OUTPUT_DIR, load_invoice


def _all_anchors_with_targets():
    """Return regression anchors that have a non-empty expected_title."""
    rules = load_rules()
    return [a for a in rules.regression_anchors if a.expected_title]


def _find_item_by_source_id(source_id: str):
    """Search every ORIGINAL invoice JSON (skip edited_*) for the given source_id.

    Returns (invoice_filename, item) or (None, None) if not found.
    Edited invoices are skipped so anchor tests don't accidentally read a
    user-curated title from the edited copy.
    """
    import json
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        if f.name.startswith("edited_"):
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for it in data.get("items", []):
            if it.get("source_id") == source_id:
                inv = load_invoice(f)
                typed_item = next((x for x in inv.items if x.source_id == source_id), None)
                return f.name, typed_item
    return None, None


@pytest.mark.parametrize(
    "anchor",
    _all_anchors_with_targets(),
    ids=lambda a: a.source_id,
)
def test_anchor_title_unchanged(anchor):
    """Each regression anchor's compose_title() must match expected_title exactly."""
    from pricing import compose_title

    invoice_name, item = _find_item_by_source_id(anchor.source_id)
    assert item is not None, (
        f"Anchor {anchor.source_id} not found in any output/*.json. "
        f"Either the invoice was deleted, the source_id changed, or this anchor "
        f"references an invoice that was never saved."
    )
    actual = compose_title(item)
    assert actual == anchor.expected_title, (
        f"\nAnchor {anchor.source_id} (from {invoice_name}) regressed:\n"
        f"  expected: {anchor.expected_title!r}\n"
        f"  actual:   {actual!r}\n"
        f"  note:     {anchor.note or '(no note)'}\n"
        f"\nIf this change is intentional, update the expected_title in "
        f"heuristics/rules.yaml:regression_anchors and document why in feedback.yaml."
    )


def test_at_least_one_anchor_exists():
    """Sanity check: we have anchors configured. Otherwise the suite is hollow."""
    anchors = _all_anchors_with_targets()
    assert len(anchors) > 0, (
        "No regression anchors with populated expected_title found in rules.yaml. "
        "Add at least one before relying on this test suite."
    )
