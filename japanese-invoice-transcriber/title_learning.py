"""Feed the title-correction log back into title composition.

Every time the user edits a proposed title, `log_title_correction` appends a
`{computed_title, override_title, brand, product_type, ...}` line to
`title_corrections.jsonl`. This module distils that log into a lookup the
composer can replay, so an item that would produce a title you've already
fixed gets your fix automatically — fewer sparse titles over time.

Safety: only **rich** (>= min_tokens) and **unambiguous** (a single distinct
override) computed titles are auto-replayed. A sparse computed title like
"Chanel Pumps" can legitimately map to many different real items, so replaying
one past override onto a new item would be wrong — those are returned
separately as *hints* (surfaced, never auto-applied).
"""
from __future__ import annotations

import collections
import json
from pathlib import Path


def _load_rows(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _computed_to_overrides(rows: list[dict]) -> dict[str, set[str]]:
    by: dict[str, set[str]] = collections.defaultdict(set)
    for e in rows:
        ct = (e.get("computed_title") or "").strip()
        ov = (e.get("override_title") or "").strip()
        if ct and ov and ct != ov:
            by[ct].add(ov)
    return by


def build_learned_titles(path, *, min_tokens: int = 3) -> dict[str, str]:
    """computed_title -> approved override, for rich unambiguous corrections only."""
    by = _computed_to_overrides(_load_rows(path))
    return {
        ct: next(iter(ovs))
        for ct, ovs in by.items()
        if len(ovs) == 1 and len(ct.split()) >= min_tokens
    }


def corrected_computed_titles(path) -> set[str]:
    """Every computed title that has ever been corrected — for a 'edited before' hint."""
    return set(_computed_to_overrides(_load_rows(path)).keys())
