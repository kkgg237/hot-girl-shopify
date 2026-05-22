import { Hono } from 'hono'
import { serve } from '@hono/node-server'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  listReviewItems,
  publishHandle,
  renderForHandle,
  resolveConfig,
  getProductDraftInfo,
  getProductImagePath,
  resolveDrop,
  renderDropFramePng,
  publishDrop,
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
  type Layout,
  type DropItem,
} from './db.js'
import { brand } from './brand.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT_DIR = path.join(ROOT, 'out')
mkdirSync(OUT_DIR, { recursive: true })

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
  .drop-status { margin-left: auto; font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.08em; }

  .drop-grid { display: grid; grid-template-columns: minmax(340px, 1fr) minmax(360px, 1fr); gap: 24px; }
  .drop-picker, .drop-compose { background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 18px 18px 22px; }
  .drop-picker h3, .drop-compose h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: #555; margin: 0 0 12px; font-weight: 600; }

  .picker-tabs { display: inline-flex; gap: 4px; background: #f4f4f4; border-radius: 8px; padding: 4px; margin-bottom: 10px; }
  .picker-tab { padding: 6px 12px; background: transparent; border: none; border-radius: 6px; font: inherit; font-size: 11px; font-weight: 600; cursor: pointer; color: #555; }
  .picker-tab.on { background: #fff; color: #111; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  .picker-controls { display: flex; gap: 8px; margin-bottom: 12px; }
  .picker-controls input { flex: 1; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; font-size: 13px; }

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
`

const FLOWS = [
  {
    slug: 'single-product',
    title: 'Single product',
    description: 'Pick a product and post a sequence of story photos with caption.',
    status: 'ready',
  },
  {
    slug: 'product-drop',
    title: 'Product drop',
    description: 'Cover card + multiple products posted as one continuous story sequence.',
    status: 'wip',
  },
  {
    slug: 'studio-event',
    title: 'Studio / event',
    description: 'Announcement card with custom copy and photos (open house, drops, hours).',
    status: 'wip',
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

  const card = (i: ReviewItem) => `
    <div class="card" data-handle="${i.handle}" data-brand="${escape(i.brand.toLowerCase())}" data-title="${escape(i.title.toLowerCase())}" data-status="${i.status}">
      <img class="thumb" src="/thumb/${i.handle}?t=${Date.now()}" loading="lazy" data-handle="${i.handle}" />
      <div class="meta">
        <div class="brand">${escape(i.brand) || '&nbsp;'}</div>
        <div class="title">${escape(i.title)}</div>
        <div class="price">${i.price ? escape(i.price) : '&nbsp;'} · ${i.image_count} images</div>
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

      <div class="drop-grid">
        <section class="drop-picker">
          <h3>Add products</h3>
          <div class="picker-tabs">
            <button data-mode="newest" class="picker-tab on">Newest</button>
            <button data-mode="tag" class="picker-tab">By tag</button>
          </div>
          <div class="picker-controls">
            <input id="picker-tag" type="text" placeholder="tag (e.g. 2025_Cavalli)" style="display:none" />
            <input id="picker-query" type="text" placeholder="search title/vendor" />
            <button id="picker-add" class="primary">Add selected</button>
          </div>
          <div class="picker-list" id="picker-list"></div>
        </section>

        <section class="drop-compose">
          <h3>In this drop</h3>
          <div class="drop-items" id="drop-items"></div>

          <h3 style="margin-top:24px">Opening cover</h3>
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

          <div class="drop-actions">
            <button class="secondary" id="drop-delete">Delete drop</button>
            <button class="primary" id="drop-publish">Publish drop</button>
          </div>
        </section>
      </div>
    </div>
  </div>

  <div class="flow-panel" id="panel-studio-event" hidden>
    <div class="wip-panel">
      <h2>Studio / event — coming soon</h2>
      <p>This flow will let you author an announcement card (open house, new drop, hours) and post it as a cover-style IG story, with optional studio photos.</p>
    </div>
  </div>
</main>

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

// ---- Flow tabs ----
const FLOW_SLUGS = ['single-product', 'product-drop', 'studio-event']
function showFlow(slug) {
  if (!FLOW_SLUGS.includes(slug)) slug = 'single-product'
  FLOW_SLUGS.forEach(s => {
    const panel = document.getElementById('panel-' + s)
    if (panel) panel.hidden = s !== slug
  })
  $$('.flow-tab').forEach(b => b.classList.toggle('active', b.dataset.flow === slug))
  // Filters/counts only relevant for the single-product flow.
  const filters = $('#flow-filters')
  const counts = $('#counts')
  if (filters) filters.style.display = slug === 'single-product' ? '' : 'none'
  if (counts) counts.style.display = slug === 'single-product' ? '' : 'none'
}
$$('.flow-tab').forEach(b => {
  b.addEventListener('click', () => {
    const slug = b.dataset.flow
    location.hash = slug
    showFlow(slug)
  })
})
window.addEventListener('hashchange', () => showFlow(location.hash.replace(/^#/, '')))
showFlow(location.hash.replace(/^#/, '') || 'single-product')

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
  else { drop.state.items = []; drop.state.cover = null; dropRender() }
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
  $('#closing-body').value = (j.drop.closing_body || []).join('\\n')
  $('#closing-include').checked = !!j.drop.include_closing
  $('#drop-status').textContent = j.drop.status
  dropRender()
  dropRefreshPreview()
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

// ── Picker ──
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
}

$$('.picker-tab').forEach(b => {
  b.addEventListener('click', () => {
    drop.state.pickerMode = b.dataset.mode
    $$('.picker-tab').forEach(x => x.classList.toggle('on', x === b))
    $('#picker-tag').style.display = drop.state.pickerMode === 'tag' ? '' : 'none'
    dropPickerLoad()
  })
})
$('#picker-tag').addEventListener('input', () => clearTimeout(drop.saveTimer) || setTimeout(dropPickerLoad, 300))
$('#picker-query').addEventListener('input', () => clearTimeout(drop.saveTimer) || setTimeout(dropPickerLoad, 300))

$('#picker-add').addEventListener('click', async () => {
  if (!drop.state.currentId) { alert('Create a drop first'); return }
  if (!drop.state.picked.size) return
  const inDrop = new Set(drop.state.items.map(it => it.handle))
  const additions = Array.from(drop.state.picked).filter(h => !inDrop.has(h))
  const newItems = drop.state.items.concat(additions.map(h => ({ handle: h, image_start: 0, image_count: 4 })))
  drop.state.picked.clear()
  await dropSaveItems(newItems)
  dropPickerRender()
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
$('#closing-body').addEventListener('input', dropScheduleSave)
$('#closing-include').addEventListener('change', dropScheduleSave)

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

$('#drop-publish').addEventListener('click', async () => {
  if (!drop.state.currentId) return
  if (!drop.state.items.length) { alert('Add some products first'); return }
  if (!confirm('Publish ' + drop.state.frameTotal + ' stories to Instagram?')) return
  $('#drop-publish').disabled = true
  $('#drop-publish').textContent = 'Publishing…'
  try {
    const r = await fetch('/drops/' + drop.state.currentId + '/publish', { method: 'POST' })
    const j = await r.json()
    if (!j.ok) throw new Error(j.error || 'publish failed')
    alert('Published! ' + (j.drop.ig_media_ids ? j.drop.ig_media_ids.length : 0) + ' stories live.')
    await dropLoadAll()
  } catch (e) {
    alert('Publish failed: ' + (e.message || e))
  } finally {
    $('#drop-publish').disabled = false
    $('#drop-publish').textContent = 'Publish drop'
  }
})

// Boot the drop UI if we land on (or switch to) that flow
window.addEventListener('hashchange', () => {
  if (location.hash === '#product-drop') { dropLoadAll(); dropPickerLoad() }
})
if (location.hash.replace(/^#/, '') === 'product-drop') {
  dropLoadAll()
  dropPickerLoad()
} else {
  // Lazy-init when user clicks the tab
  $$('.flow-tab').forEach(b => {
    if (b.dataset.flow === 'product-drop') {
      b.addEventListener('click', () => { if (!drop.state.drops.length) { dropLoadAll(); dropPickerLoad() } }, { once: true })
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
  }

  $('#modal-brand').textContent = data.defaults.brand || data.vendor || ''
  $('#modal-title').textContent = data.title

  fBrand.value = draft.brand ?? data.defaults.brand
  fName.value = draft.name ?? data.defaults.name
  fPrice.value = draft.price ?? data.defaults.price
  fLink.value = draft.link ?? data.defaults.link
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
</script>
</body>
</html>`)
})

app.get('/thumb/:handle', async (c) => {
  const handle = c.req.param('handle')
  try {
    const outPaths = await renderForHandle(handle)
    const buf = readFileSync(outPaths[0])
    return new Response(buf as unknown as BodyInit, {
      status: 200,
      headers: { 'Content-Type': 'image/png', 'Cache-Control': 'no-cache' },
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
    const outPaths = await renderForHandle(handle)
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
  }
  const db = openDb()
  try {
    const draft = upsertDraft(db, handle, {
      layout: body.layout,
      image_start: typeof body.image_start === 'number' ? body.image_start : undefined,
      image_count: typeof body.image_count === 'number' ? body.image_count : undefined,
      brand: body.brand ?? null,
      name: body.name ?? null,
      price: body.price ?? null,
      link: body.link ?? null,
    })
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
    return c.json({ ok: true, drop, items })
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

app.post('/drops/:id/publish', async (c) => {
  const id = parseInt(c.req.param('id'), 10)
  try {
    const { drop } = await publishDrop(id)
    return c.json({ ok: true, drop })
  } catch (e) {
    return c.json({ ok: false, error: e instanceof Error ? e.message : String(e) }, 500)
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

const PORT = Number(process.env.PORT ?? 3001)
serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`→ Review UI: http://localhost:${info.port}`)
})
