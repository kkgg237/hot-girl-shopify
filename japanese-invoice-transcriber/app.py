#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "streamlit>=1.35",
#   "anthropic>=0.40.0",
#   "pydantic>=2.0",
#   "python-dotenv>=1.0",
#   "pymupdf>=1.24",
#   "pandas>=2.2",
#   "pyyaml>=6.0",
#   "playwright>=1.45",
# ]
# ///
"""Past Studies — invoice → Shopify QA tool.

Designed around one job: review an invoice before uploading to Shopify.
  1. Hero metrics tell you instantly if the invoice reconciles and what the margin is.
  2. Alerts surface items that need attention.
  3. Item cards (sorted risk-first) let you spot-check without a table.
  4. Demand/rate controls recompute inline.
  5. Sticky download bar is always one click away.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, date as _date
from pathlib import Path
from typing import Optional

# Bootstrap: re-exec under streamlit when invoked as a plain script.
if __name__ == "__main__":
    import streamlit.runtime as _rt
    if not _rt.exists():
        import streamlit.web.cli as stcli
        sys.argv = ["streamlit", "run", __file__, "--server.headless=false", "--"]
        sys.exit(stcli.main())


import anthropic
import pandas as pd
import streamlit as st
from dotenv import find_dotenv, load_dotenv

from costs import DEFAULT_EXCHANGE_RATE, Invoice, InvoiceView
from pricing import canon_brand, canon_type, compose_title, price_item
from transcribe import transcribe as transcribe_pdf
from heuristics import (
    RULES_PATH,
    FEEDBACK_PATH,
    append_feedback,
    load_feedback,
    load_rules,
    update_feedback_status,
)

load_dotenv(find_dotenv(usecwd=True), override=True)

BASE = Path(__file__).parent
INPUTS = BASE / "inputs"
OUTPUT = BASE / "output"
for d in (INPUTS, OUTPUT):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Page config + styling — Past Studies editorial aesthetic
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Past Studies · Invoice Review",
    page_icon="◇",
    layout="wide",
    initial_sidebar_state="expanded",
)

CSS = """
<style>
/* Arial Nova — Microsoft system font. Bundled with Windows 11 / Office.
   On Mac systems it falls back to Arial. Not available on Google Fonts. */
html, body, [class*="css"], .stMarkdown, .stText, p, span, div, label, button, input, select, textarea {
    font-family: 'Arial Nova', 'Arial Nova Light', Arial, Helvetica, sans-serif !important;
    color: #111;
    font-weight: 400;
}
h1, h2, h3, h4, h5, h6, .display, .display-num {
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif !important;
    color: #111;
    font-weight: 500;
}

