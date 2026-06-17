"""Shopify Admin API auth — token resolution via OAuth client_credentials.

COPIED (not imported) from japanese-invoice-transcriber's shopify_inventory.py +
shopify_oauth.py, which are stdlib-only. The only changes:
  - ``get_shop`` also accepts ``SHOPIFY_STORE_DOMAIN`` (the shop-photo-editor name).
  - ``TOKEN_CACHE_PATH`` points at ``ecomm-pipeline/state/`` so this pipeline's
    token cache never collides with the transcriber's.
  - ``fetch_token_via_client_credentials`` is inlined here (no cross-import).

Resolution order in ``get_token``: fresh cached token → client_credentials
refresh (the live path, since the repo-root .env carries the OAuth pair) →
static ``SHOPIFY_ADMIN_TOKEN`` (if someone later adds a Custom App token) →
stale cached token → None.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:  # pragma: no cover
    pass

# ecomm_pipeline/shopify/auth.py → parents[2] = ecomm-pipeline/
STATE_DIR = Path(__file__).resolve().parents[2] / "state"
TOKEN_CACHE_PATH = STATE_DIR / "shopify_token.json"
TOKEN_REFRESH_BUFFER_HOURS = 1.0


def normalize_shop(shop: str) -> str:
    """Strip protocol/trailing slash. Don't add .myshopify.com — caller owns that."""
    return shop.replace("https://", "").replace("http://", "").rstrip("/")


def get_shop() -> Optional[str]:
    """Shop domain (e.g. 'paststudies.myshopify.com'), or None if unset."""
    raw = (
        os.environ.get("SHOPIFY_SHOP")
        or os.environ.get("SHOPIFY_STORE_DOMAIN")
        or ""
    ).strip()
    return normalize_shop(raw) if raw else None


def fetch_token_via_client_credentials(
    shop: str, client_id: str, client_secret: str
) -> dict:
    """Exchange Client ID + Secret for an Admin API token (24h lifetime).

    Returns the parsed response, e.g.
        {"access_token": "shpat_...", "scope": "...", "expires_in": 86400}
    Raises urllib.error.HTTPError on 401/403 (wrong secret, app not installed,
    or scopes not granted).
    """
    shop = normalize_shop(shop)
    url = f"https://{shop}/admin/oauth/access_token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _client_credentials_configured() -> bool:
    return bool(
        os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
        and os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    )


def _load_cached_token() -> Optional[dict]:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cached_token(payload: dict) -> None:
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    minimal = {
        "access_token": payload.get("access_token"),
        "issued_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "expires_in": payload.get("expires_in"),
        "scope": payload.get("scope"),
    }
    TOKEN_CACHE_PATH.write_text(json.dumps(minimal, indent=2) + "\n", encoding="utf-8")


def _token_is_still_valid(cache: dict, buffer_hours: float = TOKEN_REFRESH_BUFFER_HOURS) -> bool:
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
    """Return a valid Admin API token, refreshing via client_credentials if stale."""
    cache = _load_cached_token()
    static = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
    static = static if static and not static.startswith("AUTO_") else None

    # 1. Fresh cached token (most common path after the first refresh).
    if cache and _token_is_still_valid(cache):
        return cache["access_token"]

    # 2. Refresh via client_credentials — the live path for this repo.
    if _client_credentials_configured():
        shop = get_shop()
        if shop:
            try:
                response = fetch_token_via_client_credentials(
                    shop,
                    os.environ["SHOPIFY_CLIENT_ID"].strip(),
                    os.environ["SHOPIFY_CLIENT_SECRET"].strip(),
                )
                if response.get("access_token"):
                    _save_cached_token(response)
                    return response["access_token"]
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:200]
                except Exception:
                    pass
                print(f"[shopify] client_credentials refresh failed: HTTP {e.code} {body}")
            except Exception as e:  # noqa: BLE001 — fall through to other paths
                print(f"[shopify] client_credentials refresh failed: {e}")

    # 3. Static Custom App token, if one was added later.
    if static:
        return static

    # 4. Stale cached token beats nothing.
    if cache and cache.get("access_token"):
        return cache["access_token"]
    return None


def is_configured() -> bool:
    """True if a token can be produced from env, cache, or client_credentials."""
    return bool(
        get_shop()
        and (
            os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
            or _client_credentials_configured()
            or (_load_cached_token() or {}).get("access_token")
        )
    )
