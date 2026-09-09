// The one place the backend's address lives.
//
// The backend is a local sidecar with a fixed default origin — there is no server URL to
// configure, and the bearer token is fetched from /bootstrap on first use (that endpoint is
// unauthenticated by design; see api/routers/app.py). Everything model- or decision-shaped
// happens behind those routes; the extension holds no LLM keys and runs no prompts.

import type { BackendBlobResponse, BackendJsonResponse, ExtensionRelay } from './messages'

export const CANDIDATE_BACKEND_URLS = ['http://127.0.0.1:8080', 'http://127.0.0.1:8000']

export let BACKEND_URL = 'http://127.0.0.1:8080'

let cachedToken = ''

async function bootstrapToken(): Promise<string> {
  if (cachedToken) return cachedToken
  const candidates = [BACKEND_URL, ...CANDIDATE_BACKEND_URLS.filter((u) => u !== BACKEND_URL)]
  let lastError: unknown = null

  for (const url of candidates) {
    try {
      const resp = await fetch(`${url}/bootstrap`, { signal: AbortSignal.timeout(1500) })
      if (!resp.ok) continue
      const body = (await resp.json()) as { token?: string }
      BACKEND_URL = url
      cachedToken = body.token ?? ''
      return cachedToken
    } catch (err) {
      lastError = err
    }
  }

  throw new Error(`backend unreachable (${lastError instanceof Error ? lastError.message : 'no response'})`)
}

/** Direct authenticated call — side panel and background worker only (extension contexts
 *  are not bound by a page's CSP). Content scripts must go through the worker relay. */
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const send = async (token: string) =>
    fetch(BACKEND_URL + path, {
      ...init,
      headers: { 'content-type': 'application/json', Authorization: `Bearer ${token}`, ...(init?.headers ?? {}) },
    })
  let resp: Response
  try {
    resp = await send(await bootstrapToken())
  } catch {
    // If first attempt failed (e.g. backend restarted on another port), clear cached token and retry
    cachedToken = ''
    resp = await send(await bootstrapToken())
  }
  // Tokens are random per process: re-bootstrap once after a backend restart.
  if (resp.status === 401) {
    cachedToken = ''
    resp = await send(await bootstrapToken())
  }
  return resp
}

export class BackendError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function backendJson<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await backendFetch(path, init)
  if (!resp.ok) throw new BackendError(resp.status, (await resp.text().catch(() => '')) || resp.statusText)
  return (await resp.json()) as T
}

// --- content-script side: relay through the worker ---------------------------
// A career page's connect-src would block (or CORS-refuse) a fetch to 127.0.0.1 from the page
// context, so content scripts ask the worker to make the call. Paths only ever reach BACKEND_URL.

async function relay(message: ExtensionRelay): Promise<BackendJsonResponse | BackendBlobResponse> {
  const resp = (await chrome.runtime.sendMessage(message)) as BackendJsonResponse | BackendBlobResponse | undefined
  if (!resp) throw new BackendError(0, 'backend relay: no response from the worker')
  return resp
}

export async function pageBackendJson(path: string, body?: unknown): Promise<unknown> {
  const resp = (await relay({
    type: 'BACKEND_JSON',
    path,
    init: body === undefined ? undefined : { method: 'POST', body: JSON.stringify(body) },
  })) as BackendJsonResponse
  if (!resp.ok) throw new BackendError(resp.status, resp.body || 'backend request failed')
  return JSON.parse(resp.body || 'null')
}

export async function pageBackendBlob(path: string): Promise<Blob | null> {
  const resp = (await relay({ type: 'BACKEND_BLOB', path })) as BackendBlobResponse
  if (!resp.ok || !resp.base64) return null
  const binary = atob(resp.base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes])
}
