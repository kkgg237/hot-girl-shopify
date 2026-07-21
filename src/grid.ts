import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import sharp from 'sharp'
import { openDb, getProductByHandle, getDrop, getDropItems, getScheduledGrid, updateScheduledGrid } from './db.js'
import { brand, wordmarkImagePath } from './brand.js'
import { uploadStoryImage } from './upload.js'
import { postStoryFromUrl } from './instagram.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

// Promo grids: a collage of product tiles from a drop, for teaser stories and
// feed posts. Pure image compositing, so sharp is used directly instead of
// the satori text pipeline.

export type GridFormat = 'story' | 'post' | 'square'

const FORMAT_DIMS: Record<GridFormat, { w: number; h: number }> = {
  story: { w: 1080, h: 1920 }, // IG story 9:16
  post: { w: 1080, h: 1350 }, // IG feed portrait 4:5
  square: { w: 1080, h: 1080 }, // IG feed square
}

// cols x rows presets the UI offers. Whitelisted so the endpoint can't be
// asked for a 100x100 composite.
export const GRID_PRESETS = ['2x2', '2x3', '3x3', '3x4', '4x4'] as const
export type GridPreset = (typeof GRID_PRESETS)[number]

export function parseGridPreset(raw: string | undefined): { cols: number; rows: number } | null {
  if (!raw || !(GRID_PRESETS as readonly string[]).includes(raw)) return null
  const [cols, rows] = raw.split('x').map(Number)
  return { cols, rows }
}

export function parseGridFormat(raw: string | undefined): GridFormat {
  if (raw === 'post' || raw === 'square') return raw
  return 'story'
}

