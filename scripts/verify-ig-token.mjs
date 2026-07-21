#!/usr/bin/env node
// End-to-end check that the IG_ACCESS_TOKEN in .env can actually reach the
// Instagram publishing API for IG_USER_ID. Mirrors getPublishingLimit().
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const API = 'https://graph.facebook.com/v21.0'
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const envRaw = readFileSync(path.join(ROOT, '.env'), 'utf8')
const env = {}
for (const line of envRaw.split('\n')) {
  const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/)
  if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '').trim()
}
const u = new URL(`${API}/${env.IG_USER_ID}/content_publishing_limit`)
u.searchParams.set('fields', 'quota_usage,config')
u.searchParams.set('access_token', env.IG_ACCESS_TOKEN)
const r = await fetch(u.toString())
const j = await r.json()
if (j.error) {
  console.error(`✗ publishing check FAILED: ${JSON.stringify(j.error)}`)
  process.exit(1)
}
const d = j.data?.[0] ?? {}
console.log(`✓ token can publish — quota ${d.quota_usage ?? 0} / ${d.config?.quota_total ?? 50} used in last 24h`)
