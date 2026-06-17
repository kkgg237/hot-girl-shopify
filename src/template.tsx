import React from 'react'
import { brand } from './brand.js'
import { DEFAULTS as LOOK_DEFAULTS, type LookSettings } from './look-settings.js'

export type Layout = '1up' | '2up' | '4up'

export interface CoverCardProps {
  wordmarkDataUri?: string // data: URI for the wordmark image; if absent, falls back to text wordmark
  body?: string[]
  footer?: string
  trailingArrow?: boolean // small thin arrow under the body, hints at the IG link sticker
}

export function CoverCard({ wordmarkDataUri, body = [], footer, trailingArrow }: CoverCardProps) {
  const t = brand.type
  const c = brand.colors
  const s = brand.coverSpacing

  return (
    <div
      style={{
        width: brand.canvas.width,
        height: brand.canvas.height,
        backgroundColor: c.cream,
        color: c.text,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        fontFamily: t.body.fontFamily,
        paddingLeft: s.horizontalGutter,
        paddingRight: s.horizontalGutter,
      }}
    >
      <div style={{ display: 'flex', height: s.topPad }} />

      {wordmarkDataUri ? (
        <img
          src={wordmarkDataUri}
          style={{
            width: brand.wordmarkImageWidth,
            height: brand.wordmarkImageHeight,
            objectFit: 'contain',
            display: 'flex',
          }}
        />
      ) : (
        <span
          style={{
            fontFamily: t.wordmark.fontFamily,
            fontWeight: t.wordmark.fontWeight,
            fontSize: fitFontSize(brand.wordmark, brand.canvas.width - s.horizontalGutter * 2, t.wordmark.sizePx, 80, t.wordmark.fontWeight),
            letterSpacing: t.wordmark.tracking,
            lineHeight: 1.0,
          }}
        >
          {brand.wordmark}
        </span>
      )}

      <div style={{ display: 'flex', height: s.markToBody }} />

      {body.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            maxWidth: brand.canvas.width - s.horizontalGutter * 2,
          }}
        >
          {body.map((line, i) => (
            <span
              key={i}
              style={{
                fontFamily: t.body.fontFamily,
                fontSize: t.body.sizePx,
                fontWeight: t.body.fontWeight,
                lineHeight: t.body.lineHeight,
                letterSpacing: t.body.tracking,
                textTransform: 'uppercase',
                textAlign: 'center',
                marginTop: i === 0 ? 0 : s.bodyParagraphGap,
              }}
            >
              {line}
            </span>
          ))}
        </div>
      )}

      {trailingArrow && (
        <div style={{ display: 'flex', marginTop: 32, alignItems: 'center', justifyContent: 'center' }}>
          <svg width="100" height="16" viewBox="0 0 100 16" xmlns="http://www.w3.org/2000/svg">
            <path d="M2 8 L94 8 M84 2 L94 8 L84 14" stroke={c.text} strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {footer && (
        <span
          style={{
            fontFamily: t.footer.fontFamily,
            fontSize: t.footer.sizePx,
            fontWeight: t.footer.fontWeight,
            letterSpacing: t.footer.tracking,
            textTransform: 'uppercase',
            marginTop: s.bodyToFooter,
            lineHeight: 1.0,
            textAlign: 'center',
          }}
        >
          {footer}
        </span>
      )}

      <div style={{ display: 'flex', flex: 1 }} />
      <div style={{ display: 'flex', height: s.bottomPad }} />
    </div>
  )
}

export interface StoryProps {
  images: string[] // data URIs; takes 1, 2, or 4 depending on layout
  brand: string
  name: string
  price: string
  layout?: Layout
  look?: LookSettings
}

const CANVAS = { width: 1080, height: 1920 } as const
const CAPTION_MAX_WIDTH = 900
const LETTER_SPACING = 0

function estimatePxWidth(text: string, fontSize: number, weight: 400 | 700): number {
  const charRatio = weight === 700 ? 0.60 : 0.56
  return text.length * (fontSize * charRatio + LETTER_SPACING)
}

function fitFontSize(text: string, maxWidth: number, maxSize: number, minSize: number, weight: 400 | 700): number {
  for (let size = maxSize; size >= minSize; size -= 1) {
    if (estimatePxWidth(text, size, weight) <= maxWidth) return size
  }
  return minSize
}

