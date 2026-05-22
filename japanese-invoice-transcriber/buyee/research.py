"""Title enrichment via web search + photo vision (escalation).

Strategy (cost-efficient):
  1. For each item, decide which enrichment path:
     - Direct Buyee auction listing fetch (free) — items with lowercase-prefix
       source IDs (b/c/d/g/l/n/o/q/r/s/w + 10-digits) come from Yahoo Auctions
       and are accessible at /item/jdirectitems/auction/<id>
     - Web-search enrichment via Anthropic web_search tool — for items without
       direct auction URLs (V-prefix wholesale lot numbers etc.)
  2. Single Haiku call per item synthesizes refined fields (era, color, model,
     style adjectives, condition).
  3. Aggressive caching to output/listings/<source_id>.json — never re-research.
  4. Photo escalation only if title is still weak after enrichment (separate
     module, expensive, optional).

Cost expectations:
  - Direct scrape: free (just HTTP via Playwright session)
  - Web-search enrichment: ~$0.01-0.02 per item (Haiku + 2-3 search calls)
  - For a 30-item invoice: ~$0.30-0.60 enrichment, run once and cached forever
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field, field_validator

# Auto-load ANTHROPIC_API_KEY from .env so the CLI works regardless of how
# it's invoked. Best-effort — silent if dotenv isn't installed.
try:
    from dotenv import find_dotenv, load_dotenv
    # override=True: the sandbox / shell may have ANTHROPIC_API_KEY="" pre-set
    # which would otherwise block our .env value from loading.
    load_dotenv(find_dotenv(usecwd=True), override=True)
except ImportError:
    pass


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
LISTINGS_DIR = PROJECT_ROOT / "output" / "listings"
LISTINGS_DIR.mkdir(parents=True, exist_ok=True)


# Lowercase prefixes used by Yahoo Auctions IDs (visible on /myorders/bids/successful)
YAHOO_AUCTION_PREFIXES = set("bcdgflnoqrsvw")
# Source IDs starting with these letters + 10+ digits are Yahoo Auctions IDs
# we can fetch directly from Buyee. (Note: 'v' lowercase IS Yahoo, capital 'V'
# is wholesale lot — different namespace.)
DIRECT_FETCH_PATTERN = re.compile(r"^[a-z]\d{8,}$")


def is_direct_fetchable(source_id: str) -> bool:
    """True if source_id is a Yahoo Auctions ID with a Buyee mirror page."""
    return bool(source_id and DIRECT_FETCH_PATTERN.match(source_id))


# ---------------------------------------------------------------------------
# Result schema — what enrichment returns and what we cache
# ---------------------------------------------------------------------------

class EnrichmentResult(BaseModel):
    """Refined fields returned by the LLM, all optional."""
    color: Optional[str] = None
    era: Optional[str] = None
    model_name: Optional[str] = None
    model_size: Optional[str] = None
    material: Optional[str] = None
    pattern: Optional[str] = None
    garment_length: Optional[str] = None
    style_adjectives: Optional[str] = None
    origin: Optional[str] = None
    condition_summary: Optional[str] = None
    title_confidence: Optional[str] = None  # HIGH / MED / LOW
    notes: Optional[str] = None
    sources: list[str] = Field(default_factory=list)  # URLs the model cited

    # Cost / metadata
    method: str = "unknown"  # "direct_listing" | "web_search" | "photo_vision"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None

    # Coerce list-shaped values from the LLM into the expected string shape.
    # Models occasionally return ["Sleeveless"] instead of "Sleeveless", or
    # ["V-Neck", "Long Sleeve"] instead of "V-Neck Long Sleeve".
    @field_validator(
        "color", "era", "model_name", "model_size", "material", "pattern",
        "garment_length", "style_adjectives", "origin", "condition_summary",
        "title_confidence", mode="before",
    )
    @classmethod
    def _coerce_list_to_string(cls, v):
        if isinstance(v, list):
            joined = " ".join(str(x).strip() for x in v if x)
            return joined or None
        return v


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ENRICHMENT_SYSTEM = """You are extracting structured fashion attributes for a vintage resale shop's product titles.

