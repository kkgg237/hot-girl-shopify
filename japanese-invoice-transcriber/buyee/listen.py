"""Telegram long-poll listener — your phone is the universal control surface.

Architecture:
  1. You create a Telegram bot via @BotFather (one-time, 2 minutes)
  2. This script runs on your laptop, long-polls Telegram for messages + files
  3. Two trigger modes:
     a) Text command: "sync" → runs Buyee scraper, downloads new invoices
     b) PDF upload: forward/share any invoice PDF to the bot → laptop saves
        it to inputs/telegram/, transcribes it, replies with summary
  4. The first chat_id that messages the bot is auto-authorized; subsequent
     messages from other chats are ignored

Why long-polling vs webhook: long-polling means YOUR LAPTOP reaches out to
Telegram. No public IP, no ngrok, no firewall changes — it just works.

Text commands:
  sync           Run a full Buyee sync (download new invoices)
  status         Last sync time + counts
  help           List commands

File uploads (any source):
  Just send/forward a PDF — bot detects it and transcribes automatically.
  Supports Buyee invoices, Brand Street, Mercari, or any other format the
  LLM can interpret.
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
from pathlib import Path
from typing import Optional

from .config import BuyeeConfig, load_config, save_config


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
INPUTS_DIR = PROJECT_ROOT / "inputs" / "telegram"
OUTPUT_DIR = PROJECT_ROOT / "output"


# Force-load .env with override=True. The sandbox / launchd env may pre-set
# ANTHROPIC_API_KEY="" which would otherwise prevent our real key from
# loading when transcribe.py is imported.
try:
    from dotenv import find_dotenv, load_dotenv
    _dotenv = find_dotenv(usecwd=True) or str(PROJECT_ROOT / ".env")
    load_dotenv(_dotenv, override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Telegram HTTP helpers — stdlib only, no SDK needed
# ---------------------------------------------------------------------------

def _api_call(token: str, method: str, timeout: int = 60, **params) -> dict:
    """Call a Telegram Bot API method. Returns the parsed JSON response."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(token: str, chat_id: int, text: str) -> Optional[dict]:
    """Send a message to a chat. Truncates to Telegram's 4096-char limit."""
    text = text[:4000]
    try:
        return _api_call(token, "sendMessage", chat_id=chat_id, text=text)
    except Exception as e:
        print(f"[listen] sendMessage failed: {e}")
        return None


def get_updates(token: str, offset: int = 0, timeout: int = 30) -> list[dict]:
    """Long-poll for new messages. Returns the result array."""
    try:
        resp = _api_call(token, "getUpdates", offset=offset, timeout=timeout,
                         _timeout=timeout + 10)
        return resp.get("result", []) if resp.get("ok") else []
    except urllib.error.URLError as e:
        # Network blip — caller will sleep and retry
        print(f"[listen] getUpdates network error: {e}")
        return []
    except Exception as e:
        print(f"[listen] getUpdates failed: {e}")
        return []


def get_file_path(token: str, file_id: str) -> Optional[str]:
    """Resolve a Telegram file_id to its temporary download path.

    Telegram's API uses a two-step file download: getFile returns a relative
    path, then we fetch from the file CDN URL.
    """
    try:
        resp = _api_call(token, "getFile", file_id=file_id, _timeout=15)
        if resp.get("ok") and resp.get("result"):
            return resp["result"].get("file_path")
    except Exception as e:
        print(f"[listen] getFile failed: {e}")
    return None


def download_file(token: str, file_path: str, dest: "Path") -> bool:
    """Download a file from Telegram's CDN to a local path.

    file_path comes from get_file_path(); the full URL is
    https://api.telegram.org/file/bot<token>/<file_path>.
    """
    from pathlib import Path as _Path
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        _Path(dest).parent.mkdir(parents=True, exist_ok=True)
        _Path(dest).write_bytes(data)
        return True
    except Exception as e:
        print(f"[listen] downloadFile failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Command handlers — what to do when the user sends a known message
# ---------------------------------------------------------------------------

def _handle_sync(cfg: BuyeeConfig, chat_id: int, max_pages: int = 5) -> None:
    """Run a Buyee sync and report the result."""
    from .scraper import sync_invoices

    send_message(cfg.telegram_token, chat_id, "🔄 Starting Buyee sync...")
    try:
        stats = sync_invoices(max_pages=max_pages, dry_run=False)
    except FileNotFoundError as e:
        send_message(cfg.telegram_token, chat_id,
                     f"✗ Buyee session not configured.\n{e}")
        return
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"✗ Sync failed:\n{e}")
        return

    msg_lines = [
        "✓ Sync complete",
        f"  Pages visited:    {stats['pages_visited']}",
        f"  Orders seen:      {stats['seen']}",
        f"  New to index:     {stats['new']}",
        f"  Downloaded:       {stats['downloaded']}",
        f"  Errors:           {stats['errors']}",
    ]
    if stats["downloaded"]:
        msg_lines.append(f"\n📦 {stats['downloaded']} new PDF(s) saved to inputs/buyee/.")
        # Surface the item-URL cache refresh result — this is the V-prefix
        # LuxeWholesale URL cache that powers the Pricing-tab Auction
        # column AND the V-prefix photo scraper. Only refreshed on syncs
        # that pulled new invoices, so seeing a count > 0 here means the
        # new invoices' items will get clickable auction links + photos
        # once you transcribe them.
        if stats.get("item_urls_new"):
            msg_lines.append(
                f"🔗 {stats['item_urls_new']} new item URL(s) cached for the "
                f"new invoices' V-prefix items."
            )
        msg_lines.append("Open the Streamlit app to QA + transcribe.")
    elif stats["new"] == 0:
        msg_lines.append("\nNothing new since last check.")
    send_message(cfg.telegram_token, chat_id, "\n".join(msg_lines))


def _handle_status(cfg: BuyeeConfig, chat_id: int) -> None:
    from .index import OrderIndex, hours_since_last_sync, humanize_freshness, load_meta

    idx = OrderIndex()
    meta = load_meta()
    h = hours_since_last_sync()

    lines = [
        "📦 Buyee status",
        f"  Total indexed:  {len(idx)}",
        f"  Downloaded:     {len(idx.downloaded())}",
        f"  Pending:        {len(idx.pending())}",
        f"  Last sync:      {humanize_freshness(h)}",
        f"  Sync count:     {meta.sync_count}",
    ]
    if meta.last_sync_errors:
        lines.append(f"  ⚠ Last sync had {meta.last_sync_errors} error(s)")
    send_message(cfg.telegram_token, chat_id, "\n".join(lines))


