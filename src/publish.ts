import { writeFileSync, mkdirSync, existsSync, readdirSync, unlinkSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'
import {
  openDb,
  getProductByHandle,
  getProductById,
  getDraft,
  getDrop,
  getDropItems,
  updateDrop,
  listAllVendors,
  type Post,
  type Draft,
  type Drop,
  type DropItem,
  type Layout,
} from './db.js'
import { renderStoryPng, renderCoverPng } from './render.js'
import { uploadStoryImage } from './upload.js'
import { postStoryFromUrl, getPublishingLimit } from './instagram.js'
import { brand } from './brand.js'
import { categorize, getLookSettings, type Category } from './look-settings.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT_DIR = path.join(ROOT, 'out')
const THUMB_DIR = path.join(OUT_DIR, 'thumbs')
// Card thumbnails are tiny next to the 1080×1920 IG export — render at this
// size for the review UI so the browser doesn't decode 3 MB PNGs to display
// them at ~218 px wide. Big perceived speedup.
const THUMB_WIDTH = 540
const THUMB_HEIGHT = 960

const TEMPLATE = 'story_grid_4'

export function thumbPathFor(handle: string): string {
  return path.join(THUMB_DIR, `${handle}-1.jpg`)
}

async function ensureThumbJpeg(srcPng: string, dest: string): Promise<void> {
  mkdirSync(THUMB_DIR, { recursive: true })
  await sharp(srcPng)
    .resize(THUMB_WIDTH, THUMB_HEIGHT, { fit: 'cover' })
    .jpeg({ quality: 82, progressive: true })
    .toFile(dest)
}

function formatPrice(raw: string | null | undefined): string {
  if (!raw) return ''
  const n = Number(raw)
  if (!Number.isFinite(n)) return raw
  if (n === Math.trunc(n)) return String(Math.trunc(n))
  return n.toFixed(2)
}

function stripBrandPrefix(title: string, vendor: string | null): string {
  if (!vendor) return title
  const escaped = vendor.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`\\b${escaped}\\b`, 'gi')
  return title.replace(pattern, '').replace(/\s{2,}/g, ' ').trim() || title
}

export interface Frame {
  imagePaths: string[]
  outName: string
}

export interface ResolvedConfig {
  brand: string
  name: string
  price: string
  link: string
  layout: Layout
  frames: Frame[]
  imageStart: number
  imageCount: number
  productType: string | null
  // The raw Shopify title — used as a fallback signal for category detection
  // when product_type is empty (which it is for ~half the catalog).
  rawTitle: string
  // Manual per-product override from the edit modal. NULL = use auto-detect.
  lookCategoryOverride: Category | null
}

export function resolveConfig(handle: string): ResolvedConfig {
  const db = openDb()
  try {
    const bundle = getProductByHandle(db, handle)
    if (!bundle) throw new Error(`Product not found: ${handle}`)
    const draft = getDraft(db, handle)

    const defaultBrand = bundle.product.vendor || ''
    const defaultName = stripBrandPrefix(bundle.product.title, defaultBrand)
    const defaultPrice = formatPrice(bundle.variant?.price)
    const defaultLink = `https://paststudies.shop/products/${handle}`

    let layout: Layout = draft?.layout ?? '1up'
    const totalImgs = bundle.media.length
    const requestedStart = draft?.image_start ?? 0

    // Dormant collage layouts fall back to 1up if the product can't satisfy them.
    if (layout === '2up' && totalImgs < 2) layout = '1up'
    if (layout === '4up' && totalImgs < 4) layout = '1up'

    let frames: Frame[]
    let imageStart: number
    let imageCount: number

    if (layout === '1up') {
      const requested = draft?.image_count ?? 4
      const maxStart = Math.max(0, totalImgs - 1)
      imageStart = Math.min(Math.max(0, requestedStart), maxStart)
      imageCount = Math.max(1, Math.min(requested, totalImgs - imageStart))
      frames = []
      for (let i = 0; i < imageCount; i++) {
        frames.push({
          imagePaths: [bundle.media[imageStart + i].local_path],
          outName: `${handle}-${i + 1}`,
        })
      }
    } else {
      const needed = layout === '2up' ? 2 : 4
      const maxStart = Math.max(0, totalImgs - needed)
      imageStart = Math.min(Math.max(0, requestedStart), maxStart)
      imageCount = 1
      frames = [
        {
          imagePaths: bundle.media.slice(imageStart, imageStart + needed).map((m) => m.local_path),
          outName: `${handle}-1`,
        },
      ]
    }

    return {
      brand: draft?.brand ?? defaultBrand,
      name: draft?.name ?? defaultName,
      price: draft?.price ?? defaultPrice,
      link: draft?.link ?? defaultLink,
      layout,
      frames,
      imageStart,
      imageCount,
      productType: bundle.product.product_type,
      rawTitle: bundle.product.title,
      lookCategoryOverride: draft?.look_category ?? null,
    }
  } finally {
    db.close()
  }
}

