// Typed backend client for the side panel (an extension page — it fetches the sidecar directly
// through shared/backend.ts). Everything decision-shaped lives on the backend.

import { backendJson } from './backend'
import type { FillContext } from './types'

export { BackendError } from './backend'

export function health(): Promise<{ status: string }> {
  return backendJson<{ status: string }>('/health')
}

/** The per-job fill payload the backend prepared for the active tab (docs/11 §W1.5). */
export async function getFillContext(url: string): Promise<FillContext | null> {
  try {
    return await backendJson<FillContext>(`/api/v1/fill-context?url=${encodeURIComponent(url)}`)
  } catch (e) {
    if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) return null
    throw e
  }
}