CRITICAL RULES — violations make the data unusable:
1. ONLY return a field if you found EXPLICIT EVIDENCE in search results or the provided description. Never infer from brand origin (Fendi being Italian doesn't mean THIS item was made in Italy).
2. Never add a style adjective that's REDUNDANT with the product type (e.g. don't say "Sleeveless" on a vest, don't say "Long" on a maxi dress, don't say "Wrap" on a wrap top if "Wrap" isn't in the description).
3. If you cannot find specific evidence for a field, leave it null. Empty result is better than wrong data.
4. Use 'Multicolor' only when 3+ distinct colors are clearly visible. Otherwise pick the dominant color or null.

Return strict JSON with these fields:

- color: ONE primary color from {Black, White, Red, Blue, Green, Brown, Beige, Grey, Pink, Purple, Yellow, Orange, Silver, Gold, Burgundy, Navy, Multicolor}. Null unless visible/described.
- era: 4-digit year ("1997") OR decade ("90's", "00's", "10's"). Y2K → "00's". Only fill if a search result confirms the production year/decade — not inferred from "looks 90s style".
- model_name: specific named model from the brand's catalog (Mamma Baguette, Pochette Accessoires, Speedy, Classic Flap, Saddle, Jackie, etc.). Null if generic.
- model_size: bag size code (MM/PM/GM/BB) or numeric size if explicitly stated. Null otherwise.
- material: primary material (Cotton, Leather, Lambskin, Denim, Silk, Cashmere, Nylon, Wool, etc.). Only if confirmed in description/listing.
- pattern: signature pattern (Monogram, Damier, Zucca, Nova Check, GG Canvas, Sherry Line, Floral, Striped). Null if plain.
- garment_length: Short/Mini/Knee/Midi/Maxi/Cropped/Long. Null if N/A or not stated.
- style_adjectives: ordered space-separated descriptors from search/description ONLY. Skip any that are redundant with the product type. Pick AT MOST one from each bucket: silhouette (Belted/Wrap/A-Line/Sheath), neckline (V-Neck/Crew/Mock), sleeve (Long Sleeve/Cap), fabric-detail (Mesh/Pleated/Quilted). Example: "Belted V-Neck Long Sleeve Mesh"
- origin: STRING "Made in [Country]" ONLY if explicitly stated in description or search result quote. Examples: "Made in Italy", "Made in USA", "Made in Japan". Null unless EXPLICIT — never infer from brand nationality.
- condition_summary: ONE short phrase if described in search results. Null if not.
- title_confidence: HIGH if specific era AND model identified from sources. MED if brand+type+2-3 strong attrs from sources. LOW if generic / nothing useful found.
- sources: list of URLs you cited (max 3 most relevant)

Field order in JSON: color, era, model_name, model_size, material, pattern, garment_length, style_adjectives, origin, condition_summary, title_confidence, sources, notes."""


def _build_search_prompt(item) -> str:
    brand = item.detected_brand or "(unknown brand)"
    ptype = item.product_type or "(unknown type)"
    desc_ja = item.description_original or ""
    desc_en = item.description_english or ""
    cond = item.condition_notes or ""
    known = []
    for fname in ("color", "pattern", "material", "era", "model_name", "model_size"):
        v = getattr(item, fname, None)
        if v:
            known.append(f"{fname}={v}")
    known_str = ", ".join(known) if known else "(none)"

    return f"""Research this vintage fashion item using web search.

Brand: {brand}
Type: {ptype}
Japanese description: {desc_ja}
English description: {desc_en}
Condition notes: {cond}
Already-known attributes: {known_str}

Search Yahoo Auctions Japan (auctions.yahoo.co.jp), Mercari Japan (mercari.com/jp), Buyee (buyee.jp), Grailed, eBay, or vintage fashion sites for the SAME or VERY SIMILAR items. Use the Japanese description text in searches — it's the most specific signal.

For named models (Fendi Zucca/Mamma Baguette, LV Pochette, Chanel Cambon, etc.), use the model name + brand for searches.

From the search results, determine:
- Era (decade based on tag style, hardware, label color, model production years)
- Specific color (multicolor only if 3+ visible colors)
- Specific named model if applicable
- Material (be specific — Lambskin not just leather)
- Pattern signature
- Style descriptors

Return ONLY a JSON object with the schema described in the system prompt. No markdown fence, no prose.
"""


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def cached_enrichment(source_id: str) -> Optional[EnrichmentResult]:
    """Return cached enrichment if we've already researched this item."""
    path = LISTINGS_DIR / f"{source_id}.json"
    if not path.exists():
        return None
    try:
        return EnrichmentResult(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_enrichment(source_id: str, result: EnrichmentResult) -> None:
    path = LISTINGS_DIR / f"{source_id}.json"
    path.write_text(
        result.model_dump_json(indent=2, exclude_none=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# LLM enrichment via web search
# ---------------------------------------------------------------------------

# Haiku 4.5 pricing (per 1M tokens) for cost estimation
HAIKU_INPUT_PRICE = 1.0    # $1 per MTok input
HAIKU_OUTPUT_PRICE = 5.0   # $5 per MTok output
SEARCH_PRICE_PER_USE = 0.01  # ~$0.01 per web_search call


def research_via_web_search(
    item,
    client: Optional[anthropic.Anthropic] = None,
    max_searches: int = 3,
    model: str = "claude-haiku-4-5",
) -> EnrichmentResult:
    """Single LLM call with web_search enabled. Returns refined fields.

    Costs ~$0.01-0.03 per item. Result is cached by source_id.
    """
    cached = cached_enrichment(item.source_id)
    if cached:
        return cached

    client = client or anthropic.Anthropic(timeout=120.0, max_retries=2)

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=ENRICHMENT_SYSTEM,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_searches,
            }],
            messages=[{
                "role": "user",
                "content": _build_search_prompt(item),
            }],
        )
    except anthropic.APIError as e:
        result = EnrichmentResult(method="web_search", notes=f"API error: {e}")
        save_enrichment(item.source_id, result)
        return result

    # Extract the model's JSON output. Web-search responses interleave
    # tool_use blocks with text blocks; we want the final text block.
    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "\n".join(text_blocks).strip()

    # Strip markdown fences if present
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()

    # Try to find JSON object boundaries
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        result = EnrichmentResult(
            method="web_search",
            notes=f"JSON parse failed. Raw: {raw[:300]}",
        )
        save_enrichment(item.source_id, result)
        return result

    # Track cost
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    # Count search uses by inspecting the response for server_tool_use blocks
    search_uses = sum(
        1 for b in resp.content
        if getattr(b, "type", "") == "server_tool_use"
    )
    cost = (in_tok * HAIKU_INPUT_PRICE / 1_000_000 +
            out_tok * HAIKU_OUTPUT_PRICE / 1_000_000 +
            search_uses * SEARCH_PRICE_PER_USE)

    # Validate against schema
    try:
        result = EnrichmentResult(
            **{k: v for k, v in data.items() if v is not None and k in EnrichmentResult.model_fields},
            method="web_search",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 4),
        )
    except Exception as e:
        result = EnrichmentResult(
            method="web_search",
            notes=f"Schema validation failed: {e}. Raw: {raw[:300]}",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 4),
        )

    save_enrichment(item.source_id, result)
    return result


