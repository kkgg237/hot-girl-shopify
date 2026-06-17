#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright>=1.45",
#   "pydantic>=2.0",
# ]
# ///
"""CLI for buyee invoice ingestion.

Usage:
  # Buyee account access
  uv run python -m buyee login              # one-time interactive Buyee login
  uv run python -m buyee status             # is the session still valid?
  uv run python -m buyee sync               # download new invoices
  uv run python -m buyee sync --max-pages 3 # limit pagination
  uv run python -m buyee sync --dry-run     # discover only, don't download
  uv run python -m buyee list               # show indexed orders
  uv run python -m buyee list --pending     # only orders not yet downloaded

  # Telegram trigger — sync from your phone
  uv run python -m buyee setup              # configure the Telegram bot
  uv run python -m buyee listen             # run the Telegram listener
                                            # (keep this running; send "sync"
                                            #  from your phone to trigger)

First-run note:
  After cloning, install Playwright's Chromium browser binary once:
    uv run --with playwright playwright install chromium
"""
from __future__ import annotations

import argparse
import sys

from . import auth, scraper
from .index import OrderIndex, hours_since_last_sync, humanize_freshness, load_meta


def cmd_login(args):
    auth.login_interactive()
    return 0


def cmd_status(args):
    ok, msg = auth.is_session_valid()
    print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


def cmd_sync(args):
    print(f"Starting sync (max_pages={args.max_pages}, dry_run={args.dry_run})...")
    stats = scraper.sync_invoices(max_pages=args.max_pages, dry_run=args.dry_run)
    print()
    print("=" * 60)
    print(f"  Pages visited:    {stats['pages_visited']}")
    print(f"  Orders seen:      {stats['seen']}")
    print(f"  New to index:     {stats['new']}")
    print(f"  Downloaded:       {stats['downloaded']}")
    print(f"  Skipped (exist):  {stats['skipped_existing']}")
    print(f"  Errors:           {stats['errors']}")
    print("=" * 60)

    if stats["seen"] == 0:
        print()
        print("  ⚠ No orders parsed. Check buyee/state/raw_html/shipped_1.html and")
        print("    refine the selectors in buyee/scraper.py:_extract_orders_from_html.")
    return 0 if stats["errors"] == 0 else 1


def cmd_scrape_urls(args):
    """Walk Buyee's authenticated shipped-baggages pages and cache the btob
    item URLs that appear inline on each page.

    Populates `buyee/state/item_urls.json` keyed by source_id. The Pricing-
    tab Auction column reads from this cache for V-prefix (LuxeWholesale)
    items where the URL isn't derivable from the source_id alone.
    """
    print(f"Scraping item URLs from Buyee shipped-baggages pages...")
    stats = scraper.scrape_item_urls(headless=not args.headed)
    print()
    print("=" * 60)
    print(f"  Pages visited:     {stats['pages_visited']}")
    print(f"  Pairs found:       {stats['urls_found']}")
    print(f"  New / changed:     {stats['urls_new']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  Cache file:        {scraper.ITEM_URLS_CACHE}")
    return 0 if stats["errors"] == 0 else 1


def cmd_setup(args):
    """Interactive Telegram bot setup wizard."""
    from .listen import setup_interactive
    ok = setup_interactive()
    return 0 if ok else 1


def cmd_listen(args):
    """Run the Telegram listener loop (blocks forever)."""
    from .listen import listen
    try:
        listen(poll_timeout=args.poll_timeout)
    except KeyboardInterrupt:
        print("\n[listen] Stopped by user.")
    except RuntimeError as e:
        print(f"[listen] {e}")
        return 1
    return 0


