"""Load and persist heuristics rules + feedback notes.

Two YAML files live alongside this module:
    rules.yaml      — canonical heuristics. User-edited; loader is read-only.
    feedback.yaml   — append-only log of user notes. Loader can append + update status.

Why YAML: human-editable, supports comments (preserved on hand edit), nested
structure, handles unicode (Japanese strings) cleanly.

Why a Pydantic schema: catches typos in the YAML at load time rather than at
the call site, and gives autocomplete in editors that index models.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover — graceful degrade if pyyaml missing
    yaml = None  # type: ignore[assignment]
    print(
        "[heuristics] PyYAML is not installed. The rules engine will return "
        "empty data. Install with: `uv add pyyaml` or restart the app with "
        "`uv run app.py` so PEP 723 inline deps are re-read."
    )


HERE = Path(__file__).parent
RULES_PATH = HERE / "rules.yaml"
FEEDBACK_PATH = HERE / "feedback.yaml"
DESCRIPTION_TEMPLATES_PATH = HERE / "description_templates.yaml"


# ---------------------------------------------------------------------------
# Schema — Pydantic models mirror the YAML structure
# ---------------------------------------------------------------------------

class EraPolicy(BaseModel):
    allow_in_title_regex: str = r"^\d{4}$"
    allow_decades_for: list[str] = Field(default_factory=list)


class TitleRules(BaseModel):
    era_policy: EraPolicy = Field(default_factory=EraPolicy)
    silhouette_categorical: list[str] = Field(default_factory=list)
    acronyms_uppercase: list[str] = Field(default_factory=list)
    deluxe_materials: list[str] = Field(default_factory=list)


class RegressionAnchor(BaseModel):
    source_id: str
    expected_title: str = ""
    note: Optional[str] = None


class PricingBracket(BaseModel):
    """One row in a `pricing_brackets` table — a cost ceiling + target margin.

    The first bracket whose `max_cost` >= item.landed_cost wins. Multiplier
    is derived from target_margin via `1 / (1 - target_margin)`.

    Example:
        - {max_cost: 100, target_margin: 0.75}   # 4.00× multiplier
    """
    max_cost: float
    target_margin: float

    @property
    def multiplier(self) -> float:
        if self.target_margin >= 1.0:
            return 1000.0  # defensive: 100% margin would be infinite
        return 1.0 / (1.0 - self.target_margin)


class PricingFloors(BaseModel):
    """Cost-relative profit guards applied AFTER bracket lookup, BEFORE rounding.

    - `min_dollar_profit`: ensure (price - landed_cost) >= this $ amount.
      Cheap items naturally have small absolute profit; this floors that.
    - `max_markup_multiple`: cap price at this × landed_cost.
      Stops a $5 scarf being priced at $95 just because the markup curve says so.

    Both default to None (no floor / no ceiling) so the section is optional.
    """
    min_dollar_profit: Optional[float] = None
    max_markup_multiple: Optional[float] = None


class Rules(BaseModel):
    """Top-level container. Mirrors rules.yaml."""
    meta: dict = Field(default_factory=dict)
    titles: TitleRules = Field(default_factory=TitleRules)
    model_era: dict[str, dict[str, str]] = Field(default_factory=dict)
    brand_archetypes: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)
    tier_brands: dict[str, list[str]] = Field(default_factory=dict)
    canonicalize: dict[str, dict[str, str]] = Field(default_factory=dict)
    regression_anchors: list[RegressionAnchor] = Field(default_factory=list)
    # pricing_brackets[invoice_type][tier_category] = [PricingBracket, ...]
    # invoice_type: "vendor_invoice" | "buyee"
    # tier_category: "luxury_apparel" | "luxury_bags" | "mid_apparel" | etc.
    pricing_brackets: dict[str, dict[str, list[PricingBracket]]] = Field(default_factory=dict)
    # pricing_floors[invoice_type] = PricingFloors (min_dollar_profit + max_markup_multiple)
    pricing_floors: dict[str, PricingFloors] = Field(default_factory=dict)

    # ---- Convenience accessors that match the shapes downstream code uses ----

    def model_era_pairs(self) -> dict[tuple[str, str], str]:
        """Flatten nested {brand: {model: era}} → {(brand, model): era}.

        This matches the original `pricing.MODEL_ERA` shape so the swap-in is
        a one-liner: `MODEL_ERA = RULES.model_era_pairs()`.
        """
        out: dict[tuple[str, str], str] = {}
        for brand, models in self.model_era.items():
            for model, era in models.items():
                out[(brand, model)] = era
        return out

    def brand_archetype_pairs(self) -> dict[tuple[str, str], dict[str, str]]:
        """Flatten {brand: {type: defaults}} → {(brand, type): defaults}.

        Matches the shape of `extractors.BRAND_ARCHETYPES`.
        """
        out: dict[tuple[str, str], dict[str, str]] = {}
        for brand, types in self.brand_archetypes.items():
            for ptype, defaults in types.items():
                out[(brand, ptype)] = dict(defaults)
        return out

    def luxury_brands(self) -> set[str]:
        return set(self.tier_brands.get("luxury", []))

    def mid_tier_brands(self) -> set[str]:
        return set(self.tier_brands.get("mid", []))

    def brand_aliases(self) -> dict[str, str]:
        return dict(self.canonicalize.get("brands", {}))

    def type_aliases(self) -> dict[str, str]:
        return dict(self.canonicalize.get("types", {}))

    def acronyms(self) -> set[str]:
        return set(self.titles.acronyms_uppercase)

    def silhouette_categorical(self) -> set[str]:
        return set(self.titles.silhouette_categorical)

    def deluxe_materials(self) -> set[str]:
        """Lowercased set for case-insensitive membership checks in compose_title."""
        return {m.strip().lower() for m in self.titles.deluxe_materials if m}

    def lookup_pricing_bracket(
        self, invoice_type: str, tier_category: str, cost: float,
    ) -> Optional[PricingBracket]:
        """Find the first bracket where cost <= max_cost.

        Args:
            invoice_type: "vendor_invoice" or "buyee"
            tier_category: "luxury_apparel" / "mid_bags" / etc.
            cost: landed cost USD (the value that gets multiplied).

        Returns the matching PricingBracket, or None if no table exists for
        this (invoice_type, tier_category) — caller should fall back to legacy.
        """
        table = self.pricing_brackets.get(invoice_type, {}).get(tier_category, [])
        for bracket in table:
            if cost <= bracket.max_cost:
                return bracket
        return None


class DescriptionTemplate(BaseModel):
    """Per-category copy format for product descriptions.

    Routing: each template lists strings in `applies_to_categories` that
    are matched (case-insensitive substring) against Shopify's
    Standard Product Category field — e.g. a template entry "Handbags"
    matches both "Handbags" and "Apparel & Accessories > Handbags, Wallets
    & Cases > Handbags". The audit then runs the required_sections /
    banned_phrases / length checks against body_html.
    """
    name: str
    applies_to_categories: list[str] = Field(default_factory=list)
    required_sections: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    template: str = ""
    notes: str = ""
    # Shopify Taxonomy ID used to write back this template's category to
    # products. Accepts either the full GID ("gid://shopify/TaxonomyCategory/aa-1-13-8")
    # or the short form ("aa-1-13-8"); see `shopify_category_gid_normalized()`.
    shopify_category_gid: str = ""

    # Backwards-compat: older description_templates.yaml files used
    # `applies_to_product_types`. Accept it on load (Pydantic alias) and
    # surface it under the new name. Drops on next save.
    model_config = {"populate_by_name": True}

    def __init__(self, **data):
        if "applies_to_product_types" in data and "applies_to_categories" not in data:
            data["applies_to_categories"] = data.pop("applies_to_product_types")
        super().__init__(**data)

    def shopify_category_gid_normalized(self) -> str:
        """Return the configured category GID in canonical gid:// form, or ""."""
        s = (self.shopify_category_gid or "").strip()
        if not s:
            return ""
        if s.startswith("gid://"):
            return s
        if s.startswith("aa-"):
            return f"gid://shopify/TaxonomyCategory/{s}"
        return s


