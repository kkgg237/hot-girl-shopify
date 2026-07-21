import { Hono } from 'hono'
import { serve } from '@hono/node-server'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  listReviewItems,
  publishHandle,
  renderForHandle,
  renderThumbForHandle,
  resolveConfig,
  getProductDraftInfo,
  getProductImagePath,
  resolveDrop,
  renderDropFramePng,
  publishDrop,
  invalidateRenderedFrames,
  invalidateAllRenderedFrames,
  type ReviewItem,
} from './publish.js'
import {
  openDb,
  upsertDraft,
  searchProducts,
  listAllTags,
  listDrops,
  getDrop,
  getDropItems,
  createDrop,
  updateDrop,
  deleteDrop,
  setDropItems,
  backfillDropItemSnapshots,
  listScheduledGrids,
  getScheduledGrid,
  createScheduledGrid,
  updateScheduledGrid,
  deleteScheduledGrid,
  type Layout,
  type DropItem,
} from './db.js'
import { brand } from './brand.js'
import {
  getLookSettings,
  getAllLookSettings,
  saveLookSettings,
  resetLookSettings,
  categorize,
  CATEGORIES,
  type Category,
  type LookSettings,
} from './look-settings.js'
import { renderDropGridJpeg, postDropGridStory, publishScheduledGrid, parseGridPreset, parseGridFormat } from './grid.js'
import { notifyScheduledFailure } from './notify.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT_DIR = path.join(ROOT, 'out')
mkdirSync(OUT_DIR, { recursive: true })

function getLastSyncFinishedAt(): string | null {
  const db = openDb()
  try {
    const row = db
      .prepare(`SELECT finished_at FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1`)
      .get() as { finished_at: string } | undefined
    return row?.finished_at ?? null
  } finally {
    db.close()
  }
}

// One representative handle per look category, used as the live-preview thumb
// in the Look dashboard so the user sees the kind of product they're actively
// styling. Falls back to whatever's available if a category has no products.
function getSampleHandleByCategory(): Record<Category, string | null> {
  const out: Record<Category, string | null> = { clothing: null, bags: null }
  const db = openDb()
  try {
    const rows = db
      .prepare(
        `SELECT p.handle, p.title, p.product_type
         FROM products p
         WHERE p.status = 'ACTIVE' AND p.online_store_url IS NOT NULL
           AND (SELECT COUNT(*) FROM media m WHERE m.product_id = p.id AND m.local_path IS NOT NULL) >= 1
         ORDER BY p.title`,
      )
      .all() as Array<{ handle: string; title: string; product_type: string | null }>
    for (const r of rows) {
      const cat = categorize(r.product_type, r.title)
      if (!out[cat]) out[cat] = r.handle
      if (out.clothing && out.bags) break
    }
    return out
  } finally {
    db.close()
  }
}

const app = new Hono()

function escape(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => {
    switch (ch) {
      case '&': return '&amp;'
      case '<': return '&lt;'
      case '>': return '&gt;'
      case '"': return '&quot;'
      case "'": return '&#39;'
      default: return ch
    }
  })
}

const STYLES = `
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; font-family: -apple-system, system-ui, "Helvetica Neue", sans-serif; background: #fafaf9; color: #111; }

  header { position: sticky; top: 0; z-index: 50; background: rgba(255,255,255,0.92); backdrop-filter: blur(12px); border-bottom: 1px solid #ececec; padding: 14px 32px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  header h1 { font-size: 14px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; margin: 0; color: #111; }

  .flow-tabs { display: flex; gap: 4px; background: #f4f4f4; border-radius: 8px; padding: 4px; }
  .flow-tab { padding: 8px 16px; background: transparent; color: #555; border: none; border-radius: 6px; font: inherit; font-size: 12px; font-weight: 600; letter-spacing: 0.03em; text-transform: none; cursor: pointer; transition: background 0.12s ease, color 0.12s ease; }
  .flow-tab:hover { color: #111; }
  .flow-tab.active { background: #fff; color: #111; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .tab-badge { font-size: 9px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.08em; margin-left: 4px; }
  header .counts { font-size: 12px; color: #888; letter-spacing: 0.04em; }
  header .filters { margin-left: auto; display: flex; gap: 10px; align-items: center; }
  header input, header select { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; min-width: 200px; }
  header input:focus, header select:focus { outline: none; border-color: #111; }
  .sync-btn { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 12px; background: #fff; color: #111; cursor: pointer; white-space: nowrap; }
  .sync-btn:hover:not(:disabled) { background: #f4f4f4; }
  .sync-btn:disabled { opacity: 0.55; cursor: wait; }
  .sync-meta { font-size: 10.5px; color: #888; letter-spacing: 0.04em; white-space: nowrap; }

  /* Look tab */
  .look-shell { display: flex; flex-direction: column; gap: 20px; max-width: 1200px; margin: 0 auto; }
  .look-cat-row { display: flex; align-items: center; gap: 8px; padding: 14px 18px; background: #fff; border: 1px solid #eee; border-radius: 10px; }
  .look-cat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: #888; font-weight: 600; margin-right: 4px; }
  .look-cat-tab { padding: 7px 14px; background: #fff; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #555; cursor: pointer; }
  .look-cat-tab.on { background: #111; color: #fff; border-color: #111; }
  .look-cat-tab:hover:not(.on) { background: #f4f4f4; }
  .look-cat-hint { font-size: 10px; color: #aaa; text-transform: uppercase; letter-spacing: 0.08em; flex: 1; margin-left: 8px; }
  .save-status { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.08em; }
  .save-status.saved { color: #2a8a4f; }
  .save-status.saving { color: #b88500; }

  .look-tab-grid { display: grid; grid-template-columns: 1fr 340px; gap: 24px; align-items: start; }
  .look-controls-pane { display: flex; flex-direction: column; gap: 18px; }
  .look-group { background: #fff; border: 1px solid #eee; border-radius: 10px; padding: 18px 20px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px 24px; }
  .look-group h4 { grid-column: 1 / -1; margin: 0 0 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: #888; }
  .look-ctrl { display: flex; flex-direction: column; gap: 6px; }
  .look-ctrl label { font-size: 11px; color: #333; font-weight: 500; display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .ctrl-val { font-size: 11px; color: #888; font-weight: 500; font-variant-numeric: tabular-nums; }
  .ctrl-hint { font-size: 10px; color: #aaa; text-transform: uppercase; letter-spacing: 0.06em; }
  .look-ctrl input[type=range] { width: 100%; accent-color: #111; }
  .look-ctrl input[type=range]:disabled { opacity: 0.4; }
  .look-ctrl .row-h { display: flex; align-items: center; gap: 8px; }
  .look-ctrl input[type=color] { width: 44px; height: 32px; padding: 2px; border: 1px solid #ddd; border-radius: 4px; background: #fff; cursor: pointer; }
  .color-ctrl { grid-column: 1 / -1; }
  .seg { display: flex; gap: 0; border: 1px solid #ddd; border-radius: 6px; overflow: hidden; }
  .seg-btn { flex: 1; padding: 8px; background: #fff; color: #555; border: none; font: inherit; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; border-right: 1px solid #ddd; }
  .seg-btn:last-child { border-right: none; }
  .seg-btn.on { background: #111; color: #fff; }
  .seg-btn:hover:not(.on) { background: #f4f4f4; }
  .toggles { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .toggles label.toggle { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #333; cursor: pointer; }
  .toggles label.toggle input { accent-color: #111; }
  .look-reset-btn { align-self: flex-start; padding: 9px 16px; background: #fff; color: #555; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; cursor: pointer; }
  .look-reset-btn:hover { background: #f4f4f4; }

  .look-preview-pane { background: #fff; border: 1px solid #eee; border-radius: 10px; padding: 18px; display: flex; flex-direction: column; align-items: center; gap: 12px; position: sticky; top: 80px; }
  .look-preview-pane .preview-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: #888; font-weight: 600; }
  .look-preview-pane .frame { width: 300px; aspect-ratio: 9/16; background: #f0f0f0; border: 1px solid #eee; border-radius: 6px; overflow: hidden; }
  .look-preview-pane .frame img { width: 100%; height: 100%; object-fit: cover; display: block; }

  main { padding: 28px 32px 80px; }
  section h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: #888; margin: 28px 0 16px; font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 18px; }

  .card { background: #fff; border: 1px solid #eee; border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; transition: transform 0.12s ease, box-shadow 0.12s ease; }
  .card:hover { transform: translateY(-2px); box-shadow: 0 6px 24px rgba(0,0,0,0.06); }
  .card img.thumb { width: 100%; aspect-ratio: 9/16; object-fit: cover; display: block; background: #f0f0f0; cursor: pointer; }
  .meta { padding: 12px 14px; font-size: 12px; line-height: 1.35; }
  .meta .brand { color: #888; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 500; }
  .meta .title { color: #111; font-weight: 600; margin: 4px 0 2px; text-transform: uppercase; font-size: 11px; letter-spacing: 0.02em; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; min-height: 30px; }
  .meta .price { color: #555; font-size: 11px; }
  .actions { display: flex; gap: 6px; padding: 0 14px 14px; }
  button { padding: 9px 12px; border: 1px solid #111; background: #111; color: #fff; font: inherit; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; cursor: pointer; border-radius: 6px; flex: 1; transition: background 0.12s ease; }
  button:hover { background: #333; }
  button.secondary { background: #fff; color: #111; }
  button.secondary:hover { background: #f4f4f4; }
  button:disabled { background: #aaa; border-color: #aaa; cursor: default; }
  .badge { padding: 9px 12px; text-align: center; font-size: 10px; color: #0a7d3a; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; flex: 1; background: #f0faf3; border-radius: 6px; }
  .error-msg { padding: 9px 12px; font-size: 10px; color: #a00; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Modal */
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 100; padding: 24px; }
  .modal-backdrop.open { display: flex; }
  .modal { background: #fff; border-radius: 14px; width: 100%; max-width: 1080px; max-height: 92vh; overflow: hidden; display: grid; grid-template-columns: 420px 1fr; }
  .modal .preview { background: #f4f4f4; padding: 24px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; }
  .modal .preview img { max-width: 100%; max-height: 72vh; box-shadow: 0 4px 28px rgba(0,0,0,0.12); border-radius: 4px; }
  .frame-nav { display: flex; align-items: center; gap: 12px; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: #555; }
  .frame-nav button { padding: 6px 10px; background: #fff; color: #111; border: 1px solid #ddd; border-radius: 6px; cursor: pointer; font-size: 12px; flex: 0 0 auto; }
  .frame-nav button:hover { background: #f0f0f0; }
  .frame-nav button:disabled { opacity: 0.35; cursor: default; }
  .frame-nav .frame-label { min-width: 60px; text-align: center; font-weight: 600; color: #111; }

  .count-pick { display: flex; align-items: center; gap: 8px; }
  .count-pick button { padding: 8px 14px; background: #fff; color: #111; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 14px; cursor: pointer; flex: 0 0 auto; line-height: 1; }
  .count-pick button:hover { background: #f0f0f0; }
  .count-pick input { width: 64px; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; text-align: center; background: #fff; color: #111; }
  .count-pick input:focus { outline: none; border-color: #111; }
  .count-pick .count-hint { font-size: 10px; color: #888; letter-spacing: 0.04em; }
  .modal .form { padding: 28px 32px; overflow-y: auto; max-height: 92vh; }
  .modal h3 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: #111; margin: 0 0 6px; }
  .modal .product-title { font-size: 18px; font-weight: 600; margin: 0 0 24px; color: #111; }
  .modal label { display: block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin: 16px 0 6px; }
  .modal input[type=text] { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; }
  .modal input[type=text]:focus { outline: none; border-color: #111; }
  .modal select { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; }
  .modal select:focus { outline: none; border-color: #111; }
  .layout-pick { display: flex; gap: 6px; }
  .layout-pick button { padding: 10px; font-size: 10px; flex: 1; }
  .layout-pick button.on { background: #111; color: #fff; }
  .layout-pick button.off { background: #fff; color: #111; }
  .img-strip { display: flex; gap: 4px; overflow-x: auto; padding-bottom: 6px; margin-top: 6px; }
  .img-strip .pic { flex-shrink: 0; width: 72px; height: 108px; border-radius: 4px; cursor: pointer; background-size: cover; background-position: center; position: relative; border: 2px solid transparent; }
  .img-strip .pic.selected { border-color: #111; }
  .img-strip .pic .num { position: absolute; top: 2px; left: 2px; background: rgba(0,0,0,0.7); color: #fff; font-size: 9px; padding: 1px 5px; border-radius: 3px; }
  .img-range-hint { font-size: 10px; color: #888; margin-top: 6px; letter-spacing: 0.04em; }
  .modal-actions { display: flex; gap: 8px; padding-top: 24px; border-top: 1px solid #eee; margin-top: 24px; position: sticky; bottom: 0; background: #fff; padding-bottom: 4px; }
  .modal-actions button { padding: 11px; font-size: 11px; }
  .modal-actions .primary { flex: 2; }
  .modal-actions .reset { flex: 1; }
  .close-x { position: absolute; top: 18px; right: 18px; background: #fff; border: 1px solid #ddd; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 16px; z-index: 1; }
  .close-x:hover { background: #f4f4f4; }

  .empty { color: #888; font-size: 12px; padding: 40px 0; text-align: center; }

  /* WIP panel */
  .wip-panel { max-width: 640px; margin: 60px auto; padding: 40px; background: #fff; border: 1px solid #eee; border-radius: 14px; text-align: center; }
  .wip-panel h2 { font-size: 18px; margin: 0 0 16px; color: #111; }
  .wip-panel p { font-size: 14px; color: #555; line-height: 1.6; margin: 12px 0; }

  /* Product drop shell */
  .drop-shell { display: flex; flex-direction: column; gap: 18px; }
  .drop-topbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .drop-topbar label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #888; }
  .drop-topbar select, .drop-topbar input { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; }
  .drop-topbar button { padding: 8px 12px; font-size: 11px; }
  .drop-missing { background: #fff6e5; border: 1px solid #f0dca8; color: #8a6100; border-radius: 8px; padding: 10px 14px; font-size: 12px; line-height: 1.45; }
  .drop-missing strong { font-weight: 700; }
  .drop-missing button { margin-left: 8px; padding: 4px 10px; font-size: 11px; font-weight: 600; border: 1px solid #e0c98a; background: #fff; border-radius: 5px; cursor: pointer; color: #8a6100; }
  .drop-missing button:hover { background: #fbf1da; }
  .drop-status { margin-left: auto; font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.08em; }

  .drop-grid { display: grid; grid-template-columns: minmax(340px, 1fr) minmax(360px, 1fr); gap: 24px; }
  .drop-picker, .drop-compose { background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 18px 18px 22px; }
  .drop-picker h3, .drop-compose h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: #555; margin: 0 0 12px; font-weight: 600; }

  .picker-tabs { display: inline-flex; gap: 4px; background: #f4f4f4; border-radius: 8px; padding: 4px; margin-bottom: 10px; }
  .picker-tab { padding: 6px 12px; background: transparent; border: none; border-radius: 6px; font: inherit; font-size: 11px; font-weight: 600; cursor: pointer; color: #555; }
  .picker-tab.on { background: #fff; color: #111; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .picker-controls { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
  .picker-controls input { flex: 1; min-width: 140px; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; }
  .picker-controls select { padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 12px; background: #fff; color: #111; max-width: 220px; }
  .picker-controls .imgs-per { display: flex; align-items: center; gap: 6px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: #888; white-space: nowrap; }
  .picker-controls button { flex: 0 0 auto; }
  .picker-hint { font-size: 10.5px; color: #888; letter-spacing: 0.03em; margin: -4px 0 10px; line-height: 1.5; }
  .picker-hint .warn { color: #b88500; }

  .grid-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
  .grid-controls select { padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 12px; background: #fff; color: #111; }
  .grid-controls button { flex: 0 0 auto; padding: 9px 14px; }
  .btn-like { display: inline-block; padding: 9px 14px; border: 1px solid #111; border-radius: 6px; background: #fff; color: #111; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; text-decoration: none; }
  .btn-like:hover { background: #f4f4f4; }

  .drop-schedule { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .drop-schedule .sched-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: #888; }
  .drop-schedule input[type=datetime-local] { padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 12px; background: #fff; color: #111; }
  .drop-schedule button { flex: 0 0 auto; padding: 9px 14px; }

  .picker-list { max-height: 520px; overflow-y: auto; border: 1px solid #f0f0f0; border-radius: 8px; padding: 4px; }
  .picker-row { display: grid; grid-template-columns: 22px 56px 1fr auto; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 6px; cursor: pointer; }
  .picker-row:hover { background: #fafafa; }
  .picker-row.picked { background: #f0faf3; }
  .picker-row img { width: 56px; height: 64px; object-fit: cover; border-radius: 4px; background: #f0f0f0; }
  .picker-row .meta { font-size: 12px; line-height: 1.3; min-width: 0; }
  .picker-row .meta .title { font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .picker-row .meta .sub { color: #888; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
  .picker-row .img-count { font-size: 10px; color: #555; background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }

  .drop-items { display: flex; flex-direction: column; gap: 6px; min-height: 60px; }
  .drop-empty { color: #aaa; font-size: 11px; padding: 16px; text-align: center; border: 1px dashed #e6e6e6; border-radius: 8px; }
  .drop-item { display: grid; grid-template-columns: 56px 1fr auto auto auto; align-items: center; gap: 10px; padding: 8px; background: #fafafa; border-radius: 8px; }
  .drop-item img { width: 56px; height: 64px; object-fit: cover; border-radius: 4px; background: #f0f0f0; }
  .drop-item .di-meta { font-size: 12px; min-width: 0; }
  .drop-item .di-title { font-weight: 600; color: #111; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .drop-item .di-sub { color: #888; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
  .drop-item .di-count { display: flex; align-items: center; gap: 4px; }
  .drop-item .di-count button { padding: 4px 8px; font-size: 11px; background: #fff; color: #111; border: 1px solid #ddd; }
  .drop-item .di-count span { font-size: 11px; min-width: 18px; text-align: center; }
  .drop-item .di-move { display: flex; flex-direction: column; gap: 2px; }
  .drop-item .di-move button { padding: 2px 6px; font-size: 10px; line-height: 1; background: #fff; color: #555; border: 1px solid #e6e6e6; }
  .drop-item .di-remove { padding: 6px 10px; font-size: 10px; background: #fff; color: #a00; border: 1px solid #f0d0d0; }

  .drop-compose textarea, .drop-compose input[type=text] { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; resize: vertical; }
  .drop-compose label { display: block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin: 14px 0 6px; }
  .drop-compose label.row-checkbox { display: flex; align-items: center; gap: 8px; text-transform: none; font-size: 12px; }
  .drop-compose label.row-checkbox input { width: auto; }

  .drop-preview { margin-top: 18px; padding: 14px; background: #f4f4f4; border-radius: 10px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
  .drop-preview img { max-width: 240px; max-height: 420px; box-shadow: 0 2px 14px rgba(0,0,0,0.1); border-radius: 4px; }

  .drop-actions { display: flex; gap: 8px; margin-top: 18px; }
  .drop-actions button { flex: 1; padding: 11px; }
  .drop-actions .secondary { flex: 0 0 auto; }

  /* Calendar tab */
  .cal-shell { max-width: 1100px; margin: 0 auto; }
  .cal-bar { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; flex-wrap: wrap; }
  .cal-nav { display: flex; align-items: center; gap: 4px; }
  .cal-nav button { width: 34px; height: 34px; padding: 0; border: 1px solid #ddd; border-radius: 6px; background: #fff; color: #111; font-size: 15px; cursor: pointer; line-height: 1; }
  .cal-nav button:hover { background: #f4f4f4; }
  .cal-today-btn { height: 34px; padding: 0 14px; border: 1px solid #ddd; border-radius: 6px; background: #fff; color: #111; font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
  .cal-today-btn:hover { background: #f4f4f4; }
  .cal-label { font-size: 16px; font-weight: 600; letter-spacing: 0.02em; min-width: 210px; }
  .cal-views { display: flex; gap: 4px; background: #f4f4f4; border-radius: 8px; padding: 4px; margin-left: auto; }
  .cal-view-btn { padding: 7px 14px; background: transparent; border: none; border-radius: 6px; font: inherit; font-size: 12px; font-weight: 600; color: #555; cursor: pointer; }
  .cal-view-btn.on { background: #fff; color: #111; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .cal-summary { font-size: 12px; color: #888; letter-spacing: 0.03em; }

  .cal-dow { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; margin-bottom: 6px; }
  .cal-dow > div { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #aaa; text-align: left; padding-left: 4px; }
  .cal-weeks { display: flex; flex-direction: column; gap: 6px; }
  .cal-week { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
  .cal-day { min-height: 104px; border: 1px solid #ececec; border-radius: 8px; background: #fff; padding: 6px; display: flex; flex-direction: column; gap: 4px; }
  .cal-day.out { background: #fafaf9; opacity: 0.55; }
  .cal-day.past { background: #fbfbfa; }
  .cal-day.today { border-color: #111; box-shadow: inset 0 0 0 1px #111; }
  .cal-daynum { font-size: 11px; font-weight: 600; color: #888; padding: 1px 3px; }
  .cal-day.today .cal-daynum { color: #111; }
  .cal-chip { display: block; width: 100%; text-align: left; border: none; border-radius: 5px; padding: 4px 6px; font: inherit; font-size: 11px; line-height: 1.25; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cal-chip.scheduled { background: #eef4ff; color: #1b4fa0; }
  .cal-chip.scheduled:hover { background: #dfe9fb; }
  .cal-chip.published { background: #eef7f0; color: #2a7a4a; }
  .cal-chip.published:hover { background: #e2f1e7; }
  .cal-chip.publishing { background: #fff5e6; color: #a86500; }
  .cal-chip.publishing:hover { background: #ffedd0; }
  .cal-chip.failed { background: #fdecec; color: #b3261e; }
  .cal-chip.failed:hover { background: #f9dcdc; }
  .cal-chip.grid { border-left: 3px solid currentColor; padding-left: 4px; }
  .cal-kind { opacity: 0.6; font-size: 9px; margin-right: 1px; }
  .cal-chip-time { font-weight: 700; }
  .cal-legend { display: flex; gap: 16px; margin-top: 16px; font-size: 11px; color: #888; align-items: center; flex-wrap: wrap; }
  .cal-legend span { display: inline-flex; align-items: center; gap: 6px; }
  .cal-legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .cal-legend i.scheduled { background: #cfe0fb; }
  .cal-legend i.published { background: #d3ecdb; }
  .cal-legend i.publishing { background: #ffd8a0; }
  .cal-legend i.failed { background: #f3b4b0; }
  .cal-legend .cal-hint { margin-left: auto; color: #bbb; }
  .cal-empty { text-align: center; color: #aaa; font-size: 13px; padding: 40px 0; }

  /* Cross-tab failure banner */
  .alert-banner { background: #b3261e; color: #fff; padding: 11px 22px; font-size: 13px; display: flex; align-items: center; gap: 14px; cursor: pointer; }
  .alert-banner strong { font-weight: 700; white-space: nowrap; }
  .alert-banner .alert-detail { color: #ffd9d6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .alert-banner .alert-cta { margin-left: auto; text-decoration: underline; white-space: nowrap; font-weight: 600; }

  /* Calendar event modal (create + detail) */
  .cal-modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; align-items: center; justify-content: center; z-index: 100; }
  .cal-modal-backdrop.open { display: flex; }
  .cal-modal { background: #fff; border-radius: 12px; width: min(540px, 92vw); max-height: 90vh; overflow: auto; padding: 24px; position: relative; box-shadow: 0 12px 40px rgba(0,0,0,0.25); }
  .cal-modal .close-x { position: absolute; top: 12px; right: 14px; border: none; background: transparent; font-size: 22px; cursor: pointer; color: #999; line-height: 1; }
  .cal-modal h3 { margin: 0 0 4px; font-size: 17px; }
  .cal-modal .cm-sub { font-size: 12px; color: #888; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .cal-modal label { display: block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: #888; margin: 12px 0 5px; }
  .cal-modal input[type=text], .cal-modal input[type=datetime-local], .cal-modal select { width: 100%; padding: 9px 11px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; background: #fff; color: #111; }
  .cm-row { display: flex; gap: 10px; }
  .cm-row > div { flex: 1; }
  .cm-preview { margin-top: 14px; text-align: center; background: #f4f4f4; border-radius: 8px; padding: 12px; min-height: 60px; }
  .cm-preview img { max-height: 300px; max-width: 100%; border-radius: 4px; box-shadow: 0 2px 12px rgba(0,0,0,0.12); }
  .cm-shuffle-btn { margin-top: 8px; padding: 6px 12px; border: 1px solid #ddd; border-radius: 6px; background: #fff; font: inherit; font-size: 12px; cursor: pointer; }
  .cm-shuffle-btn:hover { background: #f4f4f4; }
  .cm-actions { display: flex; gap: 8px; margin-top: 20px; }
  .cm-actions button { padding: 10px 14px; border-radius: 6px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; border: 1px solid #ddd; background: #fff; color: #111; }
  .cm-actions button:hover { background: #f4f4f4; }
  .cm-actions .primary { background: #111; color: #fff; border-color: #111; flex: 1; }
  .cm-actions .primary:hover { background: #333; }
  .cm-actions .danger { color: #b3261e; border-color: #f0d0d0; margin-left: auto; }
  .cm-status { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; }
  .cm-status.scheduled { background: #eef4ff; color: #1b4fa0; }
  .cm-status.publishing { background: #fff5e6; color: #a86500; }
  .cm-status.published { background: #eef7f0; color: #2a7a4a; }
  .cm-status.failed { background: #fdecec; color: #b3261e; }
  .cm-error { margin-top: 12px; background: #fdecec; color: #b3261e; padding: 10px 12px; border-radius: 6px; font-size: 12px; line-height: 1.4; }
`