def cmd_enrich(args):
    """Run title enrichment on an invoice JSON. Cost-efficient by default."""
    from pathlib import Path
    from .research import enrich_invoice
    invoice_path = Path(args.invoice)
    if not invoice_path.exists():
        print(f"✗ Invoice not found: {invoice_path}")
        return 1
    only = args.only_ids.split(",") if args.only_ids else None
    print(f"Enriching {invoice_path.name}"
          + (f" (only {len(only)} item(s))" if only else "")
          + (" [DRY RUN]" if args.dry_run else "")
          + f"  · cost cap ${args.max_cost:.2f}")
    stats = enrich_invoice(
        invoice_path,
        dry_run=args.dry_run,
        only_ids=only,
        max_cost_usd=args.max_cost,
    )
    print()
    print("=" * 60)
    print(f"  Items processed:   {stats['items_processed']}")
    print(f"  Titles enriched:   {stats['items_enriched']}")
    print(f"  Titles unchanged:  {stats['items_unchanged']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  Total cost:        ${stats['total_cost_usd']:.4f}")
    print("=" * 60)
    print()
    if stats["items_enriched"]:
        print("Title diffs:")
        for sid, before in stats["before_titles"].items():
            after = stats["after_titles"][sid]
            if before != after:
                print(f"  {sid}")
                print(f"    before: {before}")
                print(f"    after:  {after}")
    return 0


def cmd_photos(args):
    """Scrape first-photo thumbnails from Buyee auction pages for an invoice."""
    from pathlib import Path
    from .photo_scraper import fetch_invoice_photos
    invoice_path = Path(args.invoice)
    if not invoice_path.exists():
        print(f"✗ Invoice not found: {invoice_path}")
        return 1
    only = args.only_ids.split(",") if args.only_ids else None
    print(f"Photo-scraping {invoice_path.name}"
          + (f" (only {len(only)} item(s))" if only else "")
          + (" [overwriting existing]" if args.overwrite else ""))
    stats = fetch_invoice_photos(
        invoice_path,
        only_ids=only,
        overwrite=args.overwrite,
    )
    print()
    print("=" * 60)
    print(f"  Total items:        {stats['total_items']}")
    print(f"  Eligible (auction): {stats['eligible']}")
    print(f"  Downloaded new:     {stats['downloaded']}")
    print(f"  Skipped existing:   {stats['skipped_existing']}")
    print(f"  Skipped ineligible: {stats['skipped_ineligible']}")
    print(f"  Errors:             {stats['errors']}")
    print("=" * 60)
    return 0 if stats["errors"] == 0 else 1


def cmd_notes_digest(args):
    """Send a Telegram digest of stale pending notes. For launchd / cron use.

    No-ops cleanly when there are no stale notes (so cron doesn't spam).
    --dry-run prints to stdout without sending to Telegram.
    """
    from heuristics import stale_pending_notes, format_digest, mark_reminded
    from .config import load_config
    from .listen import send_message

    notes = stale_pending_notes(threshold_days=args.threshold)
    if not notes and not args.always:
        print(f"No notes pending >= {args.threshold} days. Skipping.")
        return 0

    digest = format_digest(notes, header=f"📝 Note digest (>{args.threshold-1}d old)")
    if args.dry_run:
        print(digest)
        return 0

    cfg = load_config()
    if not cfg.telegram_configured:
        print("✗ Telegram not configured. Run `python -m buyee setup` first.")
        return 1

    ok = send_message(cfg.telegram_token, cfg.telegram_authorized_chat_id, digest)
    if not ok:
        print("✗ Telegram send failed.")
        return 1

    if notes:
        mark_reminded([n.id for n in notes])
    print(f"✓ Sent digest of {len(notes)} note(s) to Telegram.")
    return 0


def cmd_freshness(args):
    """Print last-sync info — useful for the launchd job to log freshness."""
    h = hours_since_last_sync()
    meta = load_meta()
    print(f"Last sync: {humanize_freshness(h)}")
    if meta.last_sync_completed_at:
        print(f"  completed_at: {meta.last_sync_completed_at}")
        print(f"  pages: {meta.last_sync_pages}, seen: {meta.last_sync_seen}, "
              f"new: {meta.last_sync_new}, downloaded: {meta.last_sync_downloaded}, "
              f"errors: {meta.last_sync_errors}")
    print(f"  total syncs: {meta.sync_count}")
    return 0