// Pick the look category for this product: explicit override wins, else auto.
function resolveCategory(cfg: { productType: string | null; rawTitle: string; lookCategoryOverride: Category | null }): Category {
  return cfg.lookCategoryOverride ?? categorize(cfg.productType, cfg.rawTitle)
}

function cleanStaleFrames(handle: string, keep: Set<string>): void {
  if (!existsSync(OUT_DIR)) return
  const prefix = `${handle}-`
  for (const f of readdirSync(OUT_DIR)) {
    if (!f.startsWith(prefix) || !f.endsWith('.png')) continue
    if (keep.has(f)) continue
    try {
      unlinkSync(path.join(OUT_DIR, f))
    } catch {
      // ignore
    }
  }
}

export async function renderForHandle(
  handle: string,
  opts: { force?: boolean } = {},
): Promise<string[]> {
  mkdirSync(OUT_DIR, { recursive: true })
  const cfg = resolveConfig(handle)
  // Fast path: if every expected frame is already on disk and the caller
  // didn't ask us to re-render, reuse the cached PNGs. Cache invalidation
  // (draft save, look save) deletes these files so we naturally re-render
  // next time. This turns a page-load full of cards from 227 satori+resvg
  // jobs into 227 disk reads.
  const expected = cfg.frames.map((f) => path.join(OUT_DIR, `${f.outName}.png`))
  if (!opts.force && expected.every((p) => existsSync(p))) {
    return expected
  }
  const look = getLookSettings(resolveCategory(cfg))
  const outPaths: string[] = []
  const keep = new Set<string>()
  for (let i = 0; i < cfg.frames.length; i++) {
    const frame = cfg.frames[i]
    const png = await renderStoryPng({
      brand: cfg.brand,
      name: cfg.name,
      price: cfg.price,
      imagePaths: frame.imagePaths,
      layout: cfg.layout,
      look,
    })
    const fileName = `${frame.outName}.png`
    const outPath = path.join(OUT_DIR, fileName)
    writeFileSync(outPath, png)
    outPaths.push(outPath)
    keep.add(fileName)
    // Produce the small JPEG for the review-UI thumb at the same time —
    // it's only the first frame that's used for cards.
    if (i === 0) {
      try { await ensureThumbJpeg(outPath, thumbPathFor(handle)) } catch { /* ignore */ }
    }
  }
  cleanStaleFrames(handle, keep)
  return outPaths
}

// Product data changed since this thumb was rendered? The hourly sync bumps
// products.updated_at on any Shopify edit (including media), so comparing it
// against the thumb file's mtime re-renders exactly the products that changed.
// This replaced wiping the whole render cache after every sync, which caused a
// 200-thumb render storm on the next page load.
function thumbIsStale(handle: string, thumbPath: string): boolean {
  const db = openDb()
  try {
    const row = db
      .prepare(`SELECT updated_at FROM products WHERE handle = ?`)
      .get(handle) as { updated_at: string | null } | undefined
    const updatedMs = row?.updated_at ? Date.parse(row.updated_at) : NaN
    if (!Number.isFinite(updatedMs)) return false
    return statSync(thumbPath).mtimeMs < updatedMs
  } catch {
    return false
  } finally {
    db.close()
  }
}

