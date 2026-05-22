"""Playwright session management for Buyee.

Buyee uses Cloudflare bot protection that blocks plain `requests`. We need a
real browser context. Strategy:

  - First time: `login_interactive()` opens a visible Chromium, lets the user
    log in (handles 2FA / CAPTCHA / Cloudflare challenges manually), then
    persists cookies + localStorage to a JSON file via Playwright's
    `storage_state`.
  - Subsequent runs: `with_session()` loads that storage state into a fresh
    headless context — no re-login needed until cookies expire.

Session expiry handling:
  - `is_session_valid()` does a cheap GET against /mybaggages/shipped/1 in a
    headless context with the saved storage. If we end up redirected to a
    login page, the session is dead — caller should run `login_interactive()`.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)


HERE = Path(__file__).parent
STATE_DIR = HERE / "state"
SESSION_PATH = STATE_DIR / "session.json"
# Persistent browser profile — real Chrome user-data-dir. Cloudflare's bot
# detection trips on Playwright's default automation flags + ephemeral
# profile; a persistent context with --disable-blink-features removes the
# most-fingerprinted markers.
PROFILE_DIR = STATE_DIR / "chromium_profile"

LOGIN_URL = "https://buyee.jp/signup/login"
HOME_URL = "https://buyee.jp/"
SHIPPED_URL = "https://buyee.jp/mybaggages/shipped/1"

USER_AGENT = (
    # Realistic Mac Chrome UA matching the Chromium 147 we install
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

# Args that hide most "I'm a robot" markers Cloudflare looks for
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--no-first-run",
]


def login_interactive(timeout_minutes: int = 15, save_signal_path: Optional[Path] = None) -> bool:
    """Open visible Chromium, let user log in, auto-save cookies.

    Detection strategy:
      1. Watch ALL tabs in the browser context (not just the original tab)
         — user might open a new tab to log in, or paste a different URL
      2. As soon as ANY tab reaches /mybaggages/, /myaccount/, or any
         buyee.jp page that isn't /signup or /login, we save state and exit
      3. Optional save_signal_path: a file the caller can `touch` to force
         immediate save, useful when invoking from another process

    Stdout is line-buffered by default in Python; we explicitly flush after
    each print so external monitors see progress.

    Returns True if login succeeded, False if timed out or aborted.
    """
    import sys
    import time

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    save_signal_path = save_signal_path or STATE_DIR / ".save_now"
    # Clear any stale signal
    if save_signal_path.exists():
        save_signal_path.unlink()

    def log(msg: str) -> None:
        print(msg, flush=True)

    log("")
    log("=" * 70)
    log(" Buyee login")
    log("=" * 70)
    log("")
    log(f"  Browser window opening. You have {timeout_minutes} minutes.")
    log("  Login on buyee.jp, then navigate to /mybaggages/shipped/1")
    log("  (or any /mybaggages/ or /myaccount/ page).")
    log("")
    log(f"  External save trigger: `touch {save_signal_path}`")
    log("  (saves session immediately, useful if auto-detect doesn't fire)")
    log("")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        # Persistent context = real Chrome profile dir, cookies survive between
        # runs, and many automation fingerprints (navigator.webdriver, etc.)
        # are absent. This is the key to getting past Cloudflare.
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            args=STEALTH_ARGS,
            ignore_default_args=["--enable-automation"],
        )
        # Hide the navigator.webdriver flag — biggest single bot-detection signal
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # Use the existing tab if any, else open one
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"  ⚠ Initial navigation failed: {e}")
            log(f"  Opening homepage instead — log in from there.")
            try:
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

        deadline = time.time() + timeout_minutes * 60
        last_urls: set[str] = set()

        def is_authenticated_url(url: str) -> bool:
            low = url.lower()
            if "buyee.jp" not in low:
                return False
            if any(t in low for t in ("/signup", "/login", "/recovery", "/forgot")):
                return False
            # Strong signals of authenticated area
            if any(t in low for t in ("/mybaggages", "/myaccount", "/myorders", "/order/")):
                return True
            return False

        def save_and_finish(reason: str) -> bool:
            try:
                ctx.storage_state(path=str(SESSION_PATH))
                log("")
                log(f"  ✓ Session saved to {SESSION_PATH}")
                log(f"  Trigger: {reason}")
                log("")
                ctx.close()
                return True
            except Exception as e:
                log(f"  ✗ Failed to save session: {e}")
                return False

        while time.time() < deadline:
            # External signal — user can `touch buyee/state/.save_now` to force save
            if save_signal_path.exists():
                save_signal_path.unlink()
                # Use the most-recently-active page for the URL log
                if ctx.pages:
                    log(f"  ⓘ Save signal received. Active tabs: " +
                        ", ".join(p.url for p in ctx.pages))
                return save_and_finish("manual save signal")

            # Poll every tab in the context
            try:
                pages = list(ctx.pages)
            except Exception:
                break

            current_urls: set[str] = set()
            for p in pages:
                try:
                    u = p.url
                    current_urls.add(u)
                except Exception:
                    continue

            new_urls = current_urls - last_urls
            for u in new_urls:
                log(f"  ⓘ Tab navigated: {u}")
            last_urls = current_urls

            # Check if any tab is on an authenticated page
            for u in current_urls:
                if is_authenticated_url(u):
                    return save_and_finish(f"detected authenticated page: {u}")

            time.sleep(2)

        # Final attempt — save whatever state exists
        log("")
        log(f"  ⏱ Reached {timeout_minutes}-minute timeout.")
        log(f"  Open tabs at exit: {list(last_urls)}")
        # Best-effort save anyway in case cookies are valid even without
        # us having navigated to a recognized page
        try:
            ctx.storage_state(path=str(SESSION_PATH))
            log(f"  Saved current state to {SESSION_PATH} as a best-effort fallback.")
            log(f"  Run `python -m buyee status` to verify.")
            browser.close()
            return True
        except Exception:
            browser.close()
            return False


@contextmanager
def with_session(headless: bool = True) -> Iterator[tuple[Playwright, BrowserContext, Page]]:
    """Yield a Playwright page authenticated with our persistent profile.

    Reuses the same persistent profile dir as login_interactive() so cookies,
    localStorage, and the browser fingerprint match the human session that
    Cloudflare last saw. This is essential to avoid fresh Cloudflare challenges
    on every sync.

    Yields (playwright, context, page). Raises FileNotFoundError if login
    hasn't been run yet.
    """
    if not PROFILE_DIR.exists() and not SESSION_PATH.exists():
        raise FileNotFoundError(
            f"No saved Buyee session at {PROFILE_DIR}. "
            f"Run `uv run python -m buyee login` first."
        )

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            args=STEALTH_ARGS,
            ignore_default_args=["--enable-automation"],
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield (pw, ctx, page)
        finally:
            try:
                ctx.storage_state(path=str(SESSION_PATH))
            except Exception:
                pass
            ctx.close()


def is_session_valid() -> tuple[bool, str]:
    """Cheap check: do we still have a logged-in session?

    Returns (ok, message). If ok is False, caller should run login_interactive.
    """
    if not SESSION_PATH.exists():
        return False, "No saved session — run `python -m buyee login`."

    try:
        with with_session(headless=True) as (pw, ctx, page):
            page.goto(SHIPPED_URL, wait_until="domcontentloaded", timeout=20000)
            final_url = page.url
            if "login" in final_url.lower() or "signup" in final_url.lower():
                return False, f"Session expired (redirected to {final_url})."
            # Sanity: page should mention 'baggage' or 'shipped' or '配送' somewhere
            content = page.content().lower()
            if any(t in content for t in ("baggage", "shipped", "配送", "mybaggage")):
                return True, f"Session valid. Current URL: {final_url}"
            return False, f"Page loaded but doesn't look authenticated. URL: {final_url}"
    except Exception as e:
        return False, f"Session check failed: {e}"
