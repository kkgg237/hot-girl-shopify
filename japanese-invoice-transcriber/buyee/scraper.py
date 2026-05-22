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
