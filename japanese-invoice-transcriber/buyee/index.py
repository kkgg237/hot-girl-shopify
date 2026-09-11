"""Order index — tracks which Buyee invoices we've downloaded.

Single source of truth: buyee/state/index.json
Schema: {order_id: IndexedOrder dict}

The order_id is whatever Buyee uses on /mybaggages/shipped/N — typically a
shipping baggage ID. We store the URL we saw it at, the PDF path on disk
once downloaded, and any metadata we scraped (date shipped, total, etc.).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


HERE = Path(__file__).parent
STATE_DIR = HERE / "state"
INDEX_PATH = STATE_DIR / "index.json"
META_PATH = STATE_DIR / "meta.json"


class IndexedOrder(BaseModel):
    """One Buyee shipped order we've seen + (optionally) downloaded."""
    order_id: str
    detail_url: Optional[str] = None
    invoice_url: Optional[str] = None
    pdf_path: Optional[str] = None        # relative path under inputs/
    shipped_at: Optional[str] = None      # ISO date if scraped
    total_jpy: Optional[int] = None       # if visible on listing
    item_count: Optional[int] = None
    first_seen_at: str = Field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    downloaded_at: Optional[str] = None
    notes: Optional[str] = None

    @property
    def is_downloaded(self) -> bool:
        return bool(self.pdf_path) and bool(self.downloaded_at)


class OrderIndex:
    """Wraps state/index.json with simple add/get/save operations."""

    def __init__(self, path: Path = INDEX_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._orders: dict[str, IndexedOrder] = self._load()

    def _load(self) -> dict[str, IndexedOrder]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {oid: IndexedOrder(**data) for oid, data in raw.items()}

    def save(self) -> None:
        raw = {oid: o.model_dump(exclude_none=False) for oid, o in self._orders.items()}
        self.path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # ---- accessors -----------------------------------------------------------

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._orders

    def __len__(self) -> int:
        return len(self._orders)

    def get(self, order_id: str) -> Optional[IndexedOrder]:
        return self._orders.get(order_id)

    def all(self) -> list[IndexedOrder]:
        return sorted(self._orders.values(), key=lambda o: o.first_seen_at, reverse=True)

    def downloaded(self) -> list[IndexedOrder]:
        return [o for o in self._orders.values() if o.is_downloaded]

    def pending(self) -> list[IndexedOrder]:
        return [o for o in self._orders.values() if not o.is_downloaded]

    # ---- mutators ------------------------------------------------------------

    def upsert(self, order: IndexedOrder) -> IndexedOrder:
        """Add or update an order. Preserves first_seen_at on update."""
        existing = self._orders.get(order.order_id)
        if existing:
            # keep original first_seen_at, prefer non-None new fields
            merged = existing.model_dump()
            for k, v in order.model_dump(exclude_none=True).items():
                if k != "first_seen_at":
                    merged[k] = v
            self._orders[order.order_id] = IndexedOrder(**merged)
        else:
            self._orders[order.order_id] = order
        return self._orders[order.order_id]

    def mark_downloaded(self, order_id: str, pdf_path: str) -> IndexedOrder:
        """Record that we successfully downloaded the invoice PDF."""
        order = self._orders.get(order_id)
        if not order:
            raise KeyError(f"Unknown order: {order_id}")
        order.pdf_path = pdf_path
        order.downloaded_at = _dt.datetime.now().isoformat(timespec="seconds")
        self._orders[order_id] = order
        return order


# ---------------------------------------------------------------------------
# Sync metadata — separate file so it doesn't clutter the order index
# ---------------------------------------------------------------------------

class IndexMeta(BaseModel):
    """Tracks when we last polled Buyee for new shipments and what happened.

    Used by the Streamlit app to decide whether to auto-sync on page load
    (sync if last completed > N hours ago) and to display freshness badges.
    """
    last_sync_started_at: Optional[str] = None
    last_sync_completed_at: Optional[str] = None
    last_sync_pages: Optional[int] = None
    last_sync_seen: Optional[int] = None
    last_sync_new: Optional[int] = None
    last_sync_downloaded: Optional[int] = None
    last_sync_errors: Optional[int] = None
    last_sync_error_msg: Optional[str] = None
    sync_count: int = 0


def load_meta() -> IndexMeta:
    if not META_PATH.exists():
        return IndexMeta()
    try:
        return IndexMeta(**json.loads(META_PATH.read_text(encoding="utf-8")))
    except Exception:
        return IndexMeta()


def save_meta(meta: IndexMeta) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(
        json.dumps(meta.model_dump(exclude_none=False), indent=2) + "\n",
        encoding="utf-8",
    )


def hours_since_last_sync() -> Optional[float]:
    """How many hours since the last successful sync? None if never synced."""
    meta = load_meta()
    if not meta.last_sync_completed_at:
        return None
    try:
        last = _dt.datetime.fromisoformat(meta.last_sync_completed_at)
    except ValueError:
        return None
    delta = _dt.datetime.now() - last
    return delta.total_seconds() / 3600.0


def humanize_freshness(hours: Optional[float]) -> str:
    """'just now' / '3h ago' / '2 days ago' / 'never'."""
    if hours is None:
        return "never"
    minutes = hours * 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    return f"{int(days/30)}mo ago"