const FLOWS = [
  {
    slug: 'product-drop',
    title: 'Product drop',
    description: 'Cover card + multiple products posted as one continuous story sequence.',
    status: 'ready',
  },
  {
    slug: 'calendar',
    title: 'Calendar',
    description: 'Two-week and month views of what drops are scheduled to post.',
    status: 'ready',
  },
  {
    slug: 'single-product',
    title: 'Single product',
    description: 'Pick a product and post a sequence of story photos with caption.',
    status: 'ready',
  },
  {
    slug: 'studio-event',
    title: 'Studio / event',
    description: 'Announcement card with custom copy and photos (open house, drops, hours).',
    status: 'wip',
  },
  {
    slug: 'look',
    title: 'Look',
    description: 'Caption styling (position, color, sizes) per category.',
    status: 'ready',
  },
] as const

app.get('/flow/:slug', (c) => c.redirect('/#' + c.req.param('slug')))

app.get('/cover-preview', async (c) => {
  const { renderCoverPng } = await import('./render.js')
  const { brand } = await import('./brand.js')
  const variant = c.req.query('v') ?? 'drop'

  const variants: Record<string, { body: string[]; footer?: string; trailingArrow?: boolean }> = {
    drop: {
      body: [brand.copy.dropIntro],
      footer: brand.copy.shippingUSA,
      trailingArrow: true,
    },
    'drop-short': {
      body: ['New drop available now'],
      footer: brand.copy.shippingUSA,
      trailingArrow: true,
    },
    event: {
      body: ['Open hours', 'Saturday 12 – 4 PM', 'Sunday 12 – 4 PM'],
      footer: brand.studioLocation,
    },
    appt: {
      body: ['Visit our West Loop, Chicago studio', 'Book an appointment here'],
      trailingArrow: true,
    },
    hours: {
      body: ['Open hours', 'Saturday 12 – 4 PM', 'Sunday 12 – 4 PM', 'Weekdays by appointment', 'Book here'],
      trailingArrow: true,
    },
    minimal: {
      body: [],
    },
  }

  const v = variants[variant] ?? variants.drop
  const png = await renderCoverPng({ body: v.body, footer: v.footer, trailingArrow: v.trailingArrow })
  return new Response(png as unknown as BodyInit, {
    status: 200,
    headers: { 'Content-Type': 'image/png', 'Cache-Control': 'no-cache' },
  })
})

