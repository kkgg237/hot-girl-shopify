#!/usr/bin/env node
// Inspect the IG_ACCESS_TOKEN currently in .env: which app owns it, what type
// it is (USER vs PAGE), whether it's expired, and what scopes it carries.
// Reads the token from .env so it never appears in argv / shell history.

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
const token = env.IG_ACCESS_TOKEN
if (!token) {
  console.error('No IG_ACCESS_TOKEN in .env')
  process.exit(1)
}

const u = new URL(`${API}/debug_token`)
u.searchParams.set('input_token', token)
u.searchParams.set('access_token', token) // self-debug
const r = await fetch(u.toString())
const j = await r.json()
if (j.error) {
  console.error(`debug_token error: ${JSON.stringify(j.error)}`)
  process.exit(1)
}
const d = j.data ?? {}
const exp = d.expires_at ? new Date(d.expires_at * 1000).toISOString() : d.expires_at
const dataExp = d.data_access_expires_at ? new Date(d.data_access_expires_at * 1000).toISOString() : undefined
console.log('Token belongs to:')
console.log(`  app_id:   ${d.app_id}`)
console.log(`  app name: ${d.application}`)
console.log(`  type:     ${d.type}`)
console.log(`  valid:    ${d.is_valid}`)
console.log(`  expires:  ${d.expires_at === 0 ? 'never' : exp}`)
if (dataExp) console.log(`  data-access expires: ${dataExp}`)
console.log(`  scopes:   ${(d.scopes ?? []).join(', ')}`)
console.log(`\n.env currently says IG_APP_ID=${env.IG_APP_ID}`)
console.log(d.app_id === env.IG_APP_ID ? '  → matches .env ✓' : '  → does NOT match .env ✗  (token is from a different app)')
