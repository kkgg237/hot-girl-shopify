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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, quote_plus, urlparse


ELIGIBLE_TERMS = (
    "bag", "bags", "handbag", "purse", "wallet", "accessory", "accessories",
    "belt", "scarf", "sunglasses", "jewelry", "jewellery", "card case",
    "coin purse", "clutch", "tote", "shoulder bag", "crossbody", "baguette",
    # Bag-model words that show up in titles without the word "bag"
    # (e.g. "Chanel Double Flap", "Gucci Monogram Pochette").
    "pouch", "pochette", "flap", "satchel", "hobo", "backpack",
    "duffle", "duffel", "boston", "sac", "vanity", "wristlet", "minaudiere",
    "cardholder", "card holder", "keychain", "necklace", "bracelet",
    "earrings", "brooch", "bangle", "charm",
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
    image_urls: list[str] = field(default_factory=list)
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


def plan_products(products: list[DropAuditProduct], force_eligible: Optional[set[str]] = None) -> list[DropAuditRow]:
    """Classify looked-up products. `force_eligible` SKUs skip the
    bag/accessory check — the UI escape hatch for classifier misses."""
    force = force_eligible or set()
    rows: list[DropAuditRow] = []
    for product in products:
        if not product.found:
            rows.append(DropAuditRow(product, "Not found", [product.error or "No Shopify product found"], False))
            continue
        if not (is_bag_or_accessory(product) or product.sku in force):
            rows.append(DropAuditRow(product, "Not eligible", ["Only bags/accessories are supported"], False))
            continue
        if product.has_description:
            rows.append(DropAuditRow(product, "Has description", [], False))
            continue
        if not product.image_url:
            rows.append(DropAuditRow(product, "Missing image", ["First image is required for generation"], False))
            continue
        rows.append(DropAuditRow(product, "Ready to generate", [], True))
    return rows


def render_shopify_description(draft: DescriptionDraft) -> str:
    details = [clean_detail_line(d) for d in draft.details if clean_detail_line(d)][:4]
    dimensions = (draft.dimensions or "").strip() or "Needs review"
    material = (draft.material or "").strip() or "Needs review"
    condition = (draft.condition_notes or "").strip() or "Needs review – Condition cannot be fully verified from available images."
    return (
        f"DIMENSIONS:\n{dimensions}\n\n"
        "DETAILS:\n"
        + "\n".join(details or ["Needs review"])
        + f"\n\nMATERIAL:\n{material}\n\n"
        f"CONDITION NOTES:\n{condition}\n"
    )


_SECTION_HEADER_RE = re.compile(r"^[A-Z][A-Z /&]*:$")


def shopify_description_html(text: str) -> str:
    """Convert the plaintext template into the store's HTML description format.

    Matches the Handbag copy template in heuristics/description_templates.yaml:
    bold section headers, DETAILS as a <ul> of bullets, other sections inline
    after the header.
    """
    parts: list[str] = []
    section: Optional[str] = None
    body: list[str] = []

    def flush() -> None:
        nonlocal section, body
        if section is None:
            if body:
                parts.append("<p>" + "<br>".join(html.escape(x) for x in body) + "</p>")
        elif section == "DETAILS:":
            items = [clean_detail_line(x) for x in body if clean_detail_line(x)]
            parts.append(f"<p><strong>{section}</strong></p>")
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in items) + "</ul>")
        else:
            joined = "<br>".join(html.escape(x) for x in body)
            parts.append(f"<p><strong>{section}</strong><br>{joined}</p>")
        section, body = None, []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SECTION_HEADER_RE.match(line):
            flush()
            section = line
        else:
            body.append(line)
    flush()
    return "\n".join(parts)


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


