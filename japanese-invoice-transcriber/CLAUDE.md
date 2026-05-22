# Notes for Claude (and future sessions)

## Telegram bot session continuity

A Telegram bot at `buyee/listen.py` accepts commands from the user's phone
while they're away from their laptop. Every action it takes is logged to:

```
buyee/state/telegram_log.jsonl
```

Each line is a structured JSON entry with `ts`, `kind`, and kind-specific
fields. Kinds you'll see:

- `command` — user invoked a text command. Fields: `verb`, `args`, `raw`.
- `rate_set` — handling or import tax rate changed. Fields: `which`, `old`, `new`, `invoice`.
- `override` — title/price/vendor override on a specific item. Fields: `field`, `source_id`, `old`, `new`, `invoice`.
- `enrich` — enrichment triggered. Fields: `invoice`, `only_ids`, `processed`, `enriched`, `cost`.
- `photos` — photo scrape triggered. Fields: `invoice`, `downloaded`, `eligible`.
- `transcribe` — invoice PDF received + processed. Fields: `file`, etc.

### When you should read it

If the user says anything like:
- "what did the bot do" / "what happened today" / "any updates from telegram"
- "did anything sync overnight"
- "are there any pending edits"
- "catch me up"

Then `Read` `buyee/state/telegram_log.jsonl` and summarize the recent
entries (last day or so). Group by kind, highlight any errors or
unexpected things.

If the file doesn't exist or is empty, say so — that means no bot
activity since you last looked.

### Don't echo secrets

The log itself shouldn't contain secrets, but any related files
(`.env`, `buyee/state/config.json`, `buyee/state/shopify_token.json`)
contain credentials. Don't print their contents in chat.

## Quick reference: what lives where

```
inputs/                 PDFs awaiting transcription
inputs/buyee/           Auto-downloaded from Buyee
inputs/telegram/        Sent via the Telegram bot
output/<stem>.json      Raw transcribed invoices (immutable)
output/edited_<stem>.json  Human-curated invoices (current working copies)
output/photos/<stem>/   Per-item thumbnails from Buyee auction pages
output/listings/        Per-item enrichment cache (web_search + photo vision)
heuristics/rules.yaml   Source of truth for title/category/etc rules
heuristics/feedback.yaml  Append-only user-feedback log
buyee/state/            Local state (gitignored): cookies, indexes, logs, tokens
tests/                  Regression: anchors + snapshots
```

## Useful CLI references

- `uv run app.py` — start Streamlit UI on :8501
- `uv run --with playwright --with pydantic --with pyyaml --with anthropic --with python-dotenv python -m buyee listen` — start Telegram listener
- `uv run --with python-dotenv python -m shopify_inventory status` — Shopify integration health
- `uv run --with pytest --with pyyaml --with pydantic pytest tests/` — regression tests
