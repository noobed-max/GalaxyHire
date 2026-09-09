import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import "./index.css";
import type { ApiFetch, PipelineTab, View } from "./types";
import { createApiFetch } from "./api/client";
import { useAppShellState, AppContext } from "./shared/context/AppContext";
import { ONBOARDING_KEY } from "./shared/lib/leadUtils";
import { dashboardFindRequest, saveActiveSearch, type ScanResult } from "./shared/lib/searchState";
import { useWS } from "./shared/hooks/useWS";
import { useLeads } from "./shared/hooks/useLeads";
import { useDueFollowups } from "./shared/hooks/useDueFollowups";
import { useKeyboardShortcuts } from "./shared/hooks/useKeyboardShortcuts";
import { preloadViews } from "./shared/lib/preloadViews";
import { TopNav } from "./shared/components/TopNav";
import { Topbar } from "./shared/components/Topbar";
import ErrorBoundary from "./shared/components/ErrorBoundary";
import { DesktopUpdatePrompt } from "./shared/components/DesktopUpdatePrompt";
import { SemanticRuntimePrompt } from "./shared/components/SemanticRuntimePrompt";

const DashboardView = lazy(() => import("./features/dashboard/DashboardView").then(module => ({ default: module.DashboardView })));
const CartView = lazy(() => import("./features/cart/CartView").then(module => ({ default: module.CartView })));
const ApplyJobView = lazy(() => import("./features/apply/ApplyJobView").then(module => ({ default: module.ApplyJobView })));
const PipelineView = lazy(() => import("./features/pipeline/PipelineView").then(module => ({ default: module.PipelineView })));
const EmailMonitoringView = lazy(() => import("./features/email/EmailMonitoringView").then(module => ({ default: module.EmailMonitoringView })));
const ActivityView = lazy(() => import("./features/activity/ActivityView").then(module => ({ default: module.ActivityView })));
const AnalyticsView = lazy(() => import("./features/analytics/AnalyticsView").then(module => ({ default: module.AnalyticsView })));
const ProfileView = lazy(() => import("./features/profile/ProfileView").then(module => ({ default: module.ProfileView })));
const IngestionView = lazy(() => import("./features/profile/IngestionView").then(module => ({ default: module.IngestionView })));
const JobDetailsPanel = lazy(() => import("./features/pipeline/components/JobDetailsPanel").then(module => ({ default: module.JobDetailsPanel })));
const OnboardingWizard = lazy(() => import("./shared/components/OnboardingWizard").then(module => ({ default: module.OnboardingWizard })));
const SettingsModal = lazy(() => import("./features/settings/SettingsModal"));

/**
 * No client deadline on a search.
 *
 * A search collects from the job boards before it filters, and how long that takes depends on
 * the phrase, how many portals have anything for it, and the network. Any number here
 * is a guess about how long a scrape "should" take, and the first run slower than the guess is
 * aborted for no reason — while the scrape carries on in the background, so the user sees a
 * failure on top of work that is still succeeding.
 *
 * 0 disables the timeout in `client.ts`. The control is the Stop button on the progress banner,
 * which cancels the collection as well as the search. Progress is visible the whole time, so a
 * long wait is legible rather than a frozen spinner.
 */
const SCAN_TIMEOUT_MS = 0;

/**
 * Last-resort recovery for a stuck *indicator*, not a limit on the work.
 *
 * If a terminal websocket frame is lost the spinner can stay on forever. This clears it. It has
 * to sit far beyond any real run: at 15 minutes it fired during healthy scrapes and announced it
 * had given up while collection was still going, making the watchdog the bug it exists to
 * prevent. The scrape itself is unaffected either way — this only touches the UI flag.
 */
const STUCK_INDICATOR_MS = 3 * 60 * 60 * 1000;

const PIPELINE_VIEW_TO_TAB: Partial<Record<View, PipelineTab>> = {
  pipeline: "all",
  "pipeline-hot": "hot",
  "pipeline-found": "found",
  "pipeline-evaluated": "evaluated",
  "pipeline-generated": "generated",
  "pipeline-applied": "applied",
  "pipeline-discarded": "discarded",
};

const PIPELINE_TAB_TO_VIEW: Record<PipelineTab, View> = {
  all: "pipeline",
  hot: "pipeline-hot",
  found: "pipeline-found",
  evaluated: "pipeline-evaluated",
  generated: "pipeline-generated",
  applied: "pipeline-applied",
  discarded: "pipeline-discarded",
};