// Fast path for the review-UI card thumb. Avoids loading/serving the 3 MB
// full-resolution PNG when we just need a 50 KB JPEG at display size.
// Returns the path to the JPEG on disk; renders on demand if needed.
export async function renderThumbForHandle(handle: string): Promise<string> {
  const thumbPath = thumbPathFor(handle)
  if (existsSync(thumbPath)) {
    if (!thumbIsStale(handle, thumbPath)) return thumbPath
    invalidateRenderedFrames(handle)
  }
  // No thumb yet — generate the full PNG (which also writes the thumb).
  await renderForHandle(handle)
  if (existsSync(thumbPath)) return thumbPath
  // Belt-and-suspenders: if the full render somehow skipped the thumb step
  // (e.g. cached PNG existed but thumb didn't), build it from the PNG now.
  const cfg = resolveConfig(handle)
  const firstPng = path.join(OUT_DIR, `${cfg.frames[0].outName}.png`)
  if (existsSync(firstPng)) await ensureThumbJpeg(firstPng, thumbPath)
  return thumbPath
}

// Invalidate cached PNGs for one product. Called after draft saves so the
// thumb reflects the new caption/start/count next time it's requested.
export function invalidateRenderedFrames(handle: string): void {
  const prefix = `${handle}-`
  if (existsSync(OUT_DIR)) {
    for (const f of readdirSync(OUT_DIR)) {
      if (!f.startsWith(prefix) || !f.endsWith('.png')) continue
      try { unlinkSync(path.join(OUT_DIR, f)) } catch { /* ignore */ }
    }
  }
  if (existsSync(THUMB_DIR)) {
    for (const f of readdirSync(THUMB_DIR)) {
      if (!f.startsWith(prefix) || !f.endsWith('.jpg')) continue
      try { unlinkSync(path.join(THUMB_DIR, f)) } catch { /* ignore */ }
    }
  }
}

// Invalidate every cached product PNG. Called after look-settings changes,
// which affect all thumbs. Drop frames live in OUT_DIR/drops and are left
// alone here; they have their own lifecycle.
export function invalidateAllRenderedFrames(): void {
  if (existsSync(OUT_DIR)) {
    for (const f of readdirSync(OUT_DIR)) {
      if (!f.endsWith('.png')) continue
      try { unlinkSync(path.join(OUT_DIR, f)) } catch { /* ignore */ }
    }
  }
  if (existsSync(THUMB_DIR)) {
    for (const f of readdirSync(THUMB_DIR)) {
      if (!f.endsWith('.jpg')) continue
      try { unlinkSync(path.join(THUMB_DIR, f)) } catch { /* ignore */ }
    }
  }
}

export function firstFramePath(handle: string): string {
  return path.join(OUT_DIR, `${handle}-1.png`)
}

export interface PublishResult {
  post: Post
}

