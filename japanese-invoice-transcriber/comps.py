"""Market comp research — find what similar items have actually sold for.

Why this exists: the auto-pricing in `pricing.py` is rules-based (cost +
demand × brand tier + bracket rounding). It's deterministic but blind to
market reality — a Margiela Tabi in mint condition will fetch 3× our
rules-based ceiling, and a generic 90s jacket will sit at our floor for
months. Comp research adds a SECOND signal: actual sold prices for similar
items on Grailed / eBay / Vestiaire / Mercari, so the user can spot items
where our auto-price is materially off the market.

Architecture (mirrors `buyee/research.py`):
  - Per-item LLM call with web_search tool (Haiku 4.5 — cheapest)
  - Strict JSON output: low_usd / median_usd / high_usd / num_comps / sources
  - Cache per-invoice sidecar at `output/<stem>.comps.json` keyed by source_id
  - Re-research only if `force=True`
  - Hard cost ceiling per batch (default $5)

Cost expectations:
  - ~$0.02-0.04 per item (Haiku + 2-3 web_search calls)
  - 30-item invoice: ~$0.60-1.20 once, cached forever after
  - The comp sidecar is excluded from snapshot tests (see test_snapshots.py)

What this is NOT:
  - This module does NOT auto-rewrite prices. It surfaces signal; the user
    decides whether to override a price based on comps. Auto-overwriting
    prices from LLM-extracted comp data would be unsafe — the model can
    hallucinate, and comp distributions are too noisy to act on blindly.
"""
from __future__ import annotations

import json
import re
import statistics as _stats
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, field_validator

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=True)
except ImportError:
    pass


PROJECT_ROOT = Path(__file__).parent


# Haiku 4.5 pricing (per 1M tokens). Mirrors buyee/research.py constants.
HAIKU_INPUT_PRICE = 1.0
HAIKU_OUTPUT_PRICE = 5.0
SEARCH_PRICE_PER_USE = 0.01


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