app.get('/', (c) => {
  const items = listReviewItems()
  const vendors = Array.from(new Set(items.map((i) => i.brand).filter(Boolean))).sort()
  const unposted = items.filter((i) => i.status === 'unposted' || i.status === 'failed')
  const done = items.filter((i) => i.status === 'published')
  const publishing = items.filter((i) => i.status === 'publishing')
  const lastSyncIso = getLastSyncFinishedAt()
  const looksByCat = getAllLookSettings()
  const sampleByCat = getSampleHandleByCategory()
  // Fall back to any pending/posted handle if the category had nothing.
  const fallbackSample = unposted[0]?.handle ?? items[0]?.handle ?? ''
  if (!sampleByCat.clothing) sampleByCat.clothing = fallbackSample
  if (!sampleByCat.bags) sampleByCat.bags = fallbackSample
  // Initial open shows clothing — most products are clothing-like.
  const initialCategory: Category = 'clothing'
  const look = looksByCat[initialCategory]
  const initialSample = sampleByCat[initialCategory]

  const card = (i: ReviewItem) => `
    <div class="card" data-handle="${i.handle}" data-brand="${escape(i.brand.toLowerCase())}" data-title="${escape(i.title.toLowerCase())}" data-status="${i.status}">
      <img class="thumb" src="/thumb/${i.handle}" loading="lazy" data-handle="${i.handle}" />
      <div class="meta">
        <div class="brand">${escape(i.brand) || '&nbsp;'}</div>
        <div class="title">${escape(i.title)}</div>
        <div class="price">${i.price ? escape(i.price) : '&nbsp;'} · qty ${i.inventory_quantity} · ${i.image_count} images</div>
      </div>
      <div class="actions">
        ${
          i.status === 'unposted' || i.status === 'failed'
            ? `
              <button class="secondary edit-btn" data-handle="${i.handle}">Edit</button>
              <button class="approve-btn" data-handle="${i.handle}">Post</button>
            `
            : ''
        }
        ${i.status === 'published' ? `<div class="badge">✓ posted</div>` : ''}
        ${i.status === 'publishing' ? `<div class="badge">posting…</div>` : ''}
      </div>
      ${i.status === 'failed' && i.error ? `<div class="error-msg">${escape(i.error)}</div>` : ''}
    </div>
  `

  return c.html(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>past studies / review</title>
<style>${STYLES}</style>
</head>
<body>
<div class="alert-banner" id="alert-banner" hidden></div>
<header>
  <h1>past studies</h1>
  <div class="flow-tabs" id="flow-tabs">
    ${FLOWS.map((f) => `
      <button type="button" class="flow-tab" data-flow="${f.slug}">
        ${escape(f.title)}${f.status === 'wip' ? ' <span class="tab-badge">soon</span>' : ''}
      </button>
    `).join('')}
  </div>
  <div class="counts" id="counts">${unposted.length} pending · ${publishing.length} in-flight · ${done.length} posted</div>
  <div class="filters" id="flow-filters">
    <input id="search" type="text" placeholder="search title or brand…" autocomplete="off" />
    <select id="vendor-filter">
      <option value="">all brands</option>
      ${vendors.map((v) => `<option value="${escape(v.toLowerCase())}">${escape(v)}</option>`).join('')}
    </select>
  </div>
  <div class="filters" id="sync-controls">
    <button id="sync-btn" class="sync-btn" type="button" title="Re-fetch products + media from Shopify">↻ Refresh</button>
    <span id="sync-meta" class="sync-meta" data-last="${lastSyncIso ?? ''}">${lastSyncIso ? '' : 'never synced'}</span>
  </div>
</header>

<main>
  <div class="flow-panel" id="panel-single-product">
    <section>
      <h2 id="pending-header">pending (${unposted.length})</h2>
      <div class="grid" id="grid-pending">${unposted.map(card).join('')}</div>
      <div class="empty" id="empty-pending" style="display:none">No matches.</div>
    </section>

    ${publishing.length ? `<section><h2>publishing (${publishing.length})</h2><div class="grid">${publishing.map(card).join('')}</div></section>` : ''}

    ${done.length ? `<section><h2 id="posted-header">posted (${done.length})</h2><div class="grid" id="grid-posted">${done.map(card).join('')}</div></section>` : ''}
  </div>

  <div class="flow-panel" id="panel-product-drop" hidden>
    <div class="drop-shell">
      <div class="drop-topbar">
        <label>Drop:</label>
        <select id="drop-select"></select>
        <button id="drop-new" class="secondary">+ New drop</button>
        <input id="drop-name" type="text" placeholder="Drop name (optional)" />
        <span class="drop-status" id="drop-status"></span>
      </div>

      <div class="drop-missing" id="drop-missing" hidden></div>

      <div class="drop-grid">
        <section class="drop-picker">
          <h3>Add products</h3>
          <div class="picker-tabs">
            <button data-mode="newest" class="picker-tab on">Newest</button>
            <button data-mode="tag" class="picker-tab">By tag</button>
          </div>
          <div class="picker-controls">
            <select id="picker-tag" style="display:none"><option value="">— pick a tag —</option></select>
            <input id="picker-query" type="text" placeholder="search title/vendor" />
            <label class="imgs-per">imgs/item
              <select id="picker-imgs-per">
                <option value="1">1</option>
                <option value="2" selected>2</option>
                <option value="3">3</option>
                <option value="4">4</option>
              </select>
            </label>
            <button id="picker-add" class="secondary">Add selected</button>
            <button id="picker-add-all" class="primary">Add all</button>
          </div>
          <div class="picker-hint" id="picker-hint"></div>
          <div class="picker-list" id="picker-list"></div>
        </section>

        <section class="drop-compose">
          <h3>In this drop</h3>
          <div class="drop-items" id="drop-items"></div>

          <h3 style="margin-top:24px">Opening cover</h3>
          <label class="row-checkbox"><input id="cover-include" type="checkbox" /> Include opening cover at start of sequence</label>
          <label>Body (one line per textarea row)</label>
          <textarea id="cover-body" rows="4"></textarea>
          <label>Footer</label>
          <input id="cover-footer" type="text" />
          <label class="row-checkbox"><input id="cover-arrow" type="checkbox" /> Trailing arrow on opening cover</label>

          <h3 style="margin-top:24px">Closing cover</h3>
          <label class="row-checkbox"><input id="closing-include" type="checkbox" /> Include closing cover at end of sequence</label>
          <label>Body</label>
          <textarea id="closing-body" rows="3"></textarea>

          <h3 style="margin-top:24px">CTA URL (drop link)</h3>
          <label>Used as the IG link sticker on the opening + closing covers. Each product story links to its own product page automatically.</label>
          <input id="cover-cta-url" type="text" />

          <div class="drop-preview">
            <img id="drop-preview-img" alt="cover preview" />
            <div class="frame-nav" id="drop-frame-nav">
              <button id="drop-frame-prev">‹</button>
              <span class="frame-label" id="drop-frame-label">1 / 1</span>
              <button id="drop-frame-next">›</button>
            </div>
          </div>

          <h3 style="margin-top:24px" id="grid-heading">Promo grid</h3>
          <div class="grid-controls" id="grid-controls">
            <select id="grid-size">
              <option value="2x2">2 × 2</option>
              <option value="2x3" selected>2 × 3</option>
              <option value="3x3">3 × 3</option>
              <option value="3x4">3 × 4</option>
              <option value="4x4">4 × 4</option>
            </select>
            <select id="grid-format">
              <option value="story" selected>Story 9:16</option>
              <option value="post">Post 4:5</option>
              <option value="square">Square 1:1</option>
            </select>
            <button id="grid-shuffle" class="secondary" type="button">↻ Shuffle</button>
            <a id="grid-download" class="btn-like" href="#" download>Download</a>
            <button id="grid-post" class="secondary" type="button">Post as story</button>
          </div>
          <div class="drop-preview" id="grid-preview-wrap">
            <img id="grid-preview-img" alt="promo grid preview" />
          </div>

          <div class="drop-actions">
            <button class="secondary" id="drop-delete">Delete drop</button>
            <button class="primary" id="drop-publish">Post now</button>
          </div>
          <div class="drop-schedule">
            <span class="sched-label">or schedule for</span>
            <input id="drop-schedule-at" type="datetime-local" />
            <button class="secondary" id="drop-schedule-btn">Schedule</button>
            <button class="secondary" id="drop-unschedule-btn" style="display:none">Cancel schedule</button>
          </div>
        </section>
      </div>
    </div>
  </div>

  <div class="flow-panel" id="panel-calendar" hidden>
    <div class="cal-shell">
      <div class="cal-bar">
        <div class="cal-nav">
          <button type="button" id="cal-prev" title="Previous">‹</button>
          <button type="button" id="cal-next" title="Next">›</button>
        </div>
        <button type="button" id="cal-today" class="cal-today-btn">Today</button>
        <div class="cal-label" id="cal-label"></div>
        <span class="cal-summary" id="cal-summary"></span>
        <div class="cal-views">
          <button type="button" class="cal-view-btn on" data-view="month">Month</button>
          <button type="button" class="cal-view-btn" data-view="twoweek">2 weeks</button>
        </div>
      </div>
      <div id="cal-grid"></div>
      <div class="cal-legend">
        <span><i class="scheduled"></i> Scheduled</span>
        <span><i class="publishing"></i> Posting…</span>
        <span><i class="failed"></i> Failed</span>
        <span><i class="published"></i> Posted</span>
        <span class="cal-hint">▦ grid · ❖ story sequence — click a day to schedule a grid</span>
      </div>
    </div>
  </div>

  <div class="flow-panel" id="panel-studio-event" hidden>
    <div class="wip-panel">
      <h2>Studio / event — coming soon</h2>
      <p>This flow will let you author an announcement card (open house, new drop, hours) and post it as a cover-style IG story, with optional studio photos.</p>
    </div>
  </div>

  <div class="flow-panel" id="panel-look" hidden>
    <div class="look-shell">
      <div class="look-cat-row">
        <span class="look-cat-label">Category</span>
        ${CATEGORIES.map((cat) => `
          <button type="button" class="look-cat-tab${cat === initialCategory ? ' on' : ''}" data-cat="${cat}">${cat[0].toUpperCase() + cat.slice(1)}</button>
        `).join('')}
        <span class="look-cat-hint">Edits below apply only to the selected category.</span>
        <span id="look-save-status" class="save-status saved">saved</span>
      </div>

      <div class="look-tab-grid">
        <div class="look-controls-pane">
          <section class="look-group">
            <h4>Position</h4>
            <div class="look-ctrl">
              <label>Horizontal <span id="L-xPct-val" class="ctrl-val">${look.xPct.toFixed(1)}%</span></label>
              <input id="L-xPct" type="range" min="0" max="50" step="0.5" value="${look.xPct}" />
              <div class="ctrl-hint">distance from active edge</div>
            </div>
            <div class="look-ctrl">
              <label>Vertical <span id="L-yPct-val" class="ctrl-val">${look.yPct.toFixed(1)}%</span></label>
              <input id="L-yPct" type="range" min="0" max="100" step="0.5" value="${look.yPct}" />
              <div class="ctrl-hint">distance from top</div>
            </div>
            <div class="look-ctrl">
              <label>Anchor</label>
              <div class="seg" id="L-align-seg">
                ${(['left','center','right'] as const).map((a) => `<button type="button" class="seg-btn${look.align === a ? ' on' : ''}" data-align="${a}">${a[0].toUpperCase() + a.slice(1)}</button>`).join('')}
              </div>
            </div>
          </section>

          <section class="look-group">
            <h4>Style</h4>
            <div class="look-ctrl color-ctrl">
              <label>Color</label>
              <div class="row-h"><input id="L-color" type="color" value="${look.color}" /><span id="L-color-val" class="ctrl-val">${look.color}</span></div>
            </div>
            <div class="look-ctrl">
              <label>Brand size <span id="L-brandSize-val" class="ctrl-val">${look.brandSize}</span></label>
              <input id="L-brandSize" type="range" min="14" max="120" value="${look.brandSize}" />
            </div>
            <div class="look-ctrl">
              <label>Name size <span id="L-nameSize-val" class="ctrl-val">${look.nameSize}</span></label>
              <input id="L-nameSize" type="range" min="14" max="120" value="${look.nameSize}" />
            </div>
            <div class="look-ctrl">
              <label>Price size <span id="L-priceSize-val" class="ctrl-val">${look.priceSize}</span></label>
              <input id="L-priceSize" type="range" min="14" max="120" value="${look.priceSize}" />
            </div>
          </section>

          <section class="look-group">
            <h4>Show</h4>
            <div class="toggles">
              <label class="toggle"><input id="L-showBrand" type="checkbox"${look.showBrand ? ' checked' : ''} /> Brand</label>
              <label class="toggle"><input id="L-showName" type="checkbox"${look.showName ? ' checked' : ''} /> Name</label>
              <label class="toggle"><input id="L-showPrice" type="checkbox"${look.showPrice ? ' checked' : ''} /> Price</label>
              <label class="toggle"><input id="L-showDollar" type="checkbox"${look.showDollar ? ' checked' : ''} /> $ on price</label>
            </div>
          </section>

          <button type="button" id="look-reset" class="look-reset-btn">Reset ${initialCategory} to defaults</button>
        </div>

        <div class="look-preview-pane">
          <div class="preview-label">Live preview</div>
          <div class="frame"><img id="look-preview-img" src="${initialSample ? `/thumb/${initialSample}?lv=0` : ''}" alt="" /></div>
        </div>
      </div>
    </div>
  </div>
</main>

<div class="cal-modal-backdrop" id="cal-modal-backdrop">
  <div class="cal-modal" id="cal-modal">
    <button class="close-x" id="cal-modal-close">×</button>
    <div id="cal-modal-body"></div>
  </div>
</div>

<div class="modal-backdrop" id="modal-backdrop">
  <div class="modal" id="modal">
    <button class="close-x" id="close-modal">×</button>
    <div class="preview">
      <img id="preview-img" alt="preview" />
      <div class="frame-nav" id="frame-nav" style="display:none">
        <button type="button" id="frame-prev">‹</button>
        <span class="frame-label" id="frame-label">1 / 1</span>
        <button type="button" id="frame-next">›</button>
      </div>
    </div>
    <div class="form" id="form">
      <h3 id="modal-brand">…</h3>
      <div class="product-title" id="modal-title">…</div>

      <label>Starting image (consecutive from here)</label>
      <div class="img-strip" id="img-strip"></div>
      <div class="img-range-hint" id="range-hint">Using images 1-4 of N</div>

      <label>Number of stories in sequence</label>
      <div class="count-pick" id="count-pick">
        <button type="button" id="count-minus">−</button>
        <input id="f-count" type="number" min="1" value="4" />
        <button type="button" id="count-plus">+</button>
        <span class="count-hint" id="count-hint"></span>
      </div>

      <label>Brand</label>
      <input id="f-brand" type="text" />

      <label>Item name</label>
      <input id="f-name" type="text" />

      <label>Price (no symbol)</label>
      <input id="f-price" type="text" />

      <label>Story link (paststudies.shop product URL — leave blank for no link sticker)</label>
      <input id="f-link" type="text" />

      <label>Look category (overrides the auto-detect for this product)</label>
      <select id="f-category">
        <option value="">Auto (detect from product type + title)</option>
        <option value="clothing">Force: Clothing</option>
        <option value="bags">Force: Bags</option>
      </select>
      <div id="f-category-hint" class="img-range-hint"></div>

      <div class="modal-actions">
        <button class="reset secondary" id="btn-reset">Reset</button>
        <button class="secondary" id="btn-save">Save</button>
        <button class="primary" id="btn-post">Approve & Post</button>
      </div>
    </div>
  </div>
</div>

<script>
const $ = (sel, root=document) => root.querySelector(sel)
const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel))

// ---- Look tab ----
// Per-category presets — both initial states are embedded so we can switch
// instantly without a fetch. Saves go to the active category.
const LOOK_PRESETS = ${JSON.stringify(looksByCat)}
const LOOK_SAMPLES = ${JSON.stringify(sampleByCat)}
let lookActiveCategory = ${JSON.stringify(initialCategory)}

// Custom alignment segmented control: writes value back via .dataset.value on
// the container so collectLookState can read it like any other field.
const alignSeg = document.getElementById('L-align-seg')
function setSegAlign(val) {
  if (!alignSeg) return
  alignSeg.dataset.value = val
  alignSeg.querySelectorAll('.seg-btn').forEach((b) => {
    b.classList.toggle('on', b.dataset.align === val)
  })
}
setSegAlign(${JSON.stringify(look.align)})

const LOOK_FIELDS = [
  { id: 'color', type: 'color' },
  { id: 'xPct', type: 'range', valFmt: 'pct' },
  { id: 'yPct', type: 'range', valFmt: 'pct' },
  { id: 'brandSize', type: 'range' },
  { id: 'nameSize', type: 'range' },
  { id: 'priceSize', type: 'range' },
  { id: 'showBrand', type: 'checkbox' },
  { id: 'showName', type: 'checkbox' },
  { id: 'showPrice', type: 'checkbox' },
  { id: 'showDollar', type: 'checkbox' },
]

function formatVal(f, raw) {
  if (f.valFmt === 'pct') return Number(raw).toFixed(1) + '%'
  return String(raw)
}

function collectLookState() {
  const out = {}
  for (const f of LOOK_FIELDS) {
    const el = document.getElementById('L-' + f.id)
    if (!el) continue
    if (f.type === 'checkbox') out[f.id] = el.checked
    else if (f.type === 'range') out[f.id] = Number(el.value)
    else out[f.id] = el.value
  }
  out.align = (alignSeg && alignSeg.dataset.value) || 'left'
  return out
}

// Live value labels next to range sliders + the hex readout next to the color picker.
for (const f of LOOK_FIELDS) {
  if (f.type !== 'range') continue
  const range = document.getElementById('L-' + f.id)
  const val = document.getElementById('L-' + f.id + '-val')
  if (range && val) {
    range.addEventListener('input', () => { val.textContent = formatVal(f, range.value) })
  }
}
const colorInput = document.getElementById('L-color')
const colorVal = document.getElementById('L-color-val')
if (colorInput && colorVal) {
  colorInput.addEventListener('input', () => { colorVal.textContent = colorInput.value })
}

let lookSaveTimer = null
let lookSaveSeq = 0
const lookStatus = document.getElementById('look-save-status')
const lookPreview = document.getElementById('look-preview-img')

function setLookStatus(text, cls) {
  if (!lookStatus) return
  lookStatus.textContent = text
  lookStatus.className = 'save-status ' + (cls || '')
}

async function persistLook() {
  const seq = ++lookSaveSeq
  setLookStatus('saving…', 'saving')
  // Snapshot category at the moment of save — if the user switches categories
  // between the debounce schedule and the actual PUT, the change still goes
  // to the category they were editing, not the one they switched to.
  const cat = lookActiveCategory
  const body = collectLookState()
  try {
    const r = await fetch('/api/look-settings?category=' + encodeURIComponent(cat), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!r.ok) throw new Error('save failed')
    if (seq !== lookSaveSeq) return // a newer save kicked off
    // Mirror the change locally so tab-switch round-trips don't lose pending edits.
    LOOK_PRESETS[cat] = { ...LOOK_PRESETS[cat], ...body }
    setLookStatus('saved', 'saved')
    // Cache-bust preview + all card thumbs
    const stamp = Date.now()
    if (lookPreview) {
      const src = lookPreview.getAttribute('src') || ''
      const base = src.split('?')[0]
      if (base) lookPreview.src = base + '?lv=' + stamp
    }
    document.querySelectorAll('img.thumb').forEach((img) => {
      const src = img.getAttribute('src') || ''
      const base = src.split('?')[0]
      if (base) img.src = base + '?lv=' + stamp
    })
  } catch (e) {
    setLookStatus('save failed', '')
  }
}

function scheduleSave() {
  if (lookSuspendSave) return
  if (lookSaveTimer) clearTimeout(lookSaveTimer)
  setLookStatus('pending…', 'saving')
  lookSaveTimer = setTimeout(() => { lookSaveTimer = null; persistLook() }, 250)
}

// Cancel the debounce timer and run the save immediately. Used before any
// context switch (category change, tab change) so the most recent edit
// can't be overwritten by a populateLookControls() call.
async function flushPendingLookSave() {
  if (!lookSaveTimer) return
  clearTimeout(lookSaveTimer)
  lookSaveTimer = null
  await persistLook()
}

// Page-unload safety net: if the user navigates away or closes the tab while
// a save is still debouncing, fire it with keepalive so the browser doesn't
// abort it on unload. Synchronous-ish — no awaiting needed.
window.addEventListener('beforeunload', () => {
  if (!lookSaveTimer) return
  clearTimeout(lookSaveTimer)
  lookSaveTimer = null
  try {
    fetch('/api/look-settings?category=' + encodeURIComponent(lookActiveCategory), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectLookState()),
      keepalive: true,
    })
  } catch {}
})

