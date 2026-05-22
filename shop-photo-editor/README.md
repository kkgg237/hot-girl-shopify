# Photo Editor

Local-first Shopify product photo restyler. Pick products, upload a reference photo and instructions, let Gemini 2.5 Flash Image edit each product image, review before/after, then publish approved edits back to Shopify.

## Setup

1. Copy `.env.example` to `.env` and fill in:
   - `GEMINI_API_KEY` — get one at <https://aistudio.google.com/apikey>.
   - `SHOPIFY_ADMIN_TOKEN` — in your Shopify admin, go to **Apps → Develop apps → Create an app**, install it, then copy the Admin API access token from **API credentials**. The app needs scopes `read_products`, `write_products`, `read_files`, `write_files`.
   - Confirm `SHOPIFY_STORE_DOMAIN` matches your `*.myshopify.com` URL (NOT a custom storefront domain).

2. Install dependencies (already done in this scaffold):
   ```
   npm install
   ```

3. Initialize the local SQLite database (also auto-created on first run):
   ```
   npm run db:push
   ```

4. Start the dev server:
   ```
   npm run dev
   ```
   Then open <http://localhost:3000>.

## Workflow

1. **Catalog** — Browse and pick products. Selection persists in `sessionStorage`.
2. **Edit** — Drop in a reference photo and write instructions. Click "Start editing".
3. **Review** — Each product's images are processed (2 in parallel). Approve, reject, or regenerate per image. When all are decided, "Publish to Shopify" replaces the originals with the approved edits.

Keyboard while reviewing: `A` approve, `R` reject, `G` regenerate, `←` / `→` move between pairs.

## Going to production

This app is local-first by design. To deploy, swap the two adapter modules:

- `lib/blob.ts` — currently writes to `./storage/`. Replace with S3/R2 (the public URL convention `/api/blob/<filename>` becomes a CDN URL).
- `db/index.ts` — currently `better-sqlite3` at `./data/app.db`. Replace with Postgres (Drizzle has a `postgres-js` driver) and update `drizzle.config.ts` accordingly.

Everything else (Shopify client, Gemini client, routes, UI) is portable as-is.
