"""Consolidate transcribed-invoice files into one row per real order.

The `output/` directory accumulates several files for the same underlying
invoice:

    W2605289159.json                 raw transcription (plain stem)
    buyee_W2605289159.json           raw transcription (buyee download stem)
    edited_buyee_W2605289159.json    human-curated working copy
    buyee_W2605289159.shopify_pushed.json   post-push snapshot (sidecar)

The picker used to list every file flat, so one order showed up 3-4 times and
loading the newest raw stem hid the user's edits (stranded under a different
stem). This module groups files by a canonical order key, picks the file to
load (edited working copy preferred), and reports status flags — pure logic so
it can be unit-tested without Streamlit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

# Buyee order ids look like W + 9 digits. Used both to collapse the plain and
# buyee_ stems of the same order and to tell buyee orders from vendor invoices.
_ORDER_ID_RE = re.compile(r"W\d{8,}")

_PUSHED_SIDECAR = ".shopify_pushed.json"
# Non-invoice caches that live in output/ but must never appear as their own
# row (comps/enrichment lookups, cleanup reports, the commercial-invoice header).
_EXCLUDE_SUFFIXES = (".comps.json",)
_EXCLUDE_EXACT = {"commercial_invoice_header"}
_EXCLUDE_PREFIXES = ("cleanup_",)
_STRIP_PREFIXES = ("edited_", "buyee_")
# Ingest timestamp the Telegram listener / web uploader prepend to a saved
# filename (YYYY-MM-DD_HHMMSS_). Stripped so the bot's un-prefixed stem and the
# web app's timestamp-prefixed stem for the SAME invoice collapse to one row.
_INGEST_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_")


def canonical_order_key(filename: str) -> str:
    """Collapse every stem variant of one order to a single key.

    W2605289159.json / buyee_W2605289159.json / edited_buyee_W2605289159.json
    → "W2605289159". Vendor/manual invoices with no order id fall back to the
    base stem with edited_/buyee_ prefixes and any ingest timestamp stripped —
    so "260613-OTK-…" (bot) and "2026-07-20_163250_260613-OTK-…" (web upload)
    resolve to the same key instead of two duplicate rows.
    """
    name = filename
    if name.endswith(".json"):
        name = name[: -len(".json")]
    if name.endswith(".shopify_pushed"):
        name = name[: -len(".shopify_pushed")]

    m = _ORDER_ID_RE.search(name)
    if m:
        return m.group(0)

    stripped = name
    for pref in _STRIP_PREFIXES:
        if stripped.startswith(pref):
            stripped = stripped[len(pref):]
    stripped = _INGEST_TS_RE.sub("", stripped)
    return stripped


def _base_stem(p: Path) -> str:
    return p.name[: -len(".json")] if p.name.endswith(".json") else p.name


def _is_excluded(p: Path) -> bool:
    if any(p.name.endswith(suf) for suf in _EXCLUDE_SUFFIXES):
        return True
    base = _base_stem(p)
    if base in _EXCLUDE_EXACT:
        return True
    return any(base.startswith(pre) for pre in _EXCLUDE_PREFIXES)


@dataclass
class InvoiceGroup:
    order_key: str
    load_path: Path          # the file to open (edited working copy preferred)
    variants: list[Path]     # all loadable files for this order (excl. sidecars)
    has_edits: bool          # an edited_ working copy exists
    is_buyee: bool           # canonical key is a Buyee order id
    is_pushed: bool          # a .shopify_pushed.json sidecar exists


def group_invoice_files(
    paths: Iterable[Path],
    *,
    mtime: Optional[Callable[[Path], float]] = None,
) -> list[InvoiceGroup]:
    """Group output/*.json into one InvoiceGroup per canonical order.

    load_path prefers the newest edited_ working copy, else the newest raw file
    — so a user's corrections always win over a later re-transcription saved
    under a different stem. `mtime` is injectable for testing.
    """
    if mtime is None:
        mtime = lambda p: p.stat().st_mtime  # noqa: E731

    buckets: dict[str, list[Path]] = {}
    for p in paths:
        if _is_excluded(p):
            continue
        buckets.setdefault(canonical_order_key(p.name), []).append(p)

    groups: list[InvoiceGroup] = []
    for key, files in buckets.items():
        pushed = [f for f in files if f.name.endswith(_PUSHED_SIDECAR)]
        loadable = [f for f in files if not f.name.endswith(_PUSHED_SIDECAR)]
        if not loadable:
            continue
        edited = [f for f in loadable if f.name.startswith("edited_")]
        raws = [f for f in loadable if not f.name.startswith("edited_")]
        load_path = max(edited or raws, key=mtime)
        groups.append(
            InvoiceGroup(
                order_key=key,
                load_path=load_path,
                variants=sorted(loadable, key=lambda f: f.name),
                has_edits=bool(edited),
                is_buyee=bool(_ORDER_ID_RE.fullmatch(key)),
                is_pushed=bool(pushed),
            )
        )
    return groups


_POSTAL_OR_STREET_RE = re.compile(r"^[\d\-\s]+$")


def from_location(
    vendor_name: str = "",
    vendor_address: str = "",
    invoice_type: str = "",
    currency: str = "",
) -> str:
    """Best-effort 'where it's from' for the invoice table.

    Prefers a city/country pulled from the vendor address; falls back to the
    Buyee (Japan) proxy origin, then a currency-based country guess.
    """
    if vendor_address:
        parts = [s.strip() for s in vendor_address.split(",") if s.strip()]
        keep = [s for s in parts if not _POSTAL_OR_STREET_RE.match(s)]
        if keep:
            tail = keep[-2:] if len(keep) >= 2 else keep
            # Normalize shouty country names ("JAPAN" -> "Japan").
            tail = [seg.title() if seg.isupper() else seg for seg in tail]
            return ", ".join(tail)

    it = (invoice_type or "").lower()
    v = (vendor_name or "").lower()
    if "buyee" in v or "buyee" in it:
        return "Japan (Buyee)"

    return {"JPY": "Japan", "EUR": "Europe", "GBP": "UK"}.get((currency or "").upper(), "")