export async function publishHandle(handle: string): Promise<PublishResult> {
  const db = openDb()
  try {
    const bundle = getProductByHandle(db, handle)
    if (!bundle) throw new Error(`Product not found: ${handle}`)

    const existing = db
      .prepare(`SELECT * FROM posts WHERE handle = ? AND template = ? AND status = 'published'`)
      .get(handle, TEMPLATE) as Post | undefined
    if (existing) throw new Error(`Already published: ${handle}`)

    const createdAt = new Date().toISOString()
    const ins = db
      .prepare(
        `INSERT INTO posts (product_id, handle, template, status, created_at)
         VALUES (?, ?, ?, 'publishing', ?)`,
      )
      .run(bundle.product.id, handle, TEMPLATE, createdAt)
    const postId = ins.lastInsertRowid as number

    try {
      const cfg = resolveConfig(handle)
      const outPaths = await renderForHandle(handle, { force: true })
      const readFile = await import('node:fs/promises').then((m) => m.readFile)
      const stamp = Date.now()

      const r2Keys: string[] = []
      const r2Urls: string[] = []
      for (let i = 0; i < outPaths.length; i++) {
        const png = await readFile(outPaths[i])
        const jpeg = await sharp(png).jpeg({ quality: 92, progressive: true }).toBuffer()
        const r2Key = `stories/${handle}-${stamp}-${i + 1}.jpg`
        const r2Url = await uploadStoryImage(r2Key, jpeg, 'image/jpeg')
        r2Keys.push(r2Key)
        r2Urls.push(r2Url)
      }
      db.prepare(`UPDATE posts SET r2_key = ?, r2_url = ? WHERE id = ?`).run(
        JSON.stringify(r2Keys),
        JSON.stringify(r2Urls),
        postId,
      )

      const containerIds: string[] = []
      const mediaIds: string[] = []
      for (const url of r2Urls) {
        const { containerId, mediaId } = await postStoryFromUrl(url, cfg.link || undefined)
        containerIds.push(containerId)
        mediaIds.push(mediaId)
      }
      const publishedAt = new Date().toISOString()
      db.prepare(
        `UPDATE posts SET status = 'published', ig_container_id = ?, ig_media_id = ?, ig_media_ids = ?, published_at = ? WHERE id = ?`,
      ).run(
        JSON.stringify(containerIds),
        mediaIds[0] ?? null,
        JSON.stringify(mediaIds),
        publishedAt,
        postId,
      )

      const post = db.prepare(`SELECT * FROM posts WHERE id = ?`).get(postId) as Post
      return { post }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      db.prepare(`UPDATE posts SET status = 'failed', error = ? WHERE id = ?`).run(msg, postId)
      throw e
    }
  } finally {
    db.close()
  }
}

export interface ReviewItem {
  handle: string
  title: string
  brand: string
  price: string
  inventory_quantity: number
  image_count: number
  status: Post['status'] | 'unposted'
  published_at: string | null
  error: string | null
  ig_media_id: string | null
}

export function listReviewItems(): ReviewItem[] {
  const db = openDb()
  try {
    // Pick the first in-stock variant (by position) for both price and qty.
    // online_store_url IS NOT NULL excludes POS-only products and drafted listings
    // (a product not published to the Online Store sales channel has no public URL).
    const rows = db
      .prepare(
        `SELECT p.handle, p.title, p.vendor,
                v.price, v.inventory_quantity,
                (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) AS image_count
         FROM products p
         JOIN variants v ON v.id = (
           SELECT id FROM variants
           WHERE product_id = p.id AND inventory_quantity > 0
           ORDER BY position LIMIT 1
         )
         WHERE p.status = 'ACTIVE'
           AND p.online_store_url IS NOT NULL
           AND (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) >= 1
         ORDER BY p.title`,
      )
      .all() as Array<{
        handle: string
        title: string
        vendor: string | null
        price: string | null
        inventory_quantity: number | null
        image_count: number
      }>

    return rows.map((r) => {
      const latest = db
        .prepare(
          `SELECT status, published_at, error, ig_media_id FROM posts
           WHERE handle = ? AND template = ? ORDER BY id DESC LIMIT 1`,
        )
        .get(r.handle, TEMPLATE) as Pick<Post, 'status' | 'published_at' | 'error' | 'ig_media_id'> | undefined

      const brand = r.vendor || ''
      return {
        handle: r.handle,
        title: stripBrandPrefix(r.title, brand),
        brand,
        price: formatPrice(r.price),
        inventory_quantity: r.inventory_quantity ?? 0,
        image_count: r.image_count,
        status: latest?.status ?? 'unposted',
        published_at: latest?.published_at ?? null,
        error: latest?.error ?? null,
        ig_media_id: latest?.ig_media_id ?? null,
      }
    })
  } finally {
    db.close()
  }
}

export interface ProductDraftInfo {
  handle: string
  title: string
  vendor: string
  defaults: { brand: string; name: string; price: string; link: string }
  draft: Draft | null
  images: Array<{ position: number; path: string }>
  // What the auto-categorizer would pick for this product. The UI shows this
  // next to the "Auto" option so users see what they're keeping or overriding.
  auto_category: Category
}

