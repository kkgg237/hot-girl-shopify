"""Parse SKU + shot index from Capture One filenames.

Capture One exports name files like ``BRU_2605_001-1.jpg`` (SKU then shot)
or ``BRU_2605_002 3.jpg`` (SKU, space, shot). The unnumbered first file
``BRU_2605_002.jpg`` is treated as shot 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional


# Matches: <sku>[ <shot>|-<shot>]   where shot is digits.
# The separator is a space or a dash. Underscores are kept as part of the SKU
# because Capture One SKUs like ``BRU_2605_002`` use underscores internally.
# Examples that match:
#   BRU_2605_002        -> sku=BRU_2605_002, shot=0
#   BRU_2605_002 3      -> sku=BRU_2605_002, shot=3
#   BRU_2605_001-1      -> sku=BRU_2605_001, shot=1
_FILENAME_RE = re.compile(r"^(?P<sku>.+?)(?:[ \-](?P<shot>\d+))?$")


@dataclass(frozen=True)
class ParsedName:
    sku: str
    shot: int
    source: Path


def parse_filename(path: Path) -> ParsedName | None:
    """Return a ParsedName or None if the stem doesn't look like a SKU file."""
    stem = path.stem.strip()
    if not stem:
        return None
    m = _FILENAME_RE.match(stem)
    if not m:
        return None
    sku = m.group("sku").rstrip(" -_")
    shot = int(m.group("shot")) if m.group("shot") is not None else 0
    return ParsedName(sku=sku, shot=shot, source=path)


def group_by_sku(
    folder: Path,
    extensions: tuple[str, ...],
    on_duplicate: Optional[Callable[[str, int, Path, Path], None]] = None,
    on_normalize: Optional[Callable[[str, Dict[int, int]], None]] = None,
) -> Dict[str, List[ParsedName]]:
    """Walk ``folder`` and group images by SKU, ordered by shot index.

    When two or more files map to the same ``(sku, shot)`` — e.g. the same
    shot exported as both ``.jpg`` and ``.jpeg``, or with both space and
    dash separators (``BRU 1.jpg`` and ``BRU-1.jpg``), or a downloaded copy
    — the first file in sorted order is kept and the rest are dropped.
    ``on_duplicate(sku, shot, kept, dropped)`` fires for each dropped file
    so the caller can warn the user.

    Auto-normalizes contiguous shot ranges: if a SKU's non-zero shot
    indices form a contiguous range ``[N, N+1, ..., N+k]`` with ``N > 1``,
    they're renumbered so the smallest becomes shot 1. This rescues SKUs
    that were shot with Capture One's continuous session counter (e.g.
    ``ISS_011`` numbered 9–17 instead of 1–9) without breaking SKUs with
    legitimate gaps (e.g. ``ISS_02`` with shots 1–6, 8 — missing 7 stays
    missing). The unnumbered file (shot 0, conventionally a safety frame)
    is never renumbered. ``on_normalize(sku, mapping)`` fires with the
    ``{original_shot: new_shot}`` dict when normalization happens.
    """
    raw: Dict[str, List[ParsedName]] = {}
    exts = {e.lower() for e in extensions}
    for child in sorted(folder.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() not in exts:
            continue
        if child.name.startswith("."):
            continue
        parsed = parse_filename(child)
        if parsed is None:
            continue
        raw.setdefault(parsed.sku, []).append(parsed)

    groups: Dict[str, List[ParsedName]] = {}
    for sku, items in raw.items():
        items.sort(key=lambda p: (p.shot, p.source.name))
        deduped: List[ParsedName] = []
        seen_shot_to_index: Dict[int, int] = {}
        for p in items:
            if p.shot in seen_shot_to_index:
                kept = deduped[seen_shot_to_index[p.shot]]
                if on_duplicate is not None:
                    on_duplicate(sku, p.shot, kept.source, p.source)
                continue
            seen_shot_to_index[p.shot] = len(deduped)
            deduped.append(p)

        deduped = _maybe_normalize(sku, deduped, on_normalize)
        groups[sku] = deduped
    return groups


def _maybe_normalize(
    sku: str,
    items: List[ParsedName],
    on_normalize: Optional[Callable[[str, Dict[int, int]], None]],
) -> List[ParsedName]:
    """Renumber non-zero shot indices to start at 1 iff they're contiguous.

    Returns a new list. Shot 0 (the unnumbered safety frame, if present) is
    never renumbered — it always means "the file with no shot suffix."
    """
    non_zero = [p for p in items if p.shot > 0]
    if len(non_zero) < 2:
        return items  # nothing to normalize

    shots = [p.shot for p in non_zero]
    smallest = shots[0]  # items already sorted by shot
    expected = list(range(smallest, smallest + len(shots)))
    if shots != expected:
        # Non-contiguous (gap inside) — preserve original indices so a
        # legitimately missing shot stays missing.
        return items
    if smallest == 1:
        return items  # already canonical

    offset = smallest - 1  # subtract this to shift to start at 1
    mapping: Dict[int, int] = {}
    out: List[ParsedName] = []
    for p in items:
        if p.shot == 0:
            out.append(p)
            continue
        new_shot = p.shot - offset
        mapping[p.shot] = new_shot
        out.append(ParsedName(sku=p.sku, shot=new_shot, source=p.source))

    if on_normalize is not None and mapping:
        on_normalize(sku, mapping)
    return out
