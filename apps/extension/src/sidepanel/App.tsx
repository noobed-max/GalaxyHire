// Fill-only panel (docs/11 §W3). The website is the UI; this panel only fills the application
// form for the job the user prepared there. It reads the per-job fill context for the active tab,
// converts it to the fill profile, and drives the filler. Never submits — and since the backend
// makes every value decision, there is nothing to configure here.

import { useEffect, useRef, useState } from 'react'
import { BackendError, getFillContext, health } from '../shared/api'
import { fillContextToProfile } from '../shared/fillProfile'
import type { ExtensionMessage, FillPage } from '../shared/messages'
import type { FillContext } from '../shared/types'

type Conn = 'checking' | 'ok' | 'down'

export default function App() {
  const [conn, setConn] = useState<Conn>('checking')
  const [ctx, setCtx] = useState<FillContext | null>(null)
  const [ctxMsg, setCtxMsg] = useState('Looking for a prepared job…')
  const [filling, setFilling] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  async function checkConn() {
    setConn('checking')
    try {
      const h = await health()
      setConn(h.status === 'ok' ? 'ok' : 'down')
    } catch {
      setConn('down')
    }
  }

  async function loadContext() {
    setCtxMsg('Looking for a prepared job…')
    try {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
      const found = await getFillContext(tab?.url ?? '')
      setCtx(found)
      if (!found) setCtxMsg('No prepared job. On the website, search and hit Apply, then come back here.')
    } catch (e) {
      setCtx(null)
      setCtxMsg(e instanceof BackendError ? `Can't reach backend (${e.status})` : "Can't reach backend")
    }
  }

  useEffect(() => {
    void checkConn()
    void loadContext()
  }, [])

  // live fill status from the content script
  useEffect(() => {
    const listener = (msg: ExtensionMessage) => {
      if (msg.type === 'AGENT_STATUS') setLog((l) => [...l, msg.text])
      else if (msg.type === 'AGENT_DONE') {
        setLog((l) => [...l, msg.success ? `✓ ${msg.summary}` : `⚠ ${msg.summary}`])
        setFilling(false)
      } else if (msg.type === 'AGENT_ERROR') {
        setLog((l) => [...l, `Error: ${msg.message}`])
        setFilling(false)
      }
    }
    chrome.runtime.onMessage.addListener(listener)
    return () => chrome.runtime.onMessage.removeListener(listener)
  }, [])

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight)
  }, [log])

  async function fillThisPage() {
    if (!ctx) return
    setFilling(true)
    setLog(['Preparing…'])
    try {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
      if (tab?.url) {
        const origin = new URL(tab.url).origin + '/*'
        const ok = await chrome.permissions.request({ origins: [origin] })
        if (!ok) {
          setLog((l) => [...l, 'Permission for this site was denied.'])
          setFilling(false)
          return
        }
      }
      const msg: FillPage = {
        type: 'FILL_PAGE',
        profile: fillContextToProfile(ctx),
        resumeUrl:
          ctx.resume_url ||
          (ctx.resume_ref ? `/api/v1/corpus-assets/${encodeURIComponent(ctx.resume_ref)}` : undefined),
        // Carried through so the fill outcome can be attributed to this application in the
        // activity log; without it the report has nothing to attach to and is dropped.
        jobId: ctx.job_id,
      }
      const ack = (await chrome.runtime.sendMessage(msg)) as { ack: boolean; error?: string }
      if (!ack?.ack) {
        setLog((l) => [...l, ack?.error ?? 'Could not start the fill.'])
        setFilling(false)
      }
    } catch (e) {
      setLog((l) => [...l, e instanceof Error ? e.message : 'Fill failed to start.'])
      setFilling(false)
    }
  }

  return (
    <div className="flex h-full flex-col bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <span className="text-lg">🪐</span>
          <h1 className="text-sm font-semibold tracking-tight">GalaxyHire · Filler</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void checkConn()
              void loadContext()
            }}
            title="Backend connection (http://127.0.0.1:8080)"
            className="flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs dark:bg-slate-800"
          >
            <span
              className={
                'h-2 w-2 rounded-full ' +
                (conn === 'ok' ? 'bg-emerald-500' : conn === 'checking' ? 'bg-amber-400' : 'bg-rose-500')
              }
            />
            {conn === 'ok' ? 'connected' : conn === 'checking' ? '…' : 'offline'}
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        <p className="mb-3 text-xs text-slate-500">
          This panel only fills forms. Do your searching and profile on the{' '}
          <strong>website</strong>; click <strong>Apply</strong> there, then fill here.
        </p>

        {!ctx ? (
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
            {ctxMsg}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <h2 className="font-semibold">{ctx.title}</h2>
              <div className="text-xs text-slate-500">{ctx.company}</div>
              <div className="mt-2 text-[11px] text-slate-400">
                Résumé ready · {ctx.projects.length} projects · {ctx.skills.length} skills
                {ctx.experience.length > 0 && <> · {ctx.experience.length} roles</>}
              </div>
            </div>

            <button
              onClick={fillThisPage}
              disabled={filling}
              className="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
            >
              {filling ? 'Filling…' : 'Fill this page'}
            </button>
            <p className="text-[11px] text-slate-400">
              Open the application form in the tab, then Fill. It never clicks Submit — you review and
              click Apply yourself.
            </p>

            {log.length > 0 && (
              <div
                ref={logRef}
                className="max-h-48 overflow-y-auto rounded-md bg-slate-50 p-2 font-mono text-[11px] leading-relaxed text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              >
                {log.map((l, i) => (
                  <div key={i}>{l}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