# ---------------------------------------------------------------------------
# Title robustness — decides if photo escalation is needed
# ---------------------------------------------------------------------------

def is_title_robust(item) -> tuple[bool, list[str]]:
    """Return (is_robust, reasons_missing). Used to decide if we should
    escalate to a photo vision pass.

    Heuristic: a title is robust if we have brand + product_type + at least 2
    of {era, model_name, color, pattern, material}. Otherwise the title will
    be too generic for SEO.
    """
    if not item.detected_brand:
        return False, ["no brand detected"]
    if not item.product_type:
        return False, ["no product type"]

    strong_signals = []
    if item.era:
        strong_signals.append("era")
    if getattr(item, "model_name", None):
        strong_signals.append("model_name")
    if item.color:
        strong_signals.append("color")
    if item.pattern:
        strong_signals.append("pattern")
    if item.material:
        strong_signals.append("material")

    missing = []
    if "era" not in strong_signals:
        missing.append("era")
    if "color" not in strong_signals and not getattr(item, "model_name", None):
        # Color matters more when there's no model name
        missing.append("color")
    if not strong_signals:
        missing.append("any specific attribute")

    is_robust = len(strong_signals) >= 2
    return is_robust, missing


# ---------------------------------------------------------------------------
# Apply enrichment to an item — only fills nulls, preserves explicit values
# ---------------------------------------------------------------------------

