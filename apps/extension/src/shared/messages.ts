// Message protocol between the side panel, background worker, and content script.
//
// The worker exists because a content script runs inside the page: its fetches are bound by that
// page's CSP/CORS, so every backend call is relayed through the extension worker instead. The
// worker itself is not page-bound, so relaying also keeps 127.0.0.1 reachable under strict CSP
// (docs/06 §4).

import type { Profile } from './types'

/** Panel → background → richest content-script frame. */
export interface FillPage {
  type: 'FILL_PAGE'
  profile: Profile
  /** Gateway-relative download path of the tailored resume, e.g. /api/v1/leads/x/pdf. */
  resumeUrl?: string
  /** Which prepared application this is, so the fill outcome can be attributed in the activity log. */
  jobId?: string
}

/**
 * Content script → background: what the fill actually achieved.
 *
 * Reported rather than inferred because "filled 9 of 14" is the only way the user learns which
 * portals the extension handles badly — and §6 wants the whole activity recorded in the pipeline.
 */
export interface FillReport {
  type: 'FILL_REPORT'
  jobId: string | null
  filled: number
  total: number
}

export interface FillAck {
  ack: boolean
  error?: string
}

/** Content script → everyone (status log lines shown in the panel). */
export interface AgentStatus {
  type: 'AGENT_STATUS'
  text: string
}
export interface AgentDone {
  type: 'AGENT_DONE'
  success: boolean
  summary: string
}
export interface AgentError {
  type: 'AGENT_ERROR'
  message: string
}

/** Content script → background: a backend call relayed through the worker.
 *  `path` is always relative — the worker only ever fetches the fixed backend origin. */
export interface BackendJsonRequest {
  type: 'BACKEND_JSON'
  path: string
  init?: { method?: string; body?: string }
}
export interface BackendJsonResponse {
  ok: boolean
  status: number
  body: string
}
export interface BackendBlobRequest {
  type: 'BACKEND_BLOB'
  path: string
}
export interface BackendBlobResponse {
  ok: boolean
  status: number
  base64?: string
}

export type ExtensionRelay = BackendJsonRequest | BackendBlobRequest

export type ExtensionMessage =
  | FillPage
  | FillReport
  | AgentStatus
  | AgentDone
  | AgentError
  | ExtensionRelay
