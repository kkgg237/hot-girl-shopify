#!/usr/bin/env python3
"""Sync in-stock products + media from Shopify into a local SQLite DB and .cache/media/."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "shop.db"
CACHE_DIR = ROOT / ".cache" / "media"
API_VERSION = "2025-01"
PAGE_SIZE = 50
MEDIA_PER_PRODUCT = 50
VARIANTS_PER_PRODUCT = 20
DOWNLOAD_WORKERS = 8
IN_STOCK_QUERY = "status:active inventory_total:>0"


# --- env + http ---------------------------------------------------------------

def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def http_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_access_token(shop: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    ).encode("utf-8")
    result = http_json(
        f"https://{shop}/admin/oauth/access_token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
    )
    return result["access_token"]


# --- sqlite schema ------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  handle TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  product_type TEXT,
  vendor TEXT,
  tags TEXT,
  total_inventory INTEGER,
  tracks_inventory INTEGER,
  online_store_url TEXT,
  updated_at TEXT,
  synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  title TEXT,
  sku TEXT,
  price TEXT,
  compare_at_price TEXT,
  inventory_quantity INTEGER,
  available_for_sale INTEGER,
  position INTEGER,
  synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id);

CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  position INTEGER NOT NULL,
  url TEXT NOT NULL,
  local_path TEXT,
  width INTEGER,
  height INTEGER,
  alt_text TEXT,
  downloaded_at TEXT,
  synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_product ON media(product_id);

CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  products_upserted INTEGER,
  products_removed INTEGER,
  media_downloaded INTEGER,
  media_failed INTEGER,
  notes TEXT
);
"""


def open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# --- graphql ------------------------------------------------------------------

PRODUCTS_QUERY = """
query Products($first: Int!, $after: String, $query: String!) {
  products(first: $first, after: $after, query: $query) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        handle
        title
        status
        productType
        vendor
        tags
        totalInventory
        tracksInventory
        onlineStoreUrl
        updatedAt
        variants(first: %d) {
          edges {
            node {
              id
              title
              sku
              price
              compareAtPrice
              inventoryQuantity
              availableForSale
              position
            }
          }
        }
        media(first: %d) {
          edges {
            node {
              mediaContentType
              alt
              ... on MediaImage {
                id
                image { url width height altText }
              }
              ... on Video {
                id
                sources { url format mimeType width height }
              }
            }
          }
        }
      }
    }
  }
}
""" % (VARIANTS_PER_PRODUCT, MEDIA_PER_PRODUCT)


def fetch_all_products(shop: str, token: str) -> list[dict]:
    endpoint = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    products: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        body = json.dumps(
            {"query": PRODUCTS_QUERY, "variables": {"first": PAGE_SIZE, "after": cursor, "query": IN_STOCK_QUERY}}
        ).encode("utf-8")
        resp = http_json(endpoint, method="POST", headers=headers, body=body)
        if "errors" in resp:
            raise RuntimeError(f"GraphQL errors: {resp['errors']}")
        conn = resp["data"]["products"]
        batch = [e["node"] for e in conn["edges"]]
        products.extend(batch)
        print(f"  page {page}: +{len(batch)} (total {len(products)})", file=sys.stderr)
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return products


