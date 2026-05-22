"""Regex fallbacks for fields the LLM sometimes misses.

The LLM is the primary extractor (prompt in transcribe.py covers it). These
functions run post-transcription, only filling in when the LLM returned null.
Runs on `description_original + description_english` so both Japanese and
English hits work.
"""
from __future__ import annotations

import re
from typing import Optional

# Order matters — more specific patterns first (Fox Fur before Fur, Lambskin before Sheepskin, etc.)
MATERIAL_PATTERNS: list[tuple[str, str]] = [
    # Premium furs (spec §10)
    ("Fox Fur",       r"\b(?:fox fur|フォックス\s*ファー|ブルーフォックス)\b"),
    ("Mink",          r"\b(?:mink|ミンク)\b"),
    ("Weasel Fur",    r"\b(?:weasel fur|ウィーゼル|イタチ)\b"),
    ("Raccoon Fur",   r"\b(?:raccoon fur|ラクーン)\b"),
    ("Squirrel Fur",  r"\b(?:squirrel fur|リス)\b"),
    ("Rabbit Fur",    r"\b(?:rabbit fur|ラビット|ファー付き)\b"),
    # Fur family
    ("Shearling",     r"\b(?:shearling|ムートン|mouton)\b"),
    ("Sheepskin",     r"\b(?:sheepskin|シープスキン)\b"),
    ("Lambskin",      r"\b(?:lambskin|ラム\s*スキン|ラム革)\b"),
    ("Fur",           r"\b(?:real fur|毛皮|リアル\s*ファー|\bfur\b|ファー)\b"),
    # Leather family
    ("Pony Hair",     r"\b(?:pony hair|ハラコ|ポニー\s*ヘア)\b"),
    ("Goat Leather",  r"\b(?:goat leather|ゴート)\b"),
    ("Suede",         r"\b(?:suede|スエード)\b"),
    ("Leather",       r"\b(?:leather|レザー|革|皮)\b"),
    # Silks and luxe
    ("Cashmere",      r"\b(?:cashmere|カシミヤ|カシミア)\b"),
    ("Silk",          r"\b(?:silk|シルク|絹)\b"),
    ("Satin",         r"\b(?:satin|サテン)\b"),
    # Basics
    ("Denim",         r"\b(?:denim|デニム)\b"),
    ("Cotton",        r"\b(?:cotton|コットン|綿)\b"),
    ("Wool",          r"\b(?:\bwool\b|ウール|毛)\b"),
    ("Linen",         r"\b(?:linen|リネン|麻)\b"),
    ("Nylon",         r"\b(?:nylon|ナイロン)\b"),
    ("Polyester",     r"\b(?:polyester|ポリエステル)\b"),
]

GARMENT_LENGTH_PATTERNS: list[tuple[str, str]] = [
    # long
    ("long",  r"\b(?:long|ロング|maxi|マキシ|floor length)\b"),
    # midi
    ("midi",  r"\b(?:midi|ミディ|half|ハーフ|knee[- ]length|knee length|ひざ丈|膝丈)\b"),
    # short
    ("short", r"\b(?:mini|ミニ|\bshort\b|ショート|crop|クロップ)\b"),
]

# Garments where length is relevant
LENGTH_RELEVANT_TYPES = {
    "coat", "jacket", "dress", "skirt", "blazer", "cardigan",
    "one-piece", "one piece", "sweater", "robe", "kimono",
}

# Color canonical values — the title should show these exactly
COLOR_PATTERNS: list[tuple[str, str]] = [
    ("Black",     r"\b(?:black|ブラック|黒)\b"),
    ("White",     r"\b(?:white|ホワイト|白|オフホワイト|off[- ]white)\b"),
    ("Red",       r"\b(?:red|レッド|赤)\b"),
    ("Blue",      r"\b(?:blue|ブルー|青|ネイビー|navy)\b"),
    ("Green",     r"\b(?:green|グリーン|緑|olive|オリーブ)\b"),
    ("Brown",     r"\b(?:brown|ブラウン|茶|タン|tan)\b"),
    ("Beige",     r"\b(?:beige|ベージュ|cream|クリーム|ivory|アイボリー)\b"),
    ("Grey",      r"\b(?:grey|gray|グレー|グレイ|灰)\b"),
    ("Pink",      r"\b(?:pink|ピンク|マゼンタ|magenta|rose|ローズ)\b"),
    ("Purple",    r"\b(?:purple|パープル|violet|lavender|ラベンダー)\b"),
    ("Yellow",    r"\b(?:yellow|イエロー|黄)\b"),
    ("Orange",    r"\b(?:orange|オレンジ)\b"),
    ("Silver",    r"\b(?:silver|シルバー)\b"),
    ("Gold",      r"\b(?:gold|ゴールド|金)\b"),
    ("Burgundy",  r"\b(?:burgundy|wine|ワイン)\b"),
]

