"""Bulk measurements → Shopify descriptions.

Takes a spreadsheet of garment measurements keyed by barcode, matches each row
to a live Shopify product, drops the numbers into that garment type's website
copy template (heuristics/description_templates.yaml), and writes the rendered
description to the listing's body_html.

The listings are expected to have EMPTY descriptions — by default we skip any
product that already has a non-empty description rather than clobber it.

Pure logic lives here so it's unit-testable without Streamlit or a network:
  * CSV parsing + column mapping (deterministic, with an optional Claude pass)
  * per-row template routing + rendering
  * barcode/SKU product lookup (the only part that touches the network)

The Streamlit UI in app.py (`render_bulk_measurements_tab`) drives the preview
and the write loop; writes go through `shopify_push.update_product_body_html`.
"""
from __future__ import annotations

import csv as _csv
import html as _html
import io
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import heuristics.loader as _h
from heuristics.loader import DescriptionTemplate


# ---------------------------------------------------------------------------
# Canonical measurement fields + column aliases
# ---------------------------------------------------------------------------
#
# The canonical names match the <strong>FIELD</strong> labels used inside the
# description templates. A CSV column is mapped to one of these; the renderer
# only fills the fields a given template actually defines, so extra columns are
# harmlessly ignored.

CANONICAL_FIELDS: tuple[str, ...] = (
    "CHEST", "LENGTH", "SLEEVE", "SHOULDER",
    "WAIST", "HIPS", "INSEAM", "RISE",
    "TOP LENGTH", "BOTTOM LENGTH",
)

# Normalized csv header -> canonical field. Longer/more-specific phrases must be
# checked before generic ones (handled by sorting in _match_measurement_field).
_FIELD_ALIASES: dict[str, str] = {
    "chest": "CHEST", "bust": "CHEST", "pit to pit": "CHEST", "p2p": "CHEST",
    "pit-to-pit": "CHEST", "armpit to armpit": "CHEST",
    "length": "LENGTH", "body length": "LENGTH", "total length": "LENGTH",
    "front length": "LENGTH", "garment length": "LENGTH",
    "sleeve": "SLEEVE", "sleeve length": "SLEEVE",
    "shoulder": "SHOULDER", "shoulder width": "SHOULDER",
    "shoulder seam": "SHOULDER", "shoulders": "SHOULDER",
    "waist": "WAIST",
    "hips": "HIPS", "hip": "HIPS",
    "inseam": "INSEAM", "in seam": "INSEAM",
    "rise": "RISE", "front rise": "RISE",
    "top length": "TOP LENGTH",
    "bottom length": "BOTTOM LENGTH",
}

_BARCODE_ALIASES = ("barcode", "bar code", "upc", "ean", "gtin")
_SKU_ALIASES = ("sku", "variant sku", "style number", "style no")
_TITLE_ALIASES = ("title", "name", "product", "product title", "item", "description")
_TAGGED_SIZE_ALIASES = ("tagged size", "size", "tag size", "label size")
_NOTES_ALIASES = ("notes", "note", "condition", "condition notes", "comments", "remarks")


def _norm(s: str) -> str:
    """Lowercase, trim, collapse internal whitespace — for tolerant matching."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _match_measurement_field(header: str) -> Optional[str]:
    """Return the canonical field a header maps to, or None.

    Tries an exact alias hit first, then a longest-alias substring match so a
    header like "Chest (pit to pit)" still resolves to CHEST.
    """
    n = _norm(header)
    if not n:
        return None
    if n in _FIELD_ALIASES:
        return _FIELD_ALIASES[n]
    for alias in sorted(_FIELD_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", n):
            return _FIELD_ALIASES[alias]
    return None


def _match_special(header: str, aliases: tuple[str, ...]) -> bool:
    n = _norm(header)
    return any(n == a or re.search(rf"\b{re.escape(a)}\b", n) for a in aliases)


# ---------------------------------------------------------------------------
# Column map
# ---------------------------------------------------------------------------

@dataclass
class ColumnMap:
    """Resolution of CSV headers to their roles."""
    title_col: Optional[str] = None
    barcode_col: Optional[str] = None
    sku_col: Optional[str] = None
    tagged_size_col: Optional[str] = None
    notes_col: Optional[str] = None
    # csv header -> canonical measurement field
    measurement_map: dict[str, str] = field(default_factory=dict)

    def is_usable(self) -> tuple[bool, str]:
        """A map needs at least a key column (barcode or sku) + a title or
        measurements to be worth running. Returns (ok, reason-if-not)."""
        if not (self.barcode_col or self.sku_col):
            return False, "no Barcode (or SKU) column found"
        if not self.measurement_map and not self.tagged_size_col:
            return False, "no measurement or size columns found"
        return True, ""


def map_columns_heuristic(headers: list[str]) -> ColumnMap:
    """Resolve headers deterministically via the alias tables. No network."""
    cm = ColumnMap()
    for h in headers:
        if h is None:
            continue
        # Measurement columns take priority over generic special matches so a
        # "Length" column isn't mistaken for anything else.
        mf = _match_measurement_field(h)
        if mf and h not in cm.measurement_map:
            cm.measurement_map[h] = mf
            continue
        if cm.barcode_col is None and _match_special(h, _BARCODE_ALIASES):
            cm.barcode_col = h
        elif cm.sku_col is None and _match_special(h, _SKU_ALIASES):
            cm.sku_col = h
        elif cm.tagged_size_col is None and _match_special(h, _TAGGED_SIZE_ALIASES):
            cm.tagged_size_col = h
        elif cm.notes_col is None and _match_special(h, _NOTES_ALIASES):
            cm.notes_col = h
        elif cm.title_col is None and _match_special(h, _TITLE_ALIASES):
            cm.title_col = h

    # The product title is often an unnamed first column ("" header). If we
    # didn't find an explicit title column, adopt the first blank-named header.
    if cm.title_col is None:
        for h in headers:
            if h is not None and _norm(h) == "":
                cm.title_col = h
                break
    return cm


_CLAUDE_SYSTEM = """You map spreadsheet column headers to roles for a garment
measurements importer. Return STRICT JSON, no prose, no markdown fence, with keys:
  title_col, barcode_col, sku_col, tagged_size_col, notes_col  (string header or null)
  measurement_map  (object: header -> one of %s)
