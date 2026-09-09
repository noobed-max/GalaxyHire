import { describe, expect, it } from 'vitest'

import type { ExtensionMessage, FillPage, FillReport } from '../shared/messages'

/**
 * The fill-report wiring (§6).
 *
 * This path is easy to break silently: the report is fire-and-forget by design, so a broken link
 * costs the activity log with no visible symptom. These tests pin the shape and the two decisions
 * that make it work — the report is forwarded by the background worker (the page origin has no host
 * permission for the backend), and it carries a jobId or is dropped.
 */

describe('FillReport message', () => {
  it('is part of the extension message union', () => {
    // A missing union member means the background switch never sees it and TypeScript won't say so
    // at the send site.
    const report: ExtensionMessage = { type: 'FILL_REPORT', jobId: 'j1', filled: 9, total: 14 }
    expect(report.type).toBe('FILL_REPORT')
  })

  it('carries both filled and total so partial coverage is visible', () => {
    // "filled 9" alone hides that five fields were missed, which is exactly the signal that tells
    // the user this portal is poorly handled.
    const report: FillReport = { type: 'FILL_REPORT', jobId: 'j1', filled: 9, total: 14 }
    expect(report.total - report.filled).toBe(5)
  })

  it('allows a null jobId for a page with no prepared application', () => {
    const report: FillReport = { type: 'FILL_REPORT', jobId: null, filled: 0, total: 3 }
    expect(report.jobId).toBeNull()
  })
})

describe('FillPage carries the job identity', () => {
  it('accepts a jobId', () => {
    // Without this the content script has nothing to attribute the outcome to and the report is
    // dropped by the background worker.
    const msg: FillPage = {
      type: 'FILL_PAGE',
      // Cast through unknown: only the jobId field is under test, and `tsc --noEmit` (run by
      // `npm run build`, unlike vitest) rejects a direct cast from a partial object.
      profile: { name: 'A' } as unknown as FillPage['profile'],
      jobId: 'j1',
      resumeUrl: '/api/v1/leads/j1/pdf',
    }
    expect(msg.jobId).toBe('j1')
    expect(msg.resumeUrl).toBe('/api/v1/leads/j1/pdf')
  })
})
