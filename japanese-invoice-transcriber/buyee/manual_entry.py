"""Manual item entry via Telegram — text-only, multi-language.

User flow:
    From phone, send the bot:
        add Borsa di pelle Gucci nera vintage 250 EUR
        add Vintage Burberry trench beige 800€
        add Cappotto Prada anni '90 600 euro
        add Black silk Chanel blouse 90s 320 USD

The bot:
  1. Parses the trailing price + currency (EUR/€, USD/$, GBP/£, JPY/¥)
  2. Sends the description (any language) to Haiku for translation +
     structured-field extraction (brand, type, color, material, era, etc.)
  3. Appends the item to a monthly per-currency manual invoice
     (output/manual_<YYYY-MM>_<CCY>.json), creating it if needed
  4. Replies with the parsed item summary

The resulting invoice flows through the existing pipeline — open it in the
Streamlit app to QA, price, generate Shopify CSV.

Cost: ~$0.001-0.005 per item via Haiku. Negligible.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Optional

import anthropic

# Auto-load .env so ANTHROPIC_API_KEY is available regardless of how invoked.
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=True)
except ImportError:
    pass


HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

# Approximate exchange rates → USD. Used to pre-fill the invoice's
# exchange_rate field so the UI shows reasonable USD totals out of the box.
# User can override in the FX control. These are the only place we hardcode
# rates; the UI always wins for actual cost math.
APPROX_TO_USD = {
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "USD": 1.0,
    "CHF": 1.10,
    "AUD": 0.65,
    "CAD": 0.74,
}

# Currency markers we accept at the trailing end of an `add` message.
# Each entry: (regex pattern, ISO 4217 code).
_CURRENCY_PATTERNS = [
    (r"€", "EUR"),
    (r"\bEUR\b", "EUR"),
    (r"\beuros?\b", "EUR"),
    (r"\beuro\b", "EUR"),
    (r"\$", "USD"),
    (r"\bUSD\b", "USD"),
    (r"\busd\b", "USD"),
    (r"\bdollars?\b", "USD"),
    (r"£", "GBP"),
    (r"\bGBP\b", "GBP"),
    (r"\bpounds?\b", "GBP"),
    (r"¥", "JPY"),
    (r"\bJPY\b", "JPY"),
    (r"\byen\b", "JPY"),
    (r"\bCHF\b", "CHF"),
    (r"\bAUD\b", "AUD"),
    (r"\bCAD\b", "CAD"),
]


# ---------------------------------------------------------------------------
# Parsing — strip the trailing "<number> <currency>" off the message
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
)


def _normalize_number(s: str) -> Optional[float]:
    """Parse '1,234.56' or '1.234,56' or '1234' or '250.00' → float.

    European format uses '.' as thousands and ',' as decimal; English uses
    the inverse. We disambiguate by which separator appears last.
    """
    s = s.strip()
    if not s:
        return None
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        # Whichever appears LAST is the decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # European
        else:
            s = s.replace(",", "")  # English thousands
    elif has_comma:
        # Single comma — treat as decimal if 1-2 digits after it, else thousands
        parts = s.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_price_and_currency(text: str) -> Optional[tuple[str, float, str]]:
    """Strip trailing price + currency from `text`.

    Returns (description, price, currency) or None if no price found.
    Currency defaults to EUR if the message contains common Italian words and
    no explicit currency marker; otherwise defaults to USD.
    """
    if not text or not text.strip():
        return None

    # Find the trailing currency marker (if any)
    currency = None
    explicit_currency_match = None
    for pattern, ccy in _CURRENCY_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            # Prefer the LAST match (currencies are usually trailing)
            explicit_currency_match = m
            currency = ccy
    # Strip the currency marker from the working string for number search
    working = text
    if explicit_currency_match:
        working = text[: explicit_currency_match.start()] + " " + text[explicit_currency_match.end():]

    # Find the LAST number in the (currency-stripped) string
    number_matches = list(_NUMBER_RE.finditer(working))
    if not number_matches:
        return None
    last_num_match = number_matches[-1]
    price = _normalize_number(last_num_match.group(0))
    if price is None or price <= 0:
        return None

    # Description is everything before the last number (and currency)
    desc_end = min(last_num_match.start(), explicit_currency_match.start() if explicit_currency_match else len(text))
    description = text[:desc_end].strip(" ,.;:-")

    if not currency:
        # Heuristic default based on language
        if _looks_italian(description):
            currency = "EUR"
        elif _looks_japanese(description):
            currency = "JPY"
        else:
            currency = "USD"

    return description, price, currency


# Common Italian fashion-vocabulary stems used as a quick language hint.
_ITALIAN_HINTS = {
    "borsa", "cappotto", "camicia", "camicetta", "vestito", "abito", "pelle",
    "seta", "cotone", "lana", "anni", "vintage", "nero", "nera", "rosso",
    "rossa", "bianco", "bianca", "blu", "verde", "giallo", "grigio", "oro",
    "argento", "marrone", "beige", "scarpe", "stivali", "borsetta", "giacca",
    "gonna", "pantaloni", "cintura", "occhiali", "sciarpa", "guanti", "del",
    "della", "dello", "di",
}


def _looks_italian(text: str) -> bool:
    tokens = re.findall(r"\w+", (text or "").lower())
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in _ITALIAN_HINTS)
    return hits >= 1 and hits / len(tokens) > 0.05


def _looks_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text or ""))


# ---------------------------------------------------------------------------
# LLM extraction — translate + extract structured fields via Haiku
# ---------------------------------------------------------------------------

_HAIKU_MODEL = "claude-haiku-4-5"

_EXTRACT_SYSTEM = """You extract structured fashion-item attributes from descriptions in any language (Italian, English, French, Japanese, Spanish, German). Translate to English and identify standard fields.

