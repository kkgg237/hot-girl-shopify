#!/usr/bin/env python3
"""Weekly Shopify catalogue audit — push findings to Telegram.

Runs the same `scan_catalogue_issues` scan that powers the Streamlit
"Shopify catalogue" tab, formats the results into a phone-friendly
message, and posts it to the authorized Telegram chat.

Default scope: `live` — only products that are LIVE on the Online Store
sales channel (status=active + published_at set). These are the listings
customers actually see, so a missing photo or wrong vendor is a real
hygiene problem worth interrupting your week for.

Wired to launchd at ~/Library/LaunchAgents/com.paststudies.weekly-audit.plist
(see install_weekly_audit_plist for the one-time install). The default
schedule is **Monday 9:00am local time** every week.

Run modes:
    uv run python weekly_catalogue_audit.py            # full run + Telegram
    uv run python weekly_catalogue_audit.py --dry-run  # scan + print, no send
    uv run python weekly_catalogue_audit.py --install  # install launchd job
    uv run python weekly_catalogue_audit.py --uninstall

Exit codes:
    0   ran cleanly (with or without findings)
    1   scan failed (network/auth) — message NOT sent
    2   misconfigured (no Telegram creds, etc.)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional


HERE = Path(__file__).parent

# Auto-load .env so SHOPIFY_* + Anthropic creds resolve regardless of how
# the script is invoked (launchd has a sparse environment).
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True) or str(HERE / ".env"), override=True)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Telegram log — append-only structured event log
# (matches the pattern documented in CLAUDE.md under "Telegram bot session
#  continuity" — same JSONL format the listener uses)
# ---------------------------------------------------------------------------

_LOG_PATH = HERE / "buyee" / "state" / "telegram_log.jsonl"


def _log_event(kind: str, **fields) -> None:
    """Append a structured event to the Telegram log. Best-effort."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            **fields,
        }
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Never let logging failure abort the audit
        print(f"[audit] log failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

MAX_EXAMPLES_PER_BUCKET = 8  # how many flagged items to list per category


def _format_audit_message(result: dict, shop: Optional[str]) -> str:
    """Render a scan-result dict into a Telegram-friendly plain-text message.

    Telegram's sendMessage caps at 4096 chars. We aggressively truncate the
    per-item lists; the full data is always accessible in the Streamlit
    Catalogue tab via the Scan button.
    """
    now = _dt.datetime.now().strftime("%a %b %-d · %H:%M")
    shop_label = f" · {shop}" if shop else ""
    lines = [f"🛍️  Weekly Shopify audit · {now}{shop_label}"]

    if result.get("error"):
        lines.append(f"\n⚠️ Scan failed: {result['error']}")
        lines.append(f"(fetched {result.get('fetched', 0)} products before failure)")
        return "\n".join(lines)

    scanned = result.get("scanned", 0)
    no_photos = result.get("no_photos", []) or []
    wrong_vendor = result.get("wrong_vendor", []) or []
    clean = scanned - len(no_photos) - len(wrong_vendor)

    lines.append(
        f"\nLive on website: {scanned:,} products"
        f"\n  ✓ Clean: {clean:,}"
        f"\n  ⚠️ No photos: {len(no_photos):,}"
        f"\n  ⚠️ Wrong vendor: {len(wrong_vendor):,}"
    )

    if result.get("partial_error"):
        lines.append(f"\nNote: {result['partial_error']}")

    if no_photos:
        lines.append(f"\n📷 NO-PHOTO LISTINGS (top {min(len(no_photos), MAX_EXAMPLES_PER_BUCKET)}):")
        for r in no_photos[:MAX_EXAMPLES_PER_BUCKET]:
            t = (r.get("title") or "")[:60]
            lines.append(f"• {t}")
            lines.append(f"  {r.get('admin_url', '')}")
        if len(no_photos) > MAX_EXAMPLES_PER_BUCKET:
            lines.append(f"… +{len(no_photos) - MAX_EXAMPLES_PER_BUCKET} more")

    if wrong_vendor:
        lines.append(f"\n🏷️ WRONG-VENDOR LISTINGS (top {min(len(wrong_vendor), MAX_EXAMPLES_PER_BUCKET)}):")
        for r in wrong_vendor[:MAX_EXAMPLES_PER_BUCKET]:
            t = (r.get("title") or "")[:60]
            v = r.get("vendor", "")
            det = r.get("detected_brand") or "?"
            lines.append(f"• {t}")
            lines.append(f"  vendor=`{v}` → suggest `{det}`")
            lines.append(f"  {r.get('admin_url', '')}")
        if len(wrong_vendor) > MAX_EXAMPLES_PER_BUCKET:
            lines.append(f"… +{len(wrong_vendor) - MAX_EXAMPLES_PER_BUCKET} more")

    if not no_photos and not wrong_vendor:
        lines.append("\n✨ Everything looks clean. Nothing to fix this week.")
    else:
        lines.append(
            "\nFix via the Streamlit Shopify catalogue tab "
            "(top-right of the app) — there's a bulk-fix button for vendors."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Audit runner
# ---------------------------------------------------------------------------

def run_audit(dry_run: bool = False) -> int:
    """Execute the catalogue scan + Telegram push. Returns exit code."""
    # ---- Telegram creds ----
    try:
        from buyee.config import load_config
    except ImportError:
        print("[audit] buyee.config not importable — can't load Telegram creds.",
              file=sys.stderr)
        return 2

    cfg = load_config()
    if not dry_run and not cfg.telegram_configured:
        print("[audit] Telegram not configured. Set telegram_token + "
              "telegram_authorized_chat_id in buyee/state/config.json.",
              file=sys.stderr)
        return 2

    # ---- Scan ----
    try:
        from shopify_push import scan_catalogue_issues
        from shopify_inventory import get_shop
    except ImportError as e:
        print(f"[audit] Can't import scanner: {e}", file=sys.stderr)
        return 2

    print(f"[audit] {_dt.datetime.now():%F %T}  starting weekly scan…")
    try:
        result = scan_catalogue_issues(scope="live")
    except Exception as e:
        print(f"[audit] Scan crashed: {type(e).__name__}: {e}", file=sys.stderr)
        _log_event("weekly_audit",
                   status="crash", error=f"{type(e).__name__}: {e}")
        return 1

    shop = get_shop()
    msg = _format_audit_message(result, shop)

    print(f"[audit] scan complete: scanned={result.get('scanned', 0)} "
          f"no_photos={len(result.get('no_photos') or [])} "
          f"wrong_vendor={len(result.get('wrong_vendor') or [])}")
    print("─" * 60)
    print(msg)
    print("─" * 60)

    if dry_run:
        print("[audit] --dry-run set, not sending to Telegram.")
        return 0

    # ---- Send to Telegram ----
    from buyee.listen import send_message
    resp = send_message(cfg.telegram_token, cfg.telegram_authorized_chat_id, msg)
    sent_ok = bool(resp and resp.get("ok"))

    _log_event("weekly_audit",
               status="sent" if sent_ok else "send_failed",
               scanned=result.get("scanned", 0),
               no_photos=len(result.get("no_photos") or []),
               wrong_vendor=len(result.get("wrong_vendor") or []))

    if not sent_ok:
        print("[audit] Telegram send failed — see stderr.", file=sys.stderr)
        return 1
    print("[audit] ✓ Telegram message sent.")
    return 0


# ---------------------------------------------------------------------------
# launchd plist install / uninstall
# ---------------------------------------------------------------------------

PLIST_LABEL = "com.paststudies.weekly-audit"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs"


def _build_plist_xml() -> str:
    """Return the plist XML for the weekly Monday 9am job.

    Uses `uv run` so we don't have to pin a Python path. Working directory is
    the project root so .env discovery works. Output goes to ~/Library/Logs
    so it's reviewable later.
    """
    uv_path = "/opt/homebrew/bin/uv"  # apple-silicon default; falls back below
    # Try a few common locations — the plist needs an absolute path
    for candidate in ("/opt/homebrew/bin/uv", "/usr/local/bin/uv",
                      str(Path.home() / ".cargo/bin/uv"),
                      str(Path.home() / ".local/bin/uv")):
        if Path(candidate).exists():
            uv_path = candidate
            break

    script = HERE / "weekly_catalogue_audit.py"
    out_log = LOG_DIR / f"{PLIST_LABEL}.out.log"
    err_log = LOG_DIR / f"{PLIST_LABEL}.err.log"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{uv_path}</string>
        <string>run</string>
        <string>--with</string><string>python-dotenv</string>
        <string>--with</string><string>pydantic</string>
        <string>--with</string><string>pyyaml</string>
        <string>python</string>
        <string>{script}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{HERE}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
</dict>
</plist>
"""


def install_plist() -> int:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_build_plist_xml(), encoding="utf-8")
    print(f"[install] wrote {PLIST_PATH}")

    import subprocess
    # Unload first in case it was already loaded — ignore errors
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                   capture_output=True)
    r = subprocess.run(["launchctl", "load", str(PLIST_PATH)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[install] launchctl load failed: {r.stderr}", file=sys.stderr)
        return 1
    print(f"[install] ✓ scheduled — every Monday 9:00am")
    print(f"[install] logs: {LOG_DIR / (PLIST_LABEL + '.out.log')}")
    print(f"[install] to trigger a test run NOW:")
    print(f"           launchctl start {PLIST_LABEL}")
    return 0


def uninstall_plist() -> int:
    import subprocess
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                       capture_output=True)
        PLIST_PATH.unlink()
        print(f"[uninstall] removed {PLIST_PATH}")
    else:
        print(f"[uninstall] {PLIST_PATH} not present — nothing to remove.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Run the scan + print the message, but don't send to Telegram.")
    ap.add_argument("--install", action="store_true",
                    help="Install the weekly Monday 9am launchd job.")
    ap.add_argument("--uninstall", action="store_true",
                    help="Remove the launchd job.")
    args = ap.parse_args()

    if args.install:
        return install_plist()
    if args.uninstall:
        return uninstall_plist()
    return run_audit(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