# model_size canonical values — bag/luxury size codes only. Clothing sizes
# (XS/S/M/L, EU 38-44, US 0-12) leak from listings if we don't gate this:
# the title would render "Vivienne Tam 0 Black ..." which looks like a typo.
_VALID_MODEL_SIZE_CODES = {"MM", "PM", "GM", "BB"}
# Bag-like product types where numeric sizes (Birkin 25/30/35, Speedy 25/30/35)
# are legit. Outside these, numbers are clothing sizes.
_BAG_TYPES = {
    "Handbag", "Shoulder Bag", "Clutch Bag", "Clutch", "Tote Bag",
    "Hobo Bag", "Pouch", "Belt Bag", "Bag", "Sunglasses",
}


def _validate_model_size(val: str, ptype: Optional[str]) -> Optional[str]:
    """Return val if it's a legit bag size code, else None.

    Accepts: MM/PM/GM/BB always; numeric (25, 30, 35, 40) only if product is
    a bag/sunglasses where numeric sizes correspond to model lines.
    Rejects: clothing sizes (0, 2, 38, XS, S, M, L, etc.) on apparel.
    """
    if not isinstance(val, str):
        return None
    v = val.strip().upper()
    if v in _VALID_MODEL_SIZE_CODES:
        return v
    # Numeric: only allow on bag-type products
    if v.isdigit():
        from pricing import canon_type
        if canon_type(ptype) in _BAG_TYPES and 20 <= int(v) <= 45:
            return v
    return None


