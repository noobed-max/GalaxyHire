// Content script (docs/06 §3). Orchestrates one fill run on the career page:
//   submit guard on → deterministic ATS mappers → backend-decided values for the rest → attach resume.
// It reads and writes DOM only. Every value decision (LLM or otherwise) is made by the backend;
// this file never clicks Submit/Apply/Next — a capture-phase guard drops untrusted clicks on those.

import type { ExtensionMessage, FillPage } from './shared/messages'
import type { ScannedField } from './fill/engine'
import { scanFields, applyValue } from './fill/engine'
import { detectPlatform, runDeterministicFill } from './fill/mappers'
import { installSubmitGuard } from './fill/guard'
import { attachFile, blobToFile, findResumeInput } from './fill/fileupload'
import { pageBackendBlob, pageBackendJson } from './shared/backend'

let running = false

chrome.runtime.onMessage.addListener((msg: ExtensionMessage, _sender, sendResponse) => {
  if (msg.type !== 'FILL_PAGE') return false
  if (running) {
    sendResponse({ ack: false, error: 'A fill is already running on this page.' })
    return false
  }
  void runFill(msg)
  sendResponse({ ack: true })
  return false
})

const status = (text: string) => void chrome.runtime.sendMessage({ type: 'AGENT_STATUS', text })
const done = (success: boolean, summary: string) =>
  void chrome.runtime.sendMessage({ type: 'AGENT_DONE', success, summary })

async function runFill(msg: FillPage) {
  running = true
  const releaseGuard = installSubmitGuard(status)
  try {
    const platform = detectPlatform()
    const fields = scanFields()
    if (!fields.length) {
      done(false, 'No fillable fields found on this page.')
      return
    }
    status(`Detected ${platform} · ${fields.length} field${fields.length === 1 ? '' : 's'}.`)

    // 1) deterministic mappers for the common identity fields
    const det = runDeterministicFill(fields, msg.profile)
    det.lines.forEach(status)
    let filled = det.filled

    // 2) everything else: describe the fields to the backend and apply what it decides
    if (det.remaining.length) {
      try {
        filled += await askBackendAndFill(det.remaining, msg.jobId)
      } catch (e) {
        status(`Backend fill skipped: ${e instanceof Error ? e.message : String(e)}`)
      }
    }

    // 3) attach the tailored resume (assisted; falls back to manual on custom widgets)
    if (msg.resumeUrl) await tryAttachResume(msg.resumeUrl)

    // Report the outcome so it lands in the activity log (§6). Fire-and-forget: the fill already
    // happened, and a failed audit write must not turn a successful fill into an error the user sees.
    void chrome.runtime.sendMessage({
      type: 'FILL_REPORT',
      jobId: msg.jobId ?? null,
      filled,
      total: fields.length,
    })

    done(
      filled > 0,
      filled > 0
        ? `Filled ${filled} of ${fields.length} field${fields.length === 1 ? '' : 's'}. Review everything, then click Apply yourself.`
        : 'Nothing was filled — check the log.',
    )
  } catch (e) {
    void chrome.runtime.sendMessage({
      type: 'AGENT_ERROR',
      message: e instanceof Error ? e.message : String(e),
    })
  } finally {
    releaseGuard()
    running = false
  }
}

interface FillDecisionResponse {
  fills?: { id?: unknown; value?: unknown }[]
}

async function askBackendAndFill(fields: ScannedField[], jobId?: string): Promise<number> {
  status(`Asking the backend about ${fields.length} field(s)…`)
  const payload = {
    job_id: jobId ?? '',
    fields: fields.map((f) => ({
      id: f.id,
      label: f.label.slice(0, 300),
      kind: f.kind,
      type: f.type,
      options: f.options.slice(0, 40),
      required: f.required,
    })),
  }
  const decision = (await pageBackendJson('/api/v1/fill/decide', payload)) as FillDecisionResponse
  const values = new Map<number, string>()
  for (const entry of decision?.fills ?? []) {
    const id = typeof entry.id === 'number' ? entry.id : Number(entry.id)
    if (Number.isFinite(id) && entry.value !== null && entry.value !== undefined) {
      values.set(id, String(entry.value))
    }
  }
  let filled = 0
  for (const field of fields) {
    const raw = values.get(field.id)
    if (raw === undefined || raw.trim() === '') continue
    try {
      if (applyValue(field, raw)) {
        filled++
        status(`✓ ${field.label}: ${raw.length > 50 ? `${raw.slice(0, 50)}…` : raw}`)
      } else {
        status(`• ${field.label}: no match for "${raw.slice(0, 40)}"`)
      }
    } catch (e) {
      status(`• ${field.label}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }
  return filled
}

async function tryAttachResume(path: string) {
  const input = findResumeInput()
  if (!input) {
    status('No resume file input found — upload it manually.')
    return
  }
  try {
    const blob = await pageBackendBlob(path)
    if (!blob) {
      status('Could not download the resume — upload it manually.')
      return
    }
    const file = blobToFile(blob, 'resume.pdf')
    if (attachFile(input, file)) status('✓ Attached your tailored resume.')
    else status('This upload widget blocks auto-attach — drag the downloaded resume in manually.')
  } catch {
    status('Resume attach failed — upload it manually.')
  }
}

export {}