export function getProductDraftInfo(handle: string): ProductDraftInfo | null {
  const db = openDb()
  try {
    const bundle = getProductByHandle(db, handle)
    if (!bundle) return null
    const draft = getDraft(db, handle)
    const brand = bundle.product.vendor || ''
    return {
      handle: bundle.product.handle,
      title: bundle.product.title,
      vendor: brand,
      defaults: {
        brand,
        name: stripBrandPrefix(bundle.product.title, brand),
        price: formatPrice(bundle.variant?.price),
        link: `https://paststudies.shop/products/${handle}`,
      },
      draft,
      images: bundle.media.map((m, i) => ({ position: m.position ?? i, path: m.local_path })),
      auto_category: categorize(bundle.product.product_type, bundle.product.title),
    }
  } finally {
    db.close()
  }
}

export function getProductImagePath(handle: string, position: number): string | null {
  const db = openDb()
  try {
    const bundle = getProductByHandle(db, handle)
    if (!bundle) return null
    const m = bundle.media.find((m) => m.position === position) || bundle.media[position]
    return m?.local_path ?? null
  } finally {
    db.close()
  }
}

// ═══════════════════════════════════════════════════════════════════════
// Product-drop flow
// ═══════════════════════════════════════════════════════════════════════

const DROPS_OUT_DIR = path.join(OUT_DIR, 'drops')

export interface DropProductFrame {
  kind: 'product'
  outName: string
  handle: string
  brand: string
  name: string
  price: string
  imagePath: string
  linkUrl: string | null // IG link sticker URL for this story
  productType: string | null
  rawTitle: string
  lookCategoryOverride: Category | null
}

export interface DropCoverFrame {
  kind: 'cover'
  role: 'opening' | 'closing'
  outName: string
  body: string[]
  footer: string | null
  trailingArrow: boolean
  linkUrl: string | null
}

export type DropFrame = DropCoverFrame | DropProductFrame

export interface ResolvedDrop {
  drop: Drop
  items: Array<DropItem & { totalImages: number }>
  frames: DropFrame[]
  // Handles the drop lists that are no longer in the catalog (sold/removed/
  // renamed in Shopify). They're skipped rather than fatal so the rest of the
  // drop still renders and posts.
  missing: string[]
}

const MEDIA_CACHE_DIR = path.join(ROOT, '.cache/media')
const IMG_EXT = /\.(jpe?g|png|webp)$/i

// Cached images for a handle still on disk, even if the product has left the
// catalog. Returns ROOT-relative paths matching media.local_path.
function diskMediaFor(handle: string): string[] {
  const dir = path.join(MEDIA_CACHE_DIR, handle)
  if (!existsSync(dir)) return []
  try {
    return readdirSync(dir)
      .filter((f) => IMG_EXT.test(f))
      .sort()
      .map((f) => path.join('.cache/media', handle, f))
  } catch {
    return []
  }
}

// Best-effort brand + display title from a bare handle, for a legacy item with
// no snapshot and no live product. stripBrandPrefix trims the vendor at render.
function deriveFromHandle(handle: string, vendors: string[]): { title: string; vendor: string | null } {
  const spaced = handle.replace(/-/g, ' ').trim()
  const title = spaced.replace(/\b\w/g, (c) => c.toUpperCase())
  const lc = ' ' + spaced.toLowerCase() + ' '
  let vendor: string | null = null
  for (const v of vendors) {
    if (lc.includes(' ' + v.toLowerCase() + ' ')) { vendor = v; break }
  }
  return { title, vendor }
}

interface ItemRender {
  title: string
  vendor: string | null
  price: string | null
  productType: string | null
  media: string[]
}