def _clean_material(val: str) -> Optional[str]:
    """Strip hedging text from material strings.

    Models occasionally return: "Cotton or Cotton Blend", "Cotton Canvas, Leather",
    "Wool/Polyester". We pick the first listed material — best single value
    for the title — and drop the rest.
    """
    if not isinstance(val, str):
        return None
    v = val.strip()
    if not v:
        return None
    # Split on hedging delimiters and take the first
    for delim in [" or ", ",", "/", " and "]:
        if delim in v.lower():
            v = re.split(delim, v, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    # Strip "Blend" qualifier on its own ("Cotton Blend" becomes "Cotton")
    # Only when "Blend" is the trailing word
    parts = v.split()
    if len(parts) >= 2 and parts[-1].lower() == "blend":
        v = " ".join(parts[:-1])
    return v or None


# Style-adjective tokens that are redundant with specific product types.
# Skip applying these when the type already implies them.
_REDUNDANT_BY_TYPE = {
    "Vest":           {"Sleeveless"},
    "T-Shirt":        {"Short Sleeve"},
    "Tank":           {"Sleeveless"},
    "Tank Top":       {"Sleeveless"},
    "Trench Coat":    {"Belted"},  # trench coats are by definition belted
    "Wrap Dress":     {"Wrap"},
    "Maxi Dress":     {"Long", "Maxi"},
    "Mini Dress":     {"Short", "Mini"},
}


def _filter_redundant_styles(style_str: str, ptype: Optional[str]) -> str:
    """Strip style adjective tokens that are redundant with the product type."""
    if not style_str or not ptype:
        return style_str
    redundant = _REDUNDANT_BY_TYPE.get(ptype, set())
    if not redundant:
        return style_str
    tokens = style_str.split()
    kept = []
    skip_next = 0
    for i, tok in enumerate(tokens):
        if skip_next > 0:
            skip_next -= 1
            continue
        # Try multi-word match (longest first)
        for n in (3, 2, 1):
            phrase = " ".join(tokens[i:i+n])
            if phrase in redundant:
                skip_next = n - 1
                break
        else:
            kept.append(tok)
            continue
    return " ".join(kept).strip()


# ---------------------------------------------------------------------------
# Photo escalation — read the PDF directly with vision when web search failed
# ---------------------------------------------------------------------------

PHOTO_ESCALATION_SYSTEM = """You are extracting structured fashion attributes from a vintage invoice PDF.

The PDF contains many items each with thumbnail photos and a Japanese description. You will be given a specific source_id to focus on — find that item in the PDF and analyze its thumbnail photo + description.

Same field rules as before, but now grounded in what you actually SEE in the photo:
- color: pick the dominant color you actually see. Use 'Multicolor' only if 3+ distinct colors.
- material: identify from the photo (denim, cotton, leather, knit, mesh, etc.) when possible.
- pattern: visible patterns (Monogram, Zucca, floral, striped, plaid).
- style_adjectives: visible details (Long Sleeve, Sleeveless, V-Neck, Cropped, Belted, etc.) — skip any that are redundant with the product type.
- garment_length: visible length (Short/Mini/Knee/Midi/Maxi/Cropped). For tops only, "Cropped" if it ends above the waist.
- model_name: only if visibly identifiable from logo/tag.
- era: only if you have direct evidence (tag style, label colors, hardware).
- origin: ONLY if you can read "Made in [Country]" on a visible tag/label.
- title_confidence: HIGH if photo is clear and you got 3+ specific attributes, MED if 1-2, LOW if photo is unclear.

Return strict JSON with the same field schema. NO markdown fence."""


def escalate_with_pdf(
    item,
    pdf_path: Path,
    client: Optional[anthropic.Anthropic] = None,
    model: str = "claude-haiku-4-5",
) -> EnrichmentResult:
    """Send the source PDF to a vision-capable LLM, ask it to focus on one item.

    Cost: ~$0.01-0.03/item (PDF is charged per page; Haiku 4.5 is cheapest).
    Use this when web_search returned LOW confidence — the photo is the
    last-resort source of truth.
    """
    if not pdf_path.exists():
        return EnrichmentResult(method="photo_vision", title_confidence="LOW",
                                notes=f"PDF not found at {pdf_path}")

    client = client or anthropic.Anthropic(timeout=120.0, max_retries=2)
    pdf_bytes = pdf_path.read_bytes()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")

    prompt = f"""Find the item with source_id "{item.source_id}" in the attached invoice PDF.

Already-known fields (don't override these, just add to them):
  brand: {item.detected_brand or '?'}
  type: {item.product_type or '?'}
  description: {item.description_original or item.description_english or '?'}
  pattern: {item.pattern or '(none yet)'}
  material: {item.material or '(none yet)'}
  color: {item.color or '(none yet)'}
  era: {item.era or '(none yet)'}

Look at the thumbnail photo for this item in the PDF. Return JSON with the refined fields you can determine FROM THE PHOTO ITSELF (color, visible details, length, material if visible). Don't repeat what's already known unless your visual confirms or refines it."""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=PHOTO_ESCALATION_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except anthropic.APIError as e:
        return EnrichmentResult(method="photo_vision", title_confidence="LOW",
                                notes=f"API error: {e}")

    text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "\n".join(text_blocks).strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return EnrichmentResult(method="photo_vision", title_confidence="LOW",
                                notes=f"JSON parse failed. Raw: {raw[:300]}")

    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = (in_tok * HAIKU_INPUT_PRICE / 1_000_000 +
            out_tok * HAIKU_OUTPUT_PRICE / 1_000_000)

    try:
        return EnrichmentResult(
            **{k: v for k, v in data.items() if v is not None and k in EnrichmentResult.model_fields},
            method="photo_vision",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 4),
        )
    except Exception as e:
        return EnrichmentResult(method="photo_vision", title_confidence="LOW",
                                input_tokens=in_tok, output_tokens=out_tok,
                                cost_usd=round(cost, 4),
                                notes=f"Schema error: {e}. Raw: {raw[:300]}")


