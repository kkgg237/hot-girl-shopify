"""Scraper — list shipped orders and download invoice PDFs from Buyee.

We don't yet know the exact selectors / URL patterns Buyee uses on the
authenticated shipped-baggages page (they may also change over time). The
strategy here is **discovery + extraction**:

  1. On each sync, we navigate to /mybaggages/shipped/<page>
  2. Save the raw HTML to buyee/state/raw_html/ for inspection (the first
     time you run this, we'll iterate selectors based on what's actually there)
  3. Use a layered set of best-guess selectors to find:
        - Order ID (the unique baggage / shipping number)
        - Invoice / PDF link (Buyee provides a "Shipping Invoice" PDF for each)
        - Optional: shipped date, total
  4. For each order we haven't downloaded yet, fetch the PDF and drop it in
     ../inputs/buyee/<order_id>.pdf

If selectors fail to find any orders, we tell the user and dump the page so
we can refine. This is a deliberately defensive design — Buyee's UI will
change, and we want to recover gracefully.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Iterator, Optional

from .auth import SHIPPED_URL, with_session
from .index import IndexedOrder, OrderIndex, IndexMeta, load_meta, save_meta


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
INPUTS_DIR = PROJECT_ROOT / "inputs" / "buyee"
RAW_HTML_DIR = HERE / "state" / "raw_html"


# Best-guess selectors — refined empirically after first sync.
# Each entry is (description, css_or_xpath_pattern).
ORDER_ROW_SELECTORS = [
    "table.mybaggages tr",
    ".mybaggages-list .mybaggages-item",
    ".baggage-list .baggage-item",
    "tr[data-baggage-id]",
    "tr",  # last-resort fallback
]

# Patterns to extract a baggage ID. Buyee baggage IDs look like W2603199549
# (the letter W followed by 10 digits). The invoice PDF lives at
# /mybaggages/pdfoutput/<baggage_id>. We deliberately do NOT match item-level
# auction IDs from /myorders/bids/successful/<id>/details — those are
# individual items consolidated into a baggage, not the invoice we want.
ORDER_ID_REGEXES = [
    re.compile(r"/mybaggages/pdfoutput/(W\d+)"),         # the PDF link itself
    re.compile(r"/mybaggages/(?:detail|invoice|view|edit|info)/(W\d+)"),
    re.compile(r"baggage[_-]?id[=:](\w+)", re.IGNORECASE),
]

# Anchors whose href looks like an invoice / PDF download
INVOICE_URL_HINTS = [
    "/mybaggages/pdfoutput/",
    "invoice",
    "/pdf",
    ".pdf",
    "shipping_invoice",
]


def list_shipped_pages(max_pages: int = 10) -> Iterator[tuple[int, str]]:
    """Yield (page_number, html) for each shipped-baggages listing page.

    Stops when a page returns no order rows (or we hit max_pages).
    """
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)

    with with_session(headless=True) as (pw, ctx, page):
        for page_num in range(1, max_pages + 1):
            url = f"https://buyee.jp/mybaggages/shipped/{page_num}"
            print(f"  → Fetching page {page_num}: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Give JS-rendered tables a moment to populate
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # don't fail the whole sync if we just timed out

            html = page.content()
            (RAW_HTML_DIR / f"shipped_{page_num}.html").write_text(html, encoding="utf-8")

            # Heuristic stopping condition: page is empty / "no shipments"
            if _looks_empty(html):
                print(f"  ⓘ Page {page_num} appears empty, stopping.")
                break

            yield page_num, html


def _looks_empty(html: str) -> bool:
    """Heuristic: does the page have no shipped baggages?"""
    indicators = [
        "no baggage",
        "no shipment",
        "no items",
        "There are no",
        "ありません",  # "none" in Japanese
        "該当する商品がありません",
    ]
    low = html.lower()
    if any(ind.lower() in low for ind in indicators):
        return True
    # If there are no <tr> at all, definitely empty
    return "<tr" not in low and "baggage" not in low


def _extract_orders_from_html(html: str) -> list[IndexedOrder]:
    """Parse a shipped-baggages page HTML into IndexedOrder objects.

    Uses a layered regex+structural approach since Buyee's exact markup may
    drift. We try to find anchor tags with hrefs that look like baggage
    detail or invoice links.
    """
    orders: list[IndexedOrder] = []
    seen_ids: set[str] = set()

    # Find every <a href="..."> in the page; it's the broadest net for
    # detail / invoice / pdf links. Then group by baggage id.
    href_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)
    hrefs = href_pattern.findall(html)

    # Map order_id -> {detail_url, invoice_url}
    by_id: dict[str, dict[str, str]] = {}

    for href in hrefs:
        if not href.startswith(("http", "/")):
            continue
        full = href if href.startswith("http") else f"https://buyee.jp{href}"

        # Try every order-id regex; first match wins
        oid = None
        for rgx in ORDER_ID_REGEXES:
            m = rgx.search(href)
            if m:
                oid = m.group(1)
                break
        if not oid:
            continue

        slot = by_id.setdefault(oid, {})

        is_invoice = any(hint in href.lower() for hint in INVOICE_URL_HINTS)
        if is_invoice and "invoice_url" not in slot:
            slot["invoice_url"] = full
        elif "detail_url" not in slot:
            slot["detail_url"] = full

    for oid, urls in by_id.items():
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        orders.append(
            IndexedOrder(
                order_id=oid,
                detail_url=urls.get("detail_url"),
                invoice_url=urls.get("invoice_url"),
            )
        )

    return orders


def sync_invoices(max_pages: int = 10, dry_run: bool = False) -> dict:
    """Discover shipped orders and download any invoices we don't have yet.

    Returns a dict with stats: {seen, new, downloaded, errors}.
    Sets `pdf_path` on each downloaded order in the index.
    Updates buyee/state/meta.json with sync timestamps + counts so the UI
    can show freshness and decide when to auto-sync next.
    """
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    index = OrderIndex()

    # Mark sync started — useful for "currently syncing" indicators
    meta = load_meta()
    meta.last_sync_started_at = _dt.datetime.now().isoformat(timespec="seconds")
    save_meta(meta)

    stats = {"seen": 0, "new": 0, "downloaded": 0, "errors": 0,
             "pages_visited": 0, "skipped_existing": 0}

    discovered: list[IndexedOrder] = []
    for page_num, html in list_shipped_pages(max_pages=max_pages):
        stats["pages_visited"] += 1
        page_orders = _extract_orders_from_html(html)
        if not page_orders:
            print(f"  ⚠ No orders parsed on page {page_num}. "
                  f"Saved HTML to {RAW_HTML_DIR / f'shipped_{page_num}.html'} "
                  f"for selector refinement.")
        discovered.extend(page_orders)

    stats["seen"] = len(discovered)

    # Upsert into index. Mark new ones.
    new_orders: list[IndexedOrder] = []
    for o in discovered:
        existed = o.order_id in index
        index.upsert(o)
        if not existed:
            new_orders.append(o)
    stats["new"] = len(new_orders)

    # Download missing PDFs
    if not dry_run:
        with with_session(headless=True) as (pw, ctx, page):
            for o in index.pending():
                if not o.invoice_url:
                    print(f"  ⏭ {o.order_id}: no invoice_url known yet — "
                          f"will retry next sync once we refine selectors.")
                    continue
                pdf_path = INPUTS_DIR / f"buyee_{o.order_id}.pdf"
                if pdf_path.exists():
                    print(f"  ⓘ {o.order_id}: PDF already exists at {pdf_path.name}")
                    index.mark_downloaded(o.order_id, str(pdf_path.relative_to(PROJECT_ROOT)))
                    stats["skipped_existing"] += 1
                    continue
                ok = _download_pdf(page, o.invoice_url, pdf_path)
                if ok:
                    index.mark_downloaded(o.order_id, str(pdf_path.relative_to(PROJECT_ROOT)))
                    stats["downloaded"] += 1
                    print(f"  ✓ {o.order_id} → {pdf_path.name}")
                else:
                    stats["errors"] += 1
                    print(f"  ✗ {o.order_id}: download failed")

    index.save()

    # Refresh the V-prefix item-URL cache whenever we discovered new
    # invoices. The cache walks `/mybaggages/shipped/*` to pair each
    # source_id with its actual btob URL — needed because LuxeWholesale
    # (V-prefix) URLs are opaque and can't be constructed from the V-id.
    # Skip on quiet syncs (no new downloads) to avoid pointless re-fetches:
    # the cache's stop-when-no-new-pairs logic would no-op anyway, but
    # skipping saves a Playwright session boot.
    if not dry_run and stats["downloaded"] > 0:
        try:
            print(f"  ⓘ {stats['downloaded']} new invoice(s) — refreshing item-URL cache…")
            url_stats = scrape_item_urls(headless=True)
            stats["item_urls_new"] = url_stats.get("urls_new", 0)
            stats["item_urls_total"] = url_stats.get("urls_found", 0)
            print(f"  ✓ Item-URL cache: {url_stats.get('urls_new', 0)} new, "
                  f"{url_stats.get('urls_found', 0)} total this run.")
        except Exception as e:
            print(f"  ⚠ Item-URL cache refresh failed: {e}")
            stats["item_urls_error"] = str(e)
    else:
        stats["item_urls_new"] = 0
        stats["item_urls_total"] = 0

    # Record sync completion in meta — drives auto-sync TTL + freshness badges
    meta = load_meta()
    meta.last_sync_completed_at = _dt.datetime.now().isoformat(timespec="seconds")
    meta.last_sync_pages = stats["pages_visited"]
    meta.last_sync_seen = stats["seen"]
    meta.last_sync_new = stats["new"]
    meta.last_sync_downloaded = stats["downloaded"]
    meta.last_sync_errors = stats["errors"]
    meta.last_sync_error_msg = None  # cleared on successful completion
    meta.sync_count += 1
    save_meta(meta)

    return stats


# ---------------------------------------------------------------------------
# Item-URL scraping — pull (source_id → btob URL) pairs from baggage pages.
#
# Why this exists: Buyee's LuxeWholesale (V-prefix) items have URLs like
#   https://buyee.jp/btob/item/25/202603068954486
# where the 15-digit ID is opaque — it's NOT derivable from the V-id or any
# field in the PDF. The only way to get the URL is to scrape Buyee's
# baggage-detail page, which lists each item with its <a href> to the btob
# URL. We cache the result so Pricing-tab Auction-column lookups are O(1).
# ---------------------------------------------------------------------------

ITEM_URLS_CACHE = HERE / "state" / "item_urls.json"

# Pattern that catches every btob item URL we've seen so far:
#   https://buyee.jp/btob/item/25/202603068954486
_BTOB_HREF_RE = re.compile(r'href="(https?://buyee\.jp/btob/item/\d+/\d+)"', re.IGNORECASE)
_BTOB_HREF_RE_REL = re.compile(r'href="(/btob/item/\d+/\d+)"', re.IGNORECASE)
# V-id pattern: capital V then 8+ digits
_VID_RE = re.compile(r'\bV\d{8,}\b')
# Yahoo Auctions ID pattern: lowercase letter + 8+ digits (already mapped
# deterministically, but we still cache the URL for completeness).
_YAH_RE = re.compile(r'\b[a-z]\d{8,}\b')


def _baggage_detail_candidates(order_id: str) -> list[str]:
    """Buyee's baggage detail URL pattern has shifted over time. Try a wide
    range of plausible paths; the scraper uses the first one that returns
    a non-empty page containing btob item links.
    """
    return [
        # btob namespace (the URL family of the items we're after)
        f"https://buyee.jp/btob/mybaggages/{order_id}",
        f"https://buyee.jp/btob/mybaggages/detail/{order_id}",
        f"https://buyee.jp/btob/orders/{order_id}",
        f"https://buyee.jp/btob/baggages/{order_id}",
        # Classic /mybaggages namespace
        f"https://buyee.jp/mybaggages/{order_id}",
        f"https://buyee.jp/mybaggages/detail/{order_id}",
        f"https://buyee.jp/mybaggages/info/{order_id}",
        f"https://buyee.jp/mybaggages/view/{order_id}",
        f"https://buyee.jp/mybaggages/edit/{order_id}",
        # Pre-shipment / consolidated views
        f"https://buyee.jp/baggages/{order_id}",
        f"https://buyee.jp/mybaggages/shipping/{order_id}",
    ]


def _extract_item_url_pairs(html: str) -> dict[str, str]:
    """From a baggage detail page's HTML, pair each btob URL with the
    nearest V-id (or Yahoo Auctions ID) that appears in the surrounding
    text. Returns {source_id: full_btob_url}.

    Strategy: walk the HTML in order, alternating between "found a btob
    URL" and "found a V-id". Each btob URL gets assigned to the LAST V-id
    seen — Buyee's detail rows render id first, link second.
    """
    pairs: dict[str, str] = {}
    # Build an ordered list of (position, kind, value) tokens
    tokens: list[tuple[int, str, str]] = []
    for m in _VID_RE.finditer(html):
        tokens.append((m.start(), "vid", m.group(0)))
    for m in _YAH_RE.finditer(html):
        tokens.append((m.start(), "yah", m.group(0)))
    for m in _BTOB_HREF_RE.finditer(html):
        tokens.append((m.start(), "btob", m.group(1)))
    for m in _BTOB_HREF_RE_REL.finditer(html):
        tokens.append((m.start(), "btob", f"https://buyee.jp{m.group(1)}"))
    tokens.sort()

    last_sid: Optional[str] = None
    for _, kind, value in tokens:
        if kind in ("vid", "yah"):
            last_sid = value
        elif kind == "btob" and last_sid:
            # Only set if we don't already have a URL for this sid — first
            # match wins, which is usually the canonical link.
            pairs.setdefault(last_sid, value)
            last_sid = None  # consume this sid so duplicates don't grab it
    return pairs


def _load_item_urls_cache() -> dict[str, str]:
    """Read the on-disk cache. Returns {} if missing/corrupt."""
    if not ITEM_URLS_CACHE.exists():
        return {}
    try:
        import json
        return json.loads(ITEM_URLS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_item_urls_cache(cache: dict[str, str]) -> None:
    """Atomic-ish write — temp file + rename."""
    import json
    ITEM_URLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ITEM_URLS_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ITEM_URLS_CACHE)


def scrape_item_urls(
    order_ids: Optional[list[str]] = None,  # kept for back-compat; ignored
    only_new: bool = True,                  # kept for back-compat; ignored
    headless: bool = True,
    max_pages: int = 10,
) -> dict:
    """Walk Buyee's shipped-baggages pages and cache (source_id → btob URL).

    Discovery path (June 2026):
      The shipped-list page itself
      (`/mybaggages/shipped/<n>?term=0&page=<n>`) embeds the btob item URL
      inline for every item in every baggage on that page. There's no
      per-baggage drill-down needed — one page fetch yields dozens of
      pairs. Pagination follows the same `?page=N` parameter the URL bar
      shows. We stop when a page produces zero new pairs (empty or
      already-cached) or when we hit `max_pages`.

    Args:
      order_ids:  legacy parameter, currently ignored. Pages contain every
                  baggage's items intermixed; filtering to a single baggage
                  isn't a useful saving since we already paginate cheaply.
      only_new:   legacy, ignored — pagination stop-on-no-new handles this.
      headless:   pass-through to Playwright. False opens a visible browser
                  window (useful when debugging Cloudflare challenges).
      max_pages:  pagination cap. 10 covers ~250 items for typical accounts.

    Returns stats dict: {pages_visited, urls_found, urls_new, errors}.
    """
    cache = _load_item_urls_cache()
    stats = {"pages_visited": 0, "urls_found": 0, "urls_new": 0, "errors": 0}

    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)

    with with_session(headless=headless) as (pw, ctx, page):
        for page_num in range(1, max_pages + 1):
            url = f"https://buyee.jp/mybaggages/shipped/{page_num}?term=0&page={page_num}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                html = page.content()
            except Exception as e:
                print(f"  ✗ page {page_num}: navigate failed: {e}")
                stats["errors"] += 1
                continue

            stats["pages_visited"] += 1
            # Persist for inspection / future selector work
            (RAW_HTML_DIR / f"shipped_authed_p{page_num}.html").write_text(
                html, encoding="utf-8",
            )

            pairs = _extract_item_url_pairs(html)
            if not pairs:
                print(f"  ⏭ page {page_num}: no btob links found; stopping.")
                break

            new_this_page = 0
            for sid, item_url in pairs.items():
                if cache.get(sid) != item_url:
                    new_this_page += 1
                cache[sid] = item_url
            stats["urls_found"] += len(pairs)
            stats["urls_new"] += new_this_page
            print(f"  ✓ page {page_num}: {len(pairs)} pairs ({new_this_page} new)")

            # If a whole page produces no new pairs we've caught up; stop.
            if new_this_page == 0 and page_num > 1:
                print(f"  ⓘ page {page_num}: all pairs already cached, stopping.")
                break

    _save_item_urls_cache(cache)
    return stats


def get_item_url(source_id: str) -> Optional[str]:
    """Public lookup — returns the cached btob URL for a source_id, or None.

    Hot path for the Pricing-tab Auction column. Reads the cache on every
    call; the app-side loader caches the dict in Streamlit's session so
    disk hits stay infrequent.
    """
    return _load_item_urls_cache().get(source_id)


def _download_pdf(page, url: str, dest: Path) -> bool:
    """Use Playwright's request context to fetch a PDF with our auth cookies."""
    try:
        resp = page.context.request.get(url, timeout=30000)
        if resp.status >= 400:
            print(f"    HTTP {resp.status} for {url}")
            return False
        body = resp.body()
        # Sanity: must look like a PDF
        if not body[:5].startswith(b"%PDF-"):
            print(f"    Response doesn't look like PDF (starts with {body[:20]!r})")
            # Save the response anyway under a debug name for inspection
            dbg = dest.with_suffix(".html")
            dbg.write_bytes(body)
            print(f"    Saved response to {dbg.name} for debugging.")
            return False
        dest.write_bytes(body)
        return True
    except Exception as e:
        print(f"    Exception fetching {url}: {e}")
        return False
