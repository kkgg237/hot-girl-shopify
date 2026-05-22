#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["python-dotenv>=1.0"]
# ///
"""Obtain Shopify Admin API access tokens using Client ID + Client Secret.

Two flows supported:

1. **Client Credentials grant** (default, recommended)
   Per https://shopify.dev/docs/apps/build/authentication-authorization/client-secrets
   Single POST to /admin/oauth/access_token with grant_type=client_credentials.
   No browser, no redirect URI. Token expires after 24 hours so we auto-refresh.

2. **Authorization Code grant** (fallback)
   The traditional 3-legged OAuth flow with browser redirect to a local
   callback server. Use only if your app type requires it.

The token cache lives at buyee/state/shopify_token.json and is refreshed
automatically by shopify_inventory.get_token() when stale.

Usage:
    uv run shopify_oauth.py                  # interactive setup
    uv run shopify_oauth.py --redirect       # use OAuth redirect flow instead
"""
from __future__ import annotations

import http.server
import json
import os
import secrets as _secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional


CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
DEFAULT_SCOPES = ["read_products"]


# ---------------------------------------------------------------------------
# Client Credentials grant — simple, no browser
# ---------------------------------------------------------------------------

def normalize_shop(shop: str) -> str:
    """Strip protocol/trailing slash. Don't add .myshopify.com — let user own that."""
    return shop.replace("https://", "").replace("http://", "").rstrip("/")


def fetch_token_via_client_credentials(
    shop: str, client_id: str, client_secret: str,
) -> dict:
    """Exchange Client ID + Client Secret for an Admin API token.

    Returns the parsed response dict, e.g.
        {"access_token": "shpat_...", "scope": "read_products", "expires_in": 86400}

    Raises urllib.error.HTTPError on auth failure (401/403). Caller should
    surface the error message clearly — common causes:
      - Wrong client_secret
      - App not installed on this shop
      - Scopes mismatch with what's configured in app settings
    """
    shop = normalize_shop(shop)
    url = f"https://{shop}/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# OAuth redirect flow (fallback) — kept for apps where Client Credentials
# isn't an option. Same code as before, refactored slightly.
# ---------------------------------------------------------------------------


# Mutable container for the callback handler to write into. Module-level so
# the BaseHTTPRequestHandler subclass (which Python instantiates per-request)
# can hand state back to the caller.
_RESULT: dict = {}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot HTTP handler that captures the OAuth `code` query param."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not found.")
            return
        params = urllib.parse.parse_qs(parsed.query)
        _RESULT.update({
            "code": (params.get("code") or [""])[0],
            "state": (params.get("state") or [""])[0],
            "shop": (params.get("shop") or [""])[0],
            "hmac": (params.get("hmac") or [""])[0],
        })
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<!doctype html><html><body style=\"font-family:-apple-system,"
            "sans-serif;text-align:center;padding:4em;color:#111;\">"
            "<h2>Authorization received.</h2>"
            "<p>Token exchange happening in your terminal -- you can close "
            "this window.</p></body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # silence default per-request logging