class FeedbackNote(BaseModel):
    """One user note in the feedback log."""
    id: str
    date: _dt.date
    topic: str = "general"
    status: str = "pending"  # pending | applied | rejected | deferred
    quote: str
    resolution: Optional[str] = None
    related_rules: list[str] = Field(default_factory=list)
    # Last time this note was surfaced as a reminder (digest, banner, etc).
    # Used to avoid re-spamming on every digest run when a pending note has
    # been recently shown but not yet acted on.
    last_reminded_at: Optional[str] = None

    def days_old(self, today: Optional[_dt.date] = None) -> int:
        today = today or _dt.date.today()
        return (today - self.date).days

    def is_stale(self, threshold_days: int = 2, today: Optional[_dt.date] = None) -> bool:
        """A pending note is 'stale' if it's been pending for more than N days."""
        return self.status == "pending" and self.days_old(today) >= threshold_days


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_rules() -> Rules:
    """Read rules.yaml and validate. Returns an empty Rules if file missing."""
    if yaml is None or not RULES_PATH.exists():
        return Rules()
    with RULES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Rules(**data)


def load_feedback() -> list[FeedbackNote]:
    """Read feedback.yaml and validate. Returns [] if file missing."""
    if yaml is None or not FEEDBACK_PATH.exists():
        return []
    with FEEDBACK_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    notes: list[FeedbackNote] = []
    for item in data:
        try:
            notes.append(FeedbackNote(**item))
        except Exception as e:
            # Bad row — skip but don't crash. CLI/UI will surface validation issues.
            print(f"[heuristics] Skipping malformed feedback note: {e}")
    return notes


