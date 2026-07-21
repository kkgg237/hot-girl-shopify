import Database from 'better-sqlite3'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DB_PATH = path.resolve(__dirname, '../data/shop.db')

export interface Product {
  id: string
  handle: string
  title: string
  vendor: string | null
  product_type: string | null
  total_inventory: number | null
}

export interface Variant {
  id: string
  price: string | null
  compare_at_price: string | null
}

export interface Media {
  id: string
  position: number
  url: string
  local_path: string
  width: number | null
  height: number | null
}

export interface ProductBundle {
  product: Product
  variant: Variant | null
  media: Media[]
}

export function openDb() {
  const db = new Database(DB_PATH, { readonly: false, fileMustExist: true })
  db.exec(`
    CREATE TABLE IF NOT EXISTS posts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
      handle TEXT NOT NULL,
      template TEXT NOT NULL,
      status TEXT NOT NULL,
      r2_key TEXT,
      r2_url TEXT,
      ig_container_id TEXT,
      ig_media_id TEXT,
      error TEXT,
      created_at TEXT NOT NULL,
      published_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_posts_handle ON posts(handle);
    CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);

    CREATE TABLE IF NOT EXISTS drafts (
      handle TEXT PRIMARY KEY,
      layout TEXT NOT NULL DEFAULT '1up',
      image_start INTEGER NOT NULL DEFAULT 0,
      image_count INTEGER NOT NULL DEFAULT 4,
      brand TEXT,
      name TEXT,
      price TEXT,
      link TEXT,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drops (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL DEFAULT '',
      cover_body TEXT NOT NULL DEFAULT '[]',     -- JSON array of body lines
      cover_footer TEXT,
      cover_cta_url TEXT,
      trailing_arrow INTEGER NOT NULL DEFAULT 1, -- bool 0/1
      status TEXT NOT NULL DEFAULT 'draft',      -- draft | publishing | published | failed
      ig_container_ids TEXT,                     -- JSON array
      ig_media_ids TEXT,                         -- JSON array
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      published_at TEXT
    );

    CREATE TABLE IF NOT EXISTS drop_items (
      drop_id INTEGER NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
      position INTEGER NOT NULL,
      handle TEXT NOT NULL,
      image_start INTEGER NOT NULL DEFAULT 0,
      image_count INTEGER NOT NULL DEFAULT 4,
      PRIMARY KEY (drop_id, position)
    );
    CREATE INDEX IF NOT EXISTS idx_drop_items_drop ON drop_items(drop_id);

    -- A promo-grid post scheduled onto the calendar. Renders a grid from its
    -- source drop's photos and posts it as an IG story when scheduled_at passes.
    CREATE TABLE IF NOT EXISTS scheduled_grids (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      drop_id INTEGER NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
      title TEXT NOT NULL DEFAULT '',
      scheduled_at TEXT NOT NULL,
      grid_preset TEXT NOT NULL DEFAULT '3x3',
      grid_seed INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled | publishing | published | failed
      error TEXT,
      ig_media_id TEXT,
      created_at TEXT NOT NULL,
      published_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_scheduled_grids_status ON scheduled_grids(status);
  `)
  const draftCols = db.prepare(`PRAGMA table_info(drafts)`).all() as Array<{ name: string }>
  if (!draftCols.some((c) => c.name === 'image_count')) {
    db.exec(`ALTER TABLE drafts ADD COLUMN image_count INTEGER NOT NULL DEFAULT 4`)
  }
  // Per-product override for which Look preset to use when rendering this
  // product's story. NULL = let categorize() decide from product_type + title.
  if (!draftCols.some((c) => c.name === 'look_category')) {
    db.exec(`ALTER TABLE drafts ADD COLUMN look_category TEXT`)
  }
  const postCols = db.prepare(`PRAGMA table_info(posts)`).all() as Array<{ name: string }>
  if (!postCols.some((c) => c.name === 'ig_media_ids')) {
    db.exec(`ALTER TABLE posts ADD COLUMN ig_media_ids TEXT`)
  }
  const dropCols = db.prepare(`PRAGMA table_info(drops)`).all() as Array<{ name: string }>
  if (!dropCols.some((c) => c.name === 'closing_body')) {
    db.exec(`ALTER TABLE drops ADD COLUMN closing_body TEXT NOT NULL DEFAULT '[]'`)
  }
  if (!dropCols.some((c) => c.name === 'include_closing')) {
    db.exec(`ALTER TABLE drops ADD COLUMN include_closing INTEGER NOT NULL DEFAULT 1`)
  }
  if (!dropCols.some((c) => c.name === 'include_opening')) {
    db.exec(`ALTER TABLE drops ADD COLUMN include_opening INTEGER NOT NULL DEFAULT 1`)
  }
  // ISO timestamp for deferred publishing. status='scheduled' + scheduled_at
  // set → the server's scheduler loop publishes the drop when it comes due.
  if (!dropCols.some((c) => c.name === 'scheduled_at')) {
    db.exec(`ALTER TABLE drops ADD COLUMN scheduled_at TEXT`)
  }
  // Per-item snapshot so a drop keeps posting an item even after its product is
  // sold / removed / renamed in Shopify. product_id survives handle renames;
  // snapshot (JSON) preserves the title/vendor/price/media captured at add time.
  const dropItemCols = db.prepare(`PRAGMA table_info(drop_items)`).all() as Array<{ name: string }>
  if (!dropItemCols.some((c) => c.name === 'product_id')) {
    db.exec(`ALTER TABLE drop_items ADD COLUMN product_id TEXT`)
  }
  if (!dropItemCols.some((c) => c.name === 'snapshot')) {
    db.exec(`ALTER TABLE drop_items ADD COLUMN snapshot TEXT`)
  }
  return db
}