def obtain_admin_token(
    shop: str,
    client_id: str,
    client_secret: str,
    scopes: Optional[list[str]] = None,
    port: int = CALLBACK_PORT,
    timeout_s: int = 300,
) -> Optional[str]:
    """Run the full OAuth flow and return the access_token.

    Returns None if the flow was aborted, timed out, or token exchange failed.
    Writes diagnostic info to stdout throughout.
    """
    if not (shop and client_id and client_secret):
        raise ValueError("shop, client_id, and client_secret all required")
    scopes = scopes or DEFAULT_SCOPES

    # Normalize shop domain
    shop = shop.replace("https://", "").replace("http://", "").rstrip("/")
    if not shop.endswith(".myshopify.com"):
        print(f"  ⚠ Shop should end in .myshopify.com (got {shop!r})")

    state = _secrets.token_urlsafe(32)
    redirect_uri = f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}"
    auth_url = (
        f"https://{shop}/admin/oauth/authorize?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "scope": ",".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        })
    )

    print()
    print(f"  Redirect URI: {redirect_uri}")
    print(f"  ⓘ This URL must be in your Shopify app's 'Allowed redirection URLs'.")
    print()
    print(f"  Opening browser...")
    print(f"  (If it doesn't open, paste this URL in manually:)")
    print(f"  {auth_url}")
    print()

    # Reset captured state and start the local server
    global _RESULT
    _RESULT = {}
    try:
        httpd = socketserver.TCPServer((CALLBACK_HOST, port), _CallbackHandler)
    except OSError as e:
        print(f"  ✗ Could not bind to {CALLBACK_HOST}:{port}: {e}")
        print(f"  Another process is using this port. Stop it and re-run.")
        return None
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    try:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass  # best-effort; user can open manually

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _RESULT.get("code"):
                break
            time.sleep(0.5)
    finally:
        httpd.shutdown()
        httpd.server_close()

    if not _RESULT.get("code"):
        print(f"  ✗ Timed out after {timeout_s}s waiting for the callback.")
        print(f"    Make sure you approved the install in the browser.")
        return None
    if _RESULT.get("state") != state:
        print(f"  ✗ State mismatch — refusing to continue (CSRF protection).")
        return None

    # Exchange code for token
    print(f"  ✓ Got auth code. Exchanging for token...")
    token_url = f"https://{shop}/admin/oauth/access_token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": _RESULT["code"],
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            token_url, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  ✗ Token exchange failed: {e}")
        return None

    token = data.get("access_token")
    if not token:
        print(f"  ✗ No access_token in response: {data}")
        return None

    print()
    print(f"  ✓ Got access token: {token[:6]}...{token[-4:]}")
    granted = data.get("scope", "")
    if granted:
        print(f"  Scopes granted: {granted}")
    return token


# ---------------------------------------------------------------------------
# .env helper
# ---------------------------------------------------------------------------

def write_to_env(
    shop: str,
    token: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    env_path: Optional[Path] = None,
) -> Path:
    """Update or add Shopify-related env vars in .env. Preserves other lines.

    Defensive: writes a backup `.env.bak` before mutating, so any accidental
    line-loss can be recovered. Only lines whose key matches a Shopify-* key
    we're explicitly replacing are dropped — everything else (comments, blank
    lines, ANTHROPIC_API_KEY, etc.) is preserved verbatim.
    """
    env_path = env_path or (Path(__file__).parent / ".env")
    keys_to_replace = {"SHOPIFY_SHOP"}
    new_pairs = [f"SHOPIFY_SHOP={shop}"]
    if token is not None:
        keys_to_replace.add("SHOPIFY_ADMIN_TOKEN")
        new_pairs.append(f"SHOPIFY_ADMIN_TOKEN={token}")
    if client_id is not None:
        keys_to_replace.add("SHOPIFY_CLIENT_ID")
        new_pairs.append(f"SHOPIFY_CLIENT_ID={client_id}")
    if client_secret is not None:
        keys_to_replace.add("SHOPIFY_CLIENT_SECRET")
        new_pairs.append(f"SHOPIFY_CLIENT_SECRET={client_secret}")

    # Back up before mutating — cheap insurance against the bug we hit on
    # 2026-05-06 where ANTHROPIC_API_KEY went missing during this rewrite.
    if env_path.exists():
        backup = env_path.with_suffix(env_path.suffix + ".bak")
        backup.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")

    lines: list[str] = []
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(raw)  # preserve comments and blank lines
                continue
            key = raw.split("=", 1)[0].strip()
            if key in keys_to_replace:
                continue  # will be replaced below
            lines.append(raw)
    lines.extend(new_pairs)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


