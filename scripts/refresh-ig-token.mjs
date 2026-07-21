#!/usr/bin/env node
// Mint a NON-EXPIRING Instagram publishing token and install it into .env.
//
// Usage:
//   node scripts/refresh-ig-token.mjs <short-lived-user-token>
//
// Where the short-lived token comes from:
//   developers.facebook.com/tools/explorer → your app → User Token →
//   permissions: instagram_basic, instagram_content_publish,
//   pages_show_list, pages_read_engagement (+ business_management if your IG
//   sits under a Business Portfolio) → Generate Access Token.
//
// Chain this script runs:
//   short-lived user token
//     → long-lived user token         (fb_exchange_token, ~60 days)
//       → Page access token           (/me/accounts — does NOT expire)
//   The Page token is what publishing actually uses, and it stays valid until
//   you change your FB password or remove the app. Set once, forget.

import { readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const API = 'https://graph.facebook.com/v21.0'
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ENV_PATH = path.join(ROOT, '.env')
const SERVICE = 'com.paststudies.stories.review'

function die(msg) {
  console.error(`\n✗ ${msg}\n`)
  process.exit(1)
}

// ---- read .env ------------------------------------------------------------
const shortToken = process.argv[2]
if (!shortToken) die('Missing short-lived token.\n  Usage: node scripts/refresh-ig-token.mjs <short-lived-user-token>')

const envRaw = readFileSync(ENV_PATH, 'utf8')
const env = {}
for (const line of envRaw.split('\n')) {
  const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/)
  if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '').trim()
}
const APP_ID = env.IG_APP_ID
const APP_SECRET = env.IG_APP_SECRET
const IG_USER_ID = env.IG_USER_ID
if (!APP_ID || !APP_SECRET) die('IG_APP_ID / IG_APP_SECRET not found in .env')
if (!IG_USER_ID) die('IG_USER_ID not found in .env')

async function fbGet(pathPart, params) {
  const u = new URL(`${API}${pathPart}`)
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v)
  const r = await fetch(u.toString())
  const j = await r.json()
  if (!r.ok || j.error) die(`Graph API error on ${pathPart}: ${JSON.stringify(j.error ?? j)}`)
  return j
}

// ---- step 1: short-lived → long-lived user token --------------------------
console.log('→ Exchanging short-lived token for a long-lived user token…')
const longLived = await fbGet('/oauth/access_token', {
  grant_type: 'fb_exchange_token',
  client_id: APP_ID,
  client_secret: APP_SECRET,
  fb_exchange_token: shortToken,
})
const userToken = longLived.access_token
if (!userToken) die('No access_token in exchange response')
console.log(`  ok (expires in ~${Math.round((longLived.expires_in ?? 0) / 86400)} days)`)

// ---- step 2: user token → Page token for the IG-linked Page ---------------
console.log('→ Looking up the Page linked to your Instagram account…')
const accounts = await fbGet('/me/accounts', {
  fields: 'name,access_token,instagram_business_account',
  access_token: userToken,
})
const pages = accounts.data ?? []
if (!pages.length) die('No Facebook Pages returned. Make sure pages_show_list was granted.')

let match = pages.find((p) => p.instagram_business_account?.id === IG_USER_ID)
if (!match) {
  const listed = pages
    .map((p) => `    - ${p.name} → IG ${p.instagram_business_account?.id ?? '(none linked)'}`)
    .join('\n')
  die(
    `None of your Pages link to IG_USER_ID ${IG_USER_ID}.\n` +
      `  Pages visible with this token:\n${listed}\n` +
      `  Fix: either the token is missing a permission, or IG_USER_ID in .env is wrong.`,
  )
}
const pageToken = match.access_token
if (!pageToken) die(`Page "${match.name}" returned no access_token (permission issue).`)
console.log(`  ok — Page "${match.name}" → IG ${IG_USER_ID}`)

// ---- step 3: verify the Page token can actually reach publishing ----------
console.log('→ Verifying the token against your publishing-limit endpoint…')
const limit = await fbGet(`/${IG_USER_ID}/content_publishing_limit`, {
  fields: 'quota_usage,config',
  access_token: pageToken,
})
const d = limit.data?.[0] ?? {}
console.log(`  ok — quota used ${d.quota_usage ?? 0} / ${d.config?.quota_total ?? 50} in last 24h`)

// ---- step 4: back up .env, write new token --------------------------------
const stamp = new Date().toISOString().replace(/[:.]/g, '-')
const backup = `${ENV_PATH}.bak-${stamp}`
copyFileSync(ENV_PATH, backup)
const updated = envRaw.match(/^\s*IG_ACCESS_TOKEN\s*=/m)
  ? envRaw.replace(/^\s*IG_ACCESS_TOKEN\s*=.*$/m, `IG_ACCESS_TOKEN=${pageToken}`)
  : `${envRaw.replace(/\n?$/, '\n')}IG_ACCESS_TOKEN=${pageToken}\n`
writeFileSync(ENV_PATH, updated)
console.log(`→ Wrote new IG_ACCESS_TOKEN to .env (backup: ${path.basename(backup)})`)

// ---- step 5: restart the service so it picks up the new token -------------
console.log('→ Restarting the stories service…')
try {
  const uid = execSync('id -u').toString().trim()
  execSync(`launchctl kickstart -k "gui/${uid}/${SERVICE}"`, { stdio: 'inherit' })
  console.log('  ok')
} catch (e) {
  console.log(`  ⚠ restart failed — do it manually:\n    launchctl kickstart -k "gui/$(id -u)/${SERVICE}"`)
}

console.log('\n✓ Done. This Page token does not expire — no 60-day refresh needed.\n')