export type Layout = '1up' | '2up' | '4up'

export interface Draft {
  handle: string
  layout: Layout
  image_start: number
  image_count: number
  brand: string | null
  name: string | null
  price: string | null
  link: string | null
  // Per-product override of the Look preset used at render time.
  // NULL = auto (categorize from product_type + title). 'clothing' or 'bags' = force.
  look_category: 'clothing' | 'bags' | null
  updated_at: string
}

export function getDraft(db: Database.Database, handle: string): Draft | null {
  return (
    (db.prepare(`SELECT * FROM drafts WHERE handle = ?`).get(handle) as Draft | undefined) ?? null
  )
}

export function upsertDraft(
  db: Database.Database,
  handle: string,
  patch: Partial<Omit<Draft, 'handle' | 'updated_at'>>,
): Draft {
  const existing = getDraft(db, handle)
  // `look_category` accepts an explicit null in the patch to clear back to auto,
  // so use `in patch` checks rather than `??` for that field.
  const look_category =
    'look_category' in patch
      ? (patch.look_category ?? null)
      : (existing?.look_category ?? null)
  const merged: Draft = {
    handle,
    layout: patch.layout ?? existing?.layout ?? '1up',
    image_start: patch.image_start ?? existing?.image_start ?? 0,
    image_count: patch.image_count ?? existing?.image_count ?? 4,
    brand: patch.brand ?? existing?.brand ?? null,
    name: patch.name ?? existing?.name ?? null,
    price: patch.price ?? existing?.price ?? null,
    link: patch.link ?? existing?.link ?? null,
    look_category,
    updated_at: new Date().toISOString(),
  }
  db.prepare(
    `INSERT INTO drafts (handle, layout, image_start, image_count, brand, name, price, link, look_category, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(handle) DO UPDATE SET
       layout = excluded.layout,
       image_start = excluded.image_start,
       image_count = excluded.image_count,
       brand = excluded.brand,
       name = excluded.name,
       price = excluded.price,
       link = excluded.link,
       look_category = excluded.look_category,
       updated_at = excluded.updated_at`,
  ).run(
    merged.handle,
    merged.layout,
    merged.image_start,
    merged.image_count,
    merged.brand,
    merged.name,
    merged.price,
    merged.link,
    merged.look_category,
    merged.updated_at,
  )
  return merged
}

export interface Post {
  id: number
  product_id: string
  handle: string
  template: string
  status: 'pending' | 'publishing' | 'published' | 'failed'
  r2_key: string | null
  r2_url: string | null
  ig_container_id: string | null
  ig_media_id: string | null
  ig_media_ids: string | null
  error: string | null
  created_at: string
  published_at: string | null
}

// ─── Drops ──────────────────────────────────────────────────────────────
export type DropStatus = 'draft' | 'scheduled' | 'publishing' | 'published' | 'failed'

export interface Drop {
  id: number
  name: string
  cover_body: string[]
  cover_footer: string | null
  cover_cta_url: string | null
  trailing_arrow: boolean
  closing_body: string[]
  include_opening: boolean
  include_closing: boolean
  status: DropStatus
  scheduled_at: string | null
  ig_container_ids: string[] | null
  ig_media_ids: string[] | null
  error: string | null
  created_at: string
  updated_at: string
  published_at: string | null
}

