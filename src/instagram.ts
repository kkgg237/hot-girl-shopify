const { IG_ACCESS_TOKEN, IG_USER_ID } = process.env

const API = 'https://graph.facebook.com/v21.0'

function assertIg(): asserts IG_ACCESS_TOKEN is string {
  if (!IG_ACCESS_TOKEN || !IG_USER_ID) {
    throw new Error('Missing IG_ACCESS_TOKEN or IG_USER_ID')
  }
}

async function fbGet<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  assertIg()
  const u = new URL(`${API}${path}`)
  u.searchParams.set('access_token', IG_ACCESS_TOKEN!)
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v)
  const r = await fetch(u.toString())
  const j = await r.json()
  if (!r.ok || j.error) throw new Error(`FB GET ${path}: ${JSON.stringify(j.error ?? j)}`)
  return j as T
}

async function fbPost<T>(path: string, body: Record<string, string>): Promise<T> {
  assertIg()
  const form = new URLSearchParams({ ...body, access_token: IG_ACCESS_TOKEN! })
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
  })
  const j = await r.json()
  if (!r.ok || j.error) throw new Error(`FB POST ${path}: ${JSON.stringify(j.error ?? j)}`)
  return j as T
}

interface ContainerStatus {
  status_code: 'IN_PROGRESS' | 'FINISHED' | 'ERROR' | 'EXPIRED' | 'PUBLISHED'
}

export async function createStoryContainer(imageUrl: string, link?: string): Promise<string> {
  assertIg()
  const body: Record<string, string> = {
    image_url: imageUrl,
    media_type: 'STORIES',
  }
  if (link) body.link = link
  const { id } = await fbPost<{ id: string }>(`/${IG_USER_ID}/media`, body)
  return id
}

async function waitForContainer(containerId: string, timeoutMs = 60_000, intervalMs = 1_500): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const { status_code } = await fbGet<ContainerStatus>(`/${containerId}`, { fields: 'status_code' })
    if (status_code === 'FINISHED') return
    if (status_code === 'ERROR' || status_code === 'EXPIRED') {
      throw new Error(`Container ${containerId} failed: ${status_code}`)
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(`Container ${containerId} timed out waiting for FINISHED`)
}

export async function publishContainer(containerId: string): Promise<string> {
  assertIg()
  const { id } = await fbPost<{ id: string }>(`/${IG_USER_ID}/media_publish`, {
    creation_id: containerId,
  })
  return id
}

export async function postStoryFromUrl(
  imageUrl: string,
  link?: string,
): Promise<{ containerId: string; mediaId: string }> {
  const containerId = await createStoryContainer(imageUrl, link)
  await waitForContainer(containerId)
  const mediaId = await publishContainer(containerId)
  return { containerId, mediaId }
}

export async function getPublishingLimit(): Promise<{ quota_usage: number; quota_total: number }> {
  assertIg()
  const r = await fbGet<{ data: Array<{ quota_usage: number; config?: { quota_total: number } }> }>(
    `/${IG_USER_ID}/content_publishing_limit`,
    { fields: 'quota_usage,config' },
  )
  const d = r.data[0]
  return { quota_usage: d.quota_usage, quota_total: d.config?.quota_total ?? 50 }
}
