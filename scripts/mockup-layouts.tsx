/**
 * V5 layout stress test — same template (full-bleed photo + gradient
 * scrim fade-up + centered white text) across a diverse set of products.
 *
 * Run: npx tsx scripts/mockup-layouts.tsx
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import satori from 'satori'
import { Resvg } from '@resvg/resvg-js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')
const OUT_DIR = path.join(ROOT, 'out', 'mockups')
mkdirSync(OUT_DIR, { recursive: true })

const ITEMS = [
  {
    slug: 'v5-issey-velvet-skirt',
    brand: 'ISSEY MIYAKE',
    name: 'Black Velvet Pleated Skirt',
    price: '$495',
    image: '.cache/media/issey-miyake-black-velvet-pleated-skirt/00_PS-Clothes3952.jpg',
  },
  {
    slug: 'v5-pleats-red-fringe',
    brand: 'PLEATS PLEASE',
    name: 'Red Fringe Top',
    price: '$475',
    image: '.cache/media/pleats-please-red-fringe-top/00_PS-Clothes3862.jpg',
  },
  {
    slug: 'v5-pleats-paisley',
    brand: 'PLEATS PLEASE',
    name: 'Paisley Print Top',
    price: '$475',
    image: '.cache/media/pleats-please-paisley-print-top/00_PS-Clothes4051.jpg',
  },
  {
    slug: 'v5-pleats-multicolor-dress',
    brand: 'PLEATS PLEASE',
    name: 'Multicolor Mini Dress',
    price: '$595',
    image: '.cache/media/pleats-please-multicolor-mini-dress/00_PS-Clothes3709.jpg',
  },
  {
    slug: 'v5-issey-cyberdot-dress',
    brand: 'ISSEY MIYAKE',
    name: '1996 Black/White Cyberdot Dress',
    price: '$550',
    image: '.cache/media/1996-issey-miyake-black-white-cyberdot-dress/00_PS-Clothes3635_b0740654-b0b9-4abf-9a3a-c107bd3e137e.jpg',
  },
  {
    slug: 'v5-issey-3d-sheer',
    brand: 'ISSEY MIYAKE',
    name: '3-D Semi-Sheer Long Sleeve',
    price: '$675',
    image: '.cache/media/issey-miyake-3-d-semi-sheer-long-sleeve/00_PS-Clothes3920_9d87b3e5-8691-4a17-a08f-2109a8902e43.jpg',
  },
  {
    slug: 'v5-pleats-nude-tank',
    brand: 'PLEATS PLEASE',
    name: 'Nude Tank Top',
    price: '$450',
    image: '.cache/media/pleats-please-nude-tank-top/00_PS-Clothes3848.jpg',
  },
]

const RHT = path.join(ROOT, 'node_modules/@fontsource/red-hat-text/files')
const NEWS = path.join(ROOT, 'node_modules/@fontsource/newsreader/files')
const fonts = [
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT, 'red-hat-text-latin-400-normal.woff')), weight: 400 as const, style: 'normal' as const },
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT, 'red-hat-text-latin-500-normal.woff')), weight: 500 as const, style: 'normal' as const },
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT, 'red-hat-text-latin-700-normal.woff')), weight: 700 as const, style: 'normal' as const },
  { name: 'Newsreader', data: readFileSync(path.join(NEWS, 'newsreader-latin-400-normal.woff')), weight: 400 as const, style: 'normal' as const },
  { name: 'Newsreader', data: readFileSync(path.join(NEWS, 'newsreader-latin-700-normal.woff')), weight: 700 as const, style: 'normal' as const },
]

function imgUri(rel: string): string {
  const buf = readFileSync(path.resolve(ROOT, rel))
  const ext = rel.split('.').pop()?.toLowerCase()
  const mime = ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg'
  return `data:${mime};base64,${buf.toString('base64')}`
}

const CANVAS = { w: 1080, h: 1920 }

// Brand-only mockup variants — drop the product name entirely.
// All three variants are pure overlay (no scrim) with text centered on canvas.

type Item = { brand: string; name: string; price: string; image: string }

function withImage(children: any[], photo: string) {
  return {
    type: 'div',
    props: {
      style: { width: CANVAS.w, height: CANVAS.h, display: 'flex', position: 'relative' },
      children: [
        {
          type: 'img',
          props: {
            src: photo,
            style: { width: CANVAS.w, height: CANVAS.h, objectFit: 'cover', display: 'flex', position: 'absolute', top: 0, left: 0 },
          },
        },
        ...children,
      ],
    },
  }
}

// A — stacked: brand caps on top, price below; tight, centered
function variantA(item: Item) {
  return withImage(
    [
      {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flexDirection: 'column',
            position: 'absolute',
            left: 0,
            right: 0,
            top: 920,
            alignItems: 'center',
            color: '#fff',
            textAlign: 'center',
          },
          children: [
            {
              type: 'span',
              props: {
                style: { fontFamily: 'Red Hat Text', fontWeight: 700, fontSize: 54, lineHeight: 1.0, letterSpacing: 4, textTransform: 'uppercase' },
                children: item.brand,
              },
            },
            {
              type: 'span',
              props: {
                style: { fontFamily: 'Red Hat Text', fontWeight: 400, fontSize: 38, lineHeight: 1.0, letterSpacing: 1, marginTop: 14 },
                children: item.price,
              },
            },
          ],
        },
      },
    ],
    imgUri(item.image),
  )
}

// B — single line: BRAND · $PRICE, all caps, tracked, centered
function variantB(item: Item) {
  return withImage(
    [
      {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'center',
            gap: 26,
            position: 'absolute',
            left: 0,
            right: 0,
            top: 940,
            justifyContent: 'center',
            color: '#fff',
            fontFamily: 'Red Hat Text',
            fontWeight: 500,
            fontSize: 38,
            textTransform: 'uppercase',
            letterSpacing: 4,
            whiteSpace: 'nowrap',
          },
          children: [
            { type: 'span', props: { style: { fontWeight: 700 }, children: item.brand } },
            { type: 'span', props: { style: { opacity: 0.55 }, children: '·' } },
            { type: 'span', props: { style: { fontWeight: 500 }, children: item.price } },
          ],
        },
      },
    ],
    imgUri(item.image),
  )
}

// C — price-led: brand small monogram top, price huge below
function variantC(item: Item) {
  return withImage(
    [
      {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flexDirection: 'column',
            position: 'absolute',
            left: 0,
            right: 0,
            top: 880,
            alignItems: 'center',
            color: '#fff',
            textAlign: 'center',
          },
          children: [
            {
              type: 'span',
              props: {
                style: { fontFamily: 'Red Hat Text', fontWeight: 500, fontSize: 28, lineHeight: 1.0, letterSpacing: 6, textTransform: 'uppercase' },
                children: item.brand,
              },
            },
            {
              type: 'span',
              props: {
                style: { fontFamily: 'Red Hat Text', fontWeight: 700, fontSize: 96, lineHeight: 1.0, letterSpacing: -1, marginTop: 18 },
                children: item.price,
              },
            },
          ],
        },
      },
    ],
    imgUri(item.image),
  )
}

// D — black text, left-justified, positioned to the left of the model.
// Brand on line 1, price on line 2, tight letter-spacing.
function variantD(item: Item) {
  return withImage(
    [
      {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flexDirection: 'column',
            position: 'absolute',
            left: 70,
            top: 900,
            alignItems: 'flex-start',
            color: '#1a1614',
            textAlign: 'left',
          },
          children: [
            {
              type: 'span',
              props: {
                style: {
                  fontFamily: 'Red Hat Text',
                  fontWeight: 700,
                  fontSize: 44,
                  lineHeight: 1.0,
                  letterSpacing: 0.5,
                  textTransform: 'uppercase',
                },
                children: item.brand,
              },
            },
            {
              type: 'span',
              props: {
                style: {
                  fontFamily: 'Red Hat Text',
                  fontWeight: 400,
                  fontSize: 34,
                  lineHeight: 1.0,
                  letterSpacing: 0.5,
                  marginTop: 12,
                },
                children: item.price,
              },
            },
          ],
        },
      },
    ],
    imgUri(item.image),
  )
}

// F — E re-mixed: black text instead of white, dollar sign stripped,
// sizes bumped up a smidge. Left-justified, tight, still no scrim.
function variantF(item: Item) {
  const priceNoSign = item.price.replace(/^\$/, '')
  return withImage(
    [
      {
        type: 'div',
        props: {
          style: {
            display: 'flex',
            flexDirection: 'column',
            position: 'absolute',
            left: 70,
            top: 920,
            alignItems: 'flex-start',
            color: '#1a1614',
            textAlign: 'left',
          },
          children: [
            {
              type: 'span',
              props: {
                style: {
                  fontFamily: 'Red Hat Text',
                  fontWeight: 700,
                  fontSize: 36,
                  lineHeight: 1.0,
                  letterSpacing: 0,
                  textTransform: 'uppercase',
                },
                children: item.brand,
              },
            },
            {
              type: 'span',
              props: {
                style: {
                  fontFamily: 'Red Hat Text',
                  fontWeight: 400,
                  fontSize: 28,
                  lineHeight: 1.0,
                  letterSpacing: 0,
                  marginTop: 8,
                },
                children: priceNoSign,
              },
            },
          ],
        },
      },
    ],
    imgUri(item.image),
  )
}

const LAYOUTS: Array<{ suffix: string; fn: (it: Item) => any }> = [
  { suffix: 'F-left-black-tight', fn: variantF },
]


let count = 0
for (const item of ITEMS) {
  for (const layout of LAYOUTS) {
    const svg = await satori(layout.fn(item), { width: CANVAS.w, height: CANVAS.h, fonts })
    const png = new Resvg(svg, { fitTo: { mode: 'width', value: CANVAS.w } }).render().asPng()
    const out = path.join(OUT_DIR, `${item.slug}-${layout.suffix}.png`)
    writeFileSync(out, png)
    count++
    console.log(`✓ ${layout.suffix.padEnd(12)} · ${item.brand} — ${item.name}`)
  }
}
console.log(`\nDone. ${count} mockups in out/mockups/`)

