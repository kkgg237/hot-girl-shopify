"""Second-pass LLM enrichment — target weak fields per spec suggestion #5.

After the primary transcribe pass (Opus, expensive), we run a cheap Haiku call
to backfill `color`, `pattern`, `era`, and `model_name` on items where those
fields are null. The primary LLM is focused on structural extraction (all four
Buyee tables, every fee row); Haiku is a text-only pass over the item
descriptions. Splitting the work keeps cost reasonable and coverage high.

Typical cost: ~$0.005 per item on Haiku for a 30-item invoice. Roughly $0.15
per invoice added — worth it for the SEO lift from model_name coverage.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import anthropic


HAIKU_MODEL = "claude-haiku-4-5"
MAX_RETRIES = 2


BACKFILL_SYSTEM = """You are extracting structured attributes from vintage fashion item descriptions.
Return strict JSON with only these fields per item, filling what you can determine and
leaving anything uncertain as null. DO NOT guess. DO NOT invent.

Fields:
- color: ONE canonical primary color ('Black', 'White', 'Red', 'Blue', 'Green',
  'Brown', 'Beige', 'Grey', 'Pink', 'Purple', 'Yellow', 'Orange', 'Silver',
  'Gold', 'Burgundy', 'Navy'). Use 'Multicolor' if 3+ colors are present.
- pattern: signature pattern name — 'Monogram', 'Damier', 'Matelasse', 'Zucca',
  'Nova Check', 'GG Canvas', 'Sherry Line', 'Intrecciato', 'Tortoise', 'Floral',
  'Striped', 'Plaid'. Null if plain.
- era: year ('1997') or decade ("90's", "00's", 'Y2K'). Null if not determinable.
- model_name: luxury model — 'Speedy', 'Neverfull', 'Pochette Accessoires',
  'Classic Flap', 'Mamma Baguette', 'Birkin', 'Jackie', etc. Null if generic.
- model_size: 'MM', 'PM', '25', '30', '35'. Null if not stated.
- style_adjectives: ordered space-separated descriptors from the translation
  — e.g. 'Belted V-Neck Long Sleeve Mesh' or 'Cache-Coeur Corsage'. One match
  per bucket: silhouette, neckline, sleeve, fabric-detail. Null if none apply."""


BACKFILL_PROMPT = """For each item below, return a JSON array of objects, one per item,
keyed by source_id. Fill only fields that are CLEARLY present in the description.
Return ONLY the JSON array — no prose, no markdown fence.

Items:
{items_json}"""


def _collect_candidates(invoice) -> list[dict]:
    """Return items with at least one weak field, as dicts the Haiku prompt expects."""
    cands = []
    for item in invoice.items:
        weak_fields = [
            "color", "pattern", "era", "model_name", "model_size", "style_adjectives"
        ]
        missing = [f for f in weak_fields if not getattr(item, f, None)]
        if missing:
            cands.append({
                "source_id": item.source_id,
                "description": (item.description_original or ""),
                "description_english": (item.description_english or ""),
                "detected_brand": item.detected_brand,
                "product_type": item.product_type,
                "missing_fields": missing,
            })
    return cands


def _apply_backfill(invoice, backfill: list[dict]) -> dict:
    """Merge Haiku's response onto the invoice. Returns stats."""
    by_sid = {b.get("source_id"): b for b in backfill if b.get("source_id")}
    stats = {"color": 0, "pattern": 0, "era": 0, "model_name": 0, "model_size": 0, "style_adjectives": 0}
    for item in invoice.items:
        b = by_sid.get(item.source_id)
        if not b:
            continue
        for field in stats.keys():
            if getattr(item, field, None):
                continue  # don't overwrite existing
            val = b.get(field)
            if val and isinstance(val, str) and val.strip().lower() not in ("null", "none", ""):
                setattr(item, field, val.strip())
                stats[field] += 1
    return stats


def backfill_via_haiku(invoice, client: Optional[anthropic.Anthropic] = None) -> dict:
    """Run the Haiku backfill pass. Mutates `invoice.items` in place. Returns stats."""
    cands = _collect_candidates(invoice)
    if not cands:
        return {"candidates": 0}

    client = client or anthropic.Anthropic(timeout=60.0, max_retries=MAX_RETRIES)

    prompt = BACKFILL_PROMPT.format(items_json=json.dumps(cands, ensure_ascii=False, indent=2))
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=8000,
            system=BACKFILL_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        return {"candidates": len(cands), "error": str(e)}

    text = next((b.text for b in resp.content if b.type == "text"), "")
    # Strip any code fences defensively
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text.strip(), re.DOTALL)
    if m:
        text = m.group(1)

    try:
        backfill = json.loads(text)
    except json.JSONDecodeError as e:
        return {"candidates": len(cands), "parse_error": str(e), "raw": text[:300]}

    if not isinstance(backfill, list):
        return {"candidates": len(cands), "error": "expected array"}

    stats = _apply_backfill(invoice, backfill)
    return {"candidates": len(cands), **stats}
