import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDb, listUsable } from './db.js'
import { getPublishingLimit } from './instagram.js'
import { publishHandle, renderForHandle } from './publish.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(__dirname, '..')

function cli() {
  const args = process.argv.slice(2)
  const cmd = args[0]
  const db = openDb()

  if (!cmd || cmd === '--help' || cmd === '-h') {
    console.log('Usage:')
    console.log('  npm run post -- list                 # list usable products')
    console.log('  npm run post -- post <handle>        # render IG story sequence for one product')
    console.log('  npm run post -- post --all           # render sequences for all usable products')
    console.log('  npm run post -- publish <handle>     # render + upload + post whole sequence to IG')
    console.log('  npm run post -- limit                # show IG content-publishing quota')
    console.log('  npm run review                       # start approval UI at localhost:3001')
    process.exit(0)
  }

  if (cmd === 'limit') {
    ;(async () => {
      const { quota_usage, quota_total } = await getPublishingLimit()
      console.log(`IG quota: ${quota_usage} / ${quota_total} used in last 24h`)
    })()
    return
  }

  if (cmd === 'publish') {
    const handle = args[1]
    if (!handle) {
      console.error('Missing <handle>')
      process.exit(1)
    }
    ;(async () => {
      const { post } = await publishHandle(handle)
      const ids = post.ig_media_ids ? (JSON.parse(post.ig_media_ids) as string[]) : [post.ig_media_id]
      console.log(`✓ Posted ${handle} — ${ids.length} stor${ids.length === 1 ? 'y' : 'ies'}: ${ids.join(', ')}`)
    })()
    return
  }

  if (cmd === 'list') {
    const products = listUsable(db, 1, 200)
    for (const p of products) {
      console.log(`${p.handle}\t${p.title}`)
    }
    console.log(`\n${products.length} products with ≥1 image`)
    return
  }

  if (cmd === 'post') {
    const target = args[1]
    if (!target) {
      console.error('Missing <handle> or --all')
      process.exit(1)
    }

    const handles = target === '--all'
      ? listUsable(db, 1, 500).map((p) => p.handle)
      : [target]

    ;(async () => {
      for (const handle of handles) {
        try {
          const outPaths = await renderForHandle(handle)
          for (const p of outPaths) {
            console.log(`✓ ${path.relative(ROOT, p)}`)
          }
        } catch (e) {
          console.error(`Skipping ${handle}: ${e instanceof Error ? e.message : String(e)}`)
        }
      }
    })()
    return
  }

  console.error(`Unknown command: ${cmd}`)
  process.exit(1)
}

cli()