for (const f of LOOK_FIELDS) {
  const el = document.getElementById('L-' + f.id)
  if (!el) continue
  const evt = f.type === 'checkbox' ? 'change' : 'input'
  el.addEventListener(evt, scheduleSave)
}
// Alignment segmented control fires on click.
if (alignSeg) {
  alignSeg.querySelectorAll('.seg-btn').forEach((b) => {
    b.addEventListener('click', () => {
      setSegAlign(b.dataset.align)
      syncXDisabled()
      scheduleSave()
    })
  })
}

const lookReset = document.getElementById('look-reset')
if (lookReset) {
  lookReset.addEventListener('click', async () => {
    if (!confirm('Reset ' + lookActiveCategory + ' look to defaults?')) return
    const r = await fetch('/api/look-settings?category=' + encodeURIComponent(lookActiveCategory), { method: 'DELETE' })
    if (!r.ok) { alert('Reset failed'); return }
    const fresh = await r.json()
    LOOK_PRESETS[lookActiveCategory] = fresh
    populateLookControls(fresh)
    syncXDisabled()
    // Cache-bust the preview + thumbs since render output changed.
    const stamp = Date.now()
    if (lookPreview) {
      const src = lookPreview.getAttribute('src') || ''
      const base = src.split('?')[0]
      if (base) lookPreview.src = base + '?lv=' + stamp
    }
    document.querySelectorAll('img.thumb').forEach((img) => {
      const src = img.getAttribute('src') || ''
      const base = src.split('?')[0]
      if (base) img.src = base + '?lv=' + stamp
    })
    setLookStatus('reset', 'saved')
  })
}

// Populate controls from a preset object. Skips events; caller should call
// syncXDisabled() after if alignment relevance might have changed.
function populateLookControls(preset) {
  for (const f of LOOK_FIELDS) {
    const el = document.getElementById('L-' + f.id)
    if (!el) continue
    const v = preset[f.id]
    if (f.type === 'checkbox') el.checked = !!v
    else el.value = (v === undefined || v === null) ? '' : v
    if (f.type === 'range') {
      const val = document.getElementById('L-' + f.id + '-val')
      if (val) val.textContent = formatVal(f, el.value)
    }
  }
  setSegAlign(preset.align || 'left')
  const colorVal = document.getElementById('L-color-val')
  if (colorVal) colorVal.textContent = preset.color
  const resetBtn = document.getElementById('look-reset')
  if (resetBtn) resetBtn.textContent = 'Reset ' + lookActiveCategory + ' to defaults'
}

// Category tab switcher: changes which preset is being edited, swaps the
// preview thumb to a representative product, and silences autosave during the
// repopulation so we don't immediately write the just-loaded values back.
let lookSuspendSave = false
async function switchLookCategory(cat) {
  if (cat === lookActiveCategory) return
  if (!LOOK_PRESETS[cat]) return
  // Flush any pending edit against the OUTGOING category before we
  // overwrite the DOM with the incoming category's values. Without this,
  // a fast click after a slider tweak silently drops the tweak.
  await flushPendingLookSave()
  lookActiveCategory = cat
  document.querySelectorAll('.look-cat-tab').forEach((b) => {
    b.classList.toggle('on', b.dataset.cat === cat)
  })
  lookSuspendSave = true
  try { populateLookControls(LOOK_PRESETS[cat]) } finally { lookSuspendSave = false }
  syncXDisabled()
  if (lookPreview && LOOK_SAMPLES[cat]) {
    lookPreview.src = '/thumb/' + LOOK_SAMPLES[cat] + '?lv=' + Date.now()
  }
  setLookStatus('saved', 'saved')
}
document.querySelectorAll('.look-cat-tab').forEach((b) => {
  b.addEventListener('click', () => switchLookCategory(b.dataset.cat))
})

// X slider is meaningless when alignment is center — dim it so that's visible.
const xRange = document.getElementById('L-xPct')
function syncXDisabled() {
  const off = alignSeg && alignSeg.dataset.value === 'center'
  if (xRange) {
    xRange.disabled = !!off
    const ctrl = xRange.closest('.look-ctrl')
    if (ctrl) ctrl.style.opacity = off ? '0.4' : ''
  }
}
syncXDisabled()

// ---- Refresh from Shopify ----
function formatAgo(iso) {
  if (!iso) return 'never synced'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return 'never synced'
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (sec < 60) return 'synced ' + sec + 's ago'
  const min = Math.round(sec / 60)
  if (min < 60) return 'synced ' + min + 'm ago'
  const hr = Math.round(min / 60)
  if (hr < 24) return 'synced ' + hr + 'h ago'
  return 'synced ' + Math.round(hr / 24) + 'd ago'
}
function refreshSyncMeta() {
  const meta = document.getElementById('sync-meta')
  if (!meta) return
  meta.textContent = formatAgo(meta.dataset.last || '')
}
refreshSyncMeta()
setInterval(refreshSyncMeta, 30000)
const syncBtn = document.getElementById('sync-btn')
if (syncBtn) {
  syncBtn.addEventListener('click', async () => {
    syncBtn.disabled = true
    const original = syncBtn.textContent
    syncBtn.textContent = '↻ Syncing…'
    try {
      const r = await fetch('/api/sync', { method: 'POST' })
      const j = await r.json().catch(() => ({ ok: r.ok, output: '' }))
      if (!r.ok || !j.ok) {
        alert('Sync failed:\\n\\n' + (j.output || r.statusText))
        syncBtn.textContent = original
        syncBtn.disabled = false
        return
      }
      // Reload to pick up the new catalogue.
      location.reload()
    } catch (e) {
      alert('Sync failed: ' + (e.message || e))
      syncBtn.textContent = original
      syncBtn.disabled = false
    }
  })
}

// ---- Flow tabs ----
const FLOW_SLUGS = ['single-product', 'product-drop', 'calendar', 'studio-event', 'look']
function showFlow(slug) {
  if (!FLOW_SLUGS.includes(slug)) slug = 'product-drop'
  FLOW_SLUGS.forEach(s => {
    const panel = document.getElementById('panel-' + s)
    if (panel) panel.hidden = s !== slug
  })
  $$('.flow-tab').forEach(b => b.classList.toggle('active', b.dataset.flow === slug))
  // Filters/counts only relevant for the single-product flow. Sync controls
  // stay visible everywhere — drop day is exactly when a fresh Shopify pull
  // is needed.
  const filters = $('#flow-filters')
  const counts = $('#counts')
  if (filters) filters.style.display = slug === 'single-product' ? '' : 'none'
  if (counts) counts.style.display = slug === 'single-product' ? '' : 'none'
  // Calendar reads live drop state — refetch every time it's opened so a just-
  // scheduled drop shows up without a page reload.
  if (slug === 'calendar') calLoad()
}
$$('.flow-tab').forEach(b => {
  b.addEventListener('click', async () => {
    const slug = b.dataset.flow
    // If we're leaving the Look tab mid-edit, make sure the pending save lands
    // before the tab change so nothing's left dangling in the timer.
    await flushPendingLookSave()
    location.hash = slug
    showFlow(slug)
  })
})
window.addEventListener('hashchange', () => {
  // Browser back/forward (or another script) changes the hash — flush too.
  flushPendingLookSave()
  showFlow(location.hash.replace(/^#/, ''))
})
// Product drop is the primary flow — land there unless the hash says otherwise.
showFlow(location.hash.replace(/^#/, '') || 'product-drop')

// ═══════════════════════════════════════════════════════════════════════
// Product drop flow
// ═══════════════════════════════════════════════════════════════════════
const drop = {
  state: {
    drops: [],
    currentId: null,
    items: [],
    cover: null,
    pickerMode: 'newest',
    pickerResults: [],
    picked: new Set(),
    frame: 1,
    frameTotal: 1,
  },
  saveTimer: null,
}

async function dropLoadAll() {
  const r = await fetch('/drops')
  const j = await r.json()
  drop.state.drops = j.drops || []
  const sel = $('#drop-select')
  sel.innerHTML = drop.state.drops.map(d =>
    '<option value="' + d.id + '">' + (d.name || ('Drop #' + d.id)) + ' · ' + d.status + '</option>'
  ).join('') || '<option value="">(none)</option>'
  if (drop.state.currentId && drop.state.drops.find(d => d.id === drop.state.currentId)) {
    sel.value = String(drop.state.currentId)
  } else if (drop.state.drops.length) {
    drop.state.currentId = drop.state.drops[0].id
    sel.value = String(drop.state.currentId)
  } else {
    drop.state.currentId = null
  }
  if (drop.state.currentId) await dropLoad(drop.state.currentId)
  else { drop.state.items = []; drop.state.cover = null; dropRender(); gridRefresh() }
}

async function dropLoad(id) {
  const r = await fetch('/drops/' + id)
  const j = await r.json()
  if (!j.ok) { alert('Failed to load drop: ' + (j.error || '')); return }
  drop.state.currentId = j.drop.id
  drop.state.cover = j.drop
  drop.state.items = j.items
  drop.state.frame = 1
  $('#drop-name').value = j.drop.name || ''
  $('#cover-body').value = (j.drop.cover_body || []).join('\\n')
  $('#cover-footer').value = j.drop.cover_footer || ''
  $('#cover-cta-url').value = j.drop.cover_cta_url || ''
  $('#cover-arrow').checked = !!j.drop.trailing_arrow
  $('#cover-include').checked = !!j.drop.include_opening
  $('#closing-body').value = (j.drop.closing_body || []).join('\\n')
  $('#closing-include').checked = !!j.drop.include_closing
  updateScheduleUi(j.drop)
  dropRenderMissing(j.missing || [])
  dropRender()
  dropRefreshPreview()
  gridRefresh()
}

// Warn when a drop lists products that have since left the catalog (sold /
// removed / renamed). They're skipped on publish, so offer a one-click purge.
function dropRenderMissing(missing) {
  const el = $('#drop-missing')
  if (!el) return
  if (!missing.length) { el.hidden = true; el.innerHTML = ''; return }
  el.hidden = false
  const list = missing.map(h => escapeHtml(h)).join(', ')
  el.innerHTML = '<strong>⚠ ' + missing.length + ' item' + (missing.length > 1 ? 's have' : ' has') +
    ' no photos on file</strong> — the product is gone and no saved copy exists, so ' +
    (missing.length > 1 ? 'they' : 'it') + ' will be skipped when posting: ' + list +
    '<button type="button" id="drop-missing-remove">Remove them</button>'
  const btn = $('#drop-missing-remove')
  if (btn) btn.addEventListener('click', async () => {
    const keep = drop.state.items.filter(it => missing.indexOf(it.handle) === -1)
    const r = await fetch('/drops/' + drop.state.currentId + '/items', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: keep.map(it => ({ handle: it.handle, image_start: it.image_start, image_count: it.image_count })) }),
    })
    const jj = await r.json()
    if (!jj.ok) { alert('Could not remove: ' + (jj.error || '')); return }
    await dropLoad(drop.state.currentId)
  })
}

function fmtLocal(iso) {
  const t = new Date(iso)
  if (isNaN(t.getTime())) return iso
  return t.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// Reflect the drop's status + schedule into the topbar and the schedule row.
function updateScheduleUi(d) {
  const status = $('#drop-status')
  const schedInput = $('#drop-schedule-at')
  const schedBtn = $('#drop-schedule-btn')
  const unschedBtn = $('#drop-unschedule-btn')
  const publishBtn = $('#drop-publish')
  const isScheduled = d.status === 'scheduled' && d.scheduled_at
  if (isScheduled) {
    status.textContent = 'scheduled · ' + fmtLocal(d.scheduled_at)
    const t = new Date(d.scheduled_at)
    const pad = (n) => String(n).padStart(2, '0')
    schedInput.value = t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate()) + 'T' + pad(t.getHours()) + ':' + pad(t.getMinutes())
  } else if (d.status === 'failed') {
    status.textContent = 'failed · ' + (d.error || 'unknown error')
  } else {
    status.textContent = d.status
  }
  unschedBtn.style.display = isScheduled ? '' : 'none'
  schedBtn.textContent = isScheduled ? 'Reschedule' : 'Schedule'
  const locked = d.status === 'publishing' || d.status === 'published'
  publishBtn.disabled = locked
  schedBtn.disabled = locked
}

function dropRender() {
  const itemsEl = $('#drop-items')
  if (!drop.state.items.length) {
    itemsEl.innerHTML = '<div class="drop-empty">No products yet. Pick some on the left and "Add selected".</div>'
  } else {
    itemsEl.innerHTML = drop.state.items.map((it, i) => {
      const result = drop.state.pickerResults.find(p => p.handle === it.handle)
      const title = (result && result.title) || it.handle
      const vendor = (result && result.vendor) || ''
      return '<div class="drop-item" data-pos="' + i + '">' +
        '<img src="/source/' + encodeURIComponent(it.handle) + '/0" />' +
        '<div class="di-meta"><div class="di-title">' + escapeHtml(title) + '</div>' +
          '<div class="di-sub">' + escapeHtml(vendor) + '</div></div>' +
        '<div class="di-count">' +
          '<button data-act="dec" data-pos="' + i + '">−</button>' +
          '<span>' + it.image_count + '</span>' +
          '<button data-act="inc" data-pos="' + i + '">+</button>' +
        '</div>' +
        '<div class="di-move">' +
          '<button data-act="up" data-pos="' + i + '" ' + (i === 0 ? 'disabled' : '') + '>↑</button>' +
          '<button data-act="down" data-pos="' + i + '" ' + (i === drop.state.items.length - 1 ? 'disabled' : '') + '>↓</button>' +
        '</div>' +
        '<button class="di-remove" data-act="remove" data-pos="' + i + '">×</button>' +
      '</div>'
    }).join('')
    itemsEl.querySelectorAll('button[data-act]').forEach(b => {
      b.addEventListener('click', () => dropItemAction(b.dataset.act, parseInt(b.dataset.pos, 10)))
    })
  }
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]))
}

async function dropItemAction(act, pos) {
  const items = drop.state.items.slice()
  if (act === 'remove') items.splice(pos, 1)
  else if (act === 'inc') items[pos] = { ...items[pos], image_count: items[pos].image_count + 1 }
  else if (act === 'dec') items[pos] = { ...items[pos], image_count: Math.max(1, items[pos].image_count - 1) }
  else if (act === 'up' && pos > 0) { const t = items[pos - 1]; items[pos - 1] = items[pos]; items[pos] = t }
  else if (act === 'down' && pos < items.length - 1) { const t = items[pos + 1]; items[pos + 1] = items[pos]; items[pos] = t }
  await dropSaveItems(items)
}

async function dropSaveItems(items) {
  const payload = items.map(it => ({ handle: it.handle, image_start: it.image_start || 0, image_count: it.image_count || 4 }))
  const r = await fetch('/drops/' + drop.state.currentId + '/items', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: payload }),
  })
  const j = await r.json()
  if (j.ok) {
    drop.state.items = j.items
    drop.state.frame = 1
    dropRender()
    dropRefreshPreview()
    gridRefresh()
  }
}

function dropScheduleSave() {
  clearTimeout(drop.saveTimer)
  drop.saveTimer = setTimeout(dropSaveCover, 600)
}