# Era cues — rough decade / Y2K / single year. Only plain decade values;
# we strip qualifiers like "late"/"early" since user wants bare "90's", "00's".
ERA_PATTERNS: list[tuple[str, str]] = [
    ("90's",    r"\b(?:(?:late |early |mid[- ])?90'?s|1990s|1990年代|90年代)\b"),
    ("00's",    r"\b(?:(?:late |early |mid[- ])?00'?s|2000s|2000年代|00年代)\b"),
    ("80's",    r"\b(?:(?:late |early |mid[- ])?80'?s|1980s|1980年代|80年代)\b"),
    ("70's",    r"\b(?:(?:late |early |mid[- ])?70'?s|1970s|1970年代|70年代)\b"),
    ("10's",    r"\b(?:(?:late |early |mid[- ])?10'?s|2010s|2010年代|10年代)\b"),
    # Y2K maps to "00's" — user-requested: only numeric decades in eras.
    ("00's",    r"\b(?:y2k)\b"),
]
# Single year 1960–2015 — capture group returns the year
ERA_YEAR_PATTERN = re.compile(r"\b(19[6-9][0-9]|20[01][0-9])\b")

# Origin — canonicalize to "Made in X"
ORIGIN_PATTERNS: list[tuple[str, str]] = [
    ("Made in USA",      r"\b(?:made in (?:the )?usa|アメリカ製|米国製|u\.?s\.?a\.? made)\b"),
    ("Made in Italy",    r"\b(?:made in italy|italian made|イタリア製|italia)\b"),
    ("Made in France",   r"\b(?:made in france|french made|フランス製)\b"),
    ("Made in Japan",    r"\b(?:made in japan|japanese made|日本製)\b"),
    ("Made in UK",       r"\b(?:made in (?:england|uk|britain|the uk)|英国製|british made)\b"),
    ("Made in Spain",    r"\b(?:made in spain|スペイン製)\b"),
    ("Made in Germany",  r"\b(?:made in germany|ドイツ製)\b"),
]