def build_generation_prompt(title: str, verified_dimensions: str = "", verified_material: str = "", image_count: int = 1) -> str:
    dims = verified_dimensions.strip() or "Needs review"
    material = verified_material.strip() or "Needs review"
    return f"""Generate a Shopify product description for Past Studies.

Use ONLY the product title, the {image_count} attached listing photo(s), and verified facts below. Do not infer exact measurements from the photos.

Product title: {title}
Verified dimensions: {dims}
Verified material/details: {material}

Return STRICT JSON only with keys:
- dimensions: string, use "Needs review" unless exact verified dimensions are supplied
- details: array of 3-4 short plain detail lines
- material: string, use "Needs review" if not verifiable
- condition_observations: array of short strings — FIRST, before scoring, inspect the photos area by area (exterior front/back, corners & edges, hardware, interior, base) and record ONLY what is actually visible. One entry per area you can see, e.g. "corners: light rubbing", "hardware: minor surface scratches". If an area is not pictured, write "<area>: not shown". Do not invent flaws. This is the evidence the grade must follow from.
- condition_notes: AFTER filling condition_observations, assign a 1–10 grade consistent with that evidence, then write dry, factual sentences in the format "N/10 (Tier) – <standardized tier sentence> <specific visible flaws>".
    Score → tier → standardized FIRST sentence (TheRealReal condition standards) — the first sentence MUST be this exact phrase for the tier:
      10 = Pristine — "New, unused condition."
      9  = Excellent — "Like new with no visible signs of wear."
      8  = Very Good — "Minor signs of wear."
      7  = Good — "Moderate signs of wear."
      6  = Fair — "Heavy signs of wear."
      1-5 = As Is — "Extensive signs of wear; may require repair."
    Do not inflate: wear visible in multiple areas cannot be Very Good (8) or above. After the standardized first sentence, add the specific visible flaws — enough to disclose every visible flaw, but stay terse: one flaw per sentence, no redundancy (never "Moderate wear throughout" AND "corner and edge wear present").
    Grade and describe ONLY from the visible evidence: never invent, guess, or hallucinate wear, marks, soiling, repairs, or restoration. If an area was "not shown", do not describe it; add a warning that it could not be assessed. If the photos are too few/unclear to grade at all, set condition_notes to "Needs review" and add a warning. Terse locations ("front and back leather", not "the lower front panel and edges"). Dry language only, no editorializing adjectives ("bright", "gorgeous", "presents well", "character", "well kept"), no alarmist words (damaged, stained, dirty, heavy wear, discoloration). Style: "8/10 (Very Good) – Minor signs of wear. Light corner rubbing and faint surface scratches to the hardware. Interior clean."
- warnings: array of short review warnings (include one if photos are too few/unclear to grade condition confidently)

Tone: professional, plain, specific, dry. No sales language. No "perfect for". No flowery copy. No markdown. Condition notes stay factual and truthful, terse, no redundant restatement of the same wear.
Escape inch marks inside JSON strings as \\" (e.g. "10\\" L x 3\\" W x 6\\" H").
"""


MAX_GENERATION_IMAGES = 8
GENERATION_IMAGE_WIDTH = 800  # plenty for condition grading, ~50x smaller than originals


def shopify_image_url(url: str, width: int) -> str:
    """Ask the Shopify CDN for a resized rendition. No-op for other hosts."""
    if not url or "shopify" not in urlparse(url).netloc:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}width={width}"


def reverse_image_search_url(image_url: str) -> str:
    """Google Lens reverse-image search on a public image URL."""
    return "https://lens.google.com/uploadbyurl?url=" + quote(image_url, safe="")


def title_listing_search_url(title: str) -> str:
    """Google search for same-model listings with published dimensions."""
    return "https://www.google.com/search?q=" + quote_plus(f"{title} dimensions")