async function dropSaveCover() {
  if (!drop.state.currentId) return
  const body = $('#cover-body').value.split(/\\n+/).map(s => s.trim()).filter(Boolean)
  const closingBody = $('#closing-body').value.split(/\\n+/).map(s => s.trim()).filter(Boolean)
  const patch = {
    name: $('#drop-name').value,
    cover_body: body,
    cover_footer: $('#cover-footer').value || null,
    cover_cta_url: $('#cover-cta-url').value || null,
    trailing_arrow: $('#cover-arrow').checked,
    closing_body: closingBody,
    include_opening: $('#cover-include').checked,
    include_closing: $('#closing-include').checked,
  }
  const r = await fetch('/drops/' + drop.state.currentId, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const j = await r.json()
  if (j.ok) {
    drop.state.cover = j.drop
    dropRefreshPreview()
    // Refresh dropdown label
    const opt = $('#drop-select').querySelector('option[value="' + drop.state.currentId + '"]')
    if (opt) opt.textContent = (j.drop.name || ('Drop #' + j.drop.id)) + ' · ' + j.drop.status
  }
}

async function dropRefreshPreview() {
  if (!drop.state.currentId) { $('#drop-preview-img').removeAttribute('src'); return }
  try {
    const r = await fetch('/drops/' + drop.state.currentId + '/sequence')
    const j = await r.json()
    drop.state.frameTotal = j.ok ? j.frame_count : 1
  } catch (e) { drop.state.frameTotal = 1 }
  if (drop.state.frame > drop.state.frameTotal) drop.state.frame = 1
  $('#drop-frame-label').textContent = drop.state.frame + ' / ' + drop.state.frameTotal
  $('#drop-frame-prev').disabled = drop.state.frame <= 1
  $('#drop-frame-next').disabled = drop.state.frame >= drop.state.frameTotal
  $('#drop-preview-img').src = '/drops/' + drop.state.currentId + '/preview/' + drop.state.frame + '?t=' + Date.now()
}

// ── Promo grid ──
// Seeded so the preview, download link, and "Post as story" all reference
// the exact grid the user is looking at. Shuffle = new seed.
drop.state.gridSeed = Math.floor(Math.random() * 1e9)

function gridParams() {
  return 'grid=' + $('#grid-size').value + '&format=' + $('#grid-format').value + '&seed=' + drop.state.gridSeed
}

function gridRefresh() {
  const img = $('#grid-preview-img')
  const hasItems = !!(drop.state.currentId && drop.state.items.length)
  ;['grid-heading', 'grid-controls', 'grid-preview-wrap'].forEach(id => {
    const el = document.getElementById(id)
    if (el) el.style.display = hasItems ? '' : 'none'
  })
  if (!hasItems) { img.removeAttribute('src'); return }
  const base = '/drops/' + drop.state.currentId + '/grid.jpg?' + gridParams()
  img.src = base
  $('#grid-download').href = base + '&download=1'
  $('#grid-post').style.display = $('#grid-format').value === 'story' ? '' : 'none'
}

$('#grid-size').addEventListener('change', gridRefresh)
$('#grid-format').addEventListener('change', gridRefresh)
$('#grid-shuffle').addEventListener('click', () => {
  drop.state.gridSeed = Math.floor(Math.random() * 1e9)
  gridRefresh()
})
$('#grid-post').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  if (!confirm('Post this promo grid to Instagram stories now?')) return
  const btn = $('#grid-post')
  const orig = btn.textContent
  btn.disabled = true
  btn.textContent = 'Posting…'
  try {
    const r = await fetch('/drops/' + drop.state.currentId + '/grid/post', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grid: $('#grid-size').value, seed: drop.state.gridSeed }),
    })
    const j = await r.json()
    if (!j.ok) throw new Error(j.error || 'post failed')
    alert('Promo grid posted to stories!')
  } catch (e) {
    alert('Post failed: ' + (e.message || e))
  } finally {
    btn.disabled = false
    btn.textContent = orig
  }
})

// ── Picker ──
// Populate the tag dropdown from the catalogue so tag drops start from a
// pick-list instead of a blind text field.
async function dropTagsLoad() {
  try {
    const r = await fetch('/tags')
    const j = await r.json()
    const sel = $('#picker-tag')
    const cur = sel.value
    sel.innerHTML = '<option value="">— pick a tag —</option>' +
      (j.tags || []).map(t =>
        '<option value="' + escapeHtml(t.tag) + '">' + escapeHtml(t.tag) + ' (' + t.count + ')</option>'
      ).join('')
    if (cur) sel.value = cur
  } catch (e) { /* tags are a convenience; picker still works via search */ }
}

async function dropPickerLoad() {
  const params = new URLSearchParams()
  if (drop.state.pickerMode === 'newest') params.set('sort', 'newest')
  else { const tag = $('#picker-tag').value.trim(); if (tag) params.set('tag', tag) }
  const q = $('#picker-query').value.trim()
  if (q) params.set('q', q)
  params.set('limit', '100')
  const r = await fetch('/products/search?' + params.toString())
  const j = await r.json()
  drop.state.pickerResults = j.items || []
  dropPickerRender()
}

function dropPickerRender() {
  const inDrop = new Set(drop.state.items.map(it => it.handle))
  const list = $('#picker-list')
  list.innerHTML = drop.state.pickerResults.map(p => {
    const already = inDrop.has(p.handle)
    const picked = drop.state.picked.has(p.handle)
    return '<div class="picker-row ' + (picked ? 'picked' : '') + '" data-handle="' + p.handle + '">' +
      '<input type="checkbox" data-handle="' + p.handle + '" ' + (picked ? 'checked' : '') + ' ' + (already ? 'disabled' : '') + ' />' +
      '<img src="/source/' + encodeURIComponent(p.handle) + '/0" />' +
      '<div class="meta"><div class="title">' + escapeHtml(p.title) + '</div>' +
        '<div class="sub">' + escapeHtml(p.vendor || '') + ' · ' + (p.updated_at ? new Date(p.updated_at).toLocaleDateString() : '') + '</div></div>' +
      '<span class="img-count">' + p.image_count + ' imgs' + (already ? ' · added' : '') + '</span>' +
    '</div>'
  }).join('')
  list.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.checked) drop.state.picked.add(cb.dataset.handle)
      else drop.state.picked.delete(cb.dataset.handle)
      cb.closest('.picker-row').classList.toggle('picked', cb.checked)
    })
  })
  dropPickerHint()
}

// The picker only lists items that are in stock AND have photos synced
// locally. On drop day, freshly-tagged items often haven't had photos
// uploaded/synced yet — say so instead of silently showing fewer items.
function dropPickerHint() {
  const el = $('#picker-hint')
  if (!el) return
  const n = drop.state.pickerResults.length
  let html = n + ' item' + (n === 1 ? '' : 's') + ' available (in stock · photos synced)'
  if (drop.state.pickerMode === 'tag') {
    const sel = $('#picker-tag')
    const opt = sel.options[sel.selectedIndex]
    const m = sel.value && opt ? opt.textContent.match(/\\((\\d+)\\)\\s*$/) : null
    const tagged = m ? parseInt(m[1], 10) : null
    if (tagged && tagged > n) {
      html += ' <span class="warn">— ' + (tagged - n) + ' more tagged item' + (tagged - n === 1 ? '' : 's') +
        ' missing photos or stock. If Shopify was just updated, hit ↻ Refresh.</span>'
    }
  }
  el.innerHTML = html
}

$$('.picker-tab').forEach(b => {
  b.addEventListener('click', () => {
    drop.state.pickerMode = b.dataset.mode
    $$('.picker-tab').forEach(x => x.classList.toggle('on', x === b))
    $('#picker-tag').style.display = drop.state.pickerMode === 'tag' ? '' : 'none'
    dropPickerLoad()
  })
})
$('#picker-tag').addEventListener('change', dropPickerLoad)
$('#picker-query').addEventListener('input', () => clearTimeout(drop.saveTimer) || setTimeout(dropPickerLoad, 300))

function imgsPerItem() {
  return parseInt($('#picker-imgs-per').value, 10) || 2
}

// If the drop hasn't been named yet, borrow the active tag — a tag drop's
// name is almost always the tag itself.
function maybeNameDropFromTag() {
  if ($('#drop-name').value) return
  if (drop.state.pickerMode !== 'tag') return
  const tag = $('#picker-tag').value
  if (!tag) return
  $('#drop-name').value = tag
  dropScheduleSave()
}

async function dropAddHandles(handles) {
  if (!drop.state.currentId) { alert('Create a drop first'); return }
  const inDrop = new Set(drop.state.items.map(it => it.handle))
  const additions = handles.filter(h => !inDrop.has(h))
  if (!additions.length) return
  const per = imgsPerItem()
  const byHandle = new Map(drop.state.pickerResults.map(p => [p.handle, p]))
  const newItems = drop.state.items.concat(additions.map(h => {
    const p = byHandle.get(h)
    const available = p && p.image_count ? p.image_count : per
    return { handle: h, image_start: 0, image_count: Math.max(1, Math.min(per, available)) }
  }))
  maybeNameDropFromTag()
  drop.state.picked.clear()
  await dropSaveItems(newItems)
  dropPickerRender()
}

$('#picker-add').addEventListener('click', async () => {
  if (!drop.state.picked.size) return
  await dropAddHandles(Array.from(drop.state.picked))
})

$('#picker-add-all').addEventListener('click', async () => {
  if (!drop.state.pickerResults.length) { alert('No products in the list to add'); return }
  await dropAddHandles(drop.state.pickerResults.map(p => p.handle))
})

$('#drop-new').addEventListener('click', async () => {
  const r = await fetch('/drops', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
  const j = await r.json()
  if (j.ok) {
    drop.state.currentId = j.drop.id
    await dropLoadAll()
  }
})

$('#drop-select').addEventListener('change', (e) => {
  const id = parseInt(e.target.value, 10)
  if (id) dropLoad(id)
})

$('#drop-name').addEventListener('input', dropScheduleSave)
$('#cover-body').addEventListener('input', dropScheduleSave)
$('#cover-footer').addEventListener('input', dropScheduleSave)
$('#cover-cta-url').addEventListener('input', dropScheduleSave)
$('#cover-arrow').addEventListener('change', dropScheduleSave)
$('#cover-include').addEventListener('change', dropScheduleSave)
$('#closing-body').addEventListener('input', dropScheduleSave)
$('#closing-include').addEventListener('change', dropScheduleSave)

// ── Scheduling ──
$('#drop-schedule-btn').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  if (!drop.state.items.length) { alert('Add some products first'); return }
  const v = $('#drop-schedule-at').value
  if (!v) { alert('Pick a date and time first'); return }
  const when = new Date(v)
  if (!(when.getTime() > Date.now())) { alert('Scheduled time must be in the future'); return }
  const r = await fetch('/drops/' + drop.state.currentId + '/schedule', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scheduled_at: when.toISOString() }),
  })
  const j = await r.json()
  if (!j.ok) { alert('Schedule failed: ' + (j.error || '')); return }
  drop.state.cover = j.drop
  updateScheduleUi(j.drop)
  dropRefreshSelectLabel(j.drop)
})

$('#drop-unschedule-btn').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  const r = await fetch('/drops/' + drop.state.currentId + '/schedule', { method: 'DELETE' })
  const j = await r.json()
  if (!j.ok) { alert('Cancel failed: ' + (j.error || '')); return }
  drop.state.cover = j.drop
  updateScheduleUi(j.drop)
  dropRefreshSelectLabel(j.drop)
})

function dropRefreshSelectLabel(d) {
  const opt = $('#drop-select').querySelector('option[value="' + d.id + '"]')
  if (opt) opt.textContent = (d.name || ('Drop #' + d.id)) + ' · ' + d.status
}

$('#drop-frame-prev').addEventListener('click', () => {
  if (drop.state.frame > 1) { drop.state.frame--; dropRefreshPreview() }
})
$('#drop-frame-next').addEventListener('click', () => {
  if (drop.state.frame < drop.state.frameTotal) { drop.state.frame++; dropRefreshPreview() }
})

$('#drop-delete').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  if (!confirm('Delete this drop? (will not unpublish anything already posted)')) return
  await fetch('/drops/' + drop.state.currentId, { method: 'DELETE' })
  drop.state.currentId = null
  await dropLoadAll()
})

// Poll a drop's status until it lands on published/failed (publishing runs in
// the background and can take minutes). Returns the final drop, or the last
// known state if it's still going after the timeout.
async function pollDropStatus(id, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 12 * 60 * 1000)
  let last = { status: 'publishing' }
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 3000))
    try {
      const r = await fetch('/drops/' + id)
      const j = await r.json()
      if (j.ok && j.drop) {
        last = j.drop
        if (j.drop.status === 'published' || j.drop.status === 'failed') return j.drop
      }
    } catch (e) { /* transient — keep polling */ }
  }
  return last
}

$('#drop-publish').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  if (!drop.state.items.length) { alert('Add some products first'); return }
  if (!confirm('Publish ' + drop.state.frameTotal + ' stories to Instagram?')) return
  const id = drop.state.currentId
  $('#drop-publish').disabled = true
  $('#drop-publish').textContent = 'Publishing…'
  try {
    const r = await fetch('/drops/' + id + '/publish', { method: 'POST' })
    const j = await r.json()
    if (!j.ok) throw new Error(j.error || 'publish failed')
    // Server posts in the background now — watch status rather than the request.
    const d = await pollDropStatus(id)
    if (d.status === 'published') {
      alert('Published! ' + (d.ig_media_ids ? d.ig_media_ids.length : 0) + ' stories live.')
    } else if (d.status === 'failed') {
      alert('Publish failed: ' + (d.error || 'unknown error'))
    } else {
      alert('Still publishing in the background — this is a big drop. Check the Calendar tab or refresh in a minute for the final status.')
    }
    await dropLoadAll()
  } catch (e) {
    alert('Publish failed: ' + (e.message || e))
  } finally {
    $('#drop-publish').disabled = false
    $('#drop-publish').textContent = 'Post now'
  }
})

// Boot the drop UI if we land on (or switch to) that flow
window.addEventListener('hashchange', () => {
  if (location.hash === '#product-drop') { dropLoadAll(); dropPickerLoad(); dropTagsLoad() }
})
const bootFlow = location.hash.replace(/^#/, '') || 'product-drop'
if (bootFlow === 'product-drop') {
  dropLoadAll()
  dropPickerLoad()
  dropTagsLoad()
} else {
  // Lazy-init when user clicks the tab
  $$('.flow-tab').forEach(b => {
    if (b.dataset.flow === 'product-drop') {
      b.addEventListener('click', () => { if (!drop.state.drops.length) { dropLoadAll(); dropPickerLoad(); dropTagsLoad() } }, { once: true })
    }
  })
}

// ---- Search/filter ----
const search = $('#search')
const vendorFilter = $('#vendor-filter')
function applyFilter() {
  const q = search.value.trim().toLowerCase()
  const v = vendorFilter.value
  const cards = $$('.card')
  let visible = 0
  cards.forEach(c => {
    const matchesSearch = !q || c.dataset.title.includes(q) || c.dataset.brand.includes(q)
    const matchesVendor = !v || c.dataset.brand === v
    const show = matchesSearch && matchesVendor
    c.style.display = show ? '' : 'none'
    if (show && c.parentElement.id === 'grid-pending') visible++
  })
  $('#empty-pending').style.display = visible === 0 ? 'block' : 'none'
}
search.addEventListener('input', applyFilter)
vendorFilter.addEventListener('change', applyFilter)

// ---- Approve & Post (skip modal) ----
$$('.approve-btn').forEach(btn => {
  btn.addEventListener('click', async () => publishCard(btn.dataset.handle, btn.closest('.card')))
})

async function publishCard(handle, card) {
  const buttons = card.querySelectorAll('button')
  buttons.forEach(b => b.disabled = true)
  const postBtn = card.querySelector('.approve-btn') || card.querySelector('.primary')
  const originalText = postBtn?.textContent
  if (postBtn) postBtn.textContent = 'Posting…'
  try {
    const res = await fetch('/publish/' + encodeURIComponent(handle), { method: 'POST' })
    const j = await res.json()
    if (!res.ok) throw new Error(j.error || 'failed')
    const actions = card.querySelector('.actions')
    actions.innerHTML = '<div class="badge">✓ posted</div>'
    closeModal()
  } catch (err) {
    buttons.forEach(b => b.disabled = false)
    if (postBtn) postBtn.textContent = originalText
    alert('Failed: ' + (err.message || err))
  }
}

// ---- Edit modal ----
const backdrop = $('#modal-backdrop')
const previewImg = $('#preview-img')
const imgStrip = $('#img-strip')
const rangeHint = $('#range-hint')
const fBrand = $('#f-brand')
const fName = $('#f-name')
const fPrice = $('#f-price')
const fLink = $('#f-link')
const fCategory = $('#f-category')
const fCategoryHint = $('#f-category-hint')
const fCount = $('#f-count')
const countHint = $('#count-hint')
const frameNav = $('#frame-nav')
const frameLabel = $('#frame-label')
const framePrev = $('#frame-prev')
const frameNext = $('#frame-next')
let modalState = null

$$('.edit-btn').forEach(btn => {
  btn.addEventListener('click', () => openModal(btn.dataset.handle))
})
$$('.thumb').forEach(img => {
  img.addEventListener('click', () => openModal(img.dataset.handle))
})
$('#close-modal').addEventListener('click', closeModal)
backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal() })