# Style-adjective vocabulary — ordered by position in the canonical title.
# Each bucket appears in this order: silhouette → neckline → sleeve →
# fabric detail. Within a bucket the first match wins. Multi-match returns
# a space-joined ordered string like "Belted V-Neck Long Sleeve Mesh".
# Bucket output order: silhouette → neckline → fabric detail → sleeve.
# Fabric comes BEFORE sleeve because "Mesh Long Sleeve" reads better than
# "Long Sleeve Mesh" in product titles.
STYLE_BUCKETS: list[list[tuple[str, str]]] = [
    # 1. Silhouette / cut — how the garment is constructed
    [
        # Wrap tops / dresses — English and French (cache-coeur) both map to "Wrap"
        ("Wrap",           r"\bwrap\b|\bcache[- ]?co?eur\b|\bカシュクール\b"),
        ("Belted",         r"\bbelted\b|\bベルテッド\b"),
        ("A-Line",         r"\ba[- ]?line\b"),
        ("Sheath",         r"\bsheath\b"),
        ("Empire",         r"\bempire\b"),
        ("Fit-and-Flare",  r"\bfit[- ]and[- ]flare\b"),
        ("Pleated",        r"\bpleated\b|\bプリーツ\b"),
        ("Ruched",         r"\bruched\b|\bgathered\b|\bギャザー\b"),
        ("Smocked",        r"\bsmocked\b"),
        ("Draped",         r"\bdraped\b"),
        ("Cropped",        r"\bcropped\b|\bクロップ\b"),
        ("Oversized",      r"\boversized\b|\bオーバーサイズ\b"),
        ("Tailored",       r"\btailored\b|\bテーラード\b"),
        ("Reversible",     r"\breversible\b|\bリバーシブル\b"),
    ],
    # Neckline — use a space-separated name, not a hyphen (buyer-friendly)
    [
        ("V Neck",         r"\bv[- ]?neck\b|\bvネック\b"),
        ("Crew Neck",      r"\bcrew[- ]?neck\b"),
        ("Scoop Neck",     r"\bscoop[- ]?neck\b"),
        ("Turtleneck",     r"\bturtle[- ]?neck\b|\bタートルネック\b"),
        ("Halter",         r"\bhalter\b|\bホルター\b"),
        ("Off-Shoulder",   r"\boff[- ]?shoulder\b|\bオフショルダー\b"),
        ("Mock Neck",      r"\bmock[- ]?neck\b"),
        ("Boat Neck",      r"\bboat[- ]?neck\b"),
        ("Sweetheart",     r"\bsweetheart\b"),
        ("Cowl",           r"\bcowl[- ]?neck\b"),
        ("Mandarin Collar",r"\bmandarin\s+collar\b"),
    ],
    # 3. Fabric detail / embellishment — not the material itself, but what's on it.
    # "Flower Mesh" ordered before plain "Mesh" so compound patterns win.
    # This bucket comes BEFORE sleeve so "Mesh Long Sleeve" reads naturally.
    [
        ("Flower Mesh",    r"\bflower\s+mesh\b|\b花\s*メッシュ\b"),
        ("Mesh",           r"\bmesh\b|\bメッシュ\b"),
        ("Lace",           r"\blace\b|\bレース\b"),
        ("Sheer",          r"\bsheer\b"),
        ("Ribbed",         r"\bribbed\b"),
        ("Knit",           r"\bknit\b|\bニット\b"),
        ("Quilted",        r"\bquilted\b"),
        ("Corsage",        r"\bcorsage\b|\bコサージュ\b"),
        ("Beaded",         r"\bbeaded\b|\bビーズ\b"),
        ("Sequined",       r"\bsequin(?:ed|s)?\b|\bスパンコール\b"),
        ("Embroidered",    r"\bembroide[r]+ed\b|\b刺繍\b"),
        ("Embellished",    r"\bembellished\b"),
        ("Water-Repellent",r"\bwater[- ]repellent\b|\b撥水\b"),
    ],
    # 4. Sleeve (last — reads well as "... Mesh Long Sleeve Top")
    [
        ("Long Sleeve",    r"\blong[- ]sleeves?\b|\b長袖\b"),
        ("Short Sleeve",   r"\bshort[- ]sleeves?\b|\b半袖\b"),
        ("3/4 Sleeve",     r"\b3[/-]?4[- ]sleeves?\b|\b七分袖\b"),
        ("Cap Sleeve",     r"\bcap[- ]sleeves?\b"),
        ("Puff Sleeve",    r"\bpuff\s+sleeves?\b"),
        ("Bell Sleeve",    r"\bbell\s+sleeves?\b"),
        ("Sleeveless",     r"\bsleeveless\b|\bノースリーブ\b"),
    ],
]