Return strict JSON with these fields. Use null for anything you can't determine.

- description_english:   string, clean English description (~10 words). Translate idiomatically.
- detected_brand:        string. Canonical brand name. Null if generic/unbranded.
- product_type:          short generic noun. Examples: 'handbag', 'coat', 'trench coat', 'dress', 'shirt', 'shoulder bag', 'wallet', 'sunglasses', 'pouch', 'shoes', 'boots', 'scarf', 'belt'. Null if unclear.
- color:                 ONE primary color from {Black, White, Red, Blue, Green, Brown, Beige, Grey, Pink, Purple, Yellow, Orange, Silver, Gold, Burgundy, Navy, Multicolor}. Null if unstated.
- material:              {Leather, Lambskin, Cotton, Silk, Wool, Cashmere, Denim, Nylon, Polyester, Suede, Patent Leather, Pony Hair, Fur, Mink, Fox Fur, Shearling}. Null if unstated.
- pattern:               {Monogram, Damier, Zucca, Nova Check, GG Canvas, Floral, Striped, Plaid, Polka Dot}. Null if plain.
- era:                   4-digit year ('1997') OR decade ("90's", "00's", "10's"). Null if unstated.
- model_name:            specific named model (Mamma Baguette, Speedy, Jackie, Classic Flap, etc.). Null if generic.
- model_size:            bag size code (MM/PM/GM/BB) or numeric (25/30/35) only when clearly a bag-model size. Null otherwise.
- garment_length:        Short/Mini/Knee/Midi/Maxi/Cropped/Long. Null if N/A.
- origin:                "Made in [Country]" only if explicitly stated. Null otherwise.
- condition_notes:       Short string if condition is described ("excellent", "vintage with patina"). Null otherwise.