async function openModal(handle) {
  const res = await fetch('/draft/' + encodeURIComponent(handle))
  const data = await res.json()
  if (!res.ok) { alert('Failed to load: ' + (data.error || res.statusText)); return }

  const draft = data.draft || {}
  const layout = draft.layout || '1up'
  const start = Math.max(0, Math.min(draft.image_start ?? 0, Math.max(0, data.images.length - 1)))
  const requestedCount = draft.image_count ?? 4
  const maxCount = Math.max(1, data.images.length - start)
  modalState = {
    handle,
    images: data.images,
    layout,
    image_start: start,
    image_count: Math.max(1, Math.min(requestedCount, maxCount)),
    frame: 1,
    frameTotal: 1,
    autoCategory: data.auto_category || 'clothing',
  }

  $('#modal-brand').textContent = data.defaults.brand || data.vendor || ''
  $('#modal-title').textContent = data.title

  fBrand.value = draft.brand ?? data.defaults.brand
  fName.value = draft.name ?? data.defaults.name
  fPrice.value = draft.price ?? data.defaults.price
  fLink.value = draft.link ?? data.defaults.link
  fCategory.value = draft.look_category ?? ''
  updateCategoryHint()
  fCount.value = modalState.image_count
  fCount.max = data.images.length

  renderImgStrip()
  renderCountControls()
  refreshPreview()
  backdrop.classList.add('open')
}

function renderImgStrip() {
  imgStrip.innerHTML = ''
  const need = modalState.layout === '1up'
    ? modalState.image_count
    : (modalState.layout === '2up' ? 2 : 4)
  modalState.images.forEach((img, i) => {
    const div = document.createElement('div')
    div.className = 'pic'
    const inRange = i >= modalState.image_start && i < modalState.image_start + need
    if (inRange) div.classList.add('selected')
    div.style.backgroundImage = "url('/source/" + modalState.handle + "/" + i + "')"
    div.title = 'Image ' + (i + 1) + ' of ' + modalState.images.length
    div.innerHTML = '<span class="num">' + (i + 1) + '</span>'
    div.addEventListener('click', () => {
      const maxStart = Math.max(0, modalState.images.length - 1)
      modalState.image_start = Math.min(i, maxStart)
      clampCount()
      renderImgStrip()
      renderCountControls()
      schedulePreview()
    })
    imgStrip.appendChild(div)
  })
  const start = modalState.image_start + 1
  rangeHint.textContent = 'Using images ' + start + '-' + (start + need - 1) + ' of ' + modalState.images.length
}

function clampCount() {
  const maxCount = Math.max(1, modalState.images.length - modalState.image_start)
  modalState.image_count = Math.max(1, Math.min(modalState.image_count, maxCount))
  fCount.value = modalState.image_count
}

function renderCountControls() {
  const maxCount = Math.max(1, modalState.images.length - modalState.image_start)
  fCount.max = maxCount
  countHint.textContent = 'max ' + maxCount + ' from start ' + (modalState.image_start + 1)
}

$('#count-minus').addEventListener('click', () => {
  modalState.image_count = Math.max(1, modalState.image_count - 1)
  clampCount()
  renderImgStrip()
  renderCountControls()
  schedulePreview()
})
$('#count-plus').addEventListener('click', () => {
  const maxCount = Math.max(1, modalState.images.length - modalState.image_start)
  modalState.image_count = Math.min(maxCount, modalState.image_count + 1)
  clampCount()
  renderImgStrip()
  renderCountControls()
  schedulePreview()
})
fCount.addEventListener('change', () => {
  const n = parseInt(fCount.value, 10)
  if (!Number.isFinite(n) || n < 1) { fCount.value = modalState.image_count; return }
  modalState.image_count = n
  clampCount()
  renderImgStrip()
  renderCountControls()
  schedulePreview()
})

framePrev.addEventListener('click', () => {
  if (modalState.frame > 1) { modalState.frame--; loadFrame() }
})
frameNext.addEventListener('click', () => {
  if (modalState.frame < modalState.frameTotal) { modalState.frame++; loadFrame() }
})

function loadFrame() {
  previewImg.src = '/preview/' + encodeURIComponent(modalState.handle) + '/' + modalState.frame + '?t=' + Date.now()
  updateFrameNav()
}

function updateFrameNav() {
  if (modalState.frameTotal > 1) {
    frameNav.style.display = 'flex'
    frameLabel.textContent = modalState.frame + ' / ' + modalState.frameTotal
    framePrev.disabled = modalState.frame <= 1
    frameNext.disabled = modalState.frame >= modalState.frameTotal
  } else {
    frameNav.style.display = 'none'
  }
}

let previewTimer = null
function schedulePreview() {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 500)
}
;[fBrand, fName, fPrice, fLink].forEach(input => input.addEventListener('input', schedulePreview))
fCategory.addEventListener('change', () => {
  updateCategoryHint()
  schedulePreview()
})

function updateCategoryHint() {
  if (!fCategoryHint || !modalState) return
  const sel = fCategory.value
  const auto = modalState.autoCategory || 'clothing'
  fCategoryHint.textContent = sel
    ? 'Forced to ' + sel + ' (auto would pick ' + auto + ')'
    : 'Auto-detect → ' + auto
}

async function saveDraft() {
  if (!modalState) return null
  const body = {
    layout: modalState.layout,
    image_start: modalState.image_start,
    image_count: modalState.image_count,
    brand: fBrand.value,
    name: fName.value,
    price: fPrice.value,
    link: fLink.value,
    look_category: fCategory.value || null,
  }
  const res = await fetch('/draft/' + encodeURIComponent(modalState.handle), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const j = await res.json().catch(() => ({}))
    throw new Error(j.error || 'save failed')
  }
}

async function refreshPreview() {
  if (!modalState) return
  try {
    await saveDraft()
    const seqRes = await fetch('/sequence/' + encodeURIComponent(modalState.handle))
    const seq = await seqRes.json()
    if (seq.ok) {
      modalState.frameTotal = seq.frame_count
      if (modalState.frame > modalState.frameTotal) modalState.frame = 1
    } else {
      modalState.frameTotal = 1
      modalState.frame = 1
    }
    loadFrame()
  } catch (e) {
    console.error(e)
  }
}

$('#btn-save').addEventListener('click', async () => {
  try {
    await saveDraft()
    await refreshPreview()
    const card = document.querySelector('.card[data-handle="' + modalState.handle + '"] img.thumb')
    if (card) card.src = '/thumb/' + modalState.handle + '?t=' + Date.now()
    closeModal()
  } catch (e) {
    alert('Save failed: ' + e.message)
  }
})

$('#btn-reset').addEventListener('click', async () => {
  if (!confirm('Reset draft for this item?')) return
  await fetch('/draft/' + encodeURIComponent(modalState.handle), { method: 'DELETE' })
  closeModal()
  openModal(modalState.handle)
})

$('#btn-post').addEventListener('click', async () => {
  try { await saveDraft() } catch (e) { alert('Save failed: ' + e.message); return }
  const card = document.querySelector('.card[data-handle="' + modalState.handle + '"]')
  await publishCard(modalState.handle, card)
})

function closeModal() {
  backdrop.classList.remove('open')
  modalState = null
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && backdrop.classList.contains('open')) closeModal()
})

// ═══════════════════════════════════════════════════════════════════════
// Posting calendar — scheduled drops + grid posts, queue + failure states
// ═══════════════════════════════════════════════════════════════════════
const cal = { view: 'month', anchor: calStartOfToday(), drops: [], grids: [] }
const calForm = { seed: 1 }
const CAL_PRESETS = ['2x2', '2x3', '3x3', '3x4', '4x4']

function calStartOfToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d }
function calAddDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x }
function calSameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate() }
// Sunday-start weeks.
function calStartOfWeek(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - x.getDay()); return x }
function calFmtTime(t) { return t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).replace(/\\s/g, '').toLowerCase() }
function calPad(n) { return String(n).padStart(2, '0') }
function calLocalInput(d) { return d.getFullYear() + '-' + calPad(d.getMonth() + 1) + '-' + calPad(d.getDate()) + 'T' + calPad(d.getHours()) + ':' + calPad(d.getMinutes()) }
function calStatusBadge(s) { return '<span class="cm-status ' + s + '">' + s + '</span>' }

async function calLoad() {
  try {
    const [dr, gr] = await Promise.all([
      fetch('/drops').then(r => r.json()),
      fetch('/scheduled-grids').then(r => r.json()),
    ])
    cal.drops = dr.drops || []
    cal.grids = gr.grids || []
  } catch (e) { cal.drops = []; cal.grids = [] }
  calRender()
}

// Which day a drop lands on: its scheduled slot, else when it posted, else
// (for a failure with no schedule) when it last changed.
function calDropSlot(d) {
  if (d.scheduled_at) return new Date(d.scheduled_at)
  if (d.published_at) return new Date(d.published_at)
  if (d.status === 'failed') return new Date(d.updated_at)
  return null
}

// Every drop + grid event landing on a given day, earliest first.
function calEventsForDay(day) {
  const out = []
  for (const d of cal.drops) {
    if (!(d.status === 'scheduled' || d.status === 'publishing' || d.status === 'published' || d.status === 'failed')) continue
    const when = calDropSlot(d)
    if (when && !isNaN(when) && calSameDay(when, day)) {
      out.push({ kind: 'drop', id: d.id, name: d.name || ('Drop #' + d.id), when: when, status: d.status })
    }
  }
  for (const g of cal.grids) {
    const when = new Date(g.scheduled_at)
    if (!isNaN(when) && calSameDay(when, day)) {
      out.push({ kind: 'grid', id: g.id, name: g.title || ('Grid #' + g.id), when: when, status: g.status })
    }
  }
  out.sort((a, b) => a.when - b.when)
  return out
}

// First visible day + how many week-rows to draw for the current view.
function calRange() {
  if (cal.view === 'twoweek') return { start: calStartOfWeek(cal.anchor), weeks: 2 }
  const first = new Date(cal.anchor.getFullYear(), cal.anchor.getMonth(), 1)
  const start = calStartOfWeek(first)
  const last = new Date(cal.anchor.getFullYear(), cal.anchor.getMonth() + 1, 0)
  const endWeek = calStartOfWeek(last)
  const weeks = Math.round((endWeek - start) / (7 * 86400000)) + 1
  return { start, weeks }
}

function calRender() {
  const grid = $('#cal-grid')
  if (!grid) return
  const { start, weeks } = calRange()
  const today = calStartOfToday()

  const label = $('#cal-label')
  if (label) {
    if (cal.view === 'month') {
      label.textContent = cal.anchor.toLocaleDateString([], { month: 'long', year: 'numeric' })
    } else {
      const end = calAddDays(start, 13)
      label.textContent = start.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' – ' +
        end.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
    }
  }

  const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  let html = '<div class="cal-dow">' + dow.map(d => '<div>' + d + '</div>').join('') + '</div>'
  html += '<div class="cal-weeks">'
  let scheduledCount = 0
  for (let w = 0; w < weeks; w++) {
    html += '<div class="cal-week">'
    for (let i = 0; i < 7; i++) {
      const day = calAddDays(start, w * 7 + i)
      const inMonth = cal.view === 'twoweek' || day.getMonth() === cal.anchor.getMonth()
      const isToday = calSameDay(day, today)
      const past = day < today
      const evs = calEventsForDay(day)
      scheduledCount += evs.filter(e => e.status === 'scheduled').length
      const cls = 'cal-day' + (inMonth ? '' : ' out') + (isToday ? ' today' : '') + (past ? ' past' : '')
      html += '<div class="' + cls + '" data-ts="' + day.getTime() + '"><div class="cal-daynum">' + day.getDate() + '</div>'
      for (const e of evs) {
        const glyph = e.kind === 'grid' ? '▦' : '❖'
        html += '<button class="cal-chip ' + e.status + (e.kind === 'grid' ? ' grid' : '') + '" data-kind="' + e.kind + '" data-id="' + e.id + '" title="' +
          escapeHtml(e.name) + ' · ' + calFmtTime(e.when) + ' · ' + e.status + '">' +
          '<span class="cal-kind">' + glyph + '</span> <span class="cal-chip-time">' + calFmtTime(e.when) + '</span> ' + escapeHtml(e.name) + '</button>'
      }
      html += '</div>'
    }
    html += '</div>'
  }
  html += '</div>'
  grid.innerHTML = html

  const sum = $('#cal-summary')
  if (sum) sum.textContent = scheduledCount === 1 ? '1 scheduled in view' : scheduledCount + ' scheduled in view'

  // Click a blank part of a day → schedule a new grid post there.
  grid.querySelectorAll('.cal-day').forEach(cell => {
    cell.addEventListener('click', (e) => {
      if (e.target.closest('.cal-chip')) return
      calOpenCreate(new Date(Number(cell.dataset.ts)))
    })
  })
  // Click an event → its detail/edit popup.
  grid.querySelectorAll('.cal-chip').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation()
      calOpenChip(b.dataset.kind, parseInt(b.dataset.id, 10))
    })
  })
}

function calNav(dir) {
  if (cal.view === 'month') {
    cal.anchor = new Date(cal.anchor.getFullYear(), cal.anchor.getMonth() + dir, 1)
  } else {
    cal.anchor = calAddDays(cal.anchor, dir * 14)
  }
  calRender()
}

function calSetView(v) {
  cal.view = v
  $$('.cal-view-btn').forEach(b => b.classList.toggle('on', b.dataset.view === v))
  calRender()
}

// Open an existing drop in the Product drop tab.
async function calJumpToDrop(id) {
  location.hash = 'product-drop'
  showFlow('product-drop')
  drop.state.currentId = id
  const sel = $('#drop-select')
  if (sel) sel.value = String(id)
  await dropLoad(id)
}

// ── Event modal (create + detail) ────────────────────────────────────────
const calBackdrop = document.getElementById('cal-modal-backdrop')
const calBody = document.getElementById('cal-modal-body')
function calShowModal(html) { calBody.innerHTML = html; calBackdrop.classList.add('open') }
function calCloseModal() { calBackdrop.classList.remove('open'); calBody.innerHTML = '' }
document.getElementById('cal-modal-close').addEventListener('click', calCloseModal)
calBackdrop.addEventListener('click', (e) => { if (e.target === calBackdrop) calCloseModal() })

function calDropOptions(sel) {
  if (!cal.drops.length) return '<option value="">(no drops yet)</option>'
  return cal.drops.map(d => '<option value="' + d.id + '"' + (d.id === sel ? ' selected' : '') + '>' + escapeHtml(d.name || ('Drop #' + d.id)) + '</option>').join('')
}
function calPresetOptions(sel) {
  return CAL_PRESETS.map(p => '<option value="' + p + '"' + (p === sel ? ' selected' : '') + '>' + p + '</option>').join('')
}

// Shared grid-post form (title / when / layout / drop / live preview / shuffle).
function calGridFormFields(o) {
  return ''
    + '<label>Title</label>'
    + '<input type="text" id="cm-title" placeholder="e.g. Friday teaser grid" value="' + escapeHtml(o.title || '') + '" />'
    + '<div class="cm-row">'
    + '<div><label>When</label><input type="datetime-local" id="cm-when" value="' + o.whenInput + '" /></div>'
    + '<div><label>Layout</label><select id="cm-preset">' + calPresetOptions(o.preset) + '</select></div>'
    + '</div>'
    + '<label>Content — photos pulled from this drop</label>'
    + '<select id="cm-drop">' + calDropOptions(o.dropId) + '</select>'
    + '<div class="cm-preview"><img id="cm-preview-img" alt="grid preview" /></div>'
    + '<div style="text-align:center"><button type="button" class="cm-shuffle-btn" id="cm-shuffle">↻ Shuffle layout</button></div>'
}

function calFormPreviewRefresh() {
  const img = document.getElementById('cm-preview-img')
  if (!img) return
  const dropSel = document.getElementById('cm-drop')
  const presetSel = document.getElementById('cm-preset')
  if (!dropSel || !dropSel.value) { img.removeAttribute('src'); return }
  img.src = '/drops/' + dropSel.value + '/grid.jpg?grid=' + presetSel.value + '&format=story&seed=' + calForm.seed
}