# Canonical model names — the single biggest SEO signal.
# Matched as whole-word substrings in the description (case-insensitive).
MODEL_NAME_PATTERNS: list[tuple[str, str]] = [
    # Louis Vuitton
    ("Pochette Accessoires", r"\bpochette\s+accessoires?\b"),
    ("Pochette Metis",       r"\bpochette\s+met[ií]s\b"),
    ("Mini Pochette Cles",   r"\bmini\s+pochette\s+cles?\b|\bpochette\s+cles?\b"),
    ("Pochette",             r"\bpochette\b"),
    ("Neverfull",            r"\bneverfull\b"),
    ("Speedy",               r"\bspeedy\b"),
    ("Keepall",              r"\bkeepall\b"),
    ("Alma",                 r"\balma\b"),
    ("Lexington",            r"\blexington\b"),
    ("Agenda",               r"\bagenda\b"),
    ("Multicles",            r"\bmulticles?\b"),
    ("Porte Monnaie",        r"\bporte\s+monnaie\b"),
    ("Porte Tresor",         r"\bporte\s+tr[eé]sor\b"),
    ("Portefeuille Sarah",   r"\bportefeiulle\s+sarah\b|\bportefeuille\s+sarah\b"),
    # Chanel
    ("Classic Flap",         r"\bclassic\s+flap\b"),
    ("2.55 Reissue",         r"\b2\.55\s+reissue\b"),
    ("Boy Bag",              r"\bboy\s+bag\b"),
    ("Choco Bar",            r"\bchoco\s+bar\b"),
    ("Coco Handle",          r"\bcoco\s+handle\b"),
    ("Cambon",               r"\bcambon\b"),
    ("Gabrielle",            r"\bgabrielle\b"),
    # Fendi
    ("Mamma Baguette",       r"\bmamma\s+baguette\b"),
    ("Baguette",             r"\bbaguette\b"),
    ("Peekaboo",             r"\bpeekaboo\b"),
    # Gucci
    ("Jackie",               r"\bjackie\b"),
    ("Bamboo",               r"\bbamboo\b"),
    ("GG Marmont",           r"\bgg\s+marmont\b|\bmarmont\b"),
    ("Horsebit",             r"\bhorsebit\b"),
    # Dior
    ("Saddle",               r"\bsaddle\b"),
    ("Lady Dior",            r"\blady\s+dior\b"),
    ("Book Tote",            r"\bbook\s+tote\b"),
    # Hermès
    ("Birkin",               r"\bbirkin\b"),
    ("Kelly",                r"\bkelly\b"),
    ("Constance",            r"\bconstance\b"),
    ("Evelyne",              r"\bevelyne\b"),
    # Celine
    ("Luggage",              r"\bluggage\b"),
    ("Boogie Bag",           r"\bboogie\s+bag\b|\bboogie\b"),
    ("Triomphe",             r"\btriomphe\b"),
    # Prada
    ("Galleria",             r"\bgalleria\b"),
    ("Cahier",               r"\bcahier\b"),
]

# Size variants — only the bag-world codes (MM/PM/GM/BB). Numeric sizes like
# 25/30/35 are tricky because they collide with clothing sizes (e.g. "Size 40"
# on a skirt). We only match numeric sizes when they appear AFTER a known
# bag-model keyword, enforced in `detect_model_size` below.
MODEL_SIZE_PATTERN = re.compile(r"(?<![A-Za-z])(MM|PM|GM|BB)(?![A-Za-z])", re.IGNORECASE)

# Numeric bag sizes, only valid after a model keyword
MODEL_SIZE_NUMERIC = re.compile(
    r"\b(speedy|neverfull|keepall|alma|birkin|kelly|noe|noé)\s+(\d{2})\b",
    re.IGNORECASE,
)


# Signature patterns — for luxury houses these matter for resale
PATTERN_PATTERNS: list[tuple[str, str]] = [
    ("Monogram",     r"\b(?:monogram|モノグラム)\b"),
    ("Damier",       r"\b(?:damier|ダミエ)\b"),
    ("Matelasse",    r"\b(?:matelass[ée]|マトラッセ)\b"),
    ("Zucca",        r"\b(?:zucca|ズッカ)\b"),
    ("Nova Check",   r"\b(?:nova check|ノバチェック)\b"),
    ("GG Canvas",    r"\b(?:gg canvas|gg supreme)\b"),
    ("Multicolor",   r"\b(?:multicolor|multi[- ]color|マルチカラー)\b"),
    ("Checker",      r"\b(?:checker|チェッカー|checkerboard)\b"),
    ("Floral",       r"\b(?:floral|花柄|flower print)\b"),
    ("Striped",      r"\b(?:stripe[ds]?|ストライプ)\b"),
    ("Plaid",        r"\b(?:plaid|tartan)\b"),
    ("Intrecciato",  r"\b(?:intrecciato|イントレチャート)\b"),
    ("Sherry Line",  r"\b(?:sherry line)\b"),
    ("Tortoise",     r"\b(?:tortoise(?:shell)?|tortoise pattern|べっ甲|rekko)\b"),
    ("Printed",      r"\b(?:all[- ]?over print|print(?:ed)?|プリント)\b"),
]


def _canonical(s: Optional[str]) -> str:
    return (s or "").lower()


