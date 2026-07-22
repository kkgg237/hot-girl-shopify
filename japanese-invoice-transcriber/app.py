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

import commercial_invoice as ci
from costs import DEFAULT_EXCHANGE_RATE, Invoice, InvoiceView
from pricing import (
    canon_brand, canon_type, compose_title, price_item,
    title_backbone_issues, set_learned_titles,
)
from transcribe import transcribe as transcribe_pdf
from heuristics import (
    RULES_PATH,
    FEEDBACK_PATH,
    DESCRIPTION_TEMPLATES_PATH,
    DescriptionTemplate,
    append_feedback,
    audit_description,
    load_description_templates,
    load_feedback,
    load_rules,
    save_description_templates,
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

/* Global heading unifier — Streamlit's default markdown headings ship with
   Source Sans Pro, but the rest of the app (hero metrics, item-card details,
   inline .items-heading h3) is Arial Nova. Without this rule, every `###`
   markdown header in a tab renders in a visibly different typeface from the
   inline-HTML headers next to it. This pulls all Streamlit-rendered h1-h5
   into the Arial Nova family so the audit, pricing, notes, and copy-formats
   tabs all match. */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {
    font-family: 'Arial Nova', Arial, Helvetica, sans-serif;
    font-weight: 500;
    letter-spacing: -0.01em;
}

/* Button text compactor — Streamlit buttons default to ~1rem (16px) which
   wraps to two lines once the label gets past ~12 chars in a narrow column
   (the audit-tab action buttons sit at 20% row width). Shrinking to 0.82rem
   + tighter letter-spacing keeps labels like "Run description audit" and
   "Scan for duplicates" on one line at the standardized 20% width without
   needing per-button label compromises. Padding is trimmed proportionally
   so the button height tracks the new text size cleanly. */
.stButton > button {
    font-size: 0.82rem;
    letter-spacing: -0.005em;
    padding: 0.35rem 0.6rem;
    white-space: nowrap;
}

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

/* Commercial invoice tab — grey out the input boxes so it's obvious where
   each field is. Scoped via the .ci-form marker so the rest of the app keeps
   its plain editorial fields. */
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stTextInputRootElement"],
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stNumberInputContainer"],
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stTextAreaRootElement"],
[data-testid="stTabPanel"]:has(.ci-form) div:has(> [data-testid="stDateInputField"]),
[data-testid="stTabPanel"]:has(.ci-form) .stSelectbox > div > div {
    background: #ececea !important;
    border: 1px solid #c9c9c4 !important;
    border-radius: 0 !important;
}
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stTextInputRootElement"] input,
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stNumberInputField"],
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stDateInputField"],
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stTextAreaRootElement"] textarea {
    background: #ececea !important;
}
[data-testid="stTabPanel"]:has(.ci-form) [data-testid="stNumberInputContainer"] button {
    background: #e0e0dc !important;
}

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
def cached_transcribe(
    file_bytes: bytes, filename: str, skip_indices_tuple: tuple = (),
) -> dict:
    """Ingest a PDF or CSV into the same Invoice dict shape.

    PDF goes through the Claude-vision transcriber (transcribe.transcribe).
    CSV goes through csv_ingest.extract_from_csv — each row becomes a
    LineItem unless its row_index is in `skip_indices_tuple` (the user's
    preview-step exclusions, passed as a frozen tuple so st.cache_data
    can hash it).
    """
    tmp = INPUTS / filename
    tmp.write_bytes(file_bytes)
    ext = Path(filename).suffix.lower()
    if ext == ".csv":
        from csv_ingest import extract_from_csv
        invoice = extract_from_csv(tmp, skip_indices=set(skip_indices_tuple))
        data = invoice.model_dump()
        # CSV cost is treated as already-landed USD — no handling/import
        # uplift. Seed the rate-control widgets (the existing `_bot_*` hint
        # pattern) so the Cost Review tab starts at 0/0 instead of the PDF
        # defaults (10% / 15%). User can still nudge them upward in the UI.
        data["_bot_handling_rate"] = 0.0
        data["_bot_import_tax_rate"] = 0.0
        out = OUTPUT / f"{tmp.stem}.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        return data
    else:
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
    return edited + originals


@st.cache_data(show_spinner=False, ttl=60)
def _invoice_searchable_meta(path_str: str, mtime_ns: int) -> tuple[str, str, str]:
    """Pull (vendor, invoice_date, item_titles_blob) for picker search.

    Cached on (path, mtime_ns) so re-saves invalidate automatically and
    cache hits cost ~nothing on keystroke-triggered reruns. Item-titles
    are smashed into one blob so a search like "burberry trench" matches
    against any item's title even when the vendor is "Past Studies".

    Returns ("", "", "") on any read error — picker still shows the file,
    just won't match content searches.
    """
    try:
        data = json.loads(Path(path_str).read_text(encoding="utf-8"))
        vendor = (data.get("vendor_name") or "").strip()
        date = (data.get("invoice_date") or "").strip()
        items = data.get("items") or []
        titles = " ".join(
            (it.get("override_title")
             or it.get("description_english")
             or it.get("description_original")
             or "") for it in items[:50]  # cap to avoid huge blobs
        )
        return (vendor, date, titles)
    except Exception:
        return ("", "", "")


@st.cache_data(show_spinner=False, ttl=60)
def _invoice_card_meta(path_str: str, mtime_ns: int) -> dict:
    """Row fields for the consolidated invoice table: date, from, location, totals.

    invoice_date is read straight from the transcribed invoice (not file mtime)
    and `location` is derived from the vendor address / Buyee origin. Cached on
    (path, mtime_ns) like _invoice_searchable_meta so re-saves invalidate.
    """
    import invoice_index as ii

    blank = {"vendor": "", "date": "", "location": "", "n_items": 0,
             "total_str": "", "titles": ""}
    try:
        data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    except Exception:
        return blank
    vendor = (data.get("vendor_name") or "").strip()
    date = (data.get("invoice_date") or "").strip()
    number = (str(data.get("invoice_number")) if data.get("invoice_number") else "").strip()
    location = ii.from_location(
        vendor,
        data.get("vendor_address") or "",
        data.get("invoice_type") or "",
        data.get("currency") or "",
    )
    items = data.get("items") or []
    total = data.get("grand_total")
    cur = (data.get("currency") or "").strip()
    total_str = f"{total:,.0f} {cur}".strip() if isinstance(total, (int, float)) else ""
    titles = " ".join(
        (it.get("override_title")
         or it.get("description_english")
         or it.get("description_original")
         or "") for it in items[:50]
    )
    return {"vendor": vendor, "date": date, "number": number, "location": location,
            "n_items": len(items), "total_str": total_str, "titles": titles}


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


def _overlay_edits_from_disk(invoice_data: dict) -> dict:
    """If an `edited_<stem>.json` sibling exists, prefer it over the in-memory dict.

    `cached_transcribe` returns a frozen snapshot of the original LLM/CSV
    output. User mutations (override_price, override_title, etc.) land in
    `edited_<stem>.json` via `persist_invoice`, NOT back into the cache.
    Without this overlay, every `st.rerun()` after a Save reads the cached
    original again — so hero metrics (Expected Revenue, Gross Margin)
    silently roll back to the pre-edit numbers even though the file on disk
    has the new prices. The user sees stale totals despite a "Saved" toast.

    Idempotent: if `__source_file` already points at the edited file, the
    overlay is a no-op (we just re-read the same JSON).
    """
    src = invoice_data.get("__source_file")
    if not src:
        return invoice_data
    edited_file = OUTPUT / edited_path_for(src)
    if not edited_file.exists():
        return invoice_data
    on_disk = json.loads(edited_file.read_text(encoding="utf-8"))
    on_disk["__source_file"] = edited_file.name
    return on_disk


_REPARSE_FIELDS = ("material", "garment_length", "color", "era", "origin",
                   "pattern", "model_name", "model_size", "style_adjectives")


def reparse_descriptions(invoice_data: dict) -> dict:
    """Re-run the description field extractors on a loaded invoice.

    `extractors.fill_missing_fields` normally runs once at transcribe time. This
    re-applies it on demand — mining `description_original`/`description_english`
    for color/model/pattern/material/etc. — so titles for already-transcribed
    invoices get richer without a re-transcribe. Only BLANK fields are filled
    (explicit values and override_title are never touched); persists to the
    edited JSON and returns fill stats.
    """
    from extractors import fill_missing_fields
    inv = Invoice(**{k: v for k, v in invoice_data.items() if not k.startswith("_")})
    stats = fill_missing_fields(inv)
    by_sid = {it.source_id: it for it in inv.items}
    for row in invoice_data.get("items", []):
        it = by_sid.get(row.get("source_id"))
        if it is None:
            continue
        for f in _REPARSE_FIELDS:
            val = getattr(it, f, None)
            if val and not (str(row.get(f) or "").strip()):
                row[f] = val
    persist_invoice(invoice_data)
    return stats


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


@st.cache_data(show_spinner=False, ttl=30)
def _learned_titles(mtime_ns: int) -> dict:
    """Rich, unambiguous computed→override title map from the corrections log.

    Cached on the log's mtime so a fresh correction is picked up after the next
    save without re-parsing on every rerun.
    """
    import title_learning
    return title_learning.build_learned_titles(CORRECTIONS_LOG)


def install_learned_titles() -> int:
    """Load the learned title map from disk and install it into the composer."""
    try:
        m = CORRECTIONS_LOG.stat().st_mtime_ns
    except OSError:
        m = 0
    mapping = _learned_titles(m)
    set_learned_titles(mapping)
    return len(mapping)


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
        # Full field snapshot so future learning can key on a structured
        # signature, not just the (sometimes sparse) computed title.
        "model_name": item.get("model_name"),
        "color": item.get("color"),
        "pattern": item.get("pattern"),
        "material": item.get("material"),
        "era": item.get("era"),
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


def apply_cost_edits(invoice_data: dict, edited_df, input_df=None) -> dict:
    """Diff the Cost-tab DataFrame back onto invoice_data['items'] by source_id.

    Returns a per-field counts dict for the Save toast.

    Critical: edit-detection compares the EDITED DataFrame against the INPUT
    DataFrame (what was rendered into the data_editor in the first place),
    NOT against `compose_title(item)` or the item's current fields. Why:
    `compose_title` respects an existing override_title, so its output can
    coincidentally equal the user's typed value and silently suppress the
    save. The data_editor knows what it gave the user — we trust that
    snapshot. Pass `input_df` to enable this safe path; if omitted, falls
    back to the legacy compose-title comparison (which has the bug, but
    keeps the function callable by any older test harness).
    """
    by_sid_edit = {row["source_id"]: row for _, row in edited_df.iterrows()}
    by_sid_orig = (
        {row["source_id"]: row for _, row in input_df.iterrows()}
        if input_df is not None else {}
    )
    counts = {"title": 0, "brand": 0, "structured": 0, "qty": 0,
              "total": 0, "matched_rows": 0}
    for item in invoice_data["items"]:
        new = by_sid_edit.get(item["source_id"])
        if new is None:
            continue
        counts["matched_rows"] += 1
        orig = by_sid_orig.get(item["source_id"])  # may be None on legacy path
        dirty = False

        # brand (treat "Vintage" as "clear it")
        new_brand = (new.get("brand") or "").strip()
        cur_brand = (item.get("detected_brand") or "").strip()
        if new_brand == "Vintage" and cur_brand:
            item["detected_brand"] = None; dirty = True; counts["brand"] += 1
        elif new_brand and new_brand != "Vintage" and new_brand != cur_brand:
            item["detected_brand"] = new_brand; dirty = True; counts["brand"] += 1

        # Proposed title → override_title. Compare to the INPUT DataFrame's
        # value for this row — if it changed in the data_editor, save it.
        # No compose_title round-trip (which was eating edits whenever
        # compose's output coincided with the user's typed string).
        new_title = (new.get("proposed title") or "").strip()
        orig_title = (
            (orig.get("proposed title") or "").strip() if orig is not None else ""
        )
        title_changed = (new_title != orig_title) if orig is not None else False
        # Legacy fallback (input_df not provided): use compose_title and
        # accept the original ambiguity — better than silent failure.
        if orig is None and new_title:
            title_changed = (new_title != compose_title_safe(item))

        if title_changed and new_title:
            from costs import LineItem as _LI
            from pricing import compose_title as _ct
            tmp = {k: v for k, v in item.items() if k in _LI.model_fields.keys()}
            tmp["override_title"] = None
            baseline = _ct(_LI(**tmp))
            item["override_title"] = new_title
            dirty = True
            counts["title"] += 1
            log_title_correction(item, baseline, new_title,
                                  invoice_data.get("__source_file", "unknown"))
        elif title_changed and not new_title and item.get("override_title"):
            # Cell cleared → drop the override and let compose_title rebuild
            item["override_title"] = None
            dirty = True
            counts["title"] += 1

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
                        item["quantity"] = v; dirty = True; counts["qty"] += 1
                else:
                    changed_field = _str_edit(new[k], item.get(field))
                    if changed_field is not None or (not new[k] and item.get(field)):
                        item[field] = (new[k] or None) if isinstance(new[k], str) else new[k]
                        dirty = True; counts["structured"] += 1
        if dirty:
            counts["total"] += 1
    return counts


def compose_title_safe(item_dict: dict) -> str:
    """compose_title that takes a raw item dict (as stored in invoice_data)
    and safely constructs a LineItem for it. Returns "" on any error."""
    try:
        from costs import LineItem as _LI
        from pricing import compose_title as _ct
        fields = {k: v for k, v in item_dict.items() if k in _LI.model_fields.keys()}
        return _ct(_LI(**fields))
    except Exception:
        return ""


