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
