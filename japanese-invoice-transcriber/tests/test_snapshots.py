"""Snapshot tests — full coverage of every item across every saved invoice.

For each invoice JSON in output/, we run compose_title() on every item and
compare to a saved snapshot in tests/snapshots/<invoice>.titles.json. Mismatch
fails the test with a clear diff.

Workflow:
    # See what's failing
    uv run --with pytest --with pyyaml --with pydantic pytest tests/test_snapshots.py -v

    # If the diff is intentional, regenerate snapshots:
    UPDATE_SNAPSHOTS=1 uv run --with pytest --with pyyaml --with pydantic pytest tests/test_snapshots.py
    # Then commit the updated snapshot files.

This is broader and noisier than the anchor tests (which are user-curated).
Snapshot diffs are useful for "did this rule change affect more items than
I expected?" — visible scope of every change.
"""
from __future__ import annotations

import pytest

from .conftest import (
    OUTPUT_DIR,
    load_invoice,
    snapshot_path_for,
    read_snapshot,
    write_snapshot,
    update_snapshots_enabled,
)


def _compute_titles(invoice_path):
    """Return {source_id: title} for every item in the invoice."""
    from pricing import compose_title
    inv = load_invoice(invoice_path)
    return {it.source_id: compose_title(it) for it in inv.items if it.source_id}


# Edited (human-curated) JSONs are intentionally drifted from the raw
# transcription — they reflect user title corrections. Snapshot tests run
# against the originals only so we catch unintended changes to the LLM
# extraction + title composition pipeline.
# We also skip sidecar metadata files (push ledgers, etc.) — they live
# alongside invoices but aren't invoices themselves.
_SIDECAR_SUFFIXES = (".shopify_pushed.json", ".metrics.json", ".comps.json")


def _is_original(p) -> bool:
    if p.name.startswith("edited_"):
        return False
    if any(p.name.endswith(suffix) for suffix in _SIDECAR_SUFFIXES):
        return False
    return True


@pytest.fixture(scope="session")
def all_invoices():
    return sorted(p for p in OUTPUT_DIR.glob("*.json") if _is_original(p))


def test_snapshots_exist(all_invoices):
    """At least one original invoice exists to test against."""
    assert len(all_invoices) > 0, (
        f"No original (non-edited) invoice JSONs found in {OUTPUT_DIR}. "
        f"Transcribe at least one invoice before running snapshot tests."
    )


@pytest.mark.parametrize(
    "invoice_path",
    sorted((p for p in OUTPUT_DIR.glob("*.json") if _is_original(p)), key=lambda p: p.name),
    ids=lambda p: p.stem,
)
def test_invoice_titles_match_snapshot(invoice_path):
    """Every item's title must match the saved snapshot, or UPDATE_SNAPSHOTS=1."""
    actual = _compute_titles(invoice_path)
    snap_path = snapshot_path_for(invoice_path)

    if update_snapshots_enabled():
        write_snapshot(snap_path, actual)
        pytest.skip(f"Updated snapshot at {snap_path.name} ({len(actual)} titles)")
        return

    if not snap_path.exists():
        write_snapshot(snap_path, actual)
        pytest.skip(
            f"Created baseline snapshot at {snap_path.name} ({len(actual)} titles). "
            f"Re-run to verify."
        )
        return

    expected = read_snapshot(snap_path)
    diffs = []
    for sid in sorted(set(actual) | set(expected)):
        a = actual.get(sid, "<missing>")
        e = expected.get(sid, "<missing>")
        if a != e:
            diffs.append((sid, e, a))

    if diffs:
        lines = [f"\n{len(diffs)} title(s) changed in {invoice_path.name}:"]
        for sid, e, a in diffs[:20]:  # limit noise
            lines.append(f"  {sid}")
            lines.append(f"    was: {e!r}")
            lines.append(f"    now: {a!r}")
        if len(diffs) > 20:
            lines.append(f"  ... +{len(diffs) - 20} more")
        lines.append(
            f"\nIf the changes are intentional, regenerate with:\n"
            f"  UPDATE_SNAPSHOTS=1 uv run --with pytest --with pyyaml --with pydantic "
            f"pytest tests/test_snapshots.py"
        )
        pytest.fail("\n".join(lines))