def _handle_help(cfg: BuyeeConfig, chat_id: int) -> None:
    send_message(cfg.telegram_token, chat_id, (
        "📦 Invoice bot commands:\n\n"
        "Text commands:\n"
        "  sync     — download new invoices from Buyee account\n"
        "  status   — last sync time + counts\n"
        "  help     — show this message\n\n"
        "File uploads:\n"
        "  Send/forward any invoice PDF and I'll transcribe it automatically.\n"
        "  Works for Buyee, Brand Street, Mercari, or any other format."
    ))


# ---------------------------------------------------------------------------
# Document handler — receive PDF uploads, transcribe, reply with summary
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Strip path components and unsafe chars from a Telegram-supplied filename."""
    base = Path(name).name  # remove any directory
    cleaned = re.sub(r"[^A-Za-z0-9._\- ]+", "_", base).strip()
    return cleaned or "invoice.pdf"


def _handle_document(cfg: BuyeeConfig, chat_id: int, doc: dict) -> None:
    """User uploaded a file — download, transcribe, reply with summary."""
    token = cfg.telegram_token
    file_id = doc.get("file_id")
    file_name = doc.get("file_name") or "invoice.pdf"
    mime = doc.get("mime_type") or ""
    size = doc.get("file_size", 0)

    # Filter to PDFs only
    if mime != "application/pdf" and not file_name.lower().endswith(".pdf"):
        send_message(token, chat_id, (
            f"⚠ I can only transcribe PDFs.\n"
            f"Got: {file_name} ({mime or 'unknown type'})\n"
            f"Send/forward a PDF invoice and I'll process it."
        ))
        return

    # Telegram has a 20MB inbound limit for bots; warn if over
    if size > 20 * 1024 * 1024:
        send_message(token, chat_id, f"⚠ File is {size/1024/1024:.1f}MB; bots are limited to 20MB. Try compressing.")
        return

    send_message(token, chat_id, f"📥 Got `{file_name}` ({size//1024} KB). Downloading...")

    # Step 1: get the file path from Telegram
    file_path = get_file_path(token, file_id)
    if not file_path:
        send_message(token, chat_id, "✗ Couldn't resolve the file with Telegram. Try sending again.")
        return

    # Step 2: download to inputs/telegram/<timestamp>_<safe-name>
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_name = _safe_filename(file_name)
    local_pdf = INPUTS_DIR / f"{timestamp}_{safe_name}"
    if not download_file(token, file_path, local_pdf):
        send_message(token, chat_id, "✗ Download failed. Try again or check the listener log.")
        return

    send_message(token, chat_id, f"✓ Saved to `inputs/telegram/{local_pdf.name}`. Transcribing now (60-120s)…")

    # Step 3: transcribe via the existing pipeline. The LLM auto-detects source.
    try:
        import anthropic
        # Re-load .env right here in case the listener was started in an env
        # where ANTHROPIC_API_KEY was unset/empty. Override always wins.
        try:
            from dotenv import find_dotenv as _fd, load_dotenv as _ld
            _ld(_fd(usecwd=True) or str(PROJECT_ROOT / ".env"), override=True)
        except ImportError:
            pass
        if not os.environ.get("ANTHROPIC_API_KEY"):
            send_message(token, chat_id, (
                "✗ `ANTHROPIC_API_KEY` not set. Add it to `.env` in the project root, "
                "then restart the listener."
            ))
            return
        from transcribe import transcribe as transcribe_pdf

        client = anthropic.Anthropic(timeout=180.0, max_retries=2)
        invoice = transcribe_pdf(local_pdf, client)
    except ImportError as e:
        send_message(token, chat_id, f"✗ Transcribe module not importable: {e}")
        return
    except Exception as e:
        send_message(token, chat_id, f"✗ Transcribe failed: {e}\nThe PDF is saved at `{local_pdf.name}` — open the app to retry manually.")
        return

    # Step 4: persist the JSON to output/<safe-name>.json
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUTPUT_DIR / (Path(safe_name).stem + ".json")
    try:
        data = invoice.model_dump(mode="json")
        out_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        send_message(token, chat_id, f"✓ Transcribed but failed to save JSON: {e}")
        return

    # Step 4b: auto-fetch photos (best-effort) for items whose source_id is
    # a Yahoo Auctions ID. Single photo per item (first one). Skipped silently
    # if no eligible items or photo scraper isn't configured.
    photos_summary = ""
    try:
        from .photo_scraper import fetch_invoice_photos, is_eligible
        eligible = sum(1 for it in data.get("items", []) if is_eligible(it.get("source_id")))
        if eligible:
            send_message(token, chat_id, f"📷 Fetching photos for {eligible} eligible item(s)...")
            photo_stats = fetch_invoice_photos(out_json)
            photos_summary = (
                f"  Photos:    {photo_stats['downloaded']} fetched, "
                f"{photo_stats['skipped_existing']} cached, "
                f"{photo_stats['skipped_ineligible']} ineligible"
            )
            _log_action(
                "auto_photos", invoice=out_json.name,
                downloaded=photo_stats.get("downloaded"),
                eligible=photo_stats.get("eligible"),
                errors=photo_stats.get("errors"),
            )
    except Exception as e:
        # Don't block the transcription summary if photo scrape fails
        photos_summary = f"  Photos:    skipped ({e})"

    # Step 5: reply with summary
    item_count = len(invoice.items)
    vendor = invoice.vendor_name or "Unknown vendor"
    inv_type = invoice.invoice_type or "unknown"
    inv_date = invoice.invoice_date or "?"
    currency = invoice.currency or "?"

    # Best-effort cost summary via InvoiceView
    cost_lines: list[str] = []
    try:
        from costs import InvoiceView
        view = InvoiceView(invoice)
        recon = view.reconciliation()
        cost_lines.append(f"Item subtotal: {currency} {recon.get('items_total', 0):,.0f}")
        if recon.get("landed_usd_sum"):
            cost_lines.append(f"Landed total: ${recon['landed_usd_sum']:,.0f} USD")
    except Exception:
        pass

    summary_lines = [
        f"✅ Transcribed `{safe_name}`",
        "",
        f"  Vendor:    {vendor}",
        f"  Type:      {inv_type}",
        f"  Date:      {inv_date}",
        f"  Items:     {item_count}",
    ]
    if cost_lines:
        summary_lines.extend(["", *("  " + l for l in cost_lines)])
    if photos_summary:
        summary_lines.append(photos_summary)
    summary_lines.extend([
        "",
        f"Saved to `output/{out_json.name}`.",
        "Open the Streamlit app to QA + price + export.",
    ])
    send_message(token, chat_id, "\n".join(summary_lines))


# ---------------------------------------------------------------------------
# Session log — every bot interaction goes here so Claude Code on the laptop
# can review what happened while the user was away. JSONL format.
# ---------------------------------------------------------------------------

SESSION_LOG_PATH = PROJECT_ROOT / "buyee" / "state" / "telegram_log.jsonl"


def _log_action(kind: str, **fields) -> None:
    """Append a structured line to the Telegram session log."""
    SESSION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        **fields,
    }
    with SESSION_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Helpers — find the latest invoice, locate items, save edits
# ---------------------------------------------------------------------------

def _latest_invoice_path() -> Optional[Path]:
    """The most recently modified invoice JSON in output/. Prefers edited_*."""
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load_invoice_dict(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_invoice_dict(path: Path, data: dict) -> Path:
    """Save edits to edited_<stem>.json (mirrors persist_invoice in app.py).

    Returns the actual file path written.
    """
    target_name = path.name if path.name.startswith("edited_") else f"edited_{path.name}"
    target_path = OUTPUT_DIR / target_name
    target_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return target_path


def _find_item_in_data(data: dict, source_id: str) -> Optional[dict]:
    for it in data.get("items", []):
        if it.get("source_id") == source_id:
            return it
    return None


def _find_item_across_invoices(source_id: str) -> tuple[Optional[Path], Optional[dict]]:
    """Search every invoice (newest first) for an item with this source_id.

    Returns (invoice_path, invoice_dict) when found, else (None, None).
    """
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        data = _load_invoice_dict(f)
        if data and _find_item_in_data(data, source_id):
            return f, data
    return None, None


# ---------------------------------------------------------------------------
# Read-only commands: pending, latest, tax (show), history
# ---------------------------------------------------------------------------

def _handle_pending(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """List invoices in output/ that haven't been edited yet."""
    files = sorted(OUTPUT_DIR.glob("*.json"))
    originals = [f for f in files if not f.name.startswith("edited_")]
    edited_set = {f.name[len("edited_"):] for f in files if f.name.startswith("edited_")}
    pending = [f for f in originals if f.name not in edited_set]
    if not pending:
        send_message(cfg.telegram_token, chat_id, "✓ No pending invoices — all originals have edited copies.")
    else:
        lines = ["🔍 Invoices pending QA:"]
        for f in pending[:15]:
            data = _load_invoice_dict(f) or {}
            n = len(data.get("items", []))
            vendor = data.get("vendor_name") or "?"
            date = data.get("invoice_date") or "?"
            lines.append(f"  · {f.stem}  ({vendor} · {date} · {n} items)")
        if len(pending) > 15:
            lines.append(f"  ... +{len(pending) - 15} more")
        send_message(cfg.telegram_token, chat_id, "\n".join(lines))