function calWireGridForm() {
  const d = document.getElementById('cm-drop')
  const p = document.getElementById('cm-preset')
  const s = document.getElementById('cm-shuffle')
  if (d) d.addEventListener('change', calFormPreviewRefresh)
  if (p) p.addEventListener('change', calFormPreviewRefresh)
  if (s) s.addEventListener('click', () => { calForm.seed = Math.floor(Math.random() * 1e9); calFormPreviewRefresh() })
  calFormPreviewRefresh()
}

function calOpenCreate(dayDate) {
  if (!cal.drops.length) { alert('Make a drop first (Product drop tab) — a grid post pulls its photos from a drop.'); return }
  const when = new Date(dayDate); when.setHours(12, 0, 0, 0)
  calForm.seed = Math.floor(Math.random() * 1e9)
  const html = '<h3>Schedule a grid post</h3>'
    + '<div class="cm-sub">Posts a promo grid to your IG story at the chosen time.</div>'
    + calGridFormFields({ title: '', whenInput: calLocalInput(when), preset: '3x3', dropId: cal.drops[0].id })
    + '<div class="cm-actions"><button type="button" class="primary" id="cm-save">Schedule it</button>'
    + '<button type="button" id="cm-cancel">Cancel</button></div>'
  calShowModal(html)
  calWireGridForm()
  document.getElementById('cm-cancel').addEventListener('click', calCloseModal)
  document.getElementById('cm-save').addEventListener('click', calSaveCreate)
}

async function calSaveCreate() {
  const whenVal = document.getElementById('cm-when').value
  if (!whenVal) { alert('Pick a date & time'); return }
  const body = {
    drop_id: Number(document.getElementById('cm-drop').value),
    title: document.getElementById('cm-title').value.trim(),
    scheduled_at: new Date(whenVal).toISOString(),
    grid_preset: document.getElementById('cm-preset').value,
    grid_seed: calForm.seed,
  }
  const r = await fetch('/scheduled-grids', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json()
  if (!j.ok) { alert('Could not schedule: ' + (j.error || '')); return }
  calCloseModal(); calLoad(); refreshAlerts()
}

function calOpenChip(kind, id) {
  if (kind === 'grid') { const g = cal.grids.find(x => x.id === id); if (g) calOpenGridEvent(g) }
  else { const d = cal.drops.find(x => x.id === id); if (d) calOpenDropEvent(d) }
}

function calOpenGridEvent(g) {
  calForm.seed = g.grid_seed
  const canPost = g.status !== 'published' && g.status !== 'publishing'
  const html = '<h3>Grid post</h3>'
    + '<div class="cm-sub">' + calStatusBadge(g.status) + ' posts as an IG story</div>'
    + calGridFormFields({ title: g.title, whenInput: calLocalInput(new Date(g.scheduled_at)), preset: g.grid_preset, dropId: g.drop_id })
    + (g.error ? '<div class="cm-error">' + escapeHtml(g.error) + '</div>' : '')
    + '<div class="cm-actions">'
    + '<button type="button" class="primary" id="cm-save">Save changes</button>'
    + (canPost ? '<button type="button" id="cm-postnow">Post now</button>' : '')
    + '<button type="button" class="danger" id="cm-delete">Delete</button>'
    + '</div>'
  calShowModal(html)
  calWireGridForm()
  document.getElementById('cm-save').addEventListener('click', async () => {
    const body = {
      drop_id: Number(document.getElementById('cm-drop').value),
      title: document.getElementById('cm-title').value.trim(),
      scheduled_at: new Date(document.getElementById('cm-when').value).toISOString(),
      grid_preset: document.getElementById('cm-preset').value,
      grid_seed: calForm.seed,
    }
    const r = await fetch('/scheduled-grids/' + g.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const j = await r.json()
    if (!j.ok) { alert('Save failed: ' + (j.error || '')); return }
    calCloseModal(); calLoad(); refreshAlerts()
  })
  document.getElementById('cm-delete').addEventListener('click', async () => {
    if (!confirm('Delete this scheduled grid post?')) return
    await fetch('/scheduled-grids/' + g.id, { method: 'DELETE' })
    calCloseModal(); calLoad(); refreshAlerts()
  })
  const postBtn = document.getElementById('cm-postnow')
  if (postBtn) postBtn.addEventListener('click', async () => {
    postBtn.disabled = true; postBtn.textContent = 'Posting…'
    const r = await fetch('/scheduled-grids/' + g.id + '/publish', { method: 'POST' })
    const j = await r.json()
    if (!j.ok) { alert('Post failed: ' + (j.error || '')); postBtn.disabled = false; postBtn.textContent = 'Post now'; calLoad(); refreshAlerts(); return }
    calCloseModal(); calLoad(); refreshAlerts()
  })
}

function calOpenDropEvent(d) {
  const when = calDropSlot(d)
  const canPost = d.status === 'failed' || d.status === 'scheduled'
  const html = '<h3>' + escapeHtml(d.name || ('Drop #' + d.id)) + '</h3>'
    + '<div class="cm-sub">' + calStatusBadge(d.status) + ' story sequence' + (when ? (' · ' + fmtLocal(when.toISOString())) : '') + '</div>'
    + '<div class="cm-preview"><img src="/drops/' + d.id + '/preview/1" alt="cover" /></div>'
    + (d.error ? '<div class="cm-error">' + escapeHtml(d.error) + '</div>' : '')
    + '<div class="cm-actions">'
    + '<button type="button" class="primary" id="cm-open">Open in Product drop</button>'
    + (canPost ? '<button type="button" id="cm-retry">' + (d.status === 'failed' ? 'Retry now' : 'Post now') + '</button>' : '')
    + '</div>'
  calShowModal(html)
  document.getElementById('cm-open').addEventListener('click', () => { calCloseModal(); calJumpToDrop(d.id) })
  const retry = document.getElementById('cm-retry')
  if (retry) retry.addEventListener('click', async () => {
    retry.disabled = true; retry.textContent = 'Posting…'
    const r = await fetch('/drops/' + d.id + '/publish', { method: 'POST' })
    const j = await r.json()
    if (!j.ok) { alert('Publish failed: ' + (j.error || '')); retry.disabled = false; calLoad(); refreshAlerts(); return }
    calCloseModal(); calLoad(); refreshAlerts()
  })
}

if ($('#cal-prev')) $('#cal-prev').addEventListener('click', () => calNav(-1))
if ($('#cal-next')) $('#cal-next').addEventListener('click', () => calNav(1))
if ($('#cal-today')) $('#cal-today').addEventListener('click', () => { cal.anchor = calStartOfToday(); calRender() })
$$('.cal-view-btn').forEach(b => b.addEventListener('click', () => calSetView(b.dataset.view)))

// ── Cross-tab failure banner ─────────────────────────────────────────────
async function refreshAlerts() {
  const banner = document.getElementById('alert-banner')
  if (!banner) return
  try {
    const r = await fetch('/alerts')
    const j = await r.json()
    const fails = j.failures || []
    if (!fails.length) { banner.hidden = true; banner.innerHTML = ''; return }
    const n = fails.length
    const first = fails[0]
    banner.hidden = false
    banner.innerHTML = '<strong>⚠ ' + n + (n === 1 ? ' post failed' : ' posts failed') + '</strong>'
      + '<span class="alert-detail">' + escapeHtml(first.label) + (n > 1 ? (' +' + (n - 1) + ' more') : '') + ' — ' + escapeHtml((first.error || 'unknown error').slice(0, 90)) + '</span>'
      + '<span class="alert-cta">View in Calendar →</span>'
  } catch (e) { /* leave banner as-is on a transient fetch error */ }
}
const alertBanner = document.getElementById('alert-banner')
if (alertBanner) alertBanner.addEventListener('click', () => { location.hash = 'calendar'; showFlow('calendar') })
setInterval(refreshAlerts, 30000)
refreshAlerts()
</script>
</body>
</html>`)
})

app.get('/thumb/:handle', async (c) => {
  const handle = c.req.param('handle')
  try {
    // Serves a 540×960 progressive JPEG (~50 KB) — the browser doesn't need
    // the 3 MB full-res PNG just to render a 218 px card. Lazy: builds the
    // thumb on demand if not cached.
    const thumbPath = await renderThumbForHandle(handle)
    const buf = readFileSync(thumbPath)
    return new Response(buf as unknown as BodyInit, {
      status: 200,
      headers: {
        'Content-Type': 'image/jpeg',
        // 5 min browser cache. Edits bypass via the `?lv=` cache-buster the
        // JS appends after look/draft saves.
        'Cache-Control': 'public, max-age=300',
      },
    })
  } catch (e) {
    return c.text(`render failed: ${e instanceof Error ? e.message : String(e)}`, 500)
  }
})

// Live preview — always re-renders. Optional :idx (1-based) selects a frame in the sequence.
app.get('/preview/:handle/:idx?', async (c) => {
  const handle = c.req.param('handle')
  const idxParam = c.req.param('idx')
  const idx = idxParam ? Math.max(1, parseInt(idxParam, 10) || 1) : 1
  try {
    const outPaths = await renderForHandle(handle, { force: true })
    const chosen = outPaths[Math.min(idx, outPaths.length) - 1]
    const buf = readFileSync(chosen)
    return new Response(buf as unknown as BodyInit, {
      status: 200,
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'no-cache',
        'X-Frame-Count': String(outPaths.length),
      },
    })
  } catch (e) {
    return c.text(`render failed: ${e instanceof Error ? e.message : String(e)}`, 500)
  }
})

// Returns the rendered sequence metadata for the current draft.
app.get('/sequence/:handle', (c) => {
  const handle = c.req.param('handle')
  try {
    const cfg = resolveConfig(handle)
    return c.json({
      ok: true,
      layout: cfg.layout,
      image_start: cfg.imageStart,
      image_count: cfg.imageCount,
      frame_count: cfg.frames.length,
    })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
  }
})

// Source image for the modal's image picker.
app.get('/source/:handle/:idx', async (c) => {
  const handle = c.req.param('handle')
  const idx = Number(c.req.param('idx'))
  const p = getProductImagePath(handle, idx)
  if (!p) return c.text('not found', 404)
  const abs = path.resolve(ROOT, p)
  if (!existsSync(abs)) return c.text('file missing', 404)
  const buf = readFileSync(abs)
  const ext = path.extname(abs).toLowerCase()
  const mime = ext === '.png' ? 'image/png' : 'image/jpeg'
  return new Response(buf as unknown as BodyInit, {
    status: 200,
    headers: { 'Content-Type': mime, 'Cache-Control': 'public, max-age=3600' },
  })
})

// Draft endpoints.
app.get('/draft/:handle', (c) => {
  const handle = c.req.param('handle')
  const info = getProductDraftInfo(handle)
  if (!info) return c.json({ error: 'not found' }, 404)
  return c.json(info)
})

app.post('/draft/:handle', async (c) => {
  const handle = c.req.param('handle')
  const body = (await c.req.json().catch(() => ({}))) as {
    layout?: Layout
    image_start?: number
    image_count?: number
    brand?: string
    name?: string
    price?: string
    link?: string
    look_category?: 'clothing' | 'bags' | null
  }
  const lookCat = body.look_category === 'clothing' || body.look_category === 'bags' ? body.look_category : null
  const db = openDb()
  try {
    const patch: Parameters<typeof upsertDraft>[2] = {
      layout: body.layout,
      image_start: typeof body.image_start === 'number' ? body.image_start : undefined,
      image_count: typeof body.image_count === 'number' ? body.image_count : undefined,
      brand: body.brand ?? null,
      name: body.name ?? null,
      price: body.price ?? null,
      link: body.link ?? null,
    }
    if ('look_category' in body) patch.look_category = lookCat
    const draft = upsertDraft(db, handle, patch)
    // The draft drives caption text + image range + category, so any change
    // invalidates this product's cached PNGs.
    invalidateRenderedFrames(handle)
    return c.json({ ok: true, draft })
  } finally {
    db.close()
  }
})

app.delete('/draft/:handle', (c) => {
  const handle = c.req.param('handle')
  const db = openDb()
  try {
    db.prepare(`DELETE FROM drafts WHERE handle = ?`).run(handle)
    invalidateRenderedFrames(handle)
    return c.json({ ok: true })
  } finally {
    db.close()
  }
})

// ── Product search (for drop picker) ──────────────────────────────────
app.get('/products/search', (c) => {
  const tag = c.req.query('tag') || undefined
  const query = c.req.query('q') || undefined
  const sort = c.req.query('sort') === 'newest' ? 'newest' : 'title'
  const limit = Math.max(1, Math.min(500, parseInt(c.req.query('limit') ?? '100', 10) || 100))
  const db = openDb()
  try {
    const rows = searchProducts(db, { tag, query, sort, limit })
    return c.json({ ok: true, items: rows })
  } finally {
    db.close()
  }
})

app.get('/tags', (c) => {
  const db = openDb()
  try {
    const tags = listAllTags(db)
    return c.json({ ok: true, tags })
  } finally {
    db.close()
  }
})

// ── Drop CRUD ─────────────────────────────────────────────────────────
app.get('/drops', (c) => {
  const db = openDb()
  try {
    return c.json({ ok: true, drops: listDrops(db) })
  } finally {
    db.close()
  }
})

app.post('/drops', async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as { name?: string }
  const db = openDb()
  try {
    const drop = createDrop(db, {
      name: body.name ?? '',
      cover_body: [...brand.flowDefaults.productDrop.body],
      cover_footer: brand.flowDefaults.productDrop.footer,
      cover_cta_url: brand.flowDefaults.productDrop.ctaUrl,
      trailing_arrow: true,
      closing_body: [...brand.flowDefaults.productDrop.closingBody],
      include_closing: brand.flowDefaults.productDrop.includeClosing,
    })
    return c.json({ ok: true, drop })
  } finally {
    db.close()
  }
})

app.get('/drops/:id', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const db = openDb()
  try {
    const drop = getDrop(db, id)
    if (!drop) return c.json({ ok: false, error: 'not found' }, 404)
    const items = getDropItems(db, id)
    // Which listed products are no longer in the catalog (sold/removed) — the
    // composer warns about these since they'll be silently skipped on publish.
    let missing: string[] = []
    try { missing = resolveDrop(id).missing } catch { /* resolve is best-effort here */ }
    return c.json({ ok: true, drop, items, missing })
  } finally {
    db.close()
  }
})

app.patch('/drops/:id', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const body = (await c.req.json().catch(() => ({}))) as {
    name?: string
    cover_body?: string[]
    cover_footer?: string | null
    cover_cta_url?: string | null
    trailing_arrow?: boolean
    closing_body?: string[]
    include_opening?: boolean
    include_closing?: boolean
  }
  const db = openDb()
  try {
    const patch: Parameters<typeof updateDrop>[2] = {}
    if (body.name !== undefined) patch.name = body.name
    if (body.cover_body !== undefined) patch.cover_body = body.cover_body
    if (body.cover_footer !== undefined) patch.cover_footer = body.cover_footer
    if (body.cover_cta_url !== undefined) patch.cover_cta_url = body.cover_cta_url
    if (body.trailing_arrow !== undefined) patch.trailing_arrow = body.trailing_arrow
    if (body.closing_body !== undefined) patch.closing_body = body.closing_body
    if (body.include_opening !== undefined) patch.include_opening = body.include_opening
    if (body.include_closing !== undefined) patch.include_closing = body.include_closing
    const drop = updateDrop(db, id, patch)
    return c.json({ ok: true, drop })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
  } finally {
    db.close()
  }
})

app.delete('/drops/:id', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const db = openDb()
  try {
    deleteDrop(db, id)
    return c.json({ ok: true })
  } finally {
    db.close()
  }
})

app.put('/drops/:id/items', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const body = (await c.req.json().catch(() => ({}))) as { items?: Array<Omit<DropItem, 'drop_id'>> }
  const items = body.items ?? []
  const db = openDb()
  try {
    setDropItems(db, id, items)
    return c.json({ ok: true, items: getDropItems(db, id) })
  } finally {
    db.close()
  }
})

app.get('/drops/:id/sequence', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  try {
    const { frames, items } = resolveDrop(id)
    return c.json({ ok: true, frame_count: frames.length, item_count: items.length })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
  }
})

app.get('/drops/:id/preview/:idx', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const idx = Math.max(1, parseInt(c.req.param('idx'), 10) || 1)
  try {
    const { frames } = resolveDrop(id)
    const frame = frames[Math.min(idx, frames.length) - 1]
    if (!frame) return c.text('frame not found', 404)
    const png = await renderDropFramePng(frame)
    return new Response(png as unknown as BodyInit, {
      status: 200,
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'no-cache',
        'X-Frame-Count': String(frames.length),
      },
    })
  } catch (e) {
    return c.text(`render failed: ${e instanceof Error ? e.message : String(e)}`, 500)
  }
})

// Promo grid: a seeded-random collage of the drop's product tiles.
// Same params → same image, so the preview, download, and post actions all
// agree on what the user saw.
app.get('/drops/:id/grid.jpg', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const preset = parseGridPreset(c.req.query('grid')) ?? { cols: 2, rows: 3 }
  const format = parseGridFormat(c.req.query('format'))
  const seed = parseInt(c.req.query('seed') ?? '1', 10) || 1
  try {
    const jpeg = await renderDropGridJpeg(id, { ...preset, format, seed })
    const headers: Record<string, string> = {
      'Content-Type': 'image/jpeg',
      'Cache-Control': 'no-cache',
    }
    if (c.req.query('download')) {
      const db = openDb()
      let name = `drop-${id}`
      try {
        const d = getDrop(db, id)
        if (d?.name) name = d.name.replace(/[^\w-]+/g, '_')
      } finally {
        db.close()
      }
      headers['Content-Disposition'] = `attachment; filename="${name}-grid-${preset.cols}x${preset.rows}-${format}.jpg"`
    }
    return new Response(jpeg as unknown as BodyInit, { status: 200, headers })
  } catch (e) {
    return c.text(`grid render failed: ${e instanceof Error ? e.message : String(e)}`, 500)
  }
})

app.post('/drops/:id/grid/post', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const body = (await c.req.json().catch(() => ({}))) as { grid?: string; seed?: number }
  const preset = parseGridPreset(body.grid) ?? { cols: 2, rows: 3 }
  const seed = Number(body.seed) || 1
  try {
    const { mediaId } = await postDropGridStory(id, { ...preset, seed })
    return c.json({ ok: true, ig_media_id: mediaId })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
  }
})

app.post('/drops/:id/publish', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const db = openDb()
  let drop
  try {
    drop = getDrop(db, id)
  } finally {
    db.close()
  }
  if (!drop) return c.json({ ok: false, error: 'not found' }, 404)
  if (drop.status === 'publishing') return c.json({ ok: false, error: 'already publishing' }, 409)
  if (drop.status === 'published') return c.json({ ok: false, error: 'already published' }, 409)
  // Publishing a multi-frame drop posts each story sequentially and can run far
  // past the reverse-proxy's request timeout (Cloudflare ~100s). Returning here
  // and letting the client poll drop status avoids a misleading "failed" (an
  // HTML timeout page) while the server is in fact still posting successfully.
  publishDrop(id)
    .then(({ drop: d }) => console.log(`[publish] drop ${id} published (${d.ig_media_ids?.length ?? 0} stories)`))
    .catch((e) => {
      const msg = e instanceof Error ? e.message : String(e)
      console.error(`[publish] drop ${id} failed:`, msg)
      notifyScheduledFailure('drop', drop?.name || `Drop #${id}`, msg)
    })
  return c.json({ ok: true, status: 'publishing' })
})

