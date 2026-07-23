"""SKU-keyed Shopify bulk editor helpers.

Pure planning lives here so the Streamlit tab stays review-first and testable:
parse pasted SKUs → lookup live products → build validated row-level update plans →
apply only approved changed fields.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Optional

from shopify_push import DEFAULT_API_VERSION


VALID_STATUSES = {"active", "draft", "archived"}


@dataclass
class ParsedSkus:
    terms: list[str]
    duplicates: list[str]


@dataclass
class ProductSkuRecord:
    sku: str
    product_id: int
    variant_id: int
    title: str
    price: str
    tags: str
    status: str
    admin_url: str = ""
    error: str = ""


@dataclass
class LookupPlan:
    records: list[ProductSkuRecord]
    not_found: list[str]
    duplicates: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)


@dataclass
class SkuUpdatePlan:
    sku: str
    product_id: int
    variant_id: int
    product_updates: dict[str, str]
    variant_updates: dict[str, str]

    @property
    def has_changes(self) -> bool:
        return bool(self.product_updates or self.variant_updates)


@dataclass
class ApplyResult:
    sku: str
    product_id: int
    variant_id: int
    ok: bool
    status: int
    response: dict


def parse_sku_terms(raw: str) -> ParsedSkus:
    """Split pasted SKU text on spaces, commas, and newlines. Preserve order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    terms: list[str] = []
    for part in re.split(r"[\s,]+", raw or ""):
        sku = part.strip()
        if not sku:
            continue
        if sku in seen:
            if sku not in duplicates:
                duplicates.append(sku)
            continue
        seen.add(sku)
        terms.append(sku)
    return ParsedSkus(terms=terms, duplicates=duplicates)


def build_update_plan(
    skus: Iterable[str],
    lookup_fn: Callable[[list[str]], dict[str, ProductSkuRecord]],
    duplicates: Optional[list[str]] = None,
) -> LookupPlan:
    """Lookup SKUs and return matched rows in the same order as the input."""
    ordered = list(skus)
    found = lookup_fn(ordered)
    records: list[ProductSkuRecord] = []
    not_found: list[str] = []
    collisions: list[str] = []
    for sku in ordered:
        rec = found.get(sku)
        if not rec:
            not_found.append(sku)
            continue
        if rec.error == "collision":
            collisions.append(sku)
            continue
        records.append(rec)
    return LookupPlan(
        records=records,
        not_found=not_found,
        duplicates=duplicates or [],
        collisions=collisions,
    )


def _normalize_price(value) -> tuple[Optional[str], Optional[str]]:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None, "blank price"
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, "invalid price"
    if amount < 0:
        return None, "invalid price"
    return f"{amount.quantize(Decimal('0.01'))}", None


def _as_int(value) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def rows_to_apply(rows: Iterable[dict]) -> tuple[list[SkuUpdatePlan], list[str]]:
    """Validate edited Streamlit rows and return changed update plans only."""
    plans: list[SkuUpdatePlan] = []
    errors: list[str] = []

    for idx, row in enumerate(rows, start=1):
        if not bool(row.get("Keep", True)):
            continue

        sku = str(row.get("SKU") or "").strip() or f"row {idx}"
        product_id = _as_int(row.get("Product ID"))
        variant_id = _as_int(row.get("Variant ID"))
        row_errors: list[str] = []
        if not product_id:
            row_errors.append("missing product ID")
        if not variant_id:
            row_errors.append("missing variant ID")

        raw_new_price = row.get("New price") if "New price" in row else row.get("Price")
        new_price, price_err = _normalize_price(raw_new_price)
        if price_err:
            row_errors.append(price_err)

        raw_new_status = row.get("New status") if "New status" in row else row.get("Status")
        new_status = str(raw_new_status or "").strip().lower()
        if new_status not in VALID_STATUSES:
            row_errors.append("invalid status")

        if row_errors:
            errors.append(f"{sku}: " + "; ".join(row_errors))
            continue

        current_title = str(row.get("Current title", row.get("Original title", "")) or "").strip()
        current_tags = str(row.get("Current tags", row.get("Original tags", "")) or "").strip()
        current_status = str(row.get("Current status", row.get("Original status", "")) or "").strip().lower()
        current_price, _ = _normalize_price(row.get("Current price", row.get("Original price", "")))

        new_title = str(row.get("New title", row.get("Title", "")) or "").strip()
        new_tags = str(row.get("New tags", row.get("Tags", "")) or "").strip()

        product_updates: dict[str, str] = {}
        variant_updates: dict[str, str] = {}
        if new_title != current_title:
            product_updates["title"] = new_title
        if new_tags != current_tags:
            product_updates["tags"] = new_tags
        if new_status != current_status:
            product_updates["status"] = new_status
        if new_price != current_price:
            variant_updates["price"] = new_price or ""

        assert product_id is not None
        assert variant_id is not None
        plan = SkuUpdatePlan(
            sku=sku,
            product_id=product_id,
            variant_id=variant_id,
            product_updates=product_updates,
            variant_updates=variant_updates,
        )
        if plan.has_changes:
            plans.append(plan)

    return plans, errors


