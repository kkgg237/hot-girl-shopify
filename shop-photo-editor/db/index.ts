import { mkdirSync } from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import * as schema from "./schema";

const DATA_DIR = path.resolve(process.cwd(), "data");
mkdirSync(DATA_DIR, { recursive: true });

const DB_PATH = path.join(DATA_DIR, "app.db");

export const sqlite = new Database(DB_PATH);
sqlite.pragma("journal_mode = WAL");
sqlite.pragma("foreign_keys = ON");

// Auto-create tables in case the user hasn't run `npm run db:push`.
sqlite.exec(`
  CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    reference_image_url TEXT NOT NULL,
    instructions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
  );
  CREATE TABLE IF NOT EXISTS product_jobs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    shopify_product_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT
  );
  CREATE TABLE IF NOT EXISTS image_edits (
    id TEXT PRIMARY KEY,
    product_job_id TEXT NOT NULL REFERENCES product_jobs(id),
    shopify_media_id TEXT NOT NULL,
    original_url TEXT NOT NULL,
    edited_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    position INTEGER NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS app_settings (
    id TEXT PRIMARY KEY DEFAULT 'singleton',
    house_style_image_urls TEXT NOT NULL DEFAULT '[]'
  );
`);

// Idempotently add reference_image_urls column to jobs if missing.
try {
  sqlite.exec(`ALTER TABLE jobs ADD COLUMN reference_image_urls TEXT`);
} catch {
  // Column already exists — ignore.
}
try {
  sqlite.exec(
    `ALTER TABLE jobs ADD COLUMN extend_canvas INTEGER NOT NULL DEFAULT 0`
  );
} catch {
  // Column already exists — ignore.
}
try {
  sqlite.exec(`ALTER TABLE app_settings ADD COLUMN prompt_rules TEXT`);
} catch {
  // Column already exists — ignore.
}

export const db = drizzle(sqlite, { schema });