def detect_material(*texts: Optional[str]) -> Optional[str]:
    """Return the first matching canonical material, or None."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for mat, pat in MATERIAL_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return mat
    return None


def detect_garment_length(product_type: Optional[str], *texts: Optional[str]) -> Optional[str]:
    """Return 'short' | 'midi' | 'long' if this looks like a garment and a length cue exists."""
    if product_type and not any(t in _canonical(product_type) for t in LENGTH_RELEVANT_TYPES):
        return None
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for length, pat in GARMENT_LENGTH_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return length
    return None


def detect_color(*texts: Optional[str]) -> Optional[str]:
    """Return a canonical color name, 'Multicolor' if 3+ colors detected, else None."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    hits: list[str] = []
    for color, pat in COLOR_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            if color not in hits:
                hits.append(color)
    if not hits:
        return None
    if len(hits) >= 3:
        return "Multicolor"
    # Prefer the first dominant color
    return hits[0]


def detect_era(*texts: Optional[str]) -> Optional[str]:
    """Return era string ('90's', 'Y2K', '1997', …) or None."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    # Check decade patterns first (higher specificity than raw year)
    for era, pat in ERA_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return era
    # Then look for single year 1960-2015 (vintage range)
    m = ERA_YEAR_PATTERN.search(blob)
    if m:
        return m.group(1)
    return None


def detect_origin(*texts: Optional[str]) -> Optional[str]:
    """Return canonical 'Made in X' string or None."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for origin, pat in ORIGIN_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return origin
    return None


def detect_pattern(*texts: Optional[str]) -> Optional[str]:
    """Return canonical signature-pattern name or None."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for pattern, pat in PATTERN_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return pattern
    return None


def detect_model_name(*texts: Optional[str]) -> Optional[str]:
    """Return canonical luxury-model name or None. More specific matches
    (Pochette Accessoires) win over less specific (Pochette) because they
    come first in the list."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for name, pat in MODEL_NAME_PATTERNS:
        if re.search(pat, blob, flags=re.IGNORECASE):
            return name
    return None


# Silhouettes that read as compound types ("Wrap Top", "A-Line Dress"). These
# move to the end of style_adjectives so they sit right next to the category:
# `Vivienne Tam Black Flower Mesh Corsage Wrap Top` vs `... Wrap Flower Mesh...`
# Source of truth: heuristics/rules.yaml (titles.silhouette_categorical).
# Fallback used only if YAML load fails.
_SILHOUETTE_CATEGORICAL_FALLBACK = {"Wrap", "A-Line", "Sheath", "Shift", "Empire", "Fit-and-Flare"}
try:
    from heuristics import RULES as _RULES_SC
    SILHOUETTE_CATEGORICAL = _RULES_SC.silhouette_categorical() or _SILHOUETTE_CATEGORICAL_FALLBACK
except Exception:  # pragma: no cover
    SILHOUETTE_CATEGORICAL = _SILHOUETTE_CATEGORICAL_FALLBACK


def detect_style_adjectives(*texts: Optional[str]) -> Optional[str]:
    """Pull ordered style descriptors from the translation.

    Output order: [silhouette modifiers] [neckline] [fabric details] [sleeve]
    [categorical silhouette]. Categorical silhouettes (Wrap, A-Line, Sheath)
    move to the end so they sit adjacent to the type — 'Wrap Top' reads better
    than '... Wrap Mesh Long Sleeve Top'.

    One match per bucket except fabric-detail which allows up to 3 (items
    layer multiple — Mesh + Corsage + Embroidered). Dedup rule: don't add a
    shorter descriptor if it's contained in an already-added longer one
    ('Mesh' is skipped when 'Flower Mesh' is already present)."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    hits: list[str] = []
    for idx, bucket in enumerate(STYLE_BUCKETS):
        max_per = 3 if idx == 2 else 1  # fabric-detail bucket (idx 2) gets multiple hits
        bucket_hits = 0
        for name, pat in bucket:
            if re.search(pat, blob, flags=re.IGNORECASE):
                # Skip if already contained in an earlier hit (Mesh ⊂ Flower Mesh)
                if any(name in h and name != h for h in hits):
                    continue
                hits.append(name)
                bucket_hits += 1
                if bucket_hits >= max_per:
                    break
    if not hits:
        return None
    # Move categorical silhouettes to the end
    prefix = [h for h in hits if h not in SILHOUETTE_CATEGORICAL]
    suffix = [h for h in hits if h in SILHOUETTE_CATEGORICAL]
    return " ".join(prefix + suffix)


def detect_model_size(*texts: Optional[str]) -> Optional[str]:
    """Return 'MM'/'PM'/'25'/'30' etc. — only the bag-size codes.

    Plain numeric sizes (25/30/35) must follow a known bag model keyword,
    otherwise they collide with clothing sizes like 'Size 40' (a skirt)."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    # 1. Bag size codes (MM/PM/GM/BB) — unambiguous
    m = MODEL_SIZE_PATTERN.search(blob)
    if m:
        return m.group(1).upper()
    # 2. Numeric size after known bag model
    m = MODEL_SIZE_NUMERIC.search(blob)
    if m:
        return m.group(2)
    return None


