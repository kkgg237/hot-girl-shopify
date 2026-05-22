"""Shopify Admin API client — fetch live inventory to prevent collisions.

Why this exists: when we generate Shopify CSVs, the auto-generated SKUs and
handles can collide with products already in your shop. SKU collisions cause
import failures; handle collisions silently merge new products as variants.
This module fetches your current inventory before each export, caches it,
and provides a disambiguator that bumps colliding values with `-2`, `-3`, etc.

Architecture:
  - config + token in .env (SHOPIFY_SHOP, SHOPIFY_ADMIN_TOKEN)
  - cache in buyee/state/shopify_inventory.json (gitignored)
  - REST endpoint: GET /admin/api/2024-10/products.json (paginated via Link)
  - one full refresh fetches all products + all variants in a single pass

Usage:
    from shopify_inventory import refresh_inventory, disambiguate
    inv = refresh_inventory()  # uses cache if <24h old
    new_sku = disambiguate("BUR_2603_015", inv.skus | local_used_skus)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).parent
CACHE_PATH = PROJECT_ROOT / "buyee" / "state" / "shopify_inventory.json"
TOKEN_CACHE_PATH = PROJECT_ROOT / "buyee" / "state" / "shopify_token.json"
DEFAULT_API_VERSION = "2024-10"
DEFAULT_TTL_HOURS = 24
TOKEN_REFRESH_BUFFER_HOURS = 1  # refresh token if less than this much life left

# Auto-load .env so SHOPIFY_* vars are available regardless of how invoked
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=True)
except ImportError:
    pass


@dataclass
class ShopifyInventory:
    """Snapshot of handles + SKUs already in the Shopify store."""
    handles: set[str] = field(default_factory=set)
    skus: set[str] = field(default_factory=set)
    fetched_at: Optional[str] = None  # ISO timestamp
    product_count: int = 0
    shop: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_loaded(self) -> bool:
        return self.fetched_at is not None and self.product_count > 0

    @property
    def age_hours(self) -> Optional[float]:
        if not self.fetched_at:
            return None
        try:
            ts = _dt.datetime.fromisoformat(self.fetched_at)
        except ValueError:
            return None
        return (_dt.datetime.now() - ts).total_seconds() / 3600.0

    def humanize_age(self) -> str:
        h = self.age_hours
        if h is None:
            return "never"
        if h < 1:
            return f"{int(h * 60)}m ago"
        if h < 24:
            return f"{int(h)}h ago"
        return f"{int(h / 24)}d ago"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def get_shop() -> Optional[str]:
    """Shop domain (e.g. 'paststudies.myshopify.com'). Strip any protocol."""
    raw = os.environ.get("SHOPIFY_SHOP", "").strip()
    if not raw:
        return None
    return raw.replace("https://", "").replace("http://", "").rstrip("/")


def _client_credentials_configured() -> bool:
    return bool(
        os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
        and os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    )


def _load_cached_token() -> Optional[dict]:
    """Read the persisted token from disk. None if missing/corrupt."""
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cached_token(payload: dict) -> None:
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Don't persist scope — it can vary per call. Keep token + expiry.
    minimal = {
        "access_token": payload.get("access_token"),
        "issued_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "expires_in": payload.get("expires_in"),
        "scope": payload.get("scope"),
    }
    TOKEN_CACHE_PATH.write_text(
        json.dumps(minimal, indent=2) + "\n",
        encoding="utf-8",
    )


def _token_is_still_valid(cache: dict, buffer_hours: float = TOKEN_REFRESH_BUFFER_HOURS) -> bool:
    """True if cached token has more than `buffer_hours` of life left."""
    if not cache or not cache.get("access_token"):
        return False
    try:
        issued = _dt.datetime.fromisoformat(cache["issued_at"])
    except Exception:
        return False
    expires_in = cache.get("expires_in") or 86400
    expires_at = issued + _dt.timedelta(seconds=expires_in)
    remaining = (expires_at - _dt.datetime.now()).total_seconds() / 3600.0
    return remaining > buffer_hours


def get_token() -> Optional[str]:
    """Return a valid Admin API token, refreshing via client_credentials if stale.

    Order of resolution:
      1. Cached token if still fresh (within TTL)
      2. Refresh via client_credentials if both CLIENT_ID + CLIENT_SECRET set
         AND we have a previously-cached client_credentials token (whose
         24h expiry means refresh is the right move) OR the static env token
         is empty/expired-by-known-issuance-time
      3. Static SHOPIFY_ADMIN_TOKEN env var as last resort (Custom App tokens
         don't expire, so this is safe when it's a true Custom App token)
      4. None — caller falls back to "not configured" UX
    """
    cache = _load_cached_token()
    cc_configured = _client_credentials_configured()
    static = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
    static = static if static and not static.startswith("AUTO_") else None

    # 1. Cached + still valid (most common path after first refresh)
    if cache and _token_is_still_valid(cache):
        return cache["access_token"]

    # 2. We have CC credentials → refresh. This covers two cases:
    #    a) The cached token is expired (TTL elapsed) — refresh.
    #    b) The static env token is from a previous CC issuance (also 24h
    #       lifetime), so trusting it indefinitely is wrong. If CLIENT_ID +
    #       SECRET are configured, prefer auto-refresh over the static.
    if cc_configured:
        shop = get_shop()
        if shop:
            try:
                from shopify_oauth import fetch_token_via_client_credentials
                response = fetch_token_via_client_credentials(
                    shop,
                    os.environ["SHOPIFY_CLIENT_ID"].strip(),
                    os.environ["SHOPIFY_CLIENT_SECRET"].strip(),
                )
                if response.get("access_token"):
                    _save_cached_token(response)
                    return response["access_token"]
            except Exception as e:
                print(f"[shopify] client_credentials refresh failed: {e}")
                # Fall through to static / cached

    # 3. Static env token as fallback (true Custom App tokens never expire).
    #    Note: this path is also taken if CC isn't configured at all.
    if static:
        return static

    # 4. Stale cached token > nothing
    if cache and cache.get("access_token"):
        return cache["access_token"]
    return None


def is_configured() -> bool:
    """True if we can produce a token either from env or auto-refresh."""
    return bool(get_shop() and (
        os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
        or _client_credentials_configured()
        or (_load_cached_token() or {}).get("access_token")
    ))


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

def _api_get(url: str, token: str, timeout: int = 30) -> tuple[dict, dict]:
    """GET a Shopify Admin API endpoint. Returns (parsed_json, headers_dict).

    Headers are returned so we can read the Link header for pagination.
    """
    req = urllib.request.Request(url, headers={
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
        "User-Agent": "PastStudies-InvoiceTranscriber/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return json.loads(body), headers


_LINK_RE = re.compile(r'<([^>]+)>; rel="(\w+)"')


def _next_page_url(link_header: str) -> Optional[str]:
    """Parse the Shopify Link header to find the next-page URL, if any."""
    for url, rel in _LINK_RE.findall(link_header or ""):
        if rel == "next":
            return url
    return None


def fetch_inventory_live(
    shop: Optional[str] = None,
    token: Optional[str] = None,
    api_version: str = DEFAULT_API_VERSION,
    rate_limit_s: float = 0.6,
) -> ShopifyInventory:
    """Fetch every handle + every variant SKU from Shopify. Paginated.

    Rate-limited to ~1.6 calls/sec to stay under the 2/sec basic API limit.
    For a 1000-product store: ~4 pages × 250/page = ~2.5 sec total.
    """
    shop = shop or get_shop()
    token = token or get_token()
    if not shop or not token:
        return ShopifyInventory(
            error="SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN must be set in .env"
        )

    inv = ShopifyInventory(shop=shop)
    base_url = (
        f"https://{shop}/admin/api/{api_version}/products.json"
        f"?limit=250&fields=handle,variants"
    )

    url = base_url
    pages = 0
    try:
        while url:
            data, headers = _api_get(url, token)
            pages += 1
            for product in data.get("products", []):
                handle = product.get("handle")
                if handle:
                    inv.handles.add(handle.lower())
                for variant in product.get("variants", []) or []:
                    sku = (variant.get("sku") or "").strip()
                    if sku:
                        inv.skus.add(sku)
                inv.product_count += 1
            url = _next_page_url(headers.get("link", ""))
            if url and rate_limit_s:
                time.sleep(rate_limit_s)
    except urllib.error.HTTPError as e:
        inv.error = f"HTTP {e.code}: {e.reason}. Pages fetched: {pages}."
        if e.code == 401:
            inv.error += " (auth failure — check SHOPIFY_ADMIN_TOKEN)"
        return inv
    except Exception as e:
        inv.error = f"{type(e).__name__}: {e}. Pages fetched: {pages}."
        return inv

    inv.fetched_at = _dt.datetime.now().isoformat(timespec="seconds")
    return inv


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cached_inventory() -> Optional[ShopifyInventory]:
    if not CACHE_PATH.exists():
        return None
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    inv = ShopifyInventory(
        handles=set(raw.get("handles", [])),
        skus=set(raw.get("skus", [])),
        fetched_at=raw.get("fetched_at"),
        product_count=raw.get("product_count", 0),
        shop=raw.get("shop"),
    )
    return inv


def save_inventory_cache(inv: ShopifyInventory) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shop": inv.shop,
        "fetched_at": inv.fetched_at,
        "product_count": inv.product_count,
        "handles": sorted(inv.handles),
        "skus": sorted(inv.skus),
    }
    CACHE_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def refresh_inventory(force: bool = False, ttl_hours: float = DEFAULT_TTL_HOURS) -> ShopifyInventory:
    """Return a usable inventory: cache if fresh enough, else live fetch.

    force=True bypasses the cache.
    Returns an empty inventory (with error field) if not configured or fetch fails.
    """
    if not force:
        cached = load_cached_inventory()
        if cached and cached.is_loaded:
            age = cached.age_hours
            if age is not None and age < ttl_hours:
                return cached

    if not is_configured():
        # Fall back to whatever's cached if nothing else
        cached = load_cached_inventory()
        if cached and cached.is_loaded:
            return cached
        return ShopifyInventory(error="Shopify not configured")

    fresh = fetch_inventory_live()
    if fresh.is_loaded:
        save_inventory_cache(fresh)
    return fresh


# ---------------------------------------------------------------------------
# Collision disambiguation
# ---------------------------------------------------------------------------

def disambiguate(value: str, taken: set[str], max_attempts: int = 999) -> str:
    """If `value` collides with any in `taken`, append `-2`, `-3`... until unique.

    Always returns a string not in `taken`. Adds the result to `taken` so
    repeated calls don't allocate the same disambiguated value twice.
    """
    if value not in taken:
        taken.add(value)
        return value
    for n in range(2, max_attempts + 1):
        candidate = f"{value}-{n}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError(f"Could not disambiguate {value!r} after {max_attempts} attempts")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Shopify inventory tools.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show cache status + connection check")
    sub.add_parser("refresh", help="Force-refresh the cached inventory from Shopify")
    args = parser.parse_args()

    if args.cmd == "status":
        cached = load_cached_inventory()
        print(f"Shop:    {get_shop() or '(not set)'}")
        print(f"Token:   {'set' if get_token() else 'NOT SET'}")
        if cached and cached.is_loaded:
            print(f"Cache:   {cached.product_count} products, "
                  f"{len(cached.skus)} SKUs, {len(cached.handles)} handles "
                  f"(last fetched {cached.humanize_age()})")
        else:
            print("Cache:   empty")
        return 0

    if args.cmd == "refresh":
        if not is_configured():
            print("✗ SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN must be set in .env")
            return 1
        print(f"Fetching {get_shop()}...")
        inv = fetch_inventory_live()
        if inv.error:
            print(f"✗ {inv.error}")
            return 1
        save_inventory_cache(inv)
        print(f"✓ {inv.product_count} products, {len(inv.skus)} SKUs, "
              f"{len(inv.handles)} handles cached.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