def _handle_latest(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """Stats of the most recent invoice."""
    p = _latest_invoice_path()
    if not p:
        send_message(cfg.telegram_token, chat_id, "No invoices in output/.")
        return
    data = _load_invoice_dict(p) or {}
    items = data.get("items", [])
    n = len(items)
    n_overrides = sum(1 for it in items if it.get("override_title") or it.get("override_price"))
    lines = [
        f"📄 Latest: `{p.name}`",
        f"  Vendor:    {data.get('vendor_name', '?')}",
        f"  Type:      {data.get('invoice_type', '?')}",
        f"  Date:      {data.get('invoice_date', '?')}",
        f"  Items:     {n}",
        f"  Overrides: {n_overrides}",
        f"  Currency:  {data.get('currency', '?')}",
    ]
    if data.get("commission_line"):
        rate = data.get("commission_line_rate")
        rate_str = f"{rate*100:.0f}%" if rate else "lump"
        lines.append(f"  Commission ({rate_str}): {data['commission_line']:,.0f}")
    send_message(cfg.telegram_token, chat_id, "\n".join(lines))


def _handle_tax(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`tax` shows; `tax <which> <value>` sets a rate or extra on the latest invoice.

    Supported:
      tax                       — show all current values
      tax handling <pct>        — set handling rate (vendor invoices)
      tax import <pct>          — set import tax rate (vendor invoices)
      tax extra <pct>           — set ad-hoc extra %
      tax flat <amount>         — set lump-sum extra in invoice currency
    """
    from costs import HANDLING_RATE as _DEF_H, IMPORT_TAX_RATE as _DEF_I

    parts = args.strip().split()
    p = _latest_invoice_path()

    if not parts:
        data = _load_invoice_dict(p) if p else {}
        h = (data or {}).get("_bot_handling_rate", _DEF_H)
        i = (data or {}).get("_bot_import_tax_rate", _DEF_I)
        ex_rate = (data or {}).get("_bot_extra_rate", 0.0)
        ex_flat = (data or {}).get("_bot_extra_flat", 0.0)
        ccy = (data or {}).get("currency", "?")
        lines = [
            "💰 Current rates + extras",
            f"  Handling:    {h*100:.0f}%  (default {_DEF_H*100:.0f}%)",
            f"  Import tax:  {i*100:.0f}%  (default {_DEF_I*100:.0f}%)",
            f"  Extra %:     {ex_rate*100:.1f}%",
            f"  Extra flat:  {ex_flat:,.2f} {ccy}",
        ]
        if p:
            lines.append(f"  Applied to:  `{p.name}`")
        lines.append("\nSet with: `tax handling 12` / `tax import 18` / "
                     "`tax extra 5` / `tax flat 200`")
        send_message(cfg.telegram_token, chat_id, "\n".join(lines))
        return

    if len(parts) < 2:
        send_message(cfg.telegram_token, chat_id,
                     "Usage: `tax handling 12` / `tax import 18` / "
                     "`tax extra 5` / `tax flat 200`")
        return

    which, value_str = parts[0].lower(), parts[1]
    rate_field_map = {
        "handling": "_bot_handling_rate",
        "import":   "_bot_import_tax_rate",
        "extra":    "_bot_extra_rate",
    }
    flat_field_map = {
        "flat": "_bot_extra_flat",
    }
    if which not in rate_field_map and which not in flat_field_map:
        send_message(cfg.telegram_token, chat_id,
                     f"Unknown: {which!r}. Use `handling` / `import` / `extra` / `flat`.")
        return

    if not p:
        send_message(cfg.telegram_token, chat_id, "No invoice loaded; nothing to apply to.")
        return
    data = _load_invoice_dict(p) or {}

    if which in rate_field_map:
        try:
            pct = float(value_str.rstrip("%"))
            rate = pct / 100 if pct > 1 else pct
        except ValueError:
            send_message(cfg.telegram_token, chat_id, f"Couldn't parse {value_str!r} as a number.")
            return
        old = data.get(rate_field_map[which], 0.0 if which == "extra" else
                       (_DEF_H if which == "handling" else _DEF_I))
        data[rate_field_map[which]] = rate
        saved = _save_invoice_dict(p, data)
        _log_action("rate_set", which=which, old=old, new=rate, invoice=saved.name)
        send_message(cfg.telegram_token, chat_id, (
            f"✓ Set {which} rate from {old*100:.1f}% → {rate*100:.1f}%\n"
            f"  Saved to `{saved.name}`. Reload the app to see updated cost."
        ))
    else:  # flat
        try:
            amount = float(value_str.replace(",", ""))
        except ValueError:
            send_message(cfg.telegram_token, chat_id, f"Couldn't parse {value_str!r} as a number.")
            return
        old = data.get(flat_field_map[which], 0.0)
        data[flat_field_map[which]] = amount
        saved = _save_invoice_dict(p, data)
        ccy = data.get("currency", "?")
        _log_action("flat_set", which=which, old=old, new=amount, invoice=saved.name)
        send_message(cfg.telegram_token, chat_id, (
            f"✓ Set extra flat from {old:,.2f} → {amount:,.2f} {ccy}\n"
            f"  Saved to `{saved.name}`. Reload the app to see updated cost."
        ))


def _handle_notes(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`notes` shows stale pending notes; `notes all` shows all pending.

    By default we surface notes >= 2 days old, since fresh ones don't need
    a reminder. `notes all` ignores the staleness filter.
    `notes 5` uses a 5-day threshold instead.
    """
    try:
        from heuristics import (
            load_feedback, stale_pending_notes, format_digest, mark_reminded,
        )
    except ImportError as e:
        send_message(cfg.telegram_token, chat_id, f"Couldn't load heuristics: {e}")
        return

    arg = args.strip().lower()
    if arg == "all":
        notes = [n for n in load_feedback() if n.status == "pending"]
        notes.sort(key=lambda n: n.date)
        header = "📝 All pending notes"
    else:
        threshold = 2
        if arg.isdigit():
            threshold = int(arg)
        notes = stale_pending_notes(threshold_days=threshold)
        header = f"📝 Stale pending notes (>{threshold-1}d old)" if threshold > 1 else "📝 Pending notes"

    digest = format_digest(notes, header=header)
    send_message(cfg.telegram_token, chat_id, digest)
    if notes:
        mark_reminded([n.id for n in notes])
        _log_action("notes_digest", count=len(notes), threshold=threshold if arg != "all" else "all")


# ---------------------------------------------------------------------------
# Manual entry — `add` (single item) and `invoice` (multi-item) commands
# Plus auto-detection when a free-form message looks like a purchase listing.
# ---------------------------------------------------------------------------

def _handle_add(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`add <description> <price>` — add a single item to the manual invoice."""
    text = args.strip()
    if not text:
        send_message(cfg.telegram_token, chat_id, (
            "Usage: `add <description> <price>`\n\n"
            "Examples:\n"
            "  add Borsa di pelle Gucci nera vintage 250 EUR\n"
            "  add Vintage Burberry trench coat 800€\n"
            "  add Cappotto Prada anni '90 600 euro\n\n"
            "Description can be in Italian, English, French, Japanese, etc.\n"
            "Currency is auto-detected (€/EUR, $/USD, £/GBP, ¥/JPY)."
        ))
        return

    send_message(cfg.telegram_token, chat_id, "🔍 Parsing item...")
    try:
        from .manual_entry import process_add_message
        result = process_add_message(text)
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"✗ Crashed: {e}")
        return

    if not result.get("ok"):
        send_message(cfg.telegram_token, chat_id, f"✗ {result.get('error', 'unknown error')}")
        return

    item = result["item"]
    msg = [
        f"✓ Added item",
        f"  {item.get('description_english', item.get('description_original', ''))}",
        "",
        f"  Brand:    {item.get('detected_brand') or '(none detected)'}",
        f"  Type:     {item.get('product_type') or '?'}",
    ]
    for f in ("color", "material", "pattern", "era"):
        v = item.get(f)
        if v:
            msg.append(f"  {f.title():10s}{v}")
    msg.append("")
    msg.append(f"  Price:    {result['price']:,.2f} {result['currency']}")
    msg.append(f"  Saved → `{Path(result['invoice_path']).name}`")
    send_message(cfg.telegram_token, chat_id, "\n".join(msg))
    _log_action("add", source_id=item.get("source_id"),
                price=result["price"], currency=result["currency"],
                invoice=Path(result["invoice_path"]).name)


def _handle_invoice(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`invoice <multi-item text>` — parse a vendor's multi-item message."""
    text = args.strip()
    if not text:
        send_message(cfg.telegram_token, chat_id, (
            "Usage: `invoice <multi-item text>`\n\n"
            "Forward a vendor's purchase message — Italian / English / Japanese / etc. — "
            "with section headers (`2 giacche`), prices (`€220`), and totals.\n"
            "I'll parse it into a structured invoice that flows through the rest of the pipeline."
        ))
        return

    send_message(cfg.telegram_token, chat_id, "🔍 Parsing multi-item invoice...")
    try:
        from .manual_entry import process_multi_item_message
        result = process_multi_item_message(text)
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"✗ Crashed: {e}")
        return

    if not result.get("ok"):
        send_message(cfg.telegram_token, chat_id, f"✗ {result.get('error', 'unknown error')}")
        return

    msg = [
        f"✓ Parsed {result['items_count']} items",
        "",
        f"  Currency:        {result['currency']}",
        f"  Listed subtotal: {result['listed_subtotal']:,.2f}",
    ]
    if result.get("discount"):
        msg.append(f"  Discount:        −{result['discount']:,.2f}")
    msg.append(f"  Final total:     {result['final_total']:,.2f}")
    if result.get("notes"):
        msg.append(f"  Notes: {result['notes']}")
    msg.append("")
    msg.append(f"  Saved → `{Path(result['invoice_path']).name}`")
    msg.append("Open the app to QA + price + export.")
    send_message(cfg.telegram_token, chat_id, "\n".join(msg))
    _log_action("manual_invoice", currency=result["currency"],
                items=result["items_count"], discount=result.get("discount", 0),
                total=result["final_total"],
                invoice=Path(result["invoice_path"]).name)


# Heuristic: does this look like a free-form purchase message?
# True if it has a currency marker AND at least one numeric token nearby.
_CURRENCY_HINT_RE = re.compile(
    r"(€|EUR\b|euros?|\$|USD\b|dollars?|£|GBP\b|pounds?|¥|JPY\b|yen)\s*\d|"
    r"\d+\s*(€|EUR\b|euros?|\$|USD\b|£|GBP\b|¥|JPY\b|yen)",
    re.IGNORECASE,
)


def _looks_like_purchase_message(text: str) -> bool:
    return bool(_CURRENCY_HINT_RE.search(text or ""))


def _handle_smart_entry(cfg: BuyeeConfig, chat_id: int, text: str) -> None:
    """Free-form auto-route: pick `add` or `invoice` based on shape.

    - 0-1 price markers → single-item `add` flow
    - 2+ price markers, OR multi-line message → `invoice` flow
    """
    price_count = len(re.findall(r"\d+\s*(?:€|EUR|euros?|\$|USD|£|GBP|¥|JPY|yen)", text, re.IGNORECASE))
    line_count = sum(1 for ln in text.splitlines() if ln.strip())
    if price_count >= 2 or line_count >= 3:
        _handle_invoice(cfg, chat_id, text)
    else:
        _handle_add(cfg, chat_id, text)


def _handle_history(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """Show last 10 entries from the session log."""
    if not SESSION_LOG_PATH.exists():
        send_message(cfg.telegram_token, chat_id, "No history yet.")
        return
    try:
        lines = SESSION_LOG_PATH.read_text(encoding="utf-8").splitlines()[-10:]
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"Couldn't read history: {e}")
        return
    if not lines:
        send_message(cfg.telegram_token, chat_id, "No history yet.")
        return
    out = ["📜 Last 10 actions:"]
    for raw in lines:
        try:
            entry = json.loads(raw)
            ts = entry.get("ts", "?")[5:16]  # MM-DD HH:MM
            kind = entry.get("kind", "?")
            # Build a short summary based on kind
            if kind == "rate_set":
                detail = f"{entry.get('which')} {entry.get('old',0)*100:.0f}%→{entry.get('new',0)*100:.0f}%"
            elif kind == "override":
                detail = f"{entry.get('field')} on {entry.get('source_id')}"
            elif kind == "transcribe":
                detail = entry.get("file", "?")
            else:
                detail = ""
            out.append(f"  {ts}  {kind}  {detail}")
        except Exception:
            continue
    send_message(cfg.telegram_token, chat_id, "\n".join(out))


# ---------------------------------------------------------------------------
# Override commands: title, price, vendor (per-item edits)
# ---------------------------------------------------------------------------

def _set_item_override(
    cfg: BuyeeConfig, chat_id: int, source_id: str, field: str, value, label: str,
) -> None:
    """Generic per-item override applier. Field must be one of override_*."""
    invoice_path, data = _find_item_across_invoices(source_id)
    if not data:
        send_message(cfg.telegram_token, chat_id,
                     f"Source ID `{source_id}` not found in any invoice.")
        return
    item = _find_item_in_data(data, source_id)
    old = item.get(field)
    item[field] = value
    saved = _save_invoice_dict(invoice_path, data)
    _log_action("override", field=field, source_id=source_id,
                old=old, new=value, invoice=saved.name)
    send_message(cfg.telegram_token, chat_id, (
        f"✓ Set {label} on `{source_id}`\n"
        f"  Was:  {old or '(unset)'}\n"
        f"  Now:  {value}\n"
        f"  Saved → `{saved.name}`"
    ))


def _handle_title(cfg: BuyeeConfig, chat_id: int, args: str) -> None:
    """`title <source_id> <new title>` — set override_title."""
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        send_message(cfg.telegram_token, chat_id,
                     "Usage: `title <source_id> <new title>`\n"
                     "Example: `title V26031300018 90's Fendi Zucca Cotton Shirt`")
        return
    source_id, new_title = parts[0], parts[1].strip()
    _set_item_override(cfg, chat_id, source_id, "override_title", new_title, "title")


def _handle_price(cfg: BuyeeConfig, chat_id: int, args: str) -> None:
    """`price <source_id> <amount>` — set override_price (USD integer)."""
    parts = args.strip().split()
    if len(parts) < 2:
        send_message(cfg.telegram_token, chat_id,
                     "Usage: `price <source_id> <amount>`\nExample: `price c1221895009 425`")
        return
    source_id = parts[0]
    try:
        amount = int(float(parts[1].lstrip("$")))
    except ValueError:
        send_message(cfg.telegram_token, chat_id, f"Couldn't parse {parts[1]!r} as a number.")
        return
    if amount <= 0:
        send_message(cfg.telegram_token, chat_id, "Price must be > 0.")
        return
    _set_item_override(cfg, chat_id, source_id, "override_price", amount, f"price → ${amount}")


def _handle_vendor(cfg: BuyeeConfig, chat_id: int, args: str) -> None:
    """`vendor <source_id> <name>` — set override_vendor."""
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        send_message(cfg.telegram_token, chat_id,
                     "Usage: `vendor <source_id> <vendor>`\n"
                     "Example: `vendor c1221895009 Burberry`")
        return
    source_id, name = parts[0], parts[1].strip()
    _set_item_override(cfg, chat_id, source_id, "override_vendor", name, "vendor")


# ---------------------------------------------------------------------------
# Trigger commands: enrich, photos
# ---------------------------------------------------------------------------

def _handle_enrich(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`enrich` runs on latest invoice; `enrich <source_id>` does one item."""
    p = _latest_invoice_path()
    if not p:
        send_message(cfg.telegram_token, chat_id, "No invoice in output/.")
        return

    only_ids = None
    target = args.strip()
    if target and target != "latest":
        only_ids = [target]

    send_message(cfg.telegram_token, chat_id, (
        f"🔍 Enriching `{p.name}`"
        + (f" — only `{target}`" if only_ids else " — all weak items")
        + "...\nThis takes ~5-30s per item."
    ))
    try:
        from buyee.research import enrich_invoice
        stats = enrich_invoice(p, only_ids=only_ids, dry_run=False, max_cost_usd=2.50)
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"✗ Enrichment crashed: {e}")
        return

    _log_action("enrich", invoice=p.name, only_ids=only_ids,
                processed=stats.get("items_processed"), enriched=stats.get("items_enriched"),
                cost=stats.get("total_cost_usd"))
    send_message(cfg.telegram_token, chat_id, (
        f"✓ Enrichment done\n"
        f"  Processed:  {stats['items_processed']}\n"
        f"  Enriched:   {stats['items_enriched']}\n"
        f"  Cost:       ${stats['total_cost_usd']:.4f}"
    ))


def _handle_photos(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    """`photos` — fetch first auction photo for each lowercase-prefix item."""
    p = _latest_invoice_path()
    if not p:
        send_message(cfg.telegram_token, chat_id, "No invoice in output/.")
        return
    send_message(cfg.telegram_token, chat_id, f"📷 Scraping photos for `{p.name}`...")
    try:
        from buyee.photo_scraper import fetch_invoice_photos
        stats = fetch_invoice_photos(p)
    except Exception as e:
        send_message(cfg.telegram_token, chat_id, f"✗ Photo scrape crashed: {e}")
        return
    _log_action("photos", invoice=p.name,
                downloaded=stats.get("downloaded"),
                eligible=stats.get("eligible"),
                errors=stats.get("errors"))
    send_message(cfg.telegram_token, chat_id, (
        f"✓ Photos done\n"
        f"  Eligible:   {stats['eligible']}\n"
        f"  Downloaded: {stats['downloaded']}\n"
        f"  Cached:     {stats['skipped_existing']}\n"
        f"  Errors:     {stats['errors']}"
    ))


# ---------------------------------------------------------------------------
# Updated help message
# ---------------------------------------------------------------------------

def _handle_help_v2(cfg: BuyeeConfig, chat_id: int, args: str = "") -> None:
    send_message(cfg.telegram_token, chat_id, (
        "📦 Invoice bot commands\n\n"
        "Sync & ingest:\n"
        "  sync                    — pull invoices from Buyee\n"
        "  status                  — last sync info\n"
        "  pending                 — invoices needing QA\n"
        "  latest                  — stats of most recent invoice\n"
        "  (or send any PDF and I'll transcribe it)\n\n"
        "Manual entry (any language):\n"
        "  add <text> <price>      — single item, e.g. `add Borsa Gucci 250 EUR`\n"
        "  invoice <text>          — multi-item, paste a vendor's purchase msg\n"
        "  (or just paste/forward — I'll auto-detect)\n\n"
        "Edits to latest invoice:\n"
        "  tax                     — show current rates + extras\n"
        "  tax handling 12         — set handling rate to 12%\n"
        "  tax import 18           — set import tax to 18%\n"
        "  tax extra 5             — set ad-hoc extra to 5%\n"
        "  tax flat 200            — set lump-sum extra (split per item)\n\n"
        "Per-item edits:\n"
        "  title <sid> <new>       — override title\n"
        "  price <sid> <amount>    — override price (USD)\n"
        "  vendor <sid> <name>     — override vendor\n\n"
        "Triggers:\n"
        "  enrich [sid|latest]     — web-search + photo enrichment\n"
        "  photos                  — fetch Buyee auction photos\n\n"
        "Notes & feedback:\n"
        "  notes                   — pending notes >2 days old\n"
        "  notes all               — every pending note\n"
        "  notes 5                 — pending notes >5 days old\n\n"
        "Infrastructure:\n"
        "  app                     — Streamlit + port + public URL health\n"
        "  app restart             — restart Streamlit (~5-10s downtime)\n"
        "  tunnel                  — Cloudflare tunnel state + pid\n"
        "  tunnel restart          — restart the public hostname\n"
        "  audit                   — last catalogue audit result\n"
        "  audit run               — trigger catalogue audit now (~30-60s)\n\n"
        "Misc:\n"
        "  history                 — last 10 bot actions\n"
        "  help                    — this message"
    ))


# ---------------------------------------------------------------------------
# Dispatcher — first word selects the handler, rest of message is args
# ---------------------------------------------------------------------------

# Maps the first whitespace-delimited word (lowercase, no slash) to a handler.
# Each handler takes (cfg, chat_id, args_string) where args_string is the
# rest of the message after the command name.
# ---------------------------------------------------------------------------
# Infrastructure controls — restart launchd-managed services from your phone
# ---------------------------------------------------------------------------

def _handle_tunnel(cfg: "BuyeeConfig", chat_id: int, args: str = "") -> None:
    """Status / restart for the Cloudflare Tunnel launchd agent.

    Subcommands:
        tunnel          → print state + last exit code (read-only, safe)
        tunnel status   → same as above
        tunnel restart  → launchctl kickstart -k (drops the tunnel briefly,
                          ~5 seconds before public hostname reconnects)
    """
    import os
    import subprocess

    LABEL = "com.paststudies.invoice.tunnel"
    uid = os.getuid()
    sub = (args or "").strip().lower() or "status"

    if sub == "restart":
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{LABEL}"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            send_message(cfg.telegram_token, chat_id,
                         f"✗ Tunnel restart crashed: {type(e).__name__}: {e}")
            return
        if result.returncode == 0:
            send_message(cfg.telegram_token, chat_id,
                f"🔄 Tunnel restarted.\n\n"
                f"Public hostname usually reconnects in ~5 seconds. "
                f"Try invoices.paststudies-tools.com in a moment."
            )
            _log_action("tunnel_restart", source="telegram")
        else:
            send_message(cfg.telegram_token, chat_id,
                f"✗ Tunnel restart failed (rc={result.returncode}):\n"
                f"{(result.stderr or result.stdout)[:300]}")
    elif sub in ("status", ""):
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{LABEL}"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as e:
            send_message(cfg.telegram_token, chat_id,
                         f"✗ Tunnel status crashed: {type(e).__name__}: {e}")
            return
        if result.returncode != 0:
            send_message(cfg.telegram_token, chat_id,
                f"✗ Couldn't read tunnel status — agent may not be loaded.\n"
                f"To install: `./launchd/install_tunnel.sh <hostname>`\n\n"
                f"{result.stderr[:200]}")
            return
        out = result.stdout
        state_m = re.search(r"state\s*=\s*(\S+)", out)
        exit_m = re.search(r"last exit code\s*=\s*(-?\d+)", out)
        pid_m = re.search(r"pid\s*=\s*(\d+)", out)
        state = state_m.group(1) if state_m else "?"
        send_message(cfg.telegram_token, chat_id,
            f"🌐 Cloudflare Tunnel · {LABEL}\n"
            f"  state: {state}\n"
            f"  last exit code: {exit_m.group(1) if exit_m else '?'}\n"
            f"  pid: {pid_m.group(1) if pid_m else '(not running)'}\n\n"
            f"Send `tunnel restart` to kick it."
        )
    else:
        send_message(cfg.telegram_token, chat_id,
            f"❓ Unknown tunnel subcommand: {sub!r}\n\n"
            f"Valid: `tunnel`, `tunnel status`, `tunnel restart`")


def _handle_app(cfg: "BuyeeConfig", chat_id: int, args: str = "") -> None:
    """Status / restart for the Streamlit invoice UI launchd agent.

    The launchd `state = running` flag only tells you the wrapper process
    is alive — it doesn't say whether anything is actually listening on
    :8501. So `app` here also probes the local port + the public URL
    behind the Cloudflare tunnel, which is what actually matters.

    Subcommands:
        app          → state + pid + port-listening + public URL reachability
        app status   → same as above
        app restart  → launchctl kickstart -k (full respawn; ~5s downtime)
    """
    import os
    import socket
    import subprocess
    import urllib.error
    import urllib.request

    LABEL = "com.paststudies.invoice.streamlit"
    PUBLIC_URL = "https://invoices.paststudies-tools.com/"
    LOCAL_PORT = 8501
    uid = os.getuid()
    sub = (args or "").strip().lower() or "status"

    if sub == "restart":
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/{LABEL}"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            send_message(cfg.telegram_token, chat_id,
                         f"✗ App restart crashed: {type(e).__name__}: {e}")
            return
        if result.returncode == 0:
            send_message(cfg.telegram_token, chat_id,
                f"🔄 Streamlit app restarted.\n\n"
                f"It usually takes ~5-10s to bind to port {LOCAL_PORT} "
                f"and the public URL to come back. "
                f"Send `app` in a moment to verify."
            )
            _log_action("app_restart", source="telegram")
        else:
            send_message(cfg.telegram_token, chat_id,
                f"✗ App restart failed (rc={result.returncode}):\n"
                f"{(result.stderr or result.stdout)[:300]}")
    elif sub in ("status", ""):
        # 1. Launchd agent state
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{LABEL}"],
                capture_output=True, text=True, timeout=10,
            )
            out = result.stdout if result.returncode == 0 else ""
        except Exception as e:
            out = ""
            err = f"{type(e).__name__}: {e}"
        state_m = re.search(r"state\s*=\s*(\S+)", out)
        pid_m = re.search(r"pid\s*=\s*(\d+)", out)
        agent_state = state_m.group(1) if state_m else "?"
        agent_pid = pid_m.group(1) if pid_m else "(not running)"

        # 2. Is anything actually listening on :8501?
        port_ok = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                port_ok = (s.connect_ex(("127.0.0.1", LOCAL_PORT)) == 0)
        except Exception:
            pass

        # 3. Does the public URL respond?
        public_status = "?"
        try:
            req = urllib.request.Request(PUBLIC_URL, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as r:
                public_status = f"HTTP {r.status}"
        except urllib.error.HTTPError as he:
            public_status = f"HTTP {he.code}"
        except Exception as e:
            public_status = f"unreachable ({type(e).__name__})"

        # Overall verdict
        if agent_state == "running" and port_ok and public_status == "HTTP 200":
            emoji = "✅"
            verdict = "all green"
        elif agent_state == "running" and not port_ok:
            emoji = "⚠️"
            verdict = "agent alive but port dead — send `app restart`"
        else:
            emoji = "⚠️"
            verdict = "degraded"

        send_message(cfg.telegram_token, chat_id,
            f"{emoji} Streamlit app · {LABEL}\n"
            f"  launchd state: {agent_state}  ·  pid: {agent_pid}\n"
            f"  port {LOCAL_PORT}: {'listening ✓' if port_ok else 'not listening ✗'}\n"
            f"  public url: {public_status}\n\n"
            f"{verdict}"
        )
    else:
        send_message(cfg.telegram_token, chat_id,
            f"❓ Unknown app subcommand: {sub!r}\n\n"
            f"Valid: `app`, `app status`, `app restart`")


def _handle_audit(cfg: "BuyeeConfig", chat_id: int, args: str = "") -> None:
    """On-demand catalogue audit (no-photo + wrong-vendor scan).

    Subcommands:
        audit         → status from the last run (read-only)
        audit status  → same as above
        audit run     → trigger the same audit launchd runs every Monday
                        9am, posts the digest to this chat when done
                        (~30-60 seconds total)
    """
    sub = (args or "").strip().lower() or "status"

    if sub == "run":
        send_message(cfg.telegram_token, chat_id,
                     "🔍 Running catalogue audit — usually 30-60 seconds…")
        try:
            from weekly_catalogue_audit import run_audit
            # run_audit() does the scan, formats the digest, sends to the
            # authorized chat itself, and writes a `weekly_audit` log entry.
            exit_code = run_audit(dry_run=False)
        except Exception as e:
            send_message(cfg.telegram_token, chat_id,
                         f"✗ Audit crashed: {type(e).__name__}: {e}")
            _log_action("audit_run", source="telegram", status="crash",
                        error=f"{type(e).__name__}: {e}")
            return
        _log_action("audit_run", source="telegram",
                    exit_code=int(exit_code) if exit_code is not None else None)
        if exit_code not in (0, None):
            send_message(cfg.telegram_token, chat_id,
                         f"⚠️ Audit finished with exit code {exit_code}. "
                         f"Check ~/Library/Logs/com.paststudies.weekly-audit.*.log.")

    elif sub in ("status", ""):
        # Find the most recent `weekly_audit` entry in the telegram log
        log_path = Path(__file__).resolve().parent.parent / "buyee" / "state" / "telegram_log.jsonl"
        last = None
        if log_path.exists():
            try:
                with log_path.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("kind") == "weekly_audit":
                            last = entry
            except OSError:
                pass
        if not last:
            send_message(cfg.telegram_token, chat_id,
                "📋 No catalogue audit runs logged yet.\n\n"
                "Send `audit run` to trigger one now (~30-60s).")
            return
        send_message(cfg.telegram_token, chat_id,
            f"📋 Last catalogue audit · {last.get('ts', '?')}\n"
            f"  status: {last.get('status', '?')}\n"
            f"  scanned: {last.get('scanned', 0):,}\n"
            f"  no photos: {last.get('no_photos', 0)}\n"
            f"  wrong vendor: {last.get('wrong_vendor', 0)}\n\n"
            f"Send `audit run` to trigger another now."
        )
    else:
        send_message(cfg.telegram_token, chat_id,
            f"❓ Unknown audit subcommand: {sub!r}\n\n"
            f"Valid: `audit`, `audit status`, `audit run`")


COMMAND_HANDLERS = {
    "sync":    lambda cfg, cid, args: _handle_sync(cfg, cid),
    "status":  lambda cfg, cid, args: _handle_status(cfg, cid),
    "help":    _handle_help_v2,
    "start":   _handle_help_v2,
    "pending": _handle_pending,
    "latest":  _handle_latest,
    "tax":     _handle_tax,
    "title":   _handle_title,
    "price":   _handle_price,
    "vendor":  _handle_vendor,
    "enrich":  _handle_enrich,
    "photos":  _handle_photos,
    "history": _handle_history,
    "notes":   _handle_notes,
    "add":     _handle_add,
    "invoice": _handle_invoice,
    "tunnel":  _handle_tunnel,
    "app":     _handle_app,
    "audit":   _handle_audit,
}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def listen(poll_timeout: int = 30, idle_sleep: int = 5) -> None:
    """Block forever, polling Telegram for messages.

    poll_timeout: seconds Telegram holds the long-poll open (no traffic
                  during this window — that's the whole point of long-polling)
    idle_sleep:   seconds to wait after a network error before retrying
    """
    cfg = load_config()
    if not cfg.telegram_token:
        raise RuntimeError(
            "No Telegram token configured. Run `python -m buyee setup` first."
        )

    last_offset = cfg.telegram_last_update_id or 0

    print(f"[listen] Starting Telegram listener.")
    print(f"[listen] Authorized chat: {cfg.telegram_authorized_chat_id or '(none yet — first message authorizes)'}")
    print(f"[listen] Polling for messages (Ctrl-C to stop)...")

    while True:
        updates = get_updates(cfg.telegram_token, offset=last_offset + 1, timeout=poll_timeout)
        if not updates:
            time.sleep(1)
            continue

        for upd in updates:
            last_offset = max(last_offset, upd.get("update_id", 0))
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = (msg.get("text") or "").strip()
            doc = msg.get("document")
            from_user = (msg.get("from") or {}).get("username", "(no username)")

            if not chat_id:
                continue
            if not text and not doc:
                continue  # might be a photo/sticker/etc — ignore

            log_payload = text or f"<doc: {(doc or {}).get('file_name','?')}>"
            print(f"[listen] {from_user}@{chat_id}: {log_payload}")

            # Auto-authorize the first chat that messages the bot
            if not cfg.telegram_authorized_chat_id:
                cfg.telegram_authorized_chat_id = chat_id
                save_config(cfg)
                send_message(cfg.telegram_token, chat_id, (
                    f"✓ Bot authorized for this chat.\n\n"
                    f"Commands:\n"
                    f"  sync     — download new Buyee invoices\n"
                    f"  status   — last sync info\n"
                    f"  help     — all commands\n\n"
                    f"Or just send/forward any invoice PDF and I'll transcribe it."
                ))
                # Don't drop the message — process it normally below
                # (e.g. they might have sent a PDF as the very first message)

            # Reject anyone else
            if chat_id != cfg.telegram_authorized_chat_id:
                send_message(cfg.telegram_token, chat_id,
                             "Sorry, this is a private bot.")
                continue

            # Document upload — transcribe automatically
            if doc:
                try:
                    _handle_document(cfg, chat_id, doc)
                except Exception as e:
                    send_message(cfg.telegram_token, chat_id, f"✗ Document handler crashed: {e}")
                    print(f"[listen] Document handler error: {e}")
                continue

            # Text command — first word is the verb, rest is the args
            stripped = text.strip()
            head, _, args = stripped.partition(" ")
            # If the message contains newlines, the "head" is just the first
            # word of the first line — args should include the rest verbatim
            # so multi-line invoice text isn't broken.
            if "\n" in head:
                head = head.split("\n", 1)[0]
                args = stripped[len(head):].lstrip()
            verb = head.lstrip("/").lower()
            handler = COMMAND_HANDLERS.get(verb)
            if handler:
                _log_action("command", verb=verb, args=args, raw=stripped[:200])
                try:
                    handler(cfg, chat_id, args)
                except Exception as e:
                    send_message(cfg.telegram_token, chat_id,
                                 f"✗ Handler crashed: {e}")
                    print(f"[listen] Handler error: {e}")
            elif _looks_like_purchase_message(stripped):
                # Free-form text with price markers → route to manual entry.
                # No verb required; user can just forward a vendor message.
                _log_action("auto_purchase", raw=stripped[:200])
                try:
                    _handle_smart_entry(cfg, chat_id, stripped)
                except Exception as e:
                    send_message(cfg.telegram_token, chat_id,
                                 f"✗ Auto-entry crashed: {e}")
                    print(f"[listen] Auto-entry error: {e}")
            else:
                send_message(cfg.telegram_token, chat_id, (
                    f"Unknown command: {text[:60]!r}\n"
                    f"Try: sync, status, latest, help — or send a PDF / "
                    f"forward a purchase message with prices."
                ))

        # Persist progress so we don't replay messages on restart
        cfg.telegram_last_update_id = last_offset
        save_config(cfg)


# ---------------------------------------------------------------------------
# Setup wizard — paste a token, send /start from your phone, done
# ---------------------------------------------------------------------------

def setup_interactive() -> bool:
    """Walk the user through Telegram bot setup. Returns True on success."""
    import sys

    print()
    print("=" * 70)
    print(" Buyee Telegram bot setup")
    print("=" * 70)
    print()
    print("  Step 1: Create a Telegram bot")
    print("  -------------------------------")
    print("    1. Open Telegram on your phone or desktop")
    print("    2. Search for: @BotFather")
    print("    3. Send: /newbot")
    print("    4. Pick a display name (e.g. 'Buyee Sync')")
    print("    5. Pick a username (e.g. 'paststudies_buyee_bot')")
    print("    6. Copy the bot token BotFather gives you")
    print("       (looks like: 1234567890:ABCdefGhIjklmnopQRStuv-wxyz)")
    print()
    token = input(" Paste your bot token here: ").strip()
    if not token or ":" not in token:
        print("  ✗ That doesn't look like a valid bot token. Aborting.")
        return False

    # Validate by hitting getMe
    try:
        resp = _api_call(token, "getMe", _timeout=15)
        if not resp.get("ok"):
            print(f"  ✗ Telegram rejected the token: {resp}")
            return False
        bot = resp["result"]
        print(f"  ✓ Bot reachable: @{bot.get('username')} ({bot.get('first_name')})")
    except Exception as e:
        print(f"  ✗ Couldn't reach Telegram: {e}")
        return False

    cfg = load_config()
    cfg.telegram_token = token
    save_config(cfg)

    print()
    print("  Step 2: Authorize your chat")
    print("  ----------------------------")
    print(f"    1. Open this URL on your phone (or search the bot username):")
    print(f"         https://t.me/{bot.get('username')}")
    print(f"    2. Send any message (try '/start')")
    print(f"    3. We'll detect you and save the chat ID")
    print()
    print("  Waiting for first message (60s timeout)...")
    print()
    sys.stdout.flush()

    deadline = time.time() + 60
    last_offset = 0
    while time.time() < deadline:
        updates = get_updates(token, offset=last_offset + 1, timeout=10)
        for upd in updates:
            last_offset = max(last_offset, upd.get("update_id", 0))
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id:
                cfg.telegram_authorized_chat_id = chat_id
                cfg.telegram_last_update_id = last_offset
                save_config(cfg)
                send_message(token, chat_id, (
                    "✓ Bot authorized.\n\n"
                    "Send 'sync' to download new invoices, 'status' for the last sync, "
                    "or 'help' for all commands.\n\n"
                    "(Make sure the listener is running on your laptop.)"
                ))
                print(f"  ✓ Authorized chat: {chat.get('username') or chat.get('first_name')} (id={chat_id})")
                print()
                print("  Setup complete. Start the listener with:")
                print("    uv run --with playwright --with pydantic python -m buyee listen")
                return True
        time.sleep(1)

    print(f"  ✗ Timed out waiting for first message.")
    print(f"  Re-run setup, or send a message and the listener will auto-authorize.")
    return False