# Known brand + type signature archetypes. When the description is terse and
# fields are null, these fill in canonical defaults — e.g. a Burberry Trench
# Coat is canonically Nova Check + Cotton + Beige. Only applied to null fields,
# so an explicit "Black Burberry Trench" still shows Black, not Beige.
#
# Source of truth: heuristics/rules.yaml (brand_archetypes section). The inline
# fallback below is used only when the YAML loader fails (missing pyyaml,
# corrupted file). Edit rules.yaml to add new archetypes — changes take effect
# on next Python start.
_BRAND_ARCHETYPES_FALLBACK: dict[tuple[str, str], dict[str, str]] = {
    ("Burberry", "Trench Coat"):        {"pattern": "Nova Check", "material": "Cotton", "color": "Beige"},
    ("Burberry", "Coat"):               {"pattern": "Nova Check", "material": "Cotton"},
    ("Burberry", "Scarf"):              {"pattern": "Nova Check", "material": "Cashmere"},
    ("Louis Vuitton", "Handbag"):       {"pattern": "Monogram"},
    ("Louis Vuitton", "Pouch"):         {"pattern": "Monogram"},
    ("Louis Vuitton", "Shoulder Bag"):  {"pattern": "Monogram"},
    ("Louis Vuitton", "Tote Bag"):      {"pattern": "Monogram"},
    ("Louis Vuitton", "Duffle Bag"):    {"pattern": "Monogram", "material": "Coated Canvas"},
    ("Louis Vuitton", "Duffel Bag"):    {"pattern": "Monogram", "material": "Coated Canvas"},
    ("Louis Vuitton", "Travel Bag"):    {"pattern": "Monogram", "material": "Coated Canvas"},
    ("Louis Vuitton", "Boston Bag"):    {"pattern": "Monogram", "material": "Coated Canvas"},
    ("Louis Vuitton", "Weekender"):     {"pattern": "Monogram", "material": "Coated Canvas"},
    ("Louis Vuitton", "Backpack"):      {"pattern": "Monogram"},
    ("Louis Vuitton", "Crossbody Bag"): {"pattern": "Monogram"},
    ("Chanel", "Handbag"):              {"material": "Lambskin"},
    ("Chanel", "Shoulder Bag"):         {"material": "Lambskin"},
    ("Fendi", "Shirt"):                 {"material": "Cotton"},
}

try:
    from heuristics import RULES as _RULES_BA
    BRAND_ARCHETYPES: dict[tuple[str, str], dict[str, str]] = (
        _RULES_BA.brand_archetype_pairs() or _BRAND_ARCHETYPES_FALLBACK
    )
except Exception as _e:  # pragma: no cover — defensive
    print(f"[extractors] heuristics load failed ({_e}); using fallback BRAND_ARCHETYPES")
    BRAND_ARCHETYPES = _BRAND_ARCHETYPES_FALLBACK


def apply_brand_archetypes(invoice) -> int:
    """Fill canonical defaults for brand+type signature pieces. Only fills
    null fields so explicit extractions are preserved. Returns fill count."""
    from pricing import canon_brand, canon_type
    filled = 0
    for item in invoice.items:
        brand = canon_brand(item.detected_brand)
        ptype = canon_type(item.product_type)
        if not brand or not ptype:
            continue
        archetype = BRAND_ARCHETYPES.get((brand, ptype))
        if not archetype:
            continue
        for field, default in archetype.items():
            if not getattr(item, field, None):
                setattr(item, field, default)
                filled += 1
    return filled