# ---------------------------------------------------------------------------
# Interactive CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Obtain a Shopify Admin API token from Client ID + Secret.",
    )
    parser.add_argument(
        "--redirect", action="store_true",
        help="Use the OAuth redirect flow (browser + local callback). "
             "Default is Client Credentials grant (no browser needed).",
    )
    args = parser.parse_args()

    print()
    print("=" * 70)
    flow_label = "OAuth redirect" if args.redirect else "Client Credentials"
    print(f" Shopify token setup — {flow_label} flow")
    print("=" * 70)
    print()

    # Prefer env vars when set; prompt otherwise.
    shop = os.environ.get("SHOPIFY_SHOP", "").strip()
    if not shop:
        shop = input("Shop domain (e.g. paststudies.myshopify.com): ").strip()
    else:
        print(f"Shop: {shop}  (from $SHOPIFY_SHOP)")

    client_id = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    if not client_id:
        client_id = input("Client ID (API key): ").strip()
    else:
        print(f"Client ID: {client_id[:6]}...  (from $SHOPIFY_CLIENT_ID)")

    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    if not client_secret:
        client_secret = input("Client Secret (API secret): ").strip()
    else:
        print(f"Client Secret: ****  (from $SHOPIFY_CLIENT_SECRET)")

    if args.redirect:
        # OAuth redirect flow (browser-based)
        print()
        print(f"Before continuing, in your Shopify app config:")
        print(f"  - Add this Allowed redirection URL: "
              f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}")
        scope_input = input("Scopes (comma-separated, default 'read_products'): ").strip()
        scopes = (
            [s.strip() for s in scope_input.split(",") if s.strip()]
            if scope_input else DEFAULT_SCOPES
        )
        token = obtain_admin_token(shop, client_id, client_secret, scopes)
        if not token:
            return 1
        print(f"  ✓ Got token: {token[:6]}...{token[-4:]}")
    else:
        # Client Credentials grant (no browser)
        print()
        print(f"  Requesting token via client_credentials grant...")
        try:
            response = fetch_token_via_client_credentials(shop, client_id, client_secret)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            print(f"  ✗ HTTP {e.code}: {e.reason}")
            print(f"    Body: {err_body[:200]}")
            print(f"  Common causes:")
            print(f"    - Wrong client_secret")
            print(f"    - App not yet installed on this shop")
            print(f"    - Required scopes not granted in the app config")
            print(f"  If your app type doesn't support client_credentials, retry with:")
            print(f"    uv run shopify_oauth.py --redirect")
            return 1
        except Exception as e:
            print(f"  ✗ Request failed: {e}")
            return 1
        token = response.get("access_token")
        if not token:
            print(f"  ✗ No access_token in response: {response}")
            return 1
        scopes_granted = response.get("scope", "")
        expires_in = response.get("expires_in")
        print(f"  ✓ Got token: {token[:6]}...{token[-4:]}")
        if scopes_granted:
            print(f"    Scopes: {scopes_granted}")
        if expires_in:
            print(f"    Expires in: {expires_in}s ({expires_in/3600:.1f} hours)")
            print(f"    The app will auto-refresh — credentials stay in .env.")

    # Write everything to .env so the auto-refresh path has what it needs.
    print()
    write = input("Write to .env? [Y/n] ").strip().lower()
    if write in ("", "y", "yes"):
        path = write_to_env(
            shop=shop,
            token=token,
            client_id=client_id,
            client_secret=client_secret,
        )
        print(f"  ✓ Wrote SHOPIFY_SHOP, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET, "
              f"SHOPIFY_ADMIN_TOKEN to {path}")
        print(f"  Restart the Streamlit app to pick up the new token.")
    else:
        print()
        print(f"  Add these to your .env manually:")
        print(f"    SHOPIFY_SHOP={shop}")
        print(f"    SHOPIFY_CLIENT_ID={client_id}")
        print(f"    SHOPIFY_CLIENT_SECRET={client_secret}")
        print(f"    SHOPIFY_ADMIN_TOKEN={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
