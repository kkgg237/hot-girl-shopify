import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Single source of truth for past studies brand identity.
// Change values here and every cover, story, and CTA across the app updates.

const __dirname = path.dirname(fileURLToPath(import.meta.url))
export const BRAND_ASSETS_DIR = path.resolve(__dirname, 'brand-assets')

export const brand = {
  // ── Identity ──────────────────────────────────────────────────
  name: 'PAST STUDIES',
  wordmark: 'PAST STUDIES', // text fallback when image not used
  wordmarkImageFile: 'wordmark.png', // resolved against BRAND_ASSETS_DIR
  wordmarkImageWidth: 260, // rendered display width on covers (px)
  wordmarkImageHeight: 260, // square logo

  // ── URLs / social ────────────────────────────────────────────
  website: 'paststudies.shop',
  websiteUrl: 'https://paststudies.shop',
  instagramHandle: '@paststudies',
  appointmentsUrl: 'https://paststudies.shop/pages/visit-us',
  productUrlTemplate: (handle: string) => `https://paststudies.shop/products/${handle}`,

  // ── Studio info ──────────────────────────────────────────────
  studioHours: 'Saturday & Sunday, 12 – 4 PM\nWeekdays by appointment',
  studioLocation: 'West Loop, Chicago',

  // ── Copy presets (reusable phrases) ───────────────────────────
  // URLs are omitted from cover copy — IG link stickers (via the `link` param on
  // publish) render an interactive "Shop new drop" / "Book appointment" pill on top
  // of the story, so we don't repeat the URL as text.
  copy: {
    dropIntro: 'All of the following items are currently available for purchase in studio or through our website',
    shippingUSA: 'Complimentary shipping USA',
    appointmentInvite: 'Visit our West Loop, Chicago studio. Book an appointment here.',
    visitInStudio: 'Visit us in West Loop, Chicago',
  },

  // ── CTA labels (used by IG link stickers and button drawing) ──
  cta: {
    shopDrop: 'Shop our new drop',
    bookAppointment: 'Book an appointment here',
    visitStudio: 'Visit our studio',
  },

  // ── Color palette (cream / ecru / warm neutrals) ──────────────
  colors: {
    cream: '#f3ebd9',
    ecru: '#e8dcc1',
    text: '#6b6258',      // warm medium grey for cover copy (felt-style)
    textMuted: '#8c8378',
    accent: '#1a1614',
    storyBg: '#ffffff',
  },

  // ── Typography roles ─────────────────────────────────────────
  // Single font family across the system: Red Hat Text. Weight + case + size
  // carry the hierarchy. (Newsreader is still registered as a fallback but
  // not referenced here.)
  type: {
    wordmark:     { fontFamily: 'Red Hat Text', fontWeight: 700 as const, sizePx: 168, tracking: -2 },
    heading:      { fontFamily: 'Red Hat Text', fontWeight: 700 as const, sizePx: 44,  tracking: 1 },
    body:         { fontFamily: 'Red Hat Text', fontWeight: 500 as const, sizePx: 30,  lineHeight: 1.18, tracking: 1.4 },
    footer:       { fontFamily: 'Red Hat Text', fontWeight: 500 as const, sizePx: 28, tracking: 1.4 },
    accentLabel:  { fontFamily: 'Red Hat Text', fontWeight: 500 as const, sizePx: 26,  tracking: 1.5 },
    storyName:    { fontFamily: 'Red Hat Text', fontWeight: 700 as const, sizePx: 40,  tracking: 0.5 },
    storyPrice:   { fontFamily: 'Red Hat Text', fontWeight: 400 as const, sizePx: 32 },
    storyBrand:   { fontFamily: 'Red Hat Text', fontWeight: 500 as const, sizePx: 26 },
  },

  // ── Canvas (IG story 9:16) ───────────────────────────────────
  canvas: { width: 1080, height: 1920 },

  // ── Spacing tokens for cover cards ────────────────────────────
  coverSpacing: {
    topPad: 320,                // ~1/6 of canvas height — wordmark top sits at y = h/6
    markToBody: 260,            // space between wordmark and body block
    bodyToFooter: 70,           // space between body and footer line
    bottomPad: 200,             // space at the bottom of canvas
    horizontalGutter: 195,      // ~18% padding each side → content sits in middle ~2/3 of width
    bodyParagraphGap: 12,       // tight space between body paragraphs (within block)
  },

  // ── Story (product photo) layout ──────────────────────────────
  storyLayout: {
    imageHeight: 1720,
    captionMarginLeft: 72,
    captionMarginBottom: 44,
    captionMaxWidth: 900,
  },

  // ── Per-flow defaults ─────────────────────────────────────────
  // What the cover defaults to when a flow first creates a draft.
  flowDefaults: {
    singleProduct: {
      includeCover: false,
    },
    productDrop: {
      includeCover: true,
      body: [
        'All of the following items are currently available for purchase in studio or through our website',
      ],
      footer: 'Complimentary shipping USA',
      ctaLabel: 'Shop our new drop',
      // Drop-wide URL used for the OPENING and CLOSING covers' IG link stickers.
      // Each product story's link sticker uses its own product URL instead.
      ctaUrl: 'https://paststudies.shop/collections/cloud-search-all-products',
      includeClosing: true,
      closingBody: ['All the new items are linked here'],
    },
    studioEvent: {
      includeCover: true,
      body: [
        'Studio open Saturday & Sunday, 12 – 4 PM',
        'Weekdays by appointment',
      ],
      footer: 'West Loop, Chicago',
      ctaLabel: 'Book an appointment here',
      ctaUrl: 'https://paststudies.shop/pages/visit-us',
    },
  },
} as const

export const wordmarkImagePath = path.join(BRAND_ASSETS_DIR, brand.wordmarkImageFile)
export type Brand = typeof brand