def generate_description_draft(
    *,
    title: str,
    image_urls: list[str],
    verified_dimensions: str = "",
    verified_material: str = "",
    sources: Optional[list[str]] = None,
    client=None,
    model: str = "claude-sonnet-4-5",
) -> DescriptionDraft:
    """Call Anthropic for a structured draft.

    All listing photos (capped at MAX_GENERATION_IMAGES) are attached so the
    condition grade is a best guess across every angle. Exact dimensions still
    come only from the verified_dimensions argument. If blank, the rendered
    description will show Needs review.
    """
    if not title.strip():
        raise ValueError("title is required")
    urls = [u for u in (image_urls or []) if u and u.strip()][:MAX_GENERATION_IMAGES]
    if not urls:
        raise ValueError("at least one image_url is required")
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=90.0, max_retries=2)

    fetch_urls = [shopify_image_url(u, GENERATION_IMAGE_WIDTH) for u in urls]
    with ThreadPoolExecutor(max_workers=min(8, len(fetch_urls))) as ex:
        fetched = list(ex.map(fetch_image_base64, fetch_urls))
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
        for media_type, b64 in fetched
    ]
    content.append({"type": "text", "text": build_generation_prompt(title, verified_dimensions, verified_material, image_count=len(urls))})

    resp = client.messages.create(
        model=model,
        max_tokens=1200,
        system="You write concise professional ecommerce descriptions for vintage bags and accessories.",
        messages=[{"role": "user", "content": content}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    data = parse_json_object(text)
    return DescriptionDraft(
        dimensions=(data.get("dimensions") or verified_dimensions or None),
        details=[str(x) for x in (data.get("details") or [])],
        material=str(data.get("material") or verified_material or "Needs review"),
        condition_notes=str(data.get("condition_notes") or "Needs review – Condition cannot be fully verified from available images."),
        sources=sources or [],
        warnings=[str(x) for x in (data.get("warnings") or [])],
    )


def _escape_inch_marks(s: str) -> str:
    """Escape unescaped inch marks inside JSON strings: `10" L` → `10\\" L`.

    Models writing dimensions like "10" L x 3" W" produce invalid JSON; an
    inch mark is a quote right after a digit that is NOT followed by a JSON
    structural character (so legit closing quotes stay untouched)."""
    return re.sub(r'(?<=\d)"(?!\s*[,}\]:])', r'\\"', s)


def parse_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1).strip()
    # Web-search responses often wrap the JSON in prose; also try the last
    # top-level {...} block.
    candidates = [cleaned]
    blocks = re.findall(r"\{.*\}", cleaned, re.DOTALL)
    if blocks:
        candidates.append(blocks[-1])
    last_err: Optional[json.JSONDecodeError] = None
    for cand in candidates:
        for attempt in (cand, _escape_inch_marks(cand)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError as e:
                last_err = e
    raise last_err


@dataclass
class DimensionSuggestion:
    dimensions: str
    confidence: str
    sources: list[str] = field(default_factory=list)
    notes: str = ""


def suggest_dimensions_via_web_search(
    *,
    title: str,
    image_url: str = "",
    client=None,
    model: str = "claude-sonnet-4-5",
) -> DimensionSuggestion:
    """Web-search resale/retail listings of the same model for published
    dimensions. When image_url is given the photo is attached so the model
    identifies the exact shape first — listing titles are often too generic
    ("Gucci Black Monogram Pochette") to pin down the model by text alone."""
    if not title.strip():
        raise ValueError("title is required")
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=180.0, max_retries=1)

    photo_step = (
        "A photo of the exact item is attached. FIRST identify the precise model/shape "
        "from the photo (e.g. 'GG canvas boat pochette', 'camera bag', 'accessory pouch'), "
        "THEN search for that model's published dimensions.\n\n"
        if image_url
        else ""
    )
    prompt = f"""Find the published dimensions for this product by searching resale and retail listings (Fashionphile, Vestiaire Collective, The RealReal, Rebag, 1stDibs, brand sites, etc.):

{title}

{photo_step}Rules:
- Vintage listing titles are generic — search by likely MODEL names, not just the title verbatim. Drop the color from queries and try shape-based names.
- Dimensions come from the model/shape and size class, NOT the colorway, hardware tone, or material finish. A credible listing of the same model in a different color or material IS a valid dimension source — use it.
- Only reject a source when the shape or size class differs (e.g. mini vs. medium, east/west vs. square).
- Prefer agreement across two or more independent sources.
- Report in inches in this format: 10" L x 3" W x 6" H
- If no same-model listing is found in any colorway, return an empty dimensions string — do not guess from photos.

Return STRICT JSON only with keys:
- dimensions: string in the format above, or "" if no same-model match
- confidence: "high" (2+ agreeing sources), "medium" (1 solid source), or "low"
- sources: array of the URLs the dimensions came from
- notes: one short line on the match (name the model you identified; note if dimensions came from a different colorway) or why it failed

Escape inch marks inside JSON strings as \\" (e.g. "10\\" L x 3\\" W x 6\\" H").
"""
    content: list[dict] = []
    if image_url:
        media_type, b64 = fetch_image_base64(shopify_image_url(image_url, GENERATION_IMAGE_WIDTH))
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
        messages=[{"role": "user", "content": content}],
    )
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = parse_json_object(text)
    return DimensionSuggestion(
        dimensions=str(data.get("dimensions") or "").strip(),
        confidence=str(data.get("confidence") or "low"),
        sources=[str(x) for x in (data.get("sources") or [])],
        notes=str(data.get("notes") or ""),
    )


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
        media(first: 12) {
          nodes {
            ... on MediaImage { image { url } }
          }
        }
      }
    }
  }
}
"""


_FIND_BY_TAG = """
query FindByTag($q: String!, $first: Int!) {
  products(first: $first, query: $q) {
    nodes {
      legacyResourceId
      title
      status
      productType
      tags
      descriptionHtml
      featuredImage { url }
      media(first: 12) {
        nodes {
          ... on MediaImage { image { url } }
        }
      }
      variants(first: 1) { nodes { legacyResourceId sku price } }
    }
  }
}
"""

TAG_SEARCH_LIMIT = 100


def lookup_products_by_skus(skus: list[str], *, gql_fn=None, shop: str = "") -> list[DropAuditProduct]:
    """Lookup Shopify products by exact variant SKU.

    `gql_fn` is injectable for tests. In production it uses bulk_measurements._gql
    so Shopify auth stays centralized.
    """
    if gql_fn is None:
        from bulk_measurements import _gql as gql_fn  # reuse existing Shopify auth

    def one(sku: str) -> DropAuditProduct:
        try:
            data = gql_fn(_FIND_BY_SKU, {"q": f'sku:"{sku}"'})
            return _product_from_sku_result(sku, data, shop=shop)
        except Exception as exc:  # noqa: BLE001 - surfaced in row status
            return DropAuditProduct(sku=sku, found=False, error=str(exc))

    if not skus:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(skus))) as ex:
        return list(ex.map(one, skus))


def lookup_products_by_tags(tags: list[str], *, gql_fn=None, shop: str = "") -> list[DropAuditProduct]:
    """Lookup Shopify products carrying any of the given tags.

    Products matching more than one tag are returned once. Tags with no
    matches produce a not-found pseudo-row so the UI can report them.
    """
    if gql_fn is None:
        from bulk_measurements import _gql as gql_fn  # reuse existing Shopify auth
    products: list[DropAuditProduct] = []
    seen_ids: set[str] = set()
    for tag in tags:
        try:
            data = gql_fn(_FIND_BY_TAG, {"q": f'tag:"{tag}"', "first": TAG_SEARCH_LIMIT})
        except Exception as exc:  # noqa: BLE001 - surfaced in row status
            products.append(DropAuditProduct(sku=f"tag:{tag}", found=False, error=str(exc)))
            continue
        nodes = ((data.get("products") or {}).get("nodes")) or []
        if not nodes:
            products.append(DropAuditProduct(sku=f"tag:{tag}", found=False, error=f'No products tagged "{tag}"'))
            continue
        for prod in nodes:
            pid = str(prod.get("legacyResourceId") or "")
            if pid and pid in seen_ids:
                continue
            seen_ids.add(pid)
            variant = (((prod.get("variants") or {}).get("nodes")) or [{}])[0] or {}
            sku = (variant.get("sku") or "").strip() or (f"pid:{pid}" if pid else f"tag:{tag}")
            products.append(_build_product(sku, prod, variant, shop=shop))
        if len(nodes) >= TAG_SEARCH_LIMIT:
            products.append(DropAuditProduct(
                sku=f"tag:{tag}", found=False,
                error=f'Tag "{tag}" matched {TAG_SEARCH_LIMIT}+ products; only the first {TAG_SEARCH_LIMIT} were loaded',
            ))
    return products


def _product_from_sku_result(sku: str, data: dict, *, shop: str = "") -> DropAuditProduct:
    nodes = ((data.get("productVariants") or {}).get("nodes")) or []
    exact = [n for n in nodes if (n.get("sku") or "").strip() == sku]
    if not exact:
        return DropAuditProduct(sku=sku, found=False, error="No exact SKU match")
    node = exact[0]
    prod = node.get("product") or {}
    return _build_product(sku, prod, node, shop=shop)


def _build_product(sku: str, prod: dict, variant: dict, *, shop: str = "") -> DropAuditProduct:
    """Assemble a DropAuditProduct from a product node + one variant node."""
    image = prod.get("featuredImage") or {}
    media_nodes = ((prod.get("media") or {}).get("nodes")) or []
    image_urls: list[str] = []
    for m in media_nodes:
        url = ((m or {}).get("image") or {}).get("url")
        if url:
            image_urls.append(url)
    featured = image.get("url") or ""
    if featured and featured not in image_urls:
        image_urls.insert(0, featured)
    try:
        product_id = int(prod.get("legacyResourceId"))
    except (TypeError, ValueError):
        product_id = None
    try:
        variant_id = int(variant.get("legacyResourceId"))
    except (TypeError, ValueError):
        variant_id = None
    admin_url = f"https://{shop}/admin/products/{product_id}" if shop and product_id else ""
    return DropAuditProduct(
        sku=sku,
        title=prod.get("title") or "",
        description_html=prod.get("descriptionHtml") or "",
        price=str(variant.get("price") or ""),
        status=prod.get("status") or "",
        product_type=prod.get("productType") or "",
        tags=list(prod.get("tags") or []),
        image_url=featured or (image_urls[0] if image_urls else ""),
        image_urls=image_urls,
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
