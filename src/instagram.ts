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

// IG returns 9007 / subcode 2207027 ("Media ID is not available … not ready
// for publishing") when media_publish is called before the backend has finished
// processing the container — even though its status already read FINISHED. It's
// flagged is_transient:false but is in practice retryable; waiting a moment and
// re-posting clears it.
function isMediaNotReady(e: unknown): boolean {
  const m = e instanceof Error ? e.message : String(e)
  return (
    m.includes('2207027') ||
    m.includes('"code":9007') ||
    /not ready for publishing|Media ID is not available/i.test(m)
  )
}

// IG returns 24 / subcode 2207006 ("Media Not Found … cannot be found") when the
// container has become invalid by publish time. Unlike "not ready", re-posting
// the same container won't help — it has to be rebuilt. Neither this nor
// "not ready" means anything was published, so recreating can't double-post.
function isMediaNotFound(e: unknown): boolean {
  const m = e instanceof Error ? e.message : String(e)
  return m.includes('2207006') || /Media Not Found|cannot be found/i.test(m)
}

// Retryable in place (same container): a short backoff usually clears a
// still-processing container.
function isRetryablePublishError(e: unknown): boolean {
  return isMediaNotReady(e) || isMediaNotFound(e)
}

export async function publishContainer(containerId: string): Promise<string> {
  assertIg()
  const backoffs = [2_000, 3_000, 5_000, 8_000, 13_000]
  let lastErr: unknown
  for (let attempt = 0; attempt <= backoffs.length; attempt++) {
    try {
      const { id } = await fbPost<{ id: string }>(`/${IG_USER_ID}/media_publish`, {
        creation_id: containerId,
      })
      return id
    } catch (e) {
      lastErr = e
      if (!isRetryablePublishError(e) || attempt === backoffs.length) throw e
      const wait = backoffs[attempt]
      console.warn(`[ig] publish not ready (attempt ${attempt + 1}), retrying in ${wait / 1000}s…`)
      await new Promise((r) => setTimeout(r, wait))
    }
  }
  throw lastErr
}

export async function postStoryFromUrl(
  imageUrl: string,
  link?: string,
): Promise<{ containerId: string; mediaId: string }> {
  // Outer loop rebuilds the container if in-place publish retries are exhausted
  // (e.g. a container that went permanently "Media Not Found").
  const maxContainerAttempts = 3
  let lastErr: unknown
  for (let attempt = 1; attempt <= maxContainerAttempts; attempt++) {
    const containerId = await createStoryContainer(imageUrl, link)
    try {
      await waitForContainer(containerId)
      // Small settle before the first publish — the container status can flip to
      // FINISHED a beat before the backend will actually accept a publish.
      await new Promise((r) => setTimeout(r, 1_500))
      const mediaId = await publishContainer(containerId)
      return { containerId, mediaId }
    } catch (e) {
      lastErr = e
      if (isRetryablePublishError(e) && attempt < maxContainerAttempts) {
        console.warn(
          `[ig] container ${containerId} unpublishable (attempt ${attempt}/${maxContainerAttempts}) — rebuilding. ${e instanceof Error ? e.message : e}`,
        )
        await new Promise((r) => setTimeout(r, 4_000))
        continue
      }
      throw e
    }
  }
  throw lastErr
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
