"""buyee — automated invoice download from buyee.jp.

Buyee has no public buyer-account API and blocks unauthenticated requests
(Cloudflare). The only viable path is a real browser session via Playwright.

Workflow:
  1. uv run python -m buyee login    # one-time, opens browser, you log in
  2. uv run python -m buyee sync     # routine: download new invoices
  3. uv run python -m buyee list     # show what we've indexed
  4. uv run python -m buyee status   # is the session still valid?

Files:
  buyee/state/session.json         # Playwright storage_state (cookies + localStorage)
  buyee/state/index.json           # which orders we've downloaded
  buyee/state/raw_html/            # last-sync HTML dumps for debugging selectors
"""
from .index import OrderIndex, IndexedOrder

# Auth and scraper depend on playwright — make their import lazy so the
# package can be used without installing playwright when only research
# (LLM-based enrichment) is needed.
def __getattr__(name):
    if name in ("login_interactive", "is_session_valid", "SESSION_PATH"):
        from . import auth
        return getattr(auth, name)
    if name in ("sync_invoices", "list_shipped_pages"):
        from . import scraper
        return getattr(scraper, name)
    raise AttributeError(f"module 'buyee' has no attribute {name!r}")


__all__ = [
    "OrderIndex",
    "IndexedOrder",
    "login_interactive",
    "is_session_valid",
    "SESSION_PATH",
    "sync_invoices",
    "list_shipped_pages",
]