def apply_enrichment(item, result: EnrichmentResult, skip_low_confidence: bool = True) -> dict:
    """Merge enrichment into item, only overwriting null fields. Returns stats.

    skip_low_confidence: when True, LOW-confidence results don't apply.
    Avoids polluting good data with hallucinated/inferred fields.
    """
    if skip_low_confidence and (result.title_confidence or "").upper() == "LOW":
        return {"_skipped": "low_confidence"}

    # Note: origin is intentionally excluded from web_search apply. The LLM
    # tends to infer origin from brand nationality (Fendi → Italy) even when
    # the item's actual provenance is unstated. Origin is only applied from
    # photo-vision passes (where we can read the "Made in X" label) or the
    # original PDF transcription.
    fields = ["color", "era", "model_name", "model_size", "material", "pattern",
              "garment_length", "style_adjectives"]
    filled = {}
    for f in fields:
        new_val = getattr(result, f, None)
        if not new_val:
            continue
        # Don't overwrite explicit values from the original transcription
        existing = getattr(item, f, None)
        if existing:
            continue
        # Reject placeholder / "i don't know" strings the model sometimes returns
        # despite our prompt instructions
        if isinstance(new_val, str):
            low = new_val.strip().lower()
            JUNK_VALUES = {
                "null", "none", "", "n/a", "na", "unknown", "not specified",
                "not stated", "not visible", "not determinable",
                "unable to determine", "none visible", "indeterminate",
                "cannot determine", "not applicable",
            }
            if low in JUNK_VALUES:
                continue
            # Also reject anything starting with "unable" / "cannot" — common
            # phrasings the model uses when it doesn't know
            if low.startswith(("unable to", "cannot determine",
                               "not determinable", "not visible", "no ")):
                continue
        # Reject "model_name == brand" — meaningless
        if f == "model_name" and isinstance(new_val, str):
            from pricing import canon_brand
            if canon_brand(new_val) == canon_brand(item.detected_brand):
                continue
        # Filter style adjectives redundant with product type
        if f == "style_adjectives":
            from pricing import canon_type
            new_val = _filter_redundant_styles(new_val, canon_type(item.product_type))
            if not new_val:
                continue
        # model_size: only allow valid bag size codes (MM/PM/GM/BB) or
        # numeric sizes on bag-type products. Reject clothing sizes (0/40/XS).
        if f == "model_size":
            cleaned = _validate_model_size(new_val, item.product_type)
            if not cleaned:
                continue
            new_val = cleaned
        # pattern: skip if the existing style_adjectives already imply the
        # same concept. We check a small synonym table for known overlaps
        # (Flower↔Floral, Stripe↔Striped) plus a substring fallback.
        if f == "pattern" and isinstance(new_val, str):
            existing_style = (getattr(item, "style_adjectives", None) or "").lower()
            if existing_style:
                _SYNONYMS = {
                    "floral": "flower", "flower": "floral",
                    "striped": "stripe", "stripe": "striped",
                    "checked": "check", "check": "checked",
                    "plaid": "tartan", "tartan": "plaid",
                }
                p_low = new_val.lower()
                # Substring: pattern's stem in style
                p_stem = p_low.rstrip("s")[:5]
                if p_stem and p_stem in existing_style:
                    continue
                # Synonym: pattern → other word, look in style
                synonym = _SYNONYMS.get(p_low)
                if synonym and synonym in existing_style:
                    continue
        # material: strip hedging text ("Cotton or Cotton Blend" → "Cotton")
        if f == "material":
            cleaned = _clean_material(new_val)
            if not cleaned:
                continue
            new_val = cleaned
        # Origin: only apply if explicitly "Made in X" formatted
        if f == "origin":
            if not isinstance(new_val, str) or not new_val.lower().startswith("made in"):
                continue
        if isinstance(new_val, str):
            new_val = new_val.strip()
        setattr(item, f, new_val)
        filled[f] = new_val

    # Condition summary appends to condition_notes if it adds info
    if result.condition_summary and not item.condition_notes:
        item.condition_notes = result.condition_summary
        filled["condition_notes"] = result.condition_summary

    return filled