# ---------------------------------------------------------------------------
# Mutators (feedback only — rules.yaml is hand-edited to preserve comments)
# ---------------------------------------------------------------------------

def append_feedback(quote: str, topic: str = "general") -> FeedbackNote:
    """Append a new pending note to feedback.yaml. Returns the saved note.

    The note ID is auto-generated as `fb-YYYY-MM-DD-NNN` where NNN is the
    next sequence number for that day.
    """
    if not quote or not quote.strip():
        raise ValueError("Cannot save an empty feedback note.")
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed; can't persist feedback notes. "
            "Install with `uv add pyyaml` and restart the app."
        )

    raw: list[dict] = []
    if FEEDBACK_PATH.exists():
        with FEEDBACK_PATH.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or []

    today = _dt.date.today()
    seq = sum(1 for n in raw if str(n.get("date", "")) == today.isoformat()) + 1
    note = FeedbackNote(
        id=f"fb-{today.isoformat()}-{seq:03d}",
        date=today,
        topic=topic,
        status="pending",
        quote=quote.strip(),
    )

    raw.append(note.model_dump(mode="json"))
    _write_feedback(raw)
    return note


def stale_pending_notes(threshold_days: int = 2) -> list[FeedbackNote]:
    """Return pending notes older than N days, oldest first.

    These are the ones that need a nudge — they've been on the list long
    enough that we should remind the user they exist.
    """
    today = _dt.date.today()
    notes = [n for n in load_feedback() if n.is_stale(threshold_days, today)]
    notes.sort(key=lambda n: n.date)  # oldest first
    return notes


def format_digest(notes: list[FeedbackNote], header: str = "📝 Pending notes") -> str:
    """Build a human-readable digest of pending notes for Telegram/CLI."""
    if not notes:
        return f"{header}: nothing stale. ✓"
    lines = [f"{header} ({len(notes)} pending):"]
    today = _dt.date.today()
    for n in notes:
        age = (today - n.date).days
        age_str = "today" if age == 0 else "1 day ago" if age == 1 else f"{age} days ago"
        snippet = n.quote.strip().split("\n")[0]
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        lines.append(f"  · {n.id}  ({n.topic}, {age_str})")
        lines.append(f"      {snippet}")
    lines.append("")
    lines.append("Mark addressed via the Rules & Notes tab in the app,")
    lines.append("or `python -m heuristics resolve <id> applied`.")
    return "\n".join(lines)