function ImageGrid({ images, layout }: { images: string[]; layout: Layout }) {
  if (layout === '1up') {
    // Full-bleed: the image fills the entire canvas. The caption floats over
    // it via absolute positioning in Story(), so no reserved strip is needed.
    return (
      <img
        src={images[0]}
        style={{
          width: 1080,
          height: 1920,
          objectFit: 'cover',
          display: 'flex',
          alignSelf: 'center',
        }}
      />
    )
  }
  if (layout === '2up') {
    // Two cells side by side, each 540 x 810 = strip 1080 x 810, vertically centered above caption.
    const cellStyle = {
      width: 540,
      height: 810,
      objectFit: 'cover' as const,
      display: 'flex',
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignSelf: 'center', marginTop: 405 }}>
        <div style={{ display: 'flex' }}>
          <img src={images[0]} style={cellStyle} />
          <img src={images[1]} style={cellStyle} />
        </div>
      </div>
    )
  }
  // 4up — 2x2 of 540x810 = 1080 x 1620, flush to top.
  const cellStyle = {
    width: 540,
    height: 810,
    objectFit: 'cover' as const,
    display: 'flex',
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignSelf: 'center' }}>
      <div style={{ display: 'flex' }}>
        <img src={images[0]} style={cellStyle} />
        <img src={images[1]} style={cellStyle} />
      </div>
      <div style={{ display: 'flex' }}>
        <img src={images[2]} style={cellStyle} />
        <img src={images[3]} style={cellStyle} />
      </div>
    </div>
  )
}

export function Story({ images, brand: brandLabel, name, price, layout = '4up', look }: StoryProps) {
  const needed = layout === '1up' ? 1 : layout === '2up' ? 2 : 4
  const cells = images.slice(0, needed)
  const c = brand.colors

  const L: LookSettings = look ?? LOOK_DEFAULTS
  const priceStr = L.showDollar ? (price.startsWith('$') ? price : `$${price}`) : price.replace(/^\$/, '')

  // Percent → pixel for satori. xPct is "distance from the active edge"
  // (left edge when align=left, right edge when align=right), yPct is
  // "distance from the top". align=center ignores xPct.
  const xPx = Math.round((L.xPct / 100) * CANVAS.width)
  const yPx = Math.round((L.yPct / 100) * CANVAS.height)
  const captionPosition: React.CSSProperties =
    L.align === 'center'
      ? { left: 0, right: 0, top: yPx, alignItems: 'center', textAlign: 'center' }
      : L.align === 'right'
      ? { right: xPx, top: yPx, alignItems: 'flex-end', textAlign: 'right' }
      : { left: xPx, top: yPx, alignItems: 'flex-start', textAlign: 'left' }

  // Fixed sensible gap between caption lines; was a control before, but it
  // was rarely worth tuning compared to font sizes themselves.
  const LINE_GAP = 12

  return (
    <div
      style={{
        width: CANVAS.width,
        height: CANVAS.height,
        backgroundColor: c.storyBg,
        display: 'flex',
        position: 'relative',
      }}
    >
      <div style={{ position: 'absolute', top: 0, left: 0, width: CANVAS.width, height: CANVAS.height, display: 'flex', flexDirection: 'column' }}>
        <ImageGrid images={cells} layout={layout} />
      </div>

      <div
        style={{
          position: 'absolute',
          display: 'flex',
          flexDirection: 'column',
          color: L.color,
          ...captionPosition,
        }}
      >
        {L.showBrand && brandLabel && (
          <span
            style={{
              fontFamily: 'Red Hat Text',
              fontWeight: 700,
              fontSize: L.brandSize,
              lineHeight: 1.0,
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
            }}
          >
            {brandLabel}
          </span>
        )}
        {L.showName && name && (
          <span
            style={{
              fontFamily: 'Red Hat Text',
              fontWeight: 700,
              fontSize: L.nameSize,
              lineHeight: 1.1,
              marginTop: L.showBrand ? LINE_GAP : 0,
              textTransform: 'uppercase',
              maxWidth: 920,
            }}
          >
            {name}
          </span>
        )}
        {L.showPrice && price && (
          <span
            style={{
              fontFamily: 'Red Hat Text',
              fontWeight: 400,
              fontSize: L.priceSize,
              lineHeight: 1.0,
              marginTop: L.showBrand || L.showName ? LINE_GAP : 0,
              whiteSpace: 'nowrap',
            }}
          >
            {priceStr}
          </span>
        )}
      </div>
    </div>
  )
}