# ---------------------------------------------------------------------------
# Orchestrator — run enrichment on every item in an invoice + persist results
# ---------------------------------------------------------------------------

def enrich_invoice(invoice_path: Path, dry_run: bool = False,
                   only_ids: Optional[list[str]] = None,
                   max_cost_usd: float = 5.0) -> dict:
    """Run web-search + photo-escalation enrichment on every item in an invoice.

    Cached results are reused — items already in output/listings/ don't re-run.

    Args:
        invoice_path: path to a transcribed invoice JSON
        dry_run: if True, don't write enriched JSON back to disk
        only_ids: if given, restrict to these source_ids (else all items)
        max_cost_usd: hard cost ceiling. Stop enriching once exceeded.

    Returns: stats dict {items_processed, items_enriched, items_unchanged,
             total_cost_usd, errors, before_titles, after_titles}
    """
    from costs import Invoice
    from pricing import compose_title
    import anthropic

    data = json.loads(invoice_path.read_text(encoding="utf-8"))
    inv = Invoice(**{k: v for k, v in data.items() if not k.startswith("_")})

    # PDF is needed for photo escalation. Look in standard locations.
    pdf_candidates = [
        invoice_path.parent.parent / "samples" / f"{invoice_path.stem}.pdf",
        invoice_path.parent.parent / "inputs" / f"{invoice_path.stem}.pdf",
        invoice_path.parent.parent / "inputs" / "buyee" /
            (data.get("invoice_number", "") and f"buyee_{data['invoice_number']}.pdf" or "_"),
    ]
    pdf_path = next((p for p in pdf_candidates if p.exists()), None)

    client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    stats = {
        "items_processed": 0, "items_enriched": 0, "items_unchanged": 0,
        "total_cost_usd": 0.0, "errors": 0,
        "before_titles": {}, "after_titles": {},
    }

    for item in inv.items:
        if only_ids and item.source_id not in only_ids:
            continue
        if stats["total_cost_usd"] >= max_cost_usd:
            print(f"[enrich] Cost ceiling ${max_cost_usd:.2f} hit; stopping.")
            break

        stats["items_processed"] += 1
        before = compose_title(item)
        stats["before_titles"][item.source_id] = before

        # Stage 1: web search (cached if already done)
        r1 = research_via_web_search(item, client=client)
        if r1.cost_usd:
            stats["total_cost_usd"] += r1.cost_usd
        if r1.notes and "error" in r1.notes.lower():
            stats["errors"] += 1
        apply_enrichment(item, r1)

        # Stage 2: photo escalation if title still weak
        robust, missing = is_title_robust(item)
        if (not robust or "era" in missing) and pdf_path:
            r2 = escalate_with_pdf(item, pdf_path, client=client)
            if r2.cost_usd:
                stats["total_cost_usd"] += r2.cost_usd
            apply_enrichment(item, r2, skip_low_confidence=False)

        after = compose_title(item)
        stats["after_titles"][item.source_id] = after
        if before != after:
            stats["items_enriched"] += 1
        else:
            stats["items_unchanged"] += 1

    # Persist enriched item fields back to the invoice JSON
    if not dry_run:
        # Update each item dict in-place by source_id match
        items_by_sid = {it.source_id: it for it in inv.items}
        for raw_item in data["items"]:
            typed = items_by_sid.get(raw_item.get("source_id"))
            if not typed:
                continue
            for f in ("color", "era", "model_name", "model_size", "material",
                      "pattern", "garment_length", "style_adjectives",
                      "origin", "condition_notes"):
                v = getattr(typed, f, None)
                if v:
                    raw_item[f] = v
        invoice_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return stats