Use the EXACT header strings as they appear in the input (preserve spacing/case).
Only include measurement columns that clearly hold a body measurement. Leave a
role null if no column fits. Do not invent headers.""" % ", ".join(CANONICAL_FIELDS)


def map_columns(
    headers: list[str],
    sample_rows: Optional[list[dict]] = None,
    *,
    use_claude: bool = True,
    client=None,
) -> ColumnMap:
    """Resolve headers, preferring a Claude pass and falling back to heuristics.

    The heuristic result is always computed and used as the fallback, so this is
    safe with no API key / offline. Claude only *overrides* when it returns a
    valid, usable mapping — otherwise we keep the deterministic one.
    """
    base = map_columns_heuristic(headers)
    if not use_claude:
        return base
    try:
        claude_map = _map_columns_via_claude(headers, sample_rows or [], client=client)
    except Exception:
        return base
    if claude_map is None:
        return base
    ok, _ = claude_map.is_usable()
    return claude_map if ok else base


def _map_columns_via_claude(
    headers: list[str], sample_rows: list[dict], *, client=None
) -> Optional[ColumnMap]:
    import anthropic  # local import: optional dep, keeps module importable offline

    client = client or anthropic.Anthropic(timeout=60.0, max_retries=2)
    payload = {
        "headers": headers,
        "sample_rows": sample_rows[:3],
    }
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1000,
        system=_CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text.strip(), re.DOTALL)
    if m:
        text = m.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        return None

    header_set = set(headers)

    def _pick(key: str) -> Optional[str]:
        v = data.get(key)
        return v if isinstance(v, str) and v in header_set else None

    mm_raw = data.get("measurement_map") or {}
    measurement_map: dict[str, str] = {}
    if isinstance(mm_raw, dict):
        for h, canon in mm_raw.items():
            if h in header_set and isinstance(canon, str) and canon.upper() in CANONICAL_FIELDS:
                measurement_map[h] = canon.upper()

    return ColumnMap(
        title_col=_pick("title_col"),
        barcode_col=_pick("barcode_col"),
        sku_col=_pick("sku_col"),
        tagged_size_col=_pick("tagged_size_col"),
        notes_col=_pick("notes_col"),
        measurement_map=measurement_map,
    )


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_measurement_csv(source) -> tuple[list[str], list[dict]]:
    """Parse a CSV (path / bytes / str / file-like) into (headers, rows).

    Rows are dicts keyed by the raw header strings. Headers are returned in file
    order so the UI can show them as-is. Fully-blank rows are dropped.
    """
    if hasattr(source, "read"):
        raw = source.read()
    elif isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    elif isinstance(source, str) and "\n" not in source and len(source) < 4096:
        # Treat a short single-line string as a path; longer/multiline = content.
        from pathlib import Path
        p = Path(source)
        raw = p.read_bytes() if p.exists() else source.encode("utf-8")
    else:
        raw = source

    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8-sig", errors="replace")
    else:
        text = str(raw)

    reader = _csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    rows: list[dict] = []
    for row in reader:
        if any((v or "").strip() for v in row.values()):
            rows.append(row)
    return headers, rows


# ---------------------------------------------------------------------------
# Per-row extraction + template routing + rendering
# ---------------------------------------------------------------------------

@dataclass
class MeasurementRow:
    title: str = ""
    barcode: str = ""
    sku: str = ""
    tagged_size: str = ""
    notes: str = ""
    measurements: dict[str, str] = field(default_factory=dict)  # canonical -> value


def extract_row(row: dict, cm: ColumnMap) -> MeasurementRow:
    """Pull a MeasurementRow out of a raw CSV dict using the column map."""
    def _g(col: Optional[str]) -> str:
        if not col:
            return ""
        return (row.get(col) or "").strip()

    measurements: dict[str, str] = {}
    for col, canon in cm.measurement_map.items():
        val = _g(col)
        if val:
            measurements[canon] = val
    return MeasurementRow(
        title=_g(cm.title_col),
        barcode=_g(cm.barcode_col),
        sku=_g(cm.sku_col),
        tagged_size=_g(cm.tagged_size_col),
        notes=_g(cm.notes_col),
        measurements=measurements,
    )


def choose_template(
    category: str,
    title: str,
    templates: Optional[list[DescriptionTemplate]] = None,
) -> Optional[DescriptionTemplate]:
    """Route a row to a template: Shopify category first, then title keywords."""
    if templates is None:
        templates = _h.load_description_templates()
    tpl = _h.find_template_for_category(category, templates) if category else None
    if tpl is not None:
        return tpl
    name = _h.suggest_template_from_product(title=title, templates=templates)
    if name:
        for t in templates:
            if t.name == name:
                return t
    return None


def _fmt_value(value: str, unit: str) -> str:
    """Append the unit to a bare numeric measurement (e.g. 18 -> 18\")."""
    v = value.strip()
    if not v or not unit:
        return _html.escape(v)
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return _html.escape(f"{v}{unit}")
    return _html.escape(v)


def render_template(
    tpl: DescriptionTemplate,
    *,
    tagged_size: str = "",
    measurements: Optional[dict[str, str]] = None,
    notes: str = "",
    unit: str = '"',
) -> str:
    """Inject values into a template's HTML, preserving its exact copy format.

    Only fields the template defines are filled; missing values stay blank.
    Returns the rendered body_html.
    """
    body = tpl.template or ""
    measurements = measurements or {}

    if tagged_size:
        body = re.sub(
            r"(<strong>\s*TAGGED SIZE:\s*</strong>)\s*</p>",
            lambda m: f"{m.group(1)} {_html.escape(tagged_size.strip())}</p>",
            body, count=1,
        )

    for canon, value in measurements.items():
        if not value:
            continue
        cell = _fmt_value(value, unit)
        # Fill the empty <td></td> next to <strong>FIELD</strong>.
        body = re.sub(
            rf"(<td><strong>\s*{re.escape(canon)}\s*</strong></td><td>)\s*(</td>)",
            lambda m, c=cell: f"{m.group(1)}{c}{m.group(2)}",
            body, count=1,
        )

    if notes:
        body = re.sub(
            r"(<strong>\s*CONDITION NOTES:\s*</strong>)\s*</p>",
            lambda m: f"{m.group(1)} {_html.escape(notes.strip())}</p>",
            body, count=1,
        )

    return body


# ---------------------------------------------------------------------------
# Shopify product lookup (barcode, then SKU) — the only networked part
# ---------------------------------------------------------------------------

_API_VERSION = "2024-10"

_FIND_BY_VARIANT = """
query FindVariant($q: String!) {
  productVariants(first: 25, query: $q) {
    nodes {
      barcode
      sku
      product {
        legacyResourceId
        title
        status
        descriptionHtml
        category { name fullName }
      }
    }
  }
}
"""


@dataclass
class ProductLookup:
    found: bool = False
    product_id: Optional[int] = None
    title: str = ""
    status: str = ""
    description_html: str = ""
    category_name: str = ""
    matched_by: str = ""          # "barcode" | "sku" | ""
    collision: bool = False       # >1 distinct product carries the key
    error: str = ""

    @property
    def has_description(self) -> bool:
        return bool(_strip_html(self.description_html).strip())


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def _gql(query: str, variables: dict, *, get_shop=None, get_token=None) -> dict:
    """POST a GraphQL op via stdlib urllib (matches shopify_push's HTTP style)."""
    if get_shop is None or get_token is None:
        from shopify_inventory import get_shop as _gs, get_token as _gt
        get_shop = get_shop or _gs
        get_token = get_token or _gt
    shop = get_shop()
    token = get_token()
    if not shop or not token:
        raise RuntimeError("Shopify not configured (missing shop or token).")

    url = f"https://{shop}/admin/api/{_API_VERSION}/graphql.json"
    data = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload.get("data") or {}


def _lookup_by_field(value: str, shop_field: str, **deps) -> ProductLookup:
    """Find a single product with an EXACT variant barcode/sku == value."""
    value = (value or "").strip()
    if not value:
        return ProductLookup(found=False)
    try:
        data = _gql(_FIND_BY_VARIANT, {"q": f'{shop_field}:"{value}"'}, **deps)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
        return ProductLookup(found=False, error=str(e))

    nodes = ((data.get("productVariants") or {}).get("nodes")) or []
    seen: dict[int, ProductLookup] = {}
    for node in nodes:
        if (node.get(shop_field) or "").strip() != value:
            continue  # tokenized search can return near-misses; require exact
        prod = node.get("product") or {}
        try:
            pid = int(prod.get("legacyResourceId"))
        except (TypeError, ValueError):
            continue
        cat = prod.get("category") or {}
        seen[pid] = ProductLookup(
            found=True,
            product_id=pid,
            title=prod.get("title") or "",
            status=prod.get("status") or "",
            description_html=prod.get("descriptionHtml") or "",
            category_name=(cat.get("name") or cat.get("fullName") or ""),
            matched_by=shop_field,
        )
    if not seen:
        return ProductLookup(found=False)
    if len(seen) > 1:
        any_one = next(iter(seen.values()))
        any_one.collision = True
        return any_one
    return next(iter(seen.values()))


def find_product(barcode: str = "", sku: str = "", **deps) -> ProductLookup:
    """Match by variant barcode first; fall back to SKU.

    If a CSV only has a barcode column, we also try that same value as a SKU when
    the barcode lookup misses (handles sheets that label SKUs as 'barcode').
    """
    barcode = (barcode or "").strip()
    sku = (sku or "").strip()
    if barcode:
        res = _lookup_by_field(barcode, "barcode", **deps)
        if res.found:
            return res
    if sku:
        res = _lookup_by_field(sku, "sku", **deps)
        if res.found:
            return res
    # Last resort: treat the barcode value as a SKU.
    if barcode and not sku:
        return _lookup_by_field(barcode, "sku", **deps)
    return ProductLookup(found=False)


# ---------------------------------------------------------------------------
# Planning a whole CSV (no writes) — drives the dry-run preview
# ---------------------------------------------------------------------------

@dataclass
class RowPlan:
    row_index: int
    title: str
    barcode: str
    sku: str
    measurements: dict[str, str]
    tagged_size: str
    notes: str
    lookup: ProductLookup
    template_name: str
    body_html: str
    action: str       # "write" | "skip-not-found" | "skip-has-desc" |
                      # "skip-no-template" | "skip-collision"
    reason: str = ""

    @property
    def will_write(self) -> bool:
        return self.action == "write"


def plan_rows(
    headers: list[str],
    rows: list[dict],
    cm: ColumnMap,
    *,
    templates: Optional[list[DescriptionTemplate]] = None,
    unit: str = '"',
    overwrite_existing: bool = False,
    lookup_fn=find_product,
) -> list[RowPlan]:
    """Resolve every row into a RowPlan (match + template + rendered body + action).

    Networked (calls lookup_fn per row). `lookup_fn` is injectable for tests.
    """
    if templates is None:
        templates = _h.load_description_templates()

    plans: list[RowPlan] = []
    for i, raw in enumerate(rows):
        mr = extract_row(raw, cm)
        lk = lookup_fn(barcode=mr.barcode, sku=mr.sku)

        tpl = None
        if lk.found:
            tpl = choose_template(lk.category_name, mr.title or lk.title, templates)

        template_name = tpl.name if tpl else ""
        body_html = ""
        if tpl is not None:
            body_html = render_template(
                tpl,
                tagged_size=mr.tagged_size,
                measurements=mr.measurements,
                notes=mr.notes,
                unit=unit,
            )

        action, reason = _decide_action(lk, tpl, overwrite_existing)
        plans.append(RowPlan(
            row_index=i,
            title=mr.title or lk.title,
            barcode=mr.barcode,
            sku=mr.sku,
            measurements=mr.measurements,
            tagged_size=mr.tagged_size,
            notes=mr.notes,
            lookup=lk,
            template_name=template_name,
            body_html=body_html,
            action=action,
            reason=reason,
        ))
    return plans


def _decide_action(
    lk: ProductLookup, tpl: Optional[DescriptionTemplate], overwrite_existing: bool
) -> tuple[str, str]:
    if not lk.found:
        return "skip-not-found", (lk.error or "no product with this barcode/SKU")
    if lk.collision:
        return "skip-collision", "more than one product carries this barcode/SKU"
    if tpl is None:
        return "skip-no-template", "could not route to a description template"
    if lk.has_description and not overwrite_existing:
        return "skip-has-desc", "listing already has a description"
    return "write", ""
