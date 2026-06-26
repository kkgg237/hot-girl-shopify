"""Bulk Drop Audit helpers.

Pure, testable pieces for the Streamlit SKU → bag/accessory description workflow.
Network calls are kept behind injectable functions so the UI can reuse them while
unit tests stay offline.
"""
from __future__ import annotations

import base64
import html
import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


ELIGIBLE_TERMS = (
    "bag", "bags", "handbag", "purse", "wallet", "accessory", "accessories",
    "belt", "scarf", "sunglasses", "jewelry", "jewellery", "card case",
    "coin purse", "clutch", "tote", "shoulder bag", "crossbody", "baguette",
)

BANNED_PHRASES = (
    "perfect for",
    "timeless statement",
    "effortlessly elevates",
    "elevate any wardrobe",
    "must-have",
    "chic addition",
    "wardrobe staple",
)

REQUIRED_SECTIONS = ("DIMENSIONS:", "DETAILS:", "MATERIAL:", "CONDITION NOTES:")


@dataclass
class DropAuditProduct:
    sku: str
    title: str = ""
    description_html: str = ""
    price: str = ""
    status: str = ""
    product_type: str = ""
    tags: list[str] = field(default_factory=list)
    image_url: str = ""
    product_id: Optional[int] = None
    variant_id: Optional[int] = None
    admin_url: str = ""
    error: str = ""
    found: bool = True

    @property
    def plain_description(self) -> str:
        return strip_html(self.description_html).strip()

    @property
    def has_description(self) -> bool:
        return bool(self.plain_description)


@dataclass
class DropAuditRow:
    product: DropAuditProduct
    status: str
    warnings: list[str] = field(default_factory=list)
    selected_by_default: bool = False

    @property
    def sku(self) -> str:
        return self.product.sku


@dataclass
class DescriptionDraft:
    dimensions: Optional[str]
    details: list[str]
    material: str
    condition_notes: str
    sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DescriptionAudit:
    passed: bool
    issues: list[str] = field(default_factory=list)


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(value or ""))


def parse_skus(raw: str) -> list[str]:
    """Parse pasted SKUs, accepting newlines and commas, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for part in re.split(r"[\n,]+", raw or ""):
        sku = part.strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        out.append(sku)
    return out


def is_bag_or_accessory(product: DropAuditProduct) -> bool:
    haystack = " ".join([
        product.title or "",
        product.product_type or "",
        " ".join(product.tags or []),
    ]).lower()
    return any(re.search(rf"\b{re.escape(term)}\b", haystack) for term in ELIGIBLE_TERMS)


def plan_products(products: list[DropAuditProduct]) -> list[DropAuditRow]:
    rows: list[DropAuditRow] = []
    for product in products:
        warnings: list[str] = []
        if not product.found:
            rows.append(DropAuditRow(product, "Not found", [product.error or "No Shopify product found"], False))
            continue
        if product.has_description:
            rows.append(DropAuditRow(product, "Has description", [], False))
            continue
        if not is_bag_or_accessory(product):
            rows.append(DropAuditRow(product, "Not eligible", ["Only bags/accessories are supported"], False))
            continue
        if not product.image_url:
            rows.append(DropAuditRow(product, "Missing image", ["First image is required for generation"], False))
            continue
        rows.append(DropAuditRow(product, "Ready to generate", warnings, True))
    return rows


def render_shopify_description(draft: DescriptionDraft) -> str:
    details = [clean_detail_line(d) for d in draft.details if clean_detail_line(d)][:4]
    dimensions = (draft.dimensions or "").strip() or "Needs review"
    material = (draft.material or "").strip() or "Needs review"
    condition = (draft.condition_notes or "").strip() or "Needs review – Condition cannot be fully verified from available image."
    return (
        f"DIMENSIONS:\n{dimensions}\n\n"
        "DETAILS:\n"
        + "\n".join(details or ["Needs review"])
        + f"\n\nMATERIAL:\n{material}\n\n"
        f"CONDITION NOTES:\n{condition}\n"
    )


def clean_detail_line(value: str) -> str:
    line = re.sub(r"^[-•*\s]+", "", (value or "").strip())
    return re.sub(r"\s+", " ", line)


def audit_generated_description(text: str) -> DescriptionAudit:
    issues: list[str] = []
    body = text or ""
    lower = body.lower()
    for section in REQUIRED_SECTIONS:
        if section not in body:
            issues.append(f"missing {section[:-1]}")
    for phrase in BANNED_PHRASES:
        if phrase in lower:
            issues.append(f"banned phrase: {phrase}")

    if "DETAILS:" in body and "\n\nMATERIAL:" in body:
        details_block = body.split("DETAILS:", 1)[1].split("\n\nMATERIAL:", 1)[0]
        details = [ln for ln in (clean_detail_line(x) for x in details_block.splitlines()) if ln]
        if len(details) > 4:
            issues.append("DETAILS has more than 4 lines")
    return DescriptionAudit(passed=not issues, issues=issues)


def build_generation_prompt(title: str, verified_dimensions: str = "", verified_material: str = "") -> str:
    dims = verified_dimensions.strip() or "Needs review"
    material = verified_material.strip() or "Needs review"
    return f"""Generate a Shopify product description for Past Studies.