/* Top-level scaffolding */
.stApp { background: #fafaf8; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2rem; padding-bottom: 6rem; max-width: 1280px; }

/* Page header — logo-only, no text */
.ps-header { border-bottom: 1px solid #111; padding: 0.5rem 0 1rem;
             margin-bottom: 2rem; display: flex; align-items: center; }
.ps-logo   { height: 72px; width: auto; flex-shrink: 0; }

/* Hero metrics strip */
.hero-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin: 1rem 0 1.5rem;
            background: #fff; border: 1px solid #111; }
.hero-cell { padding: 1.25rem 1.5rem; border-right: 1px solid #eee; }
.hero-cell:last-child { border-right: none; }
.hero-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.12em;
              color: #777; margin-bottom: 0.4rem; font-weight: 500; }
.hero-value { font-family: 'Arial Nova', Arial, Helvetica, sans-serif; font-size: 2rem; font-weight: 500;
              line-height: 1.05; letter-spacing: -0.02em; color: #111; }
.hero-sub   { font-size: 0.78rem; color: #888; margin-top: 0.3rem; }

/* Invoice meta strip */
.meta-row { display: flex; gap: 2rem; padding: 0.75rem 1rem; margin-bottom: 1.5rem;
            background: #fff; border: 1px solid #eee; font-size: 0.85rem; }
.meta-row .k { text-transform: uppercase; letter-spacing: 0.08em; color: #888;
               font-size: 0.68rem; margin-right: 0.5rem; }
.meta-row .v { font-weight: 500; color: #111; }

/* Reconciliation banner */
.recon { padding: 0.9rem 1.1rem; margin-bottom: 1.5rem; font-size: 0.92rem;
         display: flex; justify-content: space-between; align-items: center; }
.recon.ok    { background: #eef7ee; border-left: 3px solid #2c7a2c; color: #1a4a1a; }
.recon.warn  { background: #fef5e7; border-left: 3px solid #c77f14; color: #7a4a0a; }
.recon .badge { font-family: 'Arial Nova', Arial, Helvetica, sans-serif; font-weight: 500;
                font-size: 1.05rem; }

/* Alerts list */
.alerts { background: #fff; border: 1px solid #f0d9a8; padding: 1rem 1.25rem;
          margin-bottom: 1.5rem; }
.alerts h4 { font-family: 'Arial Nova', Arial, Helvetica, sans-serif; font-size: 1.05rem; font-weight: 500;
             color: #7a4a0a; margin-bottom: 0.5rem; }
.alerts ul { font-size: 0.85rem; padding-left: 1.25rem; color: #5a3a0a; margin: 0; }
.alerts li { margin-bottom: 0.2rem; }

/* Items heading + controls */
.items-heading { display: flex; justify-content: space-between; align-items: center;
                 margin: 2rem 0 0.5rem; }
.items-heading h3 { font-family: 'Arial Nova', Arial, Helvetica, sans-serif; font-size: 1.35rem;
                    font-weight: 500; margin: 0; letter-spacing: -0.01em; }
.items-count { font-size: 0.85rem; color: #777; }

/* Item card */
.item-card { background: #fff; border: 1px solid #e5e5e5; padding: 1rem 1.25rem;
             margin-bottom: 0.75rem; transition: border-color 0.15s; }
.item-card:hover { border-color: #111; }
.item-card.has-warning { border-left: 3px solid #c77f14; }
.item-card.no-brand    { border-left: 3px solid #999; background: #fafafa; }
/* Items with a photo get an extra leading column for the thumbnail.
   Items without a photo lay out as before — :has() selector targets only
   the cards that contain a photo link. */
.item-head { display: grid; grid-template-columns: 1fr auto auto auto;
             gap: 1.5rem; align-items: baseline; margin-bottom: 0.35rem; }
.item-head:has(.item-photo-link) { grid-template-columns: auto 1fr auto auto auto; }
.item-photo-link { display: inline-block; line-height: 0; }
.item-photo { width: 64px; height: 64px; object-fit: cover;
              border: 1px solid #e5e5e5; background: #fafafa;
              transition: border-color 0.15s, transform 0.15s; }
.item-photo:hover { border-color: #111; transform: scale(1.05); }
.item-title { font-size: 1rem; font-weight: 500; color: #111; line-height: 1.3; }
.item-brand-tag { font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
                  background: #111; color: #fff; padding: 0.1rem 0.5rem;
                  font-size: 0.65rem; letter-spacing: 0.1em;
                  text-transform: uppercase; font-weight: 500; margin-right: 0.5rem;
                  vertical-align: 0.12em; display: inline-block; }
.item-brand-tag.unknown { background: #ddd; color: #555; }

.item-numbers { display: flex; gap: 1.5rem; align-items: baseline; }
.item-cost, .item-price, .item-margin {
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
    font-weight: 500;
    line-height: 1;
}
.item-cost  { color: #666; font-size: 0.85rem; }
.item-cost-num { font-size: 1.05rem; }
.item-price { color: #111; font-size: 1.5rem; }
.item-margin { color: #2c7a2c; font-size: 0.85rem; }
.item-margin.thin { color: #c77f14; }
.item-margin.negative { color: #a02020; }
.to-arrow { color: #999; font-size: 1.1rem; vertical-align: 0.1em; }

.item-meta { font-size: 0.78rem; color: #777; margin: 0.3rem 0 0.5rem;
             display: flex; flex-wrap: wrap; gap: 0.4rem 0.75rem; }
.item-meta .chip { background: #f4f1eb; padding: 0.08rem 0.5rem; border-radius: 2px;
                   font-size: 0.72rem; color: #555; }
.item-meta .chip.sku { font-family: 'Courier New', monospace; }

.item-warnings { margin-top: 0.4rem; }
.item-warnings .warning {
    display: inline-block; font-size: 0.72rem; color: #7a4a0a;
    background: #fef5e7; padding: 0.1rem 0.5rem; margin-right: 0.3rem;
    border-radius: 2px;
}

/* Expandable detail */
details.item-detail { margin-top: 0.5rem; padding-top: 0.5rem;
                      border-top: 1px dashed #eee; }
details.item-detail summary { cursor: pointer; font-size: 0.78rem; color: #777;
                               list-style: none; outline: none; user-select: none; }
details.item-detail summary:hover { color: #111; }
details.item-detail summary::-webkit-details-marker { display: none; }
details.item-detail summary::before { content: '+ '; color: #999; }
details.item-detail[open] summary::before { content: '− '; }
details.item-detail .body { padding-top: 0.75rem; display: grid;
                            grid-template-columns: 1fr 1fr; gap: 1.5rem;
                            font-size: 0.8rem; }
details.item-detail .body h5 { font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
                                font-size: 0.9rem; margin-bottom: 0.3rem;
                                font-weight: 500; color: #111; }
details.item-detail table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
details.item-detail td { padding: 0.15rem 0; color: #444; }
details.item-detail td.v { text-align: right; font-family: 'Courier New', monospace;
                           color: #111; }
details.item-detail tr.final td { border-top: 1px solid #111; font-weight: 600;
                                    color: #111; padding-top: 0.3rem; }
details.item-detail .orig { font-family: 'Courier New', monospace; font-size: 0.72rem;
                             color: #777; margin-top: 0.4rem; }

/* Streamlit control overrides */
.stSlider > div > div > div > div { background: #111 !important; }
.stSlider [data-baseweb="slider"] > div > div { background: #eee !important; }
.stNumberInput input, .stTextInput input, .stSelectbox > div > div {
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif !important;
    border-radius: 0 !important;
}
.stNumberInput label, .stSlider label, .stSelectbox label, .stRadio label, .stFileUploader label {
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #777 !important;
    font-weight: 500 !important;
}
.stRadio > div { flex-direction: row; gap: 1rem; }
.stRadio [role="radiogroup"] { gap: 1rem; }

/* Buttons — high specificity + !important so Streamlit's primary-button
   theme (which would otherwise leave text invisible against its red default)
   can't override our black-bg/white-text editorial styling. */
.stButton > button,
.stDownloadButton > button,
button[kind="primary"],
button[kind="secondary"],
.stButton > button[kind="primary"],
.stButton > button[kind="secondary"] {
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    border-radius: 0 !important;
    border: 1px solid #111 !important;
    background: #111 !important;
    color: #fff !important;
    padding: 0.6rem 1.5rem !important;
}
.stButton > button *,
.stDownloadButton > button *,
button[kind="primary"] *,
button[kind="secondary"] *,
.stButton > button[kind="primary"] *,
.stButton > button[kind="secondary"] * {
    color: #fff !important;
}
.stButton > button:hover,
.stDownloadButton > button:hover,
button[kind="primary"]:hover,
button[kind="secondary"]:hover,
.stButton > button[kind="primary"]:hover,
.stButton > button[kind="secondary"]:hover {
    background: #fff !important;
    color: #111 !important;
}
.stButton > button:hover *,
.stDownloadButton > button:hover *,
button[kind="primary"]:hover *,
button[kind="secondary"]:hover *,
.stButton > button[kind="primary"]:hover *,
.stButton > button[kind="secondary"]:hover * {
    color: #111 !important;
}

/* Stage tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid #111;
    margin-bottom: 1.5rem;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Arial Nova', Arial, sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.7rem 1.5rem !important;
    border-radius: 0 !important;
    background: transparent !important;
    color: #777 !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    margin-right: 0.5rem;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: #111 !important;
    border-bottom: 2px solid #111 !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1rem; }

/* Shared-input cards on the Cost tab */
.shared-inputs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0;
                 background: #fff; border: 1px solid #e5e5e5; margin-bottom: 1.5rem; }
.shared-inputs .cell { padding: 0.85rem 1.1rem; border-right: 1px solid #eee; }
.shared-inputs .cell:last-child { border-right: none; }
.shared-inputs .label { font-size: 0.68rem; text-transform: uppercase;
                        letter-spacing: 0.1em; color: #888; margin-bottom: 0.3rem; }
.shared-inputs .value { font-family: 'Arial Nova', Arial, sans-serif;
                        font-size: 1.15rem; font-weight: 500; }
.shared-inputs .sub   { font-size: 0.72rem; color: #888; margin-top: 0.2rem; }

/* Section headings inside tabs */
.section-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.12em;
                 color: #777; margin: 1.25rem 0 0.6rem; font-weight: 500; }

/* Sticky download bar */
.sticky-download {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #fff; border-top: 1px solid #111;
    padding: 0.75rem 2rem; z-index: 100;
    display: flex; justify-content: space-between; align-items: center;
    box-shadow: 0 -4px 12px rgba(0,0,0,0.05);
}

/* File uploader — hide Streamlit's built-in dropzone icon + instructional div
   (the Material Symbols ligature "upload" renders as literal text when its
   font isn't loaded, producing the doubled "uploadupload" glitch) */
[data-testid="stFileUploaderDropzone"] {
    border: 1px dashed #bbb !important;
    border-radius: 0 !important;
    background: #fff !important;
    padding: 1rem 1.25rem !important;
    justify-content: space-between !important;
}
[data-testid="stFileUploaderDropzone"] > div:first-child,
[data-testid="stFileUploaderDropzoneInstructions"] {
    display: none !important;
}
[data-testid="stFileUploaderDropzone"]::before {
    content: "Drop a PDF here or choose a file";
    color: #555;
    font-size: 0.88rem;
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
    flex: 1;
}
/* File uploader button */
/* Zero out the button's actual children (icon ligature + label text) on all
   states — hover and focus included — so nothing sneaks back in. */
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploaderDropzone"] button * {
    font-size: 0 !important;
    line-height: 1;
}
[data-testid="stFileUploaderDropzone"] button {
    text-transform: none !important;
    letter-spacing: 0 !important;
    font-weight: 500 !important;
    background: #111 !important;
    color: #fff !important;
    border: 1px solid #111 !important;
    border-radius: 0 !important;
    padding: 0.5rem 1.25rem !important;
    min-height: auto !important;
    transition: background-color 0.12s ease, color 0.12s ease;
    cursor: pointer;
}
[data-testid="stFileUploaderDropzone"] button::after {
    content: "Browse";
    font-size: 0.82rem !important;
    letter-spacing: 0.05em;
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
    color: #fff;
}
[data-testid="stFileUploaderDropzone"] button:hover,
[data-testid="stFileUploaderDropzone"] button:focus,
[data-testid="stFileUploaderDropzone"] button:focus-visible {
    background: #fff !important;
    color: #111 !important;
    border-color: #111 !important;
    outline: none !important;
    box-shadow: none !important;
}
[data-testid="stFileUploaderDropzone"] button:hover::after,
[data-testid="stFileUploaderDropzone"] button:focus::after,
[data-testid="stFileUploaderDropzone"] button:focus-visible::after {
    color: #111 !important;
}

/* Hide some streamlit chrome */
#MainMenu, footer, header [data-testid="stDecoration"] { visibility: hidden; }

/* Streamlit uses Material Symbols font ligatures throughout — the font never
   loads in our app (we didn't import it), so ligatures like "arrow_drop_down",
   "check", "upload" render as literal text and overlap other UI. Hide all of
   them globally, the semantic meaning is carried by the adjacent text. */
[data-testid="stIconMaterial"],
[data-testid="stIcon"],
[data-testid="stExpanderToggleIcon"],
[data-testid*="ExpandIcon"],
span.material-icons,
span.material-symbols,
span.material-symbols-outlined,
span.material-symbols-rounded,
i.material-icons,
i.material-symbols,
[class*="MaterialIcon"],
[class*="material-symbols"] {
    display: none !important;
}

/* Exception: the sidebar open/close controls must stay visible — otherwise
   there's no way to toggle the sidebar if it gets collapsed. Force them on
   and label them with text after-content so the user sees a caret. */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] *,
[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapsedControl"] * {
    display: inline-flex !important;
    visibility: visible !important;
}
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapsedControl"] svg {
    display: none !important;
}
[data-testid="stSidebarCollapseButton"]::after {
    content: "◀";
    font-size: 14px;
    padding: 4px 8px;
}
[data-testid="stSidebarCollapsedControl"]::after {
    content: "▶ Notes";
    font-size: 12px;
    padding: 4px 8px;
    background: #f4f1eb;
    border: 1px solid #ddd;
    border-radius: 3px;
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_transcribe(pdf_bytes: bytes, filename: str) -> dict:
    tmp = INPUTS / filename
    tmp.write_bytes(pdf_bytes)
    client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    invoice = transcribe_pdf(tmp, client)
    out = OUTPUT / f"{tmp.stem}.json"
    out.write_text(invoice.model_dump_json(indent=2), encoding="utf-8")
    return invoice.model_dump()


def list_transcribed() -> list[Path]:
    """List transcribed invoices, edited variants first.

    Edited files (with `edited_` prefix) are the human-curated versions and
    typically what you want to load. The originals (raw transcriptions) are
    kept around for audit + regression but rarely re-loaded directly.
    """
    files = sorted(OUTPUT.glob("*.json"))
    edited = [f for f in files if f.name.startswith("edited_")]
    originals = [f for f in files if not f.name.startswith("edited_")]
    # Edited at top, originals below — sorted by name within each group
    return edited + originals


EDITED_PREFIX = "edited_"


def edited_path_for(source_file: str) -> str:
    """Map a source filename to its 'edited' counterpart.

    Originals (transcribed JSONs) get an `edited_` prefix on first save so
    the raw LLM output is preserved unchanged for audit / re-running tests.
    Already-edited files keep writing to themselves (no `edited_edited_`).

      foo.json        → edited_foo.json
      edited_foo.json → edited_foo.json
    """
    if source_file.startswith(EDITED_PREFIX):
        return source_file
    return EDITED_PREFIX + source_file


def persist_invoice(invoice_data: dict) -> Path:
    """Write the mutated invoice dict to output/edited_<stem>.json.

    NEVER overwrites the original transcription — that JSON is the immutable
    record of what the LLM extracted, kept for audit + regression tests.
    All human edits land in a sibling `edited_` file; subsequent saves write
    back to the same edited file.
    """
    source_file = invoice_data.get("__source_file") or "edited.json"
    target_file = edited_path_for(source_file)
    path = OUTPUT / target_file
    clean = {k: v for k, v in invoice_data.items() if not k.startswith("__")}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # Update the in-memory dict so subsequent saves in the same session
    # also write to the edited file (not back to the original).
    invoice_data["__source_file"] = target_file
    return path


CORRECTIONS_LOG = BASE / "title_corrections.jsonl"


def log_title_correction(item: dict, computed_title: str, override_title: str, source_file: str):
    """Append an override event to a JSONL log — feeds future prompt few-shots.

    Each line: {timestamp, source_file, source_id, brand, product_type,
                computed_title, override_title}. We analyze these later to
    extract patterns like 'always rename Shoes → Ballet Flats for Chanel'.
    """
    if computed_title == override_title:
        return  # no-op
    from datetime import datetime as _dt
    entry = {
        "timestamp": _dt.utcnow().isoformat() + "Z",
        "source_file": source_file,
        "source_id": item.get("source_id"),
        "brand": item.get("detected_brand"),
        "product_type": item.get("product_type"),
        "computed_title": computed_title,
        "override_title": override_title,
    }
    with CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _str_edit(new_val, old_val):
    """Return the new value if it differs from old (normalized), else None."""
    a = new_val.strip() if isinstance(new_val, str) else new_val
    b = old_val.strip() if isinstance(old_val, str) else old_val
    if a != b:
        return a or None
    return None


def apply_cost_edits(invoice_data: dict, edited_df) -> int:
    """Diff the Cost-tab DataFrame back onto invoice_data['items'] by source_id.
    Returns count of items changed."""
    by_sid = {row["source_id"]: row for _, row in edited_df.iterrows()}
    changed = 0
    for item in invoice_data["items"]:
        new = by_sid.get(item["source_id"])
        if new is None:
            continue
        dirty = False

        # brand (treat "Vintage" as "clear it")
        new_brand = (new.get("brand") or "").strip()
        cur_brand = (item.get("detected_brand") or "").strip()
        if new_brand == "Vintage" and cur_brand:
            item["detected_brand"] = None; dirty = True
        elif new_brand and new_brand != "Vintage" and new_brand != cur_brand:
            item["detected_brand"] = new_brand; dirty = True

        # proposed title — becomes override_title if it differs from baseline
        new_title = (new.get("proposed title") or "").strip()
        if new_title:
            from costs import LineItem as _LI
            from pricing import compose_title as _ct
            tmp = {k: v for k, v in item.items() if k in _LI.model_fields.keys()}
            tmp["override_title"] = None
            baseline = _ct(_LI(**tmp))
            if new_title != baseline:
                if new_title != item.get("override_title"):
                    item["override_title"] = new_title; dirty = True
                    log_title_correction(item, baseline, new_title,
                                          invoice_data.get("__source_file", "unknown"))
            elif item.get("override_title"):
                item["override_title"] = None; dirty = True

        for k, field in [("qty", "quantity"), ("product_type", "product_type"),
                         ("material", "material"), ("garment_length", "garment_length"),
                         ("era", "era"), ("color", "color"), ("pattern", "pattern"),
                         ("origin", "origin"), ("model_name", "model_name"),
                         ("model_size", "model_size"),
                         ("style_adjectives", "style_adjectives")]:
            if k in new.index:
                if field == "quantity":
                    v = int(new[k]) if new[k] else 1
                    if v != item.get("quantity", 1):
                        item["quantity"] = v; dirty = True
                else:
                    changed_field = _str_edit(new[k], item.get(field))
                    if changed_field is not None or (not new[k] and item.get(field)):
                        item[field] = (new[k] or None) if isinstance(new[k], str) else new[k]
                        dirty = True
        if dirty:
            changed += 1
    return changed


def apply_pricing_edits(invoice_data: dict, edited_df) -> int:
    """Apply edits from the Pricing-tab DataFrame."""
    by_sid = {row["Source ID"]: row for _, row in edited_df.iterrows()}
    changed = 0
    for item in invoice_data["items"]:
        new = by_sid.get(item["source_id"])
        if new is None:
            continue
        dirty = False

        # Title override
        new_title = (new.get("Title") or "").strip()
        if new_title:
            from costs import LineItem as _LI
            from pricing import compose_title as _ct
            tmp = {k: v for k, v in item.items() if k in _LI.model_fields.keys()}
            tmp["override_title"] = None
            baseline = _ct(_LI(**tmp))
            if new_title != baseline:
                if new_title != item.get("override_title"):
                    item["override_title"] = new_title; dirty = True
                    log_title_correction(item, baseline, new_title,
                                          invoice_data.get("__source_file", "unknown"))
            elif item.get("override_title"):
                item["override_title"] = None; dirty = True

        # Vendor override
        new_vendor = (new.get("Vendor") or "").strip()
        default_vendor = (item.get("detected_brand") or "").strip()
        if new_vendor == "Vintage":
            if item.get("override_vendor"):
                item["override_vendor"] = None; dirty = True
        elif new_vendor and new_vendor != default_vendor:
            if new_vendor != (item.get("override_vendor") or ""):
                item["override_vendor"] = new_vendor; dirty = True
        elif new_vendor == default_vendor and item.get("override_vendor"):
            item["override_vendor"] = None; dirty = True

        # Product type
        new_type = (new.get("Type") or "").strip()
        if new_type != (item.get("product_type") or "").strip():
            item["product_type"] = new_type or None; dirty = True

        # Price override
        new_price = new.get("Variant Price")
        try:
            np_int = int(new_price) if new_price is not None else None
        except (TypeError, ValueError):
            np_int = None
        if np_int and np_int > 0 and np_int != (item.get("override_price") or 0):
            item["override_price"] = np_int; dirty = True
        if dirty:
            changed += 1
    return changed


def compute_rows(
    invoice_data: dict,
    rate: float,
    demand: float,
    handling_rate: Optional[float] = None,
    import_tax_rate: Optional[float] = None,
    extra_rate: Optional[float] = None,
    extra_flat: Optional[float] = None,
):
    """Return (view, list of enriched item dicts).

    All rate overrides are forwarded into InvoiceView so Pricing & Export
    tabs see the same landed cost the Cost Review tab is displaying.
    """
    from costs import HANDLING_RATE as _DEF_H, IMPORT_TAX_RATE as _DEF_I
    invoice = Invoice(**{k: v for k, v in invoice_data.items() if not k.startswith("_")})
    view = InvoiceView(
        invoice,
        exchange_rate=rate,
        handling_rate=handling_rate if handling_rate is not None else _DEF_H,
        import_tax_rate=import_tax_rate if import_tax_rate is not None else _DEF_I,
        extra_rate=extra_rate if extra_rate is not None else 0.0,
        extra_flat=extra_flat if extra_flat is not None else 0.0,
    )
    items = []
    for raw, it in zip(invoice_data["items"], invoice.items):
        b = view.breakdown(it)
        p = price_item(it, view, demand=demand)
        items.append({
            "item": it,
            "breakdown": b,
            "pricing": p,
        })
    return view, items


def fmt_usd(x: float, decimals: int = 0) -> str:
    return f"${x:,.{decimals}f}"


def fmt_native(x: float, currency: str) -> str:
    if currency == "JPY":
        return f"¥{int(round(x)):,}"
    return f"${x:,.2f}"


def margin_class(margin: float, cost: float) -> str:
    if margin < 0:
        return "negative"
    if cost > 0 and margin < cost * 0.3:  # under 30% margin is thin
        return "thin"
    return ""


def build_shopify_csv(
    invoice_data: dict, rate: float, demand: float,
    handling_rate: Optional[float] = None,
    import_tax_rate: Optional[float] = None,
    extra_rate: Optional[float] = None,
    extra_flat: Optional[float] = None,
    return_collisions: bool = False,
):
    """Build the Shopify-import CSV. Optionally pre-checks against live
    Shopify inventory and disambiguates colliding SKUs/handles.

    return_collisions=True returns (csv_bytes, collision_log) tuple.
    Default returns just csv_bytes for backward compatibility.
    """
    from price import price_invoice
    from to_shopify import item_to_rows, HEADER
    from shopify_inventory import refresh_inventory, is_configured
    import csv

    invoice = Invoice(**{k: v for k, v in invoice_data.items() if not k.startswith("_")})
    priced = price_invoice(
        invoice, rate, demand,
        handling_rate=handling_rate, import_tax_rate=import_tax_rate,
        extra_rate=extra_rate, extra_flat=extra_flat,
    )
    priced["__source_file"] = invoice_data.get("__source_file", "upload.json")

    # Pull live Shopify inventory if configured. If not configured or fetch
    # failed, we proceed with empty sets — local uniqueness still enforced.
    inventory = refresh_inventory() if is_configured() else None
    existing_skus: set[str] = set(inventory.skus) if inventory and inventory.is_loaded else set()
    existing_handles: set[str] = set(inventory.handles) if inventory and inventory.is_loaded else set()

    used_skus: set[str] = set()
    used_handles: set[str] = set()
    collision_log: list[dict] = []
    all_rows: list[dict] = []
    for item in priced["items"]:
        all_rows.extend(item_to_rows(
            item, priced, priced["__source_file"], used_skus,
            used_handles=used_handles,
            existing_skus=existing_skus,
            existing_handles=existing_handles,
            collision_log=collision_log,
        ))

    buf = io.StringIO()
    header = [h for h in HEADER if not h.startswith("_")]  # strip internal cols
    writer = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_rows)
    csv_bytes = buf.getvalue().encode("utf-8")
    if return_collisions:
        return csv_bytes, collision_log
    return csv_bytes


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------

def render_header():
    """Top-of-page brand header — logo only, no text.

    Logo is loaded from `assets/ps_logo.png` (or .svg / .jpg / .webp) when
    present. Gracefully degrades to an empty bordered strip if the asset is
    missing, so nothing breaks.
    """
    import base64 as _b64
    from pathlib import Path as _Path

    logo_html = ""
    here = _Path(__file__).parent
    for ext in ("png", "svg", "jpg", "jpeg", "webp"):
        p = here / "assets" / f"ps_logo.{ext}"
        if p.exists():
            mime = {"svg": "svg+xml", "jpg": "jpeg"}.get(ext, ext)
            try:
                data = _b64.b64encode(p.read_bytes()).decode("ascii")
                logo_html = (
                    f'<img class="ps-logo" '
                    f'src="data:image/{mime};base64,{data}" '
                    f'alt="Past Studies" />'
                )
            except Exception:
                pass
            break

    st.markdown(
        f'<div class="ps-header">{logo_html}</div>',
        unsafe_allow_html=True,
    )


def render_buyee_sync_panel():
    """Compact panel above the source picker — sync invoices from Buyee account.

    Shows session status + a single 'Sync now' button that calls the scraper.
    Login is interactive (opens a browser) and is intentionally NOT triggered
    from this UI — it must happen in a terminal. We just point the user there
    if the session is missing/expired.
    """
    try:
        from buyee import is_session_valid, SESSION_PATH
        from buyee.index import (
            OrderIndex,
            hours_since_last_sync,
            humanize_freshness,
            load_meta,
        )
        from buyee.config import load_config
        from buyee.scraper import sync_invoices
    except ImportError as e:
        # Soft fail — Buyee module is optional infrastructure
        return

    idx = OrderIndex()
    pending = len(idx.pending())
    downloaded = len(idx.downloaded())
    total = len(idx)
    last_sync_h = hours_since_last_sync()
    cfg = load_config()

    badge_parts = []
    if total > 0:
        badge_parts.append(f"{downloaded}/{total} downloaded")
    badge_parts.append(f"last sync {humanize_freshness(last_sync_h)}")
    if cfg.telegram_configured:
        badge_parts.append("Telegram bot configured")
    badge = " · " + " · ".join(badge_parts) if badge_parts else ""

    with st.expander(f"📦  Sync invoices from Buyee{badge}", expanded=False):
        if not SESSION_PATH.exists():
            st.warning(
                "**No Buyee session saved.** First time? Run this in a terminal "
                "to log in once:\n\n"
                "```\nuv run --with playwright --with pydantic python -m buyee login\n```\n\n"
                "A browser will open. Log in (including any 2FA), navigate to "
                "https://buyee.jp/mybaggages/shipped/1, then return to the terminal "
                "and press Enter. Your session cookies will be saved locally."
            )
            return

        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            sync_clicked = st.button("🔄 Sync now", type="primary", use_container_width=True)
        with c2:
            check_clicked = st.button("Check session", use_container_width=True)
        with c3:
            max_pages = st.number_input("Max pages", min_value=1, max_value=50,
                                        value=5, label_visibility="collapsed",
                                        help="Stop after N shipped-list pages")

        if check_clicked:
            with st.spinner("Checking Buyee session..."):
                ok, msg = is_session_valid()
            (st.success if ok else st.error)(msg)
            if not ok:
                st.info(
                    "Run `uv run --with playwright --with pydantic python -m buyee login` "
                    "to refresh your session."
                )

        if sync_clicked:
            with st.spinner(f"Scanning {max_pages} page(s) of shipped baggages…"):
                try:
                    stats = sync_invoices(max_pages=int(max_pages), dry_run=False)
                except FileNotFoundError as e:
                    st.error(f"Session missing: {e}")
                    return
                except Exception as e:
                    st.error(f"Sync failed: {e}")
                    st.caption("If this is a session issue, re-run `python -m buyee login` "
                               "in your terminal.")
                    return

            ok = stats["errors"] == 0
            (st.success if ok else st.warning)(
                f"Pages: {stats['pages_visited']} · "
                f"Seen: {stats['seen']} · "
                f"New: {stats['new']} · "
                f"Downloaded: {stats['downloaded']} · "
                f"Errors: {stats['errors']}"
            )
            if stats["downloaded"]:
                st.info(
                    f"{stats['downloaded']} new invoice(s) saved to `inputs/buyee/`. "
                    f"Use the file uploader below or pick from the transcribed list "
                    f"after running transcription."
                )
            if stats["seen"] == 0:
                st.warning(
                    "No orders parsed from the page. The selectors in "
                    "`buyee/scraper.py` may need refinement — see "
                    "`buyee/state/raw_html/shipped_1.html` for what was actually returned."
                )

        # Recent index summary
        if total > 0:
            recent = idx.all()[:5]
            st.markdown("**Recent orders**")
            for o in recent:
                status = "✓" if o.is_downloaded else "·"
                bits = [f"`{o.order_id}`"]
                if o.shipped_at:
                    bits.append(o.shipped_at)
                if o.pdf_path:
                    bits.append(f"→ `{o.pdf_path}`")
                st.markdown(f"  {status} {' · '.join(bits)}")

        # ------------------------------------------------------------------
        # Photo scraper — small, only relevant for Buyee invoices currently
        # loaded. Surfaces directly here so user doesn't have to drop to CLI.
        # ------------------------------------------------------------------
        try:
            current_invoice = st.session_state.get("__current_invoice_path")
            if current_invoice:
                from buyee.photo_scraper import fetch_invoice_photos, is_eligible
                from pathlib import Path as _P
                inv_path = _P(current_invoice)
                if inv_path.exists():
                    inv_data = json.loads(inv_path.read_text(encoding="utf-8"))
                    eligible = sum(
                        1 for it in inv_data.get("items", [])
                        if is_eligible(it.get("source_id"))
                    )
                    if eligible:
                        st.markdown("---")
                        c1, c2 = st.columns([3, 1])
                        with c1:
                            st.markdown(
                                f"**📷 Photo thumbnails** — "
                                f"{eligible} of {len(inv_data.get('items', []))} items have "
                                f"Buyee auction pages with photos."
                            )
                            st.caption("~3-5 sec per item, free. Cached after first fetch.")
                        with c2:
                            if st.button("Fetch photos", key="fetch_photos", use_container_width=True):
                                with st.spinner(f"Fetching {eligible} photo(s)..."):
                                    stats = fetch_invoice_photos(inv_path)
                                if stats["errors"] == 0:
                                    st.success(
                                        f"✓ {stats['downloaded']} new, "
                                        f"{stats['skipped_existing']} cached, "
                                        f"{stats['skipped_ineligible']} ineligible"
                                    )
                                else:
                                    st.warning(
                                        f"{stats['downloaded']} downloaded, {stats['errors']} errors"
                                    )
                                st.rerun()
        except Exception:
            pass  # photo panel is bonus; don't block on errors

        st.markdown("---")
        st.markdown("**📱 Trigger sync from your phone (Telegram)**")
        if cfg.telegram_configured:
            st.success(
                "Telegram bot configured. Send `sync` to your bot from any phone "
                "to trigger a sync remotely."
            )
            st.caption(
                "Listener must be running. Start it with `python -m buyee listen` "
                "or install as a background service via "
                "`bash buyee/launchd/install.sh` (macOS)."
            )
        else:
            st.info(
                "**Not configured.** To trigger syncs by sending a Telegram message:\n\n"
                "1. Run in a terminal: `uv run --with playwright --with pydantic python -m buyee setup`\n"
                "2. Follow the wizard — creates a bot via @BotFather, authorizes your phone\n"
                "3. Run `uv run --with playwright --with pydantic python -m buyee listen` to start receiving messages\n"
                "4. (Optional) `bash buyee/launchd/install.sh` to keep listener running automatically"
            )


def render_incoming_panel():
    """Surface PDFs sitting in inputs/ that haven't been transcribed yet.

    Searches inputs/**.pdf, samples/*.pdf and shows any that don't have a
    matching JSON in output/. Includes a "Transcribe now" button per item.
    """
    import os

    BASE = Path(__file__).parent

    # Discover all incoming PDFs (inputs/ subtree + samples/)
    pdfs: list[Path] = []
    for root in (BASE / "inputs", BASE / "samples"):
        if not root.exists():
            continue
        pdfs.extend(root.rglob("*.pdf"))
    pdfs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if not pdfs:
        return

    # A PDF is "processed" if a JSON with the same stem exists in output/.
    # We also look for a few common stem variants the pipeline produces.
    output_stems = {p.stem for p in (BASE / "output").glob("*.json")}

    def status_for(pdf: Path) -> str:
        if pdf.stem in output_stems:
            return "processed"
        return "pending"

    pending = [p for p in pdfs if status_for(p) == "pending"]
    processed = [p for p in pdfs if status_for(p) == "processed"]

    badge = ""
    if pending:
        badge = f" · {len(pending)} pending"
    elif pdfs:
        badge = f" · all {len(pdfs)} processed"

    with st.expander(f"📥  Incoming PDFs{badge}", expanded=bool(pending)):
        st.caption(
            "PDFs found in `inputs/` and `samples/`. Pending items don't have a "
            "matching JSON in `output/` yet — they may be mid-transcription, "
            "or never started."
        )

        if pending:
            st.markdown(f"**⏳ Pending ({len(pending)})**")
            for pdf in pending[:20]:
                rel = pdf.relative_to(BASE)
                age_min = (datetime.now().timestamp() - pdf.stat().st_mtime) / 60
                age_str = (
                    f"{int(age_min)}m ago" if age_min < 60
                    else f"{int(age_min/60)}h ago" if age_min < 1440
                    else f"{int(age_min/1440)}d ago"
                )
                size_kb = pdf.stat().st_size // 1024
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"`{rel}`  ·  {size_kb} KB  ·  {age_str}")
                with col2:
                    if st.button("Transcribe", key=f"trx_{pdf.name}",
                                  use_container_width=True):
                        with st.spinner(f"Transcribing {pdf.name}…"):
                            try:
                                client = anthropic.Anthropic(timeout=180.0, max_retries=2)
                                invoice = transcribe_pdf(pdf, client)
                                out = BASE / "output" / f"{pdf.stem}.json"
                                out.write_text(
                                    json.dumps(invoice.model_dump(mode="json"),
                                               ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                                msg = f"✓ Transcribed → `output/{out.name}`."
                                # Auto-fetch photos (first one each) for eligible items.
                                # Single-shot — same as the Telegram doc flow.
                                try:
                                    from buyee.photo_scraper import fetch_invoice_photos, is_eligible
                                    eligible = sum(
                                        1 for it in invoice.items
                                        if is_eligible(it.source_id)
                                    )
                                    if eligible:
                                        with st.spinner(f"Fetching {eligible} photo(s)…"):
                                            pstats = fetch_invoice_photos(out)
                                        msg += (f" Photos: {pstats['downloaded']} new, "
                                                f"{pstats['skipped_existing']} cached.")
                                except Exception:
                                    pass  # photos are best-effort; never block transcribe
                                st.success(msg + " Refresh and pick from the dropdown.")
                            except Exception as e:
                                st.error(f"✗ Transcribe failed: {e}")

        if processed:
            with st.expander(f"✓ Processed ({len(processed)})", expanded=False):
                for pdf in processed[:30]:
                    rel = pdf.relative_to(BASE)
                    st.markdown(f"  ✓ `{rel}` → `output/{pdf.stem}.json`")


def render_source_picker():
    render_buyee_sync_panel()
    render_incoming_panel()
    col_a, col_b = st.columns([2, 1])
    with col_a:
        uploaded = st.file_uploader("Upload invoice PDF", type=["pdf"])
    with col_b:
        existing = list_transcribed()
        # Group: edited variants first (your working copies), then originals
        # (raw transcriptions). Visual prefix in the labels makes the
        # distinction obvious without committing to grouped <optgroup>
        # which Streamlit's selectbox doesn't support.
        labelled = []
        for p in existing:
            if p.name.startswith("edited_"):
                labelled.append((p.name, f"✎  {p.name[len('edited_'):]}"))
            else:
                labelled.append((p.name, f"○  {p.name}  (original)"))
        options = ["— pick transcribed —"] + [label for _, label in labelled]
        value_for_label = {label: name for name, label in labelled}
        value_for_label["— pick transcribed —"] = None

        # URL ?source=<filename.json> deep-links to a transcribed invoice
        preselected = st.query_params.get("source")
        default_idx = 0
        if preselected:
            for i, (name, _) in enumerate(labelled, start=1):
                if name == preselected:
                    default_idx = i
                    break
        picked_label = st.selectbox(
            "Or pick a transcribed invoice", options, index=default_idx,
            help="✎ = edited (your working copy) · ○ = original (raw transcription, kept for audit)",
        )
        picked = value_for_label.get(picked_label)
    return uploaded, picked


def render_meta(invoice_data: dict):
    bits = [
        ("Vendor", invoice_data.get("vendor_name", "—")),
        ("Invoice", invoice_data.get("invoice_number") or "—"),
        ("Date", invoice_data.get("invoice_date") or "—"),
        ("Type", invoice_data.get("invoice_type", "—").replace("_", " ")),
        ("Items", str(len(invoice_data.get("items", [])))),
    ]
    spans = "".join(f'<span><span class="k">{k}</span><span class="v">{v}</span></span>' for k, v in bits)
    st.markdown(f'<div class="meta-row">{spans}</div>', unsafe_allow_html=True)


def render_hero(recon: dict, total_price: int, items: list, currency: str):
    landed = recon["landed_usd_sum"]
    margin = total_price - landed
    margin_pct = (margin / landed * 100) if landed else 0
    # Effective markup: rounded_price / unit_cost_usd. This respects manual
    # price overrides (where p.markup is stale — it's the algorithm's
    # intended markup, not what actually got applied).
    eff_markups = [
        i["pricing"].rounded_price / i["pricing"].unit_cost_usd
        for i in items if i["pricing"].unit_cost_usd > 0
    ]
    avg_markup = sum(eff_markups) / len(eff_markups) if eff_markups else 0
    overrides = sum(1 for i in items if getattr(i["item"], "override_price", None))

    # Main metrics
    html = f"""
    <div class="hero-row">
      <div class="hero-cell">
        <div class="hero-label">Cost basis</div>
        <div class="hero-value">{fmt_usd(landed)}</div>
        <div class="hero-sub">Sum of Cost per Item (USD)</div>
      </div>
      <div class="hero-cell">
        <div class="hero-label">Expected revenue</div>
        <div class="hero-value">{fmt_usd(total_price)}</div>
        <div class="hero-sub">Sum of Variant Price</div>
      </div>
      <div class="hero-cell">
        <div class="hero-label">Gross margin</div>
        <div class="hero-value">{fmt_usd(margin)}</div>
        <div class="hero-sub">+{margin_pct:.0f}% over cost · avg effective markup {avg_markup:.2f}×{f' · {overrides} override(s)' if overrides else ''}</div>
      </div>
      <div class="hero-cell">
        <div class="hero-label">Invoice total</div>
        <div class="hero-value">{fmt_native(recon['invoice_total'], currency)}</div>
        <div class="hero-sub">As printed on the invoice</div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_reconciliation(recon: dict, currency: str, fallback_applied: bool):
    if recon["reconciled"]:
        html = (
            f'<div class="recon ok">'
            f'<div><b>Reconciled</b> — computed {fmt_native(recon["computed"], currency)} matches invoice {fmt_native(recon["invoice_total"], currency)}</div>'
            f'<div class="badge">✓</div>'
            f'</div>'
        )
    else:
        sign = "+" if recon["delta"] > 0 else "−"
        html = (
            f'<div class="recon warn">'
            f'<div><b>Cost mismatch</b> — computed {fmt_native(recon["computed"], currency)}, '
            f'invoice {fmt_native(recon["invoice_total"], currency)} '
            f'({sign}{fmt_native(abs(recon["delta"]), currency)})</div>'
            f'<div class="badge">Δ</div>'
            f'</div>'
        )
    st.markdown(html, unsafe_allow_html=True)
    if fallback_applied:
        st.markdown(
            '<div class="recon warn">'
            '<div><b>Note</b> — international shipping missing from invoice; using $20 USD fallback per spec</div>'
            '<div class="badge">i</div></div>',
            unsafe_allow_html=True,
        )


def render_cost_review(view: InvoiceView, inv_data_ref: dict):
    """Intermediate QA step: full cost-input table + reconciliation.

    Every column that contributes to landed cost, per item. Editable: brand,
    title, product_type, material, garment_length, qty. Save writes to JSON.

    Args:
      view: computed InvoiceView
      inv_data_ref: raw invoice dict (mutated on save so callers see the edits)
    """
    inv = view.inv
    ccy = inv.currency
    recon = view.reconciliation()

    # 1. Reconciliation banner at top — go/no-go
    render_reconciliation(recon, ccy, view.intl_fallback_applied)

    # 2. Shared inputs card — the invoice-wide numbers split across all items
    #    For BrandStreet, shows the handling + import assumptions (not on the invoice)
    #    For Buyee, shows the intl shipping + customs splits
    is_buyee = inv.invoice_type == "buyee_breakdown"
    n = view.n_items
    landed_sum_native = sum(view.breakdown(i)["landed_native"] for i in inv.items)
    landed_sum_usd = recon["landed_usd_sum"]

    # Helper: format a native-currency amount with USD next to it
    def fmt_dual(amt_native: float, ccy_: str) -> str:
        if not amt_native:
            return f"{fmt_native(0, ccy_)} (≈ $0)"
        usd = amt_native if ccy_ == "USD" else amt_native * view.exchange_rate
        return f"{fmt_native(amt_native, ccy_)} ≈ ${usd:,.0f}"

    if is_buyee:
        intl = view.effective_intl
        customs = inv.customs_duty
        cells = [
            ("Items", str(n), "rows in Item Price table"),
            ("Intl shipping", fmt_dual(intl, ccy),
             f"{fmt_dual(intl / n if n else 0, ccy)} per item{' (fallback)' if view.intl_fallback_applied else ''}"),
            ("Customs duty", fmt_dual(customs, ccy),
             f"{fmt_dual(customs / n if n else 0, ccy)} per item"),
            ("Invoice total", fmt_dual(recon["invoice_total"], ccy), "as printed on the invoice"),
            ("Total landed", f"${landed_sum_usd:,.2f}",
             f"{fmt_native(landed_sum_native, ccy)} → USD at {view.exchange_rate:.4f}"),
        ]
    else:
        # Vendor invoice (BrandStreet, DKC, etc.): show the two assumed rates
        subtotal_native = sum(view._subtotal(i) for i in inv.items)
        handling_sum = sum(view.breakdown(i)["handling_amount"] for i in inv.items)
        import_sum = sum(view.breakdown(i)["import_amount"] for i in inv.items)
        cells = [
            ("Items", str(n), "rows in the invoice"),
            ("Handling (assumed)", f"{view.handling_rate * 100:.0f}%",
             f"= {fmt_dual(handling_sum, ccy)} across invoice"),
            ("Import tax (assumed)", f"{view.import_tax_rate * 100:.0f}%",
             f"= {fmt_dual(import_sum, ccy)} across invoice"),
        ]
        # Extras — surface only when actually set (default 0 = hidden)
        if view.extra_rate:
            extra_pct_sum = sum(view.breakdown(i)["extra_pct_amount"] for i in inv.items)
            cells.append((
                f"Extra ({view.extra_rate * 100:.1f}%)",
                fmt_dual(extra_pct_sum, ccy),
                f"per-item % surcharge across invoice",
            ))
        if view.extra_flat:
            ef_per_item = view.extra_flat / n if n else 0
            cells.append((
                f"Extra flat",
                fmt_dual(view.extra_flat, ccy),
                f"split equally: {fmt_dual(ef_per_item, ccy)} per item",
            ))

        # Commission line — explicit lump-sum commission (e.g. 5% on DKC).
        # Distinct from per-item commission_fees and from generic other_fees.
        if inv.commission_line:
            rate_label = (
                f"{inv.commission_line_rate * 100:.0f}%"
                if inv.commission_line_rate else "lump sum"
            )
            cl_share = inv.commission_line / n if n else 0
            cells.append((
                f"Commission ({rate_label})",
                fmt_dual(inv.commission_line, ccy),
                f"split equally: {fmt_dual(cl_share, ccy)} per item",
            ))
        # Generic other_fees (uncategorized) — keep visible if present
        if inv.other_fees:
            other_share_sum = sum(view.breakdown(i)["other_share"] for i in inv.items)
            cells.append((
                "Other fees (uncategorized)",
                fmt_dual(inv.other_fees, ccy),
                f"split equally: {fmt_dual(other_share_sum / n if n else 0, ccy)} per item",
            ))
        cells.extend([
            ("Invoice total", fmt_dual(recon["invoice_total"], ccy), "as printed on the invoice"),
            ("Total landed", f"${landed_sum_usd:,.2f}",
             f"subtotal + fees + handling + import  (+{(landed_sum_usd/(subtotal_native * view.exchange_rate if ccy != 'USD' else subtotal_native) - 1)*100:.0f}% uplift)"
             if subtotal_native else "—"),
        ])

    cells_html = "".join(
        f'<div class="cell"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'<div class="sub">{sub}</div></div>'
        for label, value, sub in cells
    )
    st.markdown(
        f'<div class="shared-inputs" style="grid-template-columns: repeat({len(cells)}, 1fr);">{cells_html}</div>',
        unsafe_allow_html=True,
    )

    # 3. Fee-table totals check (Buyee only) — confirms the join captured
    # exactly what the invoice's summary tables report
    if is_buyee:
        fees = view.fee_table_totals()
        check_html = f'''<div class="shared-inputs" style="grid-template-columns: repeat(3, 1fr);">
          <div class="cell">
            <div class="label">Σ Commission fees (join)</div>
            <div class="value">{fmt_native(fees["commission"], ccy)}</div>
            <div class="sub">{len(inv.commission_fees)} rows joined by source_id</div>
          </div>
          <div class="cell">
            <div class="label">Σ Domestic shipping (join)</div>
            <div class="value">{fmt_native(fees["domestic_shipping"], ccy)}</div>
            <div class="sub">{len(inv.domestic_shipping_fees)} rows</div>
          </div>
          <div class="cell">
            <div class="label">Σ Service fees (join)</div>
            <div class="value">{fmt_native(fees["service"], ccy)}</div>
            <div class="sub">{len(inv.service_fees)} rows</div>
          </div>
        </div>'''
        st.markdown(check_html, unsafe_allow_html=True)

    # 4. Orphan fee warnings — source_ids in a fee table that don't match items
    orph = view.orphan_fees()
    orphan_lines = []
    for table, sids in orph.items():
        if sids:
            orphan_lines.append(f"**{table.replace('_', ' ')}** — orphan source_ids: `{', '.join(sids)}`")
    if orphan_lines:
        st.warning("Join issues — the following fee rows didn't match any item:\n\n" + "\n\n".join(orphan_lines))

    # 5. THE TABLE — every cost input, one row per item
    st.markdown('<div class="section-label">Cost input table — every column that goes into landed cost</div>', unsafe_allow_html=True)

    # Helper: convert a native-currency amount to USD at the current FX rate
    def to_usd(native: float) -> float:
        return native if ccy == "USD" else native * view.exchange_rate

    rows = []
    for idx, item in enumerate(inv.items, 1):
        b = view.breakdown(item)
        row = {
            "#": idx,
            "source_id": item.source_id,
            "brand": canon_brand(item.detected_brand) or "Vintage",
            "proposed title": compose_title(item),
            "model_name": item.model_name or "",
            "model_size": item.model_size or "",
            "era": item.era or "",
            "color": item.color or "",
            "style_adjectives": item.style_adjectives or "",
            "pattern": item.pattern or "",
            "material": item.material or "",
            "origin": item.origin or "",
            "product_type": canon_type(item.product_type) or (item.product_type or ""),
            "garment_length": item.garment_length or "",
            "qty": item.quantity,
            f"item price ({ccy})": b["item_price"],
            "item price (USD)": to_usd(b["item_price"]),
            f"coupon ({ccy})": b["coupon"],
            f"subtotal ({ccy})": b["subtotal"],
            "subtotal (USD)": to_usd(b["subtotal"]),
        }
        if is_buyee:
            row.update({
                f"commission ({ccy})": b["commission"],
                "commission (USD)": to_usd(b["commission"]),
                f"dom ship ({ccy})": b["domestic_shipping"],
                "dom ship (USD)": to_usd(b["domestic_shipping"]),
                f"service ({ccy})": b["service"],
                "service (USD)": to_usd(b["service"]),
                f"intl share ({ccy})": b["intl_share"],
                "intl share (USD)": to_usd(b["intl_share"]),
                f"customs share ({ccy})": b["customs_share"],
                "customs share (USD)": to_usd(b["customs_share"]),
            })
        else:
            row[f"handling {view.handling_rate*100:.0f}% ({ccy})"] = b["handling_amount"]
            row[f"handling {view.handling_rate*100:.0f}% (USD)"] = to_usd(b["handling_amount"])
            row[f"import {view.import_tax_rate*100:.0f}% ({ccy})"] = b["import_amount"]
            row[f"import {view.import_tax_rate*100:.0f}% (USD)"] = to_usd(b["import_amount"])
            # Commission line — explicit lump-sum commission split per-item.
            # Show even when 0 if the field is configured to keep columns
            # consistent across rows.
            if inv.commission_line:
                rate_label = (
                    f"commission {inv.commission_line_rate*100:.0f}%"
                    if inv.commission_line_rate else "commission (lump)"
                )
                row[f"{rate_label} ({ccy})"] = b["commission_line_share"]
                row[f"{rate_label} (USD)"] = to_usd(b["commission_line_share"])
            # Generic other_fees fallback — only when other_fees > 0
            if b.get("other_share"):
                row[f"other share ({ccy})"] = b["other_share"]
                row["other share (USD)"] = to_usd(b["other_share"])
        # Ad-hoc extras — show whenever set (applies to both Buyee + vendor invoices)
        if view.extra_rate:
            label = f"extra {view.extra_rate*100:.1f}%"
            row[f"{label} ({ccy})"] = b.get("extra_pct_amount", 0)
            row[f"{label} (USD)"] = to_usd(b.get("extra_pct_amount", 0))
        if view.extra_flat:
            row[f"extra flat ({ccy})"] = b.get("extra_flat_per_item", 0)
            row["extra flat (USD)"] = to_usd(b.get("extra_flat_per_item", 0))
        row[f"landed ({ccy})"] = b["landed_native"]
        row["landed (USD)"] = b["landed_usd"]
        row["unit cost (USD)"] = b["unit_cost_usd"]
        rows.append(row)

    df = pd.DataFrame(rows)

    # Column formatting — all native-currency money columns are integers for JPY
    col_config = {
        "#": st.column_config.NumberColumn("#", width="small"),
        "source_id": st.column_config.TextColumn("source_id", width="small",
                                                  help="Join key — do not edit"),
        "brand": st.column_config.TextColumn("brand", width="small",
                                              help="Editable. Set to 'Vintage' to clear detected brand."),
        "proposed title": st.column_config.TextColumn("proposed title", width="large",
                                                       help="Editable. Per-category template: bags include model/size; coats include length/origin; etc. Saves as override_title."),
        "model_name": st.column_config.TextColumn("model_name", width="small",
                                                   help="Editable. Luxury model — 'Speedy', 'Neverfull', 'Mamma Baguette', 'Classic Flap', etc."),
        "model_size": st.column_config.TextColumn("model_size", width="small",
                                                   help="Editable. 'MM', 'PM', '25', '30', '35'."),
        "era": st.column_config.TextColumn("era", width="small",
                                            help="Editable. Year (1997) or decade (90's, 00's, Y2K). Auto-filled from model-era DB if missing."),
        "color": st.column_config.TextColumn("color", width="small",
                                              help="Editable. One primary color or 'Multicolor'."),
        "style_adjectives": st.column_config.TextColumn("style_adjectives", width="medium",
                                                         help="Editable. Ordered garment descriptors — e.g. 'Belted V-Neck Long Sleeve Mesh'."),
        "pattern": st.column_config.TextColumn("pattern", width="small",
                                                help="Editable. Monogram, Zucca, Nova Check, Matelasse, etc."),
        "material": st.column_config.TextColumn("material", width="small",
                                                 help="Editable. e.g. 'Lambskin', 'Fox Fur', 'Denim'."),
        "origin": st.column_config.TextColumn("origin", width="small",
                                               help="Editable. 'Made in USA', 'Made in Italy', etc."),
        "product_type": st.column_config.TextColumn("product_type", width="small",
                                                     help="Editable. e.g. 'Handbag', 'Coat', 'Top'."),
        "garment_length": st.column_config.SelectboxColumn("garment_length", width="small",
                                                            options=["", "short", "midi", "long"],
                                                            help="Editable. Affects Buyee markup."),
        "qty": st.column_config.NumberColumn("qty", width="small", min_value=1, step=1,
                                              help="Editable. Lot count."),
    }
    money_fmt = "¥%d" if ccy == "JPY" else "$%.2f"
    for col in df.columns:
        if col in col_config:
            continue
        if col.startswith("landed (USD)") or col.startswith("unit cost"):
            col_config[col] = st.column_config.NumberColumn(col, format="$%.2f", width="small")
        elif col.startswith("landed"):
            col_config[col] = st.column_config.NumberColumn(col, format=money_fmt, width="small")
        else:
            col_config[col] = st.column_config.NumberColumn(col, format=money_fmt, width="small")

    # Everything that's not editable is disabled. source_id, # and all numbers are derived.
    editable_cols = {"brand", "proposed title", "era", "color", "pattern", "material",
                     "origin", "product_type", "garment_length", "qty",
                     "model_name", "model_size", "style_adjectives"}
    disabled_cols = [c for c in df.columns if c not in editable_cols]

    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
        disabled=disabled_cols,
        num_rows="fixed",
        key=f"cost_editor_{inv_data_ref.get('__source_file', 'none')}",
        height=min(700, 50 + len(rows) * 35),
    )

    sc1, sc2 = st.columns([4, 1])
    with sc1:
        if not edited.equals(df):
            st.caption("✎ Unsaved edits — click Save to write back to the JSON.")
    with sc2:
        if st.button("Save edits", key="save_cost", type="primary", use_container_width=True):
            changes = apply_cost_edits(inv_data_ref, edited)
            if changes:
                path = persist_invoice(inv_data_ref)
                st.success(f"Saved {changes} item(s) → {path.name}.")
                # Auto-recompute everything (margins, hero totals, etc.)
                # without requiring the user to manually reload.
                st.rerun()
            else:
                st.info("No changes to save.")

    # 6. Totals strip below the table
    sum_price = sum(r[f"item price ({ccy})"] for r in rows)
    sum_coupon = sum(r[f"coupon ({ccy})"] for r in rows)
    sum_sub = sum(r[f"subtotal ({ccy})"] for r in rows)
    sum_landed_native = sum(r[f"landed ({ccy})"] for r in rows)
    sum_landed_usd = sum(r["landed (USD)"] for r in rows)

    totals_html = f'''<div class="shared-inputs" style="grid-template-columns: repeat(5, 1fr); margin-top: 0.5rem;">
      <div class="cell">
        <div class="label">Σ Item price × qty</div>
        <div class="value">{fmt_native(sum_price, ccy)}</div>
      </div>
      <div class="cell">
        <div class="label">Σ Coupon</div>
        <div class="value">− {fmt_native(sum_coupon, ccy)}</div>
      </div>
      <div class="cell">
        <div class="label">Σ Subtotal</div>
        <div class="value">{fmt_native(sum_sub, ccy)}</div>
      </div>
      <div class="cell">
        <div class="label">Σ Landed {ccy}</div>
        <div class="value">{fmt_native(sum_landed_native, ccy)}</div>
        <div class="sub">subtotal + all fees + intl + customs</div>
      </div>
      <div class="cell">
        <div class="label">Σ Landed USD</div>
        <div class="value">${sum_landed_usd:,.2f}</div>
        <div class="sub">goes into Shopify Cost per Item</div>
      </div>
    </div>'''
    st.markdown(totals_html, unsafe_allow_html=True)

    # 7. Reference hint — how to interpret the table
    if is_buyee:
        st.caption(
            "**How landed is built:** `subtotal + commission + dom ship + service + intl/n + customs/n`. "
            "Commission/dom/service are joined per-item by `source_id` from Buyee's breakdown tables. "
            "Intl shipping and customs are split equally across all items per spec §6. "
            "Auction items (no `V…` source_id) typically have no commission fee."
        )
    else:
        from costs import HANDLING_RATE, IMPORT_TAX_RATE
        total_uplift = HANDLING_RATE + IMPORT_TAX_RATE
        st.caption(
            f"**How landed is built (BrandStreet / vendor invoice):** "
            f"`subtotal + handling ({HANDLING_RATE*100:.0f}%) + import ({IMPORT_TAX_RATE*100:.0f}%)`. "
            f"Both uplifts are applied additively to subtotal, not compounded — so total landed = subtotal × {1 + total_uplift:.2f}. "
            "Handling covers your time and fixed costs; import is estimated US duty. Neither is on the invoice; both are assumed rates (tune in `costs.py`)."
        )


def render_alerts(items: list, view: InvoiceView):
    """Surface items that need QA attention."""
    alerts = []
    no_brand = [i for i in items if not i["pricing"].vendor]
    if no_brand:
        alerts.append(f"{len(no_brand)} item(s) without a detected brand — prices fall into the non-branded markup tier")
    missing_material = [i for i in items if not i["item"].material and i["pricing"].item_type in ("Coat", "Jacket", "Dress", "Skirt")]
    if missing_material:
        alerts.append(f"{len(missing_material)} garment(s) without a material — Buyee markup may be undervalued")
    ceiling_hits = [i for i in items if any("ceiling" in w for w in i["pricing"].warnings)]
    if ceiling_hits:
        alerts.append(f"{len(ceiling_hits)} item(s) clamped to band ceiling — check if ceiling is right for the item")
    lot_items = [i for i in items if i["item"].quantity > 1]
    if lot_items:
        alerts.append(f"{len(lot_items)} item(s) with quantity > 1 — each unit needs individual review (tagged [REVIEW])")
    # Negative or thin margin
    thin = [i for i in items if i["pricing"].rounded_price < i["pricing"].unit_cost_usd * 1.3]
    if thin:
        alerts.append(f"{len(thin)} item(s) priced below 1.3× cost — consider raising demand or overriding")
    orph = view.orphan_fees()
    for table, sids in orph.items():
        if sids:
            alerts.append(f"{len(sids)} orphan {table.replace('_', ' ')} fee(s) — join key mismatch, transcription may be off")

    if not alerts:
        return

    items_html = "".join(f"<li>{a}</li>" for a in alerts)
    st.markdown(
        f'<div class="alerts"><h4>Needs attention · {len(alerts)}</h4>'
        f'<ul>{items_html}</ul></div>',
        unsafe_allow_html=True,
    )


def render_fx_control(default_rate: float, currency: str = "JPY") -> float:
    """Global FX rate — affects cost and pricing both.

    Currency-aware: label, bounds, and step size adjust based on the invoice's
    native currency. JPY needs <0.02 (small per-yen), EUR/GBP need >0.5
    (each unit ≈ 1 USD), USD is identity.
    """
    ccy = (currency or "JPY").upper()
    if ccy == "USD":
        # Identity — no conversion needed; lock the control to 1.0
        st.caption(f"Invoice is USD — no FX conversion needed.")
        return 1.0

    if ccy == "JPY":
        label = "FX rate (JPY → USD)"
        min_v, max_v, step, fmt = 0.001, 0.02, 0.0001, "%.4f"
    else:
        # Larger currencies (EUR, GBP, CHF, AUD, CAD…) sit near 1.0
        label = f"FX rate ({ccy} → USD)"
        min_v, max_v, step, fmt = 0.20, 3.00, 0.01, "%.4f"

    # Clamp the default to the visible range so a stale JPY default doesn't
    # cap-out the slider for an EUR invoice.
    visible_default = min(max(default_rate, min_v), max_v)

    c1, c2 = st.columns([1, 3])
    with c1:
        rate = st.number_input(
            label,
            min_value=min_v, max_value=max_v,
            value=visible_default, format=fmt, step=step,
            help="Pre-filled from the invoice's exchange_rate field; "
                 "update to match the rate your card was charged at.",
        )
    return rate


def render_shopify_inventory_panel():
    """Setup + status panel for the Shopify Admin API integration.

    Three states:
      1. No env vars set → show setup instructions + token input form
      2. Configured but never refreshed → "Fetch inventory" button
      3. Cache populated → status badge + manual refresh button
    """
    try:
        from shopify_inventory import (
            is_configured, get_shop, refresh_inventory,
            load_cached_inventory, fetch_inventory_live,
            save_inventory_cache,
        )
    except ImportError:
        return

    cached = load_cached_inventory()

    with st.expander(
        f"🛍️  Shopify inventory check"
        + (f"  ·  {cached.product_count} products cached, last refreshed {cached.humanize_age()}"
           if cached and cached.is_loaded else "  ·  not configured" if not is_configured() else "  ·  not yet fetched"),
        expanded=False,
    ):
        if not is_configured():
            st.warning(
                "**Shopify API not configured.** Without this, generated SKUs and handles "
                "may collide with products already in your store, causing failed imports "
                "or accidental product merges."
            )
            t1, t2 = st.tabs(["Client ID + Secret (recommended)", "Custom App (direct token)"])
            with t1:
                st.markdown(
                    "Use this if your app provides Client ID + Client Secret. "
                    "[Per Shopify docs](https://shopify.dev/docs/apps/build/authentication-authorization/client-secrets), "
                    "the Client Credentials grant is the simplest path — single POST, no browser.\n\n"
                    "1. In your Shopify app config, confirm the scopes include `read_products`\n"
                    "2. Run this in your terminal:\n"
                    "    ```\n"
                    "    cd /Users/kat/workspace/hot-girl-shopify/japanese-invoice-transcriber\n"
                    "    uv run shopify_oauth.py\n"
                    "    ```\n"
                    "3. Paste your shop domain + Client ID + Client Secret when prompted. "
                    "Token is fetched, saved to `.env`, and auto-refreshes every 24h.\n"
                    "4. Restart this Streamlit app."
                )
                st.caption(
                    "If client_credentials isn't supported by your app type, retry with "
                    "`uv run shopify_oauth.py --redirect` to use the browser-based OAuth flow."
                )
            with t2:
                st.markdown(
                    "Easiest if you have store admin access.\n\n"
                    "1. Shopify Admin → Settings → **Apps and sales channels** → **Develop apps**\n"
                    "2. Create app → Configuration → enable `read_products` scope → Install\n"
                    "3. API credentials → reveal **Admin API access token** (starts with `shpat_`)\n"
                    "4. Add to your project's `.env`:\n"
                    "```\n"
                    "SHOPIFY_SHOP=paststudies.myshopify.com\n"
                    "SHOPIFY_ADMIN_TOKEN=shpat_xxx\n"
                    "```\n"
                    "5. Restart the app. (No auto-refresh needed — Custom App tokens don't expire.)"
                )
            return

        # Configured. Show shop + cache status, refresh button.
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**Shop:** `{get_shop()}`")
            if cached and cached.is_loaded:
                st.markdown(
                    f"**Cached:** {cached.product_count:,} products · "
                    f"{len(cached.skus):,} SKUs · "
                    f"{len(cached.handles):,} handles  "
                    f"·  last refreshed **{cached.humanize_age()}**"
                )
                age_h = cached.age_hours
                if age_h is not None and age_h > 24:
                    st.caption(
                        f"ⓘ Cache is {age_h/24:.0f} day(s) old. Click **Refresh** "
                        f"if you've manually added products to Shopify since."
                    )
            else:
                st.info("No inventory cached yet. Click Refresh to fetch.")
        with c2:
            if st.button("🔄 Refresh", key="shopify_refresh", use_container_width=True):
                with st.spinner("Fetching from Shopify..."):
                    fresh = fetch_inventory_live()
                if fresh.error:
                    st.error(f"✗ {fresh.error}")
                else:
                    save_inventory_cache(fresh)
                    st.success(
                        f"✓ Fetched {fresh.product_count} products, "
                        f"{len(fresh.skus)} SKUs."
                    )
                    st.rerun()


def render_shopify_catalogue_tab() -> None:
    """Top-level Shopify catalogue audit tab.

    Three sections:
      1. Connection status + manual refresh (mirrors the inventory pre-flight panel)
      2. Catalogue scan — surfaces products with no photos + wrong vendor
      3. Duplicate finder — same scan as the Export-tab tool, accessible without
         picking an invoice first
    """
    try:
        from shopify_inventory import (
            is_configured, get_shop, load_cached_inventory,
            fetch_inventory_live, save_inventory_cache,
        )
    except ImportError:
        st.error("`shopify_inventory` module not importable.")
        return

    if not is_configured():
        st.warning(
            "**Shopify API not configured.** Set `SHOPIFY_SHOP` and a token in "
            "`.env` to enable catalogue audits. See the Export tab on any invoice "
            "for setup instructions."
        )
        return

    cached = load_cached_inventory()

    # ----- 1. Connection / cache status -----------------------------------
    st.markdown("### Connection")
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(f"**Shop:** `{get_shop()}`")
        if cached and cached.is_loaded:
            st.markdown(
                f"**Cached inventory:** {cached.product_count:,} products · "
                f"{len(cached.skus):,} SKUs · {len(cached.handles):,} handles · "
                f"last refreshed **{cached.humanize_age()}**"
            )
        else:
            st.info(
                "No inventory cached yet. The catalogue scan below fetches live data "
                "and doesn't depend on the cache."
            )
    with c2:
        if st.button("🔄 Refresh cache", key="catalogue_refresh_cache",
                     use_container_width=True):
            with st.spinner("Fetching from Shopify..."):
                fresh = fetch_inventory_live()
            if fresh.error:
                st.error(f"✗ {fresh.error}")
            else:
                save_inventory_cache(fresh)
                st.success(
                    f"✓ Refreshed: {fresh.product_count} products, "
                    f"{len(fresh.skus)} SKUs."
                )
                st.rerun()

    st.markdown("---")

    # ----- 2. Catalogue scan ----------------------------------------------
    st.markdown("### Catalogue audit")
    st.caption(
        "Walks every product in your Shopify store and flags two common "
        "data-hygiene problems: listings with **no photos** (placeholder or "
        "aborted upload), and listings where **vendor is the store name** "
        "instead of the actual brand (`Past Studies` / `paststudies`)."
    )

    sc1, sc2, sc3 = st.columns([2, 2, 1])
    with sc1:
        extra_bad = st.text_input(
            "Additional bad vendor strings (comma-separated)",
            value=st.session_state.get("catalogue_extra_bad_vendors", ""),
            placeholder="e.g. unknown, default",
            key="catalogue_extra_bad_vendors",
            help="Treated as case-insensitive matches in addition to the "
                 "built-in 'Past Studies' variants.",
        )
    with sc2:
        scope_choice = st.radio(
            "Scope",
            ["Live on website", "All active", "All products (incl. drafts)"],
            index=0,
            key="catalogue_scope_choice",
            horizontal=True,
            help=(
                "**Live on website** = status `active` AND published to the "
                "Online Store sales channel (the strictest, default — these "
                "are what customers actually see). "
                "**All active** includes active products that aren't "
                "published to Online Store. "
                "**All** also includes drafts and archived."
            ),
        )
    with sc3:
        scan_clicked = st.button(
            "🔍 Scan now", key="catalogue_scan_btn",
            use_container_width=True, type="primary",
        )

    if scan_clicked:
        from shopify_push import scan_catalogue_issues
        bad_extra = [s.strip() for s in (extra_bad or "").split(",") if s.strip()]
        scope_map = {
            "Live on website": "live",
            "All active": "active",
            "All products (incl. drafts)": "all",
        }
        try:
            with st.spinner("Walking the catalogue — usually 5-30 seconds depending on size…"):
                result = scan_catalogue_issues(
                    bad_vendors=bad_extra,
                    scope=scope_map.get(scope_choice, "live"),
                )
        except Exception as e:
            # Last-resort guard — scan_catalogue_issues itself should return
            # an error dict, but anything raised here would otherwise crash
            # the whole tab.
            result = {
                "error": f"unhandled {type(e).__name__}: {e}",
                "fetched": 0, "scanned": 0,
                "no_photos": [], "wrong_vendor": [],
                "by_vendor": {}, "bad_vendor_list": [],
                "scope": scope_map.get(scope_choice, "live"),
            }
        st.session_state["catalogue_scan_result"] = result

    result = st.session_state.get("catalogue_scan_result")
    if not result:
        st.info("Click **Scan now** to start an audit.")
    elif result.get("error"):
        st.error(
            f"Scan failed: {result['error']}  "
            f"(fetched {result.get('fetched', 0)} products before failure)"
        )
        st.caption(
            "If this is a timeout, try again — the scanner now retries each page "
            "3× with backoff. If it keeps timing out, your store may be very large "
            "(thousands of products) and you may need to scan during off-peak hours."
        )
    else:
        if result.get("partial_error"):
            st.warning(result["partial_error"])
        m1, m2, m3, m4 = st.columns(4)
        scope_label = {
            "live": "Live on website",
            "active": "All active",
            "all": "All products",
        }.get(result.get("scope", "live"), result.get("scope", "?"))
        m1.metric(f"Scanned ({scope_label})", f"{result['scanned']:,}")
        m2.metric("⚠️ No photos", f"{len(result['no_photos']):,}")
        m3.metric("⚠️ Wrong vendor", f"{len(result['wrong_vendor']):,}")
        clean = result["scanned"] - len(result["no_photos"]) - len(result["wrong_vendor"])
        m4.metric("Clean", f"{clean:,}")
        st.caption(
            f"Total products in store: {result['fetched']:,}. "
            f"Bad-vendor blocklist used: {', '.join(repr(v) for v in result['bad_vendor_list'])}."
        )

        if result["no_photos"]:
            with st.expander(
                f"📷 {len(result['no_photos'])} products with NO PHOTOS",
                expanded=True,
            ):
                # Bulk "Move to draft" action — only acts on products that are
                # actually LIVE on the Online Store sales channel. A no-photo
                # draft is harmless; a no-photo LIVE product is broken customer
                # experience. This button un-publishes the latter so they stop
                # hurting the storefront until a photo is added.
                live_no_photos = [
                    r for r in result["no_photos"] if r.get("live_on_website")
                ]
                draft_no_photos = [
                    r for r in result["no_photos"] if not r.get("live_on_website")
                ]

                if live_no_photos:
                    st.caption(
                        f"**{len(live_no_photos)}** of these are LIVE on the Online Store — "
                        f"customers see them right now without an image. "
                        f"The remaining {len(draft_no_photos)} are drafts (harmless until activated)."
                    )
                    np_c1, np_c2 = st.columns([2, 1])
                    with np_c1:
                        confirm_unpublish = st.checkbox(
                            f"Confirm un-publish {len(live_no_photos)} LIVE no-photo "
                            f"product(s) → set status `draft`",
                            value=False,
                            key="catalogue_confirm_unpublish_no_photo",
                            help="This sets product status to 'draft' on Shopify, "
                                 "removing the listing from all storefronts until "
                                 "you manually re-activate. Reversible via Shopify "
                                 "admin or by setting status back to 'active'.",
                        )
                    with np_c2:
                        unpublish_clicked = st.button(
                            f"🛡️ Move {len(live_no_photos)} to draft",
                            key="catalogue_unpublish_no_photo_btn",
                            use_container_width=True,
                            type="primary",
                            disabled=not confirm_unpublish,
                        )

                    if unpublish_clicked:
                        from shopify_push import update_product_status
                        ok_count = 0
                        fail_log: list[str] = []
                        progress = st.progress(0.0, text="Moving to draft…")
                        for i, r in enumerate(live_no_photos):
                            status, _ = update_product_status(r["id"], "draft")
                            if status == 200:
                                ok_count += 1
                            else:
                                fail_log.append(
                                    f"{r['id']} {r['title'][:50]} → HTTP {status}"
                                )
                            progress.progress(
                                (i + 1) / len(live_no_photos),
                                text=f"Moved {i+1}/{len(live_no_photos)}…",
                            )
                        progress.empty()
                        if ok_count == len(live_no_photos):
                            st.success(
                                f"✓ Un-published {ok_count} no-photo product(s) "
                                f"from Online Store (status → draft)."
                            )
                        else:
                            st.warning(
                                f"Un-published {ok_count}/{len(live_no_photos)} — "
                                f"{len(fail_log)} failed:\n\n"
                                + "\n".join(fail_log[:10])
                            )
                        st.session_state.pop("catalogue_scan_result", None)
                        st.rerun()
                else:
                    st.caption(
                        f"None of these {len(result['no_photos'])} products are "
                        f"LIVE on Online Store — all are drafts. Safe to ignore "
                        f"until activated."
                    )

                df = pd.DataFrame([
                    {
                        "Title": r["title"],
                        "Vendor": r["vendor"],
                        "Type": r["product_type"],
                        "Status": r["status"],
                        "Live on web": "✅" if r["live_on_website"] else "—",
                        "Published": r["published_at"] or "—",
                        "Created": r["created_at"],
                        "Edit": r["admin_url"],
                    }
                    for r in result["no_photos"]
                ])
                st.dataframe(
                    df, hide_index=True, use_container_width=True,
                    column_config={
                        "Edit": st.column_config.LinkColumn(
                            "Edit", display_text="🔗 admin",
                        ),
                    },
                )

        if result["wrong_vendor"]:
            with st.expander(
                f"🏷️ {len(result['wrong_vendor'])} products with WRONG VENDOR",
                expanded=True,
            ):
                # Split rows into "we detected a brand" vs "we didn't" — the
                # bulk-fix logic handles each bucket differently.
                with_brand = [r for r in result["wrong_vendor"] if r.get("detected_brand")]
                without_brand = [r for r in result["wrong_vendor"] if not r.get("detected_brand")]

                st.caption(
                    "Vendor was set to the store name. For each product we scan the title "
                    "+ tags against the known-brand corpus in `rules.yaml` and suggest the "
                    "real brand. Detected brands get applied per-product; rows with no "
                    "detection fall back to a manual value (default `Vintage`)."
                )

                # Editable per-row table so the user can override the detection
                # before applying. We use a data_editor so individual rows can
                # have their Apply value tweaked.
                editable_rows = []
                for r in result["wrong_vendor"]:
                    editable_rows.append({
                        "Apply": r.get("detected_brand") or "",
                        "Title": r["title"],
                        "Current vendor": r["vendor"],
                        "Detected": r.get("detected_brand") or "—",
                        "Type": r["product_type"],
                        "Live on web": "✅" if r["live_on_website"] else "—",
                        "Created": r["created_at"],
                        "Edit": r["admin_url"],
                    })
                edit_df = pd.DataFrame(editable_rows)
                edited = st.data_editor(
                    edit_df,
                    hide_index=True,
                    use_container_width=True,
                    key="catalogue_wrong_vendor_editor",
                    column_config={
                        "Apply": st.column_config.TextColumn(
                            "Apply",
                            help="Vendor that will be written to Shopify. Pre-filled "
                                 "from the title-based detection — edit before clicking "
                                 "the bulk-fix button to override per row.",
                            width="medium",
                        ),
                        "Title": st.column_config.TextColumn(disabled=True),
                        "Current vendor": st.column_config.TextColumn(disabled=True),
                        "Detected": st.column_config.TextColumn(disabled=True),
                        "Type": st.column_config.TextColumn(disabled=True),
                        "Live on web": st.column_config.TextColumn(disabled=True),
                        "Created": st.column_config.TextColumn(disabled=True),
                        "Edit": st.column_config.LinkColumn(
                            "Edit", display_text="🔗 admin", disabled=True,
                        ),
                    },
                )

                # Summary + fallback for un-detected rows
                stat_c1, stat_c2, stat_c3 = st.columns([1, 1, 2])
                stat_c1.metric("Detected", f"{len(with_brand)}")
                stat_c2.metric("No detection", f"{len(without_brand)}")
                with stat_c3:
                    fallback = st.text_input(
                        "Fallback for rows where the Apply cell is blank",
                        value=st.session_state.get("catalogue_fix_fallback", "Vintage"),
                        key="catalogue_fix_fallback",
                        help="Set per-row Apply directly in the table above, or leave "
                             "blank to use this fallback (typically 'Vintage' for "
                             "unbranded items).",
                    )

                # Build the final plan: per-product vendor based on edited table
                plan: list[tuple[int, str, str]] = []  # (product_id, new_vendor, title)
                for original, edited_row in zip(result["wrong_vendor"], edited.to_dict("records")):
                    apply_val = (edited_row.get("Apply") or "").strip() or (fallback or "").strip()
                    if apply_val:
                        plan.append((original["id"], apply_val, original["title"]))

                bc1, bc2 = st.columns([2, 1])
                with bc1:
                    st.caption(
                        f"Plan: update **{len(plan)}** of {len(result['wrong_vendor'])} "
                        f"products. Distinct vendors that will be set: "
                        f"`{', '.join(sorted({v for _, v, _ in plan})) or '(none)'}`."
                    )
                with bc2:
                    fix_clicked = st.button(
                        f"🔧 Apply to {len(plan)} products",
                        key="catalogue_fix_vendor_btn",
                        use_container_width=True,
                        type="primary",
                        disabled=len(plan) == 0,
                    )

                if fix_clicked:
                    from shopify_push import update_product_vendor
                    ok_count = 0
                    fail_log: list[str] = []
                    progress = st.progress(0.0, text="Updating vendors…")
                    for i, (pid, new_v, title) in enumerate(plan):
                        status, _ = update_product_vendor(pid, new_v)
                        if status == 200:
                            ok_count += 1
                        else:
                            fail_log.append(f"{pid} {title[:50]} → HTTP {status}")
                        progress.progress(
                            (i + 1) / len(plan),
                            text=f"Updated {i+1}/{len(plan)}…",
                        )
                    progress.empty()
                    if ok_count == len(plan):
                        st.success(
                            f"✓ Updated vendor on {ok_count} products using "
                            f"per-product detected brands."
                        )
                    else:
                        st.warning(
                            f"Updated {ok_count}/{len(plan)} — "
                            f"{len(fail_log)} failed:\n\n" + "\n".join(fail_log[:10])
                        )
                    st.session_state.pop("catalogue_scan_result", None)
                    st.rerun()

        with st.expander(
            f"📊 Vendor frequency ({len(result['by_vendor'])} unique vendors)",
            expanded=False,
        ):
            st.caption("Quick sanity check — anything weird in the top of this list?")
            df = pd.DataFrame(
                [{"Vendor": v, "Products": n} for v, n in result["by_vendor"].items()]
            )
            st.dataframe(df, hide_index=True, use_container_width=True, height=400)

    st.markdown("---")

    # ----- 3. Duplicate finder --------------------------------------------
    st.markdown("### Duplicate finder")
    st.caption(
        "Useful after an accidental double-publish. Scans the live store for "
        "products sharing the same handle or SKU and lists them so you can delete "
        "the extras."
    )
    dc1, dc2 = st.columns([2, 1])
    with dc1:
        sku_prefix = st.text_input(
            "Filter by SKU prefix (optional)",
            value=st.session_state.get("catalogue_dupe_prefix", ""),
            placeholder="e.g. LOU_, FEN_, BUR_",
            key="catalogue_dupe_prefix",
        )
    with dc2:
        dupe_clicked = st.button(
            "🧹 Scan for duplicates", key="catalogue_dupe_btn",
            use_container_width=True,
        )

    if dupe_clicked:
        from shopify_push import find_duplicates
        with st.spinner("Walking the catalogue for duplicate handles + SKUs…"):
            dupe_result = find_duplicates(sku_prefix=sku_prefix.strip() or None)
        st.session_state["catalogue_dupe_result"] = dupe_result

    dupe_result = st.session_state.get("catalogue_dupe_result")
    if dupe_result:
        if dupe_result.get("error"):
            st.error(f"Scan failed: {dupe_result['error']}")
        else:
            st.markdown(
                f"Scanned **{dupe_result['fetched']}** products → "
                f"**{dupe_result['duplicate_groups']}** duplicate groups found."
            )
            n_handle_dupes = len(dupe_result["by_handle"])
            n_sku_dupes = len(dupe_result["by_sku"])
            if n_handle_dupes:
                st.markdown("##### Handle duplicates")
                rows = []
                for handle, prods in dupe_result["by_handle"].items():
                    for p in prods:
                        rows.append({
                            "handle": handle,
                            "product_id": p.get("id"),
                            "title": p.get("title"),
                            "vendor": p.get("vendor"),
                            "created_at": (p.get("created_at") or "")[:19],
                            "delete_url": (
                                f"https://{get_shop()}/admin/products/{p.get('id')}"
                            ),
                        })
                st.dataframe(
                    pd.DataFrame(rows), hide_index=True, use_container_width=True,
                    column_config={
                        "delete_url": st.column_config.LinkColumn(
                            "delete_url", display_text="🔗 open",
                        ),
                    },
                )
            if n_sku_dupes:
                st.markdown("##### SKU duplicates")
                rows = []
                for sku, variants in dupe_result["by_sku"].items():
                    for v in variants:
                        rows.append({
                            "sku": sku,
                            "product_id": v["product_id"],
                            "title": v["product_title"],
                            "created_at": (v.get("created_at") or "")[:19],
                        })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            if not n_handle_dupes and not n_sku_dupes:
                st.success("✓ No duplicates found.")


def render_assumed_rates_controls(invoice_currency: str = "JPY") -> tuple[float, float, float, float]:
    """Editable assumed rates — affect this invoice's landed cost.

    Returns (handling_rate, import_tax_rate, extra_rate, extra_flat).

    - Handling + import: only meaningful on vendor invoices (BrandStreet, DKC,
      manual). Buyee invoices have actual fee tables, but the controls still
      render so the user can experiment.
    - Extra %: ad-hoc per-item percentage on top of subtotal (default 0%).
    - Extra flat: lump-sum amount in invoice currency, split equally across
      items (default 0). Useful for an extra shipping fee paid separately.
    """
    from costs import HANDLING_RATE as _DEF_HANDLING, IMPORT_TAX_RATE as _DEF_IMPORT
    ccy = (invoice_currency or "JPY").upper()
    flat_step = 100.0 if ccy == "JPY" else 1.0
    flat_fmt = "%.0f" if ccy == "JPY" else "%.2f"
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        handling = st.number_input(
            "Handling %",
            min_value=0.0, max_value=0.50,
            value=float(st.session_state.get("handling_rate", _DEF_HANDLING)),
            step=0.01, format="%.2f",
            help="Per-item % of subtotal. Default 10%. Vendor-invoice processing markup.",
            key="handling_rate",
        )
    with c2:
        import_tax = st.number_input(
            "Import tax %",
            min_value=0.0, max_value=0.50,
            value=float(st.session_state.get("import_tax_rate", _DEF_IMPORT)),
            step=0.01, format="%.2f",
            help="Per-item % of subtotal. Default 15%. Estimated US import duty.",
            key="import_tax_rate",
        )
    with c3:
        extra_rate = st.number_input(
            "Extra %",
            min_value=0.0, max_value=1.0,
            value=float(st.session_state.get("extra_rate", 0.0)),
            step=0.01, format="%.2f",
            help="Ad-hoc per-item % on top of subtotal. Default 0%.",
            key="extra_rate",
        )
    with c4:
        extra_flat = st.number_input(
            f"Extra flat ({ccy})",
            min_value=0.0,
            value=float(st.session_state.get("extra_flat", 0.0)),
            step=flat_step, format=flat_fmt,
            help=f"Lump-sum extra cost in {ccy}, split evenly across items. "
                 f"E.g. an additional shipping invoice paid separately. Default 0.",
            key="extra_flat",
        )
    return handling, import_tax, extra_rate, extra_flat


def render_pricing_controls():
    """Demand + sort + filter — only relevant on the Pricing tab."""
    c1, c2, c3 = st.columns([2, 1.2, 1.2])
    with c1:
        demand = st.slider(
            "Demand multiplier",
            min_value=0.5, max_value=1.5, value=1.0, step=0.05,
            key="demand_for_export",
            help="Global multiplier applied after markup, band, and market adjustment. 1.0 = baseline.",
        )
    with c2:
        sort_by = st.selectbox(
            "Sort by",
            ["Risk first", "Price (high → low)", "Price (low → high)", "Cost (high → low)", "Brand"],
        )
    with c3:
        filt = st.selectbox(
            "Filter",
            ["All items", "Only warnings", "Bags & wallets", "Clothing", "Accessories"],
        )
    return demand, sort_by, filt


def item_card_html(enriched: dict, currency: str, invoice_stem: str = "") -> str:
    item = enriched["item"]
    b = enriched["breakdown"]
    p = enriched["pricing"]

    # Optional thumbnail from Buyee auction-page scrape
    photo_html = ""
    if invoice_stem:
        try:
            from buyee.photo_scraper import photo_for
            from photos import photo_data_uri
            photo_path = photo_for(invoice_stem, item.source_id)
            if photo_path:
                data_uri = photo_data_uri(photo_path, max_width=200)
                if data_uri:
                    # Click → open full-size in new tab via target="_blank"
                    photo_html = (
                        f'<a class="item-photo-link" href="{data_uri}" target="_blank" '
                        f'title="Open full-size">'
                        f'<img class="item-photo" src="{data_uri}" alt="{item.source_id}" />'
                        f'</a>'
                    )
        except Exception:
            pass  # photos are optional; never block the card render

    # Header row — brand tag + title + numbers
    brand = p.vendor or "UNBRANDED"
    brand_class = "unknown" if not p.vendor else ""
    card_class = "item-card"
    if p.warnings:
        card_class += " has-warning"
    if not p.vendor:
        card_class += " no-brand"

    # Numbers
    cost_str = fmt_usd(p.unit_cost_usd, 2)
    price_str = fmt_usd(p.rounded_price)
    margin_val = p.rounded_price - p.unit_cost_usd
    margin_str = fmt_usd(margin_val)
    mclass = margin_class(margin_val, p.unit_cost_usd)

    # Chips
    chips = []
    if p.item_type:
        chips.append(f'<span class="chip">{p.item_type}</span>')
    if item.material:
        chips.append(f'<span class="chip">{item.material}</span>')
    if item.garment_length:
        chips.append(f'<span class="chip">{item.garment_length}</span>')
    if item.condition_notes:
        chips.append(f'<span class="chip">{item.condition_notes[:50]}</span>')
    if item.quantity > 1:
        chips.append(f'<span class="chip"><b>lot × {item.quantity}</b></span>')
    source = f"{item.source_platform or ''}({item.source_id})" if item.source_platform else item.source_id
    chips.append(f'<span class="chip sku">{source}</span>')

    # Warnings
    warnings_html = ""
    if p.warnings:
        badges = "".join(f'<span class="warning">⚠ {w}</span>' for w in p.warnings)
        warnings_html = f'<div class="item-warnings">{badges}</div>'

    # Full detail (expandable)
    cost_rows = [f'<tr><td>Item price × qty</td><td class="v">{fmt_native(b["item_price"], currency)}</td></tr>']
    if b["coupon"]:
        cost_rows.append(f'<tr><td>− Coupon</td><td class="v">− {fmt_native(b["coupon"], currency)}</td></tr>')
    if b["commission"]:
        cost_rows.append(f'<tr><td>+ Commission</td><td class="v">{fmt_native(b["commission"], currency)}</td></tr>')
    if b["domestic_shipping"]:
        cost_rows.append(f'<tr><td>+ Domestic shipping</td><td class="v">{fmt_native(b["domestic_shipping"], currency)}</td></tr>')
    if b["service"]:
        cost_rows.append(f'<tr><td>+ Service fee</td><td class="v">{fmt_native(b["service"], currency)}</td></tr>')
    if b["intl_share"]:
        cost_rows.append(f'<tr><td>+ Intl ship (÷n)</td><td class="v">{fmt_native(b["intl_share"], currency)}</td></tr>')
    if b["customs_share"]:
        cost_rows.append(f'<tr><td>+ Customs (÷n)</td><td class="v">{fmt_native(b["customs_share"], currency)}</td></tr>')
    if b["handling_amount"]:
        from costs import HANDLING_RATE
        cost_rows.append(f'<tr><td>+ Handling ({HANDLING_RATE*100:.0f}%)</td><td class="v">{fmt_native(b["handling_amount"], currency)}</td></tr>')
    if b["import_amount"]:
        from costs import IMPORT_TAX_RATE
        cost_rows.append(f'<tr><td>+ Import tax ({IMPORT_TAX_RATE*100:.0f}%)</td><td class="v">{fmt_native(b["import_amount"], currency)}</td></tr>')
    cost_rows.append(f'<tr class="final"><td>Landed</td><td class="v">{fmt_native(b["landed_native"], currency)} ({fmt_usd(b["landed_usd"], 2)})</td></tr>')

    price_rows = [f'<tr><td>Unit cost</td><td class="v">{fmt_usd(p.unit_cost_usd, 2)}</td></tr>']
    if currency == "JPY":
        price_rows.append(f'<tr><td>× 1.2 handling</td><td class="v">{fmt_usd(p.markup_applied_to, 2)}</td></tr>')
    price_rows.append(f'<tr><td>× Markup ({p.markup:.2f}×)</td><td class="v">{fmt_usd(p.base_price, 2)}</td></tr>')
    if p.band_floor is not None or p.band_ceil is not None:
        band = f"[${p.band_floor or ''}–${p.band_ceil or '∞'}]"
        price_rows.append(f'<tr><td>Band {band}</td><td class="v">{fmt_usd(p.after_band, 2)}</td></tr>')
    if p.market_adjustment != 1.0:
        price_rows.append(f'<tr><td>× Market adj ({p.market_adjustment})</td><td class="v">{fmt_usd(p.after_adjustment, 2)}</td></tr>')
    if p.demand_multiplier != 1.0:
        price_rows.append(f'<tr><td>× Demand ({p.demand_multiplier})</td><td class="v">{fmt_usd(p.after_demand, 2)}</td></tr>')
    price_rows.append(f'<tr class="final"><td>Variant Price</td><td class="v">{fmt_usd(p.rounded_price)}</td></tr>')
    # When the price was manually overridden, the algorithmic markup (`p.markup`)
    # is stale — show the EFFECTIVE markup actually achieved so the user can
    # see what the override implies for margin.
    if getattr(item, "override_price", None) and p.unit_cost_usd > 0:
        eff_markup = p.rounded_price / p.unit_cost_usd
        eff_margin = p.rounded_price - p.unit_cost_usd
        eff_margin_pct = (eff_margin / p.unit_cost_usd) * 100
        price_rows.append(
            f'<tr><td><i>Effective markup (override)</i></td>'
            f'<td class="v"><i>{eff_markup:.2f}× · +{eff_margin_pct:.0f}% margin</i></td></tr>'
        )

    orig_html = ""
    if item.description_original and item.description_original != item.description_english:
        orig_html = f'<div class="orig">原文: {item.description_original[:120]}</div>'

    return f"""
    <div class="{card_class}">
      <div class="item-head">
        {photo_html}
        <div class="item-title">
          <span class="item-brand-tag {brand_class}">{brand}</span>
          {item.description_english}
        </div>
        <div><span class="item-cost">cost <span class="item-cost-num">{cost_str}</span></span></div>
        <div><span class="to-arrow">→</span> <span class="item-price">{price_str}</span></div>
        <div><span class="item-margin {mclass}">margin {margin_str}</span></div>
      </div>
      <div class="item-meta">{"".join(chips)}</div>
      {warnings_html}
      <details class="item-detail">
        <summary>detail</summary>
        <div class="body">
          <div>
            <h5>Cost breakdown</h5>
            <table>{"".join(cost_rows)}</table>
          </div>
          <div>
            <h5>Pricing pipeline</h5>
            <table>{"".join(price_rows)}</table>
          </div>
        </div>
        {orig_html}
      </details>
    </div>
    """


FILTERS = {
    "All items":     lambda p, item: True,
    "Only warnings": lambda p, item: bool(p.warnings) or not p.vendor,
    "Bags & wallets": lambda p, item: p.item_type in {"Handbag", "Shoulder Bag", "Clutch Bag", "Clutch", "Tote Bag", "Hobo Bag", "Pouch", "Belt Bag", "Bag", "Wallet", "Card Holder", "Key Holder"},
    "Clothing":      lambda p, item: p.item_type in {"Coat", "Jacket", "Blazer", "Dress", "Top", "Sweater", "Cardigan", "Skirt", "Pants"},
    "Accessories":   lambda p, item: p.item_type in {"Sunglasses", "Belt", "Scarf", "Shawl", "Stole"},
}


def sort_key(sort_by: str):
    if sort_by == "Risk first":
        # Warnings first (more = higher), then no-brand, then by price desc
        return lambda x: (
            -(len(x["pricing"].warnings)),
            0 if x["pricing"].vendor else -1,
            -x["pricing"].rounded_price,
        )
    if sort_by == "Price (high → low)":
        return lambda x: -x["pricing"].rounded_price
    if sort_by == "Price (low → high)":
        return lambda x: x["pricing"].rounded_price
    if sort_by == "Cost (high → low)":
        return lambda x: -x["pricing"].unit_cost_usd
    if sort_by == "Brand":
        return lambda x: (x["pricing"].vendor or "zzz").lower()
    return lambda x: 0


def render_items_table(items: list, sort_by: str, filt: str, currency: str,
                       invoice_date: str | None = None,
                       inv_data_ref: dict | None = None,
                       invoice_stem: str = ""):
    """Shopify-style table view. QA-only columns (disabled, left) and Shopify-bound
    columns (editable, right) are visually separated. Edits save back to the JSON.

    invoice_stem: when provided, looks up cached photo thumbnails from
    output/photos/<invoice_stem>/<source_id>.jpg and renders them in a
    leading "Photo" column.
    """
    import urllib.parse as _urlparse
    from to_shopify import make_sku, shopify_category
    # Photo lookup helpers — soft import so the table renders even if the
    # buyee photo scraper or Pillow aren't available.
    try:
        from buyee.photo_scraper import photo_for as _photo_for
        from photos import photo_data_uri as _photo_data_uri
    except Exception:
        _photo_for = lambda *a, **k: None
        _photo_data_uri = lambda *a, **k: None

    filtered = [i for i in items if FILTERS[filt](i["pricing"], i["item"])]
    filtered.sort(key=sort_key(sort_by))

    hdr = (
        f'<div class="items-heading">'
        f'<h3>Items — Shopify preview</h3>'
        f'<div class="items-count">{len(filtered)} of {len(items)} shown · '
        f'<span style="color:#999; font-style:italic">grey columns = QA only, not exported</span></div>'
        f'</div>'
    )
    st.markdown(hdr, unsafe_allow_html=True)

    if not filtered:
        st.markdown('<p style="color:#999">No items match this filter.</p>', unsafe_allow_html=True)
        return

    # Build rows — QA cols on left, Shopify cols on right
    used_skus: set[str] = set()
    rows = []
    has_any_photo = False
    for enriched in filtered:
        item = enriched["item"]
        p = enriched["pricing"]
        margin = p.rounded_price - p.unit_cost_usd
        warn_count = len(p.warnings) + (0 if p.vendor else 1)
        warn_badge = f"⚠ {warn_count}" if warn_count else ""
        vendor = canon_brand(item.detected_brand) or "Vintage"
        # make_sku now returns (sku, original_proposal); the proposal is for
        # collision-log only — Pricing tab preview doesn't need it.
        sku, _ = make_sku(vendor, invoice_date, item.source_id, used_skus)
        # Bands are currently disabled (see pricing.price_item); always show "—"
        # so the column still aligns with historical layouts. Field kept in the
        # PricingResult for forward compatibility if we re-enable later.
        band = f"${p.band_floor or ''}–${p.band_ceil or ''}" if (p.band_floor or p.band_ceil) else "—"

        # Effective markup: respects manual price overrides. p.markup is the
        # ALGORITHM's intended multiplier; when the user overrides, the actual
        # ratio is rounded_price / unit_cost_usd. Show the effective number with
        # a marker when an override is in play.
        if getattr(item, "override_price", None) and p.unit_cost_usd > 0:
            eff_markup = p.rounded_price / p.unit_cost_usd
            markup_display = f"{eff_markup:.2f}× ✎"
        else:
            markup_display = f"{p.markup:.2f}×"

        # Photo data URI — None if no thumbnail cached for this item
        photo_uri = ""
        if invoice_stem:
            ppath = _photo_for(invoice_stem, item.source_id)
            if ppath:
                photo_uri = _photo_data_uri(ppath, max_width=120) or ""
                if photo_uri:
                    has_any_photo = True

        # Gem.app comp search — prefer the user's override_title if set
        # (curated, highest signal), else fall back to the computed title.
        # Click-through opens gem.app in a new tab pre-filled, so the user
        # can spot-check resale comps across eBay / Grailed / RealReal /
        # Vestiaire / Fashionphile / Etsy etc. in one place. Zero API spend,
        # zero scraping — just a deep-link.
        composed_title = compose_title(item)
        search_seed = (getattr(item, "override_title", None) or composed_title).strip()
        gem_url = f"https://gem.app/search?terms={_urlparse.quote_plus(search_seed)}"

        # Margin % — visibility into the per-item gross margin. When the YAML
        # bracket priced this item, also show the target margin from the
        # bracket so the user can compare "intended" vs "actual" (they diverge
        # when round_price snaps to a 25/45/75/95 boundary, or when an
        # override_price clobbers the formula output).
        if p.rounded_price > 0:
            margin_pct = (p.rounded_price - p.unit_cost_usd) / p.rounded_price
            if getattr(p, "target_margin", None) is not None:
                margin_pct_display = (
                    f"{margin_pct*100:.0f}% (target {int(p.target_margin*100)}%)"
                )
            else:
                margin_pct_display = f"{margin_pct*100:.0f}%"
        else:
            margin_pct_display = "—"

        rows.append({
            # --- Photo (leading column when any item has one) ---
            "Photo": photo_uri,
            # --- QA-only columns (left, greyed) ---
            "⚠": warn_badge,
            "Markup": markup_display,
            "Band": band,
            "Margin": round(margin, 2),
            "Margin %": margin_pct_display,
            "Source ID": item.source_id,
            "🔍 Gem": gem_url,
            # --- Shopify CSV columns (right, black) ---
            "Title": composed_title,
            "Vendor": vendor,
            "Product Category": shopify_category(item.product_type),
            "Type": p.item_type or "",
            "Cost per Item": round(p.unit_cost_usd, 2),
            "Variant Price": p.rounded_price,
            "SKU": sku,
        })

    df = pd.DataFrame(rows)
    # Drop the Photo column entirely when no item has one — prevents an
    # empty leading column from cluttering the table for V-prefix-only invoices.
    if not has_any_photo and "Photo" in df.columns:
        df = df.drop(columns=["Photo"])

    qa_cols = ["⚠", "Markup", "Band", "Margin", "Margin %", "Source ID", "🔍 Gem"]
    shopify_cols = ["Title", "Vendor", "Product Category", "Type", "Cost per Item", "Variant Price", "SKU"]

    col_config = {
        "Photo": st.column_config.ImageColumn("📷", width="small",
                                                help="First photo from Buyee auction listing. Click to enlarge."),
        # QA (left, narrow)
        "⚠": st.column_config.TextColumn("⚠", width="small",
                                          help="QA — warning count per item (see detail below)"),
        "Markup": st.column_config.TextColumn("Markup", width="small",
                                               help="QA — effective markup (Variant Price / Cost). ✎ marks items with manual price override."),
        "Band": st.column_config.TextColumn("Band", width="small",
                                             help="QA — price floor–ceiling from the rule tables (clamps the raw markup output)"),
        "Margin": st.column_config.NumberColumn("Margin", format="$%.2f", width="small",
                                                 help="QA — Price minus Cost per Item"),
        "Margin %": st.column_config.TextColumn("Margin %", width="small",
                                                 help="QA — gross margin percent: (price − cost) / price. "
                                                      "When YAML pricing brackets are active, shows the "
                                                      "target margin in parens. Differences come from "
                                                      "round_price snapping to 25/45/75/95 or from a manual "
                                                      "override_price."),
        "Source ID": st.column_config.TextColumn("Source ID", width="small",
                                                  help="QA — the auction/auth code from the invoice (join key)"),
        "🔍 Gem": st.column_config.LinkColumn(
            "🔍 Gem", width="small", display_text="search ↗",
            help="Open Gem.app cross-resale search in a new tab, pre-filled with this "
                 "item's title. Aggregates eBay, Grailed, Vestiaire, The RealReal, "
                 "Fashionphile, Etsy, Poshmark, Farfetch, LiveAuctioneers + others. "
                 "Useful for spot-checking your pricing against the live resale market. "
                 "Click only the items you actually want to comp.",
        ),
        # Shopify (right, normal)
        "Title": st.column_config.TextColumn("Title", width="large",
                                              help="→ Shopify 'Title' column"),
        "Vendor": st.column_config.TextColumn("Vendor", width="small",
                                               help="→ Shopify 'Vendor' column. Defaults to 'Vintage' when no brand detected."),
        "Product Category": st.column_config.TextColumn("Product Category", width="large",
                                                         help="→ Shopify 'Product Category' (full GCP taxonomy path)"),
        "Type": st.column_config.TextColumn("Type", width="small",
                                             help="→ Shopify 'Type' column"),
        "Cost per Item": st.column_config.NumberColumn("Cost per Item", format="$%.2f", width="small",
                                                        help="→ Shopify 'Cost per Item' — landed USD"),
        "Variant Price": st.column_config.NumberColumn("Variant Price", format="$%d", width="small",
                                                        help="→ Shopify 'Variant Price' — rounded"),
        "SKU": st.column_config.TextColumn("SKU", width="small", help="→ Shopify 'SKU'"),
    }

    # QA columns are disabled (read-only). Editable: Title, Vendor, Type, Variant Price.
    editable_cols = {"Title", "Vendor", "Type", "Variant Price"}
    disabled_cols = [c for c in df.columns if c not in editable_cols]

    pricing_key = f"pricing_editor_{inv_data_ref.get('__source_file', 'none')}" if inv_data_ref else "pricing_editor"
    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
        disabled=disabled_cols,
        num_rows="fixed",
        key=pricing_key,
        height=min(700, 50 + len(rows) * 35),
    )

    sc1, sc2 = st.columns([4, 1])
    with sc1:
        if not edited.equals(df):
            st.caption("✎ Unsaved edits — click Save to write back and recompute totals.")
    with sc2:
        if inv_data_ref is not None and st.button("Save edits", key="save_pricing", type="primary", use_container_width=True):
            changes = apply_pricing_edits(inv_data_ref, edited)
            if changes:
                path = persist_invoice(inv_data_ref)
                st.success(f"Saved {changes} item(s) → {path.name}.")
                # Auto-recompute hero totals, per-item margins, and effective
                # markup so the new override_price is reflected immediately.
                st.rerun()
            else:
                st.info("No changes to save.")

    # Expandable: per-item warning detail
    warned = [i for i in filtered if i["pricing"].warnings or not i["pricing"].vendor]
    if warned:
        with st.expander(f"⚠ Warning detail  ({len(warned)} items)", expanded=False):
            for e in warned:
                vendor = canon_brand(e["item"].detected_brand) or "Vintage"
                title = compose_title(e["item"])
                warns = e["pricing"].warnings[:]
                if not e["pricing"].vendor:
                    warns.append("No brand detected — vendor set to 'Vintage'")
                st.markdown(f"**{vendor} · {title}** — {' · '.join(warns)}")


# ---------------------------------------------------------------------------
# Rules & Notes — heuristics rules engine view + feedback capture
# ---------------------------------------------------------------------------

TOPIC_OPTIONS = ["general", "titles", "costs", "aesthetic", "data_quality", "scope"]
STATUS_BADGES = {
    "pending":  ("⧗", "#a47200"),
    "applied":  ("✓", "#2e7d32"),
    "rejected": ("✗", "#9c1c1c"),
    "deferred": ("⏸", "#555"),
}


def render_inline_note_capture(context_hint: str = "") -> None:
    """In-page note capture — works regardless of sidebar state.

    Renders as an expander so it's present but not loud. Opens into a compact
    form that lands a `pending` note in feedback.yaml. Use `context_hint` to
    pre-fill context like the current invoice filename.
    """
    notes = load_feedback()
    pending = sum(1 for n in notes if n.status == "pending")
    pending_tag = f" · {pending} pending" if pending else ""

    with st.expander(
        f"📝  Add a note / capture feedback{pending_tag}",
        expanded=False,
    ):
        st.caption(
            "Drops a `pending` entry into `heuristics/feedback.yaml`. "
            "Address it later in the Rules & Notes tab, or via "
            "`uv run python -m heuristics feedback --status pending`."
        )
        with st.form("inline_note", clear_on_submit=True, border=False):
            default = f"[{context_hint}] " if context_hint else ""
            text = st.text_area(
                "What did you notice?",
                value=default,
                key="inline_note_text",
                placeholder="e.g. Margiela items come back without era — default to 00's",
                height=90,
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                topic = st.selectbox(
                    "Topic", TOPIC_OPTIONS, index=0, key="inline_note_topic",
                    label_visibility="collapsed",
                )
            with c2:
                submitted = st.form_submit_button(
                    "Save note", type="primary", use_container_width=True,
                )
            if submitted:
                if text and text.strip() and text.strip() != default.strip():
                    n = append_feedback(text.strip(), topic=topic)
                    st.success(f"Saved {n.id} — view in the Rules & Notes tab.")
                else:
                    st.warning("Please enter some text first.")


def render_sidebar_notes() -> None:
    """Always-available sidebar: quick-add note + recent feedback list.

    Designed for the moment a user notices something while reviewing an invoice
    and wants to capture it without leaving the current tab.
    """
    with st.sidebar:
        st.markdown("### 📝 Add a note")
        st.caption("Captures feedback as a `pending` entry in feedback.yaml. "
                   "Address it later via the Rules & Notes tab.")
        with st.form("sidebar_note", clear_on_submit=True, border=False):
            text = st.text_area("What did you notice?", key="sidebar_note_text",
                                placeholder="e.g. Margiela items are missing era — default to 00's",
                                height=100, label_visibility="collapsed")
            topic = st.selectbox("Topic", TOPIC_OPTIONS, index=0, key="sidebar_note_topic")
            submitted = st.form_submit_button("Save note", type="primary",
                                              use_container_width=True)
            if submitted:
                if text and text.strip():
                    n = append_feedback(text.strip(), topic=topic)
                    st.success(f"Saved as {n.id}")
                else:
                    st.warning("Please enter some text first.")

        st.markdown("---")
        st.markdown("### Recent notes")
        notes = list(reversed(load_feedback()))[:5]
        if not notes:
            st.caption("No notes yet.")
            return
        for n in notes:
            badge, color = STATUS_BADGES.get(n.status, ("?", "#999"))
            st.markdown(
                f"<div style='font-size:0.85em; margin-bottom:0.5rem;'>"
                f"<span style='color:{color}; font-weight:bold;'>{badge}</span> "
                f"<span style='color:#666;'>{n.date} · {n.topic}</span><br/>"
                f"{n.quote[:120]}{'...' if len(n.quote) > 120 else ''}"
                f"</div>",
                unsafe_allow_html=True,
            )


def _badge_html(status: str) -> str:
    badge, color = STATUS_BADGES.get(status, ("?", "#999"))
    return (f"<span style='background:{color};color:white;padding:2px 8px;"
            f"border-radius:3px;font-size:0.75em;font-weight:bold;'>"
            f"{badge} {status.upper()}</span>")


def render_rules_tab() -> None:
    """Main rules engine view: browse all heuristics + manage feedback notes."""
    rules = load_rules()  # always fresh — file may have been edited mid-session

    st.markdown("### Heuristics rules engine")
    st.caption(
        f"Source of truth: `{RULES_PATH.name}` (edit by hand to change behavior). "
        f"Feedback log: `{FEEDBACK_PATH.name}`. "
        f"Run `uv run python -m heuristics view` for a terminal dump."
    )

    # ----- Stale pending notes — surfaced prominently so they don't rot ------
    try:
        from heuristics import stale_pending_notes as _stale_pending_notes
        stale = _stale_pending_notes(threshold_days=2)
    except Exception:
        stale = []

    if stale:
        with st.container(border=True):
            st.markdown(
                f"##### ⏰ Stale pending notes "
                f"<span style='color:#888; font-weight:400;'>"
                f"({len(stale)} pending more than 2 days)</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                "These came in a while ago and haven't been marked addressed. "
                "Click a status button to dispose of each one."
            )
            for n in stale:
                age = (_date.today() - n.date).days
                age_str = f"{age}d ago" if age else "today"
                snippet = n.quote.strip().split("\n")[0]
                if len(snippet) > 110:
                    snippet = snippet[:107] + "..."
                cols = st.columns([5, 1, 1, 1])
                with cols[0]:
                    st.markdown(
                        f"**{n.id}**  ·  *{n.topic}*  ·  {age_str}\n\n"
                        f"<span style='color:#444'>{snippet}</span>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    if st.button("✓ Applied", key=f"stale_apply_{n.id}", use_container_width=True):
                        update_feedback_status(n.id, "applied")
                        st.rerun()
                with cols[2]:
                    if st.button("⏸ Defer", key=f"stale_defer_{n.id}", use_container_width=True):
                        update_feedback_status(n.id, "deferred")
                        st.rerun()
                with cols[3]:
                    if st.button("✗ Reject", key=f"stale_reject_{n.id}", use_container_width=True):
                        update_feedback_status(n.id, "rejected")
                        st.rerun()
            st.markdown("")  # spacer

    # ----- Notes log first (highest-velocity workflow) ----------------------
    st.markdown("#### Notes & feedback")
    notes = list(reversed(load_feedback()))
    f1, f2, f3 = st.columns([2, 2, 1])
    with f1:
        status_filter = st.selectbox(
            "Status",
            ["(all)", "pending", "applied", "rejected", "deferred"],
            key="notes_status_filter",
        )
    with f2:
        topic_filter = st.selectbox(
            "Topic", ["(all)"] + TOPIC_OPTIONS, key="notes_topic_filter",
        )
    with f3:
        st.metric("Total", len(notes))

    filtered = notes
    if status_filter != "(all)":
        filtered = [n for n in filtered if n.status == status_filter]
    if topic_filter != "(all)":
        filtered = [n for n in filtered if n.topic == topic_filter]

    if not filtered:
        st.info("No matching notes.")
    else:
        for n in filtered:
            with st.expander(
                f"{STATUS_BADGES.get(n.status, ('?', ''))[0]}  "
                f"{n.id}  ·  {n.topic}  ·  {n.date}  —  {n.quote[:80]}"
                f"{'...' if len(n.quote) > 80 else ''}",
                expanded=False,
            ):
                st.markdown(_badge_html(n.status), unsafe_allow_html=True)
                st.markdown(f"**Quote**\n\n> {n.quote}")
                if n.resolution:
                    st.markdown(f"**Resolution**\n\n{n.resolution}")
                if n.related_rules:
                    st.markdown(f"**Related rules:** `{'`, `'.join(n.related_rules)}`")

                c1, c2 = st.columns([3, 2])
                with c1:
                    new_resolution = st.text_area(
                        "Add or update resolution",
                        value=n.resolution or "",
                        key=f"res_{n.id}",
                        height=80,
                    )
                with c2:
                    new_status = st.selectbox(
                        "Set status",
                        ["pending", "applied", "rejected", "deferred"],
                        index=["pending", "applied", "rejected", "deferred"].index(n.status),
                        key=f"st_{n.id}",
                    )
                    if st.button("Update", key=f"upd_{n.id}", use_container_width=True):
                        ok = update_feedback_status(
                            n.id, new_status,
                            resolution=new_resolution if new_resolution.strip() else None,
                        )
                        if ok:
                            st.success("Updated. Reload to see in list.")
                        else:
                            st.error("Update failed.")

    st.markdown("---")
    st.markdown("#### Rules")

    # ----- Each section as a collapsible expander ----------------------------
    with st.expander(f"📐 Title rules", expanded=False):
        st.markdown(f"**Format:** `{rules.meta.get('title_format', '(unset)')}`")
        st.markdown(
            f"**Era policy:** Era only appears in titles when matching "
            f"`{rules.titles.era_policy.allow_in_title_regex}`. "
            f"Decade-allowed categories: "
            f"`{rules.titles.era_policy.allow_decades_for or '(none)'}`"
        )
        st.markdown(
            f"**Silhouettes that move to END of style chain:** "
            f"`{', '.join(rules.titles.silhouette_categorical)}`"
        )
        st.markdown(f"**Acronyms preserved as ALL-CAPS** ({len(rules.titles.acronyms_uppercase)}):")
        st.code(", ".join(rules.titles.acronyms_uppercase), language="text")

    with st.expander(
        f"📅 Model → era database  ·  "
        f"{sum(len(v) for v in rules.model_era.values())} entries  ·  WIRED",
        expanded=False,
    ):
        st.caption("Edits to this section in rules.yaml take effect immediately.")
        rows = [
            {"Brand": brand, "Model": model, "Era": era}
            for brand, models in rules.model_era.items()
            for model, era in models.items()
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with st.expander(
        f"🏷️ Brand archetypes  ·  "
        f"{sum(len(v) for v in rules.brand_archetypes.values())} pairs  ·  MIRROR",
        expanded=False,
    ):
        st.caption("Mirror only — also edit `extractors.BRAND_ARCHETYPES` until migrated.")
        rows = [
            {"Brand": brand, "Type": ptype, **defaults}
            for brand, types in rules.brand_archetypes.items()
            for ptype, defaults in types.items()
        ]
        st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, use_container_width=True)

    with st.expander(
        f"💎 Brand tiers  ·  "
        f"luxury={len(rules.tier_brands.get('luxury', []))}, "
        f"mid={len(rules.tier_brands.get('mid', []))}  ·  MIRROR",
        expanded=False,
    ):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Luxury**")
            st.code("\n".join(rules.tier_brands.get("luxury", [])), language="text")
        with c2:
            st.markdown("**Mid-tier**")
            st.code("\n".join(rules.tier_brands.get("mid", [])), language="text")

    with st.expander(
        f"🔤 Canonicalization  ·  "
        f"{len(rules.canonicalize.get('brands', {}))} brand aliases, "
        f"{len(rules.canonicalize.get('types', {}))} type aliases  ·  MIRROR",
        expanded=False,
    ):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Brand aliases**")
            df = pd.DataFrame(
                [{"Input": k, "Canonical": v} for k, v in rules.canonicalize.get("brands", {}).items()]
            )
            st.dataframe(df, hide_index=True, use_container_width=True, height=300)
        with c2:
            st.markdown("**Type aliases**")
            df = pd.DataFrame(
                [{"Input": k, "Canonical": v} for k, v in rules.canonicalize.get("types", {}).items()]
            )
            st.dataframe(df, hide_index=True, use_container_width=True, height=300)

    with st.expander(
        f"🎯 Regression anchors  ·  {len(rules.regression_anchors)} confirmed titles",
        expanded=False,
    ):
        st.caption("Snapshot tests should verify these stay correct after rule changes.")
        rows = [
            {"source_id": a.source_id, "expected_title": a.expected_title or "(empty)",
             "note": a.note or ""}
            for a in rules.regression_anchors
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.markdown("---")
    st.caption(
        f"To edit rules: open `{RULES_PATH}` in your editor. Comments are preserved on hand-edit. "
        f"Reload this page after saving to see changes."
    )


# ---------------------------------------------------------------------------
# Pricing tab — read-only view of pricing_brackets + pricing_floors + a small
# cost-to-price calculator so the user can probe the table behavior without
# having to load a real invoice.
# ---------------------------------------------------------------------------

def render_pricing_tab() -> None:
    """Browse the YAML-driven pricing tables.

    Read-only by design: edits go through `heuristics/rules.yaml` so YAML
    comments stay intact (a naive `yaml.dump` would destroy them). After
    saving a YAML edit, restart Streamlit to pick up the new values.

    Sections:
      1. Pricing formula (pipeline diagram)
      2. Pricing floors (one row per invoice_type)
      3. Pricing brackets (one table per tier × category, grouped by
         invoice_type)
      4. Cost → price calculator (probe a hypothetical landed cost)
      5. Edit instructions (YAML path + restart note)
    """
    rules = load_rules()  # always fresh — pick up mid-session YAML edits

    st.markdown("### Pricing formula")
    st.caption(
        "Cost-function pricing. Market comps don't enter the formula — you "
        "spot-check those manually via the `🔍 Gem` link per item in the "
        "Pricing & QA table."
    )

    with st.container(border=True):
        st.markdown(
            "```\n"
            "landed_cost = item_price × FX × (1 + handling) × (1 + import)\n"
            "multiplier  = bracket_lookup(invoice_type, tier, category, landed_cost)\n"
            "base_price  = landed_cost × multiplier   (Buyee: × 1.2 first per §10)\n"
            "            × market_adjustment[brand, type]\n"
            "            × demand_multiplier\n"
            "  ↳ enforce min_dollar_profit:  price ≥ landed_cost + floor\n"
            "  ↳ enforce max_markup_multiple: price ≤ landed_cost × ceiling\n"
            "round_price → snap UP to next 25 / 45 / 75 / 95 per $100\n"
            "```"
        )
        st.caption(
            "Override an item's price in the Pricing & QA table to skip the "
            "whole pipeline for that one item."
        )

    # ----- Section 2: Pricing floors --------------------------------------
    st.markdown("### Pricing floors  ·  cost-relative profit guards")
    floor_rows = []
    for invoice_type, floor in rules.pricing_floors.items():
        floor_rows.append({
            "Invoice type": invoice_type,
            "Min $ profit/item": (
                f"${floor.min_dollar_profit:,.0f}"
                if floor.min_dollar_profit else "—"
            ),
            "Max markup ×": (
                f"{floor.max_markup_multiple:.1f}×"
                if floor.max_markup_multiple else "—"
            ),
        })
    if floor_rows:
        st.dataframe(
            pd.DataFrame(floor_rows),
            hide_index=True, use_container_width=True,
        )
        st.caption(
            "Applied AFTER the bracket multiplier + market_adjustment + "
            "demand, BEFORE rounding. Triggered items get a warning in the "
            "Pricing & QA table's ⚠ column."
        )
    else:
        st.info("No `pricing_floors:` section in rules.yaml — guards disabled.")

    # ----- Section 3: Pricing brackets (the main table) -------------------
    st.markdown("### Pricing brackets  ·  cost → multiplier lookup")
    st.caption(
        "First bracket where `landed_cost ≤ max_cost` wins. "
        "`multiplier = 1 / (1 − target_margin)`."
    )

    for invoice_type in ("vendor_invoice", "buyee"):
        categories = rules.pricing_brackets.get(invoice_type, {})
        if not categories:
            continue
        is_default_open = (invoice_type == "vendor_invoice")
        with st.expander(
            f"📦 `{invoice_type}`  ·  {len(categories)} bracket tables",
            expanded=is_default_open,
        ):
            # Group by tier (luxury / mid / standard) for visual grouping
            by_tier: dict[str, list[tuple[str, list]]] = {}
            for cat_key, brackets in categories.items():
                tier = cat_key.split("_", 1)[0]
                by_tier.setdefault(tier, []).append((cat_key, brackets))

            tier_order = ["luxury", "mid", "standard"]
            for tier in tier_order:
                if tier not in by_tier:
                    continue
                st.markdown(f"##### {tier.title()} tier")
                tier_tables = by_tier[tier]
                # Render category tables side-by-side in columns of 3
                cols = st.columns(min(3, len(tier_tables)))
                for idx, (cat_key, brackets) in enumerate(tier_tables):
                    with cols[idx % len(cols)]:
                        category = cat_key.split("_", 1)[1] if "_" in cat_key else cat_key
                        st.markdown(f"**`{category}`**")
                        rows = []
                        for b in brackets:
                            max_label = (
                                f"${int(b.max_cost):,}"
                                if b.max_cost < 99999 else "∞"
                            )
                            rows.append({
                                "max_cost": max_label,
                                "margin": f"{int(b.target_margin*100)}%",
                                "mult": f"{b.multiplier:.2f}×",
                            })
                        st.dataframe(
                            pd.DataFrame(rows),
                            hide_index=True, use_container_width=True,
                        )

    # ----- Section 4: Cost → price calculator -----------------------------
    st.markdown("### Try a cost  ·  see what the formula would output")
    st.caption(
        "Probe the pricing tables without loading an invoice. Useful when "
        "tuning bracket values — pick a known item's landed cost and tier, "
        "see if the output matches what you'd hand-price."
    )

    calc_c1, calc_c2, calc_c3, calc_c4 = st.columns(4)
    with calc_c1:
        test_cost = st.number_input(
            "Landed cost (USD)",
            min_value=1.0, max_value=10000.0,
            value=float(st.session_state.get("pricing_calc_cost", 200.0)),
            step=10.0, key="pricing_calc_cost",
        )
    with calc_c2:
        test_invoice = st.selectbox(
            "Invoice type",
            ["vendor_invoice", "buyee"],
            key="pricing_calc_invoice",
        )
    with calc_c3:
        test_tier = st.selectbox(
            "Brand tier",
            ["luxury", "mid", "standard"],
            key="pricing_calc_tier",
        )
    with calc_c4:
        test_cat = st.selectbox(
            "Category",
            ["apparel", "bags", "accessories"],
            key="pricing_calc_category",
        )

    test_key = f"{test_tier}_{test_cat}"
    bracket = rules.lookup_pricing_bracket(test_invoice, test_key, test_cost)

    if bracket:
        # Mirror the pipeline that price_item() runs, sans market_adjustment
        # (which depends on the actual brand) and demand (which is per-invoice)
        from pricing import round_price as _round_price
        base = test_cost * bracket.multiplier
        floors = rules.pricing_floors.get(test_invoice)
        note = ""
        final_pre_round = base
        if floors:
            if (floors.min_dollar_profit
                    and (base - test_cost) < floors.min_dollar_profit):
                final_pre_round = test_cost + floors.min_dollar_profit
                note = (f" · ↑ bumped by min_profit floor "
                        f"(${floors.min_dollar_profit:.0f})")
            elif (floors.max_markup_multiple
                  and base > test_cost * floors.max_markup_multiple):
                final_pre_round = test_cost * floors.max_markup_multiple
                note = (f" · ↓ capped at {floors.max_markup_multiple:.1f}× "
                        f"cost ceiling")
        rounded = _round_price(final_pre_round)
        margin = (rounded - test_cost) / rounded if rounded > 0 else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Output price", f"${rounded:,}")
        c2.metric("Profit", f"${rounded - test_cost:,.0f}")
        c3.metric("Margin", f"{margin*100:.1f}%")
        st.caption(
            f"Bracket `{test_key}` · max_cost=${int(bracket.max_cost):,} · "
            f"target_margin={int(bracket.target_margin*100)}% · "
            f"multiplier={bracket.multiplier:.2f}×{note}"
        )
        st.caption(
            "Real-world price will also include market_adjustment (per-brand) "
            "and demand multiplier — those are applied on top of the result above."
        )
    else:
        st.warning(
            f"No bracket defined for ({test_invoice}, {test_key}). "
            f"`price_item()` would fall back to the legacy lerp curve "
            f"(or buyee_markup for buyee invoices)."
        )

    # ----- Section 5: Edit instructions -----------------------------------
    st.markdown("---")
    st.markdown("### Tune the tables")
    st.markdown(
        f"1. Open `{RULES_PATH}` in your editor (e.g. `code {RULES_PATH}`)\n"
        f"2. Find the `pricing_brackets:` or `pricing_floors:` section\n"
        f"3. Edit values, save\n"
        f"4. **Restart Streamlit** for changes to take effect "
        f"(`R` in the terminal running it)\n"
    )
    st.caption(
        "The YAML is loaded once at Python module import, so a Streamlit "
        "page reload alone won't pick up edits — you need a full restart. "
        "Comments in the YAML are preserved when hand-edited."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

render_sidebar_notes()

render_header()

# Top-level tabs: keep the homepage focused on invoice work. Knowledge tools
# (rules, notes), Shopify catalogue tools, and pricing-table inspection all
# get their own tabs so they're accessible without picking an invoice first.
home_tab, catalogue_tab, pricing_tab, knowledge_tab = st.tabs([
    "📦 Invoices",
    "🛍️ Shopify catalogue",
    "💰 Pricing",
    "📐 Heuristics, Rules & Notes",
])

with catalogue_tab:
    render_shopify_catalogue_tab()

with pricing_tab:
    render_pricing_tab()

with knowledge_tab:
    render_inline_note_capture()
    render_rules_tab()

with home_tab:
    uploaded, picked = render_source_picker()

    invoice_data = None
    source_label = ""

    if uploaded is not None:
        with st.spinner(f"Transcribing {uploaded.name} — usually under 90 seconds…"):
            invoice_data = cached_transcribe(uploaded.getvalue(), uploaded.name)
            invoice_data["__source_file"] = Path(uploaded.name).stem + ".json"
        source_label = uploaded.name
    elif picked:
        p = OUTPUT / picked
        invoice_data = json.loads(p.read_text(encoding="utf-8"))
        invoice_data["__source_file"] = p.name
        source_label = picked
        # Make path available to the Buyee panel's "Fetch photos" button
        st.session_state["__current_invoice_path"] = str(p)

    if invoice_data:
        render_meta(invoice_data)

        # FX rate is global — affects both cost and pricing. Currency-aware:
        # the control's label + bounds + default come from the invoice itself,
        # so an EUR invoice gets an EUR-shaped slider seeded from ~1.08.
        currency = invoice_data.get("currency", "JPY")
        saved_rate = invoice_data.get("exchange_rate")
        if saved_rate is None or saved_rate == 0:
            # Fall back to a sensible per-currency default
            _approx = {"EUR": 1.08, "GBP": 1.27, "JPY": DEFAULT_EXCHANGE_RATE,
                       "USD": 1.0, "CHF": 1.10, "AUD": 0.65, "CAD": 0.74}
            saved_rate = _approx.get(currency.upper(), DEFAULT_EXCHANGE_RATE)
        rate = render_fx_control(saved_rate, currency=currency)

        # Editable rates — handling/import for vendor invoices, plus the always-
        # available extras (extra %, extra flat). Bot-set values flow in via
        # invoice's _bot_handling_rate / _bot_import_tax_rate / _bot_extra_rate /
        # _bot_extra_flat fields and seed the session_state defaults.
        inv_type = invoice_data.get("invoice_type", "")
        for k in ("handling_rate", "import_tax_rate", "extra_rate", "extra_flat"):
            bot_key = f"_bot_{k}"
            if bot_key in invoice_data:
                st.session_state[k] = invoice_data[bot_key]

        if inv_type == "vendor_invoice":
            handling_rate, import_tax_rate, extra_rate, extra_flat = (
                render_assumed_rates_controls(invoice_currency=currency)
            )
        else:
            from costs import HANDLING_RATE as handling_rate, IMPORT_TAX_RATE as import_tax_rate
            # Buyee invoices: still allow extras (e.g. extra shipping fee), but
            # skip handling/import since those don't apply to Buyee.
            ext_c1, ext_c2 = st.columns(2)
            with ext_c1:
                extra_rate = st.number_input(
                    "Extra %",
                    min_value=0.0, max_value=1.0,
                    value=float(st.session_state.get("extra_rate", 0.0)),
                    step=0.01, format="%.2f",
                    help="Ad-hoc per-item % on top of subtotal. Default 0%.",
                    key="extra_rate",
                )
            with ext_c2:
                flat_step = 100.0 if currency.upper() == "JPY" else 1.0
                flat_fmt = "%.0f" if currency.upper() == "JPY" else "%.2f"
                extra_flat = st.number_input(
                    f"Extra flat ({currency})",
                    min_value=0.0,
                    value=float(st.session_state.get("extra_flat", 0.0)),
                    step=flat_step, format=flat_fmt,
                    help=f"Lump-sum extra cost in {currency}, split evenly across items.",
                    key="extra_flat",
                )

        # Build the view once; reuse across tabs
        inv_for_view = Invoice(**{k: v for k, v in invoice_data.items() if not k.startswith("_")})
        view = InvoiceView(
            inv_for_view, exchange_rate=rate,
            handling_rate=handling_rate, import_tax_rate=import_tax_rate,
            extra_rate=extra_rate, extra_flat=extra_flat,
        )
        recon = view.reconciliation()

        tab_cost, tab_price, tab_export = st.tabs([
            "1 · Cost review",
            "2 · Pricing & QA",
            "3 · Export",
        ])

        # --- Tab 1: Cost review (no pricing noise) --------------------------------
        with tab_cost:
            render_cost_review(view, invoice_data)
            if recon["reconciled"]:
                st.info(
                    "**Cost looks good.** Switch to **2 · Pricing & QA** to apply markup, "
                    "review per-item pricing, and scan warnings."
                )
            else:
                st.error(
                    "**Do not proceed to pricing** until the Δ is investigated — every downstream "
                    "price depends on these cost inputs being right. Re-transcribe if numbers look wrong."
                )

        # --- Tab 2: Pricing & QA --------------------------------------------------
        with tab_price:
            demand, sort_by, filt = render_pricing_controls()
            # Pass through the user-adjusted handling/import rates so the Pricing
            # tab's landed cost matches what's shown in Cost Review (otherwise
            # the two tabs diverge whenever rates are tweaked).
            _, items = compute_rows(
                invoice_data, rate, demand,
                handling_rate=handling_rate,
                import_tax_rate=import_tax_rate,
                extra_rate=extra_rate,
                extra_flat=extra_flat,
            )
            total_price = sum(i["pricing"].rounded_price for i in items)
            render_hero(recon, total_price, items, currency)
            render_alerts(items, view)
            # Pass invoice_stem so the table can look up cached photo thumbnails
            # at output/photos/<stem>/<source_id>.jpg
            invoice_stem = Path(invoice_data.get("__source_file", "")).stem
            render_items_table(items, sort_by, filt, currency,
                               invoice_data.get("invoice_date"),
                               inv_data_ref=invoice_data,
                               invoice_stem=invoice_stem)

        # --- Tab 3: Export --------------------------------------------------------
        with tab_export:
            # Recompute with current demand — use session state so demand persists across tabs
            demand_final = st.session_state.get("demand_for_export", 1.0)
            _, items_final = compute_rows(
                invoice_data, rate, demand_final,
                handling_rate=handling_rate,
                import_tax_rate=import_tax_rate,
                extra_rate=extra_rate,
                extra_flat=extra_flat,
            )
            total_price_final = sum(i["pricing"].rounded_price for i in items_final)

            st.markdown("### Ready to upload")
            st.markdown(
                f"**{source_label}** · {len(items_final)} items · "
                f"Cost basis **${recon['landed_usd_sum']:,.0f}** · "
                f"Expected revenue **${total_price_final:,.0f}** · "
                f"Margin **${total_price_final - recon['landed_usd_sum']:,.0f}** · "
                f"FX rate **{rate}** · Demand **{demand_final}×**"
            )
            st.caption(
                "The CSV mirrors the Shopify inventory template: Title, Vendor, Product Category, "
                "Cost per Item (landed USD), Variant Price, SKU, Tags. Draft status — flip to active on Shopify after upload."
            )

            # ------------------------------------------------------------------
            # Shopify inventory pre-flight: warn about SKU/handle collisions
            # against the live store before downloading the CSV.
            # ------------------------------------------------------------------
            render_shopify_inventory_panel()

            csv_bytes, collision_log = build_shopify_csv(
                invoice_data, rate, demand_final,
                handling_rate=handling_rate, import_tax_rate=import_tax_rate,
                extra_rate=extra_rate, extra_flat=extra_flat,
                return_collisions=True,
            )

            if collision_log:
                sku_renames = [c for c in collision_log if c["kind"] == "sku"]
                handle_renames = [c for c in collision_log if c["kind"] == "handle"]
                with st.expander(
                    f"⚠ {len(collision_log)} collision(s) auto-resolved "
                    f"({len(sku_renames)} SKU, {len(handle_renames)} handle)",
                    expanded=True,
                ):
                    st.caption(
                        "These items had a generated SKU or handle that conflicted with "
                        "your existing Shopify inventory or another item in this export. "
                        "We bumped them with `-2`, `-3`, etc. so the import won't merge "
                        "or fail."
                    )
                    rename_rows = [
                        {
                            "kind": c["kind"].upper(),
                            "source_id": c["source_id"],
                            "proposed": c["proposed"],
                            "renamed_to": c["renamed_to"],
                            "reason": c["reason"],
                        }
                        for c in collision_log
                    ]
                    st.dataframe(pd.DataFrame(rename_rows), hide_index=True, use_container_width=True)

            stem = Path(source_label).stem or "shopify"
            date = invoice_data.get("invoice_date") or datetime.now().strftime("%Y-%m-%d")
            csv_name = f"shopify_{date}_{stem}.csv"
            st.download_button(
                "Download Shopify CSV",
                data=csv_bytes,
                file_name=csv_name,
                mime="text/csv",
            )

            # ------------------------------------------------------------------
            # Direct push: create draft products in Shopify via the API.
            # Replaces the CSV upload step entirely. Each item becomes a draft
            # product with its first cached photo attached.
            # ------------------------------------------------------------------
            st.markdown("---")
            head_c1, head_c2 = st.columns([3, 1])
            with head_c1:
                st.markdown("### 🚀 Push directly to Shopify")
            with head_c2:
                # Quick refresh of the live Shopify inventory cache. Useful after
                # a push (new products won't appear in collision checks otherwise)
                # or any time you've manually edited products in Shopify admin.
                try:
                    from shopify_inventory import (
                        fetch_inventory_live as _fetch_live,
                        save_inventory_cache as _save_cache,
                        load_cached_inventory as _load_cache,
                        is_configured as _shopify_configured_ok,
                    )
                    cached_now = _load_cache()
                    age_label = cached_now.humanize_age() if cached_now and cached_now.is_loaded else "never"
                    if _shopify_configured_ok():
                        if st.button(
                            f"🔄 Refresh Shopify\n({age_label})",
                            key="refresh_shopify_inventory_top",
                            use_container_width=True,
                            help="Re-fetch product catalog from Shopify. Run this after a push "
                                 "or after manual edits in Shopify admin so the local cache "
                                 "matches the live store.",
                        ):
                            with st.spinner("Fetching from Shopify..."):
                                fresh = _fetch_live()
                            if fresh.error:
                                st.error(f"✗ {fresh.error}")
                            else:
                                _save_cache(fresh)
                                st.success(f"✓ {fresh.product_count} products, "
                                           f"{len(fresh.skus)} SKUs cached")
                                st.rerun()
                except Exception:
                    pass
            try:
                from shopify_inventory import is_configured as _shopify_configured
                shopify_ready = _shopify_configured()
            except Exception:
                shopify_ready = False

            if not shopify_ready:
                st.info(
                    "Configure Shopify API first (see the **🛍️ Shopify inventory check** "
                    "panel above). Direct push needs the same token, plus `write_products` "
                    "scope on your app."
                )
            else:
                # Count items already pushed via the SIDECAR LEDGER (source of truth).
                # We also fall back to the in-memory invoice's shopify_product_id field
                # in case the ledger was deleted.
                from shopify_push import load_push_ledger as _load_ledger
                invoice_path_for_ledger = OUTPUT / (invoice_data.get("__source_file") or "")
                ledger = _load_ledger(invoice_path_for_ledger) if invoice_path_for_ledger.exists() else {}
                n_items = len(invoice_data.get("items", []))
                published_source_ids = set(ledger.keys()) | {
                    it["source_id"] for it in invoice_data.get("items", [])
                    if it.get("shopify_product_id") and it.get("source_id")
                }
                published_count = sum(
                    1 for it in invoice_data.get("items", [])
                    if it.get("source_id") in published_source_ids
                )
                pending_count = n_items - published_count

                st.caption(
                    f"{published_count}/{n_items} items already pushed (per ledger) · "
                    f"{pending_count} pending. Idempotent — pushed items skipped. "
                    f"Products are created as **draft** (hidden until you activate them)."
                )

                c1, c2, c3, c4 = st.columns([1, 1, 1.5, 2])
                with c1:
                    do_dry_run = st.checkbox("Dry run", value=False,
                                              help="Preview what would happen; don't actually create products.")
                with c2:
                    force_push = st.checkbox("Force re-push", value=False,
                                              help="Override the dedup check. Use only when you intentionally want to re-create products. RARELY needed.")
                with c3:
                    push_clicked = st.button(
                        "🚀 Publish to Shopify",
                        type="primary",
                        disabled=(pending_count == 0 and not do_dry_run and not force_push)
                                 or st.session_state.get("_pushing_in_progress", False),
                        use_container_width=True,
                        key="publish_to_shopify_button",
                    )

                # If user re-clicks while publish is mid-flight, ignore. Streamlit
                # disables the button via the disabled= flag above, but session
                # state survives across reruns where the button render hasn't
                # caught up yet.
                if push_clicked and not st.session_state.get("_pushing_in_progress", False):
                    from shopify_push import publish_invoice_to_shopify
                    invoice_path = OUTPUT / (invoice_data.get("__source_file") or "")
                    if not invoice_path.exists():
                        st.error(f"Invoice path missing: {invoice_path}")
                    else:
                        if force_push and not do_dry_run:
                            # Show explicit warning since force = create duplicates risk
                            st.warning(
                                "**Force re-push enabled.** This will create NEW products "
                                "even for items already in the ledger. If you click ahead, "
                                "you'll likely have duplicates in Shopify."
                            )
                        st.session_state["_pushing_in_progress"] = True
                        try:
                            label = "Dry-run preview..." if do_dry_run else "Publishing to Shopify..."
                            with st.spinner(label):
                                result = publish_invoice_to_shopify(
                                    invoice_path, rate=rate, demand=demand_final,
                                    handling_rate=handling_rate, import_tax_rate=import_tax_rate,
                                    extra_rate=extra_rate, extra_flat=extra_flat,
                                    dry_run=do_dry_run, force=force_push,
                                )
                        finally:
                            st.session_state["_pushing_in_progress"] = False

                        if not result.get("ok"):
                            st.error(f"✗ {result.get('error')}")
                        else:
                            if do_dry_run:
                                st.success(
                                    f"Dry run · would create {result.get('items_attempted', 0)} products "
                                    f"(skipping {result.get('items_skipped_existing', 0)} already pushed)"
                                )
                            else:
                                st.success(
                                    f"✓ Published {result['items_published']} new products · "
                                    f"skipped {result['items_skipped_existing']} already-pushed · "
                                    f"{result['items_failed']} failed"
                                )

                            # Show per-item log
                            with st.expander(
                                f"Per-item results ({len(result['log'])})",
                                expanded=result.get("items_failed", 0) > 0,
                            ):
                                log_rows = [
                                    {
                                        "source_id": e.get("source_id"),
                                        "status": e.get("status"),
                                        "product_id": e.get("product_id") or "",
                                        "message": e.get("message", "")[:200],
                                    }
                                    for e in result["log"]
                                ]
                                if log_rows:
                                    st.dataframe(pd.DataFrame(log_rows), hide_index=True,
                                                 use_container_width=True)
                            if not do_dry_run and result["items_published"] > 0:
                                # Auto-refresh the local cache so freshly-pushed
                                # products show up in next collision check.
                                try:
                                    from shopify_inventory import (
                                        fetch_inventory_live, save_inventory_cache,
                                    )
                                    with st.spinner("Refreshing Shopify cache..."):
                                        fresh = fetch_inventory_live()
                                    if not fresh.error:
                                        save_inventory_cache(fresh)
                                except Exception:
                                    pass
                                st.rerun()

            # ------------------------------------------------------------------
            # Duplicate cleanup — find & delete dupes from a previous mishap
            # ------------------------------------------------------------------
            if shopify_ready:
                with st.expander("🧹 Find duplicate products in Shopify", expanded=False):
                    st.caption(
                        "Walk the live Shopify catalog and report products that share "
                        "a handle OR a SKU. Use this if you accidentally double-pushed "
                        "and want to find what to delete in Shopify admin."
                    )
                    dc1, dc2 = st.columns([2, 1])
                    with dc1:
                        sku_filter = st.text_input(
                            "Filter by SKU prefix (optional)",
                            value="", placeholder="e.g. LOU_ or FEN_",
                            help="Limit the scan to products whose SKU starts with this. "
                                 "Leave blank to scan everything.",
                        )
                    with dc2:
                        scan_clicked = st.button("Scan", key="scan_dupes",
                                                  use_container_width=True)
                    if scan_clicked:
                        from shopify_push import find_duplicates
                        with st.spinner("Scanning Shopify catalog (~30s for 3500 products)..."):
                            result = find_duplicates(sku_prefix=sku_filter.strip() or None)
                        if result.get("error"):
                            st.error(f"✗ {result['error']}")
                        else:
                            n_handle_dupes = len(result.get("by_handle") or {})
                            n_sku_dupes = len(result.get("by_sku") or {})
                            st.markdown(
                                f"**Scanned:** {result['fetched']} products "
                                f"(filtered to {result.get('filtered', result['fetched'])}). "
                                f"**Found:** {n_handle_dupes} handle dupes · "
                                f"{n_sku_dupes} SKU dupes."
                            )
                            if n_handle_dupes:
                                st.markdown("##### Handle duplicates")
                                rows = []
                                for handle, prods in result["by_handle"].items():
                                    for p in prods:
                                        rows.append({
                                            "handle": handle,
                                            "product_id": p.get("id"),
                                            "title": p.get("title"),
                                            "created_at": (p.get("created_at") or "")[:19],
                                            "delete_url": f"https://{__import__('shopify_inventory').get_shop()}/admin/products/{p.get('id')}",
                                        })
                                st.dataframe(pd.DataFrame(rows), hide_index=True,
                                             use_container_width=True)
                                st.caption(
                                    "To delete a duplicate: click its `delete_url` to open "
                                    "the product in Shopify admin, then use Shopify's Delete "
                                    "button. (We intentionally don't auto-delete — too easy "
                                    "to wipe the wrong one.)"
                                )
                            if n_sku_dupes:
                                st.markdown("##### SKU duplicates")
                                rows = []
                                for sku, variants in result["by_sku"].items():
                                    for v in variants:
                                        rows.append({
                                            "sku": sku,
                                            "product_id": v["product_id"],
                                            "title": v["product_title"],
                                            "created_at": (v.get("created_at") or "")[:19],
                                        })
                                st.dataframe(pd.DataFrame(rows), hide_index=True,
                                             use_container_width=True)

            st.markdown("---")
            st.markdown("### Pre-upload checklist")
            st.markdown(
                "- [ ] Tab 1 cost reconciliation shows ✓\n"
                "- [ ] Tab 2 warnings reviewed — each one has a deliberate decision\n"
                "- [ ] Ceiling-capped items hand-priced if a specimen deserves premium\n"
                "- [ ] Unbranded items have their Vendor column manually set to 'Vintage' in the CSV\n"
                "- [ ] Demand multiplier matches current market conditions"
            )
