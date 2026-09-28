"""Automated Buyee Bidding Engine using Playwright.

Handles two-step auction bidding (Place Bid -> Enter Amount -> Confirm),
item detail scraping (start price, category, title), and batch execution.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .auth import with_session
from .pricing import calculate_bid, load_pricing_config

CATEGORY_KEYWORDS = {
    "Bags": ["bag", "handbag", "shoulder", "tote", "backpack", "pouch", "waist bag", "crossbody", "clutch", "バッグ", "ショルダー", "トート", "リュック"],
    "Shoes": ["shoe", "sneaker", "boot", "loafer", "heel", "pump", "sandal", "靴", "スニーカー", "ブーツ", "ローファー"],
    "Watches": ["watch", "chronograph", "automatic", "diver", "clock", "時計", "ウォッチ", "クロノグラフ"],
    "Clothing": ["jacket", "coat", "shirt", "pant", "denim", "dress", "sweater", "hoodie", "suit", "服", "ジャケット", "コート", "シャツ", "パンツ"],
}


def infer_category_from_title_or_breadcrumbs(title: str, breadcrumbs: str = "") -> str:
    """Infer product category from title and breadcrumbs."""
    text = (title + " " + breadcrumbs).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return "Bags"


def parse_jpy_price(text: str) -> Optional[int]:
    """Extract numeric JPY price from text (e.g. '28,000 YEN' -> 28000)."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text)
    if cleaned:
        try:
            return int(cleaned)
        except ValueError:
            return None
    return None


def scrape_item_details(page: Page, url: str) -> Dict[str, Any]:
    """Safely navigate to item page and scrape title, starting price, and category.

    Guaranteed never to throw 403 unhandled errors to the caller.
    """
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1000)

        if resp and resp.status in (403, 401, 503):
            return {
                "url": url,
                "title": "Buyee Item",
                "start_price": 0,
                "category": "Bags",
                "scraped": False,
            }

        # Scrape title
        title = ""
        for sel in ["h1.g-pageTitle", "h1.item_title", "h1", "title"]:
            el = page.query_selector(sel)
            if el:
                txt = el.inner_text().strip()
                if txt and "403" not in txt and "forbidden" not in txt.lower():
                    title = txt
                    break

        # Scrape start price / current price
        start_price: Optional[int] = None
        price_selectors = [
            ".g-price__number",
            ".item_price",
            "span:has-text('YEN')",
            "td:has-text('YEN')",
            ".price",
        ]
        for sel in price_selectors:
            els = page.query_selector_all(sel)
            for el in els:
                txt = el.inner_text().strip()
                val = parse_jpy_price(txt)
                if val is not None and val > 0:
                    start_price = val
                    break
            if start_price is not None:
                break

        # Scrape breadcrumbs/categories if present
        breadcrumbs = ""
        bc_el = page.query_selector(".g-breadcrumbs, .breadcrumb")
        if bc_el:
            breadcrumbs = bc_el.inner_text().strip()

        category = infer_category_from_title_or_breadcrumbs(title, breadcrumbs)

        return {
            "url": url,
            "title": title or "Buyee Item",
            "start_price": start_price or 0,
            "category": category,
            "scraped": True,
        }

    except Exception:
        return {
            "url": url,
            "title": "Buyee Item",
            "start_price": 0,
            "category": "Bags",
            "scraped": False,
        }


def place_single_bid(
    page: Page,
    url: str,
    bid_amount: int,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute two-step bid on Buyee auction page."""
    if bid_amount <= 0:
        return {
            "url": url,
            "status": "skipped",
            "bid_amount": 0,
            "message": "Bid amount set to 0 (skipped by user).",
        }

    try:
        if page.url != url:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(1000)

        # Step 1: Click "Place Bid" button
        bid_btn = page.query_selector("a.g-btn--primary:has-text('Place Bid'), button:has-text('Place Bid'), a:has-text('Place Bid'), #bid_submit_btn")
        if not bid_btn:
            if "ended" in page.content().lower() or "closed" in page.content().lower():
                return {
                    "url": url,
                    "status": "failed",
                    "bid_amount": bid_amount,
                    "message": "Auction has ended or is closed.",
                }
            return {
                "url": url,
                "status": "failed",
                "bid_amount": bid_amount,
                "message": "Could not find 'Place Bid' button on page.",
            }

        bid_btn.click()
        page.wait_for_timeout(1500)

        # Step 2: Input bid price in modal/form
        price_input = page.query_selector("input[name='bid_price'], input#bid_price, input[type='number'], input[name='price']")
        if not price_input:
            return {
                "url": url,
                "status": "failed",
                "bid_amount": bid_amount,
                "message": "Bid input field not found after clicking Place Bid.",
            }

        price_input.fill(str(bid_amount))
        page.wait_for_timeout(500)

        if dry_run:
            return {
                "url": url,
                "status": "dry_run_success",
                "bid_amount": bid_amount,
                "message": f"[DRY-RUN] Prepared bid of {bid_amount:,} YEN (not submitted).",
            }

        # Step 3: Click confirm / submit bid button
        confirm_btn = page.query_selector("button[type='submit']:has-text('Bid'), button:has-text('Confirm'), input[type='submit']")
        if not confirm_btn:
            return {
                "url": url,
                "status": "failed",
                "bid_amount": bid_amount,
                "message": "Confirm bid button not found.",
            }

        confirm_btn.click()
        page.wait_for_timeout(2500)

        return {
            "url": url,
            "status": "success",
            "bid_amount": bid_amount,
            "message": f"Successfully placed bid of {bid_amount:,} YEN on Buyee.",
        }

    except Exception as e:
        return {
            "url": url,
            "status": "failed",
            "bid_amount": bid_amount,
            "message": f"Error during bid execution: {e}",
        }


def process_batch_bids(
    item_bid_pairs: List[Dict[str, Any]],
    dry_run: bool = False,
    headless: bool = True,
) -> List[Dict[str, Any]]:
    """Process a batch of items with pre-calculated or overridden bids."""
    results = []
    with with_session(headless=headless) as (_, _, page):
        for pair in item_bid_pairs:
            url = pair.get("url", "")
            bid_amount = pair.get("bid_amount", 0)

            if not url:
                continue

            if bid_amount <= 0:
                results.append({
                    "url": url,
                    "status": "skipped",
                    "bid_amount": 0,
                    "message": "Skipped (0 bid amount).",
                })
                continue

            res = place_single_bid(page, url, bid_amount=bid_amount, dry_run=dry_run)
            results.append(res)
            time.sleep(1)

    return results
