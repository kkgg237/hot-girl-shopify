# Invoice Ingestion — Plan & Open Questions

## Current state (v0)

```
samples/  →  uv run transcribe.py <file>  →  stdout + output/*.json
output/   →  uv run to_shopify.py output/ -o shopify.csv
```

One invoice at a time. Works for both Buyee breakdowns and Brand Street Tokyo.

## Target workflow

```
         ┌─────────────────────────┐
Gmail ──▶│  fetcher (cron, daily)  │──▶ inbox/*.pdf
         └─────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────┐
                          │ transcribe.py --inbox inbox/     │
                          │                --out output/     │
                          │                --archive archive/│
                          └──────────────────────────────────┘
                                              │
                                              ▼
                                   output/*.json
                                              │
                                              ▼
                          ┌──────────────────────────────────┐
                          │ to_shopify.py output/            │
                          │              --jpy-usd $RATE     │
                          │              -o shopify.csv      │
                          └──────────────────────────────────┘
```

Drop PDFs in `inbox/` (manually or via fetcher), run two commands, get a Shopify-ready CSV.

## Ingestion sources — ranked by effort

### 1. Manual drop folder ✅ (works now)

User drags PDFs into `inbox/`, runs `transcribe.py --inbox`. Already supported. Zero deps.
**Do this first for anything new.**

### 2. Gmail → inbox/ auto-fetch (v2 — recommended)

Most invoices arrive by email, so Gmail is the natural pipe:

- **Brand Street Tokyo** emails PDFs directly from `g.y.a.i.a.n0920@gmail.com`.
  Easy: filter by sender, save attachments.
- **Buyee** emails a tracking notification per shipment. The breakdown PDF itself is **not**
  attached — has to be downloaded from My Page (see option 3). But the tracking email
  contains the package reference number, which can trigger the download.

Implementation sketch:
- Gmail API (OAuth) or IMAP + Google app password.
- Use a label like `invoices/unprocessed` — apply via Gmail filter on known senders.
- Script moves processed emails to `invoices/processed`.
- Attachment → `inbox/<date>_<sender>_<subject>.pdf`.

Effort: half a day. Saves ongoing manual work.

### 3. Buyee auto-download (v3 — fragile, defer)

**Buyee has no public API.** Researched 2026-04-21:
- Invoice PDFs download from My Page → Package Information → Delivery → [PDF] button.
- Computer-only (mobile site doesn't expose the button).
- A third-party scraper exists on Apify (paid, not official).
- Login has no documented rate limit; CAPTCHA and 2FA are possible.

Options:
- **Playwright script**: log in, walk package list, click PDF buttons, save to `inbox/`.
  Works but fragile — breaks whenever Buyee changes their HTML. ToS gray area.
- **Buy credits on Apify scraper**: avoids hosting, still third-party data exfil.
- **Request data export from Buyee support**: slow but zero-risk; might be viable quarterly.

**Recommendation:** skip until Gmail ingestion covers enough volume to show it's worth it.
If we do build it, Playwright + headless Chrome, run monthly, human approves before
moving files into `inbox/`.

### 4. Mobile photo upload (nice-to-have)

For paper receipts from in-person Tokyo buys: take a photo, AirDrop / email / iCloud
sync into `inbox/`. `transcribe.py` already handles PNG/JPG/WebP. Nothing new to build.

## Open questions (answer before building v2)

- **Currency conversion**: JPY invoices need an FX rate for `Cost per Item` in USD.
  - Daily rate from a free API (e.g. `exchangerate-api.com`, `ecb.europa.eu`)?
  - Or: user enters the bank's charged rate when reconciling the credit card statement?
  - Latter is more accurate (matches what actually hit the card) — prefer that.

- **SKU continuity**: current `to_shopify.py` generates SKUs using the auction/auth code
  suffix for traceability, with a counter fallback. Need to confirm this is what the
  Shopify store already expects (the template example `PRA_2601_685` suggests a running
  counter per month). If the store keeps a running counter, we need to persist it.

- **Brand detection misses**: anything without a clear brand (generic "Burberry
  バーバリーズ トレンチコート" vs. plain "トレンチコート") should flag for manual review,
  not silently end up under "UNK". Add a `--review-missing-brands` report.

- **Variant price**: currently left blank. Store policy is to price later after
  inspecting condition. Leave blank? Or auto-set to `Cost per Item × markup`?

- **Batch sanity check**: the Buyee sample had a line saying "Sum of landed costs + tax =
  ¥X (Δ ¥Y)" — a reconciliation warning. Extend to emit a per-invoice check in the
  Shopify CSV export too.

- **Duplicate detection**: if the same PDF is dropped twice (or Gmail re-delivers one),
  we shouldn't create duplicate rows. Hash PDFs, keep a `processed.json` ledger.

## Things NOT to automate

- Approving individual items for resale (condition check is human work)
- Setting `Variant Price` (depends on market, condition, rarity)
- Writing `Body (HTML)` product descriptions (voice/SEO is shop-specific)