def _coerce_price_int(raw) -> int | None:
    """Parse a Variant Price cell into a positive int, or None if unparseable.

    Streamlit's data_editor (NumberColumn with format="$%d") returns int-like
    values for clean edits, but in practice you also see:
      - pandas NaN for cleared cells (int(nan) raises ValueError)
      - numpy.float64(150.0) — int() works, but isnan check needs care
      - strings like "$150" or "150.00" if Streamlit ever leaks the format
    Earlier the price-edit branch silently swallowed all of these as "no
    change", which is the user-facing "saved nothing" symptom. This helper
    consolidates the coercion so price/cost edits always survive a round-trip.
    """
    if raw is None:
        return None
    try:
        import math
        if isinstance(raw, float) and math.isnan(raw):
            return None
    except Exception:
        pass
    # pandas NaN / numpy NaN: catch via pd.isna without forcing pandas import
    try:
        import pandas as _pd
        if _pd.isna(raw):
            return None
    except Exception:
        pass
    if isinstance(raw, str):
        raw = raw.replace("$", "").replace(",", "").strip()
        if not raw:
            return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def apply_pricing_edits(invoice_data: dict, edited_df) -> dict:
    """Apply edits from the Pricing-tab DataFrame.

    Returns a per-field breakdown so the Save toast can show exactly what
    was detected — e.g. `{"price": 3, "title": 0, "vendor": 1, "type": 0,
    "total": 4}`. Earlier this returned just an int, which hid the case
    where a user thought they were editing prices but the price-detection
    branch silently swallowed the input (NaN, leading $, etc.).
    """
    by_sid = {row["Source ID"]: row for _, row in edited_df.iterrows()}
    counts = {"price": 0, "title": 0, "vendor": 0, "type": 0, "total": 0,
              "matched_rows": 0}
    for item in invoice_data["items"]:
        new = by_sid.get(item["source_id"])
        if new is None:
            continue
        counts["matched_rows"] += 1
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
                    counts["title"] += 1
                    log_title_correction(item, baseline, new_title,
                                          invoice_data.get("__source_file", "unknown"))
            elif item.get("override_title"):
                item["override_title"] = None; dirty = True
                counts["title"] += 1

        # Vendor override
        new_vendor = (new.get("Vendor") or "").strip()
        default_vendor = (item.get("detected_brand") or "").strip()
        if new_vendor == "Vintage":
            if item.get("override_vendor"):
                item["override_vendor"] = None; dirty = True
                counts["vendor"] += 1
        elif new_vendor and new_vendor != default_vendor:
            if new_vendor != (item.get("override_vendor") or ""):
                item["override_vendor"] = new_vendor; dirty = True
                counts["vendor"] += 1
        elif new_vendor == default_vendor and item.get("override_vendor"):
            item["override_vendor"] = None; dirty = True
            counts["vendor"] += 1

        # Product type
        new_type = (new.get("Type") or "").strip()
        if new_type != (item.get("product_type") or "").strip():
            item["product_type"] = new_type or None; dirty = True
            counts["type"] += 1

        # Price override (uses _coerce_price_int to survive NaN / "$" / floats)
        np_int = _coerce_price_int(new.get("Variant Price"))
        if np_int is not None and np_int != (item.get("override_price") or 0):
            item["override_price"] = np_int; dirty = True
            counts["price"] += 1
        if dirty:
            counts["total"] += 1
    return counts


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

    # Load once per export — both lookups are batched against every item.
    # Catch ImportError so a broken heuristics/taxonomy file doesn't break
    # the legacy CSV export path.
    _templates = _taxonomy = None
    try:
        from heuristics import load_description_templates
        _templates = load_description_templates()
    except Exception:
        pass
    try:
        from shopify_taxonomy import load_taxonomy
        _taxonomy = load_taxonomy()
    except Exception:
        pass

    for item in priced["items"]:
        all_rows.extend(item_to_rows(
            item, priced, priced["__source_file"], used_skus,
            used_handles=used_handles,
            existing_skus=existing_skus,
            existing_handles=existing_handles,
            collision_log=collision_log,
            templates=_templates,
            taxonomy=_taxonomy,
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
    meta = load_meta()
    # The last sync landed on the login screen (session expired) → flag it in
    # the collapsed header so the user sees it without opening the panel.
    session_expired = bool(getattr(meta, "last_sync_login_wall", False))

    badge_parts = []
    if total > 0:
        badge_parts.append(f"{downloaded}/{total} downloaded")
    badge_parts.append(f"last sync {humanize_freshness(last_sync_h)}")
    if session_expired:
        badge_parts.append("⚠️ session expired — re-login")
    if cfg.telegram_configured:
        badge_parts.append("Telegram bot configured")
    badge = " · " + " · ".join(badge_parts) if badge_parts else ""

    # A just-completed sync stashes its stats here and triggers a rerun, so the
    # header badge above recomputes from fresh meta (clears a stale "session
    # expired" the moment a re-login + sync succeed). Peek before the expander
    # so it stays open to show the result.
    pending_result = st.session_state.get("_buyee_sync_result")
    with st.expander(f"Sync invoices from Buyee{badge}",
                     expanded=session_expired or pending_result is not None):
        result = st.session_state.pop("_buyee_sync_result", None)
        if result is not None and not result.get("login_wall"):
            ok = result["errors"] == 0
            (st.success if ok else st.warning)(
                f"Pages: {result['pages_visited']} · "
                f"Seen: {result['seen']} · New: {result['new']} · "
                f"Downloaded: {result['downloaded']} · Errors: {result['errors']}"
            )
            if result["downloaded"]:
                st.caption(
                    f"{result['downloaded']} new invoice(s) → `inputs/buyee/`. "
                    f"They'll appear in the Incoming PDFs panel below."
                )
            if result["seen"] == 0:
                st.warning(
                    "No orders parsed. Selectors in `buyee/scraper.py` may need "
                    "refinement — see `buyee/state/raw_html/shipped_1.html`."
                )

        if session_expired:
            st.error(
                "**Buyee session expired.** The last sync was redirected to the "
                "login screen, so no orders were read. Re-login in a terminal, "
                "then Sync now:\n\n"
                "`uv run --with playwright --with pydantic python -m buyee login`"
            )
        if not SESSION_PATH.exists():
            st.warning(
                "**No Buyee session saved.** Run in a terminal once: "
                "`uv run --with playwright --with pydantic python -m buyee login` "
                "→ log in (incl. 2FA) → return to terminal → press Enter."
            )
            return

        # Action row: sync + session-check + pages-to-scan in one line.
        # Recent-orders list dropped — duplicates the Incoming PDFs panel
        # below. Inline photo-scraper dropped — was a mixed concern (about
        # the currently loaded invoice, not about Buyee sync) and auto-
        # photo-fetch already runs in the transcribe path.
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            sync_clicked = st.button("Sync now", type="primary", width="stretch")
        with c2:
            check_clicked = st.button("Check session", width="stretch")
        with c3:
            max_pages = st.number_input("Max pages", min_value=1, max_value=50,
                                        value=5, label_visibility="collapsed",
                                        help="Stop after N shipped-list pages")

        if check_clicked:
            with st.spinner("Checking Buyee session..."):
                ok, msg = is_session_valid()
            (st.success if ok else st.error)(msg)
            if not ok:
                st.caption("Refresh: `uv run --with playwright --with pydantic python -m buyee login`")

        if sync_clicked:
            with st.spinner(f"Scanning {max_pages} page(s) of shipped baggages…"):
                try:
                    stats = sync_invoices(max_pages=int(max_pages), dry_run=False)
                except FileNotFoundError as e:
                    st.error(f"Session missing: {e}")
                    return
                except Exception as e:
                    st.error(f"Sync failed: {e}")
                    st.caption("If session-related, re-run `python -m buyee login` in your terminal.")
                    return
            # Stash + rerun so the header badge/banner recompute from the meta
            # this sync just wrote (login_wall now reflects reality). The result
            # summary renders at the top of the expander on the next run.
            st.session_state["_buyee_sync_result"] = stats
            st.rerun()

        # Telegram footer — collapses to one line when configured; the long
        # 4-step setup wall only appears for first-time setup (which the user
        # does once, then never sees again).
        st.divider()
        if cfg.telegram_configured:
            st.caption("Configured — send `sync` to your bot to trigger remotely.")
        else:
            with st.expander("Set up Telegram triggering (optional)", expanded=False):
                st.markdown(
                    "Sync from your phone by sending a message:\n"
                    "1. `uv run --with playwright --with pydantic python -m buyee setup` — wizard creates a bot via @BotFather, authorizes your phone\n"
                    "2. `uv run --with playwright --with pydantic python -m buyee listen` — start receiving messages\n"
                    "3. *(optional)* `bash buyee/launchd/install.sh` — keep listener running across reboots"
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

    with st.expander(f"Incoming PDFs{badge}", expanded=False):
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
                                  width="stretch"):
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
                                msg = f"Transcribed → `output/{out.name}`."
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
                                st.error(f"Transcribe failed: {e}")

        if processed:
            # Was a nested expander — Streamlit doesn't officially support
            # nested expanders and the list is redundant anyway since every
            # processed PDF is in the picker dropdown below. Replaced with
            # a single caption + show-on-demand toggle.
            if pending:
                st.caption(
                    f"{len(processed)} also processed — pick from the "
                    f"transcribed dropdown below."
                )
            else:
                st.success(
                    f"All {len(processed)} discovered PDF(s) transcribed. "
                    f"Pick one from the dropdown below to edit, or upload a new file."
                )


def render_source_picker():
    render_buyee_sync_panel()
    render_incoming_panel()
    uploaded = st.file_uploader(
        "Upload invoice PDF or inventory CSV",
        type=["pdf", "csv"],
        help=(
            "PDF: Buyee auction or vendor invoice — extracted via Claude vision. "
            "CSV: Shopify-shaped product list (Title / Vendor / Cost per Item / Qty). "
            "If your CSV costs are already landed, drop Handling/Import rates to 0 "
            "in the Cost controls after upload."
        ),
    )
    picked = _render_invoice_table()
    return uploaded, picked


def _render_invoice_table():
    """Consolidated, sortable table of transcribed invoices — one row per order.

    Collapses the raw/edited/buyee stem variants of each order into a single
    row (see invoice_index.group_invoice_files), shows the invoice date and
    origin pulled from the invoice itself, and loads the edited working copy
    when a row is selected — so corrected copies always surface.
    """
    import invoice_index as ii

    groups = ii.group_invoice_files(list_transcribed())
    if not groups:
        st.caption("No transcribed invoices yet — upload one above or sync from Buyee.")
        return None

    from datetime import date as _date_cls

    rows = []
    for g in groups:
        try:
            mtime_ns = g.load_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        m = _invoice_card_meta(str(g.load_path), mtime_ns)
        status = "✎ edited" if g.has_edits else "○ raw"
        if g.is_pushed:
            status += " · pushed"

        # When the invoice was added to the system — the newest file touch across
        # its variants. Drives the default sort so a freshly-submitted invoice
        # surfaces at the top even when its printed date is old (common when
        # catching up on a backlog of past-dated invoices).
        added_ns = 0
        for v in g.variants:
            try:
                added_ns = max(added_ns, v.stat().st_mtime_ns)
            except OSError:
                pass

        # Date column: the shipped/invoice date printed on the invoice; if the
        # invoice carries no date (e.g. a CSV import), fall back to the date the
        # order was first added to the system (earliest file mtime).
        date_val = m["date"]
        if not date_val:
            mtimes = []
            for v in g.variants:
                try:
                    mtimes.append(v.stat().st_mtime)
                except OSError:
                    pass
            if mtimes:
                date_val = _date_cls.fromtimestamp(min(mtimes)).isoformat()

        # Package #: the invoice/package reference (W-number for Buyee orders).
        package = m["number"] or (g.order_key if g.is_buyee else "—")

        rows.append({
            "group": g,
            "Added": _date_cls.fromtimestamp(added_ns / 1e9).isoformat() if added_ns else "—",
            "Date": date_val,
            "Package #": package,
            "From": m["vendor"] or ("Buyee" if g.is_buyee else "—"),
            "Location": m["location"] or "—",
            "Items": m["n_items"],
            "Total": m["total_str"],
            "Status": status,
            "_added_ns": added_ns,
            "_hay": f"{g.load_path.name} {m['vendor']} {m['date']} {package} "
                    f"{m['location']} {m['titles']}".lower(),
        })

    st.markdown(f"#### Transcribed invoices · {len(rows)}")
    search = st.text_input(
        "Search transcribed invoices",
        placeholder="Search by vendor, date, location, order #, or item…",
        key="picker_search",
        label_visibility="collapsed",
    ).strip().lower()
    if search:
        rows = [r for r in rows if search in r["_hay"]]
        if not rows:
            st.caption(f"No matches for **{search}**.")
            return None

    # Default sort: most-recently-added first, so a freshly-submitted invoice
    # (e.g. just sent via Telegram) always surfaces at the top even if its
    # printed invoice date is old. The printed date stays visible in its column.
    rows.sort(key=lambda r: r["_added_ns"], reverse=True)

    df = pd.DataFrame([{k: r[k] for k in
                        ("Added", "Date", "Package #", "From", "Location", "Items", "Total", "Status")}
                       for r in rows])

    event = st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="invoice_table",
        column_config={
            "Added": st.column_config.TextColumn("Added", width="small",
                                                 help="When this invoice was transcribed/added to the app (drives the default sort)"),
            "Date": st.column_config.TextColumn("Date", width="small",
                                                help="Shipped/invoice date printed on the invoice; date added if the invoice has none"),
            "Package #": st.column_config.TextColumn("Package #", width="small",
                                                     help="Invoice / Buyee package reference number"),
            "From": st.column_config.TextColumn("From", width="medium"),
            "Location": st.column_config.TextColumn("From location", width="small"),
            "Items": st.column_config.NumberColumn("Items", width="small"),
            "Total": st.column_config.TextColumn("Total", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small",
                                                  help="✎ edited working copy · ○ raw only · pushed to Shopify"),
        },
    )

    selection = getattr(event, "selection", None) if event else None
    if isinstance(selection, dict):
        sel = list(selection.get("rows") or [])
    else:
        sel = list(getattr(selection, "rows", []) or [])
    picked = None
    selected_group = None
    if sel and sel[0] < len(rows):
        selected_group = rows[sel[0]]["group"]
        picked = selected_group.load_path.name

    # URL ?source=<filename.json> deep-links to a transcribed invoice even
    # without a table click — match against the load target or any variant.
    if picked is None:
        preselected = st.query_params.get("source")
        if preselected:
            for r in rows:
                g = r["group"]
                if preselected == g.load_path.name or any(v.name == preselected for v in g.variants):
                    picked, selected_group = g.load_path.name, g
                    break

    if selected_group is not None:
        _render_selected_invoice_controls(selected_group)
    else:
        st.caption("Click a row to load an invoice. Sorted by date — click any column header to re-sort.")
    return picked


def _render_selected_invoice_controls(g) -> None:
    """Variants disclosure + two-click delete for the selected invoice row."""
    picked = g.load_path.name
    label = "✎ edited working copy" if g.has_edits else "○ raw transcription"
    st.caption(f"Loading **{picked}**  ·  {label}")

    if len(g.variants) > 1:
        with st.expander(f"{len(g.variants)} files for this order", expanded=False):
            for v in g.variants:
                tag = " ← loading" if v.name == picked else ""
                st.markdown(f"- `{v.name}`{tag}")
            if g.is_pushed:
                st.caption("A `.shopify_pushed.json` snapshot also exists for this order.")

    confirm_key = f"_confirm_delete::{picked}"
    confirmed = st.session_state.get(confirm_key, False)
    if not confirmed:
        if st.button("Delete this transcription", key=f"del_btn::{picked}",
                     help="Removes just the loaded JSON from output/ and clears the "
                          "ingest cache. Other variants of this order are left in place."):
            st.session_state[confirm_key] = True
            st.rerun()
    else:
        dc = st.columns([2, 1])
        with dc[0]:
            if st.button(f"Confirm delete {picked}", key=f"del_confirm::{picked}",
                         type="primary", width="stretch"):
                target = OUTPUT / picked
                try:
                    if target.exists():
                        target.unlink()
                except OSError as e:
                    st.error(f"Couldn't delete: {e}")
                else:
                    try:
                        cached_transcribe.clear()
                    except Exception:
                        pass
                    st.session_state.pop(confirm_key, None)
                    st.session_state.pop("invoice_table", None)  # drop stale row selection
                    st.success(f"Deleted {picked}.")
                    st.rerun()
        with dc[1]:
            if st.button("Cancel", key=f"del_cancel::{picked}", type="tertiary",
                         width="stretch"):
                st.session_state.pop(confirm_key, None)
                st.rerun()


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

    # Photo-fetch trigger — moved here from the Buyee sync panel (which was
    # the wrong place; it's about THIS invoice, not Buyee scraping). Renders
    # ONLY when there are Buyee-eligible items whose photo isn't cached yet,
    # so it's invisible noise on non-Buyee invoices or fully-cached ones.
    # Auto-fetch runs at transcribe-time; this button covers older invoices
    # transcribed before auto-fetch existed, or where the fetch was skipped.
    try:
        from buyee.photo_scraper import (
            fetch_invoice_photos, is_eligible, photo_for as _photo_for,
        )
        items = invoice_data.get("items", []) or []
        source_file = invoice_data.get("__source_file", "")
        if not source_file:
            return
        stem = Path(source_file).stem
        if stem.startswith("edited_"):
            stem = stem[len("edited_"):]
        eligible_ids = [it.get("source_id") for it in items
                        if it.get("source_id") and is_eligible(it.get("source_id"))]
        if not eligible_ids:
            return
        missing = [sid for sid in eligible_ids if not _photo_for(stem, sid)]
        if not missing:
            return
        c1, c2 = st.columns([4, 1])
        with c1:
            st.caption(
                f"{len(missing)} of {len(eligible_ids)} Buyee items "
                f"missing cached photos · ~3-5 sec/item, free."
            )
        with c2:
            if st.button("Fetch photos", key=f"fetch_photos_{stem}",
                         width="stretch", type="secondary"):
                inv_path = OUTPUT / source_file
                with st.spinner(f"Fetching {len(missing)} photo(s)…"):
                    pstats = fetch_invoice_photos(inv_path)
                if pstats["errors"] == 0:
                    st.success(
                        f"{pstats['downloaded']} new, "
                        f"{pstats['skipped_existing']} cached"
                    )
                else:
                    st.warning(
                        f"{pstats['downloaded']} downloaded, {pstats['errors']} errors"
                    )
                st.rerun()
    except Exception:
        pass  # photo trigger is opportunistic — never block the page


def render_hero(recon: dict, total_price: int, items: list, currency: str):
    landed = recon["landed_usd_sum"]
    margin = total_price - landed
    # Gross margin = margin / revenue (NOT margin / cost). The latter is
    # markup — same dollar margin, different denominator, and it makes the
    # hero number look ~20pp better than the per-item "Margin %" column
    # (which correctly divides by price). Keeping them consistent matters:
    # the user spot-checks the hero against the row %s and rightly notices
    # when they don't agree.
    gm_pct = (margin / total_price * 100) if total_price else 0
    markup_pct = (margin / landed * 100) if landed else 0
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
        <div class="hero-sub">{gm_pct:.0f}% of revenue · {markup_pct:.0f}% markup · avg {avg_markup:.2f}×{f' · {overrides} override(s)' if overrides else ''}</div>
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

    # Replay learned title corrections into compose_title for this render.
    install_learned_titles()

    # 1. Reconciliation banner at top — go/no-go
    render_reconciliation(recon, ccy, view.intl_fallback_applied)

    # Two ways to fatten thin titles without a re-transcribe:
    #  • Re-parse — free/instant regex pass over the descriptions (local).
    #  • Enrich — the web-search + photo-vision pipeline (buyee.research) for
    #    items whose title is still thin; parses JA/EN description, searches
    #    similar listings, and reads the photo with vision. Costs API per item
    #    (cached after). Both only fill BLANK fields — your edits are untouched.
    flagged_ids = [it.source_id for it in inv.items if title_backbone_issues(it)]
    rp1, rp2, rp3 = st.columns([1, 1, 2])
    with rp1:
        reparse_clicked = st.button(
            "Re-parse descriptions", key="reparse_desc", width="stretch",
            help="Free/instant: mine each item's description text to fill blank "
                 "color / model / pattern / material / era. Only fills empties.",
        )
    with rp2:
        enrich_clicked = st.button(
            f"Enrich {len(flagged_ids)} (web + photo)" if flagged_ids else "Enrich (web + photo)",
            key="enrich_titles", width="stretch", disabled=not flagged_ids,
            help="For thin-title items only: parse the JA/EN description, web-search "
                 "similar listings, and read the item photo with vision to fill "
                 "color / model / material. Costs an Anthropic API call per item "
                 "(cached after). Fills blanks only.",
        )

    if reparse_clicked:
        stats = reparse_descriptions(inv_data_ref)
        filled = {k.replace("_filled", ""): v for k, v in stats.items()
                  if k.endswith("_filled") and v}
        with rp3:
            if filled:
                st.success("Filled from text: " + ", ".join(f"{v} {k}" for k, v in filled.items()) + ".")
            else:
                st.info("Nothing in the text to fill — try Enrich (web + photo).")
        if filled:
            st.rerun()

    if enrich_clicked and flagged_ids:
        try:
            with st.spinner(f"Researching {len(flagged_ids)} item(s) — description + web search + photo vision…"):
                path = persist_invoice(inv_data_ref)
                from buyee.research import enrich_invoice
                stats = enrich_invoice(path, only_ids=flagged_ids, max_cost_usd=3.0)
                fresh = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            with rp3:
                st.error(f"Enrich failed: {e}")
        else:
            preserved = {k: v for k, v in inv_data_ref.items() if k.startswith("__")}
            inv_data_ref.clear()
            inv_data_ref.update(fresh)
            inv_data_ref.update(preserved)
            with rp3:
                st.success(
                    f"Enriched {stats['items_enriched']} of {stats['items_processed']} "
                    f"item(s) · ${stats['total_cost_usd']:.2f}"
                )
            st.rerun()

    # 2. Shared inputs card — the invoice-wide numbers split across all items
    #    For BrandStreet, shows the handling + import assumptions (not on the invoice)
    #    For Buyee, shows the intl shipping + customs splits
    if getattr(view, "assumed_uplifts_suppressed", False):
        st.info(
            "Kanagawa commission detected. Handling % and import tax % are suppressed so the invoice is not double-charged."
        )
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
                f"${view.extra_flat:,.2f}",
                f"split equally: ${ef_per_item:,.2f} per item",
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

    rows = []
    for idx, item in enumerate(inv.items, 1):
        b = view.breakdown(item)
        row = {
            "#": idx,
            "source_id": item.source_id,
            "brand": canon_brand(item.detected_brand) or "Vintage",
            "proposed title": compose_title(item),
            "title check": ("⚠ add " + ", ".join(_iss)) if (_iss := title_backbone_issues(item)) else "✓",
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
            f"coupon ({ccy})": b["coupon"],
            f"subtotal ({ccy})": b["subtotal"],
        }
        if is_buyee:
            row.update({
                f"commission ({ccy})": b["commission"],
                f"dom ship ({ccy})": b["domestic_shipping"],
                f"service ({ccy})": b["service"],
                f"intl share ({ccy})": b["intl_share"],
                f"customs share ({ccy})": b["customs_share"],
            })
        else:
            row[f"handling {view.handling_rate*100:.0f}% ({ccy})"] = b["handling_amount"]
            row[f"import {view.import_tax_rate*100:.0f}% ({ccy})"] = b["import_amount"]
            # Commission line — explicit lump-sum commission split per-item.
            # Show even when 0 if the field is configured to keep columns
            # consistent across rows.
            if inv.commission_line:
                rate_label = (
                    f"commission {inv.commission_line_rate*100:.0f}%"
                    if inv.commission_line_rate else "commission (lump)"
                )
                row[f"{rate_label} ({ccy})"] = b["commission_line_share"]
            # Generic other_fees fallback — only when other_fees > 0
            if b.get("other_share"):
                row[f"other share ({ccy})"] = b["other_share"]
        # Ad-hoc extras — show whenever set (applies to both Buyee + vendor invoices)
        if view.extra_rate:
            label = f"extra {view.extra_rate*100:.1f}%"
            row[f"{label} ({ccy})"] = b.get("extra_pct_amount", 0)
        if view.extra_flat:
            row["extra flat (USD)"] = b.get("extra_flat_usd_per_item", 0)
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
        "title check": st.column_config.TextColumn("title check", width="small",
                                                    help="SEO backbone check (read-only). ✓ = has brand + type + color (+ model for bags). "
                                                         "⚠ add … lists the missing fields — fill them in the row and the title fills itself."),
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
        if col.endswith("(USD)") or col.startswith("unit cost"):
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
        width="stretch",
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
        if st.button("Save edits", key="save_cost", type="primary", width="stretch"):
            # Pass the INPUT df too so edit-detection compares edited-vs-input
            # rather than edited-vs-compose_title(item). The compose path was
            # silently dropping edits whenever compose's output coincided
            # with the user's typed string.
            counts = apply_cost_edits(inv_data_ref, edited, input_df=df)
            if counts["total"]:
                path = persist_invoice(inv_data_ref)
                bits = [
                    f"{counts[k]} {k}" for k in ("title", "brand", "structured", "qty")
                    if counts[k]
                ]
                detail = ", ".join(bits) or "no field changes detected"
                st.success(f"Saved {counts['total']} item(s) ({detail}) → {path.name}.")
                st.rerun()
            elif counts["matched_rows"] == 0:
                st.error(
                    "Couldn't match any rows back to invoice items by source_id. "
                    "Refresh the page; if it persists, the source_id column may "
                    "have been renamed upstream."
                )
            else:
                # DEBUG dump — if apply_cost_edits matched all rows but
                # found zero changes, surface a side-by-side of the input
                # df vs the edited df for the title column so we can SEE
                # whether the data_editor actually captured anything.
                # Either the user clicked Save without blurring the cell
                # (Streamlit quirk) OR the data_editor is silently dropping
                # edits to the "proposed title" column.
                st.warning("No changes detected — diagnostic dump below.")
                with st.expander("🔬 Title column: before-edit vs after-edit", expanded=True):
                    try:
                        cmp_rows = []
                        for i in range(min(len(df), len(edited))):
                            before = df.iloc[i].get("proposed title", "")
                            after = edited.iloc[i].get("proposed title", "")
                            cmp_rows.append({
                                "row": i + 1,
                                "source_id": df.iloc[i].get("source_id", ""),
                                "input → editor": before,
                                "← returned by editor": after,
                                "differ?": "YES" if before != after else "—",
                            })
                        st.dataframe(
                            pd.DataFrame(cmp_rows), hide_index=True,
                            width="stretch",
                        )
                        st.caption(
                            "If every row's `differ?` says '—' even after you typed "
                            "in a cell, the cell-commit-on-blur quirk is the cause: "
                            "click outside the edited cell (Tab or click any non-cell "
                            "area) BEFORE clicking Save. If one or more rows say "
                            "YES but apply_cost_edits still reported 0 changes, "
                            "screenshot this and send it — there's a deeper bug."
                        )
                    except Exception as _diag_err:
                        st.caption(f"(Diagnostic dump failed: {_diag_err})")

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
        f"Shopify inventory check"
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
            if st.button("Refresh", key="shopify_refresh", width="stretch"):
                with st.spinner("Fetching from Shopify..."):
                    fresh = fetch_inventory_live()
                if fresh.error:
                    st.error(f"{fresh.error}")
                else:
                    save_inventory_cache(fresh)
                    st.success(
                        f"Fetched {fresh.product_count} products, "
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
    # Column ratio [4, 1] puts the Refresh-cache button at 1/5 of row width —
    # matches the Scan-now, Scan-for-duplicates, and Run-description-audit
    # buttons below so all four primary actions on this tab render the same
    # size regardless of how many controls share their row.
    st.markdown("### Connection")
    c1, c2 = st.columns([4, 1])
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
        if st.button("Refresh cache", key="catalogue_refresh_cache",
                     width="stretch"):
            with st.spinner("Fetching from Shopify..."):
                fresh = fetch_inventory_live()
            if fresh.error:
                st.error(f"{fresh.error}")
            else:
                save_inventory_cache(fresh)
                st.success(
                    f"Refreshed: {fresh.product_count} products, "
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
            "Scan now", key="catalogue_scan_btn",
            width="stretch", type="primary",
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
                f"No photos · {len(result['no_photos'])} products",
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
                            f"Move {len(live_no_photos)} to draft",
                            key="catalogue_unpublish_no_photo_btn",
                            width="stretch",
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
                                f"Un-published {ok_count} no-photo product(s) "
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
                    df, hide_index=True, width="stretch",
                    column_config={
                        "Edit": st.column_config.LinkColumn(
                            "Edit", display_text="🔗 admin",
                        ),
                    },
                )

        if result["wrong_vendor"]:
            with st.expander(
                f"Wrong vendor · {len(result['wrong_vendor'])} products",
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
                    width="stretch",
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
                        f"Apply to {len(plan)} products",
                        key="catalogue_fix_vendor_btn",
                        width="stretch",
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
                            f"Updated vendor on {ok_count} products using "
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
            f"Vendor frequency ({len(result['by_vendor'])} unique vendors)",
            expanded=False,
        ):
            st.caption("Quick sanity check — anything weird in the top of this list?")
            df = pd.DataFrame(
                [{"Vendor": v, "Products": n} for v, n in result["by_vendor"].items()]
            )
            st.dataframe(df, hide_index=True, width="stretch", height=400)

    st.markdown("---")

    # ----- 3. Description audit -------------------------------------------
    render_description_audit_section()

    st.markdown("---")

    # ----- 4. Duplicate finder --------------------------------------------
    st.markdown("### Duplicate finder")
    st.caption(
        "Useful after an accidental double-publish. Scans the live store for "
        "products sharing the same handle or SKU and lists them so you can delete "
        "the extras."
    )
    dc1, dc2 = st.columns([4, 1])  # button at 1/5 row width — matches other audit-tab actions
    with dc1:
        sku_prefix = st.text_input(
            "Filter by SKU prefix (optional)",
            value=st.session_state.get("catalogue_dupe_prefix", ""),
            placeholder="e.g. LOU_, FEN_, BUR_",
            key="catalogue_dupe_prefix",
        )
    with dc2:
        dupe_clicked = st.button(
            "Scan for duplicates", key="catalogue_dupe_btn",
            width="stretch",
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
                    pd.DataFrame(rows), hide_index=True, width="stretch",
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
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            if not n_handle_dupes and not n_sku_dupes:
                st.success("No duplicates found.")


def render_assumed_rates_controls(invoice_currency: str = "JPY") -> tuple[float, float, float, float]:
    """Editable assumed rates — affect this invoice's landed cost.

    Returns (handling_rate, import_tax_rate, extra_rate, extra_flat).

    - Handling + import: only meaningful on vendor invoices (BrandStreet, DKC,
      manual). Buyee invoices have actual fee tables, but the controls still
      render so the user can experiment.
    - Extra %: ad-hoc per-item percentage on top of subtotal (default 0%).
    - Extra flat: lump-sum amount in USD, split equally across items (default
      0). Useful for an extra shipping fee paid separately.
    """
    from costs import HANDLING_RATE as _DEF_HANDLING, IMPORT_TAX_RATE as _DEF_IMPORT
    ccy = (invoice_currency or "JPY").upper()
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
            "Extra flat (USD)",
            min_value=0.0,
            value=float(st.session_state.get("extra_flat", 0.0)),
            step=1.0, format="%.2f",
            help="Lump-sum extra cost in USD, split evenly across items. "
                 "E.g. an additional shipping invoice paid separately. Default 0.",
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
            # "Invoice order" is the default so this table aligns row-for-row
            # with the Cost-review tab (which always shows natural invoice
            # order). Switching to a different sort is opt-in; the Cost tab
            # is unaffected.
            ["Invoice order", "Risk first", "Price (high → low)",
             "Price (low → high)", "Cost (high → low)", "Brand"],
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
    # "Invoice order" and the catch-all both return a constant — Python's
    # sorted() is stable, so a constant key preserves the input list's
    # natural order. This is what makes the Pricing tab match the Cost
    # tab row-for-row when neither user-chosen sort is active.
    if sort_by == "Invoice order":
        return lambda x: 0
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

        # Buyee deep-link with two paths:
        #   1. Yahoo Auctions (lowercase prefix) — URL is DETERMINISTIC from
        #      the source_id, so construct it inline (no scraper dependency).
        #   2. LuxeWholesale (V-prefix) — URL is OPAQUE (Buyee assigns the
        #      15-digit btob ID independently), so we look it up in the
        #      cache populated by `buyee.scraper.scrape_item_urls()`. Cache
        #      MISS → empty link (column renders blank for that row); user
        #      can populate by running `python -m buyee scrape-urls` (or
        #      sending `scrape urls` to the Telegram bot once we wire it).
        auction_url = ""
        sid = item.source_id or ""
        try:
            from buyee.photo_scraper import AUCTION_PATTERN as _AUCT
            if _AUCT.match(sid):
                auction_url = f"https://buyee.jp/item/jdirectitems/auction/{sid}"
            else:
                # Cache lookup — covers V-prefix items AND any Yahoo IDs
                # where the cached URL might be more canonical (e.g. has
                # tracking params Buyee uses internally).
                from buyee.scraper import get_item_url as _gu
                cached = _gu(sid)
                if cached:
                    auction_url = cached
        except Exception:
            pass

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
            "Auction": auction_url,
            "Comps": gem_url,
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

    qa_cols = ["⚠", "Markup", "Band", "Margin", "Margin %", "Source ID", "Auction", "Comps"]
    shopify_cols = ["Title", "Vendor", "Product Category", "Type", "Cost per Item", "Variant Price", "SKU"]

    col_config = {
        "Photo": st.column_config.ImageColumn("Photo", width="small",
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
        "Auction": st.column_config.LinkColumn(
            "Auction", width="small", display_text="buyee ↗",
            help="Open the Buyee item page for this listing — original seller "
                 "photos, full description, condition notes. Covers both "
                 "Yahoo Auctions (lowercase + digits) and LuxeWholesale "
                 "(V-prefix). Blank for CSV-imported rows. Note: Yahoo "
                 "Auction pages expire some weeks after the auction ends; "
                 "expired links redirect to a search page.",
        ),
        "Comps": st.column_config.LinkColumn(
            "Comps", width="small", display_text="search ↗",
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
        width="stretch",
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
        if inv_data_ref is not None and st.button("Save edits", key="save_pricing", type="primary", width="stretch"):
            counts = apply_pricing_edits(inv_data_ref, edited)
            if counts["total"]:
                path = persist_invoice(inv_data_ref)
                # Per-field breakdown — surfaces silent-swallow bugs (e.g.
                # title edit detected but price edit lost because the cell
                # came back as NaN). Earlier this was a bare item-count and
                # there was no way to tell from the UI why prices weren't
                # sticking.
                bits = [f"{counts[k]} {k}" for k in ("price", "title", "vendor", "type") if counts[k]]
                detail = ", ".join(bits) or "no field changes detected"
                st.success(f"Saved {counts['total']} item(s) ({detail}) → {path.name}.")
                st.rerun()
            elif counts["matched_rows"] == 0:
                # Source-ID join collapsed: every invoice item failed to match
                # a row in the data_editor. Usually means the Source ID column
                # was renamed or filtered out — surface it instead of a silent
                # "no changes to save."
                st.error(
                    "Couldn't match any rows back to invoice items by Source ID. "
                    "Refresh the page; if it persists, the Source ID column may "
                    "have been hidden upstream."
                )
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
# Each entry: (glyph, color). Glyph kept empty in the streamlined UI so the
# colored pill carries all the signal; the status word + pill color are
# enough to scan a feedback log without an extra symbol. Color hex values
# are still used as the pill background by _badge_html.
STATUS_BADGES = {
    "pending":  ("", "#a47200"),
    "applied":  ("", "#2e7d32"),
    "rejected": ("", "#9c1c1c"),
    "deferred": ("", "#555"),
}


def render_quick_note_form() -> None:
    """Compact, always-visible capture form for the Rules & Notes tab.

    Replaces the old `render_inline_note_capture` expander (which hid the
    form on the tab whose entire purpose is note management — a discoverability
    bug) and the old `render_sidebar_notes` (which duplicated this form on
    every tab, eating screen width). Now: one form, in the place a user goes
    to deal with notes.
    """
    with st.container(border=True):
        st.markdown("**📝 Add a note**")
        with st.form("quick_note", clear_on_submit=True, border=False):
            text = st.text_area(
                "What did you notice?",
                key="quick_note_text",
                placeholder="e.g. Margiela items come back without era — default to 00's",
                height=80,
                label_visibility="collapsed",
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                topic = st.selectbox(
                    "Topic", TOPIC_OPTIONS, index=0, key="quick_note_topic",
                    label_visibility="collapsed",
                )
            with c2:
                submitted = st.form_submit_button(
                    "Save note", type="primary", width="stretch",
                )
            if submitted:
                if text and text.strip():
                    n = append_feedback(text.strip(), topic=topic)
                    st.success(f"Saved {n.id}.")
                    st.rerun()  # refresh list below so the new note appears
                else:
                    st.warning("Please enter some text first.")


# render_sidebar_notes was removed 2026-06: it duplicated the quick-add form
# and "recent notes" list already present in the Rules & Notes tab, and ate
# screen width on every other tab. The capture form now lives inline at the
# top of render_rules_tab so it's visible without an expander click whenever
# the user is on the notes-management tab.


def _badge_html(status: str) -> str:
    badge, color = STATUS_BADGES.get(status, ("", "#999"))
    label = f"{badge} {status.upper()}".strip()  # strip handles empty-badge case
    return (f"<span style='background:{color};color:white;padding:2px 8px;"
            f"border-radius:3px;font-size:0.75em;font-weight:bold;'>"
            f"{label}</span>")


_NEW_TEMPLATE_SENTINEL = "+ New category…"


def _split_lines(text: str) -> list[str]:
    """Split a multi-line textarea into stripped, non-empty lines."""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _render_snapshots_panel() -> None:
    """Manual snapshot + rollback for bulk Shopify edits. Captures the
    current state of products in the cached scan result so a botched Apply
    can be replayed back. 7-day retention, auto-pruned on render."""
    import snapshots as _snap

    with st.expander("Snapshots & rollback", expanded=False):
        st.caption(
            "Three kinds of snapshots: **weekly** baselines of the live catalog "
            "(kept ~3 months), **pre_apply** auto-snapshots taken before every "
            "bulk Apply (kept 30 minutes — drives the ↩️ Undo button), and "
            "**manual** ad-hoc snapshots of the current audit-scope products "
            "(7-day retention). Restore PUTs every captured product's "
            "`body_html` + `category` back to Shopify."
        )

        # Weekly status — show last weekly's age, plus a button to run one now
        wstatus_cols = st.columns([4, 2])
        with wstatus_cols[0]:
            if _snap.is_weekly_due():
                st.warning(
                    "No weekly snapshot for the current ISO week yet. "
                    "Run `weekly_snapshot.py` (or click the button) to "
                    "capture the live catalog baseline."
                )
            else:
                last_weekly = next(
                    (s for s in _snap.list_snapshots() if s["kind"] == "weekly"),
                    None,
                )
                if last_weekly:
                    st.caption(
                        f"Latest weekly: **{last_weekly['ts_iso']}** · "
                        f"{last_weekly['count']:,} products · "
                        f"`{last_weekly['name']}`"
                    )
        with wstatus_cols[1]:
            if st.button("Run weekly now",
                         key="snap_run_weekly_btn",
                         width="stretch",
                         help="Fetches every live product's current state — "
                              "~30s for a few thousand products."):
                from weekly_snapshot import _list_live_product_ids
                with st.spinner("Listing live products via GraphQL…"):
                    ids, err = _list_live_product_ids()
                if err and not ids:
                    st.error(f"List failed: {err}")
                else:
                    with st.spinner(f"Snapshotting {len(ids):,} products…"):
                        c, p = _snap.create_snapshot(
                            ids, label="catalog_baseline", kind="weekly",
                        )
                    if c > 0:
                        st.success(f"Weekly snapshot: {c:,} products.")
                        st.rerun()
                    else:
                        st.error(f"Snapshot failed: {p}")

        st.markdown("---")

        result = st.session_state.get("desc_audit_result") or {}
        scoped_ids: list[int] = []
        for r in (result.get("category_issues") or []):
            if r.get("id"):
                scoped_ids.append(int(r["id"]))
        for r in (result.get("description_failures") or []):
            if r.get("id"):
                scoped_ids.append(int(r["id"]))
        scoped_ids = sorted(set(scoped_ids))

        sc1, sc2 = st.columns([3, 2])
        with sc1:
            label = st.text_input(
                "Snapshot label",
                value=st.session_state.get("snap_label", ""),
                placeholder="e.g. before-category-bulk-fix",
                key="snap_label",
                help="Short tag baked into the filename — slugified to lowercase letters / numbers / dashes.",
            )
        with sc2:
            st.metric("Products in audit", f"{len(scoped_ids):,}")
        if st.button(
            f"Snapshot now ({len(scoped_ids)} products)",
            key="snap_create_btn",
            disabled=(len(scoped_ids) == 0),
            type="primary",
            width="stretch",
            help=("Captures current Shopify state for every product in the "
                  "latest audit result." if scoped_ids
                  else "Run the audit first to populate the snapshot scope."),
        ):
            with st.spinner(f"Fetching current Shopify state for {len(scoped_ids)} products…"):
                count, result_or_err = _snap.create_snapshot(
                    scoped_ids, label=label or "manual"
                )
            if count > 0:
                st.success(f"Snapshotted {count} products → `{Path(result_or_err).name}`")
            else:
                st.error(f"Snapshot failed: {result_or_err}")

        # ---- Existing snapshots --------------------------------------------
        snaps = _snap.list_snapshots()
        if not snaps:
            st.caption("_No snapshots yet._")
            return

        st.markdown(f"##### {len(snaps)} snapshot(s)")
        # Per-snapshot row: label + View + (Restore | Confirm | Cancel).
        # Previously used 4 columns ([4,1,1,1]) where the 4th was empty
        # unless the user had hit Restore — created uneven visible button
        # widths between confirm and non-confirm states. Now: 3 fixed
        # columns, the Restore button morphs in place into Confirm during
        # the two-click flow, and a Cancel link sits inline as a secondary
        # under the Confirm button instead of stealing its own column.
        for s in snaps:
            with st.container(border=True):
                cols = st.columns([5, 1, 1], vertical_alignment="center")
                with cols[0]:
                    st.markdown(f"**{s['label']}**  ·  {s['count']} products")
                    st.caption(
                        f"{s['ts_iso']}  ·  "
                        f"{s['size_bytes']/1024:.1f} KB  ·  "
                        f"`{s['name']}`"
                    )
                view_key = f"snap_view_{s['name']}"
                with cols[1]:
                    if st.button("View   ", key=f"snap_view_btn_{s['name']}",
                                 width="stretch"):
                        st.session_state[view_key] = not st.session_state.get(view_key, False)
                confirm_key = f"snap_confirm_{s['name']}"
                with cols[2]:
                    if not st.session_state.get(confirm_key):
                        if st.button("Restore", key=f"snap_restore_btn_{s['name']}",
                                     width="stretch"):
                            st.session_state[confirm_key] = True
                            st.rerun()
                    else:
                        if st.button("Confirm",
                                     key=f"snap_confirm_btn_{s['name']}",
                                     type="primary",
                                     width="stretch"):
                            snap = _snap.load_snapshot(s["path"])
                            with st.spinner(
                                f"Restoring {snap.get('count', 0)} products to snapshotted state…"
                            ):
                                stats = _snap.restore_snapshot(snap)
                            st.session_state.pop(confirm_key, None)
                            st.success(
                                f"Restored body_html: {stats['body_html_ok']} ok, "
                                f"{stats['body_html_fail']} failed.  "
                                f"Category: {stats['category_ok']} ok, "
                                f"{stats['category_fail']} failed, "
                                f"{stats['category_skipped']} skipped (none captured)."
                            )
                            if stats["failures"]:
                                with st.expander(
                                    f"{len(stats['failures'])} failure(s)",
                                    expanded=True,
                                ):
                                    for f in stats["failures"][:20]:
                                        st.markdown(
                                            f"- **{f.get('title')}**  ·  "
                                            f"`{f.get('field')}`  ·  "
                                            f"HTTP {f.get('status')}  ·  "
                                            f"`{f.get('response')}`"
                                        )
                            # Clear cached scan so the next audit pull reflects
                            # the restored values
                            st.session_state.pop("desc_audit_result", None)
                        # Cancel as a tiny inline secondary right under the
                        # Confirm button — no longer steals its own column.
                        if st.button("Cancel", key=f"snap_cancel_btn_{s['name']}",
                                     width="stretch", type="tertiary"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()

                if st.session_state.get(view_key):
                    snap = _snap.load_snapshot(s["path"])
                    prods = snap.get("products") or []
                    st.caption(f"Showing first 50 of {len(prods)} products:")
                    df = pd.DataFrame([
                        {
                            "ID": p.get("id"),
                            "Title": p.get("title"),
                            "Vendor": p.get("vendor") or "(none)",
                            "Category": p.get("category_full_name") or "(none)",
                            "Body chars": len(p.get("body_html") or ""),
                        }
                        for p in prods[:50]
                    ])
                    st.dataframe(df, hide_index=True, width="stretch")


def render_description_audit_section() -> None:
    """Description-format audit section for the Shopify audit tab.

    Walks every product in the chosen scope, routes each one to its category
    template (description_templates.yaml → applies_to_categories), runs
    the audit, and surfaces failing listings + products with no matching
    template.
    """
    st.markdown("### Description audit")
    st.caption(
        "Routes every product to its category template (from Copy formats) "
        "using Shopify's **Standard Product Category** field, then flags "
        "listings whose `body_html` is missing required sections, contains "
        "banned phrases, or falls outside the length window. Products whose "
        "category doesn't match any template (or has no category set in "
        "Shopify) are listed separately so you can fix coverage."
    )

    # Snapshot + rollback control — shown above the audit run controls so
    # users have a clear "save current state" affordance before bulk Apply.
    _render_snapshots_panel()

    dc1, dc2, dc3 = st.columns([2, 2, 1])  # button at 1/5 row width — matches other audit-tab actions
    with dc1:
        desc_scope = st.radio(
            "Scope",
            ["Live on website", "All active", "All products (incl. drafts)"],
            index=0,
            key="desc_audit_scope_choice",
            horizontal=True,
        )
    with dc2:
        in_stock_only = st.checkbox(
            "Only in stock (qty > 0)",
            value=st.session_state.get("desc_audit_in_stock_only", True),
            key="desc_audit_in_stock_only",
            help=(
                "Hide products whose tracked variants all have inventory_quantity == 0. "
                "Products that don't track inventory at all are always included."
            ),
        )
    with dc3:
        desc_clicked = st.button(
            "Run description audit", key="desc_audit_btn",
            width="stretch", type="primary",
        )

    scope_map = {
        "Live on website": "live",
        "All active": "active",
        "All products (incl. drafts)": "all",
    }

    if desc_clicked:
        from shopify_push import scan_description_issues
        try:
            with st.spinner("Walking the catalogue + running audit — usually 10-60s…"):
                result = scan_description_issues(
                    scope=scope_map[desc_scope],
                    in_stock_only=in_stock_only,
                )
        except Exception as e:
            st.error(f"Audit crashed: {type(e).__name__}: {e}")
            return
        st.session_state["desc_audit_result"] = result

    # Persistent banner from the last category-apply action. Lives in session
    # state so it survives the st.rerun() that follows an apply, otherwise
    # the success message gets blanked before the user can see it.
    import time as _time
    outcome = st.session_state.get("cat_fix_last_outcome")
    if outcome and (_time.time() - outcome.get("ts", 0)) < 300:
        s = outcome.get("succeeded_count", 0)
        f = outcome.get("failed_count", 0)
        if s:
            st.success(
                f"{s} category write(s) applied. The corrected rows have "
                f"been dropped from the table below. Hit **Run description "
                f"audit** any time for a fresh read from Shopify."
            )
        if f:
            with st.expander(f"{f} category write(s) failed", expanded=True):
                for title, status, resp in (outcome.get("failed_details") or []):
                    st.markdown(f"- **{title}** — HTTP `{status}` — `{resp}`")
        # Always-on debug payload: raw Shopify response for the first up-to-5
        # mutations. Paste this back to me if rows still aren't dropping.
        raw_samples = outcome.get("raw_samples") or []
        if raw_samples:
            import json as _json
            with st.expander(
                "🔬 Debug — raw Shopify mutation responses (paste this if stuck)",
                expanded=False,
            ):
                st.code(
                    _json.dumps(raw_samples, indent=2, default=str),
                    language="json",
                )
        col_dismiss = st.columns([6, 1])[1]
        with col_dismiss:
            if st.button("Dismiss", key="cat_fix_outcome_dismiss",
                         type="tertiary", width="stretch"):
                st.session_state.pop("cat_fix_last_outcome", None)
                st.rerun()

    # Undo: visible whenever a pre_apply snapshot still exists and is fresh
    # (within the 30-min retention window). One click → full restore of that
    # snapshot's products to their pre-write state.
    import snapshots as _snap
    undo_path_str = st.session_state.get("undo_snapshot_path")
    undo_path = Path(undo_path_str) if undo_path_str else None
    if undo_path and undo_path.exists():
        try:
            mtime = undo_path.stat().st_mtime
        except OSError:
            mtime = 0
        age_sec = _time.time() - mtime
        if age_sec <= _snap.PRE_APPLY_RETENTION_MINUTES * 60:
            mins_left = max(0, int(_snap.PRE_APPLY_RETENTION_MINUTES - age_sec / 60))
            ucols = st.columns([5, 2])
            with ucols[0]:
                st.info(
                    f"**Undo available** — last bulk Apply can be rolled "
                    f"back for **{mins_left} more minute(s)**. After that the "
                    f"snapshot is auto-deleted."
                )
            with ucols[1]:
                if st.button(
                    "Undo last change",
                    key="undo_last_change_btn",
                    type="primary",
                    width="stretch",
                ):
                    snap = _snap.load_snapshot(undo_path)
                    with st.spinner(
                        f"Restoring {snap.get('count', 0)} products from pre-apply snapshot…"
                    ):
                        stats = _snap.restore_snapshot(snap)
                    st.session_state.pop("undo_snapshot_path", None)
                    st.session_state.pop("desc_audit_result", None)
                    st.success(
                        f"Undo complete: {stats['body_html_ok']} body_html + "
                        f"{stats['category_ok']} category restored. "
                        f"{stats['body_html_fail'] + stats['category_fail']} failure(s). "
                        f"Re-run the audit to see the rolled-back state."
                    )
                    if stats["failures"]:
                        with st.expander(
                            f"{len(stats['failures'])} restore failure(s)",
                            expanded=True,
                        ):
                            for f in stats["failures"][:20]:
                                st.markdown(
                                    f"- **{f.get('title')}** · `{f.get('field')}` · "
                                    f"HTTP {f.get('status')} · `{f.get('response')}`"
                                )
                    st.rerun()
        else:
            # Past the 30-min window — clear the stale reference
            st.session_state.pop("undo_snapshot_path", None)

    result = st.session_state.get("desc_audit_result")
    if not result:
        st.info("Click **Run description audit** to scan the live catalogue.")
        return

    if result.get("error"):
        st.error(f"{result['error']}")
        return

    scanned = result.get("scanned", 0)
    passing = result.get("passing", 0)
    category_issues = result.get("category_issues") or []
    desc_failures = result.get("description_failures") or []

    # KPI strip — left half is Step 1, right half is Step 2, so the visual
    # ordering matches the workflow.
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Scanned", f"{scanned:,}")
    m2.metric("Step 1 — categories to fix", f"{len(category_issues):,}")
    m3.metric("Step 2 — descriptions to fix", f"{len(desc_failures):,}")
    m4.metric("Fully passing", f"{passing:,}")

    if result.get("partial_error"):
        st.warning(result["partial_error"])

    # Sequential workflow callout
    step1_done = len(category_issues) == 0
    if not step1_done:
        st.info(
            "**Workflow:**  Step 1 → fix product categories on the items first. "
            "Once that's done, every product will route to the right description "
            "template, and Step 2 (descriptions) will reflect the correct buckets. "
            "Step 2 is shown below for reference but you should address Step 1 first."
        )
    else:
        st.info(
            "**Workflow:**  Step 1 (categories) is clear. ✓  Moving on to Step 2 — "
            "products are now routed to the right templates by category, so the "
            "description failures below reflect the real state."
        )

    # ----- Step 1: Fix product categories --------------------------------
    st.markdown("#### Step 1 — Fix product categories")
    st.caption(
        "First, make sure every product has the right Shopify Standard Product "
        "Category. `missing` = no category set; `unmapped` = set but doesn't "
        "route to any template; `mismatched` = routes to template A but title "
        "suggests template B (advisory). Use the auto-populate table to write "
        "categories back in bulk."
    )

    if not category_issues:
        if scanned > 0:
            st.success("✨ Every scanned product has a sensible, routed category.")
    else:
        _render_category_issues(category_issues)

    # ----- Step 2: Fix descriptions ---------------------------------------
    st.markdown("---")
    st.markdown("#### Step 2 — Fix descriptions (template matches by category)")
    st.caption(
        "After Step 1, each product's category routes it to its description "
        "template. This step checks `body_html` against that template: required "
        "sections present, no banned phrases, length window OK. Auto-populate "
        "below pre-fills failing listings with the template body so you only "
        "make corrections."
    )
    if not step1_done:
        st.warning(
            f"Step 1 still has {len(category_issues)} issue(s). Numbers below "
            f"will shift once those are fixed and the audit is re-run, because "
            f"products will route to different templates."
        )

    # Per-template breakdown
    by_template = result.get("by_template") or {}
    if by_template:
        with st.expander(
            f"By template ({len(by_template)} templates matched)",
            expanded=False,
        ):
            df = pd.DataFrame([
                {
                    "Template": name,
                    "Passing": stats.get("passing", 0),
                    "Failing": stats.get("failing", 0),
                    "Total": stats.get("passing", 0) + stats.get("failing", 0),
                }
                for name, stats in by_template.items()
            ])
            st.dataframe(df, hide_index=True, width="stretch")

    if not desc_failures:
        if any(stats.get("passing", 0) for stats in by_template.values()):
            st.success("✨ Every templated product passes its description audit.")
        else:
            st.info("No products routed to a template — fix Phase 1 first.")
        return

    # Combined failure / auto-populate / preview view — one row per product
    # with header metadata, editable body_html, and rendered HTML preview.
    _render_combined_description_failures(desc_failures)


_TEMPLATE_SECTION_LABELS = [
    "TAGGED SIZE",
    "DIMENSIONS",
    "MEASUREMENTS",
    "DETAILS",
    "MATERIAL",
    "CONDITION NOTES",
    "CONDITION",
]
# CONDITION and CONDITION NOTES are aliased — if either appears in existing,
# don't append the other (avoids duplicate condition sections).
_SECTION_ALIASES = {
    "CONDITION NOTES": {"condition notes", "condition"},
    "CONDITION":       {"condition notes", "condition"},
}


def _section_present(body: str, label: str) -> bool:
    """Case-insensitive presence check. Allows arbitrary attributes on the
    <strong> opening tag (e.g. data-start="..." from rich-text editors) so
    a Shopify body with editor metadata still gets detected."""
    import re as _re
    aliases = _SECTION_ALIASES.get(label.upper(), {label.lower()})
    for alias in aliases:
        if _re.search(
            rf'<strong\b[^>]*>\s*{_re.escape(alias)}\s*:\s*</strong>',
            body or "",
            _re.IGNORECASE,
        ):
            return True
    return False


def _extract_template_section(template: str, label: str) -> str:
    """Extract the chunk for `label` from the template — starts at the
    <p><strong>LABEL:</strong> opening and runs up to the next section
    header or end of template."""
    import re as _re
    start_m = _re.search(
        rf'<p[^>]*><strong>\s*{_re.escape(label)}\s*:</strong>',
        template,
        _re.IGNORECASE,
    )
    if not start_m:
        return ""
    next_m = _re.search(
        r'<p[^>]*><strong>\s*[A-Z][A-Z ]*?:</strong>',
        template[start_m.end():],
    )
    end = (start_m.end() + next_m.start()) if next_m else len(template)
    return template[start_m.start():end].rstrip() + "\n"


def _blank_section(chunk: str) -> str:
    """Strip every example/placeholder value from a section chunk."""
    import re as _re
    out = chunk
    out = _re.sub(
        r'(<strong>[A-Z][A-Z &]*?</strong>\s*</td>\s*<td[^>]*?>)[^<]*(</td>)',
        r'\1\2', out,
    )
    out = _re.sub(
        r'(<strong>TAGGED SIZE:</strong>\s*)[^<]*?(</p>)',
        r'\1\2', out, flags=_re.IGNORECASE,
    )
    out = _re.sub(
        r'(<strong>[A-Z][A-Z &]*?:</strong>\s*<br[^>]*>\s*)[^<]*?(</p>)',
        r'\1\2', out,
    )
    out = _re.sub(
        r'(<strong>CONDITION(?:\s*NOTES)?:</strong>\s*)[^<]*?(</p>)',
        r'\1\2', out, flags=_re.IGNORECASE,
    )
    out = _re.sub(r'(<li[^>]*>)[^<]*?(</li>)', r'\1\2', out)
    return out


# Measurement-label normalization: existing bodies use various spellings
# ("Hip" vs "Hips", "Bust" vs "Chest"). Map every variant to the canonical
# template label so values land in the right column.
_MEASUREMENT_ALIAS = {
    "HIP": "HIPS", "HIPS": "HIPS",
    "WAIST": "WAIST", "WAISTS": "WAIST",
    "INSEAM": "INSEAM", "INSEAMS": "INSEAM",
    "RISE": "RISE", "RISES": "RISE",
    "LENGTH": "LENGTH", "LENGTHS": "LENGTH",
    "CHEST": "CHEST", "BUST": "CHEST",
    "SHOULDER": "SHOULDER", "SHOULDERS": "SHOULDER",
    "SLEEVE": "SLEEVE", "SLEEVES": "SLEEVE",
    "TOP LENGTH": "TOP LENGTH",
    "BOTTOM LENGTH": "BOTTOM LENGTH",
}


def _parse_existing_body(body: str) -> dict:
    """Pull section values out of an existing body_html so we can re-emit
    them in the template's canonical structure.

    Tolerant to: rich-text editor attributes on every tag, <span> wrappers
    instead of <strong>, plain-text labels (no wrapper at all), lowercase /
    mixed-case labels, singular/plural variations ("Hip" → "HIPS"), and
    Pages/Word-export class noise like class="p1"/"s1"/"td1".

    Returns:
        {"TAGGED SIZE": "34X34",
         "DIMENSIONS":  "...",
         "MATERIAL":    "...",
         "CONDITION":   "...",
         "MEASUREMENTS": {"WAIST": "34\"", "HIPS": "42\"", ...},
         "DETAILS":     ["...", ...]}
    """
    import re as _re
    out: dict = {}
    if not body:
        return out

    # 1. Inline sections — locate "LABEL:" anywhere (any wrapper, any case)
    # and grab the value up to the next tag or newline. Works for
    # <strong>LABEL:</strong> value</p>, <span>LABEL: value</span>,
    # and bare "LABEL: value" text.
    #
    # Each entry: (canonical-section-name, [phrase aliases to look for]).
    # The first matching alias wins per section.
    SECTION_PROBES = [
        ("TAGGED SIZE", ["TAGGED SIZE", "TAG SIZE", "Size"]),
        ("DIMENSIONS",  ["DIMENSIONS", "Dimensions"]),
        ("MATERIAL",    ["MATERIAL", "Material"]),
        ("CONDITION NOTES", ["CONDITION NOTES"]),
        ("CONDITION",   ["CONDITION"]),
    ]
    for canonical, aliases in SECTION_PROBES:
        if canonical in out:
            continue
        for alias in aliases:
            m = _re.search(
                rf'\b{_re.escape(alias)}\b\s*:\s*'
                rf'(?:</[^>]+>\s*)*'
                rf'(?:<[^/>][^>]*>\s*)*'
                rf'([^<\n]+?)'
                rf'\s*(?=<|\n|$)',
                body, _re.IGNORECASE,
            )
            if m:
                v = m.group(1).strip()
                if v:
                    out[canonical] = v
                    break

    # 2. MEASUREMENTS table — pairs of (label, value), case-insensitive,
    # any wrapper. Captures the first text inside each <td>.
    rows = _re.findall(
        r'<tr\b[^>]*>\s*'
        r'<td\b[^>]*>\s*'
        r'(?:<[^/>][^>]*>\s*)*'                  # any opening tags
        r'([A-Za-z][A-Za-z\s&]*?)'               # the label
        r'\s*:?\s*'                              # optional colon
        r'(?:</[^>]+>\s*)*'                      # any closing tags
        r'</td>\s*'
        r'<td\b[^>]*>\s*'
        r'(?:<[^/>][^>]*>\s*)*'                  # any opening tags
        r'([^<]*?)'                              # the value
        r'\s*(?:</[^>]+>\s*)*'                   # any closing tags
        r'</td>',
        body, _re.IGNORECASE | _re.DOTALL,
    )
    if rows:
        meas: dict = {}
        for raw_label, raw_val in rows:
            norm = _MEASUREMENT_ALIAS.get(
                raw_label.strip().rstrip(":").upper(),
                raw_label.strip().rstrip(":").upper(),
            )
            val = raw_val.strip().replace("\xa0", " ")
            if norm and val:
                meas[norm] = val
        if meas:
            out["MEASUREMENTS"] = meas

    # 2b. Inline measurement labels — for bodies that use
    # "<p>CHEST: 36"</p>" instead of a <table>. Fills any keys not already
    # discovered via the table pass above.
    INLINE_MEASUREMENTS = (
        "CHEST", "LENGTH", "WAIST", "HIPS", "HIP",
        "INSEAM", "RISE", "SHOULDER", "SLEEVE",
        "BUST", "TOP LENGTH", "BOTTOM LENGTH",
    )
    existing_meas = out.get("MEASUREMENTS") or {}
    for label in INLINE_MEASUREMENTS:
        m = _re.search(
            rf'\b{_re.escape(label)}\b\s*:\s*'
            rf'(?:</[^>]+>\s*)*'
            rf'(?:<[^/>][^>]*>\s*)*'
            rf'([^<\n]+?)'
            rf'\s*(?=<|\n|$)',
            body, _re.IGNORECASE,
        )
        if not m:
            continue
        v = m.group(1).strip().replace("\xa0", " ")
        if not v:
            continue
        norm = _MEASUREMENT_ALIAS.get(label.upper(), label.upper())
        # Only fill if not already set by the table-row pass
        if norm not in existing_meas:
            existing_meas[norm] = v
    if existing_meas:
        out["MEASUREMENTS"] = existing_meas

    # 3. DETAILS bullets — find the DETAILS section, grab the chunk between
    # the header and the next bold-labeled section, then extract <li> contents.
    dm = _re.search(
        r'\bDETAILS\b\s*:\s*'                    # DETAILS: anywhere
        r'(?:</[^>]+>\s*)*'                      # optional closing tags after the colon
        r'(.*?)'                                 # the chunk content
        r'(?=<p\b[^>]*>\s*(?:<[^/>][^>]*>\s*)*<strong\b|\Z)',  # next bold-labeled section
        body, _re.IGNORECASE | _re.DOTALL,
    )
    if dm:
        chunk = dm.group(1)
        # 3a. Standard <ul><li>...</li></ul> format
        bullets = _re.findall(
            r'<li\b[^>]*>\s*'
            r'(?:<[^/>][^>]*>\s*)*'
            r'([^<]*?)'
            r'\s*(?:</[^>]+>\s*)*'
            r'</li>',
            chunk, _re.IGNORECASE | _re.DOTALL,
        )
        # 3b. Fallback: <br>-separated text with bullet markers (•, ·, -, *)
        # inside a <p>. Strip remaining HTML, split on <br>, strip the bullet
        # char from each line.
        if not bullets:
            # Find the largest <p>...</p> in the DETAILS chunk
            p_blocks = _re.findall(r'<p\b[^>]*>(.*?)</p>', chunk, _re.IGNORECASE | _re.DOTALL)
            for p_inner in p_blocks:
                lines = _re.split(r'<br\b[^>]*/?>', p_inner, flags=_re.IGNORECASE)
                for line in lines:
                    text = _re.sub(r'<[^>]+>', '', line).strip()
                    # Strip leading bullet markers + any whitespace
                    text = _re.sub(r'^[•·●▪◦\*\-]\s*', '', text).strip()
                    if text:
                        bullets.append(text)
                if bullets:
                    break
        cleaned = [b.strip() for b in bullets if b.strip()]
        if cleaned:
            out["DETAILS"] = cleaned

    return out


def _render_template_with_values(template: str, values: dict) -> str:
    """Take a blank template skeleton and fill in extracted values per section.
    Missing values stay blank. Output uses the template's clean HTML — no
    editor data-* attributes leak through."""
    import re as _re
    out = template

    # Inline sections — value goes right after the bold label (with the
    # template's own <br> separator if present, else nothing)
    for label in ("TAGGED SIZE", "DIMENSIONS", "MATERIAL"):
        v = values.get(label, "")
        def _sub(m, value=v):
            return m.group(1) + (m.group(2) or "") + value + m.group(3)
        out = _re.sub(
            rf'(<strong>{_re.escape(label)}:</strong>)'
            rf'(\s*(?:<br[^>]*>)?\s*)'
            rf'[^<]*?'
            rf'(</p>)',
            _sub, out, count=1, flags=_re.IGNORECASE,
        )

    # CONDITION / CONDITION NOTES — accept either parsed key
    cond_val = values.get("CONDITION NOTES") or values.get("CONDITION") or ""
    def _sub_cond(m, value=cond_val):
        return m.group(1) + (m.group(2) or "") + value + m.group(3)
    out = _re.sub(
        r'(<strong>CONDITION(?:\s*NOTES)?:</strong>)'
        r'(\s*(?:<br[^>]*>)?\s*)'
        r'[^<]*?'
        r'(</p>)',
        _sub_cond, out, count=1, flags=_re.IGNORECASE,
    )

    # MEASUREMENTS table — fill each row's value from the parsed dict
    meas = values.get("MEASUREMENTS") or {}
    def _sub_row(m, table=meas):
        return m.group(1) + table.get(m.group(2).strip().upper(), "") + m.group(3)
    out = _re.sub(
        r'(<strong>([A-Z][A-Z &]*?)</strong>\s*</td>\s*<td[^>]*?>)'
        r'[^<]*?'
        r'(</td>)',
        _sub_row, out,
    )

    # DETAILS bullets — replace the template's blank <li></li> placeholders
    # with the extracted ones.
    bullets = values.get("DETAILS") or []
    if bullets:
        ul_match = _re.search(
            r'(<strong>DETAILS:</strong>\s*</p>\s*)(<ul[^>]*>)(.*?)(</ul>)',
            out, _re.IGNORECASE | _re.DOTALL,
        )
        if ul_match:
            html_bullets = "\n".join(f"  <li>{b}</li>" for b in bullets)
            new_block = (
                ul_match.group(1) + ul_match.group(2) + "\n" +
                html_bullets + "\n" + ul_match.group(4)
            )
            out = out[:ul_match.start()] + new_block + out[ul_match.end():]

    return out


# Per-brand SIZING NOTES injected right after TAGGED SIZE. Each entry is
# (needles, paragraph_html). `needles` is a tuple of lowercase substrings
# searched in vendor + title — first matching entry wins. The paragraph
# should be a single <p>...</p> block; the SIZING NOTES heading is added
# above it automatically. Add new brands by appending another entry.
_VENDOR_SIZING_NOTES: list[dict] = [
    {
        "needles": ("issey miyake", "pleats please"),
        # Strip any session/tracking params (?srsltid=...) before saving —
        # those are per-visit and shouldn't be baked into product descriptions.
        "paragraph": (
            '<p>Please note as ISSEY MIYAKE items are designed with unique '
            'fabrications and fits, sizing may vary between styles. Link to '
            '<a href="https://us.isseymiyake.com/pages/size-charts-by-brand" '
            'target="_blank" rel="noopener">official Issey Miyake sizing '
            'chart</a>.</p>\n'
        ),
    },
]


def _build_sizing_notes_block(vendor: str, title: str) -> str:
    """Pick the first brand entry whose needle appears in vendor + title and
    return a SIZING NOTES block. Returns "" when nothing matches."""
    haystack = f"{vendor or ''} {title or ''}".lower()
    for entry in _VENDOR_SIZING_NOTES:
        if any(n in haystack for n in entry["needles"]):
            return (
                '<p><strong>SIZING NOTES:</strong></p>\n'
                + entry["paragraph"]
            )
    return ""


def _inject_vendor_sizing_notes(body: str, vendor: str, title: str = "") -> str:
    """If vendor (or title) matches a configured brand override, splice the
    SIZING NOTES block right after the TAGGED SIZE paragraph. Stacks
    multiple charts when several entries match (e.g. an Issey Miyake Pleats
    Please item gets both charts). Idempotent."""
    import re as _re
    if not body:
        return body
    chunk = _build_sizing_notes_block(vendor, title)
    if not chunk:
        return body
    if _re.search(r'<strong\b[^>]*>\s*SIZING NOTES\s*:', body, _re.IGNORECASE):
        return body  # already present, don't duplicate

    # Find "TAGGED SIZE:" anywhere, then walk forward to the next </p> —
    # robust against intermediate <strong>/<span> tags.
    label_m = _re.search(r'TAGGED SIZE\s*:', body, _re.IGNORECASE)
    if label_m:
        close = body.find('</p>', label_m.end())
        if close != -1:
            insertion = close + len('</p>')
            return body[:insertion] + "\n" + chunk + body[insertion:]
    # No TAGGED SIZE in body → prepend
    return chunk + body


def _merge_existing_into_template(existing: str, template: str, vendor: str = "", title: str = "") -> str:
    """Reformat the existing body_html into the template's canonical
    structure. Strategy: parse the existing body for each section's value,
    then re-emit the template with those values plugged in.

    Tolerant to rich-text-editor attributes on every tag — that's the bug
    that caused duplicate sections in the old "append missing" approach.

    Missing values come through blank. Existing values are preserved
    verbatim (just relocated into the template's clean HTML).
    """
    if not template:
        return _inject_vendor_sizing_notes(existing or "", vendor, title)
    if not (existing or "").strip():
        return _inject_vendor_sizing_notes(_blank_section(template), vendor, title)

    values = _parse_existing_body(existing)
    merged = _render_template_with_values(_blank_section(template), values)
    return _inject_vendor_sizing_notes(merged, vendor, title)


def _render_combined_description_failures(failing_rows: list[dict]) -> None:
    """Single combined view: filter at top, per-row card with Apply checkbox,
    metadata, editable body_html, and rendered HTML preview, bulk Apply at
    the bottom. Replaces the old read-only failures table + separate
    auto-populate editor.

    Each row's proposed body_html lives in session_state keyed by product
    ID — that way edits survive reruns and the bulk Apply reads whatever
    the user has typed for each row.
    """
    if not failing_rows:
        return

    st.markdown(f"##### {len(failing_rows)} failing descriptions")

    # ----- Filter by template ----------------------------------------------
    options = ["(all)"] + sorted({r["template_name"] for r in failing_rows})
    tf = st.selectbox(
        "Filter by template",
        options,
        key="desc_audit_template_filter",
    )
    rows = (failing_rows if tf == "(all)"
            else [r for r in failing_rows if r["template_name"] == tf])

    if not rows:
        st.info(f"No failing rows for template `{tf}`.")
        return

    LIMIT = 50
    visible = rows[:LIMIT]
    if len(rows) > LIMIT:
        st.caption(
            f"Showing first **{LIMIT}** of {len(rows)} matches. "
            f"Use the filter above to narrow further."
        )

    # ----- Pre-compute proposed bodies (seed session_state once per row) ---
    all_tpls = load_description_templates()
    name_to_tpl = {t.name: t for t in all_tpls}
    for r in visible:
        body_key = f"desc_body_edit_{r['id']}"
        if body_key not in st.session_state:
            tpl = name_to_tpl.get(r["template_name"])
            existing = r.get("body_html", "")
            proposed = _merge_existing_into_template(
                existing, tpl.template,
                vendor=r.get("vendor", ""),
                title=r.get("title", ""),
            ) if tpl else ""
            st.session_state[body_key] = proposed

    # ----- Top action bar --------------------------------------------------
    st.caption(
        "Each row shows the matching template's auto-populated body_html "
        "alongside a live rendered preview. Edit the textarea on the left "
        "to override; the preview updates on the next render. Check **Apply** "
        "on rows you want to push, then hit **Apply selected** below."
    )
    top_cols = st.columns([4, 1, 1])
    with top_cols[1]:
        if st.button("Check all visible", key="desc_check_all_btn",
                     width="stretch"):
            for r in visible:
                st.session_state[f"desc_apply_check_{r['id']}"] = True
            st.rerun()
    with top_cols[2]:
        if st.button("Uncheck all", key="desc_uncheck_all_btn",
                     width="stretch"):
            for r in visible:
                st.session_state[f"desc_apply_check_{r['id']}"] = False
            st.rerun()

    # ----- Per-row cards ---------------------------------------------------
    for r in visible:
        pid = r["id"]
        body_key = f"desc_body_edit_{pid}"
        apply_key = f"desc_apply_check_{pid}"

        with st.container(border=True):
            # Header: checkbox + title + template + findings
            hcols = st.columns([0.5, 5, 2, 4])
            with hcols[0]:
                st.checkbox(
                    "Apply",
                    key=apply_key,
                    label_visibility="collapsed",
                )
            with hcols[1]:
                st.markdown(f"**{r['title']}**")
                st.caption(
                    f"{r.get('vendor') or '(no vendor)'}  ·  "
                    f"created {r.get('created_at', '?')}"
                )
            with hcols[2]:
                st.markdown(f"`{r['template_name']}`")
                st.link_button(
                    "Shopify ↗",
                    r.get("admin_url", "#"),
                    width="stretch",
                )
            with hcols[3]:
                st.caption("**Findings:**  " + " · ".join(r.get("findings") or []))

            # Rendered preview, bounded so a big body doesn't blow out the
            # card height. Click "✏️ Edit HTML" below to switch to raw editor.
            with st.container(border=True, height=180):
                st.markdown(
                    st.session_state[body_key] or "_(empty)_",
                    unsafe_allow_html=True,
                )
            with st.expander("Edit HTML", expanded=False):
                st.text_area(
                    "body_html",
                    key=body_key,
                    height=280,
                    label_visibility="collapsed",
                )

    # ----- Bulk apply ------------------------------------------------------
    st.markdown("")
    # Container-sized so it matches the other primary action buttons on this
    # tab (Run audit / Scan now), and the label is shortened from the
    # jargon-y "PUT body_html to Shopify" — the caption above already
    # explains what Apply does.
    if st.button(
        "Apply selected to Shopify",
        key="desc_apply_btn", type="primary",
        width="stretch",
    ):
        from shopify_push import update_product_body_html
        to_apply = []
        for r in visible:
            if not st.session_state.get(f"desc_apply_check_{r['id']}", False):
                continue
            body = st.session_state.get(f"desc_body_edit_{r['id']}", "")
            to_apply.append((r["id"], body, r["title"]))

        if not to_apply:
            st.warning("No rows checked.")
        else:
            # Auto-snapshot the pre-apply state so Undo can roll this back
            # within the next 30 minutes.
            import snapshots as _snap
            pre_ids = [pid for pid, _, _ in to_apply]
            with st.spinner(f"Snapshotting current state of {len(pre_ids)} products…"):
                _sc, _sp = _snap.create_snapshot(
                    pre_ids, label="step2_body_html", kind="pre_apply"
                )
            if _sc > 0:
                st.session_state["undo_snapshot_path"] = str(_sp)
            succeeded = 0
            failed = []
            progress = st.progress(0.0, text=f"PUTting {len(to_apply)} products…")
            for i, (pid, body, title) in enumerate(to_apply):
                status, resp = update_product_body_html(pid, body)
                if status == 200:
                    succeeded += 1
                else:
                    failed.append((title, status, resp))
                progress.progress(
                    (i + 1) / len(to_apply),
                    text=f"{i+1}/{len(to_apply)} done",
                )
            progress.empty()
            if succeeded:
                # Drop the applied rows from the cached scan so the table
                # visibly shortens without forcing a full Shopify re-fetch.
                cached = st.session_state.get("desc_audit_result")
                if cached:
                    succ_set = {pid for pid, _, _ in to_apply
                                if pid in {sid for sid, _, _ in to_apply}}
                    # Only drop rows we actually succeeded for
                    succ_set = set()
                    fail_titles = {t for t, _, _ in failed}
                    for pid, _, title in to_apply:
                        if title not in fail_titles:
                            succ_set.add(pid)
                    cached["description_failures"] = [
                        r for r in (cached.get("description_failures") or [])
                        if r.get("id") not in succ_set
                    ]
                    st.session_state["desc_audit_result"] = cached
                st.success(f"Updated {succeeded} product(s).")
            if failed:
                st.error(f"{len(failed)} failure(s):")
                for title, status, resp in failed[:10]:
                    st.markdown(f"- **{title}** — HTTP {status} — `{resp}`")
            if succeeded:
                st.rerun()


def _render_category_fix_table(rows: list[dict]) -> None:
    """Editable in-app category fixer.

    The dropdown sources directly from the cached Shopify taxonomy — pick
    any category for any product, no template indirection. Apply button
    PUTs each checked row to Shopify via the REST product.category field.
    """
    import shopify_taxonomy as _stax

    taxonomy = _stax.load_taxonomy()
    if not taxonomy:
        st.warning(
            "🛠 Setup needed: the Shopify taxonomy isn't cached yet. Open "
            "the **📝 Copy formats** tab → hit **📥 Fetch taxonomy** "
            "(one-time, ~5–30s). The Apply button below stays disabled "
            "until then."
        )

    NO_CHANGE = "(don't change)"
    sorted_tax = sorted(taxonomy, key=lambda x: (x.get("full_name") or "").lower())
    options: list[str] = [NO_CHANGE] + [item["full_name"] for item in sorted_tax]
    gid_by_fullname = {item["full_name"]: item["id"] for item in sorted_tax}

    # Auto-populate the best Shopify taxonomy match per row. We deliberately
    # don't use st.cache_data here — the taxonomy file can change beneath us
    # (Refresh button) and a sticky empty result was the bug that made all
    # rows show "(don't change)". 200 rows × 525 nodes ≈ 100k comparisons,
    # which is a sub-100ms hit in Python.
    from shopify_taxonomy import suggest_category_for_product as _suggest_cat
    def _row_suggestion(title: str, product_type: str, tags: str) -> tuple:
        hit = _suggest_cat(title, product_type, tags, taxonomy=taxonomy)
        if not hit:
            return ("", "")
        return (hit.get("id") or "", hit.get("full_name") or "")

    # Apply pending "check all" toggle BEFORE the widget is instantiated —
    # Streamlit raises if we modify the widget's session_state key after.
    check_all_pending = st.session_state.pop("cat_fix_check_all_pending", False)

    table_rows = []
    for r in rows[:200]:
        _, suggested_full_name = _row_suggestion(
            r.get("title") or "",
            r.get("product_type") or "",
            r.get("tags") or "",
        )
        default_label = suggested_full_name if suggested_full_name in options else NO_CHANGE

        default_apply = (
            r["kind"] in ("missing", "unmapped")
            and default_label != NO_CHANGE
        )
        if check_all_pending:
            default_apply = default_label != NO_CHANGE
        table_rows.append({
            "Apply": default_apply,
            "Kind": _KIND_LABEL[r["kind"]],
            "Title": r["title"],
            "Current category": r.get("category") or "(unset)",
            "Set category to": default_label,
            "_product_id": r["id"],
        })

    df = pd.DataFrame(table_rows)

    if check_all_pending:
        # Reseed the widget so the new defaults take effect — popping the
        # widget's session entry forces re-init from `df` on this render.
        st.session_state.pop("cat_fix_editor", None)

    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        key="cat_fix_editor",
        column_config={
            "Apply": st.column_config.CheckboxColumn("Apply"),
            "Kind": st.column_config.TextColumn("Kind", disabled=True),
            "Title": st.column_config.TextColumn(
                "Title", disabled=True, width="medium",
            ),
            "Current category": st.column_config.TextColumn(
                "Current category", disabled=True,
            ),
            "Set category to": st.column_config.SelectboxColumn(
                "Set Shopify category to",
                options=options,
                required=True,
            ),
            "_product_id": st.column_config.NumberColumn(
                "_product_id", disabled=True, format="%d",
            ),
        },
    )
    if len(rows) > 200:
        st.caption(f"…and {len(rows) - 200} more (cap at 200).")

    bc1, bc2 = st.columns([1, 1])
    with bc1:
        apply_disabled = not taxonomy
        if st.button(
            "Apply selected — PUT categories to Shopify",
            key="cat_fix_apply_btn", type="primary",
            width="stretch",
            disabled=apply_disabled,
            help=("Fetch the Shopify taxonomy in Copy formats first."
                  if apply_disabled else None),
        ):
            from shopify_push import update_product_category
            to_apply = []
            for _, row in edited.iterrows():
                if not row["Apply"]:
                    continue
                full_name = row["Set category to"]
                if full_name == NO_CHANGE:
                    continue
                gid = gid_by_fullname.get(full_name, "")
                if not gid:
                    continue
                to_apply.append((int(row["_product_id"]), gid, row["Title"]))

            if not to_apply:
                st.warning(
                    "No rows applied — nothing checked, or rows had '(don't "
                    "change)' selected."
                )
            else:
                import time as _time
                # Auto-snapshot the pre-apply state so the Undo button can
                # roll this batch back within the next 30 minutes.
                import snapshots as _snap
                pre_ids = [pid for pid, _, _ in to_apply]
                with st.spinner(f"Snapshotting current state of {len(pre_ids)} products…"):
                    _sc, _sp = _snap.create_snapshot(
                        pre_ids, label="step1_categories", kind="pre_apply"
                    )
                if _sc > 0:
                    st.session_state["undo_snapshot_path"] = str(_sp)
                succeeded_ids: list[int] = []
                failed: list[tuple] = []
                raw_samples: list[dict] = []  # full debug payload, first 5
                progress = st.progress(0.0, text=f"PUTting {len(to_apply)} products…")
                for i, (pid, gid, title) in enumerate(to_apply):
                    status, resp = update_product_category(pid, gid)
                    if status == 200:
                        succeeded_ids.append(pid)
                    else:
                        failed.append((title, status, resp))
                    if len(raw_samples) < 5:
                        raw_samples.append({
                            "product_id": pid,
                            "title": title,
                            "category_gid_sent": gid,
                            "http_status": status,
                            "response": resp,
                        })
                    progress.progress(
                        (i + 1) / len(to_apply),
                        text=f"{i+1}/{len(to_apply)} done",
                    )
                progress.empty()

                # Persist outcome so the banner survives the rerun below
                st.session_state["cat_fix_last_outcome"] = {
                    "ts": _time.time(),
                    "succeeded_count": len(succeeded_ids),
                    "failed_count": len(failed),
                    "failed_details": failed[:10],
                    "raw_samples": raw_samples,
                }

                # Optimistically drop the just-applied rows from the cached
                # scan so the table visibly shortens without forcing a full
                # Shopify re-fetch. User can hit "Run description audit"
                # whenever they want a fresh read.
                if succeeded_ids:
                    success_set = set(succeeded_ids)
                    cached = st.session_state.get("desc_audit_result")
                    if cached:
                        cached["category_issues"] = [
                            r for r in (cached.get("category_issues") or [])
                            if r.get("id") not in success_set
                        ]
                        st.session_state["desc_audit_result"] = cached
                st.rerun()
    with bc2:
        if st.button(
            "Check all rows", key="cat_fix_check_all_btn",
            width="stretch",
        ):
            # Set a pending flag instead of mutating the widget state directly
            # (Streamlit forbids modifying widget-bound session_state after
            # the widget has instantiated this run).
            st.session_state["cat_fix_check_all_pending"] = True
            st.rerun()


_KIND_LABEL = {
    "missing":    "🔴 No category set",
    "unmapped":   "🟡 Category not mapped to a template",
}


def _render_category_issues(issues: list[dict]) -> None:
    """Phase 1 detail UI: by-kind summary, per-row table, and the bulk
    'map unmapped category → template' tool."""
    # By-kind tally — only Missing / Unmapped. The old "Mismatched" bucket
    # was a heuristic flag that re-fired even after the user fixed a row,
    # so we no longer emit it from the scan.
    counts = {"missing": 0, "unmapped": 0}
    for r in issues:
        if r["kind"] in counts:
            counts[r["kind"]] += 1
    c1, c2 = st.columns(2)
    c1.metric(_KIND_LABEL["missing"], counts["missing"])
    c2.metric(_KIND_LABEL["unmapped"], counts["unmapped"])

    # Filter chip
    kind_filter = st.radio(
        "Show",
        ["All", "Missing", "Unmapped"],
        index=0,
        horizontal=True,
        key="cat_audit_kind_filter",
    )
    kind_map = {"All": None, "Missing": "missing", "Unmapped": "unmapped"}
    selected_kind = kind_map[kind_filter]
    rows = issues if selected_kind is None else [r for r in issues if r["kind"] == selected_kind]

    if not rows:
        st.info("Nothing in this bucket.")
    else:
        _render_category_fix_table(rows)

    # ----- Bulk-fix: map unmapped Shopify categories → templates ----------
    unmapped = [r for r in issues if r["kind"] == "unmapped" and r.get("category")]
    if unmapped:
        with st.expander(
            "⚙️ Bulk: map unmapped Shopify categories to templates",
            expanded=False,
        ):
            st.caption(
                "Each row is a unique Shopify category with no template. "
                "Pick which template should cover it, hit **💾 Apply** — the "
                "category string gets appended to that template's "
                "`applies_to_categories` so the next scan will route those "
                "products correctly. Use the suggested column as a hint."
            )
            # Group unmapped issues by category and remember the best suggestion
            by_cat: dict[str, dict] = {}
            for r in unmapped:
                slot = by_cat.setdefault(r["category"], {"count": 0, "suggested": None})
                slot["count"] += 1
                if r.get("suggested_template") and not slot["suggested"]:
                    slot["suggested"] = r["suggested_template"]

            templates_list = load_description_templates()
            template_names = [t.name for t in templates_list]
            LEAVE_UNMAPPED = "(leave unmapped)"

            mapping_df = pd.DataFrame([
                {
                    "Shopify category": c,
                    "Count": v["count"],
                    "Suggested": v["suggested"] or "—",
                    "Map to template": v["suggested"] if v["suggested"] in template_names else LEAVE_UNMAPPED,
                }
                for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1]["count"])
            ])

            edited = st.data_editor(
                mapping_df,
                hide_index=True,
                width="stretch",
                key="cat_audit_bulk_editor",
                column_config={
                    "Shopify category": st.column_config.TextColumn(
                        "Shopify category", disabled=True,
                    ),
                    "Count": st.column_config.NumberColumn(
                        "Count", disabled=True, format="%d",
                    ),
                    "Suggested": st.column_config.TextColumn(
                        "Suggested", disabled=True,
                    ),
                    "Map to template": st.column_config.SelectboxColumn(
                        "Map to template",
                        options=[LEAVE_UNMAPPED] + template_names,
                        required=True,
                    ),
                },
            )

            if st.button("Apply mappings",
                         key="cat_audit_apply_mappings_btn",
                         type="primary", width="stretch"):
                changes: dict[str, list[str]] = {}
                for _, row in edited.iterrows():
                    target = row["Map to template"]
                    if target == LEAVE_UNMAPPED:
                        continue
                    changes.setdefault(target, []).append(row["Shopify category"])

                if not changes:
                    st.info("Nothing to apply — no rows had a template selected.")
                else:
                    updated_count = 0
                    for t in templates_list:
                        adds = changes.get(t.name, [])
                        if not adds:
                            continue
                        existing_lower = {c.strip().lower() for c in t.applies_to_categories}
                        for cat in adds:
                            if cat.strip().lower() not in existing_lower:
                                t.applies_to_categories.append(cat)
                                updated_count += 1
                    save_description_templates(templates_list)
                    st.session_state.pop("desc_audit_result", None)
                    st.success(
                        f"Added {updated_count} category mapping(s) across "
                        f"{len(changes)} template(s). Re-run the audit."
                    )
                    st.rerun()


def render_copy_formats_tab() -> None:
    """Per-category copy-format staging area.

    Lets the user define which substrings must appear in a product
    description (e.g. "Condition", "Measurements"), which phrases are banned
    (e.g. "gorgeous"), a length window, and a reference template. The
    description-format audit will read these to flag listings that don't
    conform.
    """
    st.markdown("### Copy formats")
    st.caption(
        f"Per-category description rules — drives the upcoming description-format "
        f"audit. Stored at `{DESCRIPTION_TEMPLATES_PATH.name}`; this tab is the "
        f"source of truth (the file is rewritten on every save, so the leading "
        f"comment block doesn't survive — edit here, not by hand)."
    )

    # --- Shopify taxonomy cache status + fetch button --------------------
    import shopify_taxonomy as _stax
    tax_cached = _stax.load_taxonomy()
    age = _stax.cache_age_seconds()

    tcols = st.columns([3, 2, 1])
    with tcols[0]:
        if tax_cached:
            st.caption(
                f"📚 Shopify taxonomy cached: **{len(tax_cached):,} categories** · "
                f"last refreshed **{_stax.humanize_age(age)}** · "
                f"`{_stax.cache_path().relative_to(BASE)}`"
            )
        else:
            st.warning(
                "Shopify taxonomy not cached yet — fetch it to enable the "
                "category-picker dropdown in each template below."
            )
    with tcols[1]:
        fetch_scope = st.radio(
            "Scope",
            ["Apparel only (fast)", "Everything (slow)"],
            index=0,
            horizontal=True,
            key="copy_fmt_tax_scope",
            help=(
                "Apparel only: descend into Apparel & Accessories + Luggage & "
                "Bags. ~1500 nodes, 5-15s. Everything: walk all 26 roots, "
                "~10k nodes, 30-90s — rarely needed for a fashion catalogue."
            ),
        )
    with tcols[2]:
        if st.button(
            "Refresh taxonomy" if tax_cached else "Fetch taxonomy",
            key="copy_fmt_fetch_taxonomy_btn",
            width="stretch",
        ):
            roots = None if fetch_scope == "Apparel only (fast)" else ["*"]
            with st.spinner(
                "Walking Shopify Admin GraphQL `taxonomy` "
                f"({'apparel subtrees' if roots is None else 'full tree'})…"
            ):
                count, items_or_err = _stax.fetch_taxonomy(root_names=roots)
            if count > 0:
                st.success(f"Cached {count:,} categories.")
                st.rerun()
            else:
                st.error(f"Fetch failed: {items_or_err}")

    # Cost catalog: per-product cost from Shopify InventoryItem.unitCost.
    # Powers the CSV ingest's auto-fill for items without a Cost per Item.
    import cost_estimator as _cest
    cost_cached = _cest.is_shopify_cost_cached()
    cost_age = _cest.shopify_cost_cache_age_seconds()
    ccols = st.columns([4, 1])
    with ccols[0]:
        if cost_cached:
            try:
                import json as _json
                _cost_entries = _json.loads(_cest.SHOPIFY_COST_CACHE.read_text())
            except Exception:
                _cost_entries = []
            st.caption(
                f"Shopify cost catalog cached: **{len(_cost_entries):,} products** with "
                f"per-unit cost · last refreshed **{_stax.humanize_age(cost_age)}** · "
                f"powers the CSV-ingest cost auto-fill."
            )
        else:
            st.caption(
                "Shopify cost catalog: not cached yet — fetch to power "
                "CSV-ingest cost auto-fill (especially for vintage / "
                "non-luxury vendors not in past invoice data)."
            )
    with ccols[1]:
        if st.button(
            "Refresh costs" if cost_cached else "Fetch costs",
            key="copy_fmt_fetch_costs_btn",
            width="stretch",
        ):
            with st.spinner(
                "Walking Shopify Admin GraphQL for InventoryItem.unitCost "
                "(~30-60s for a few thousand products)…"
            ):
                count, msg = _cest.fetch_shopify_costs()
            if count > 0:
                st.success(f"{msg}")
                st.rerun()
            else:
                st.error(f"Fetch failed: {msg}")

    templates = load_description_templates()
    names = [t.name for t in templates]

    selectable = names + [_NEW_TEMPLATE_SENTINEL]

    # Apply any pending selection set by a save/delete handler from the
    # previous run. Must happen BEFORE the selectbox is instantiated —
    # Streamlit raises if you reassign a widget-bound session_state key
    # after the widget exists in the same run.
    pending = st.session_state.pop("copy_formats_pending_select", None)
    if pending in selectable:
        st.session_state["copy_formats_selected"] = pending

    default_idx = 0
    prior = st.session_state.get("copy_formats_selected")
    if prior in selectable:
        default_idx = selectable.index(prior)

    selected = st.selectbox(
        "Category",
        selectable,
        index=default_idx,
        key="copy_formats_selected",
        help="Pick a category to edit, or '+ New category…' to add one.",
    )

    is_new = selected == _NEW_TEMPLATE_SENTINEL
    current: DescriptionTemplate
    if is_new:
        current = DescriptionTemplate(name="")
    else:
        current = next(t for t in templates if t.name == selected)

    # Form key changes per template so widget state resets on selection switch
    form_key = f"copy_format_form__{selected}"
    with st.form(form_key, clear_on_submit=False):
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            name = st.text_input(
                "Category name",
                value=current.name,
                placeholder="e.g. Footwear",
                help="Short label, also the dict key. Must be unique.",
            )
        with c2:
            min_length = st.number_input(
                "Min length (chars)",
                value=int(current.min_length) if current.min_length is not None else 0,
                min_value=0,
                step=50,
                help="0 = no minimum.",
            )
        with c3:
            max_length = st.number_input(
                "Max length (chars)",
                value=int(current.max_length) if current.max_length is not None else 0,
                min_value=0,
                step=100,
                help="0 = no maximum.",
            )

        applies_to_text = st.text_area(
            "Applies to Shopify categories (one per line)",
            value="\n".join(current.applies_to_categories),
            height=130,
            help=(
                "Shopify Standard Product Category strings this template covers. "
                "Match is case-insensitive substring — so an entry “Handbags” "
                "matches both the leaf name and the full taxonomy path "
                "“Apparel & Accessories > Handbags, Wallets & Cases > Handbags”."
            ),
        )

        # Shopify category picker — sourced from the cached taxonomy if available,
        # otherwise we fall back to a text input so the field is still editable.
        if tax_cached:
            NO_CAT = "(none — don't auto-populate this template's category)"
            sorted_tax = sorted(tax_cached, key=lambda x: (x.get("full_name") or "").lower())
            fullname_options = [NO_CAT] + [item["full_name"] for item in sorted_tax]
            gid_by_fullname = {item["full_name"]: item["id"] for item in sorted_tax}
            fullname_by_gid = {item["id"]: item["full_name"] for item in sorted_tax}

            current_gid = current.shopify_category_gid_normalized()
            current_label = fullname_by_gid.get(current_gid, NO_CAT) if current_gid else NO_CAT
            if current_label not in fullname_options:
                # Stored GID isn't in the cached taxonomy (stale cache or
                # deprecated node). Fall back to NO_CAT but warn.
                st.warning(
                    f"Currently stored category GID `{current_gid}` isn't in "
                    f"the cached taxonomy — refresh the taxonomy or re-pick."
                )
                current_label = NO_CAT

            picked_fullname = st.selectbox(
                "Shopify Standard Product Category",
                options=fullname_options,
                index=fullname_options.index(current_label),
                help=(
                    "Pick this template's canonical Shopify category. Used by the "
                    "Phase 1 audit's Auto-populate action to write the category "
                    "back to products that are missing or wrong. Type to filter."
                ),
            )
            shopify_gid_input = (
                "" if picked_fullname == NO_CAT
                else gid_by_fullname.get(picked_fullname, "")
            )
        else:
            st.info(
                "Taxonomy cache empty — fetch it above to get the dropdown. "
                "For now you can paste a GID manually:"
            )
            shopify_gid_input = st.text_input(
                "Shopify Taxonomy ID (fallback — paste GID manually)",
                value=current.shopify_category_gid,
                placeholder="e.g. aa-1-13-8  or  gid://shopify/TaxonomyCategory/aa-1-13-8",
            )

        rs_col, bp_col = st.columns(2)
        with rs_col:
            required_text = st.text_area(
                "Required sections (one per line)",
                value="\n".join(current.required_sections),
                height=160,
                help="Substrings that must appear in the description (case-insensitive).",
            )
        with bp_col:
            banned_text = st.text_area(
                "Banned phrases (one per line)",
                value="\n".join(current.banned_phrases),
                height=160,
                help="Substrings that must NOT appear (case-insensitive).",
            )

        template_body = st.text_area(
            "Reference template",
            value=current.template,
            height=220,
            help="Starter copy shown when drafting a new listing in this category. "
                 "HTML is supported — see preview below.",
        )

        with st.expander("Preview rendered HTML", expanded=False):
            if template_body.strip():
                st.markdown(template_body, unsafe_allow_html=True)
            else:
                st.caption("(template is empty)")

        notes = st.text_area(
            "Notes (designer-only, not used by audit)",
            value=current.notes,
            height=80,
        )

        save_clicked = st.form_submit_button("Save", type="primary", width="content")

    # Save handler
    if save_clicked:
        new_name = (name or "").strip()
        if not new_name:
            st.error("Category name is required.")
        else:
            collisions = [t.name for t in templates if t.name == new_name and t.name != current.name]
            if is_new and new_name in names:
                st.error(f"A category named “{new_name}” already exists. Pick a different name.")
            elif collisions:
                st.error(f"A category named “{new_name}” already exists. Pick a different name.")
            else:
                updated = DescriptionTemplate(
                    name=new_name,
                    applies_to_categories=_split_lines(applies_to_text),
                    required_sections=_split_lines(required_text),
                    banned_phrases=_split_lines(banned_text),
                    min_length=int(min_length) if min_length else None,
                    max_length=int(max_length) if max_length else None,
                    template=template_body or "",
                    notes=notes or "",
                    shopify_category_gid=(shopify_gid_input or "").strip(),
                )
                if is_new:
                    templates.append(updated)
                else:
                    for i, t in enumerate(templates):
                        if t.name == current.name:
                            templates[i] = updated
                            break
                save_description_templates(templates)
                st.session_state["copy_formats_pending_select"] = new_name
                st.success(f"Saved “{new_name}”.")
                st.rerun()

    # Delete (outside the form so it doesn't fight the save submit)
    if not is_new:
        with st.expander("Delete this category", expanded=False):
            confirm = st.checkbox(
                f"Yes, delete “{current.name}” permanently.",
                key=f"copy_formats_confirm_delete__{current.name}",
            )
            if st.button("Delete category", key=f"copy_formats_delete_btn__{current.name}",
                         disabled=not confirm, type="primary", width="stretch"):
                remaining = [t for t in templates if t.name != current.name]
                save_description_templates(remaining)
                next_select = remaining[0].name if remaining else _NEW_TEMPLATE_SENTINEL
                st.session_state["copy_formats_pending_select"] = next_select
                st.success(f"Deleted “{current.name}”.")
                st.rerun()

    # ----- Preview panel ---------------------------------------------------
    st.markdown("---")
    st.markdown("#### Preview — paste a description to check it")
    st.caption(
        "Paste a real product description (HTML or plain text) and see how the "
        "currently-loaded template above will judge it. This is the same check "
        "the upcoming catalogue audit will run."
    )

    sample = st.text_area(
        "Sample description",
        value=st.session_state.get("copy_formats_preview_sample", ""),
        height=220,
        key="copy_formats_preview_sample",
        placeholder="<p>Vintage Chanel quilted lambskin flap bag…</p>",
    )

    if sample.strip():
        # Audit against the in-memory edited values (using the form's last
        # saved state — i.e. `current`). If the user wants to test unsaved
        # tweaks they save first; that's the contract.
        preview_tpl = current if not is_new else DescriptionTemplate(name="(new)")
        result = audit_description(sample, preview_tpl)
        if result["passed"]:
            st.success(f"Passes the “{preview_tpl.name or '(new)'}” template.")
        else:
            st.warning(f"{len(result['findings'])} issue(s) against “{preview_tpl.name or '(new)'}”:")
            for f in result["findings"]:
                st.markdown(f"- {f}")

        with st.expander("Preview rendered HTML", expanded=False):
            st.markdown(sample, unsafe_allow_html=True)


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
            st.markdown(f"##### Stale pending notes  &nbsp; *{len(stale)} pending > 2 days*")
            st.caption(
                "These came in a while ago and haven't been marked addressed. "
                "Click a status button to dispose of each one."
            )
            # Status-action buttons: identical width per button (equal columns
            # 2/2/2 of the action area), padded labels so the glyph+word
            # combos visually balance, and `type=` gives one primary action
            # (Applied — the affirmative path) with the other two as
            # secondary. Wider text column trimmed from 5/8 → 6/12 so the
            # buttons get real width instead of being squashed into 1/8 each.
            BTN_LABELS = {
                "applied":  "Applied",
                "deferred": "Defer",
                "rejected": "Reject",
            }
            for n in stale:
                age = (_date.today() - n.date).days
                age_str = f"{age}d ago" if age else "today"
                snippet = n.quote.strip().split("\n")[0]
                if len(snippet) > 110:
                    snippet = snippet[:107] + "..."
                cols = st.columns([6, 2, 2, 2], gap="small", vertical_alignment="center")
                with cols[0]:
                    st.markdown(
                        f"**{n.id}**  ·  *{n.topic}*  ·  {age_str}  \n"
                        f"<span style='color:#444'>{snippet}</span>",
                        unsafe_allow_html=True,
                    )
                with cols[1]:
                    if st.button(BTN_LABELS["applied"], key=f"stale_apply_{n.id}",
                                 width="stretch", type="primary"):
                        update_feedback_status(n.id, "applied")
                        st.rerun()
                with cols[2]:
                    if st.button(BTN_LABELS["deferred"], key=f"stale_defer_{n.id}",
                                 width="stretch", type="secondary"):
                        update_feedback_status(n.id, "deferred")
                        st.rerun()
                with cols[3]:
                    if st.button(BTN_LABELS["rejected"], key=f"stale_reject_{n.id}",
                                 width="stretch", type="secondary"):
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
                # Status word as the leading chip (replaces the prior glyph
                # which the streamlined UI dropped). Upper-case for scanability.
                f"[{n.status.upper()}]  "
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
                    # Tolerate any legacy/unknown status value (e.g. an older
                    # "resolved") by surfacing it as an extra option instead of
                    # crashing the whole tab on .index().
                    _status_opts = ["pending", "applied", "rejected", "deferred"]
                    if n.status not in _status_opts:
                        _status_opts = _status_opts + [n.status]
                    new_status = st.selectbox(
                        "Set status",
                        _status_opts,
                        index=_status_opts.index(n.status),
                        key=f"st_{n.id}",
                    )
                    if st.button("Update", key=f"upd_{n.id}", width="stretch"):
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
    with st.expander(f"Title rules", expanded=False):
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
        f"Model → era database  ·  "
        f"{sum(len(v) for v in rules.model_era.values())} entries  ·  WIRED",
        expanded=False,
    ):
        st.caption("Edits to this section in rules.yaml take effect immediately.")
        rows = [
            {"Brand": brand, "Model": model, "Era": era}
            for brand, models in rules.model_era.items()
            for model, era in models.items()
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    with st.expander(
        f"Brand archetypes  ·  "
        f"{sum(len(v) for v in rules.brand_archetypes.values())} pairs  ·  MIRROR",
        expanded=False,
    ):
        st.caption("Mirror only — also edit `extractors.BRAND_ARCHETYPES` until migrated.")
        rows = [
            {"Brand": brand, "Type": ptype, **defaults}
            for brand, types in rules.brand_archetypes.items()
            for ptype, defaults in types.items()
        ]
        st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, width="stretch")

    with st.expander(
        f"Brand tiers  ·  "
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
        f"Canonicalization  ·  "
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
            st.dataframe(df, hide_index=True, width="stretch", height=300)
        with c2:
            st.markdown("**Type aliases**")
            df = pd.DataFrame(
                [{"Input": k, "Canonical": v} for k, v in rules.canonicalize.get("types", {}).items()]
            )
            st.dataframe(df, hide_index=True, width="stretch", height=300)

    with st.expander(
        f"Regression anchors  ·  {len(rules.regression_anchors)} confirmed titles",
        expanded=False,
    ):
        st.caption("Snapshot tests should verify these stay correct after rule changes.")
        rows = [
            {"source_id": a.source_id, "expected_title": a.expected_title or "(empty)",
             "note": a.note or ""}
            for a in rules.regression_anchors
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

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
        "spot-check those manually via the `Comps` link per item in the "
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
            hide_index=True, width="stretch",
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
            f"`{invoice_type}`  ·  {len(categories)} bracket tables",
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
                            hide_index=True, width="stretch",
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


def render_bulk_drop_audit_tab() -> None:
    """SKU/tag search → bag/accessory description drafts → approved Shopify writes."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse

    import drop_audit as da
    from shopify_inventory import get_shop, is_configured

    st.subheader("Bulk Drop Audit")
    st.caption(
        "Find bag/accessory listings by SKU or tag, generate Shopify-template "
        "description drafts, review dimensions/condition per listing, then push "
        "approved descriptions only. Existing descriptions are skipped."
    )

    if not is_configured():
        st.warning(
            "Shopify isn't configured. Connect it in the **Shopify audit** tab first."
        )
        return

    # Outline + grey-fill the search box so it's obvious where terms go.
    st.markdown(
        """<style>
        div[data-baseweb="textarea"]:has(textarea[aria-label^="SKUs"]),
        div[data-baseweb="textarea"]:has(textarea[aria-label^="Tags"]) {
            border: 1.5px solid #9a9a9a !important;
            border-radius: 8px;
            background-color: #f5f5f7;
        }
        textarea[aria-label^="SKUs"], textarea[aria-label^="Tags"] {
            background-color: #f5f5f7 !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    mode_col, terms_col = st.columns([1, 3])
    with mode_col:
        mode = st.selectbox("Search Shopify by", ["SKUs", "Tags"], key="bda_search_mode")
    unit = "SKU" if mode == "SKUs" else "tag"
    with terms_col:
        terms_text = st.text_area(
            f"{mode} — one per line, commas OK",
            key=f"bda_terms::{mode}",
            height=120,
            placeholder=(
                "Paste one SKU per line. Commas are OK too."
                if mode == "SKUs"
                else "e.g. drop-7 — one tag per line, commas OK."
            ),
        )
    terms = da.parse_skus(terms_text)
    st.caption(f"Parsed {len(terms)} unique {unit}(s)." if terms else f"Paste {unit}s above to search.")

    if st.button(f"Search by {unit}", type="primary", width="stretch", disabled=not terms, key="bda_search"):
        shop = get_shop() or ""
        with st.spinner(f"Searching Shopify for {len(terms)} {unit}(s)…"):
            if mode == "SKUs":
                products = da.lookup_products_by_skus(terms, shop=shop)
            else:
                products = da.lookup_products_by_tags(terms, shop=shop)
        st.session_state["bda_products"] = products
        st.session_state["bda_drafts"] = {}
        st.session_state["bda_sources"] = {}
        st.session_state["bda_warnings"] = {}
        st.session_state["bda_pushed"] = set()
        st.session_state["bda_gen_attempted"] = set()
        st.session_state["bda_gen_errors"] = {}
        for k in [
            k for k in st.session_state
            if isinstance(k, str) and k.startswith(("bda_prefilled::", "bda_dim_suggestion::"))
        ]:
            del st.session_state[k]

    products = st.session_state.get("bda_products") or []
    if not products:
        st.info("Pick a search mode, paste terms, and click **Search** to begin.")
        return

    # Plan twice: the base plan feeds the force-include options; the final
    # plan honors whatever the user has force-included so far.
    base_rows = da.plan_products(products)
    not_eligible_skus = [r.product.sku for r in base_rows if r.status == "Not eligible"]
    forced = set(st.session_state.get("bda_force_eligible") or [])
    rows = da.plan_products(products, force_eligible=forced) if forced else base_rows

    ready = [r for r in rows if r.status == "Ready to generate"]
    cards = [r for r in rows if r.status in ("Ready to generate", "Has description")]
    skipped = [r for r in rows if r.status not in ("Ready to generate", "Has description")]
    status_counts = {status: sum(1 for r in rows if r.status == status) for status in sorted({r.status for r in rows})}
    if status_counts:
        st.markdown(" · ".join(f"**{k}:** {v}" for k, v in status_counts.items()))

    if skipped or not_eligible_skus:
        with st.expander(f"Skipped ({len(skipped)}) — not found / not eligible", expanded=not cards):
            if skipped:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "SKU": r.product.sku,
                            "Title": r.product.title,
                            "Status": r.status,
                            "Warnings": "; ".join(r.warnings),
                            "Admin": r.product.admin_url,
                        }
                        for r in skipped
                    ]),
                    hide_index=True,
                    width="stretch",
                    column_config={"Admin": st.column_config.LinkColumn("Admin", display_text="open")},
                )
            if not_eligible_skus:
                st.multiselect(
                    "Wrongly marked not eligible? Force-include as bag/accessory:",
                    not_eligible_skus,
                    key="bda_force_eligible",
                )

    if not cards:
        st.info("No listings to work on (needs a bag/accessory with at least one photo).")
        return

    st.markdown("### Listings")
    st.caption(
        "First drafts generate automatically — dimensions come from a web search of same-model "
        "listings, condition is the AI's best guess from all listing photos. Edit the fields, "
        "flag anything doubtful, and approve when the preview looks right. Listings that "
        "already have a description keep it unless you tick **Re-prompt**."
    )

    drafts: dict = st.session_state.setdefault("bda_drafts", {})
    pushed: set = st.session_state.setdefault("bda_pushed", set())
    st.session_state.setdefault("bda_sources", {})
    st.session_state.setdefault("bda_warnings", {})

    def _wants_generation(row) -> bool:
        sku = row.product.sku
        if sku in drafts or sku in pushed:
            return False
        if row.status == "Has description":
            return bool(st.session_state.get(f"bda_reprompt::{sku}"))
        return True

    attempted: set = st.session_state.setdefault("bda_gen_attempted", set())
    gen_errors: dict = st.session_state.setdefault("bda_gen_errors", {})

    _UNSEARCHED = object()

    def _gen_inputs(p):
        """Snapshot per-SKU inputs from session state on the main thread —
        worker threads must not touch st.session_state."""
        dims_val = (st.session_state.get(f"bda_dims::{p.sku}") or "").strip()
        material_val = st.session_state.get(f"bda_material::{p.sku}", "")
        sources = [s.strip() for s in (st.session_state.get(f"bda_sources_input::{p.sku}") or "").splitlines() if s.strip()]
        sugg_key = f"bda_dim_suggestion::{p.sku}"
        cached = st.session_state[sugg_key] if sugg_key in st.session_state else _UNSEARCHED
        return dims_val, material_val, sources, cached

    def _gen_worker(p, dims_val, material_val, sources, cached_sugg):
        """Pure network work: dimension web-search (unless cached) + draft."""
        sugg = None if cached_sugg is _UNSEARCHED else cached_sugg
        search_err = None
        dims_from_search = False
        if not dims_val and cached_sugg is _UNSEARCHED:
            first_img = next((u for u in (p.image_urls or [p.image_url]) if u), "")
            try:
                sugg = da.suggest_dimensions_via_web_search(title=p.title, image_url=first_img)
            except Exception as e:  # noqa: BLE001 — search is best-effort, not fatal
                sugg, search_err = None, str(e)
        if not dims_val and sugg and sugg.dimensions:
            dims_val = sugg.dimensions
            sources = sources + [u for u in sugg.sources if u not in sources]
            dims_from_search = True
        draft = da.generate_description_draft(
            title=p.title,
            image_urls=[u for u in (p.image_urls or [p.image_url]) if u][: da.MAX_GENERATION_IMAGES],
            verified_dimensions=dims_val,
            verified_material=material_val,
            sources=sources,
        )
        return {"draft": draft, "sugg": sugg, "sources": sources,
                "search_err": search_err, "dims_from_search": dims_from_search}

    def _apply_result(p, res) -> None:
        drafts[p.sku] = res["draft"]
        st.session_state["bda_sources"][p.sku] = res["sources"]
        if res["sugg"] is not None or res["search_err"] is not None:
            st.session_state[f"bda_dim_suggestion::{p.sku}"] = res["sugg"]
        warns = list(res["draft"].warnings)
        if res["search_err"]:
            warns.append(f"dimension search failed: {res['search_err']}")
        elif res["dims_from_search"]:
            warns.append(f"dimensions via web search ({res['sugg'].confidence} confidence) — verify sources")
        st.session_state["bda_warnings"][p.sku] = warns
        st.session_state.pop(f"bda_prefilled::{p.sku}", None)
        gen_errors.pop(p.sku, None)

    for sku, msg in gen_errors.items():
        st.error(f"Generation failed for {sku}: {msg} — use ↻ Regenerate on the card to retry.")

    # Auto-generate in parallel batches; finished cards appear (and stay
    # interactive) after each batch while the rest of the queue continues.
    GEN_BATCH = 5
    ungenerated = [r for r in cards if _wants_generation(r) and r.product.sku not in attempted]
    if ungenerated:
        batch = [r.product for r in ungenerated[:GEN_BATCH]]
        done = sum(1 for r in cards if r.product.sku in drafts or r.product.sku in pushed)
        st.progress(
            done / max(done + len(ungenerated), 1),
            text=f"Generating {len(batch)} draft(s) in parallel — {len(ungenerated)} remaining…",
        )
        inputs = {p.sku: _gen_inputs(p) for p in batch}
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futures = {p.sku: ex.submit(_gen_worker, p, *inputs[p.sku]) for p in batch}
        for p in batch:
            try:
                _apply_result(p, futures[p.sku].result())
            except Exception as e:  # noqa: BLE001 — surfaced above the cards
                gen_errors[p.sku] = str(e)
            attempted.add(p.sku)
        st.rerun()

    approved: list[tuple[da.DropAuditProduct, str]] = []

    COL_SPEC = [1.0, 1.4, 1.5, 0.55]
    header_cols = st.columns(COL_SPEC, gap="medium")
    for hc, label in zip(header_cols, ("Photos", "Dimensions & condition", "Description preview", "Approve")):
        with hc:
            st.markdown(f"**{label}**")

    for r in cards:
        p = r.product
        sku = p.sku
        has_desc = r.status == "Has description"
        draft = drafts.get(sku)
        gen_images = (p.image_urls or [p.image_url])[: da.MAX_GENERATION_IMAGES]
        gen_images = [u for u in gen_images if u]

        if sku in pushed:
            with st.container(border=True):
                st.markdown(f"✅ **{sku}** · {p.title} — pushed to Shopify")
            continue

        # Prefill the editable fields from a fresh draft. Must happen before
        # the widgets below are instantiated for this run.
        if draft and not st.session_state.get(f"bda_prefilled::{sku}"):
            gen_dims = (draft.dimensions or "").strip()
            if gen_dims and gen_dims.lower() != "needs review":
                st.session_state[f"bda_dims::{sku}"] = gen_dims
            st.session_state[f"bda_cond::{sku}"] = (draft.condition_notes or "").strip()
            gen_material = (draft.material or "").strip()
            st.session_state[f"bda_material::{sku}"] = "" if gen_material.lower() == "needs review" else gen_material
            st.session_state[f"bda_details::{sku}"] = "\n".join(
                da.clean_detail_line(d) for d in draft.details[:4] if da.clean_detail_line(d)
            )
            if draft.sources and not (st.session_state.get(f"bda_sources_input::{sku}") or "").strip():
                st.session_state[f"bda_sources_input::{sku}"] = "\n".join(draft.sources)
            st.session_state[f"bda_prefilled::{sku}"] = True

        with st.container(border=True):
            price = f" — ${p.price}" if p.price else ""
            admin = f" · [open in Shopify]({p.admin_url})" if p.admin_url else ""
            status_note = f" · {p.status}" if p.status and p.status != "ACTIVE" else ""
            desc_note = " · has description" if has_desc else ""
            flag_note = " · 🚩 flagged" if st.session_state.get(f"bda_flag::{sku}") else ""
            st.markdown(f"**{sku}** · {p.title}{price}{status_note}{desc_note}{admin}{flag_note}")

            if has_desc:
                with st.expander("Current description — kept unless you approve a replacement"):
                    st.markdown(p.description_html or "", unsafe_allow_html=True)
                reprompt = st.checkbox(
                    "Re-prompt — generate a replacement (overwrites the current description only when you approve and push)",
                    key=f"bda_reprompt::{sku}",
                    disabled=not gen_images,
                    help=None if gen_images else "No photos on this listing — can't generate.",
                )
                if not reprompt:
                    continue

            img_col, field_col, prev_col, appr_col = st.columns(COL_SPEC, gap="medium")

            with img_col:
                if gen_images:
                    st.image(da.shopify_image_url(gen_images[0], 640), use_container_width=True)
                    extra = gen_images[1:5]
                    if extra:
                        for tc, u in zip(st.columns(len(extra)), extra):
                            with tc:
                                st.image(da.shopify_image_url(u, 240), use_container_width=True)
                st.caption(f"{len(gen_images)} photo(s) used for condition")

            with field_col:
                dims = st.text_input("Dimensions", key=f"bda_dims::{sku}", placeholder='10" L x 3" W x 6" H')
                suggestion = st.session_state.get(f"bda_dim_suggestion::{sku}")
                if suggestion is not None:
                    if suggestion.dimensions:
                        hosts = ", ".join(sorted(
                            {urlparse(u).netloc.removeprefix("www.") for u in suggestion.sources[:5]} - {""}
                        ))
                        if suggestion.dimensions == dims.strip():
                            st.caption(f"✓ Web search ({suggestion.confidence} confidence){' · ' + hosts if hosts else ''}")
                        else:

                            def _apply_dims(sku=sku, s=suggestion):
                                st.session_state[f"bda_dims::{sku}"] = s.dimensions
                                existing = st.session_state.get(f"bda_sources_input::{sku}", "")
                                merged = [u for u in existing.splitlines() if u.strip()]
                                merged += [u for u in s.sources if u not in merged]
                                st.session_state[f"bda_sources_input::{sku}"] = "\n".join(merged)

                            st.button(
                                f'Use web result: {suggestion.dimensions} ({suggestion.confidence})',
                                key=f"bda_dimapply::{sku}",
                                on_click=_apply_dims,
                            )
                    else:
                        st.caption("Web search found no same-model dimensions — try 🔎 Reverse image search below, then type them in.")

                cond = st.text_input(
                    "Condition",
                    key=f"bda_cond::{sku}",
                    placeholder="8/10 – Light wear. Interior clean.",
                )
                material = st.text_input(
                    "Material",
                    key=f"bda_material::{sku}",
                    placeholder="Leather, Fabric Lining, Gold-Tone Hardware",
                )
                details_text = st.text_area(
                    "Details — one bullet per line",
                    key=f"bda_details::{sku}",
                    height=100,
                )
                st.text_area(
                    "Sources — URLs, one per line",
                    key=f"bda_sources_input::{sku}",
                    height=68,
                    placeholder="Auto-filled from the dimension search; add exact-match URLs.",
                )
                link_col1, link_col2 = st.columns(2)
                with link_col1:
                    if gen_images:
                        st.link_button(
                            "🔎 Reverse image search",
                            da.reverse_image_search_url(da.shopify_image_url(gen_images[0], 800)),
                            width="stretch",
                            help="Opens Google Lens in a new tab with this listing's photo — same-model listings, any colorway.",
                        )
                with link_col2:
                    st.link_button(
                        "🔍 Listings by title",
                        da.title_listing_search_url(p.title),
                        width="stretch",
                        help="Opens a Google search for this title + dimensions in a new tab.",
                    )
                regen_clicked = st.button(
                    "↻ Regenerate" if draft else "Generate",
                    key=f"bda_generate::{sku}",
                    width="stretch",
                )
                if regen_clicked:
                    try:
                        with st.spinner(f"Generating {sku}…"):
                            res = _gen_worker(p, *_gen_inputs(p))
                        _apply_result(p, res)
                        attempted.add(sku)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001 — show API/image errors to user
                        gen_errors[sku] = str(e)
                        st.error(f"Generation failed for {sku}: {e}")

            with prev_col:
                if not draft:
                    st.info("No draft yet — generate one to preview the description.")
                else:
                    final = da.DescriptionDraft(
                        dimensions=dims.strip() or None,
                        details=[ln for ln in details_text.splitlines() if ln.strip()],
                        material=material.strip(),
                        condition_notes=cond.strip(),
                    )
                    rendered = da.render_shopify_description(final)
                    audit = da.audit_generated_description(rendered)
                    warnings = st.session_state.get("bda_warnings", {}).get(sku, [])
                    if warnings:
                        st.warning("; ".join(warnings))
                    st.markdown(da.shopify_description_html(rendered), unsafe_allow_html=True)
                    if not audit.passed:
                        st.error("Template check: " + "; ".join(audit.issues))

            with appr_col:
                if not draft:
                    st.caption("—")
                else:
                    flagged = bool(st.session_state.get(f"bda_flag::{sku}"))
                    if flagged and st.session_state.get(f"bda_approve::{sku}"):
                        st.session_state[f"bda_approve::{sku}"] = False
                    approve = st.checkbox(
                        "Approve",
                        key=f"bda_approve::{sku}",
                        disabled=(not audit.passed) or flagged,
                        help="Approve this description for the Shopify push.",
                    )
                    if approve and audit.passed and not flagged:
                        approved.append((p, rendered))
                    st.checkbox(
                        "🚩 Flag",
                        key=f"bda_flag::{sku}",
                        help="Flag for another look; flagged listings can't be approved.",
                    )

    st.divider()
    if not approved:
        st.info("No approved descriptions to push yet.")
        return

    overwrites = sum(1 for p, _ in approved if p.has_description)
    st.warning(
        f"This will update **{len(approved)}** live Shopify product description(s)"
        + (f", including **{overwrites}** overwrite(s) of existing descriptions you opted to re-prompt" if overwrites else "")
        + ". It will not touch price, inventory, customers, or titles."
    )
    if st.button(f"Push {len(approved)} approved description(s) to Shopify", type="primary", key="bda_push"):
        from shopify_push import update_product_body_html
        import snapshots as _snap

        pids = [p.product_id for p, _ in approved if p.product_id]
        if pids:
            with st.spinner(f"Snapshotting {len(pids)} products for Undo…"):
                sc, sp = _snap.create_snapshot(pids, label="bulk_drop_audit", kind="pre_apply")
            if sc > 0:
                st.session_state["undo_snapshot_path"] = str(sp)

        succeeded = 0
        failed: list[tuple[str, int, dict]] = []
        log_path = BASE / "logs" / "bulk_drop_audit_writes.jsonl"
        for p, desc in approved:
            if not p.product_id:
                failed.append((p.title or p.sku, 0, {"error": "missing Shopify product ID"}))
                continue
            if p.has_description and not st.session_state.get(f"bda_reprompt::{p.sku}"):
                failed.append((p.title or p.sku, 0, {"error": "has an existing description and re-prompt was not enabled"}))
                continue
            html_body = da.shopify_description_html(desc)
            status, resp = update_product_body_html(p.product_id, html_body)
            if status == 200:
                succeeded += 1
                da.append_audit_log(
                    log_path,
                    product=p,
                    description=desc,
                    sources=st.session_state.get("bda_sources", {}).get(p.sku, []),
                    warnings=st.session_state.get("bda_warnings", {}).get(p.sku, []),
                )
                drafts.pop(p.sku, None)
                pushed.add(p.sku)
            else:
                failed.append((p.title or p.sku, status, resp))
        if succeeded:
            st.success(f"Updated {succeeded} listing(s).")
        if failed:
            st.error(f"{len(failed)} failure(s):")
            for title, status, resp in failed[:10]:
                st.markdown(f"- **{title}** — HTTP {status} — `{resp}`")



def render_bulk_measurements_tab() -> None:
    """Upload a measurements CSV → match products by barcode/SKU → fill the
    garment's copy template → write the rendered description to the live listing.

    Pure logic lives in bulk_measurements.py; this is just the Streamlit shell:
    parse → map columns → dry-run preview → explicit push (snapshotted for Undo).
    """
    import bulk_measurements as bm
    from heuristics.loader import load_description_templates
    from shopify_inventory import is_configured

    st.subheader("Shopify bulk editor — measurements → descriptions")
    st.caption(
        "Upload a measurements sheet keyed by **barcode**. Each row is matched to "
        "a live product, its numbers are dropped into the matching copy template, "
        "and the rendered description is written to the listing. Listings are "
        "expected to have **empty** descriptions — existing ones are skipped "
        "unless you allow overwrite."
    )

    if not is_configured():
        st.warning(
            "Shopify isn't configured. Connect it in the **Shopify audit** tab first."
        )
        return

    up = st.file_uploader("Measurements CSV", type=["csv"], key="bm_csv")
    if up is None:
        return
    name = up.name

    try:
        headers, rows = bm.parse_measurement_csv(up.getvalue())
    except Exception as e:  # noqa: BLE001 — surface any parse failure to the UI
        st.error(f"Couldn't parse CSV: {e}")
        return
    if not rows:
        st.info("No data rows found in the CSV.")
        return

    st.markdown(
        f"**{len(rows)} rows** · columns: "
        + ", ".join((repr(h) if h == "" else f"`{h}`") for h in headers)
    )

    # ── Column mapping ────────────────────────────────────────────────────
    use_claude = st.checkbox(
        "Use Claude (Haiku) to map columns", value=False,
        help="Off = deterministic alias matching (works offline). On = one Haiku "
             "call to handle unusual headers. You can override either way below.",
    )
    map_key = f"bm_map::{name}::{use_claude}"
    if map_key not in st.session_state:
        with st.spinner("Resolving columns…"):
            st.session_state[map_key] = bm.map_columns(
                headers, rows[:3], use_claude=use_claude
            )
    cm = st.session_state[map_key]

    opts = ["—"] + list(headers)

    def _fmt(h: str) -> str:
        if h == "—":
            return "(none)"
        return "(unnamed col)" if h == "" else h

    def _sel(label: str, current) -> Optional[str]:
        cur = current if (current is not None and current in opts) else "—"
        choice = st.selectbox(
            label, opts, index=opts.index(cur),
            key=f"bm_sel::{label}::{name}", format_func=_fmt,
        )
        return None if choice == "—" else choice

    with st.expander("Column mapping", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            cm.title_col = _sel("Title", cm.title_col)
            cm.barcode_col = _sel("Barcode", cm.barcode_col)
            cm.sku_col = _sel("SKU (fallback)", cm.sku_col)
        with c2:
            cm.tagged_size_col = _sel("Tagged size", cm.tagged_size_col)
            cm.notes_col = _sel("Condition notes", cm.notes_col)
        if cm.measurement_map:
            st.caption("Measurement columns → template fields:")
            st.dataframe(
                pd.DataFrame(
                    [{"CSV column": k, "Field": v} for k, v in cm.measurement_map.items()]
                ),
                hide_index=True, width="stretch",
            )
        else:
            st.warning("No measurement columns detected.")

    ok, reason = cm.is_usable()
    if not ok:
        st.error(f"Can't proceed: {reason}.")
        return

    o1, o2 = st.columns([1, 2])
    with o1:
        unit_label = st.selectbox(
            "Measurement unit", ['inches (")', "in", "cm", "(none)"], index=0,
            help="Appended to bare numeric measurements. Non-numeric values are left as-is.",
        )
        unit = {'inches (")': '"', "in": " in", "cm": " cm", "(none)": ""}[unit_label]
    with o2:
        overwrite = st.checkbox(
            "Overwrite listings that already have a description", value=False,
            help="Off (default) skips any product whose description isn't empty.",
        )
        ignore_audit = st.checkbox(
            "Push rows that fail the template audit", value=False,
            help="Off (default) blocks any rendered description that's missing a "
                 "required section, trips a banned phrase, or is out of length "
                 "bounds. The reason shows in the preview's “Why” column.",
        )

    plans_key = f"bm_plans::{name}"

    if st.button("Match & preview (dry run)", type="primary", width="stretch"):
        templates = load_description_templates()
        progress = st.progress(0.0, text="Matching products…")
        plans: list = []

        # plan_rows does the lookups; run it row-by-row here so we can show
        # progress over a 100+ row sheet rather than a silent 30s spinner.
        for i, raw in enumerate(rows):
            sub = bm.plan_rows(
                headers, [raw], cm, templates=templates,
                unit=unit, overwrite_existing=overwrite, ignore_audit=ignore_audit,
            )
            plans.extend(sub)
            progress.progress((i + 1) / len(rows), text=f"Matched {i+1}/{len(rows)}")
        progress.empty()
        st.session_state[plans_key] = plans

    plans = st.session_state.get(plans_key)
    if not plans:
        return

    # ── Dry-run preview ───────────────────────────────────────────────────
    from collections import Counter
    counts = Counter(p.action for p in plans)
    writeable = [p for p in plans if p.will_write]

    st.markdown("### Preview")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Will write", counts.get("write", 0))
    m2.metric("Not found", counts.get("skip-not-found", 0))
    m3.metric("Has description", counts.get("skip-has-desc", 0))
    m4.metric("Failed audit / other", counts.get("skip-failed-audit", 0)
              + counts.get("skip-no-template", 0)
              + counts.get("skip-collision", 0))

    preview_df = pd.DataFrame([
        {
            "Row": p.row_index + 1,
            "Title": p.title,
            "Barcode": p.barcode,
            "Status": "matched" if p.lookup.found else "not found",
            "Template": p.template_name or "—",
            "Measurements": ", ".join(f"{k} {v}" for k, v in p.measurements.items()) or "—",
            "Action": p.action,
            "Why": p.reason,
        }
        for p in plans
    ])
    st.dataframe(preview_df, hide_index=True, width="stretch")

    # ── Needs manual attention: not-found + failed-audit ──────────────────
    # Always shown (even when nothing is writeable) so the user has a worklist
    # to fix by hand. Matched-but-skipped rows get a deep link into the admin.
    followup = [
        p for p in plans
        if p.action in ("skip-not-found", "skip-failed-audit",
                        "skip-no-template", "skip-collision")
    ]
    if followup:
        from shopify_inventory import get_shop
        shop = get_shop() or ""
        st.markdown("### Needs manual attention")
        st.caption(
            "Not written — go in by hand. **failed-audit** rendered a description "
            "that tripped the template contract; **not-found** had no barcode/SKU "
            "match; **no-template** couldn't be routed to a garment type."
        )
        followup_df = pd.DataFrame([
            {
                "Row": p.row_index + 1,
                "Title": p.title,
                "Barcode": p.barcode,
                "Issue": p.action.replace("skip-", ""),
                "Why": p.reason,
                "Admin": (f"https://{shop}/admin/products/{p.lookup.product_id}"
                          if (shop and p.lookup.found and p.lookup.product_id) else ""),
            }
            for p in followup
        ])
        st.dataframe(
            followup_df, hide_index=True, width="stretch",
            column_config={
                "Admin": st.column_config.LinkColumn("Admin", display_text="open"),
            },
        )

    # ── Push ──────────────────────────────────────────────────────────────
    if not writeable:
        st.info("Nothing to write with the current settings.")
        return

    # ── Review, edit & delete before pushing ──────────────────────────────
    # Only body_html is ever written — the Shopify title is left untouched, and
    # the CSV title is internal (routing + display) only. "Before" pulls each
    # listing's current description so collisions can be debugged inline;
    # "After (HTML)" is editable; untick "Keep" to drop a row from the push.
    st.markdown("### Rows to write")
    st.caption(
        "Edit the rendered HTML inline, or untick **Keep** to skip a row. The "
        "Shopify **title is preserved** — only the description is written."
    )
    editor_df = pd.DataFrame([
        {
            "Keep": True,
            "Row": p.row_index + 1,
            "Shopify title": p.lookup.title or "(kept as-is)",
            "Barcode": p.barcode,
            "Template": p.template_name or "—",
            "Before": bm._strip_html(p.lookup.description_html).strip() or "(empty)",
            "After (HTML)": p.body_html,
        }
        for p in writeable
    ])
    edited = st.data_editor(
        editor_df, hide_index=True, width="stretch", key=f"bm_edit::{name}",
        column_config={
            "Keep": st.column_config.CheckboxColumn(
                "Keep", help="Untick to skip this row on push", default=True),
            "Row": st.column_config.NumberColumn("Row", disabled=True),
            "Shopify title": st.column_config.TextColumn(
                "Shopify title", disabled=True,
                help="Left unchanged — only the description is written"),
            "Barcode": st.column_config.TextColumn("Barcode", disabled=True),
            "Template": st.column_config.TextColumn("Template", disabled=True),
            "Before": st.column_config.TextColumn(
                "Before (current desc)", disabled=True, width="medium"),
            "After (HTML)": st.column_config.TextColumn(
                "After (HTML — editable)", width="large"),
        },
    )
    # Fold inline edits + deletions back onto the plans, in row order.
    kept: list = []
    for p, (_, erow) in zip(writeable, edited.iterrows()):
        if not bool(erow["Keep"]):
            continue
        p.body_html = str(erow["After (HTML)"])
        kept.append(p)
    writeable = kept

    if writeable:
        with st.expander(f"Rendered preview ({len(writeable)})"):
            for p in writeable[:50]:
                st.markdown(
                    f"**{p.lookup.title or p.title}** · {p.template_name} "
                    f"· barcode {p.barcode}"
                )
                if p.lookup.description_html.strip():
                    bcol, acol = st.columns(2)
                    with bcol:
                        st.caption("Before")
                        st.html(p.lookup.description_html)
                    with acol:
                        st.caption("After")
                        st.html(p.body_html)
                else:
                    try:
                        st.html(p.body_html)
                    except Exception:
                        st.markdown(p.body_html, unsafe_allow_html=True)
                st.divider()

    if not writeable:
        st.info("All rows unticked — nothing to push.")
        return

    st.divider()
    st.warning(
        f"This writes **{len(writeable)}** descriptions to the **live** Shopify "
        "catalogue. A snapshot is taken first so you can Undo."
    )
    if st.button(f"Push {len(writeable)} description(s) to Shopify",
                 type="primary", width="stretch", key="bm_push"):
        from shopify_push import update_product_body_html
        import snapshots as _snap

        pids = [p.lookup.product_id for p in writeable]
        with st.spinner(f"Snapshotting {len(pids)} products for Undo…"):
            sc, sp = _snap.create_snapshot(
                pids, label="bulk_measurements", kind="pre_apply"
            )
        if sc > 0:
            st.session_state["undo_snapshot_path"] = str(sp)

        succeeded = 0
        failed: list[tuple[str, int, dict]] = []
        progress = st.progress(0.0, text=f"Writing {len(writeable)}…")
        for i, p in enumerate(writeable):
            status, resp = update_product_body_html(p.lookup.product_id, p.body_html)
            if status == 200:
                succeeded += 1
            else:
                failed.append((p.title, status, resp))
            progress.progress((i + 1) / len(writeable), text=f"{i+1}/{len(writeable)} done")
        progress.empty()

        if succeeded:
            st.success(f"Updated {succeeded} listing(s).")
            # Drop applied rows from the cached preview so they don't re-show.
            done_ids = {p.lookup.product_id for p in writeable
                        if p.title not in {t for t, _, _ in failed}}
            st.session_state[plans_key] = [
                pl for pl in plans if pl.lookup.product_id not in done_ids
            ]
        if failed:
            st.error(f"{len(failed)} failure(s):")
            for title, status, resp in failed[:10]:
                st.markdown(f"- **{title}** — HTTP {status} — `{resp}`")


def render_bulk_sku_editor_tab() -> None:
    """Paste SKUs → edit title/price/tags/status → approved in-app Shopify push."""
    import bulk_sku_editor as bse
    from shopify_inventory import get_shop, is_configured

    st.subheader("SKU bulk editor — title, price, tags, status")
    st.caption(
        "Paste SKUs separated by spaces. Commas and line breaks also work. The tool "
        "pulls live Shopify rows, lets you edit selected fields, then pushes only "
        "approved changed rows after a snapshot."
    )
    st.info(
        "Writes are limited to product title, variant price, product tags, and "
        "product status. This does not touch images, descriptions, inventory, vendor, "
        "customers, orders, or handles."
    )

    if not is_configured():
        st.warning(
            "Shopify isn't configured. Connect it in the **Shopify audit** tab first."
        )
        return

    raw = st.text_area(
        "SKUs",
        key="bse_skus_raw",
        height=120,
        placeholder="ABC123 DEF456 GHI789",
        help="Primary delimiter is a space. Newlines and commas are accepted too.",
    )
    c1, c2 = st.columns([1, 3])
    with c1:
        search_clicked = st.button(
            "Search Shopify",
            type="primary",
            width="stretch",
            disabled=not raw.strip(),
            key="bse_search",
        )
    with c2:
        st.caption("Exact variant SKU match. Input order is preserved.")

    if search_clicked:
        parsed = bse.parse_sku_terms(raw)
        if not parsed.terms:
            st.warning("Paste at least one SKU.")
            return
        shop = get_shop() or ""
        with st.spinner(f"Searching Shopify for {len(parsed.terms)} SKU(s)…"):
            plan = bse.build_update_plan(
                parsed.terms,
                lambda terms: bse.lookup_products_by_skus(terms, shop=shop),
                duplicates=parsed.duplicates,
            )
        st.session_state["bse_lookup_plan"] = plan

    plan = st.session_state.get("bse_lookup_plan")
    if not plan:
        return

    if plan.duplicates:
        st.warning("Duplicate SKU(s) ignored: " + ", ".join(plan.duplicates))
    if plan.not_found:
        st.error("Not found: " + ", ".join(plan.not_found))
    if plan.collisions:
        st.error(
            "Blocked SKU collision(s), more than one product matched: "
            + ", ".join(plan.collisions)
        )
    if not plan.records:
        st.info("No editable products found for that SKU list.")
        return

    st.markdown("### Edit rows")
    st.caption(
        "Leave a field unchanged to skip that field. Untick **Keep** to remove a "
        "row from the push."
    )
    editor_df = pd.DataFrame([
        {
            "Keep": True,
            "SKU": r.sku,
            "Product ID": r.product_id,
            "Variant ID": r.variant_id,
            "Current title": r.title,
            "Current price": r.price,
            "Current tags": r.tags,
            "Current status": r.status,
            "New title": r.title,
            "New price": r.price,
            "New tags": r.tags,
            "New status": r.status,
            "Admin": r.admin_url,
        }
        for r in plan.records
    ])
    edited = st.data_editor(
        editor_df,
        hide_index=True,
        width="stretch",
        key="bse_editor",
        column_config={
            "Keep": st.column_config.CheckboxColumn("Keep", default=True),
            "SKU": st.column_config.TextColumn("SKU", disabled=True),
            "Product ID": st.column_config.NumberColumn("Product ID", disabled=True, format="%d"),
            "Variant ID": st.column_config.NumberColumn("Variant ID", disabled=True, format="%d"),
            "Current title": st.column_config.TextColumn("Current title", disabled=True, width="medium"),
            "Current price": st.column_config.TextColumn("Current price", disabled=True),
            "Current tags": st.column_config.TextColumn("Current tags", disabled=True, width="medium"),
            "Current status": st.column_config.TextColumn("Current status", disabled=True),
            "New title": st.column_config.TextColumn("New title", width="medium"),
            "New price": st.column_config.TextColumn("New price"),
            "New tags": st.column_config.TextColumn("New tags", width="medium"),
            "New status": st.column_config.SelectboxColumn(
                "New status", options=["draft", "active", "archived"], required=True,
            ),
            "Admin": st.column_config.LinkColumn("Admin", display_text="open"),
        },
    )

    update_plans, errors = bse.rows_to_apply(edited.to_dict("records"))
    if errors:
        st.error("Fix these rows before pushing:")
        for err in errors[:20]:
            st.markdown(f"- {err}")
        return

    st.markdown("### Push preview")
    m1, m2, m3 = st.columns(3)
    m1.metric("Matched", len(plan.records))
    m2.metric("Changed rows", len(update_plans))
    m3.metric("Blocked / not found", len(plan.not_found) + len(plan.collisions))

    if not update_plans:
        st.info("No changed rows selected.")
        return

    preview_rows = []
    for p in update_plans:
        preview_rows.append({
            "SKU": p.sku,
            "Product ID": p.product_id,
            "Variant ID": p.variant_id,
            "Product fields": ", ".join(p.product_updates) or "—",
            "Variant fields": ", ".join(p.variant_updates) or "—",
        })
    st.dataframe(pd.DataFrame(preview_rows), hide_index=True, width="stretch")

    st.warning(
        f"This will update **{len(update_plans)}** live Shopify listing(s). "
        "A snapshot is taken first so you can Undo."
    )
    if st.button(
        f"Push {len(update_plans)} selected update(s) to Shopify",
        type="primary",
        width="stretch",
        key="bse_push",
    ):
        import snapshots as _snap

        pids = sorted({p.product_id for p in update_plans})
        with st.spinner(f"Snapshotting {len(pids)} products for Undo…"):
            sc, sp = _snap.create_snapshot(pids, label="bulk_sku_editor", kind="pre_apply")
        if sc > 0:
            st.session_state["undo_snapshot_path"] = str(sp)
        else:
            st.error(f"Snapshot failed: {sp}")
            return

        succeeded = 0
        failed: list[tuple[str, int, dict]] = []
        log_path = BASE / "logs" / "bulk_sku_editor_writes.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        progress = st.progress(0.0, text=f"Writing {len(update_plans)}…")
        for i, p in enumerate(update_plans):
            results = bse.apply_update_plan(p)
            if results and all(r.ok for r in results):
                succeeded += 1
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "sku": p.sku,
                        "product_id": p.product_id,
                        "variant_id": p.variant_id,
                        "product_updates": p.product_updates,
                        "variant_updates": p.variant_updates,
                    }, ensure_ascii=False) + "\n")
            else:
                first = results[0] if results else None
                failed.append((p.sku, first.status if first else 0, first.response if first else {"error": "no update attempted"}))
            progress.progress((i + 1) / len(update_plans), text=f"{i+1}/{len(update_plans)} done")
        progress.empty()

        if succeeded:
            st.success(f"Updated {succeeded} listing(s).")
        if failed:
            st.error(f"{len(failed)} failure(s):")
            for sku, status, resp in failed[:10]:
                st.markdown(f"- **{sku}** — HTTP {status} — `{resp}`")


# ---------------------------------------------------------------------------
# Commercial invoice tab — describe items → customs line items → emailable CSV
# ---------------------------------------------------------------------------

CI_HEADER_PATH = OUTPUT / "commercial_invoice_header.json"

CI_HEADER_DEFAULTS = {
    "supplier_name": "Kosuke Yamamoto",
    "supplier_address": "4-22-35-12 Nango, Chigasaki",
    "supplier_city": "Kanagawa",
    "supplier_country": "Japan",
    "importer_name": "Past Studies",
    "importer_address": "213 N Morgan unit 2G, 60607",
    "importer_city": "Chicago IL",
    "importer_country": "U.S.",
    "country_of_origin": "Japan",
    "currency": "JPY",
}


def _ci_load_header() -> dict:
    """Saved header defaults, falling back to the invoice-83 values.

    Exchange rates are kept per currency so switching JPY ⇄ EUR restores the
    rate last used for that currency.
    """
    vals = dict(CI_HEADER_DEFAULTS)
    vals["rates"] = dict(ci.DEFAULT_RATES)
    if CI_HEADER_PATH.exists():
        try:
            data = json.loads(CI_HEADER_PATH.read_text())
        except Exception:
            data = {}
        legacy_jpy = data.pop("jpy_per_usd", None)  # pre-currency file format
        rates = data.pop("rates", {})
        vals.update(data)
        vals["rates"].update(rates)
        if legacy_jpy and "JPY" not in rates:
            vals["rates"]["JPY"] = float(legacy_jpy)
    return vals


def render_commercial_invoice_tab() -> None:
    """Commercial invoice generator.

    Describe the shipment in plain words; Claude formats customs line items
    (codes kept, brands stripped, HS codes by shop convention); the download
    is a CSV in the manual spreadsheet's exact layout, ready to email.
    """
    saved = _ci_load_header()

    # .ci-form marks this tab's panel so the CSS block can grey out its
    # input boxes without touching the rest of the app.
    st.markdown('<div class="section-label ci-form">Invoice header</div>', unsafe_allow_html=True)
    h1, h2, h3, h4 = st.columns([2, 2, 1, 2])
    with h1:
        inv_number = st.text_input("Invoice number", key="ci_number", placeholder="84")
    with h2:
        inv_date = st.date_input("Invoice date", value=_date.today(), key="ci_date")
    with h3:
        currencies = list(ci.CURRENCIES)
        saved_ccy = saved.get("currency", "JPY")
        currency = st.selectbox(
            "Currency", currencies,
            index=currencies.index(saved_ccy) if saved_ccy in currencies else 0,
            key="ci_currency",
            help="Currency the items were bought in — drives the Unit Value column.",
        )
    with h4:
        rates = dict(ci.DEFAULT_RATES)
        rates.update(saved.get("rates", {}))
        rate = st.number_input(
            f"Exchange rate ({currency} per 1 USD)",
            min_value=0.01,
            step=0.5 if currency == "JPY" else 0.01,
            value=float(rates.get(currency, ci.DEFAULT_RATES[currency])),
            key=f"ci_rate_{currency}",  # per-currency key: switching keeps both rates
            help="Drives the whole USD column. Line totals round to whole dollars.",
        )

    sc, ic = st.columns(2)
    with sc:
        st.markdown("**Supplier information**")
        s_name = st.text_input("Name", value=saved["supplier_name"], key="ci_s_name")
        s_addr = st.text_input("Address", value=saved["supplier_address"], key="ci_s_addr")
        s_city = st.text_input("City", value=saved["supplier_city"], key="ci_s_city")
        s_country = st.text_input("Country", value=saved["supplier_country"], key="ci_s_country")
    with ic:
        st.markdown("**Importer of record**")
        i_name = st.text_input("Company", value=saved["importer_name"], key="ci_i_name")
        i_addr = st.text_input("Address", value=saved["importer_address"], key="ci_i_addr")
        i_city = st.text_input("City", value=saved["importer_city"], key="ci_i_city")
        i_country = st.text_input("Country", value=saved["importer_country"], key="ci_i_country")

    origin = st.text_input("Country of origin", value=saved["country_of_origin"],
                           key="ci_origin")

    # Persist header edits so the next invoice starts from these values.
    current = {
        "supplier_name": s_name, "supplier_address": s_addr,
        "supplier_city": s_city, "supplier_country": s_country,
        "importer_name": i_name, "importer_address": i_addr,
        "importer_city": i_city, "importer_country": i_country,
        "country_of_origin": origin, "currency": currency,
        "rates": {**rates, currency: rate},
    }
    if current != saved:
        CI_HEADER_PATH.write_text(json.dumps(current, indent=2))

    st.divider()
    st.markdown('<div class="section-label">Describe items</div>', unsafe_allow_html=True)
    desc = st.text_area(
        f"One item per line — code, what it is, size if shoes, price per item in {currency}",
        key="ci_describe", height=140,
        placeholder=(
            "06-220 leather shoulder bag 5248\n"
            "16127 black rubber sandals size 37 womens 787\n"
            "B086-6 canvas handbag 1604"
        ),
    )
    if st.button("Generate line items", type="primary", key="ci_generate",
                 disabled=not desc.strip()):
        try:
            with st.spinner("Formatting line items…"):
                client = anthropic.Anthropic(timeout=180.0, max_retries=2)
                items = ci.generate_items(desc, client, currency=currency)
        except Exception as e:
            st.error(f"Generation failed: {e}")
        else:
            st.session_state.setdefault("ci_items", [])
            for item in items:
                row = item.model_dump()
                # Store the full dropdown option so the HS selectbox shows it.
                row["hs_code"] = ci.hs_option_for(row["hs_code"])
                st.session_state["ci_items"].append(row)
            st.session_state["ci_flash"] = f"Added {len(items)} line item(s) — review below."
            st.rerun()

    st.divider()
    if st.session_state.get("ci_flash"):
        st.success(st.session_state.pop("ci_flash"))
    rows = st.session_state.setdefault("ci_items", [])
    st.markdown(
        f'<div class="section-label">Line items ({len(rows)})</div>',
        unsafe_allow_html=True,
    )

    item_cols = ["quantity", "description", "material_content", "hs_code", "unit_value"]
    df = pd.DataFrame(rows, columns=item_cols)
    df.insert(0, "PO #", range(1, len(df) + 1))
    df["total (USD)"] = [
        ci.line_total_usd(ci.CommercialLineItem(**r), rate) for r in rows
    ]
    # Blackship-style product-type dropdown: curated HS6 list, plus any
    # already-entered value not on the list so nothing is silently dropped.
    hs_options = list(ci.HS_OPTIONS)
    hs_options += sorted(
        {r["hs_code"] for r in rows if r["hs_code"] and r["hs_code"] not in hs_options}
    )
    symbol = ci.CURRENCY_SYMBOLS.get(currency, "")
    unit_fmt = f"{symbol}%d" if currency == "JPY" else f"{symbol}%.2f"
    edited = st.data_editor(
        df,
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        key="ci_editor",
        disabled=["PO #", "total (USD)"],
        column_config={
            "PO #": st.column_config.NumberColumn("PO #", width="small"),
            "quantity": st.column_config.NumberColumn("Qty", min_value=1, step=1, width="small"),
            "description": st.column_config.TextColumn("Product Description", width="large"),
            "material_content": st.column_config.TextColumn("Material", width="small"),
            "hs_code": st.column_config.SelectboxColumn(
                "HS code / product type", options=hs_options, width="medium",
                help="HS6 product type printed on the invoice — only the code is exported.",
            ),
            "unit_value": st.column_config.NumberColumn(
                f"Unit Value ({currency})", format=unit_fmt, min_value=0,
                help="Price per individual item — the USD total multiplies by Qty.",
            ),
            "total (USD)": st.column_config.NumberColumn("Total (USD)", format="$%d", width="small"),
        },
    )

    # Write edits straight back; rerun so PO #s and USD totals recompute.
    # Cells in freshly added dynamic rows come back as None/NaN.
    def _cell_num(v, default: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return default if pd.isna(f) else f

    def _cell_str(v) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v)

    new_rows = []
    for r in edited.to_dict("records"):
        new_rows.append({
            "quantity": max(1, int(_cell_num(r.get("quantity"), 1))),
            "description": _cell_str(r.get("description")),
            "material_content": _cell_str(r.get("material_content")),
            "hs_code": _cell_str(r.get("hs_code")),
            "unit_value": _cell_num(r.get("unit_value"), 0.0),
        })
    if new_rows != rows:
        st.session_state["ci_items"] = new_rows
        st.rerun()

    total = sum(ci.line_total_usd(ci.CommercialLineItem(**r), rate) for r in rows)
    st.markdown(f"**Estimated total value of all goods: ${total:,}**")

    b1, b2 = st.columns([1, 4])
    with b1:
        if st.button("Clear all items", key="ci_clear", disabled=not rows):
            st.session_state["ci_items"] = []
            st.rerun()
    with b2:
        if not inv_number.strip():
            st.caption("Enter an invoice number to enable the CSV download.")
        elif not rows:
            st.caption("Generate or add line items to enable the CSV download.")
        else:
            invoice = ci.CommercialInvoice(
                invoice_number=inv_number.strip(),
                invoice_date=inv_date.isoformat(),
                supplier=ci.CommercialParty(
                    name=s_name, address=s_addr, city=s_city, country=s_country),
                importer=ci.CommercialParty(
                    name=i_name, address=i_addr, city=i_city, country=i_country),
                country_of_origin=origin,
                currency=currency,
                rate_per_usd=rate,
                items=[ci.CommercialLineItem(**r) for r in rows],
            )
            st.download_button(
                "Download invoice CSV",
                data=ci.to_invoice_csv(invoice),
                file_name=f"commercial-invoice-{inv_number.strip()}.csv",
                mime="text/csv",
                type="primary",
                key="ci_download",
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

render_header()

# Top-level tabs: keep the homepage focused on invoice work. Knowledge tools
# (rules, notes), Shopify catalogue tools, and pricing-table inspection all
# get their own tabs so they're accessible without picking an invoice first.
home_tab, commercial_tab, catalogue_tab, drop_audit_tab, bulk_tab, sku_bulk_tab, copy_tab, pricing_tab, knowledge_tab = st.tabs([
    "Invoices",
    "Commercial invoice",
    "Shopify audit",
    "Bulk Drop Audit",
    "Shopify bulk editor",
    "SKU bulk editor",
    "Copy formats",
    "Pricing",
    "Notes & rules",
])

with commercial_tab:
    render_commercial_invoice_tab()

with catalogue_tab:
    render_shopify_catalogue_tab()

with drop_audit_tab:
    render_bulk_drop_audit_tab()

with bulk_tab:
    render_bulk_measurements_tab()

with sku_bulk_tab:
    render_bulk_sku_editor_tab()

with copy_tab:
    render_copy_formats_tab()

with pricing_tab:
    render_pricing_tab()

with knowledge_tab:
    render_quick_note_form()
    render_rules_tab()

with home_tab:
    uploaded, picked = render_source_picker()

    invoice_data = None
    source_label = ""

    if uploaded is not None:
        is_csv = Path(uploaded.name).suffix.lower() == ".csv"

        if is_csv:
            # CSV gets a preview-and-edit step so the user can drop rows
            # before they become invoice items. Skip set is held in session
            # state under a key tied to the filename so toggling a row
            # doesn't bounce on every rerun.
            confirm_key = f"csv_import_confirmed::{uploaded.name}"
            skip_key = f"csv_skip_indices::{uploaded.name}"

            if not st.session_state.get(confirm_key):
                from csv_ingest import preview_csv_rows
                # Write to a temp path so the parser can use its existing
                # Path-based API; cached_transcribe will re-write later.
                tmp_path = INPUTS / uploaded.name
                tmp_path.write_bytes(uploaded.getvalue())
                try:
                    preview = preview_csv_rows(tmp_path)
                except ValueError as e:
                    st.error(f"Couldn't preview CSV: {e}")
                    st.stop()

                st.markdown(f"### CSV preview — {len(preview)} rows")
                st.caption(
                    "Uncheck **Keep** to skip a row. Only kept rows become "
                    "invoice items when you hit **Import**. Defaults to all-kept."
                )

                # Pre-fill the skip set from session_state so toggles stick
                prior_skip = st.session_state.get(skip_key, set())
                rows_df = pd.DataFrame([
                    {
                        "Keep": (r["row_index"] not in prior_skip),
                        "Row #": r["row_index"] + 1,
                        "Title": r["title"],
                        "Vendor": r["vendor"] or "(none)",
                        "Cost": r["cost"],
                        "Qty": r["qty"],
                        "Tags": r["tags"],
                        "_row_index": r["row_index"],
                    }
                    for r in preview
                ])
                edited = st.data_editor(
                    rows_df,
                    hide_index=True,
                    width="stretch",
                    key=f"csv_preview_editor::{uploaded.name}",
                    column_config={
                        "Keep": st.column_config.CheckboxColumn("Keep", default=True),
                        "Row #": st.column_config.NumberColumn(
                            "Row #", disabled=True, format="%d",
                        ),
                        "Title": st.column_config.TextColumn(
                            "Title", disabled=True, width="medium",
                        ),
                        "Vendor": st.column_config.TextColumn("Vendor", disabled=True),
                        "Cost": st.column_config.NumberColumn(
                            "Cost", disabled=True, format="$%.2f",
                        ),
                        "Qty": st.column_config.NumberColumn(
                            "Qty", disabled=True, format="%d",
                        ),
                        "Tags": st.column_config.TextColumn("Tags", disabled=True),
                        "_row_index": st.column_config.NumberColumn(
                            "_row_index", disabled=True, format="%d",
                        ),
                    },
                )

                keep_count = int(edited["Keep"].sum())
                skip_count = len(preview) - keep_count

                bc1, bc2, bc3 = st.columns([2, 1, 1])
                with bc1:
                    st.caption(
                        f"**{keep_count}** kept · **{skip_count}** skipped"
                    )
                with bc2:
                    if st.button(
                        f"Import {keep_count} rows",
                        key=f"csv_import_btn::{uploaded.name}",
                        type="primary",
                        width="stretch",
                        disabled=(keep_count == 0),
                    ):
                        skip_set = {
                            int(row["_row_index"])
                            for _, row in edited.iterrows()
                            if not row["Keep"]
                        }
                        st.session_state[skip_key] = skip_set
                        st.session_state[confirm_key] = True
                        st.rerun()
                with bc3:
                    if st.button(
                        "Cancel",
                        key=f"csv_cancel_btn::{uploaded.name}",
                        width="stretch",
                        type="tertiary",
                    ):
                        st.session_state.pop(skip_key, None)
                        st.session_state.pop(confirm_key, None)
                        st.stop()

                # Don't render anything below — user hasn't confirmed yet
                st.stop()

            # User confirmed — proceed with ingest, threading the skip set
            # into cached_transcribe so cache key reflects the choice
            skip_tuple = tuple(sorted(st.session_state.get(skip_key, set())))
            with st.spinner(f"Parsing {uploaded.name} (CSV ingest)…"):
                invoice_data = cached_transcribe(
                    uploaded.getvalue(), uploaded.name, skip_tuple,
                )
                invoice_data["__source_file"] = Path(uploaded.name).stem + ".json"
                invoice_data = _overlay_edits_from_disk(invoice_data)
            source_label = uploaded.name
        else:
            with st.spinner(
                f"Transcribing {uploaded.name} — usually under 90 seconds…"
            ):
                invoice_data = cached_transcribe(uploaded.getvalue(), uploaded.name)
                invoice_data["__source_file"] = Path(uploaded.name).stem + ".json"
                invoice_data = _overlay_edits_from_disk(invoice_data)
            source_label = uploaded.name
    elif picked:
        p = OUTPUT / picked
        invoice_data = json.loads(p.read_text(encoding="utf-8"))
        invoice_data["__source_file"] = p.name
        # Same overlay as the upload branch: if the user picked the original
        # but an edited sibling exists, prefer the edited file so prior
        # session overrides aren't silently dropped on re-open.
        invoice_data = _overlay_edits_from_disk(invoice_data)
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
                extra_flat = st.number_input(
                    "Extra flat (USD)",
                    min_value=0.0,
                    value=float(st.session_state.get("extra_flat", 0.0)),
                    step=1.0, format="%.2f",
                    help="Lump-sum extra cost in USD, split evenly across items.",
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
                    st.dataframe(pd.DataFrame(rename_rows), hide_index=True, width="stretch")

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
                st.markdown("### Push directly to Shopify")
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
                            f"Refresh Shopify\n({age_label})",
                            key="refresh_shopify_inventory_top",
                            width="stretch",
                            help="Re-fetch product catalog from Shopify. Run this after a push "
                                 "or after manual edits in Shopify admin so the local cache "
                                 "matches the live store.",
                        ):
                            with st.spinner("Fetching from Shopify..."):
                                fresh = _fetch_live()
                            if fresh.error:
                                st.error(f"{fresh.error}")
                            else:
                                _save_cache(fresh)
                                st.success(f"{fresh.product_count} products, "
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
                        "Publish to Shopify",
                        type="primary",
                        disabled=(pending_count == 0 and not do_dry_run and not force_push)
                                 or st.session_state.get("_pushing_in_progress", False),
                        width="stretch",
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
                            st.error(f"{result.get('error')}")
                        else:
                            if do_dry_run:
                                st.success(
                                    f"Dry run · would create {result.get('items_attempted', 0)} products "
                                    f"(skipping {result.get('items_skipped_existing', 0)} already pushed)"
                                )
                            else:
                                st.success(
                                    f"Published {result['items_published']} new products · "
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
                                                 width="stretch")
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
                with st.expander("Find duplicate products in Shopify", expanded=False):
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
                                                  width="stretch")
                    if scan_clicked:
                        from shopify_push import find_duplicates
                        with st.spinner("Scanning Shopify catalog (~30s for 3500 products)..."):
                            result = find_duplicates(sku_prefix=sku_filter.strip() or None)
                        if result.get("error"):
                            st.error(f"{result['error']}")
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
                                             width="stretch")
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
                                             width="stretch")

            st.markdown("---")
            st.markdown("### Pre-upload checklist")
            st.markdown(
                "- [ ] Tab 1 cost reconciliation shows ✓\n"
                "- [ ] Tab 2 warnings reviewed — each one has a deliberate decision\n"
                "- [ ] Ceiling-capped items hand-priced if a specimen deserves premium\n"
                "- [ ] Unbranded items have their Vendor column manually set to 'Vintage' in the CSV\n"
                "- [ ] Demand multiplier matches current market conditions"
            )