// Frozen copy of a product's render data, taken when it's added to a drop, so
// the drop can still post the item if the product later leaves the catalog.
export interface DropItemSnapshot {
  title: string
  vendor: string | null
  price: string | null
  product_type: string | null
  media: string[] // local_path of each usable image, in order
}

export interface DropItem {
  drop_id: number
  position: number
  handle: string
  image_start: number
  image_count: number
  product_id: string | null
  snapshot: DropItemSnapshot | null
}

interface DropRow {
  id: number
  name: string
  cover_body: string
  cover_footer: string | null
  cover_cta_url: string | null
  trailing_arrow: number
  closing_body: string
  include_opening: number
  include_closing: number
  status: DropStatus
  scheduled_at: string | null
  ig_container_ids: string | null
  ig_media_ids: string | null
  error: string | null
  created_at: string
  updated_at: string
  published_at: string | null
}

function safeParseArray(s: string | null | undefined): string[] {
  if (!s) return []
  try { return JSON.parse(s) as string[] } catch { return [] }
}

function hydrateDrop(row: DropRow): Drop {
  return {
    id: row.id,
    name: row.name,
    cover_body: safeParseArray(row.cover_body),
    cover_footer: row.cover_footer,
    cover_cta_url: row.cover_cta_url,
    trailing_arrow: row.trailing_arrow === 1,
    closing_body: safeParseArray(row.closing_body),
    include_opening: row.include_opening === 1,
    include_closing: row.include_closing === 1,
    status: row.status,
    scheduled_at: row.scheduled_at,
    ig_container_ids: row.ig_container_ids ? (JSON.parse(row.ig_container_ids) as string[]) : null,
    ig_media_ids: row.ig_media_ids ? (JSON.parse(row.ig_media_ids) as string[]) : null,
    error: row.error,
    created_at: row.created_at,
    updated_at: row.updated_at,
    published_at: row.published_at,
  }
}

export function listDrops(db: Database.Database): Drop[] {
  const rows = db.prepare(`SELECT * FROM drops ORDER BY id DESC`).all() as DropRow[]
  return rows.map(hydrateDrop)
}

export function getDrop(db: Database.Database, id: number): Drop | null {
  const row = db.prepare(`SELECT * FROM drops WHERE id = ?`).get(id) as DropRow | undefined
  return row ? hydrateDrop(row) : null
}

interface DropItemRow {
  drop_id: number
  position: number
  handle: string
  image_start: number
  image_count: number
  product_id: string | null
  snapshot: string | null
}

function hydrateDropItem(row: DropItemRow): DropItem {
  let snapshot: DropItemSnapshot | null = null
  if (row.snapshot) {
    try { snapshot = JSON.parse(row.snapshot) as DropItemSnapshot } catch { snapshot = null }
  }
  return {
    drop_id: row.drop_id,
    position: row.position,
    handle: row.handle,
    image_start: row.image_start,
    image_count: row.image_count,
    product_id: row.product_id ?? null,
    snapshot,
  }
}

export function getDropItems(db: Database.Database, dropId: number): DropItem[] {
  const rows = db
    .prepare(`SELECT * FROM drop_items WHERE drop_id = ? ORDER BY position ASC`)
    .all(dropId) as DropItemRow[]
  return rows.map(hydrateDropItem)
}

// Look a product up by its Shopify gid — used to recover an item whose handle
// was renamed (the id is stable across renames).
export function getProductById(db: Database.Database, id: string): ProductBundle | null {
  const product = db
    .prepare(
      `SELECT id, handle, title, vendor, product_type, total_inventory
       FROM products WHERE id = ?`,
    )
    .get(id) as Product | undefined
  if (!product) return null
  const variant = db
    .prepare(`SELECT id, price, compare_at_price FROM variants WHERE product_id = ? ORDER BY position ASC LIMIT 1`)
    .get(product.id) as Variant | undefined ?? null
  const media = db
    .prepare(
      `SELECT id, position, url, local_path, width, height
       FROM media WHERE product_id = ? AND local_path IS NOT NULL ORDER BY position ASC`,
    )
    .all(product.id) as Media[]
  return { product, variant, media }
}

// Build the frozen snapshot from a live product bundle.
export function snapshotFromBundle(b: ProductBundle): DropItemSnapshot {
  return {
    title: b.product.title,
    vendor: b.product.vendor ?? null,
    price: b.variant?.price ?? null,
    product_type: b.product.product_type ?? null,
    media: b.media.map((m) => m.local_path).filter((p): p is string => !!p),
  }
}