def mark_reminded(note_ids: list[str]) -> None:
    """Stamp last_reminded_at on the given notes so we don't re-nudge them
    multiple times the same day. Persists to feedback.yaml."""
    if yaml is None or not note_ids or not FEEDBACK_PATH.exists():
        return
    with FEEDBACK_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    now = _dt.datetime.now().isoformat(timespec="seconds")
    changed = False
    targets = set(note_ids)
    for n in raw:
        if n.get("id") in targets:
            n["last_reminded_at"] = now
            changed = True
    if changed:
        _write_feedback(raw)


def update_feedback_status(
    note_id: str,
    status: str,
    resolution: Optional[str] = None,
    related_rules: Optional[list[str]] = None,
) -> bool:
    """Update the status (and optionally resolution + related_rules) of a note.

    Returns True if the note was found and updated, False otherwise.
    """
    if status not in ("pending", "applied", "rejected", "deferred"):
        raise ValueError(f"Invalid status: {status}")
    if yaml is None or not FEEDBACK_PATH.exists():
        return False
    with FEEDBACK_PATH.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []

    found = False
    for n in raw:
        if n.get("id") == note_id:
            n["status"] = status
            if resolution:
                n["resolution"] = resolution
            if related_rules is not None:
                n["related_rules"] = related_rules
            found = True
            break
    if found:
        _write_feedback(raw)
    return found


# ---------------------------------------------------------------------------
# Description templates — round-tripped by the Copy formats tab
# ---------------------------------------------------------------------------

def load_description_templates() -> list[DescriptionTemplate]:
    """Read description_templates.yaml and validate.

    Returns the templates in file order. Malformed entries are skipped with a
    warning rather than crashing the loader — keeps the UI usable when one
    template has a typo.
    """
    if yaml is None or not DESCRIPTION_TEMPLATES_PATH.exists():
        return []
    with DESCRIPTION_TEMPLATES_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw_list = data.get("templates") or []
    templates: list[DescriptionTemplate] = []
    for item in raw_list:
        try:
            templates.append(DescriptionTemplate(**item))
        except Exception as e:
            print(f"[heuristics] Skipping malformed description template: {e}")
    return templates


def find_template_for_category(
    category: str,
    templates: Optional[list[DescriptionTemplate]] = None,
) -> Optional[DescriptionTemplate]:
    """Return the first template whose `applies_to_categories` matches
    Shopify's standard product category string.

    Match rule: case-insensitive substring. A template entry "Handbags"
    matches both the leaf name ("Handbags") and the full taxonomy path
    ("Apparel & Accessories > Handbags, Wallets & Cases > Handbags"),
    so users can keep their applies_to lists short. None if no template
    matches.
    """
    if not category:
        return None
    if templates is None:
        templates = load_description_templates()
    haystack = category.strip().lower()
    for tpl in templates:
        for entry in tpl.applies_to_categories:
            needle = (entry or "").strip().lower()
            if needle and needle in haystack:
                return tpl
    return None


