# Rules & standards

Documented copy/quality standards for the invoice-transcriber tools. The
prompt in code is the source of truth; this file is the human-readable
standard so the intent survives prompt edits.

## Invoice item titles (proposed title)

Composed by `pricing.compose_title()` from the structured fields the
transcriber extracts — never from description text. Order is fixed per
category (`heuristics/rules.yaml` `title_format`), e.g. bags:
`[Era] Brand [Model] [Size] [Color] [Style] [Pattern] [Material] Type`.
Gates: manual `override_title` wins; unknown brand → "Vintage"; era only if a
4-digit year (or an allowlisted decade); only "deluxe" materials survive;
adjacent/overlapping tokens deduped.

**Shoe size** (2026-06-18): shoe titles end with `Size <EU>` when the invoice
records one — e.g. `Chanel Shoes Size 37`, `Chanel Black Leather Sandals Size
36.5`. There's no structured size field, so `pricing._extract_shoe_size()` reads
the **European** size from `condition_notes` → `description_english` →
`description_original` (prefers `EU 39` / `size 37.5C`; a bare 34–47 number is
trusted only on the LLM-clean fields). The **cm foot-length** (24.5cm) is never
used. Scoped to the SHOES template only — non-shoes never gain a size suffix.

**SEO backbone check** (`pricing.title_backbone_issues()`): presence-only —
every title needs Brand + Type + Color, and bags also need a Model. The
Cost-table "title check" column shows ✓ or "⚠ add color, model" so sparse
titles are visible; fill the row fields and the title recomposes. Order is NOT
enforced (that's compose_title's job).

**Learning from edits:** manual title edits are logged to
`title_corrections.jsonl` (now with full field snapshot). `title_learning.build_learned_titles()`
distils the **rich (≥3 tokens), unambiguous (1:1)** computed→override mappings;
`compose_title` replays them so an item that would produce a title you already
fixed gets your version automatically. Sparse/ambiguous corrections are NOT
auto-applied (a bare "Chanel Pumps" maps to many real items).

**Fattening thin titles (Cost review buttons):**
- **Re-parse descriptions** (`app.reparse_descriptions`) — free/instant; re-runs
  `extractors.fill_missing_fields` (normally transcribe-time only) over the
  item descriptions to fill blank fields. Helps when the info is in the text.
- **Enrich (web + photo)** (`buyee.research.enrich_invoice`) — for thin-title
  items only; parses JA/EN description, web-searches similar listings, and
  escalates to **photo vision** (reads the item photo from the PDF). Fills only
  blanks, caches to `output/listings/`, costs an API call per item. Use this
  when the missing field (usually colour) isn't in the description text at all.
- The flag is advisory only — publishing/push never blocks on it.

## Bulk Drop Audit — condition grading

Enforced in `drop_audit.py` → `build_generation_prompt()` (the
`condition_observations` + `condition_notes` keys) and structurally checked in
`audit_generated_description()`. Tests in `tests/test_drop_audit.py`.

### Mechanism

The `N/10` score is produced by the vision model (`claude-sonnet-4-5`) in a
single call over up to 8 Shopify photos (800px CDN renditions). There is no
separate scoring algorithm — accuracy comes from the prompt. Two guards make
the number consistent:

1. **Reason-then-score.** The model first fills `condition_observations`: a
   per-area inspection (exterior front/back, corners & edges, hardware,
   interior, base) recording only what is visible, or `"<area>: not shown"`.
   The grade must follow from that evidence. This field is parsed but **not
   rendered** into the listing — it is a private scratchpad.
2. **Fixed rubric** anchors what each score means.

### Scoring rubric — 1–10 mapped to TheRealReal tiers

The score maps to a named tier, and the **first sentence of the condition note
is the tier's standardized phrase** (mirrors TheRealReal's condition standards).

| Score | Tier | Standardized first sentence |
|-------|------|-----------------------------|
| 10  | Pristine  | New, unused condition. |
| 9   | Excellent | Like new with no visible signs of wear. |
| 8   | Very Good | Minor signs of wear. |
| 7   | Good      | Moderate signs of wear. |
| 6   | Fair      | Heavy signs of wear. |
| 1–5 | As Is     | Extensive signs of wear; may require repair. |

Format: `N/10 (Tier) – <standardized first sentence> <specific visible flaws>`.

Anti-inflation rule: wear visible in **multiple** areas cannot be Very Good (8)
or above.

### Voice rules for condition notes

- **Grade only from visible evidence.** Never invent, guess, or hallucinate
  wear, marks, soiling, repairs, or restoration. Unseen areas get a warning,
  not a description. Ungradeable photos → `condition_notes = "Needs review"`
  plus a warning.
- **Standardized first sentence** = the tier phrase above; then add the
  specific visible flaws, enough to disclose every flaw. No fixed sentence cap.
- **Dry, factual, terse.** One flaw per sentence, no redundancy — never
  "Moderate wear throughout" AND "corner and edge wear present".
- **Rough locations** ("front and back leather"), not exhaustive detail ("the
  lower front panel and edges").
- **No editorializing adjectives** (bright, gorgeous, presents well, character,
  well kept) and **no alarmist words** (damaged, stained, dirty, heavy wear,
  discoloration) — name the specific spot instead.

Approved style example:

> 8/10 (Very Good) – Minor signs of wear. Light corner rubbing and faint
> surface scratches to the hardware. Interior clean.

### Known accuracy levers (not yet applied)

- Send detail shots at 1200–1500px; fine scuffs/edge fraying are lost at 800px.
- Raise the 8-image cap if listings have more angles.
- A dedicated condition-only model pass (separate from description copy).