export function createDrop(
  db: Database.Database,
  patch: Partial<Omit<Drop, 'id' | 'created_at' | 'updated_at' | 'status' | 'ig_container_ids' | 'ig_media_ids' | 'error' | 'published_at'>> = {},
): Drop {
  const now = new Date().toISOString()
  const result = db
    .prepare(
      `INSERT INTO drops (name, cover_body, cover_footer, cover_cta_url, trailing_arrow, closing_body, include_opening, include_closing, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)`,
    )
    .run(
      patch.name ?? '',
      JSON.stringify(patch.cover_body ?? []),
      patch.cover_footer ?? null,
      patch.cover_cta_url ?? null,
      patch.trailing_arrow === false ? 0 : 1,
      JSON.stringify(patch.closing_body ?? []),
      patch.include_opening === false ? 0 : 1,
      patch.include_closing === false ? 0 : 1,
      now,
      now,
    )
  const drop = getDrop(db, result.lastInsertRowid as number)
  if (!drop) throw new Error('failed to create drop')
  return drop
}

export function updateDrop(
  db: Database.Database,
  id: number,
  patch: Partial<Pick<Drop, 'name' | 'cover_body' | 'cover_footer' | 'cover_cta_url' | 'trailing_arrow' | 'closing_body' | 'include_opening' | 'include_closing' | 'status' | 'scheduled_at' | 'ig_container_ids' | 'ig_media_ids' | 'error' | 'published_at'>>,
): Drop {
  const existing = getDrop(db, id)
  if (!existing) throw new Error(`Drop ${id} not found`)
  const now = new Date().toISOString()
  const merged: Drop = {
    ...existing,
    ...patch,
    updated_at: now,
  }
  db.prepare(
    `UPDATE drops SET
       name = ?,
       cover_body = ?,
       cover_footer = ?,
       cover_cta_url = ?,
       trailing_arrow = ?,
       closing_body = ?,
       include_opening = ?,
       include_closing = ?,
       status = ?,
       scheduled_at = ?,
       ig_container_ids = ?,
       ig_media_ids = ?,
       error = ?,
       updated_at = ?,
       published_at = ?
     WHERE id = ?`,
  ).run(
    merged.name,
    JSON.stringify(merged.cover_body),
    merged.cover_footer,
    merged.cover_cta_url,
    merged.trailing_arrow ? 1 : 0,
    JSON.stringify(merged.closing_body),
    merged.include_opening ? 1 : 0,
    merged.include_closing ? 1 : 0,
    merged.status,
    merged.scheduled_at,
    merged.ig_container_ids ? JSON.stringify(merged.ig_container_ids) : null,
    merged.ig_media_ids ? JSON.stringify(merged.ig_media_ids) : null,
    merged.error,
    merged.updated_at,
    merged.published_at,
    id,
  )
  return merged
}

export function deleteDrop(db: Database.Database, id: number): void {
  db.prepare(`DELETE FROM drops WHERE id = ?`).run(id)
}

// ── Scheduled grid posts ────────────────────────────────────────────────
export type ScheduledGridStatus = 'scheduled' | 'publishing' | 'published' | 'failed'

export interface ScheduledGrid {
  id: number
  drop_id: number
  title: string
  scheduled_at: string
  grid_preset: string
  grid_seed: number
  status: ScheduledGridStatus
  error: string | null
  ig_media_id: string | null
  created_at: string
  published_at: string | null
}

export function listScheduledGrids(db: Database.Database): ScheduledGrid[] {
  return db.prepare(`SELECT * FROM scheduled_grids ORDER BY scheduled_at ASC`).all() as ScheduledGrid[]
}

export function getScheduledGrid(db: Database.Database, id: number): ScheduledGrid | null {
  return (db.prepare(`SELECT * FROM scheduled_grids WHERE id = ?`).get(id) as ScheduledGrid | undefined) ?? null
}

export function createScheduledGrid(
  db: Database.Database,
  patch: Pick<ScheduledGrid, 'drop_id' | 'scheduled_at'> &
    Partial<Pick<ScheduledGrid, 'title' | 'grid_preset' | 'grid_seed'>>,
): ScheduledGrid {
  const now = new Date().toISOString()
  const info = db
    .prepare(
      `INSERT INTO scheduled_grids (drop_id, title, scheduled_at, grid_preset, grid_seed, status, created_at)
       VALUES (?, ?, ?, ?, ?, 'scheduled', ?)`,
    )
    .run(
      patch.drop_id,
      patch.title ?? '',
      patch.scheduled_at,
      patch.grid_preset ?? '3x3',
      patch.grid_seed ?? 1,
      now,
    )
  return getScheduledGrid(db, Number(info.lastInsertRowid))!
}