# Hand-curated keyword hints used to suggest which template a product
# probably belongs to. Keyed by template name so adding/renaming templates
# doesn't quietly break the suggester. Order matters: most specific first,
# so "Shirt Dress" routes to Dresses rather than Tops.
_TEMPLATE_SYNONYMS: dict[str, set[str]] = {
    "Dresses": {
        # Generic
        "dress", "dresses", "gown", "frock",
        # Silhouettes — keep bare words dress-specific. "maxi" / "midi" /
        # "mini" alone are ambiguous (Midi Skirt is Bottoms) so qualify them.
        "maxi dress", "midi dress", "mini dress", "minidress", "mini-dress",
        "shift dress", "sheath dress", "wrap dress", "slip dress", "slipdress",
        "sundress", "sun dress", "shirt dress", "shirtdress", "tea dress",
        "babydoll", "bodycon", "a-line dress",
        # Occasion
        "cocktail", "ball gown", "evening gown", "prom",
        # One-pieces
        "jumpsuit", "romper", "playsuit", "kaftan", "caftan", "muumuu",
        # Bridal (treat as dress)
        "bridal", "wedding dress",
    },
    "Clothing": {
        # Coats
        "coat", "overcoat", "trench", "peacoat", "duster", "topcoat",
        "raincoat", "mackintosh", "shearling coat", "fur coat",
        # Jackets
        "jacket", "blazer", "puffer", "parka", "anorak", "windbreaker",
        "bomber", "moto", "biker jacket", "varsity", "field jacket",
        "denim jacket", "leather jacket", "suede jacket", "shearling",
        # Outer layers
        "cape", "poncho", "gilet", "fur", "vest",  # vest = waistcoat / fur vest
        "kimono coat", "robe coat", "outerwear",
    },
    "Bottoms": {
        # Pants
        "pants", "trousers", "slacks", "chinos", "khakis", "cords",
        "corduroys", "jeans", "denim", "jeggings", "leggings", "joggers",
        "sweatpants", "track pants", "cargos", "cargo pants",
        "flares", "bootcut", "wide-leg", "wide leg", "capri", "capris",
        "palazzo", "culottes",
        # Skirts
        "skirt", "miniskirt", "midi skirt", "maxi skirt", "pencil skirt",
        "a-line skirt", "pleated skirt", "wrap skirt",
        # Shorts
        "shorts", "bermuda", "bermudas", "hot pants", "boardshorts",
        "biker shorts",
    },
    "Handbag": {
        # Generic
        "bag", "handbag", "purse", "purses",
        # Silhouettes
        "tote", "clutch", "crossbody", "cross-body", "satchel", "hobo",
        "backpack", "duffle", "duffel", "weekender", "pouch",
        "shoulder bag", "top handle", "top-handle", "frame bag",
        "bucket bag", "baguette", "saddle bag", "messenger", "sling",
        "belt bag", "fanny pack", "evening bag", "mini bag",
        # Iconic models (treat as handbags)
        "kelly", "birkin", "constance", "boy bag", "flap bag", "speedy",
        "neverfull", "alma", "lady dior", "saddle", "jackie",
        # Carriers
        "briefcase", "attache", "luggage", "carry-on", "carryon",
        # Small leather goods
        "wallet", "card holder", "cardholder", "card case", "coin purse",
        "passport holder", "key case",
    },
    "Tops": {
        # Shirts
        "shirt", "blouse", "button-down", "button down", "buttondown",
        "oxford", "tunic", "overshirt",
        # Tees / casual
        "tee", "t-shirt", "tshirt", "graphic tee", "ringer tee",
        "baby tee", "henley", "polo",
        # Knits
        "sweater", "cardigan", "knit", "knitwear", "pullover",
        "turtleneck", "mock neck", "mockneck", "crewneck", "crew neck",
        "v-neck", "vneck", "jumper", "twinset", "twin set",
        # Casual
        "sweatshirt", "hoodie", "pullover hoodie",
        # Sleeveless / cropped
        "tank", "tank top", "tanktop", "camisole", "cami",
        "halter", "halterneck", "tube top", "bralette", "crop top",
        "croptop", "bustier", "corset top",
        # Generic
        "top", "topwear",
    },
}

_SUGGESTION_ORDER = ["Dresses", "Clothing", "Bottoms", "Handbag", "Tops"]


def _derive_template_keywords(tpl: DescriptionTemplate) -> set[str]:
    """Pull likely title keywords from a template's metadata.

    Each entry in `applies_to_categories` typically ends with a taxonomy
    leaf like "Pants" or "Coats & Jackets"; we tokenize those so the user
    gets sensible suggestions for templates the synonym table doesn't cover.
    """
    import re as _re
    words: set[str] = {tpl.name.strip().lower()}
    for cat in tpl.applies_to_categories:
        leaf = cat.split(">")[-1].strip()
        for tok in _re.split(r"[\s,/&]+", leaf):
            tok = tok.strip().lower()
            if len(tok) >= 3 and tok not in ("and", "the"):
                words.add(tok)
    return words


