import { writeFileSync, mkdirSync, existsSync, readdirSync, unlinkSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'
import {
  openDb,
  getProductByHandle,
  getDraft,
  getDrop,
  getDropItems,
  updateDrop,
  type Post,
  type Draft,
  type Drop,
  type DropItem,
  type Layout,
} from './db.js'
import { renderStoryPng, renderCoverPng } from './render.js'
import { uploadStoryImage } from './upload.js'
import { postStoryFromUrl } from './instagram.js'
import { brand } from './brand.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT_DIR = path.join(ROOT, 'out')

const TEMPLATE = 'story_grid_4'

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
    }
  } finally {
    db.close()
  }
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

export async function renderForHandle(handle: string): Promise<string[]> {
  mkdirSync(OUT_DIR, { recursive: true })
  const cfg = resolveConfig(handle)
  const outPaths: string[] = []
  const keep = new Set<string>()
  for (const frame of cfg.frames) {
    const png = await renderStoryPng({
      brand: cfg.brand,
      name: cfg.name,
      price: cfg.price,
      imagePaths: frame.imagePaths,
      layout: cfg.layout,
    })
    const fileName = `${frame.outName}.png`
    const outPath = path.join(OUT_DIR, fileName)
    writeFileSync(outPath, png)
    outPaths.push(outPath)
    keep.add(fileName)
  }
  cleanStaleFrames(handle, keep)
  return outPaths
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
      const outPaths = await renderForHandle(handle)
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
  image_count: number
  status: Post['status'] | 'unposted'
  published_at: string | null
  error: string | null
  ig_media_id: string | null
}

export function listReviewItems(): ReviewItem[] {
  const db = openDb()
  try {
    const rows = db
      .prepare(
        `SELECT p.handle, p.title, p.vendor,
                (SELECT v.price FROM variants v WHERE v.product_id = p.id ORDER BY v.position LIMIT 1) AS price,
                (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) AS image_count
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.total_inventory > 0
           AND (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) >= 1
         ORDER BY p.title`,
      )
      .all() as Array<{
        handle: string
        title: string
        vendor: string | null
        price: string | null
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
}

export function resolveDrop(dropId: number): ResolvedDrop {
  const db = openDb()
  try {
    const drop = getDrop(db, dropId)
    if (!drop) throw new Error(`Drop ${dropId} not found`)
    const items = getDropItems(db, dropId)
    const frames: DropFrame[] = []

    frames.push({
      kind: 'cover',
      role: 'opening',
      outName: `drop-${dropId}-1-cover-open`,
      body: drop.cover_body,
      footer: drop.cover_footer,
      trailingArrow: drop.trailing_arrow,
      linkUrl: drop.cover_cta_url,
    })

    const hydratedItems: Array<DropItem & { totalImages: number }> = []
    let frameIdx = 2
    for (const item of items) {
      const bundle = getProductByHandle(db, item.handle)
      if (!bundle) throw new Error(`Drop ${dropId} references missing product: ${item.handle}`)
      const total = bundle.media.length
      const start = Math.min(Math.max(0, item.image_start), Math.max(0, total - 1))
      const count = Math.max(1, Math.min(item.image_count, total - start))
      hydratedItems.push({ ...item, image_start: start, image_count: count, totalImages: total })

      const vendor = bundle.product.vendor || ''
      const name = stripBrandPrefix(bundle.product.title, vendor)
      const price = formatPrice(bundle.variant?.price)
      const productUrl = brand.productUrlTemplate(item.handle)
      for (let i = 0; i < count; i++) {
        frames.push({
          kind: 'product',
          outName: `drop-${dropId}-${frameIdx}-${item.handle}-${i + 1}`,
          handle: item.handle,
          brand: vendor,
          name,
          price,
          imagePath: bundle.media[start + i].local_path,
          linkUrl: productUrl,
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

    return { drop, items: hydratedItems, frames }
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

    updateDrop(db, dropId, { status: 'publishing', error: null })

    try {
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

      const { frames } = resolveDrop(dropId)
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