Only return facts present in the input. NEVER invent. NEVER infer origin from brand nationality. Return ONLY the JSON object — no markdown fence, no prose."""


def extract_item_fields_via_llm(
    description: str,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    """Send `description` to Haiku, return structured-fields dict.

    Always returns a dict — never raises. On failure, returns {description_english: <as-is>}
    so the caller can still create an item entry with whatever the user typed.
    """
    if not description.strip():
        return {"description_english": ""}

    client = client or anthropic.Anthropic(timeout=60.0, max_retries=2)
    try:
        resp = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=600,
            system=_EXTRACT_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Extract fields from: {description}",
            }],
        )
    except Exception as e:
        return {"description_english": description, "_error": f"LLM call failed: {e}"}

    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    raw = text.strip()
    # Strip code fences defensively
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"description_english": description, "_error": f"JSON parse failed: {e}"}

    # Strip nulls so the resulting LineItem doesn't have explicit None overwriting defaults
    return {k: v for k, v in data.items() if v not in (None, "", "null", "Null")}


# ---------------------------------------------------------------------------
# Invoice append — find or create the monthly per-currency manual invoice
# ---------------------------------------------------------------------------

def _manual_invoice_path(currency: str, when: Optional[_dt.date] = None) -> Path:
    """Path: output/manual_<YYYY-MM>_<CCY>.json (one file per currency per month)."""
    when = when or _dt.date.today()
    return OUTPUT_DIR / f"manual_{when.strftime('%Y-%m')}_{currency.upper()}.json"


def _create_empty_invoice(currency: str) -> dict:
    today = _dt.date.today()
    return {
        "invoice_type": "vendor_invoice",
        "vendor_name": "Manual Entry",
        "invoice_number": f"MANUAL-{today.strftime('%Y-%m')}-{currency}",
        "invoice_date": today.isoformat(),
        "currency": currency,
        "items": [],
        "commission_fees": [],
        "domestic_shipping_fees": [],
        "service_fees": [],
        "international_shipping": 0,
        "customs_duty": 0,
        "commission_line": 0,
        "other_fees": 0,
        "tax": 0,
        "grand_total": 0.0,
        "exchange_rate": APPROX_TO_USD.get(currency.upper(), 1.0),
    }


def _next_source_id(existing_items: list[dict], date: _dt.date) -> str:
    """Generate a unique source_id for a manual entry on this date.

    Format: M<YYMMDD>-<NN> where NN is a 2-digit sequence within the day.
    """
    prefix = f"M{date.strftime('%y%m%d')}-"
    used_seqs = set()
    for it in existing_items:
        sid = it.get("source_id", "")
        if sid.startswith(prefix):
            try:
                used_seqs.add(int(sid[len(prefix):]))
            except ValueError:
                pass
    seq = 1
    while seq in used_seqs:
        seq += 1
    return f"{prefix}{seq:02d}"


def append_manual_item(
    description_input: str,
    price: float,
    currency: str,
    structured: dict,
) -> tuple[Path, dict]:
    """Append a new item to the monthly manual invoice for this currency.

    Returns (invoice_path, item_dict).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _manual_invoice_path(currency)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = _create_empty_invoice(currency)
    else:
        data = _create_empty_invoice(currency)

    today = _dt.date.today()
    source_id = _next_source_id(data.get("items", []), today)

    item = {
        "source_id": source_id,
        "description_original": description_input,
        "description_english": structured.get("description_english") or description_input,
        "detected_brand": structured.get("detected_brand"),
        "product_type": structured.get("product_type"),
        "color": structured.get("color"),
        "material": structured.get("material"),
        "pattern": structured.get("pattern"),
        "era": structured.get("era"),
        "model_name": structured.get("model_name"),
        "model_size": structured.get("model_size"),
        "garment_length": structured.get("garment_length"),
        "origin": structured.get("origin"),
        "condition_notes": structured.get("condition_notes"),
        "quantity": 1,
        "currency": currency,
        "item_price": float(price),
        "coupon_discount": 0,
    }
    # Drop None/empty so Pydantic uses field defaults when reloading
    item = {k: v for k, v in item.items() if v not in (None, "", [])}

    data.setdefault("items", []).append(item)
    # Update grand_total to reflect the new sum
    data["grand_total"] = sum(
        (it.get("item_price", 0) - it.get("coupon_discount", 0)) * it.get("quantity", 1)
        for it in data["items"]
    )

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path, item


# ---------------------------------------------------------------------------
# Multi-item parsing — the user pastes a vendor's free-form purchase message
# ---------------------------------------------------------------------------