// Deterministic PRNG so a given seed always reproduces the same grid — the
// Shuffle button just rolls a new seed, and Download/Post reuse the seed of
// the preview the user is looking at.
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function shuffled<T>(arr: T[], rand: () => number): T[] {
  const out = arr.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

export interface GridOptions {
  cols: number
  rows: number
  format: GridFormat
  seed: number
}

export async function renderDropGridJpeg(dropId: number, opts: GridOptions): Promise<Buffer> {
  // Image pool: every item's lead image (the image_start frame chosen for the
  // drop) first, remaining product photos as backfill. Tiles cycle through
  // the pool if the drop is smaller than the grid.
  const leads: string[] = []
  const extras: string[] = []
  const db = openDb()
  try {
    const drop = getDrop(db, dropId)
    if (!drop) throw new Error(`Drop ${dropId} not found`)
    const items = getDropItems(db, dropId)
    if (!items.length) throw new Error('Drop has no products yet — add some first')
    for (const item of items) {
      const bundle = getProductByHandle(db, item.handle)
      if (!bundle || !bundle.media.length) continue
      const start = Math.min(Math.max(0, item.image_start), bundle.media.length - 1)
      leads.push(bundle.media[start].local_path)
      for (let i = 0; i < bundle.media.length; i++) {
        if (i !== start) extras.push(bundle.media[i].local_path)
      }
    }
  } finally {
    db.close()
  }
  if (!leads.length) throw new Error('No product images available in this drop')

  const rand = mulberry32(opts.seed)
  const pool = shuffled(leads, rand).concat(shuffled(extras, rand))
  const n = opts.cols * opts.rows
  const tiles: string[] = []
  for (let i = 0; i < n; i++) tiles.push(pool[i % pool.length])

  const { w, h } = FORMAT_DIMS[opts.format]
  // Story format keeps a header band with the wordmark so the teaser reads
  // as the brand even before the first product story.
  const headerH = opts.format === 'story' ? 300 : 0

  // Tiles are flush — no gaps, edge to edge. Cell boundaries are computed by
  // rounding fractional positions so leftover pixels spread invisibly across
  // cells instead of piling up as a seam at one edge.
  const gridH = h - headerH
  const xs = Array.from({ length: opts.cols + 1 }, (_, i) => Math.round((i * w) / opts.cols))
  const ys = Array.from({ length: opts.rows + 1 }, (_, i) => headerH + Math.round((i * gridH) / opts.rows))

  const composites: sharp.OverlayOptions[] = []
  for (let i = 0; i < tiles.length; i++) {
    const r = Math.floor(i / opts.cols)
    const c = i % opts.cols
    const abs = path.resolve(ROOT, tiles[i])
    const buf = await sharp(abs)
      .resize(xs[c + 1] - xs[c], ys[r + 1] - ys[r], { fit: 'cover' })
      .toBuffer()
    composites.push({ input: buf, left: xs[c], top: ys[r] })
  }

  if (headerH > 0 && existsSync(wordmarkImagePath)) {
    const markH = 190
    const mark = sharp(wordmarkImagePath).resize({ height: markH })
    const meta = await mark.metadata()
    const markBuf = await mark.toBuffer()
    const markW = meta.width && meta.height ? Math.round((meta.width / meta.height) * markH) : markH
    composites.push({
      input: markBuf,
      left: Math.floor((w - markW) / 2),
      top: Math.floor((headerH - markH) / 2),
    })
  }

  return sharp({
    create: {
      width: w,
      height: h,
      channels: 3,
      background: brand.colors.storyBg, // white, matching the product stories
    },
  })
    .composite(composites)
    .jpeg({ quality: 92, progressive: true })
    .toBuffer()
}

// Render the story-format grid and post it to IG stories immediately, with
// the drop's CTA URL as the link sticker (same link the covers use).
export async function postDropGridStory(
  dropId: number,
  opts: Omit<GridOptions, 'format'>,
): Promise<{ mediaId: string }> {
  const jpeg = await renderDropGridJpeg(dropId, { ...opts, format: 'story' })
  let ctaUrl: string | null = null
  const db = openDb()
  try {
    ctaUrl = getDrop(db, dropId)?.cover_cta_url ?? null
  } finally {
    db.close()
  }
  const key = `drops/${dropId}-grid-${Date.now()}.jpg`
  const url = await uploadStoryImage(key, jpeg, 'image/jpeg')
  const { mediaId } = await postStoryFromUrl(url, ctaUrl ?? undefined)
  return { mediaId }
}

// Publish a calendar-scheduled grid post. Mirrors publishDrop's atomic status
// claim so the scheduler loop and a manual "post now" can't double-fire. The db
// connection is released before the (slow) render+network work so it isn't held
// open across I/O.
export async function publishScheduledGrid(id: number): Promise<{ mediaId: string }> {
  let dropId: number
  let preset: { cols: number; rows: number }
  let seed: number
  const db = openDb()
  try {
    const sg = getScheduledGrid(db, id)
    if (!sg) throw new Error(`Scheduled grid ${id} not found`)
    if (sg.status === 'published') throw new Error(`Scheduled grid ${id} already published`)
    const claim = db
      .prepare(
        `UPDATE scheduled_grids SET status = 'publishing', error = NULL
         WHERE id = ? AND status IN ('scheduled', 'failed')`,
      )
      .run(id)
    if (claim.changes === 0) throw new Error(`Scheduled grid ${id} is already being published`)
    dropId = sg.drop_id
    preset = parseGridPreset(sg.grid_preset) ?? { cols: 3, rows: 3 }
    seed = sg.grid_seed
  } finally {
    db.close()
  }

  try {
    const { mediaId } = await postDropGridStory(dropId, { cols: preset.cols, rows: preset.rows, seed })
    const done = openDb()
    try {
      updateScheduledGrid(done, id, {
        status: 'published',
        ig_media_id: mediaId,
        published_at: new Date().toISOString(),
        error: null,
      })
    } finally {
      done.close()
    }
    return { mediaId }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    const fail = openDb()
    try {
      updateScheduledGrid(fail, id, { status: 'failed', error: msg })
    } finally {
      fail.close()
    }
    throw e
  }
}
