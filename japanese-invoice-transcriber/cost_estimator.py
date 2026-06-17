"""Estimate item cost from past invoice data.

Reads every invoice JSON in `output/` and indexes (vendor → list of USD
costs). Given a new item with no cost, returns the average cost of past
items with the same vendor. Falls back to None when there's no match.

This lets the CSV-ingest path backfill missing `Cost per Item` values
without needing to query Shopify (the inventory cache doesn't include
unit cost — that lives behind the Shopify inventory_items API).

API:
    estimate_cost(title, vendor)  -> Optional[float]   (USD)
    build_index()                 -> dict[str, list[float]]
    summary()                     -> dict[str, dict]   (debug)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# JPY → USD fallback when an invoice has no exchange_rate set. Matches the
# default in costs.DEFAULT_EXCHANGE_RATE (~149 JPY/USD).
_DEFAULT_JPY_USD = 0.0067


HERE = Path(__file__).parent
OUTPUT_DIR = HERE / "output"

# Where the Shopify-catalog cost index lives. Same gitignored area as the
# other Shopify state (tokens, inventory cache). Cached as a flat list of
# {vendor, title, cost_usd, product_type} dicts.
SHOPIFY_COST_CACHE = HERE / "buyee" / "state" / "shopify_costs.json"


def _normalize_vendor(s: str) -> str:
    """Vendor key for cross-spelling matching. Lowercases, collapses
    whitespace, swaps " and " <-> " & " so "Dolce and Gabbana" and
    "Dolce & Gabbana" hit the same bucket."""
    import re as _re
    s = (s or "").strip().lower()
    # Normalize " and " variations to " & " so both spellings collide
    s = _re.sub(r"\s+and\s+", " & ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _to_usd(amount: float, currency: str, rate: Optional[float]) -> Optional[float]:
    """Best-effort conversion to USD. Returns None if we can't get there."""
    if amount is None or amount <= 0:
        return None
    ccy = (currency or "USD").upper()
    if ccy == "USD":
        return amount
    if ccy == "JPY":
        r = rate if (rate and rate > 0) else _DEFAULT_JPY_USD
        return amount * r
    # Other currencies — fall back to the invoice's exchange_rate as-is
    if rate and rate > 0:
        return amount * rate
    return None