export function updateScheduledGrid(
  db: Database.Database,
  id: number,
  patch: Partial<Pick<ScheduledGrid, 'drop_id' | 'title' | 'scheduled_at' | 'grid_preset' | 'grid_seed' | 'status' | 'error' | 'ig_media_id' | 'published_at'>>,
): ScheduledGrid {
  const existing = getScheduledGrid(db, id)
  if (!existing) throw new Error(`Scheduled grid ${id} not found`)
  const merged = { ...existing, ...patch }
  db.prepare(
    `UPDATE scheduled_grids
     SET drop_id = ?, title = ?, scheduled_at = ?, grid_preset = ?, grid_seed = ?, status = ?, error = ?, ig_media_id = ?, published_at = ?
     WHERE id = ?`,
  ).run(
    merged.drop_id,
    merged.title,
    merged.scheduled_at,
    merged.grid_preset,
    merged.grid_seed,
    merged.status,
    merged.error,
    merged.ig_media_id,
    merged.published_at,
    id,
  )
  return merged
}

export function deleteScheduledGrid(db: Database.Database, id: number): void {
  db.prepare(`DELETE FROM scheduled_grids WHERE id = ?`).run(id)
}

export function setDropItems(db: Database.Database, dropId: number, items: Array<Omit<DropItem, 'drop_id'>>): void {
  // Preserve any snapshot/product_id already stored for these handles, so
  // re-saving a drop whose product has since vanished doesn't wipe the frozen
  // copy that lets it keep posting.
  const prior = new Map<string, { product_id: string | null; snapshot: string | null }>()
  for (const row of db.prepare(`SELECT handle, product_id, snapshot FROM drop_items WHERE drop_id = ?`).all(dropId) as Array<{ handle: string; product_id: string | null; snapshot: string | null }>) {
    prior.set(row.handle, { product_id: row.product_id, snapshot: row.snapshot })
  }
  const tx = db.transaction(() => {
    db.prepare(`DELETE FROM drop_items WHERE drop_id = ?`).run(dropId)
    const ins = db.prepare(
      `INSERT INTO drop_items (drop_id, position, handle, image_start, image_count, product_id, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    items.forEach((item, idx) => {
      // Snapshot from the live product if it exists now; otherwise keep whatever
      // was frozen before (or an explicit snapshot passed in).
      const live = getProductByHandle(db, item.handle)
      const priorRow = prior.get(item.handle)
      const productId = live?.product.id ?? item.product_id ?? priorRow?.product_id ?? null
      const snapshot = live
        ? JSON.stringify(snapshotFromBundle(live))
        : item.snapshot
          ? JSON.stringify(item.snapshot)
          : (priorRow?.snapshot ?? null)
      ins.run(dropId, idx, item.handle, item.image_start ?? 0, item.image_count ?? 4, productId, snapshot)
    })
    db.prepare(`UPDATE drops SET updated_at = ? WHERE id = ?`).run(new Date().toISOString(), dropId)
  })
  tx()
}

// ────────────────────────────────────────────────────────────────────────

export function getLatestPost(db: Database.Database, handle: string, template: string): Post | null {
  return (
    (db
      .prepare(
        `SELECT * FROM posts WHERE handle = ? AND template = ? ORDER BY id DESC LIMIT 1`,
      )
      .get(handle, template) as Post | undefined) ?? null
  )
}

export function getProductByHandle(db: Database.Database, handle: string): ProductBundle | null {
  const product = db
    .prepare(
      `SELECT id, handle, title, vendor, product_type, total_inventory
       FROM products WHERE handle = ?`,
    )
    .get(handle) as Product | undefined
  if (!product) return null

  const variant = db
    .prepare(
      `SELECT id, price, compare_at_price
       FROM variants WHERE product_id = ? ORDER BY position ASC LIMIT 1`,
    )
    .get(product.id) as Variant | undefined ?? null

  const media = db
    .prepare(
      `SELECT id, position, url, local_path, width, height
       FROM media WHERE product_id = ? AND local_path IS NOT NULL
       ORDER BY position ASC`,
    )
    .all(product.id) as Media[]

  return { product, variant, media }
}

export interface SearchableProduct {
  handle: string
  title: string
  vendor: string | null
  tags: string[]
  image_count: number
  updated_at: string | null
}

export interface SearchProductsOpts {
  tag?: string         // substring match against any tag in the product's tag array
  query?: string       // substring against title/vendor
  sort?: 'newest' | 'title'
  limit?: number
  minImages?: number   // require at least N usable images
}

export function searchProducts(db: Database.Database, opts: SearchProductsOpts = {}): SearchableProduct[] {
  const sort = opts.sort === 'newest' ? 'p.updated_at DESC' : 'p.title ASC'
  const limit = opts.limit ?? 200
  const minImages = opts.minImages ?? 1

  const where: string[] = [
    `p.status = 'ACTIVE'`,
    `p.total_inventory > 0`,
    `(SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) >= ?`,
  ]
  const params: unknown[] = [minImages]

  if (opts.tag) {
    where.push(`LOWER(IFNULL(p.tags, '')) LIKE ?`)
    params.push(`%${opts.tag.toLowerCase()}%`)
  }
  if (opts.query) {
    where.push(`(LOWER(p.title) LIKE ? OR LOWER(IFNULL(p.vendor, '')) LIKE ?)`)
    params.push(`%${opts.query.toLowerCase()}%`, `%${opts.query.toLowerCase()}%`)
  }

  const rows = db
    .prepare(
      `SELECT p.handle, p.title, p.vendor, p.tags, p.updated_at,
              (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) AS image_count
       FROM products p
       WHERE ${where.join(' AND ')}
       ORDER BY ${sort}
       LIMIT ?`,
    )
    .all(...params, limit) as Array<{
      handle: string
      title: string
      vendor: string | null
      tags: string | null
      updated_at: string | null
      image_count: number
    }>

  return rows.map((r) => {
    let tags: string[] = []
    if (r.tags) {
      try { tags = JSON.parse(r.tags) as string[] } catch { /* ignore */ }
    }
    return {
      handle: r.handle,
      title: r.title,
      vendor: r.vendor,
      tags,
      image_count: r.image_count,
      updated_at: r.updated_at,
    }
  })
}

export function listAllTags(db: Database.Database): Array<{ tag: string; count: number }> {
  const rows = db
    .prepare(`SELECT tags FROM products WHERE status='ACTIVE' AND tags IS NOT NULL AND tags != '' AND tags != '[]'`)
    .all() as Array<{ tags: string }>
  const counts = new Map<string, number>()
  for (const r of rows) {
    try {
      const arr = JSON.parse(r.tags) as string[]
      for (const t of arr) {
        counts.set(t, (counts.get(t) ?? 0) + 1)
      }
    } catch { /* ignore */ }
  }
  return Array.from(counts.entries())
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
}

// One-time (idempotent) fill of product_id + snapshot for existing drop items
// whose product is still live, so they survive a future rename/sale even though
// they were added before snapshots existed. Items already gone are left for the
// render-time disk fallback. Returns how many rows were filled.
export function backfillDropItemSnapshots(db: Database.Database): number {
  const rows = db
    .prepare(`SELECT drop_id, position, handle FROM drop_items WHERE snapshot IS NULL OR product_id IS NULL`)
    .all() as Array<{ drop_id: number; position: number; handle: string }>
  const upd = db.prepare(`UPDATE drop_items SET product_id = ?, snapshot = ? WHERE drop_id = ? AND position = ?`)
  let filled = 0
  for (const r of rows) {
    const live = getProductByHandle(db, r.handle)
    if (live && live.media.length) {
      upd.run(live.product.id, JSON.stringify(snapshotFromBundle(live)), r.drop_id, r.position)
      filled++
    }
  }
  return filled
}

// Distinct vendor names, longest first so multi-word brands match before any
// single-word substring of them. Used to guess a brand from a bare handle.
export function listAllVendors(db: Database.Database): string[] {
  const rows = db
    .prepare(`SELECT DISTINCT vendor FROM products WHERE vendor IS NOT NULL AND vendor != ''`)
    .all() as Array<{ vendor: string }>
  return rows.map((r) => r.vendor).sort((a, b) => b.length - a.length)
}

export function listUsable(db: Database.Database, minImages = 4, limit = 50): Product[] {
  return db
    .prepare(
      `SELECT p.id, p.handle, p.title, p.vendor, p.total_inventory
       FROM products p
       WHERE p.status = 'ACTIVE' AND p.total_inventory > 0
         AND (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) >= ?
       ORDER BY p.title
       LIMIT ?`,
    )
    .all(minImages, limit) as Product[]
}
