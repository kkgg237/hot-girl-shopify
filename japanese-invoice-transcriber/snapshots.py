"""Snapshot + rollback for the Shopify audit's bulk-apply operations.

A snapshot captures the current Shopify state (category, body_html, etc.)
for a specified set of product IDs *before* any writes happen. If a bulk
Apply produces unexpected results, the snapshot can be replayed to revert
every product to the captured state.

Manual-trigger only — the user clicks "📸 Snapshot now" in the audit tab.
Snapshots older than 7 days are auto-pruned on the next list call.

Storage:
    buyee/state/snapshots/snapshot_<UTC-timestamp>_<label>.json

Public API:
    create_snapshot(product_ids, label) -> (count, path | error_message)
    list_snapshots()                    -> list[{path, ts, label, count, size_bytes}]
    load_snapshot(path)                 -> dict
    restore_snapshot(snapshot)          -> dict(stats per field type)
    prune_old_snapshots(days=7)         -> int (number deleted)
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Union

from shopify_push import DEFAULT_API_VERSION


HERE = Path(__file__).parent
SNAPSHOT_DIR = HERE / "buyee" / "state" / "snapshots"

# Retention rules per snapshot KIND:
#   - "pre_apply" snapshots are auto-created before every bulk Apply so the
#     UI can offer an Undo button. Kept for 30 minutes only — after that,
#     the change is considered intentional and the undo affordance expires.
#   - "weekly" snapshots are full-catalog baselines, captured once per ISO
#     week. Kept for ~3 months (last 12 files) so a long-running issue can
#     be rolled back to a known-good state.
#   - "manual" (legacy) snapshots from the original manual button — 7-day
#     retention, identical behavior to the v1 implementation.
PRE_APPLY_RETENTION_MINUTES = 30
WEEKLY_KEEP_COUNT = 12
MANUAL_RETENTION_DAYS = 7


# ---------------------------------------------------------------------------
# Fetch + write
# ---------------------------------------------------------------------------

def _gql(query: str, variables: dict) -> tuple[Optional[dict], Optional[str]]:
    from shopify_inventory import get_shop, get_token
    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return None, "Shopify not configured"
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        return None, f"HTTP {e.code}: {body}"
    except urllib.error.URLError as e:
        return None, f"Network error: {e.reason}"


_NODES_QUERY = """
query Nodes($ids: [ID!]!) {
  nodes(ids: $ids) {
    __typename
    ... on Product {
      id
      legacyResourceId
      title
      vendor
      productType
      tags
      status
      bodyHtml
      category { id name fullName }
    }
  }
}
"""


def create_snapshot(
    product_ids: list[int],
    label: str = "manual",
    batch_size: int = 100,
    kind: str = "manual",
) -> tuple[int, Union[str, Path]]:
    """Capture the current Shopify state of every product in `product_ids`
    and write to a snapshot JSON file. Returns (count, path) on success or
    (0, error_message) on failure."""
    if not product_ids:
        return 0, "no product IDs provided"

    # Dedupe + cast to int
    seen: set[int] = set()
    clean_ids: list[int] = []
    for pid in product_ids:
        try:
            n = int(pid)
        except (TypeError, ValueError):
            continue
        if n and n not in seen:
            seen.add(n)
            clean_ids.append(n)
    if not clean_ids:
        return 0, "no valid product IDs"

    products: list[dict] = []
    for i in range(0, len(clean_ids), batch_size):
        batch = clean_ids[i:i + batch_size]
        gids = [f"gid://shopify/Product/{pid}" for pid in batch]
        data, err = _gql(_NODES_QUERY, {"ids": gids})
        if err:
            # Partial: save what we have so far, append error note
            partial_err = f"Fetch failed mid-batch ({i}): {err}"
            if products:
                break
            return 0, partial_err
        if data.get("errors"):
            return 0, f"GraphQL errors: {data['errors']}"
        for node in (data.get("data") or {}).get("nodes") or []:
            if not node or node.get("__typename") != "Product":
                continue
            try:
                pid = int(node.get("legacyResourceId") or 0)
            except (TypeError, ValueError):
                continue
            if not pid:
                continue
            cat = node.get("category") or {}
            products.append({
                "id": pid,
                "title": node.get("title") or "",
                "vendor": node.get("vendor") or "",
                "product_type": node.get("productType") or "",
                "tags": node.get("tags") or [],
                "status": node.get("status") or "",
                "body_html": node.get("bodyHtml") or "",
                "category_gid": (cat.get("id") if cat else None),
                "category_name": (cat.get("name") if cat else None),
                "category_full_name": (cat.get("fullName") if cat else None),
            })
        time.sleep(0.05)

    if not products:
        return 0, "fetched nothing — Shopify returned no Product nodes for those IDs"

    # Slug the label so it's filesystem-safe
    safe_label = re.sub(r"[^a-z0-9_-]+", "_", label.lower()).strip("_") or "manual"
    safe_kind = re.sub(r"[^a-z_]+", "_", (kind or "manual").lower()).strip("_") or "manual"
    ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    # File name shape: snapshot_<ts>_<kind>_<label>.json. The kind segment
    # is what list_snapshots / restore reads to bucket retention.
    path = SNAPSHOT_DIR / f"snapshot_{ts}_{safe_kind}_{safe_label}.json"
    payload = {
        "ts": ts,
        "ts_iso": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "kind": safe_kind,
        "label": label,
        "count": len(products),
        "products": products,
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return len(products), path


# ---------------------------------------------------------------------------
# List + load + prune
# ---------------------------------------------------------------------------

def _kind_from_name(name: str) -> str:
    """Pull the kind segment from a snapshot filename. Falls back to "manual"
    for v1 files that pre-date the kind segment."""
    m = re.match(r"snapshot_\d{8}T\d{6}Z_([a-z_]+)_", name)
    return (m.group(1) if m else "manual")


def list_snapshots() -> list[dict]:
    """Return metadata for each snapshot file, newest first. Side-effect:
    runs prune_old_snapshots so callers always see the retention-trimmed set."""
    prune_old_snapshots()
    if not SNAPSHOT_DIR.exists():
        return []
    out = []
    for p in SNAPSHOT_DIR.glob("snapshot_*.json"):
        try:
            with p.open(encoding="utf-8") as f:
                head = json.load(f)
            out.append({
                "path": str(p),
                "name": p.name,
                "ts_iso": head.get("ts_iso") or "",
                "kind": head.get("kind") or _kind_from_name(p.name),
                "label": head.get("label") or "(no label)",
                "count": int(head.get("count") or 0),
                "size_bytes": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            })
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


def load_snapshot(path: Union[str, Path]) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def latest_pre_apply_snapshot_path() -> Optional[Path]:
    """Most recent pre_apply snapshot still within the 30-min retention,
    or None. Used to drive the Undo button after a bulk Apply."""
    if not SNAPSHOT_DIR.exists():
        return None
    cutoff = time.time() - PRE_APPLY_RETENTION_MINUTES * 60
    candidates: list[tuple[float, Path]] = []
    for p in SNAPSHOT_DIR.glob("snapshot_*_pre_apply_*.json"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            candidates.append((mtime, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def is_weekly_due() -> bool:
    """True if no weekly snapshot exists from the current ISO week
    (Mon 00:00 UTC → Sun 23:59 UTC)."""
    if not SNAPSHOT_DIR.exists():
        return True
    now = _dt.datetime.utcnow()
    monday = now - _dt.timedelta(days=now.weekday())
    monday_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = monday_start.timestamp()
    for p in SNAPSHOT_DIR.glob("snapshot_*_weekly_*.json"):
        try:
            if p.stat().st_mtime >= cutoff:
                return False
        except OSError:
            continue
    return True


def prune_old_snapshots() -> int:
    """Apply per-kind retention. Returns the count deleted."""
    if not SNAPSHOT_DIR.exists():
        return 0
    now = time.time()
    deleted = 0

    # 1. pre_apply: 30-minute retention
    pa_cutoff = now - PRE_APPLY_RETENTION_MINUTES * 60
    for p in SNAPSHOT_DIR.glob("snapshot_*_pre_apply_*.json"):
        try:
            if p.stat().st_mtime < pa_cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            continue

    # 2. weekly: keep last WEEKLY_KEEP_COUNT
    weeklies = []
    for p in SNAPSHOT_DIR.glob("snapshot_*_weekly_*.json"):
        try:
            weeklies.append((p.stat().st_mtime, p))
        except OSError:
            continue
    weeklies.sort(reverse=True)
    for _, p in weeklies[WEEKLY_KEEP_COUNT:]:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            continue

    # 3. manual: 7-day retention. This catches the legacy v1 files too.
    legacy_cutoff = now - MANUAL_RETENTION_DAYS * 86400
    for p in SNAPSHOT_DIR.glob("snapshot_*.json"):
        name = p.name
        if "_weekly_" in name or "_pre_apply_" in name:
            continue
        try:
            if p.stat().st_mtime < legacy_cutoff:
                p.unlink()
                deleted += 1
        except OSError:
            continue

    return deleted


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore_snapshot(snapshot: dict) -> dict:
    """Replay a snapshot — PUT body_html via REST and category via GraphQL
    for every product in the snapshot. Returns per-field success/failure
    counts so the UI can report what happened."""
    from shopify_push import update_product_body_html, update_product_category
    stats = {
        "body_html_ok": 0, "body_html_fail": 0,
        "category_ok": 0, "category_fail": 0, "category_skipped": 0,
        "failures": [],
    }
    products = snapshot.get("products") or []
    for p in products:
        pid = int(p.get("id") or 0)
        if not pid:
            continue
        # Restore body_html unconditionally — even empty strings are valid state
        body = p.get("body_html") or ""
        status, resp = update_product_body_html(pid, body)
        if status == 200:
            stats["body_html_ok"] += 1
        else:
            stats["body_html_fail"] += 1
            stats["failures"].append({
                "product_id": pid,
                "title": p.get("title"),
                "field": "body_html",
                "status": status,
                "response": resp,
            })

        # Restore category only if the snapshot recorded one
        gid = p.get("category_gid")
        if gid:
            cstatus, cresp = update_product_category(pid, gid)
            if cstatus == 200:
                stats["category_ok"] += 1
            else:
                stats["category_fail"] += 1
                stats["failures"].append({
                    "product_id": pid,
                    "title": p.get("title"),
                    "field": "category",
                    "status": cstatus,
                    "response": cresp,
                })
        else:
            stats["category_skipped"] += 1
        time.sleep(0.05)
    return stats
