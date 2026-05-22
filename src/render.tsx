import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import satori from 'satori'
import { Resvg } from '@resvg/resvg-js'
import { Story, CoverCard, type StoryProps, type Layout, type CoverCardProps } from './template.js'
import { brand, wordmarkImagePath } from './brand.js'
import { existsSync } from 'node:fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

const RHT_DIR = path.join(ROOT, 'node_modules/@fontsource/red-hat-text/files')
const NEWS_DIR = path.join(ROOT, 'node_modules/@fontsource/newsreader/files')

const fonts = [
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT_DIR, 'red-hat-text-latin-400-normal.woff')), weight: 400 as const, style: 'normal' as const },
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT_DIR, 'red-hat-text-latin-500-normal.woff')), weight: 500 as const, style: 'normal' as const },
  { name: 'Red Hat Text', data: readFileSync(path.join(RHT_DIR, 'red-hat-text-latin-700-normal.woff')), weight: 700 as const, style: 'normal' as const },
  { name: 'Newsreader', data: readFileSync(path.join(NEWS_DIR, 'newsreader-latin-400-normal.woff')), weight: 400 as const, style: 'normal' as const },
  { name: 'Newsreader', data: readFileSync(path.join(NEWS_DIR, 'newsreader-latin-600-normal.woff')), weight: 600 as const, style: 'normal' as const },
  { name: 'Newsreader', data: readFileSync(path.join(NEWS_DIR, 'newsreader-latin-700-normal.woff')), weight: 700 as const, style: 'normal' as const },
]

function imageToDataUri(relPath: string): string {
  const abs = path.resolve(ROOT, relPath)
  const buf = readFileSync(abs)
  const ext = (relPath.split('.').pop() || 'jpg').toLowerCase()
  const mime =
    ext === 'png' ? 'image/png' :
    ext === 'webp' ? 'image/webp' :
    'image/jpeg'
  return `data:${mime};base64,${buf.toString('base64')}`
}

export async function renderStoryPng(
  props: Omit<StoryProps, 'images'> & { imagePaths: string[]; layout?: Layout },
): Promise<Buffer> {
  const layout = props.layout ?? '4up'
  const needed = layout === '1up' ? 1 : layout === '2up' ? 2 : 4
  const images = props.imagePaths.slice(0, needed).map(imageToDataUri)
  const element = Story({ images, brand: props.brand, name: props.name, price: props.price, layout })

  const svg = await satori(element, {
    width: 1080,
    height: 1920,
    fonts,
  })

  const png = new Resvg(svg, {
    fitTo: { mode: 'width', value: 1080 },
  }).render().asPng()

  return png
}

const wordmarkDataUri = (() => {
  if (!existsSync(wordmarkImagePath)) return undefined
  const buf = readFileSync(wordmarkImagePath)
  const ext = wordmarkImagePath.split('.').pop()?.toLowerCase()
  const mime = ext === 'svg' ? 'image/svg+xml' : ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png'
  return `data:${mime};base64,${buf.toString('base64')}`
})()

export async function renderCoverPng(props: CoverCardProps): Promise<Buffer> {
  const element = CoverCard({ wordmarkDataUri, ...props })
  const svg = await satori(element, {
    width: brand.canvas.width,
    height: brand.canvas.height,
    fonts,
  })
  return new Resvg(svg, {
    fitTo: { mode: 'width', value: brand.canvas.width },
  }).render().asPng()
}