_MULTI_ITEM_SYSTEM = """You parse multi-item fashion-purchase messages (any language) into a structured invoice.

Input format: a free-form chat-style message describing items for sale. It may contain:
- Section headers ("2 giacche" = 2 jackets, "3 pantaloni" = 3 pants, "7 completi" = 7 sets/outfits)
- Indented sub-items prefixed by '-' or '.' or '*' or numbered lines, with prices
- Inline conversation (greetings, totals, requests, sign-offs) — IGNORE these entirely
- A subtotal line ("Il totale per i 21 pezzi sono € 3070")
- A final-after-discount line ("con lo sconto sarebbe €2800")

Return strict JSON:
{
  "currency": "EUR" | "USD" | "GBP" | "JPY" | "CHF",
  "listed_subtotal": <sum of listed item prices, number>,
  "final_total": <amount actually paid after any discount, number; null if not stated>,
  "items": [
    {
      "description_original": "<exactly the line as written, no edits>",
      "description_english": "<clean concise English translation, ~6-12 words>",
      "detected_brand": "<canonical brand>" | null,
      "product_type": "handbag|coat|trench coat|dress|shirt|jacket|vest|pants|skirt|top|set|...",
      "color": "Black|White|Red|Blue|Green|Brown|Beige|Grey|Pink|Purple|Yellow|Orange|Silver|Gold|Burgundy|Navy|Multicolor" | null,
      "material": "Leather|Lambskin|Suede|Cotton|Silk|Wool|Cashmere|Denim|Nylon|Sequin|Feather|Fur|..." | null,
      "pattern": "Floral|Striped|Plaid|Tiger|Leopard|Crocodile|Tattoo|..." | null,
      "era": "<4-digit year>" | "<decade like 80's>" | null,
      "garment_length": "Mini|Knee|Midi|Maxi|Long|Cropped" | null,
      "model_name": "<specific named model>" | null,
      "item_price": <listed price as a positive number>
    }
  ],
  "notes": "<any extra context like vendor name or pickup instructions>" | null
}

Rules — read carefully:

1. Group section descriptor with each sub-item. "2 giacche\\n- Tiger Tattoo €220" produces ONE item with description_english="Tiger Tattoo Jacket", product_type="jacket". Generate one item per sub-line, NOT one per section.

2. Ignore all conversational filler ("OK Katherine", "Aspetto conferma", "un bacio", "potrebbero partire", "calcola la dimensione"). Only keep item lines.

3. "completi" = "set" / "outfit" (typically top+bottom). product_type="set". When the line lists specific pieces (e.g. "Top - gonna pelle anni 80'"), include them in description_english as "Top + Leather Skirt Set" with era="80's" / material="Leather".

4. Brand recognition (Italian fashion vendor vocabulary):
   - "Cavalli Class", "Roberto Cavalli", "Cavalli Jeans", "R. Cavalli" all → "Roberto Cavalli"
   - "Just C." → "Just Cavalli"
   - "Moschino", "Prada", "Gucci", "Fendi", "Dolce & Gabbana" → as-is

5. NOT brands (these are pattern / material descriptors, not brand names):
   - "Tiger Tattoo", "Fiori tattoo", "Tattoo" → pattern="Tattoo"
   - "Coccodrillo" → pattern="Crocodile"
   - "Maculata" → pattern="Leopard"
   - "Paillettes" → material="Sequin"
   - "Piume" → material="Feather"
   - "Art Collection" → not a brand, leave detected_brand=null

6. Italian → English vocabulary cheat sheet:
   giacca/giacche=jacket, cappotto=coat, trench=trench coat, gilet=vest,
   maglietta=t-shirt, abito/abiti=dress, pantalone/pantaloni=pants,
   camicia/camicie=shirt, gonna=skirt, top=top, completo/completi=set,
   pelle=leather, seta=silk, cotone=cotton, lana=wool,
   nero/nera=black, verde=green, rosa=pink, blu=blue, bianco=white,
   anni '80=80's, anni '90=90's

7. NEVER invent. Use null when uncertain.

8. Prices may have currency markers attached or separated:
   "€220", "€  60", "Trench Moschino€120" — all valid.
   Always extract a positive number.

Return ONLY the JSON object — no markdown fence, no prose."""


def parse_multi_item_message(
    text: str,
    client: Optional[anthropic.Anthropic] = None,
    model: str = _HAIKU_MODEL,
) -> dict:
    """Single LLM call that parses a multi-item purchase message.

    Returns the parsed dict (with `items` array, currency, totals).
    Returns {"items": [], "_error": ...} on failure.
    """
    if not text or not text.strip():
        return {"items": [], "_error": "empty input"}

    client = client or anthropic.Anthropic(timeout=120.0, max_retries=2)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=_MULTI_ITEM_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Parse this purchase message:\n\n{text}",
            }],
        )
    except Exception as e:
        return {"items": [], "_error": f"LLM call failed: {e}"}

    out = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    raw = out.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"items": [], "_error": f"JSON parse failed: {e}", "_raw": raw[:300]}

    return data