_FIND_SKUS_QUERY = """
query VariantsBySku($q: String!) {
  productVariants(first: 20, query: $q) {
    nodes {
      legacyResourceId
      sku
      price
      product {
        legacyResourceId
        title
        tags
        status
      }
    }
  }
}
"""


def _gql(query: str, variables: dict) -> dict:
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        raise RuntimeError("Shopify not configured")
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    data = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
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


def lookup_products_by_skus(skus: list[str], *, shop: str = "") -> dict[str, ProductSkuRecord]:
    """Find products by exact variant SKU.

    Returns one record per exact SKU. If Shopify returns multiple products with the
    same SKU, the record is marked with error="collision" so the UI blocks it.
    """
    from shopify_inventory import get_shop

    shop_domain = shop or get_shop() or ""
    out: dict[str, ProductSkuRecord] = {}
    for sku in skus:
        try:
            data = _gql(_FIND_SKUS_QUERY, {"q": f'sku:"{sku}"'})
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
            out[sku] = ProductSkuRecord(sku=sku, product_id=0, variant_id=0, title="", price="", tags="", status="", error=str(e))
            continue
        nodes = ((data.get("productVariants") or {}).get("nodes")) or []
        wanted = sku.strip().casefold()
        exact = [
            n for n in nodes
            if (n.get("sku") or "").strip().casefold() == wanted
        ]
        product_ids = {
            int((n.get("product") or {}).get("legacyResourceId") or 0)
            for n in exact
            if (n.get("product") or {}).get("legacyResourceId")
        }
        if not exact:
            continue
        if len(product_ids) > 1:
            out[sku] = ProductSkuRecord(sku=sku, product_id=0, variant_id=0, title="", price="", tags="", status="", error="collision")
            continue
        node = exact[0]
        product = node.get("product") or {}
        product_id = int(product.get("legacyResourceId") or 0)
        variant_id = int(node.get("legacyResourceId") or 0)
        tags = product.get("tags") or []
        out[sku] = ProductSkuRecord(
            sku=str(node.get("sku") or sku).strip(),
            product_id=product_id,
            variant_id=variant_id,
            title=product.get("title") or "",
            price=str(node.get("price") or ""),
            tags=", ".join(tags) if isinstance(tags, list) else str(tags or ""),
            status=(product.get("status") or "").lower(),
            admin_url=(f"https://{shop_domain}/admin/products/{product_id}" if shop_domain and product_id else ""),
        )
        time.sleep(0.05)
    return out


def _api_put(path: str, body: dict) -> tuple[int, dict]:
    from shopify_inventory import get_shop, get_token

    shop = get_shop()
    token = get_token()
    if not shop or not token:
        return 0, {"error": "Shopify not configured"}
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"_raw_error": raw}
        return e.code, parsed


def apply_update_plan(plan: SkuUpdatePlan) -> list[ApplyResult]:
    """Apply a single row's approved changes to Shopify."""
    results: list[ApplyResult] = []
    if plan.product_updates:
        status, resp = _api_put(
            f"/products/{plan.product_id}.json",
            {"product": {"id": plan.product_id, **plan.product_updates}},
        )
        results.append(ApplyResult(plan.sku, plan.product_id, plan.variant_id, status == 200, status, resp))
    if plan.variant_updates:
        status, resp = _api_put(
            f"/variants/{plan.variant_id}.json",
            {"variant": {"id": plan.variant_id, **plan.variant_updates}},
        )
        results.append(ApplyResult(plan.sku, plan.product_id, plan.variant_id, status == 200, status, resp))
    return results