class CompResult(BaseModel):
    """One item's market comp research — what similar items have sold for.

    All price fields are USD-denominated. The model is expected to convert
    from JPY/EUR/etc. when it cites foreign-market comps.
    """
    # The actual signal
    low_usd: Optional[float] = Field(default=None, description="Lowest comp seen")
    median_usd: Optional[float] = Field(default=None, description="Median of all comps")
    high_usd: Optional[float] = Field(default=None, description="Highest comp seen")
    num_comps: int = Field(default=0, description="How many distinct sold listings the model found")

    # Where it came from
    sources: list[str] = Field(default_factory=list, description="URLs cited (max 5)")
    markets: list[str] = Field(default_factory=list,
                               description="e.g. ['Grailed', 'eBay sold', 'Mercari JP']")
    confidence: Optional[str] = Field(default=None, description="HIGH / MED / LOW")
    notes: Optional[str] = Field(default=None, description="Caveats — small sample, condition variance, etc.")

    # Metadata
    query_text: Optional[str] = Field(default=None, description="The search seed used (for debugging)")
    method: str = "web_search"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    researched_at: Optional[str] = None  # ISO timestamp

    @field_validator("low_usd", "median_usd", "high_usd", mode="before")
    @classmethod
    def _coerce_price(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            # Strip currency symbols / commas
            s = re.sub(r"[^\d.]", "", v)
            try:
                return float(s) if s else None
            except ValueError:
                return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

COMPS_SYSTEM = """You are researching market comp prices for a vintage resale shop.

For each item, search the open web for SIMILAR items that have ACTUALLY SOLD recently (last 12-24 months). Prefer SOLD listings over active asks. The goal is to find what the item is realistically worth, NOT the asking-price ceiling.

Markets to prioritize, in order:
1. Grailed sold listings (grailed.com) — best signal for US vintage menswear
2. eBay sold listings (ebay.com → "sold items" filter) — broadest sample
3. Vestiaire Collective (vestiairecollective.com) — mid-luxury authenticated resale
4. The RealReal (therealreal.com) — luxury, condition-graded
5. Mercari Japan (mercari.com/jp) — the source market, JPY prices need conversion
6. Yahoo Auctions Japan — ditto, JPY

Skip:
- Active/asking-price listings unless no sold data exists at all
- "Make an offer" listings without a recorded sale price
- Wholesale lots, "as-is" damaged items unless our item is also damaged
- Replicas, knock-offs, "inspired by" pieces

CRITICAL RULES:
1. Convert all prices to USD. Use ~150 JPY/USD, ~1.08 EUR/USD, ~1.27 GBP/USD as rough rates.
2. Only count comps that match BRAND + TYPE + (era OR model) at minimum. A 90s Burberry trench is NOT a comp for a 2010s Burberry trench.
3. Condition matters massively. If our item is described as having damage, weight comps toward similar-condition sold listings.
4. If you find fewer than 3 valid comps, mark confidence as LOW and report what you have. Don't pad with weak matches.
5. If you find ZERO valid comps after 2-3 searches, return all null prices with notes explaining why (too obscure, too new, etc.).

Return strict JSON:
{
  "low_usd": float | null,
  "median_usd": float | null,
  "high_usd": float | null,
  "num_comps": int,
  "sources": [url, url, ...],  // max 5 most relevant
  "markets": [str, str, ...],   // which markets you cited (e.g. ["Grailed", "eBay sold"])
  "confidence": "HIGH" | "MED" | "LOW",
  "notes": str | null
}

NO markdown fence. NO prose outside the JSON object."""


def _build_comp_query(item, invoice_date: Optional[str] = None) -> tuple[str, str]:
    """Return (search_seed, full_prompt) for an item.

    search_seed is the human-readable query text we'll persist for debugging.
    full_prompt is what we send to the model.

    Field-priority: when the user has set `override_title` (manually curated
    title that's typically the richest single signal — brand + era + model +
    color + material + type all in one), we use that as the seed. Otherwise
    we synthesize from the structured fields.
    """
    override_title = getattr(item, "override_title", None)
    if override_title and override_title.strip():
        seed = override_title.strip()
    else:
        brand = item.detected_brand or "(unknown brand)"
        ptype = item.product_type or "(unknown type)"
        parts = [brand, ptype]
        if getattr(item, "model_name", None):
            parts.append(item.model_name)
        if getattr(item, "model_size", None):
            parts.append(item.model_size)
        if getattr(item, "era", None):
            parts.append(item.era)
        if getattr(item, "color", None):
            parts.append(item.color)
        if getattr(item, "pattern", None):
            parts.append(item.pattern)
        if getattr(item, "material", None):
            parts.append(item.material)
        seed = " ".join(p for p in parts if p)

    extras = []
    if getattr(item, "condition_notes", None):
        extras.append(f"Condition: {item.condition_notes}")
    if getattr(item, "description_english", None):
        extras.append(f"Source desc: {item.description_english[:200]}")
    extras_str = "\n".join(extras) if extras else "(none)"

    prompt = f"""Research recent SOLD comps for this vintage fashion item.

Search seed: {seed}
{extras_str}

Find 3-5 sold listings of the SAME or VERY SIMILAR items (same brand, same type, same era/model). Note their final sold prices. Convert to USD if foreign-market.

Compute low / median / high USD across the comps you found. Cite source URLs.

Return ONLY the JSON object specified in the system prompt."""
    return seed, prompt


# ---------------------------------------------------------------------------
# Single-item research
# ---------------------------------------------------------------------------

def research_comps_for_item(
    item,
    client: Optional[anthropic.Anthropic] = None,
    max_searches: int = 3,
    model: str = "claude-haiku-4-5",
    invoice_date: Optional[str] = None,
) -> CompResult:
    """One LLM call with web_search enabled. Returns price-comp signal.

    Costs ~$0.02-0.04/item. Does NOT cache — caller manages the sidecar.
    """
    import datetime as _dt

    client = client or anthropic.Anthropic(timeout=180.0, max_retries=2)
    seed, prompt = _build_comp_query(item, invoice_date=invoice_date)

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=COMPS_SYSTEM,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_searches,
            }],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        return CompResult(
            query_text=seed,
            notes=f"API error: {e}",
            researched_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )

    # Pull text blocks (web_search responses interleave tool_use + text)
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "\n".join(text_blocks).strip()

    # Strip markdown fences if present
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    search_uses = sum(
        1 for b in resp.content if getattr(b, "type", "") == "server_tool_use"
    )
    cost = (in_tok * HAIKU_INPUT_PRICE / 1_000_000
            + out_tok * HAIKU_OUTPUT_PRICE / 1_000_000
            + search_uses * SEARCH_PRICE_PER_USE)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return CompResult(
            query_text=seed,
            notes=f"JSON parse failed. Raw: {raw[:300]}",
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=round(cost, 4),
            researched_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )

    try:
        result = CompResult(
            **{k: v for k, v in data.items() if k in CompResult.model_fields},
            query_text=seed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 4),
            researched_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as e:
        return CompResult(
            query_text=seed,
            notes=f"Schema validation failed: {e}. Raw: {raw[:300]}",
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=round(cost, 4),
            researched_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )

    # Sanity-check: low <= median <= high. If violated, sort.
    prices = [p for p in (result.low_usd, result.median_usd, result.high_usd)
              if p is not None]
    if len(prices) >= 2:
        prices_sorted = sorted(prices)
        if result.low_usd is not None:
            result.low_usd = prices_sorted[0]
        if result.high_usd is not None:
            result.high_usd = prices_sorted[-1]
        if result.median_usd is not None and len(prices_sorted) == 3:
            result.median_usd = prices_sorted[1]
        elif result.median_usd is None and len(prices_sorted) >= 2:
            # Compute a median if model didn't return one
            result.median_usd = _stats.median(prices_sorted)

    return result