def _iter_invoice_items():
    """Yield (vendor_lower, title, cost_usd) for every priced item in
    `output/*.json`. Skips sidecar `.shopify_pushed.json` files."""
    if not OUTPUT_DIR.exists():
        return
    for path in OUTPUT_DIR.glob("*.json"):
        if path.name.endswith(".shopify_pushed.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data.get("items") or []
        invoice_rate = data.get("exchange_rate")
        invoice_currency = (data.get("currency") or "").upper()
        invoice_vendor = (data.get("vendor_name") or "").strip()
        for item in items:
            # Use the item's own currency if present, else the invoice's
            currency = (item.get("currency") or invoice_currency or "USD").upper()
            amount = item.get("item_price")
            cost_usd = _to_usd(amount, currency, invoice_rate)
            if not cost_usd:
                continue
            vendor = (item.get("detected_brand") or invoice_vendor or "").strip()
            title = (item.get("description_english")
                     or item.get("description_original") or "").strip()
            if not vendor or not title:
                continue
            yield _normalize_vendor(vendor), title, cost_usd


def _iter_shopify_cost_index():
    """Yield (vendor_normalized, title, cost_usd) for every entry in the
    cached Shopify cost index. Empty if no cache."""
    if not SHOPIFY_COST_CACHE.exists():
        return
    try:
        data = json.loads(SHOPIFY_COST_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for entry in data:
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost_usd")
        if not cost or cost <= 0:
            continue
        vendor = (entry.get("vendor") or "").strip()
        title = (entry.get("title") or "").strip()
        if not vendor or not title:
            continue
        yield _normalize_vendor(vendor), title, float(cost)


def build_index() -> dict[str, list[float]]:
    """Build {vendor_lower: [cost_usd, ...]} from BOTH past invoices and the
    cached Shopify catalog cost data. Past invoices are typically tiny and
    luxury-heavy; the Shopify catalog (when fetched via
    `fetch_shopify_costs`) gives much broader coverage including Vintage."""
    out: dict[str, list[float]] = {}
    for vendor_lower, _title, cost in _iter_invoice_items():
        out.setdefault(vendor_lower, []).append(cost)
    for vendor_lower, _title, cost in _iter_shopify_cost_index():
        out.setdefault(vendor_lower, []).append(cost)
    return out


# ---------------------------------------------------------------------------
# Shopify catalog cost fetcher
# ---------------------------------------------------------------------------

def fetch_shopify_costs(page_size: int = 100) -> tuple[int, str]:
    """Pull every product's per-unit cost from Shopify via GraphQL and cache
    locally. Cost lives on InventoryItem.unitCost (not on Product directly),
    one level below Variant.

    Returns (count, message). Cache is written atomically.
    """
    try:
        from shopify_inventory import get_shop, get_token
    except ImportError:
        return 0, "shopify_inventory not importable"
    from shopify_push import DEFAULT_API_VERSION  # reuse the pinned version
    import urllib.error
    import urllib.request
    import time as _time

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, "Shopify not configured"

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    QUERY = """
    query Costs($cursor: String) {
      products(first: 100, after: $cursor) {
        edges {
          cursor
          node {
            id
            title
            vendor
            productType
            tags
            variants(first: 10) {
              edges {
                node {
                  inventoryItem {
                    unitCost { amount currencyCode }
                  }
                }
              }
            }
          }
        }
        pageInfo { hasNextPage }
      }
    }
    """

    entries: list[dict] = []
    cursor = None
    page = 0
    while True:
        page += 1
        payload = json.dumps({"query": QUERY, "variables": {"cursor": cursor}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            return len(entries), f"HTTP {e.code} on page {page}: {body}"
        except urllib.error.URLError as e:
            return len(entries), f"Network error on page {page}: {e.reason}"
        if data.get("errors"):
            return len(entries), f"GraphQL errors on page {page}: {data['errors']}"

        conn = (data.get("data") or {}).get("products") or {}
        edges = conn.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            # Take the first variant's cost (most products are 1-of-1 vintage,
            # variants beyond the first are rare in this catalog).
            variants = ((node.get("variants") or {}).get("edges") or [])
            cost = None
            for ve in variants:
                inv_item = ((ve.get("node") or {}).get("inventoryItem") or {})
                unit_cost = inv_item.get("unitCost")
                if unit_cost and unit_cost.get("amount") is not None:
                    try:
                        amt = float(unit_cost["amount"])
                    except (TypeError, ValueError):
                        amt = 0.0
                    if amt > 0:
                        cost = amt
                        break
            if not cost:
                continue
            entries.append({
                "title": node.get("title") or "",
                "vendor": (node.get("vendor") or "").strip(),
                "product_type": (node.get("productType") or "").strip(),
                "tags": node.get("tags") or [],
                "cost_usd": cost,
            })
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not edges:
            break
        cursor = edges[-1].get("cursor")
        _time.sleep(0.1)

    SHOPIFY_COST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHOPIFY_COST_CACHE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    tmp.replace(SHOPIFY_COST_CACHE)
    return len(entries), f"Cached {len(entries)} products with cost data."


def is_shopify_cost_cached() -> bool:
    return SHOPIFY_COST_CACHE.exists() and SHOPIFY_COST_CACHE.stat().st_size > 0


def shopify_cost_cache_age_seconds():
    import time as _time
    if not is_shopify_cost_cached():
        return None
    return _time.time() - SHOPIFY_COST_CACHE.stat().st_mtime


def estimate_cost(
    title: str,
    vendor: str,
    index: Optional[dict[str, list[float]]] = None,
) -> Optional[float]:
    """Average USD cost of past items with the same vendor.

    Returns None if no match. The title argument is currently unused but
    accepted so callers can pass it — future versions may add keyword-
    based similarity on top of vendor matching.
    """
    target = _normalize_vendor(vendor)
    if not target:
        return None
    if index is None:
        index = build_index()
    matches = index.get(target)
    if not matches:
        return None
    return sum(matches) / len(matches)


def summary() -> dict[str, dict]:
    """Debug: per-vendor count + average + min/max."""
    idx = build_index()
    return {
        v: {
            "n": len(costs),
            "avg": sum(costs) / len(costs),
            "min": min(costs),
            "max": max(costs),
        }
        for v, costs in sorted(idx.items())
    }