def suggest_template_from_product(
    title: str = "",
    product_type: str = "",
    tags: str = "",
    templates: Optional[list[DescriptionTemplate]] = None,
) -> Optional[str]:
    """Guess which template a product probably belongs to, based on its
    title + product_type + tags. Returns the template name or None.

    Matching uses word-boundary regex against the lowercased combined
    haystack — so "tank" in "Tank Top" matches but "tank" in random
    substrings doesn't. Synonyms are checked in _SUGGESTION_ORDER first
    (most-specific category wins ties), then derived keywords for any
    user-added templates.
    """
    import re as _re

    if templates is None:
        templates = load_description_templates()
    if not templates:
        return None

    haystack = f" {title} {product_type} {tags} ".lower()
    name_to_tpl = {t.name: t for t in templates}

    def _hit(keywords: set[str]) -> bool:
        for kw in keywords:
            if not kw:
                continue
            if _re.search(rf"\b{_re.escape(kw)}\b", haystack):
                return True
        return False

    # Pass 1 — ordered, well-known templates with synonyms
    for name in _SUGGESTION_ORDER:
        tpl = name_to_tpl.get(name)
        if tpl is None:
            continue
        keywords = _derive_template_keywords(tpl) | _TEMPLATE_SYNONYMS.get(name, set())
        if _hit(keywords):
            return name

    # Pass 2 — any user-added templates (use only derived keywords)
    for tpl in templates:
        if tpl.name in _SUGGESTION_ORDER:
            continue
        if _hit(_derive_template_keywords(tpl)):
            return tpl.name

    return None


def audit_description(body: str, tpl: DescriptionTemplate) -> dict:
    """Run a single description body through a template's checks.

    Returns {"passed": bool, "findings": [human-readable strings]}. The
    Streamlit preview and the catalogue-wide audit both call this so the
    two never drift apart.

    All checks are substring-based (case-insensitive) so they work directly
    on Shopify's body_html without HTML parsing.
    """
    body = body or ""
    body_lc = body.lower()
    findings: list[str] = []

    for section in tpl.required_sections:
        needle = (section or "").strip().lower()
        if needle and needle not in body_lc:
            findings.append(f"missing required section: “{section}”")

    for phrase in tpl.banned_phrases:
        needle = (phrase or "").strip().lower()
        if needle and needle in body_lc:
            findings.append(f"banned phrase present: “{phrase}”")

    n = len(body)
    if tpl.min_length is not None and n < tpl.min_length:
        findings.append(f"length {n} < min {tpl.min_length}")
    if tpl.max_length is not None and n > tpl.max_length:
        findings.append(f"length {n} > max {tpl.max_length}")

    return {"passed": not findings, "findings": findings}


def save_description_templates(templates: list[DescriptionTemplate]) -> None:
    """Persist the templates list to description_templates.yaml.

    The leading comment block in the file is sacrificed on every save — this
    file is meant to be UI-managed, not hand-edited. See rules.yaml for the
    hand-edited counterpart.
    """
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed; can't persist description templates. "
            "Install with `uv add pyyaml` and restart the app."
        )
    payload = {
        "version": 1,
        "templates": [t.model_dump(mode="json") for t in templates],
    }
    with DESCRIPTION_TEMPLATES_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            payload,
            f,
            sort_keys=False,
            allow_unicode=True,
            width=100,
            default_flow_style=False,
        )


def _write_feedback(raw: list[dict]) -> None:
    """Dump the feedback list back to YAML, preserving Unicode and key order."""
    with FEEDBACK_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            raw,
            f,
            sort_keys=False,
            allow_unicode=True,
            width=100,
            default_flow_style=False,
        )


# ---------------------------------------------------------------------------
# Module-level singleton — convenience for `from heuristics import RULES`
# ---------------------------------------------------------------------------

try:
    RULES: Rules = load_rules()
except Exception as e:  # pragma: no cover — fallback so import never fails
    print(f"[heuristics] Failed to load rules.yaml: {e}. Using empty rules.")
    RULES = Rules()