type SubsystemHealth = Record<string, { status: string; error?: string; reason?: string; [key: string]: unknown }>;

function isActionableSubsystemIssue(name: string, value: SubsystemHealth[string]) {
  if (value.status === "ok") return false;
  const message = String(value.error || value.reason || "").toLowerCase();
  if (name === "llm" && message.includes("api key")) return false;
  if (name === "embeddings" && value.mode === "hashing") return false;
  return true;
}

export default function App() {
  const { conn, port, apiToken, sidecarError, logs, addLog: wsAddLog, progress, resetProgress } = useWS();
  const api = useMemo<ApiFetch | null>(() => {
    if (!port || !apiToken) return null;
    return createApiFetch(port, apiToken);
  }, [port, apiToken]);
  const { leads, setLeads, loading: leadsLoading, error: leadsError } = useLeads(api, wsAddLog);
  const dueFollowups = useDueFollowups(api);
  const appShell = useAppShellState(api);
  const {
    view, setView, sel, setSel, showSettings, setShowSettings, showOnboarding,
    setShowOnboarding, applyDraft, setApplyDraft, applyAutoFocus, setApplyAutoFocus,
    scanning, setScanning, reevaluating, setReevaluating, cleaning, setCleaning,
    scanErr, setScanErr, closeDrawer, focusApplyView, openSettings, cart, addToCart,
    removeFromCart, activeIngestion,
  } = appShell;
  // Always pass the live version of the selected lead so the drawer reflects real-time updates
  const liveSel = sel ? (leads.find(l => l.job_id === sel.job_id) ?? sel) : null;
  const [startupSeconds, setStartupSeconds] = useState(0);
  const [subsystems, setSubsystems] = useState<SubsystemHealth | null>(null);

  useEffect(() => {
    // Pull every lazy view chunk into the browser cache during idle time so
    // nav clicks never sit on the "Opening…" spinner waiting for a dynamic
    // import (see preloadViews.ts).
    preloadViews();
  }, []);

  useEffect(() => {
    const h = () => setScanning(false);
    window.addEventListener("scan-done", h);
    return () => window.removeEventListener("scan-done", h);
  }, []);

  useEffect(() => {
    const h = (event: Event) => {
      const detail = (event as CustomEvent<{ scanning?: boolean; reevaluating?: boolean }>).detail || {};
      if (typeof detail.scanning === "boolean") setScanning(detail.scanning);
      if (typeof detail.reevaluating === "boolean") setReevaluating(detail.reevaluating);
    };
    window.addEventListener("backend-status", h);
    return () => window.removeEventListener("backend-status", h);
  }, []);

  useEffect(() => {
    // Watchdog for ANY long-running op (scan / re-evaluate / cleanup): if a lost
    // terminal WS frame leaves a flag stuck while the socket stays connected, clear
    // it (and the progress bar) so the UI isn't wedged.
    //
    // Must outlast a real scan. A search collects from the selected portals before it filters,
    // which can run many minutes, so the old 15-minute window fired *during* healthy runs and
    // announced it had given up while collection was still going — the watchdog itself becoming
    // the bug it exists to prevent. Kept just above the request timeout so a genuinely wedged UI
    // still recovers, only after the request itself has had its chance to fail.
    if (!scanning && !reevaluating && !cleaning) return;
    const timer = window.setTimeout(() => {
      setScanning(false);
      setReevaluating(false);
      setCleaning(false);
      resetProgress();
      const msg = "Activity indicator cleared after a long wait with no backend progress.";
      setScanErr(msg);
      wsAddLog(msg, "system", "scan");
    }, STUCK_INDICATOR_MS);
    return () => window.clearTimeout(timer);
  }, [scanning, reevaluating, cleaning, progress.updatedAt, setScanning, setReevaluating, setCleaning, setScanErr, wsAddLog, resetProgress]);

  useEffect(() => {
    if (api) return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      setStartupSeconds(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [api]);

  useEffect(() => {
    if (!api) {
      setSubsystems(null);
      return;
    }
    let stopped = false;
    const load = async () => {
      try {
        const response = await api("/api/v1/health/subsystems", { timeoutMs: 10000 });
        if (!response.ok) return;
        const payload = await response.json();
        if (!stopped) setSubsystems(payload);
      } catch {
        if (!stopped) setSubsystems(null);
      }
    };
    load();
    const timer = window.setInterval(load, 30000);
    window.addEventListener("subsystems-refresh", load);
    return () => {
      stopped = true;
      window.removeEventListener("subsystems-refresh", load);
      window.clearInterval(timer);
    };
  }, [api]);

  useKeyboardShortcuts({
    onEscape: closeDrawer,
    onCmdK: focusApplyView,
    onCmdComma: openSettings,
  });

  useEffect(() => {
    if (view !== "apply" || !applyAutoFocus) return;
    const timer = window.setTimeout(() => setApplyAutoFocus(false), 0);
    return () => window.clearTimeout(timer);
  }, [view, applyAutoFocus]);

  useEffect(() => {
    const h = () => setReevaluating(false);
    window.addEventListener("reevaluate-done", h);
    return () => window.removeEventListener("reevaluate-done", h);
  }, []);

  useEffect(() => {
    const h = () => setCleaning(false);
    window.addEventListener("cleanup-done", h);
    return () => window.removeEventListener("cleanup-done", h);
  }, []);

  const onScan = useCallback(async (brief?: string, portals?: string[] | null, location?: string) => {
    if (!port || !api || scanning) return;
    const submittedBrief = String(brief || "").trim();
    if (!submittedBrief) {
      setScanErr("Describe the role you want before starting a search.");
      return;
    }
    setScanning(true); setScanErr(null);
    try {
      // No deadline: this collects before it filters. The 30s default aborted it at 30 seconds
      // with "Local backend timed out" while the scrape carried on — the product looked broken
      // on the exact path that makes it work. Stop is the control; see SCAN_TIMEOUT_MS.
      //
      // The portal selection travels explicitly ("this run only") rather than relying on the
      // saved toggle map the server would otherwise resolve: a toggle followed by an immediate
      // Find must not race the async persist. Null (catalog still loading) falls back to the
      // saved map server-side — same as before. Location travels the same way; empty means
      // everywhere.
      const r = await api(`/api/v1/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // This is the explicit dashboard Find action. Background searches omit this intent and
        // may use the server's freshness cache; every completed click here asks for a new scrape.
        body: JSON.stringify(dashboardFindRequest(submittedBrief, portals, location)),
        timeoutMs: SCAN_TIMEOUT_MS,
      });
      const body = await r.json().catch(() => ({})) as ScanResult;
      if (!r.ok) {
        const detail = typeof body.detail === "string"
          ? body.detail
          : body.detail && typeof body.detail === "object" && "error" in body.detail
            ? String((body.detail as { error?: unknown }).error || "")
            : "";
        throw new Error(detail || body.error || "Backend unreachable");
      }
      if (body.cancelled) return;
      if (!body.ok) throw new Error(body.error || "Search could not be completed");
      saveActiveSearch(body, submittedBrief);
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (e: any) {
      setScanErr(e.message || "Search failed");
    } finally {
      setScanning(false);
      resetProgress();
    }
  }, [port, api, scanning, resetProgress, setScanErr, setScanning]);

  const onStopScan = useCallback(async () => {
    if (!port || !api) return;
    try {
      const r = await api(`/api/v1/scan/stop`, { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(body.detail || "Stop search failed");
      }
    } catch (e: any) {
      const msg = e.message || "Stop search request failed";
      setScanErr(msg);
      wsAddLog(msg, "system", "scan");
    } finally {
      setScanning(false);
      resetProgress();
    }
  }, [port, api, resetProgress, setScanErr, setScanning, wsAddLog]);

  const onReevaluateJobs = useCallback(async () => {
    if (!port || !api || reevaluating || scanning) return;
    setReevaluating(true); setScanErr(null);
    try {
      const r = await api(`/api/v1/leads/reevaluate`, { method: "POST" });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || "Re-evaluation failed");
      }
    } catch (e: any) {
      const msg = e.message || "Re-evaluation failed";
      setScanErr(msg); setReevaluating(false);
      wsAddLog(msg, "system", "reeval");
    }
  }, [port, api, reevaluating, scanning, wsAddLog]);

  const onStopReevaluate = useCallback(async () => {
    if (!port || !api) return;
    try {
      const r = await api(`/api/v1/leads/reevaluate/stop`, { method: "POST" });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || "Stop re-evaluation failed");
      }
    } catch (e: any) {
      const msg = e.message || "Stop re-evaluation request failed";
      setScanErr(msg);
      wsAddLog(msg, "system", "reeval");
    }
  }, [port, api, setScanErr, wsAddLog]);

  const onCleanupLeads = useCallback(async () => {
    if (!port || !api || scanning || reevaluating || cleaning) return;
    const ok = window.confirm("Discard obvious bad rows like HN discussion comments and non-job content? This keeps the rows in Discarded with a cleanup reason.");
    if (!ok) return;
    setCleaning(true); setScanErr(null);
    try {
      const r = await api(`/api/v1/leads/cleanup`, { method: "POST" });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || "Cleanup failed");
      }
      const result = await r.json();
      wsAddLog(`Cleanup discarded ${result.candidates ?? 0} bad rows after scanning ${result.scanned ?? 0}`, "system", "cleanup");
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (e: any) {
      const msg = e.message || "Cleanup failed";
      setScanErr(msg);
      wsAddLog(msg, "system", "cleanup");
    } finally {
      setCleaning(false);
    }
  }, [port, api, scanning, reevaluating, cleaning, wsAddLog]);

  const deleteLead = useCallback(async (jobId: string) => {
    if (!port || !api) return;
    const r = await api(`/api/v1/leads/${jobId}`, { method: "DELETE" });
    // Only remove it locally on a real success — a swallowed HTTP error left the
    // lead deleted in the UI (and broke bulkDelete's failure counting).
    if (!r.ok) throw new Error(`Delete failed (${r.status})`);
    setLeads(prev => prev.filter(l => l.job_id !== jobId));
  }, [port, api, setLeads]);

  const leadCounts = {
    total:        leads.length,
    hot:          leads.filter(l => (l.signal_score || 0) >= 80 || (l.score || 0) >= 85).length,
    discovered:   leads.filter(l=>l.status==="discovered").length,
    evaluated:    leads.filter(l => l.score > 0 || (l.signal_score || 0) > 0).length,
    evaluating:   leads.filter(l=>l.status==="evaluating").length,
    tailoring:    leads.filter(l=>l.status==="tailoring").length,
    approved:     leads.filter(l=>l.status==="approved").length,
    ready:        leads.filter(l=>l.status==="tailoring" || l.status==="approved").length,
    applied:      leads.filter(l=>["applied", "interviewing", "accepted", "rejected"].includes(l.status)).length,
    discarded:    leads.filter(l=>l.status==="discarded").length,
    interviewing: leads.filter(l=>l.status==="interviewing").length,
    accepted: leads.filter(l=>l.status==="accepted").length,
    rejected: leads.filter(l=>l.status==="rejected").length,
    cart: cart.length,
  };
  const pipelineTab = PIPELINE_VIEW_TO_TAB[view] || "all";
  const isPipelineView = Boolean(PIPELINE_VIEW_TO_TAB[view]);
  const degradedSubsystems = Object.entries(subsystems ?? {}).filter(([name, value]) => isActionableSubsystemIssue(name, value));

  if (!api) {
    return (
      <>
        <StartupScreen conn={conn} port={port} seconds={startupSeconds} sidecarError={sidecarError} />
        <DesktopUpdatePrompt />
      </>
    );
  }

  return (
    <AppContext.Provider value={appShell}>
      <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw", overflow: "hidden", alignItems: "stretch" }}>
        <TopNav
          view={view}
          setView={setView}
          leadCounts={leadCounts}
          onSettings={() => setShowSettings(true)}
        />
        <div className="app-main">
          <Topbar view={view} progress={progress} activeIngestion={activeIngestion} />
          <SubsystemBanner items={degradedSubsystems} />
          <NoticeBanner />
          <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", background: "var(--paper)" }}>
            <Suspense fallback={<ViewLoading />}>
              {view === "apply"     && <ErrorBoundary label="Apply" api={api ?? undefined}><ApplyJobView port={port} api={api} leads={leads} openDrawer={setSel} initialInput={applyDraft} autoFocus={applyAutoFocus} /></ErrorBoundary>}
              {view === "dashboard" && <ErrorBoundary label="Dashboard" api={api ?? undefined}><DashboardView leads={leads} dueFollowups={dueFollowups} logs={logs} setView={setView} openDrawer={setSel} scanning={scanning} reevaluating={reevaluating} cleaning={cleaning} progress={progress} onScan={onScan} onStopScan={onStopScan} onReevaluate={onReevaluateJobs} onStopReevaluate={onStopReevaluate} onCleanup={onCleanupLeads} scanErr={scanErr} api={api} cart={cart} addToCart={addToCart} /></ErrorBoundary>}
              {view === "cart" && <ErrorBoundary label="Apply cart" api={api ?? undefined}><CartView api={api} leads={leads} cart={cart} removeFromCart={removeFromCart} /></ErrorBoundary>}
              {isPipelineView  && <ErrorBoundary label="Jobs" api={api ?? undefined}><PipelineView leads={leads} openDrawer={setSel} deleteLead={deleteLead} port={port} api={api} scanning={scanning} reevaluating={reevaluating} cleaning={cleaning} onStopScan={onStopScan} onReevaluate={onReevaluateJobs} onStopReevaluate={onStopReevaluate} onCleanup={onCleanupLeads} loading={leadsLoading || !port || !api} error={leadsError} tab={pipelineTab} onTabChange={next => setView(PIPELINE_TAB_TO_VIEW[next])} cart={cart} addToCart={addToCart} /></ErrorBoundary>}
              {view === "email"     && <ErrorBoundary label="Email updates" api={api ?? undefined}><EmailMonitoringView api={api} /></ErrorBoundary>}
              {view === "activity"  && <ErrorBoundary label="Activity log" api={api ?? undefined}><ActivityView logs={logs} /></ErrorBoundary>}
              {view === "analytics" && <ErrorBoundary label="Analytics" api={api ?? undefined}><AnalyticsView leads={leads} /></ErrorBoundary>}
              {view === "profile"   && (api ? <ErrorBoundary label="Profile" api={api ?? undefined}><ProfileView api={api} setView={setView} /></ErrorBoundary> : <BackendUnavailable title="Profile" conn={conn} port={port} />)}
              {view === "ingestion" && (api ? <ErrorBoundary label="Add experience" api={api ?? undefined}><IngestionView api={api} /></ErrorBoundary> : <BackendUnavailable title="Add experience" conn={conn} port={port} />)}
            </Suspense>
          </div>
        </div>

        {/* The details layer renders the richest untyped lead data; a
            render crash here without a boundary would blank the whole app. */}
        <ErrorBoundary label="Job details" api={api ?? undefined}>
          <Suspense fallback={null}>
            <>
              {liveSel && api && (
                <JobDetailsPanel key={liveSel.job_id} j={liveSel} api={api} onClose={() => setSel(null)} onAddToCart={() => addToCart(liveSel.job_id)} carted={cart.includes(liveSel.job_id)} />
              )}
              {showSettings && api && (
                <SettingsModal key="settings" api={api} onClose={() => setShowSettings(false)} />
              )}
              {showOnboarding && api && (
                <OnboardingWizard
                  key="onboarding"
                  api={api}
                  onOpenSettings={() => setShowSettings(true)}
                  onFinish={(draft) => {
                    localStorage.setItem(ONBOARDING_KEY, "done");
                    setApplyDraft(draft);
                    setView("apply");
                    setShowOnboarding(false);
                  }}
                />
              )}
            </>
          </Suspense>
        </ErrorBoundary>
      </div>
      <ErrorBoundary label="Prompts" api={api ?? undefined}>
        <SemanticRuntimePrompt api={api} />
        <DesktopUpdatePrompt />
      </ErrorBoundary>
    </AppContext.Provider>
  );
}

function ViewLoading() {
  return (
    <div className="gh-view-loading" role="status">
      <span className="spinner" />
      <span>Opening…</span>
    </div>
  );
}

function NoticeBanner() {
  // Transient banner for degraded/notable backend outcomes (LLM-fallback scoring,
  // empty scout, feedback re-rank) that would otherwise be buried in the log.
  const [notice, setNotice] = useState<{ level: string; msg: string } | null>(null);
  useEffect(() => {
    let timer = 0;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ level?: string; msg?: string }>).detail;
      if (!detail?.msg) return;
      setNotice({ level: detail.level || "info", msg: detail.msg });
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setNotice(null), 9000);
    };
    window.addEventListener("backend-notice", handler);
    return () => { window.removeEventListener("backend-notice", handler); window.clearTimeout(timer); };
  }, []);
  if (!notice) return null;
  const warn = notice.level === "warn";
  return (
    <div
      role="status"
      style={{
        display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
        fontSize: 13, borderBottom: "1px solid var(--line, #e5e7eb)",
        background: warn ? "var(--warn-bg, #fef3c7)" : "var(--info-bg, #e0f2fe)",
        color: warn ? "#92400e" : "#075985",
      }}
    >
      <span style={{ flex: 1 }}>{notice.msg}</span>
      <button
        onClick={() => setNotice(null)}
        aria-label="Dismiss"
        style={{ background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "inherit", lineHeight: 1 }}
      >
        ×
      </button>
    </div>
  );
}


function SubsystemBanner({ items }: { items: Array<[string, SubsystemHealth[string]]> }) {
  if (items.length === 0) return null;
  const summary = items.map(([name, value]) => `${name}: ${value.status}`).join(" | ");
  const detail = items
    .map(([name, value]) => {
      const message = value.error || value.reason;
      return message ? `${name}: ${message}` : "";
    })
    .filter(Boolean)
    .join(" | ");
  return (
    <div className="subsystem-banner" role="status">
      <strong>Subsystem degraded</strong>
      <span>{summary}</span>
      {detail && <span className="subsystem-banner-detail">{detail}</span>}
    </div>
  );
}

function StartupScreen({ conn, port, seconds, sidecarError }: { conn: string; port: number | null; seconds: number; sidecarError: string | null }) {
  const isSlow = seconds >= 20;
  return (
    <div style={{
      minHeight: "100vh",
      width: "100vw",
      display: "grid",
      placeItems: "center",
      background: "var(--paper)",
      color: "var(--ink)",
      padding: 24,
    }}>
      <section className="card col gap-4" style={{ width: "min(720px, 100%)", padding: 30 }}>
        <div className="row gap-3">
          <div className="spinner" />
          <div>
            <div className="eyebrow">Starting GalaxyHire</div>
            <h1 style={{ fontSize: 30, marginTop: 6 }}>Preparing your local workspace</h1>
          </div>
        </div>
        <p style={{ color: "var(--ink-2)", lineHeight: 1.6, maxWidth: 620 }}>
          The desktop app is launching its bundled backend, opening the local database, and waiting for a private API token.
          The setup guide will appear automatically as soon as the backend is ready.
        </p>
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          <span className="pill">Backend: {conn}</span>
          <span className="pill">Port: {port ?? "pending"}</span>
          <span className="pill">Elapsed: {seconds}s</span>
        </div>
        {isSlow && (
          <div style={{
            border: "1px solid var(--line)",
            borderRadius: 8,
            padding: 14,
            background: "var(--paper-3)",
            color: "var(--ink-2)",
            lineHeight: 1.55,
          }}>
            This is taking longer than expected. If it stays here, the bundled backend failed to start.
            On macOS, use Privacy &amp; Security &gt; Open Anyway if the app was blocked, then restart GalaxyHire.
          </div>
        )}
        {sidecarError && (
          <div style={{
            border: "1px solid var(--bad)",
            borderRadius: 8,
            padding: 14,
            background: "var(--bad-soft)",
            color: "var(--bad)",
            lineHeight: 1.55,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            whiteSpace: "pre-wrap",
          }}>
            {sidecarError}
          </div>
        )}
      </section>
    </div>
  );
}

function BackendUnavailable({ title, conn, port }: { title: string; conn: string; port: number | null }) {
  return (
    <div className="ingestion-page scroll">
      <div className="ingestion-shell">
        <div className="card col gap-4" style={{ padding: 28 }}>
          <div className="row gap-3">
            <div className="spinner" />
            <div>
              <div className="eyebrow">Starting local backend</div>
              <h2 style={{ marginTop: 6 }}>{title} will appear automatically</h2>
            </div>
          </div>
          <p style={{ color: "var(--ink-2)", maxWidth: 620, lineHeight: 1.6 }}>
            GalaxyHire is waiting for the bundled sidecar to publish its API token and port. This should take a few seconds after launch.
          </p>
          <div className="row gap-2" style={{ flexWrap: "wrap" }}>
            <span className="pill">Connection: {conn}</span>
            <span className="pill">Port: {port ?? "pending"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