def fill_missing_fields(invoice) -> dict:
    """Run regex fallbacks on null fields. Mutates items in place."""
    stats = {"material_filled": 0, "length_filled": 0, "color_filled": 0,
             "era_filled": 0, "origin_filled": 0, "pattern_filled": 0}
    for item in invoice.items:
        texts = (item.description_original, item.description_english,
                 item.condition_notes, item.product_type)
        if not item.material:
            v = detect_material(*texts)
            if v:
                item.material = v; stats["material_filled"] += 1
        if not item.garment_length:
            v = detect_garment_length(item.product_type, *texts)
            if v:
                item.garment_length = v; stats["length_filled"] += 1
        if not item.color:
            v = detect_color(*texts)
            if v:
                item.color = v; stats["color_filled"] += 1
        if not item.era:
            v = detect_era(*texts)
            if v:
                item.era = v; stats["era_filled"] += 1
        if not item.origin:
            v = detect_origin(*texts)
            if v:
                item.origin = v; stats["origin_filled"] += 1
        if not item.pattern:
            v = detect_pattern(*texts)
            if v:
                item.pattern = v; stats["pattern_filled"] += 1
        if not getattr(item, "model_name", None):
            v = detect_model_name(*texts)
            if v:
                item.model_name = v
                stats.setdefault("model_name_filled", 0)
                stats["model_name_filled"] += 1
        if not getattr(item, "model_size", None):
            v = detect_model_size(*texts)
            if v:
                item.model_size = v
                stats.setdefault("model_size_filled", 0)
                stats["model_size_filled"] += 1
        if not getattr(item, "style_adjectives", None):
            v = detect_style_adjectives(*texts)
            if v:
                item.style_adjectives = v
                stats.setdefault("style_filled", 0)
                stats["style_filled"] += 1
    # Brand archetype defaults — applied AFTER regex extractors so explicit
    # data always wins over defaults.
    arch_filled = apply_brand_archetypes(invoice)
    if arch_filled:
        stats["archetype_filled"] = arch_filled
    return stats


# ---------------------------------------------------------------------------
# E-commerce search keywords — populate Shopify's Tags column
# ---------------------------------------------------------------------------

def search_keywords(item) -> list[str]:
    """Generate a list of searchable keywords for this item. Goes into
    Shopify's `Tags` column so buyers find the item via on-site search + SEO.

    Includes: brand, color, material, pattern, era/decade, origin, category,
    plus derived terms (Vintage, Archive, Designer, Luxury) based on the item.
    """
    kw: list[str] = []

    def add(*vals):
        for v in vals:
            if v and v not in kw:
                kw.append(v)

    # Direct attribute keywords — canonicalize brand casing
    try:
        from pricing import canon_brand
        add(canon_brand(item.detected_brand))
    except Exception:
        add(item.detected_brand)
    add(item.color)
    add(item.material)
    add(item.pattern)
    add(item.era)
    add(item.origin)
    add(item.product_type)
    add(item.garment_length)

    # Derived: vintage/archive for older pieces
    era = (item.era or "").strip()
    if era:
        add("Vintage")
        # 20+ years old = "Archive"
        m = re.match(r"(\d{2,4})", era)
        if m:
            year = int(m.group(1))
            if year < 100:  # decade like '90'
                year = 1900 + year if year >= 60 else 2000 + year
            # current year ~2026 — if item is 20+ years old, archive-worthy
            if year <= 2006:
                add("Archive")
    if (era == "Y2K") or era in ("90's", "00's", "80's", "70's"):
        add("Vintage", "Archive")
    if not item.detected_brand:
        add("Vintage")

    # Material-driven terms — Lambskin is leather, not fur. Separate groups.
    material = (item.material or "").lower()
    if material in ("fox fur", "mink", "weasel fur", "raccoon fur", "squirrel fur", "rabbit fur"):
        add("Real Fur", "Luxury")
    elif material in ("shearling", "sheepskin", "fur"):
        add("Real Fur")
    if material in ("leather", "suede", "goat leather", "pony hair", "lambskin"):
        add("Leather")
    if material in ("silk", "cashmere", "satin"):
        add("Luxury")

    # Brand-tier-driven terms — luxury brands always get "Designer" + "Luxury"
    try:
        from pricing import brand_tier
        tier = brand_tier(item.detected_brand)
        if tier == "luxury":
            add("Designer", "Luxury")
        elif tier == "mid":
            add("Designer")
    except Exception:
        pass

    # Category-driven terms (common e-comm search)
    ptype = (item.product_type or "").lower()
    if "bag" in ptype or "pouch" in ptype or "clutch" in ptype:
        add("Handbag")  # common umbrella term buyers search
    if ptype in ("coat", "jacket", "trench coat"):
        add("Outerwear")

    return kw
