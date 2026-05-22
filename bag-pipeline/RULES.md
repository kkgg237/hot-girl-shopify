# Listing copy rules

Style and content rules for AI-drafted listings. Source of truth for the prompt lives in [`pipeline/prompts.py`](pipeline/prompts.py); this file is the human-readable log of what rules exist and why.

Format: `YYYY-MM-DD — short rule — why`. Add new entries at the top.

## Voice
- 2026-05-11 — TheRealReal voice. Dry, factual, noun-phrase-led. No marketing voice. — User feedback: listings should read like catalog entries, not sales pitches.
- 2026-05-11 — Banned words: stunning, iconic, timeless, must-have, beautiful, gorgeous, elevated, elevate, perfect, elegant, luxe. No exclamation marks. — Reinforce dry voice.

## Bullets
- 2026-05-11 — 3–4 bullets total, never more, never fewer. — User feedback: 5–6 was too long.
- 2026-05-11 — Bullets describe the bag strictly (material, hardware, closure, strap, signature feature). — Avoid marketing fluff.
- 2026-05-11 — At most one bullet may reference the collection or runway season, only if confident. — Pertinence over guesswork.
- 2026-05-11 — Bullets must not restate facts already in the title (era, brand, color, material, silhouette). — Bullets add detail the title doesn't carry.

## Title
- 2026-05-11 — Title format: `{Era}'s {Brand} {Color} {Material} {Silhouette}`. No marketing adjectives. — Consistent shop voice.

## Material
- 2026-05-11 — Material line is a comma-separated noun list, no descriptive language. — TheRealReal-style catalog format.

## Condition
- 2026-05-11 — Condition text is 2–4 factual sentences. No marketing voice. — TheRealReal-style.
- 2026-05-11 — Always list what cannot be verified from the hero shot (interior, base, back, corners, edge glazing, odor, stickiness, structural integrity). — Hero shot has known blind spots; human checks in hand.
- 2026-05-11 — Never guess dimensions. Placeholder `[measure in hand]` only. — Dimensions are measured by hand.