export function resolveDrop(dropId: number): ResolvedDrop {
  const db = openDb()
  try {
    const drop = getDrop(db, dropId)
    if (!drop) throw new Error(`Drop ${dropId} not found`)
    const items = getDropItems(db, dropId)
    const vendors = listAllVendors(db)
    const frames: DropFrame[] = []

    if (drop.include_opening) {
      frames.push({
        kind: 'cover',
        role: 'opening',
        outName: `drop-${dropId}-1-cover-open`,
        body: drop.cover_body,
        footer: drop.cover_footer,
        trailingArrow: drop.trailing_arrow,
        linkUrl: drop.cover_cta_url,
      })
    }

    const hydratedItems: Array<DropItem & { totalImages: number }> = []
    const missing: string[] = []
    let frameIdx = frames.length + 1
    for (const item of items) {
      // Resolve the item's render data, most-current first, so a sold / removed
      // / renamed product never blanks the drop:
      //   live by handle → live by product_id (rename) → frozen snapshot →
      //   on-disk cached images (brand/title derived) → truly gone.
      let render: ItemRender | null = null
      let bundle = getProductByHandle(db, item.handle)
      if ((!bundle || !bundle.media.length) && item.product_id) {
        const byId = getProductById(db, item.product_id)
        if (byId && byId.media.length) bundle = byId
      }
      if (bundle && bundle.media.length) {
        render = {
          title: bundle.product.title,
          vendor: bundle.product.vendor,
          price: bundle.variant?.price ?? null,
          productType: bundle.product.product_type,
          media: bundle.media.map((m) => m.local_path).filter((p): p is string => !!p),
        }
      } else if (item.snapshot && item.snapshot.media.length) {
        render = {
          title: item.snapshot.title,
          vendor: item.snapshot.vendor,
          price: item.snapshot.price,
          productType: item.snapshot.product_type,
          media: item.snapshot.media,
        }
      } else {
        const disk = diskMediaFor(item.handle)
        if (disk.length) {
          const d = deriveFromHandle(item.handle, vendors)
          render = { title: d.title, vendor: d.vendor, price: null, productType: null, media: disk }
        }
      }

      if (!render || !render.media.length) {
        missing.push(item.handle)
        continue
      }

      const total = render.media.length
      const start = Math.min(Math.max(0, item.image_start), Math.max(0, total - 1))
      const count = Math.max(1, Math.min(item.image_count, total - start))
      hydratedItems.push({ ...item, image_start: start, image_count: count, totalImages: total })

      const vendor = render.vendor || ''
      const name = stripBrandPrefix(render.title, render.vendor)
      const price = formatPrice(render.price)
      const productUrl = brand.productUrlTemplate(item.handle)
      const itemDraft = getDraft(db, item.handle)
      for (let i = 0; i < count; i++) {
        frames.push({
          kind: 'product',
          outName: `drop-${dropId}-${frameIdx}-${item.handle}-${i + 1}`,
          handle: item.handle,
          brand: vendor,
          name,
          price,
          imagePath: render.media[start + i],
          linkUrl: productUrl,
          productType: render.productType,
          rawTitle: render.title,
          lookCategoryOverride: itemDraft?.look_category ?? null,
        })
        frameIdx++
      }
    }

    if (drop.include_closing && drop.closing_body.length > 0) {
      frames.push({
        kind: 'cover',
        role: 'closing',
        outName: `drop-${dropId}-${frameIdx}-cover-close`,
        body: drop.closing_body,
        footer: null,
        trailingArrow: true,
        linkUrl: drop.cover_cta_url,
      })
    }

    return { drop, items: hydratedItems, frames, missing }
  } finally {
    db.close()
  }
}

export async function renderDropFramePng(frame: DropFrame): Promise<Buffer> {
  if (frame.kind === 'cover') {
    return renderCoverPng({
      body: frame.body,
      footer: frame.footer ?? undefined,
      trailingArrow: frame.trailingArrow,
    })
  }
  return renderStoryPng({
    brand: frame.brand,
    name: frame.name,
    price: frame.price,
    imagePaths: [frame.imagePath],
    layout: '1up',
    look: getLookSettings(frame.lookCategoryOverride ?? categorize(frame.productType, frame.rawTitle)),
  })
}