Use ONLY the product title, first image, and verified facts below. Do not infer exact measurements from the image.

Product title: {title}
Verified dimensions: {dims}
Verified material/details: {material}

Return STRICT JSON only with keys:
- dimensions: string, use "Needs review" unless exact verified dimensions are supplied
- details: array of 3-4 short plain detail lines
- material: string, use "Needs review" if not verifiable
- condition_notes: one compact line in this style: "8/10 – Light wear to suede. Minor surface marks. Interior remains clean."
- warnings: array of short review warnings

Tone: professional, plain, specific. No sales language. No "perfect for". No flowery copy. No markdown.
"""


def generate_description_draft(
    *,
    title: str,
    image_url: str,
    verified_dimensions: str = "",
    verified_material: str = "",
    sources: Optional[list[str]] = None,
    client=None,
    model: str = "claude-sonnet-4-5",
) -> DescriptionDraft:
    """Call Anthropic for a structured draft.

    Exact dimensions come only from the verified_dimensions argument. If blank,
    the rendered description will show Needs review.
    """
    if not title.strip():
        raise ValueError("title is required")
    if not image_url.strip():
        raise ValueError("image_url is required")
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=90.0, max_retries=2)

    media_type, b64 = fetch_image_base64(image_url)
    resp = client.messages.create(
        model=model,
        max_tokens=1200,
        system="You write concise professional ecommerce descriptions for vintage bags and accessories.",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": build_generation_prompt(title, verified_dimensions, verified_material)},
            ],
        }],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    data = parse_json_object(text)
    return DescriptionDraft(
        dimensions=(data.get("dimensions") or verified_dimensions or None),
        details=[str(x) for x in (data.get("details") or [])],
        material=str(data.get("material") or verified_material or "Needs review"),
        condition_notes=str(data.get("condition_notes") or "Needs review – Condition cannot be fully verified from available image."),
        sources=sources or [],
        warnings=[str(x) for x in (data.get("warnings") or [])],
    )


def parse_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1).strip()
    return json.loads(cleaned)


def fetch_image_base64(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(8_000_000)
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
    if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        content_type = "image/jpeg"
    return content_type, base64.b64encode(raw).decode("ascii")


_FIND_BY_SKU = """
query FindBySku($q: String!) {
  productVariants(first: 10, query: $q) {
    nodes {
      legacyResourceId
      sku
      price
      product {
        legacyResourceId
        title
        status
        productType
        tags
        descriptionHtml
        featuredImage { url }
      }
    }
  }
}
"""


def lookup_products_by_skus(skus: list[str], *, gql_fn=None, shop: str = "") -> list[DropAuditProduct]:
    """Lookup Shopify products by exact variant SKU.

    `gql_fn` is injectable for tests. In production it uses bulk_measurements._gql
    so Shopify auth stays centralized.
    """
    if gql_fn is None:
        from bulk_measurements import _gql as gql_fn  # reuse existing Shopify auth
    products: list[DropAuditProduct] = []
    for sku in skus:
        try:
            data = gql_fn(_FIND_BY_SKU, {"q": f'sku:"{sku}"'})
            products.append(_product_from_sku_result(sku, data, shop=shop))
        except Exception as exc:  # noqa: BLE001 - surfaced in row status
            products.append(DropAuditProduct(sku=sku, found=False, error=str(exc)))
    return products


def _product_from_sku_result(sku: str, data: dict, *, shop: str = "") -> DropAuditProduct:
    nodes = ((data.get("productVariants") or {}).get("nodes")) or []
    exact = [n for n in nodes if (n.get("sku") or "").strip() == sku]
    if not exact:
        return DropAuditProduct(sku=sku, found=False, error="No exact SKU match")
    node = exact[0]
    prod = node.get("product") or {}
    image = prod.get("featuredImage") or {}
    try:
        product_id = int(prod.get("legacyResourceId"))
    except (TypeError, ValueError):
        product_id = None
    try:
        variant_id = int(node.get("legacyResourceId"))
    except (TypeError, ValueError):
        variant_id = None
    admin_url = f"https://{shop}/admin/products/{product_id}" if shop and product_id else ""
    return DropAuditProduct(
        sku=sku,
        title=prod.get("title") or "",
        description_html=prod.get("descriptionHtml") or "",
        price=str(node.get("price") or ""),
        status=prod.get("status") or "",
        product_type=prod.get("productType") or "",
        tags=list(prod.get("tags") or []),
        image_url=image.get("url") or "",
        product_id=product_id,
        variant_id=variant_id,
        admin_url=admin_url,
        found=True,
    )


def append_audit_log(path: Path, *, product: DropAuditProduct, description: str, sources: list[str], warnings: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sku": product.sku,
        "product_id": product.product_id,
        "variant_id": product.variant_id,
        "title": product.title,
        "old_description_present": product.has_description,
        "new_description": description,
        "sources_used": sources,
        "warnings": warnings,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