def process_multi_item_message(text: str) -> dict:
    """End-to-end: parse a multi-item message + save as a manual invoice JSON.

    Result shape:
        {ok, invoice_path, items_count, currency, listed_subtotal,
         final_total, discount, error}
    """
    parsed = parse_multi_item_message(text)
    if parsed.get("_error"):
        return {"ok": False, "error": parsed["_error"]}
    items = parsed.get("items") or []
    if not items:
        return {"ok": False, "error": "No items extracted from the message."}

    currency = (parsed.get("currency") or "EUR").upper()
    today = _dt.date.today()

    listed_subtotal = sum(float(i.get("item_price") or 0) for i in items)
    final_total = parsed.get("final_total")
    if final_total is None:
        final_total = listed_subtotal
    final_total = float(final_total)

    # Distribute any discount proportionally as per-item coupon_discount
    # so item-level cost basis reflects the deal you actually got.
    discount_total = max(listed_subtotal - final_total, 0)
    discount_ratio = (discount_total / listed_subtotal) if listed_subtotal else 0.0

    invoice = _create_empty_invoice(currency)
    invoice["vendor_name"] = "Manual Entry (multi-item)"
    invoice["invoice_date"] = today.isoformat()
    invoice["invoice_number"] = (
        f"MANUAL-{today.strftime('%Y%m%d')}-"
        f"{_dt.datetime.now().strftime('%H%M%S')}-{currency}"
    )
    if parsed.get("notes"):
        invoice["notes"] = parsed["notes"]

    for parsed_item in items:
        listed_price = float(parsed_item.get("item_price") or 0)
        if listed_price <= 0:
            continue
        coupon = round(listed_price * discount_ratio, 2)
        new_item = {
            "source_id": _next_source_id(invoice["items"], today),
            "description_original": parsed_item.get("description_original") or "",
            "description_english": parsed_item.get("description_english") or "",
            "detected_brand":   parsed_item.get("detected_brand"),
            "product_type":     parsed_item.get("product_type"),
            "color":            parsed_item.get("color"),
            "material":         parsed_item.get("material"),
            "pattern":          parsed_item.get("pattern"),
            "era":              parsed_item.get("era"),
            "model_name":       parsed_item.get("model_name"),
            "garment_length":   parsed_item.get("garment_length"),
            "quantity":         1,
            "currency":         currency,
            "item_price":       listed_price,
            "coupon_discount":  coupon,
        }
        new_item = {k: v for k, v in new_item.items() if v not in (None, "", [])}
        invoice["items"].append(new_item)

    # grand_total = what the user actually paid (post-discount)
    invoice["grand_total"] = final_total

    # Save with a timestamped filename so consecutive multi-item messages
    # don't clobber each other
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"manual_invoice_{timestamp}_{currency}.json"
    path.write_text(
        json.dumps(invoice, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "invoice_path": str(path),
        "items_count": len(invoice["items"]),
        "currency": currency,
        "listed_subtotal": listed_subtotal,
        "final_total": final_total,
        "discount": discount_total,
        "notes": parsed.get("notes"),
    }


# ---------------------------------------------------------------------------
# High-level entry point used by the Telegram bot (single-item `add`)
# ---------------------------------------------------------------------------

def process_add_message(text: str) -> dict:
    """Full pipeline for a single `add` message. Returns a result dict.

    Result shape:
        {ok, invoice_path, item, price, currency, structured, error}
    """
    parsed = parse_price_and_currency(text)
    if not parsed:
        return {"ok": False, "error":
                "Couldn't find a price in the message. Try: `add <description> 250 EUR`"}
    description, price, currency = parsed

    structured = extract_item_fields_via_llm(description)
    invoice_path, item = append_manual_item(description, price, currency, structured)

    return {
        "ok": True,
        "invoice_path": str(invoice_path),
        "item": item,
        "price": price,
        "currency": currency,
        "structured": structured,
    }