export async function renderDropAll(dropId: number): Promise<string[]> {
  mkdirSync(DROPS_OUT_DIR, { recursive: true })
  const { frames } = resolveDrop(dropId)
  const outPaths: string[] = []
  const keep = new Set<string>()
  for (const frame of frames) {
    const png = await renderDropFramePng(frame)
    const fileName = `${frame.outName}.png`
    const outPath = path.join(DROPS_OUT_DIR, fileName)
    writeFileSync(outPath, png)
    outPaths.push(outPath)
    keep.add(fileName)
  }
  // cleanup stale frames from prior renders of this drop
  if (existsSync(DROPS_OUT_DIR)) {
    const prefix = `drop-${dropId}-`
    for (const f of readdirSync(DROPS_OUT_DIR)) {
      if (!f.startsWith(prefix) || !f.endsWith('.png')) continue
      if (!keep.has(f)) {
        try { unlinkSync(path.join(DROPS_OUT_DIR, f)) } catch { /* ignore */ }
      }
    }
  }
  return outPaths
}

export interface PublishDropResult {
  drop: Drop
}

export async function publishDrop(dropId: number): Promise<PublishDropResult> {
  const db = openDb()
  try {
    const existing = getDrop(db, dropId)
    if (!existing) throw new Error(`Drop ${dropId} not found`)
    if (existing.status === 'published') throw new Error(`Drop ${dropId} already published`)
    if (existing.status === 'publishing') throw new Error(`Drop ${dropId} is currently publishing`)

    // Atomic claim: the scheduler loop and a manual "Post now" can race, so
    // the status flip doubles as a lock — whoever loses the UPDATE bails here.
    const claim = db
      .prepare(
        `UPDATE drops SET status = 'publishing', error = NULL, updated_at = ?
         WHERE id = ? AND status IN ('draft', 'scheduled', 'failed')`,
      )
      .run(new Date().toISOString(), dropId)
    if (claim.changes === 0) throw new Error(`Drop ${dropId} is already being published`)

    try {
      // Refuse upfront if the drop won't fit in IG's rolling 24h publishing
      // quota — otherwise it dies midway with half the sequence live.
      const { frames, missing } = resolveDrop(dropId)
      if (missing.length) {
        console.warn(`[publish] drop ${dropId} skipping ${missing.length} missing product(s): ${missing.join(', ')}`)
      }
      if (!frames.length) {
        throw new Error(
          `Drop ${dropId} has nothing to post — all ${missing.length} product(s) are missing from the catalog` +
            `${missing.length ? ` (${missing.join(', ')})` : ''}. Re-add current products.`,
        )
      }
      let quotaError: string | null = null
      try {
        const { quota_usage, quota_total } = await getPublishingLimit()
        const remaining = Math.max(0, quota_total - quota_usage)
        if (frames.length > remaining) {
          quotaError = `Drop needs ${frames.length} story posts but only ${remaining} remain in IG's 24h publishing quota (${quota_usage}/${quota_total} used). Trim images per item or wait for the quota to roll over.`
        }
      } catch {
        // Quota check is best-effort — don't block publishing if the API hiccups.
      }
      if (quotaError) throw new Error(quotaError)

      const outPaths = await renderDropAll(dropId)
      const stamp = Date.now()
      const r2Urls: string[] = []
      for (let i = 0; i < outPaths.length; i++) {
        const png = await readFile(outPaths[i])
        const jpeg = await sharp(png).jpeg({ quality: 92, progressive: true }).toBuffer()
        const r2Key = `drops/${dropId}-${stamp}-${i + 1}.jpg`
        const url = await uploadStoryImage(r2Key, jpeg, 'image/jpeg')
        r2Urls.push(url)
      }

      const containerIds: string[] = []
      const mediaIds: string[] = []
      for (let i = 0; i < r2Urls.length; i++) {
        const link = frames[i]?.linkUrl || undefined
        const { containerId, mediaId } = await postStoryFromUrl(r2Urls[i], link)
        containerIds.push(containerId)
        mediaIds.push(mediaId)
      }

      const published = updateDrop(db, dropId, {
        status: 'published',
        ig_container_ids: containerIds,
        ig_media_ids: mediaIds,
        published_at: new Date().toISOString(),
        error: null,
      })
      return { drop: published }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      updateDrop(db, dropId, { status: 'failed', error: msg })
      throw e
    }
  } finally {
    db.close()
  }
}
