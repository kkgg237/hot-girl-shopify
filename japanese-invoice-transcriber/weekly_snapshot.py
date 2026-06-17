#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.0", "python-dotenv>=1.0", "pyyaml>=6.0"]
# ///
"""Weekly Shopify catalogue baseline snapshot.

Run once per week to capture the current state of every LIVE product in
Shopify. Hooked into the same snapshots subsystem the audit UI uses, so a
weekly file shows up in the same "📦 Snapshots & rollback" panel.

Run modes:
    uv run python weekly_snapshot.py            # one-shot snapshot
    uv run python weekly_snapshot.py --dry-run  # count what'd be snapshotted

Wire to launchd to run automatically every Monday morning:
    See launchd/install_streamlit.sh for the pattern.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


HERE = Path(__file__).parent

# Auto-load .env so SHOPIFY_* vars are available (launchd has a sparse environment).
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True) or str(HERE / ".env"), override=True)
except ImportError:
    pass


def _list_live_product_ids() -> tuple[list[int], str]:
    """Page through GraphQL products and return every live product's legacy ID."""
    from shopify_inventory import get_shop, get_token
    from shopify_push import DEFAULT_API_VERSION

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return [], "Shopify not configured"

    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    query = """
    query Live($cursor: String) {
      products(first: 250, after: $cursor, query: "status:active published_status:published") {
        edges {
          cursor
          node { legacyResourceId }
        }
        pageInfo { hasNextPage }
      }
    }
    """
    ids: list[int] = []
    cursor = None
    while True:
        payload = json.dumps({"query": query, "variables": {"cursor": cursor}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            return ids, f"HTTP {e.code}: {body}"
        if data.get("errors"):
            return ids, f"GraphQL errors: {data['errors']}"

        conn = (data.get("data") or {}).get("products") or {}
        edges = conn.get("edges") or []
        for edge in edges:
            try:
                ids.append(int((edge.get("node") or {}).get("legacyResourceId") or 0))
            except (TypeError, ValueError):
                continue
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not edges:
            break
        cursor = edges[-1].get("cursor")
        time.sleep(0.1)
    ids = [i for i in ids if i]
    return ids, ""


def _notify_telegram(message: str) -> None:
    """Best-effort Telegram ping. Silent failure — don't let a Telegram
    config issue mask a snapshot result."""
    try:
        from buyee.config import load_config
        from buyee.listen import send_message
        cfg = load_config()
        if cfg.telegram_configured:
            send_message(cfg.telegram_token, cfg.telegram_authorized_chat_id, message)
    except Exception as e:
        print(f"[weekly-snapshot] (telegram notify failed: {e})", file=sys.stderr)


def run(dry_run: bool = False, notify: bool = True) -> int:
    print(f"[weekly-snapshot] {time.strftime('%F %T')}  starting…")
    ids, err = _list_live_product_ids()
    if err and not ids:
        msg = f"✗ Weekly snapshot · list failed: {err}"
        print(f"[weekly-snapshot] {msg}", file=sys.stderr)
        if notify and not dry_run:
            _notify_telegram(msg)
        return 1
    print(f"[weekly-snapshot] {len(ids):,} live products discovered.")
    if dry_run:
        print(f"[weekly-snapshot] --dry-run: skipping snapshot write.")
        return 0

    from snapshots import create_snapshot
    count, result = create_snapshot(ids, label="catalog_baseline", kind="weekly")
    if count > 0:
        print(f"[weekly-snapshot] ✓ snapshotted {count:,} products → {result}")
        if notify:
            stem = Path(str(result)).name
            _notify_telegram(
                f"📸 Weekly Shopify snapshot · {time.strftime('%a %b %-d %H:%M')}"
                f"\n✓ {count:,} live products captured"
                f"\nFile: {stem}"
                f"\n\nKept 12 weekly baselines (≈3 months). "
                f"Restore via the 🛍️ Shopify audit tab's 📦 Snapshots panel."
            )
        return 0
    msg = f"✗ Weekly snapshot · capture failed: {result}"
    print(f"[weekly-snapshot] {msg}", file=sys.stderr)
    if notify:
        _notify_telegram(msg)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="List the products that would be snapshotted, but skip the write.")
    ap.add_argument("--no-notify", action="store_true",
                    help="Skip the Telegram ping (default: notify on success/failure).")
    args = ap.parse_args()
    return run(dry_run=args.dry_run, notify=not args.no_notify)


if __name__ == "__main__":
    raise SystemExit(main())