// Schedule a drop for automatic publishing. The scheduler loop below picks
// it up once scheduled_at passes.
app.post('/drops/:id/schedule', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const body = (await c.req.json().catch(() => ({}))) as { scheduled_at?: string }
  const when = body.scheduled_at ? Date.parse(body.scheduled_at) : NaN
  if (!Number.isFinite(when)) return c.json({ ok: false, error: 'invalid scheduled_at' }, 400)
  if (when <= Date.now()) return c.json({ ok: false, error: 'scheduled time must be in the future' }, 400)
  const db = openDb()
  try {
    const existing = getDrop(db, id)
    if (!existing) return c.json({ ok: false, error: 'not found' }, 404)
    if (existing.status === 'publishing' || existing.status === 'published') {
      return c.json({ ok: false, error: `cannot schedule a ${existing.status} drop` }, 400)
    }
    const drop = updateDrop(db, id, {
      status: 'scheduled',
      scheduled_at: new Date(when).toISOString(),
      error: null,
    })
    return c.json({ ok: true, drop })
  } finally {
    db.close()
  }
})

app.delete('/drops/:id/schedule', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const db = openDb()
  try {
    const existing = getDrop(db, id)
    if (!existing) return c.json({ ok: false, error: 'not found' }, 404)
    if (existing.status !== 'scheduled') {
      return c.json({ ok: false, error: 'drop is not scheduled' }, 400)
    }
    const drop = updateDrop(db, id, { status: 'draft', scheduled_at: null })
    return c.json({ ok: true, drop })
  } finally {
    db.close()
  }
})

// ── Scheduled grid posts (calendar events) ───────────────────────────────
app.get('/scheduled-grids', (c) => {
  const db = openDb()
  try {
    return c.json({ ok: true, grids: listScheduledGrids(db) })
  } finally {
    db.close()
  }
})

app.post('/scheduled-grids', async (c) => {
  const body = (await c.req.json().catch(() => ({}))) as {
    drop_id?: number
    title?: string
    scheduled_at?: string
    grid_preset?: string
    grid_seed?: number
  }
  const dropId = Number(body.drop_id)
  if (!Number.isFinite(dropId)) return c.json({ ok: false, error: 'drop_id required' }, 400)
  const when = body.scheduled_at ? Date.parse(body.scheduled_at) : NaN
  if (!Number.isFinite(when)) return c.json({ ok: false, error: 'invalid scheduled_at' }, 400)
  if (when <= Date.now()) return c.json({ ok: false, error: 'scheduled time must be in the future' }, 400)
  if (!parseGridPreset(body.grid_preset)) return c.json({ ok: false, error: 'invalid grid layout' }, 400)
  const db = openDb()
  try {
    if (!getDrop(db, dropId)) return c.json({ ok: false, error: 'drop not found' }, 404)
    const grid = createScheduledGrid(db, {
      drop_id: dropId,
      title: body.title ?? '',
      scheduled_at: new Date(when).toISOString(),
      grid_preset: body.grid_preset,
      grid_seed: Number(body.grid_seed) || 1,
    })
    return c.json({ ok: true, grid })
  } finally {
    db.close()
  }
})

app.patch('/scheduled-grids/:id', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const body = (await c.req.json().catch(() => ({}))) as {
    title?: string
    scheduled_at?: string
    grid_preset?: string
    grid_seed?: number
    drop_id?: number
  }
  const db = openDb()
  try {
    const existing = getScheduledGrid(db, id)
    if (!existing) return c.json({ ok: false, error: 'not found' }, 404)
    if (existing.status === 'publishing') return c.json({ ok: false, error: 'currently publishing' }, 400)
    const patch: Parameters<typeof updateScheduledGrid>[2] = {}
    if (body.drop_id !== undefined) {
      const did = Number(body.drop_id)
      if (!Number.isFinite(did) || !getDrop(db, did)) return c.json({ ok: false, error: 'invalid drop' }, 400)
      patch.drop_id = did
    }
    if (body.title !== undefined) patch.title = body.title
    if (body.grid_seed !== undefined) patch.grid_seed = Number(body.grid_seed) || 1
    if (body.grid_preset !== undefined) {
      if (!parseGridPreset(body.grid_preset)) return c.json({ ok: false, error: 'invalid grid layout' }, 400)
      patch.grid_preset = body.grid_preset
    }
    if (body.scheduled_at !== undefined) {
      const when = Date.parse(body.scheduled_at)
      if (!Number.isFinite(when)) return c.json({ ok: false, error: 'invalid scheduled_at' }, 400)
      if (when <= Date.now()) return c.json({ ok: false, error: 'scheduled time must be in the future' }, 400)
      patch.scheduled_at = new Date(when).toISOString()
      // Editing the time re-arms a failed/published event so it fires again.
      patch.status = 'scheduled'
      patch.error = null
    }
    const grid = updateScheduledGrid(db, id, patch)
    return c.json({ ok: true, grid })
  } finally {
    db.close()
  }
})

app.delete('/scheduled-grids/:id', (c) => {
  const id = parseInt(c.req.param('id'), 10)
  const db = openDb()
  try {
    deleteScheduledGrid(db, id)
    return c.json({ ok: true })
  } finally {
    db.close()
  }
})

// Manual "post now" / retry for a scheduled grid.
app.post('/scheduled-grids/:id/publish', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  try {
    const { mediaId } = await publishScheduledGrid(id)
    return c.json({ ok: true, ig_media_id: mediaId })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
  }
})

// Everything currently in a failed state — powers the cross-tab red banner.
app.get('/alerts', (c) => {
  const db = openDb()
  try {
    const failures: Array<{ kind: string; id: number; label: string; error: string | null; when: string | null }> = []
    for (const d of listDrops(db)) {
      if (d.status === 'failed') {
        failures.push({ kind: 'drop', id: d.id, label: d.name || `Drop #${d.id}`, error: d.error, when: d.scheduled_at ?? d.updated_at })
      }
    }
    for (const g of listScheduledGrids(db)) {
      if (g.status === 'failed') {
        failures.push({ kind: 'grid', id: g.id, label: g.title || `Grid #${g.id}`, error: g.error, when: g.scheduled_at })
      }
    }
    return c.json({ ok: true, failures })
  } finally {
    db.close()
  }
})

app.post('/publish/:handle', async (c) => {
  const handle = c.req.param('handle')
  try {
    const { post } = await publishHandle(handle)
    return c.json({ ok: true, ig_media_id: post.ig_media_id })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error(`publish ${handle} failed:`, msg)
    return c.json({ ok: false, error: msg }, 500)
  }
})

let syncInFlight: Promise<{ ok: boolean; output: string; finishedAt: string | null }> | null = null

function runShopifySync(): Promise<{ ok: boolean; output: string; finishedAt: string | null }> {
  if (syncInFlight) return syncInFlight
  syncInFlight = new Promise((resolve) => {
    const proc = spawn('python3', ['scripts/sync.py'], {
      cwd: ROOT,
      env: process.env,
    })
    const chunks: string[] = []
    proc.stdout.on('data', (d) => chunks.push(d.toString()))
    proc.stderr.on('data', (d) => chunks.push(d.toString()))
    proc.on('close', (code) => {
      const output = chunks.join('')
      const ok = code === 0
      const finishedAt = ok ? getLastSyncFinishedAt() : null
      resolve({ ok, output, finishedAt })
    })
    proc.on('error', (e) => {
      resolve({ ok: false, output: `spawn failed: ${e.message}`, finishedAt: null })
    })
  })
  syncInFlight.finally(() => {
    syncInFlight = null
  })
  return syncInFlight
}

app.post('/api/sync', async (c) => {
  const result = await runShopifySync()
  return c.json(result, result.ok ? 200 : 500)
})

function parseCategory(raw: string | undefined): Category {
  if (raw === 'bags') return 'bags'
  return 'clothing'
}

app.get('/api/look-settings', (c) => {
  const cat = parseCategory(c.req.query('category'))
  return c.json(getLookSettings(cat))
})

app.put('/api/look-settings', async (c) => {
  const cat = parseCategory(c.req.query('category'))
  try {
    const body = await c.req.json()
    const saved = saveLookSettings(cat, body as Partial<LookSettings>)
    // Look change affects every product thumb. Wipe the cache; thumbs will
    // re-render lazily on the next request (which the client immediately
    // triggers via the `?lv=` cache-buster).
    invalidateAllRenderedFrames()
    return c.json(saved)
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 400)
  }
})

app.delete('/api/look-settings', (c) => {
  const cat = parseCategory(c.req.query('category'))
  const reset = resetLookSettings(cat)
  invalidateAllRenderedFrames()
  return c.json(reset)
})

const PORT = Number(process.env.PORT ?? 3001)
serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`→ Review UI: http://localhost:${info.port}`)
})

// ── Automatic background sync ────────────────────────────────────────────
// Without this, the cached catalogue drifts: items sold on Shopify still
// show up here as in-stock until someone hits Refresh. We've seen 19-day
// gaps, so the tool needed a way to keep itself current.
//
// Strategy:
//   1. At boot, kick a sync if the last one was >SYNC_FRESH_AFTER_MS ago.
//   2. Re-sync on a fixed interval after that.
// runShopifySync() already debounces concurrent calls, so it's safe to
// stack the boot-time sync with the interval.
const SYNC_INTERVAL_MS = 60 * 60 * 1000          // 1 hour
const SYNC_FRESH_AFTER_MS = 6 * 60 * 60 * 1000   // 6 hours: skip boot sync if newer

function maybeBootSync(): void {
  const last = getLastSyncFinishedAt()
  const lastMs = last ? Date.parse(last) : 0
  const ageMs = Date.now() - (Number.isFinite(lastMs) ? lastMs : 0)
  if (ageMs >= SYNC_FRESH_AFTER_MS) {
    console.log(`[sync] last sync ${last ?? 'never'} — running boot sync`)
    runShopifySync().then((r) => {
      console.log(`[sync] boot sync ${r.ok ? 'ok' : 'FAILED'} at ${r.finishedAt ?? 'unknown'}`)
      // Stale renders are detected per-product at serve time (thumb mtime vs
      // products.updated_at), so no blanket cache wipe is needed here.
    }).catch((e) => console.error('[sync] boot sync error:', e))
  } else {
    console.log(`[sync] last sync ${last} is fresh (${Math.round(ageMs / 60000)}m old); skipping boot sync`)
  }
}

setInterval(() => {
  console.log('[sync] periodic sync starting')
  runShopifySync().then((r) => {
    console.log(`[sync] periodic sync ${r.ok ? 'ok' : 'FAILED'} at ${r.finishedAt ?? 'unknown'}`)
  }).catch((e) => console.error('[sync] periodic sync error:', e))
}, SYNC_INTERVAL_MS)

maybeBootSync()

// Snapshot existing drop items that are still live, so they keep posting even
// if their product is later sold/renamed (new adds snapshot automatically).
try {
  const sdb = openDb()
  try {
    const n = backfillDropItemSnapshots(sdb)
    if (n) console.log(`[drops] snapshotted ${n} existing drop item(s) for resilience`)
  } finally {
    sdb.close()
  }
} catch (e) {
  console.error('[drops] snapshot backfill failed:', e instanceof Error ? e.message : e)
}

// ── Scheduled drop publisher ─────────────────────────────────────────────
// Drops with status='scheduled' publish automatically once scheduled_at
// passes. The launchd agent keeps this process alive, so an in-process loop
// is reliable enough — no external cron needed. publishDrop's atomic status
// claim makes double-fires (or a second server instance) harmless.
const SCHEDULE_TICK_MS = 30 * 1000

setInterval(() => {
  let due: number[] = []
  const db = openDb()
  try {
    due = (
      db
        .prepare(
          `SELECT id FROM drops
           WHERE status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= ?`,
        )
        .all(new Date().toISOString()) as Array<{ id: number }>
    ).map((r) => r.id)
  } finally {
    db.close()
  }
  for (const id of due) {
    console.log(`[schedule] drop ${id} is due — publishing`)
    publishDrop(id)
      .then(({ drop }) => {
        console.log(`[schedule] drop ${id} published (${drop.ig_media_ids?.length ?? 0} stories)`)
      })
      .catch((e) => {
        const msg = e instanceof Error ? e.message : String(e)
        console.error(`[schedule] drop ${id} failed:`, msg)
        notifyScheduledFailure('drop', `Drop #${id}`, msg)
      })
  }

  // Scheduled grid posts, same due-and-fire pattern.
  let dueGrids: Array<{ id: number; title: string }> = []
  const gdb = openDb()
  try {
    dueGrids = gdb
      .prepare(
        `SELECT id, title FROM scheduled_grids
         WHERE status = 'scheduled' AND scheduled_at <= ?`,
      )
      .all(new Date().toISOString()) as Array<{ id: number; title: string }>
  } finally {
    gdb.close()
  }
  for (const g of dueGrids) {
    console.log(`[schedule] grid ${g.id} is due — publishing`)
    publishScheduledGrid(g.id)
      .then(({ mediaId }) => console.log(`[schedule] grid ${g.id} published (media ${mediaId})`))
      .catch((e) => {
        const msg = e instanceof Error ? e.message : String(e)
        console.error(`[schedule] grid ${g.id} failed:`, msg)
        notifyScheduledFailure('grid', g.title || `Grid #${g.id}`, msg)
      })
  }
}, SCHEDULE_TICK_MS)
