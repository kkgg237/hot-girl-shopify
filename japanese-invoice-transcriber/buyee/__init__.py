"""buyee — automated invoice download from buyee.jp."""

def __getattr__(name):
    if name in ("OrderIndex", "IndexedOrder"):
        from . import index
        return getattr(index, name)
    if name in ("login_interactive", "is_session_valid", "SESSION_PATH"):
        from . import auth
        return getattr(auth, name)
    if name in ("sync_invoices", "list_shipped_pages"):
        from . import scraper
        return getattr(scraper, name)
    raise AttributeError(f"module buyee has no attribute {name!r}")

__all__ = [
    "OrderIndex",
    "IndexedOrder",
    "login_interactive",
    "is_session_valid",
    "SESSION_PATH",
    "sync_invoices",
    "list_shipped_pages",
]
