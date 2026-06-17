// look-settings.ts — JSON-file-backed settings for how the story caption
// is rendered. Edited live from the Look tab.
//
// Position is stored as percentages of the 1080×1920 canvas (xPct, yPct),
// not pixels — easier to reason about ("about a third down the canvas")
// and survives canvas size changes if we ever introduce alternative formats.
//
// Per-category storage because the visual sweet spot differs between
// clothing (caption lower, on the garment) and bags (caption higher, off
// the strap).

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const DATA_DIR = path.join(ROOT, 'data')
const SETTINGS_PATH = path.join(DATA_DIR, 'look-settings.json')

// Canvas constants used for px↔% conversion when migrating old saved data.
const CANVAS_WIDTH = 1080
const CANVAS_HEIGHT = 1920

export type Category = 'clothing' | 'bags'
export const CATEGORIES: readonly Category[] = ['clothing', 'bags'] as const

export interface LookSettings {
  color: string // text color, e.g. "#1a1614" or "#ffffff"
  align: 'left' | 'center' | 'right'
  // Distance from the active edge as a % of canvas width.
  //   align=left  → xPct% from canvas left
  //   align=right → xPct% from canvas right
  //   align=center → ignored, caption is always centered
  xPct: number
  // Distance of the caption block's top edge from the canvas top,
  // as a % of canvas height.
  yPct: number
  brandSize: number
  nameSize: number
  priceSize: number
  showBrand: boolean
  showName: boolean
  showPrice: boolean
  showDollar: boolean
}

export const DEFAULTS: LookSettings = {
  color: '#1a1614',
  align: 'left',
  xPct: 6.5, // ~70 px from left edge on a 1080-wide canvas
  yPct: 48,  // ~920 px from top — middle-ish of a 1920-tall canvas
  brandSize: 36,
  nameSize: 30,
  priceSize: 28,
  showBrand: true,
  showName: false,
  showPrice: true,
  showDollar: false,
}

// Bag-detection regex. Matches against the combined "product_type + title"
// text because ~918 active products have an empty product_type, so the title
// is often the only signal we have. Covers Shopify product_type values
// ("Bag", "Bags", "Shoulder Bag", "Handbag", "Clutch Bag", "Tote Bag", etc.)
// plus title-only cues like "Hobo", "Sac Plat", "Handbag", "Pouch", "Crossbody".
// Word boundaries on the short ambiguous tokens (bag/purse/sac) prevent
// matching inside unrelated words.
const BAG_RE = /handbag|backpack|crossbody|shoulder ?bag|tote bag|\btotes?\b|\bclutch(es)?\b|\bpouch(es)?\b|\bwallets?\b|\bsatchels?\b|\bhobo\b|duff(le|el)|\bagenda\b|\bbags?\b|\bpurses?\b|\bsac\b/i

export function categorize(productType: string | null | undefined, title?: string | null): Category {
  const text = `${productType ?? ''} ${title ?? ''}`
  if (BAG_RE.test(text)) return 'bags'
  return 'clothing'
}

function clamp(n: number, min: number, max: number, fallback: number): number {
  return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback
}

// Accepts either the new shape (xPct/yPct) or the legacy shape
// (left/right/top in pixels) and produces a fully-defaulted LookSettings.
type LegacyFields = { left?: number; right?: number; top?: number }
function coerce(input: (Partial<LookSettings> & LegacyFields) | undefined | null): LookSettings {
  const raw = (input || {}) as Partial<LookSettings> & LegacyFields
  // Migrate legacy pixel fields → percentages.
  // Old "left" was used when align=left; old "right" when align=right. Both
  // meant "distance from the active edge", so either maps to xPct.
  let xPct = raw.xPct
  if (xPct === undefined) {
    const legacyEdge = raw.align === 'right' ? raw.right : raw.left
    if (typeof legacyEdge === 'number') xPct = (legacyEdge / CANVAS_WIDTH) * 100
  }
  let yPct = raw.yPct
  if (yPct === undefined && typeof raw.top === 'number') {
    yPct = (raw.top / CANVAS_HEIGHT) * 100
  }

  const s: LookSettings = {
    ...DEFAULTS,
    ...raw,
    xPct: xPct ?? DEFAULTS.xPct,
    yPct: yPct ?? DEFAULTS.yPct,
  } as LookSettings

  if (s.align !== 'left' && s.align !== 'center' && s.align !== 'right') s.align = DEFAULTS.align
  s.xPct = clamp(s.xPct, 0, 50, DEFAULTS.xPct)
  s.yPct = clamp(s.yPct, 0, 100, DEFAULTS.yPct)
  s.brandSize = clamp(s.brandSize, 12, 200, DEFAULTS.brandSize)
  s.nameSize = clamp(s.nameSize, 12, 200, DEFAULTS.nameSize)
  s.priceSize = clamp(s.priceSize, 12, 200, DEFAULTS.priceSize)
  if (typeof s.color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(s.color)) s.color = DEFAULTS.color
  s.showBrand = !!s.showBrand
  s.showName = !!s.showName
  s.showPrice = !!s.showPrice
  s.showDollar = !!s.showDollar
  return s
}

export type LookSettingsByCategory = Record<Category, LookSettings>

function emptyFile(): LookSettingsByCategory {
  return { clothing: { ...DEFAULTS }, bags: { ...DEFAULTS } }
}

function loadFile(): LookSettingsByCategory {
  if (!existsSync(SETTINGS_PATH)) return emptyFile()
  try {
    const raw = readFileSync(SETTINGS_PATH, 'utf-8')
    const parsed = JSON.parse(raw) as unknown
    // Back-compat: older format was a flat LookSettings. Promote to clothing.
    if (parsed && typeof parsed === 'object' && 'color' in (parsed as object) && !('clothing' in (parsed as object))) {
      return { clothing: coerce(parsed as Partial<LookSettings>), bags: { ...DEFAULTS } }
    }
    const obj = (parsed && typeof parsed === 'object') ? (parsed as Partial<LookSettingsByCategory>) : {}
    return {
      clothing: coerce(obj.clothing),
      bags: coerce(obj.bags),
    }
  } catch {
    return emptyFile()
  }
}

function writeFile(file: LookSettingsByCategory): void {
  mkdirSync(DATA_DIR, { recursive: true })
  writeFileSync(SETTINGS_PATH, JSON.stringify(file, null, 2))
}

export function getAllLookSettings(): LookSettingsByCategory {
  return loadFile()
}

export function getLookSettings(category: Category = 'clothing'): LookSettings {
  return loadFile()[category]
}

export function saveLookSettings(category: Category, input: Partial<LookSettings>): LookSettings {
  const file = loadFile()
  file[category] = coerce({ ...file[category], ...input })
  writeFile(file)
  return file[category]
}

export function resetLookSettings(category: Category): LookSettings {
  const file = loadFile()
  file[category] = { ...DEFAULTS }
  writeFile(file)
  return file[category]
}
