"""Pure SKU/filename helpers — no I/O beyond a directory glob, fully testable.

The crop pipeline writes ``{SKU}_{slot}.jpg`` where SKU itself contains
underscores (e.g. ``BRU_2605_001``) and slot is one of the listing-standard
slots (``01_hero`` …). So we recover the SKU by stripping a KNOWN slot suffix,
never by splitting on ``_``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ecomm_pipeline.config import DEFAULT_SLOT_ORDER


def slot_of(filename: str, slot_order: tuple[str, ...] = DEFAULT_SLOT_ORDER) -> Optional[str]:
    """Return the slot a crop filename ends with, or None if it matches none."""
    stem = Path(filename).stem  # drop .jpg
    for slot in slot_order:
        if stem.endswith(f"_{slot}"):
            return slot
    return None


def sku_from_crop_name(
    filename: str, slot_order: tuple[str, ...] = DEFAULT_SLOT_ORDER
) -> Optional[str]:
    """Recover the bare SKU from a ``{SKU}_{slot}.jpg`` crop filename.

    >>> sku_from_crop_name("BRU_2605_001_01_hero.jpg")
    'BRU_2605_001'
    >>> sku_from_crop_name("A_B_C_D_03_back.jpg")
    'A_B_C_D'

    Returns None when the filename doesn't end in a known slot suffix (so stray
    files in the staging dir are ignored rather than mis-parsed).
    """
    stem = Path(filename).stem
    for slot in slot_order:
        suffix = f"_{slot}"
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return None


def group_crops_by_sku(
    staging_dir: Path, slot_order: tuple[str, ...] = DEFAULT_SLOT_ORDER
) -> dict[str, list[tuple[str, Path]]]:
    """Group ``staging_dir/*.jpg`` crops into ``{SKU: [(slot, path), ...]}``.

    Each SKU's list is ordered by ``slot_order`` (hero → … → detail) so photos
    upload in the right sequence. Files that don't end in a known slot are
    skipped. (Used by the Phase 1+ push loop; pure enough to live here.)
    """
    order = {slot: i for i, slot in enumerate(slot_order)}
    grouped: dict[str, list[tuple[str, Path]]] = {}
    for path in sorted(Path(staging_dir).glob("*.jpg")):
        slot = slot_of(path.name, slot_order)
        sku = sku_from_crop_name(path.name, slot_order)
        if slot is None or sku is None:
            continue
        grouped.setdefault(sku, []).append((slot, path))
    for slots in grouped.values():
        slots.sort(key=lambda pair: order.get(pair[0], len(order)))
    return grouped