# ---------------------------------------------------------------------------
# Sidecar persistence
# ---------------------------------------------------------------------------

def _comps_path(invoice_path: Path) -> Path:
    """Sidecar path. Strips `edited_` prefix so original and edited share state."""
    stem = invoice_path.stem
    if stem.startswith("edited_"):
        stem = stem[len("edited_"):]
    return invoice_path.parent / f"{stem}.comps.json"


def load_comps(invoice_path: Path) -> dict[str, CompResult]:
    """Return {source_id: CompResult} from the sidecar, or empty dict."""
    p = _comps_path(invoice_path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, CompResult] = {}
    for sid, payload in raw.items():
        try:
            out[sid] = CompResult(**payload)
        except Exception:
            continue  # skip corrupted entries
    return out


def save_comps(invoice_path: Path, comps: dict[str, CompResult]) -> None:
    p = _comps_path(invoice_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        sid: result.model_dump(exclude_none=False)
        for sid, result in comps.items()
    }
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Batch orchestrator — walk an invoice's items
# ---------------------------------------------------------------------------

def enrich_invoice_with_comps(
    invoice_path: Path,
    only_ids: Optional[list[str]] = None,
    force: bool = False,
    max_cost_usd: float = 5.0,
    progress_cb=None,
) -> dict:
    """Research comps for every item in the invoice. Sidecar-cached.

    Args:
        invoice_path: path to the invoice JSON (original or edited; sidecar
            is shared via _comps_path).
        only_ids: limit to these source_ids (default: all).
        force: re-research items even if cached.
        max_cost_usd: hard ceiling — stop once exceeded.
        progress_cb: callable(idx, total, source_id, status, cost_so_far) for
            UI progress updates. Optional.

    Returns:
        stats {items_processed, items_researched, items_cached, items_failed,
               total_cost_usd, hit_cost_ceiling, log}
    """
    from costs import Invoice

    data = json.loads(invoice_path.read_text(encoding="utf-8"))
    inv = Invoice(**{k: v for k, v in data.items() if not k.startswith("_")})

    existing = load_comps(invoice_path)
    client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    invoice_date = data.get("invoice_date")

    items = [it for it in inv.items if it.source_id]
    if only_ids:
        only_set = set(only_ids)
        items = [it for it in items if it.source_id in only_set]

    stats = {
        "items_processed": 0,
        "items_researched": 0,
        "items_cached": 0,
        "items_failed": 0,
        "total_cost_usd": 0.0,
        "hit_cost_ceiling": False,
        "log": [],  # [{source_id, status, cost, note}]
    }

    for idx, item in enumerate(items):
        sid = item.source_id
        stats["items_processed"] += 1

        if not force and sid in existing:
            stats["items_cached"] += 1
            stats["log"].append({"source_id": sid, "status": "cached", "cost": 0})
            if progress_cb:
                progress_cb(idx + 1, len(items), sid, "cached", stats["total_cost_usd"])
            continue

        if stats["total_cost_usd"] >= max_cost_usd:
            stats["hit_cost_ceiling"] = True
            stats["log"].append({
                "source_id": sid, "status": "skipped_cost_ceiling", "cost": 0,
                "note": f"hit ${max_cost_usd:.2f} ceiling",
            })
            if progress_cb:
                progress_cb(idx + 1, len(items), sid, "skipped", stats["total_cost_usd"])
            continue

        try:
            result = research_comps_for_item(
                item, client=client, invoice_date=invoice_date,
            )
        except Exception as e:
            stats["items_failed"] += 1
            stats["log"].append({
                "source_id": sid, "status": "failed", "cost": 0,
                "note": f"{type(e).__name__}: {e}",
            })
            if progress_cb:
                progress_cb(idx + 1, len(items), sid, "failed", stats["total_cost_usd"])
            continue

        existing[sid] = result
        # Persist after every item so an interrupted batch doesn't lose progress
        save_comps(invoice_path, existing)

        if result.cost_usd:
            stats["total_cost_usd"] += result.cost_usd
        if result.notes and "error" in (result.notes or "").lower():
            stats["items_failed"] += 1
            stats["log"].append({
                "source_id": sid, "status": "error",
                "cost": result.cost_usd, "note": result.notes[:120],
            })
        else:
            stats["items_researched"] += 1
            stats["log"].append({
                "source_id": sid,
                "status": "researched",
                "cost": result.cost_usd,
                "note": f"n={result.num_comps}, median=${result.median_usd or 0:.0f}",
            })

        if progress_cb:
            progress_cb(idx + 1, len(items), sid, "researched", stats["total_cost_usd"])

    return stats


# ---------------------------------------------------------------------------
# Helpers for UI: compare auto-price to comp median
# ---------------------------------------------------------------------------

def auto_price_delta(auto_price_usd: float, comp: CompResult) -> Optional[dict]:
    """Return delta-info comparing the auto-price to the comp median.

    Returns None if we don't have a usable comp median.

    Returned dict:
      {
        "median_usd": float,
        "delta_usd": float,        # auto - median (positive = auto is higher)
        "delta_pct": float,        # (auto - median) / median
        "verdict": "fair" | "low" | "high",  # within ±25% = fair
      }
    """
    if not comp or comp.median_usd is None or comp.median_usd <= 0:
        return None
    if not auto_price_usd or auto_price_usd <= 0:
        return None

    delta = auto_price_usd - comp.median_usd
    pct = delta / comp.median_usd

    if abs(pct) <= 0.25:
        verdict = "fair"
    elif pct < 0:
        verdict = "low"  # auto-price is below market — we're leaving money on the table
    else:
        verdict = "high"  # auto-price is above market — might not sell

    return {
        "median_usd": comp.median_usd,
        "delta_usd": delta,
        "delta_pct": pct,
        "verdict": verdict,
    }