def cmd_list(args):
    idx = OrderIndex()
    if args.pending:
        orders = idx.pending()
        label = "Pending download"
    elif args.downloaded:
        orders = idx.downloaded()
        label = "Downloaded"
    else:
        orders = idx.all()
        label = "All indexed"

    print(f"== {label} ({len(orders)}) ==")
    if not orders:
        print("  (none)")
        return 0
    for o in orders:
        status = "✓" if o.is_downloaded else "·"
        info = []
        if o.shipped_at:
            info.append(f"shipped {o.shipped_at}")
        if o.total_jpy:
            info.append(f"¥{o.total_jpy:,}")
        if o.item_count:
            info.append(f"{o.item_count} items")
        if o.pdf_path:
            info.append(f"-> {o.pdf_path}")
        suffix = " · " + ", ".join(info) if info else ""
        print(f"  {status} {o.order_id:<14s}{suffix}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="buyee",
        description="Auto-download invoices from your Buyee account.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Interactive login (run once, saves cookies)")\
        .set_defaults(func=cmd_login)

    sub.add_parser("status", help="Check if saved session is still valid")\
        .set_defaults(func=cmd_status)

    ps = sub.add_parser("sync", help="Discover and download new invoices")
    ps.add_argument("--max-pages", type=int, default=10,
                    help="Stop after N pagination pages (default: 10)")
    ps.add_argument("--dry-run", action="store_true",
                    help="Discover only — don't download PDFs")
    ps.set_defaults(func=cmd_sync)

    sub.add_parser("setup", help="Configure the Telegram bot (one-time wizard)")\
        .set_defaults(func=cmd_setup)

    pli = sub.add_parser("listen", help="Run the Telegram trigger listener (blocks)")
    pli.add_argument("--poll-timeout", type=int, default=30,
                     help="Seconds Telegram holds the long-poll open (default: 30)")
    pli.set_defaults(func=cmd_listen)

    sub.add_parser("freshness", help="Print last-sync info (used by launchd jobs)")\
        .set_defaults(func=cmd_freshness)

    pn = sub.add_parser("notes-digest",
                        help="Send a Telegram digest of stale pending feedback notes")
    pn.add_argument("--threshold", type=int, default=2,
                    help="Minimum age in days to surface a pending note (default: 2)")
    pn.add_argument("--dry-run", action="store_true",
                    help="Print to stdout instead of sending to Telegram")
    pn.add_argument("--always", action="store_true",
                    help="Send even when there are no stale notes (default: skip)")
    pn.set_defaults(func=cmd_notes_digest)

    psu = sub.add_parser(
        "scrape-urls",
        help="Walk baggage detail pages, cache (source_id → btob URL) pairs "
             "for LuxeWholesale V-prefix items. Populates the Pricing-tab "
             "Auction column for V-prefix items.",
    )
    psu.add_argument("--order", action="append", default=None,
                     help="Specific baggage ID to scrape (W…); repeat to "
                          "list more. Default: scrape every indexed baggage.")
    psu.add_argument("--all", action="store_true",
                     help="Force re-scrape every baggage (ignores any "
                          "fully-cached optimization).")
    psu.add_argument("--headed", action="store_true",
                     help="Run Playwright with a visible browser window — "
                          "useful for debugging Buyee's auth challenges.")
    psu.set_defaults(func=cmd_scrape_urls)

    pp = sub.add_parser("photos", help="Scrape first-photo thumbnails from Buyee auction pages")
    pp.add_argument("invoice", help="Path to a transcribed invoice JSON")
    pp.add_argument("--only-ids", help="Comma-separated source_ids to limit to")
    pp.add_argument("--overwrite", action="store_true", help="Re-fetch even if cached")
    pp.set_defaults(func=cmd_photos)

    pe = sub.add_parser("enrich", help="Run web_search + photo-vision title enrichment on an invoice")
    pe.add_argument("invoice", help="Path to a transcribed invoice JSON (e.g. output/foo.json)")
    pe.add_argument("--only-ids", help="Comma-separated source_ids to limit to (else all items)")
    pe.add_argument("--dry-run", action="store_true", help="Don't write enriched fields back to JSON")
    pe.add_argument("--max-cost", type=float, default=5.0,
                    help="Cost ceiling in USD; stop enriching once exceeded (default: 5.0)")
    pe.set_defaults(func=cmd_enrich)

    pl = sub.add_parser("list", help="Show indexed orders")
    g = pl.add_mutually_exclusive_group()
    g.add_argument("--pending", action="store_true", help="Only orders not yet downloaded")
    g.add_argument("--downloaded", action="store_true", help="Only completed downloads")
    pl.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