# --- upsert -------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert(conn: sqlite3.Connection, products: list[dict]) -> tuple[int, int]:
    synced_at = now_iso()
    seen_ids: set[str] = set()

    for p in products:
        seen_ids.add(p["id"])
        conn.execute(
            """
            INSERT INTO products
              (id, handle, title, status, product_type, vendor, tags, total_inventory,
               tracks_inventory, online_store_url, updated_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              handle=excluded.handle,
              title=excluded.title,
              status=excluded.status,
              product_type=excluded.product_type,
              vendor=excluded.vendor,
              tags=excluded.tags,
              total_inventory=excluded.total_inventory,
              tracks_inventory=excluded.tracks_inventory,
              online_store_url=excluded.online_store_url,
              updated_at=excluded.updated_at,
              synced_at=excluded.synced_at
            """,
            (
                p["id"],
                p["handle"],
                p["title"],
                p["status"],
                p.get("productType"),
                p.get("vendor"),
                json.dumps(p.get("tags") or []),
                p.get("totalInventory"),
                1 if p.get("tracksInventory") else 0,
                p.get("onlineStoreUrl"),
                p.get("updatedAt"),
                synced_at,
            ),
        )

        # Variants: replace-all for this product
        conn.execute("DELETE FROM variants WHERE product_id = ?", (p["id"],))
        for idx, edge in enumerate(p.get("variants", {}).get("edges", [])):
            v = edge["node"]
            conn.execute(
                """
                INSERT INTO variants
                  (id, product_id, title, sku, price, compare_at_price, inventory_quantity,
                   available_for_sale, position, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    v["id"],
                    p["id"],
                    v.get("title"),
                    v.get("sku"),
                    v.get("price"),
                    v.get("compareAtPrice"),
                    v.get("inventoryQuantity"),
                    1 if v.get("availableForSale") else 0,
                    v.get("position") if v.get("position") is not None else idx,
                    synced_at,
                ),
            )

        # Media: upsert, preserving local_path + downloaded_at so we don't re-download
        existing = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT id, local_path, downloaded_at FROM media WHERE product_id = ?", (p["id"],)
            )
        }
        current_media_ids: set[str] = set()
        for idx, edge in enumerate(p.get("media", {}).get("edges", [])):
            node = edge["node"]
            kind = node.get("mediaContentType")
            media_id = node.get("id")
            if not media_id:
                continue
            if kind == "IMAGE" and node.get("image"):
                img = node["image"]
                url, w, h, alt = img["url"], img.get("width"), img.get("height"), img.get("altText")
            elif kind == "VIDEO" and node.get("sources"):
                src = max(node["sources"], key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))
                url, w, h, alt = src["url"], src.get("width"), src.get("height"), node.get("alt")
            else:
                continue
            current_media_ids.add(media_id)
            prev_local, prev_downloaded = existing.get(media_id, (None, None))
            conn.execute(
                """
                INSERT INTO media
                  (id, product_id, kind, position, url, local_path, width, height, alt_text, downloaded_at, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  product_id=excluded.product_id,
                  kind=excluded.kind,
                  position=excluded.position,
                  url=excluded.url,
                  width=excluded.width,
                  height=excluded.height,
                  alt_text=excluded.alt_text,
                  synced_at=excluded.synced_at
                """,
                (media_id, p["id"], kind, idx, url, prev_local, w, h, alt, prev_downloaded, synced_at),
            )
        # Remove media that dropped from this product
        stale = set(existing) - current_media_ids
        for mid in stale:
            conn.execute("DELETE FROM media WHERE id = ?", (mid,))

    # Remove products no longer in the in-stock set (cascades to variants + media)
    existing_ids = {row[0] for row in conn.execute("SELECT id FROM products")}
    to_remove = existing_ids - seen_ids
    for pid in to_remove:
        conn.execute("DELETE FROM products WHERE id = ?", (pid,))

    conn.commit()
    return len(seen_ids), len(to_remove)


# --- downloads ----------------------------------------------------------------

def filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return os.path.basename(path) or "file"


def download(url: str, dest: Path) -> tuple[bool, str | None]:
    """Returns (downloaded_now, error). If file exists, returns (False, None)."""
    if dest.exists() and dest.stat().st_size > 0:
        return (False, None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return (True, None)
    except Exception as e:
        if dest.exists():
            dest.unlink(missing_ok=True)
        return (False, str(e))


def download_missing(conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str]]]:
    rows = conn.execute(
        """
        SELECT m.id, m.url, m.kind, m.position, p.handle
        FROM media m JOIN products p ON p.id = m.product_id
        WHERE m.local_path IS NULL
           OR NOT EXISTS (SELECT 1 FROM media m2 WHERE m2.id = m.id AND m2.local_path IS NOT NULL)
        """
    ).fetchall()

    # Resolve targets
    jobs: list[tuple[str, str, Path]] = []  # (media_id, url, dest)
    for media_id, url, _kind, position, handle in rows:
        base = filename_from_url(url)
        dest = CACHE_DIR / handle / f"{position:02d}_{base}"
        jobs.append((media_id, url, dest))

    downloaded = 0
    failed: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as ex:
        futures = {ex.submit(download, url, dest): (media_id, url, dest) for media_id, url, dest in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            media_id, url, dest = futures[fut]
            was_new, err = fut.result()
            if err:
                failed.append((url, err))
                continue
            conn.execute(
                "UPDATE media SET local_path = ?, downloaded_at = COALESCE(downloaded_at, ?) WHERE id = ?",
                (str(dest.relative_to(ROOT)), now_iso(), media_id),
            )
            if was_new:
                downloaded += 1
            if i % 25 == 0:
                conn.commit()
                print(f"  downloaded/checked {i}/{len(jobs)}", file=sys.stderr)
    conn.commit()
    return downloaded, failed


# --- main ---------------------------------------------------------------------

def main() -> int:
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("Missing .env", file=sys.stderr)
        return 1
    env = load_env(env_file)
    shop = env["SHOPIFY_SHOP"]
    client_id = env["SHOPIFY_CLIENT_ID"]
    client_secret = env["SHOPIFY_CLIENT_SECRET"]

    started_at = now_iso()
    conn = open_db()
    cur = conn.execute(
        "INSERT INTO sync_runs (started_at) VALUES (?)",
        (started_at,),
    )
    run_id = cur.lastrowid
    conn.commit()

    print(f"→ Getting access token for {shop}", file=sys.stderr)
    token = get_access_token(shop, client_id, client_secret)

    print(f"→ Fetching products ({IN_STOCK_QUERY})", file=sys.stderr)
    products = fetch_all_products(shop, token)
    print(f"  total in-stock products: {len(products)}", file=sys.stderr)

    print(f"→ Upserting into {DB_PATH.relative_to(ROOT)}", file=sys.stderr)
    upserted, removed = upsert(conn, products)
    print(f"  upserted={upserted} removed={removed}", file=sys.stderr)

    print(f"→ Downloading missing media", file=sys.stderr)
    downloaded, failed = download_missing(conn)
    print(f"  downloaded={downloaded} failed={len(failed)}", file=sys.stderr)

    conn.execute(
        """UPDATE sync_runs SET finished_at=?, products_upserted=?, products_removed=?,
           media_downloaded=?, media_failed=? WHERE id=?""",
        (now_iso(), upserted, removed, downloaded, len(failed), run_id),
    )
    conn.commit()

    # Summary
    counts = dict(
        products=conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        variants=conn.execute("SELECT COUNT(*) FROM variants").fetchone()[0],
        media=conn.execute("SELECT COUNT(*) FROM media").fetchone()[0],
        media_local=conn.execute("SELECT COUNT(*) FROM media WHERE local_path IS NOT NULL").fetchone()[0],
    )
    print("", file=sys.stderr)
    print(f"✓ DB counts: {counts}", file=sys.stderr)
    if failed:
        print("", file=sys.stderr)
        print("First download failures:", file=sys.stderr)
        for url, err in failed[:5]:
            print(f"  {err}\n    {url}", file=sys.stderr)
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
