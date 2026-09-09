import { useCallback, useEffect, useRef, useState } from "react";
import { httpBaseFor, platform, wsBaseFor } from "../lib/platform";
import type { ConnSt, Lead, LogLine, OperationProgress } from "../../types";
import type { WSMessage } from "../../api/types";

const READY_RETRY_MS = 180;
const READY_ATTEMPTS = 60;

const delay = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

const emptyProgress = (): OperationProgress => ({
  active: false,
  mode: null,
  total: 0,
  completed: 0,
  current: "",
  updatedAt: Date.now(),
});

function firstNumber(value: string | undefined) {
  const match = String(value || "").match(/\b(\d+)\b/);
  return match ? Number(match[1]) : 0;
}

function prevTotalFromMessage(value: string | undefined) {
  const match = String(value || "").match(/\[(\d+)\/(\d+)\]/);
  return match ? Number(match[2]) : 0;
}

const EVENT_LABELS: Record<string, string> = {
  scan_start: "Job search started",
  scan_info: "Job search update",
  scan_done: "Job search complete",
  scan_stop: "Job search stopped",
  scan_error: "Job search failed",
  scan_warn: "Job search warning",
  scout_done: "Collection complete",
  eval_start: "Matching started",
  eval_scored: "Match scored",
  eval_done: "Matching complete",
  reeval_start: "Re-evaluation started",
  reeval_scored: "Job re-scored",
  reeval_done: "Re-evaluation complete",
  gen_start: "Document generation started",
  gen_done: "Documents ready",
  gen_error: "Document generation failed",
  ingested: "Profile import complete",
  ingest_progress: "Profile import progress",
  ingest_thought: "AI extraction",
  ingest_review_required: "Profile import needs review",
  ingest_failed: "Profile import failed",
  ingest_resolved: "Profile duplicate review completed",
  fill_done: "Application fields filled",
  applied: "Application marked as applied",
};

function sourceForAgentEvent(event = "") {
  if (/^(scan|scout|ghost)/.test(event)) return "search";
  if (/^(eval|reeval|feedback|cleanup|auto_discard)/.test(event)) return "matching";
  if (/^(gen|fill|applied|package)/.test(event)) return "documents";
  if (/^ingest/.test(event)) return "profile";
  return "workflow";
}

export function activityEntryFromWSMessage(message: WSMessage): Pick<LogLine, "msg" | "kind" | "src"> | null {
  if (message.type === "heartbeat" || message.type === "pong") return null;
  if (message.type === "agent") {
    const event = message.event || "workflow";
    const detail = message.msg?.trim() || "";
    const label = EVENT_LABELS[event];
    return {
      msg: label && detail && detail !== label ? `${label} — ${detail}` : label || detail || "Workflow update",
      kind: "agent",
      src: sourceForAgentEvent(event),
    };
  }
  if (message.type === "ingest_progress") {
    const stage = message.stage_number ? `Step ${message.stage_number}` : "Progress";
    const percent = typeof message.progress_percent === "number" ? ` · ${message.progress_percent}%` : "";
    return { msg: `Profile import ${stage}${percent} — ${message.message || message.stage || "Working"}`, kind: "agent", src: "profile" };
  }
  if (message.type === "ingest_thought") {
    return { msg: `AI extraction — ${message.thought || "Analyzing the document"}`, kind: "agent", src: "profile" };
  }
  if (message.type === "ingest_review_required") {
    const count = message.duplicates_count ?? 0;
    return { msg: `Profile import needs review — ${count} similar point${count === 1 ? "" : "s"} found`, kind: "agent", src: "profile" };
  }
  if (message.type === "ingest_failed") {
    return { msg: `Profile import failed — ${message.error || "Unknown error"}`, kind: "agent", src: "profile" };
  }
  if (message.type === "ingest_resolved") {
    return { msg: "Profile duplicate review completed", kind: "agent", src: "profile" };
  }
  if (message.type === "LEAD_UPDATED" && message.data?.title) {
    const company = message.data.company ? ` at ${message.data.company}` : "";
    const status = message.data.status ? ` → ${message.data.status}` : "";
    return { msg: `${message.data.title}${company}${status}`, kind: "agent", src: "jobs" };
  }
  if (message.type === "HOT_X_LEAD" && message.data) {
    return { msg: `Strong match found — ${message.data.title} at ${message.data.company}`, kind: "agent", src: "matching" };
  }
  if (message.type === "LEADS_REFRESH") {
    return { msg: `Jobs refreshed${message.data?.reason ? ` — ${message.data.reason.split("_").join(" ")}` : ""}`, kind: "agent", src: "jobs" };
  }
  return null;
}

export function nextProgressFromAgentEvent(
  previous: OperationProgress,
  event: string | undefined,
  message: string | undefined,
  now = Date.now(),
): OperationProgress {
  if (event === "scan_start") {
    return { active: true, mode: "scan", total: 0, completed: 0, current: message ?? "", updatedAt: now };
  }
  if (event === "scan_info") {
    return { ...previous, active: true, mode: "scan", current: message ?? "", updatedAt: now };
  }
  if (event === "eval_start") {
    return { active: true, mode: "scan", total: firstNumber(message), completed: 0, current: "", updatedAt: now };
  }
  if (event === "eval_scored") {
    return { active: true, mode: "scan", total: previous.total, completed: previous.completed + 1, current: message ?? "", updatedAt: now };
  }
  if (event === "reeval_start") {
    return { active: true, mode: "reevaluate", total: firstNumber(message), completed: 0, current: "", updatedAt: now };
  }
  if (event === "reeval_scored") {
    return {
      active: true,
      mode: "reevaluate",
      total: previous.total || prevTotalFromMessage(message),
      completed: previous.completed + 1,
      current: message ?? "",
      updatedAt: now,
    };
  }
  if (
    event === "scan_done" ||
    event === "scan_stop" ||
    event === "scan_error" ||
    event === "eval_done" ||
    event === "reeval_done" ||
    event === "cleanup_done"
  ) {
    return { active: false, mode: null, total: 0, completed: 0, current: "", updatedAt: now };
  }
  return previous;
}

export const __wsTest = { emptyProgress, firstNumber, prevTotalFromMessage };

async function waitForBackendReady(port: number, isCurrent: () => boolean) {
  const base = httpBaseFor(port);
  for (let attempt = 0; attempt < READY_ATTEMPTS; attempt += 1) {
    if (!isCurrent()) return false;
    try {
      const response = await fetch(`${base}/health`, { cache: "no-store" });
      if (response.ok) return true;
    } catch {
      // The sidecar prints its port before uvicorn starts accepting connections.
    }
    await delay(READY_RETRY_MS);
  }
  return false;
}

export function useWS() {
  const [conn, setConn] = useState<ConnSt>("disconnected");
  const [port, setPort] = useState<number | null>(null);
  const [apiToken, setApiToken] = useState<string | null>(null);
  const [sidecarError, setSidecarError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [progress, setProgress] = useState<OperationProgress>(() => emptyProgress());
  const wsRef = useRef<WebSocket | null>(null);
  const wsEndpointRef = useRef("");
  const idRef = useRef(0);
  const retryRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const readinessSeqRef = useRef(0);
  const manuallyClosedRef = useRef(false);
  // Set when the reconnect budget is exhausted, so the sidecar poll knows to
  // resume probing instead of staying stuck on "Backend unreachable" forever.
  const forceResyncRef = useRef(false);
  const MAX_RETRY_DELAY = 30000;
  const MAX_RETRIES = 20;

  const addLog = useCallback((msg: string, kind: LogLine["kind"], src = "system", timestamp?: string) => {
    setLogs(previous => {
      if (previous[0]?.msg === msg && previous[0]?.src === src) return previous;
      const persistedTimestamp = timestamp && !timestamp.includes("T")
        ? `${timestamp.replace(" ", "T")}Z`
        : timestamp;
      const parsed = persistedTimestamp ? new Date(persistedTimestamp) : new Date();
      const now = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
      const ts = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
      return [
        { id: idRef.current++, ts, msg, src, kind },
        ...previous.slice(0, 149),
      ];
    });
  }, []);

  const connect = useCallback((p: number, token: string) => {
    const endpoint = `${p}:${token}`;
    const current = wsRef.current;
    if (current && wsEndpointRef.current === endpoint && (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING)) return;
    if (current) {
      current.onclose = null;
      current.close();
    }
    // L4: cancel any pending reconnect timer before (re)connecting so a stale
    // backoff callback can't fire a duplicate connection later.
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    manuallyClosedRef.current = false;
    setConn("connecting");
    // Auth token rides in the Sec-WebSocket-Protocol header (2nd subprotocol),
    // not the URL, so it never lands in logs/history. Server echoes "jhm.bearer".
    const ws = new WebSocket(`${wsBaseFor(p)}/ws`, ["jhm.bearer", token]);
    wsRef.current = ws;
    wsEndpointRef.current = endpoint;
    const reconcileBackendStatus = () => {
      fetch(`${httpBaseFor(p)}/api/v1/scan/result`, {
        cache: "no-store",
        headers: { Authorization: `Bearer ${token}` },
      })
        .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
        .then(status => {
          if (!status?.running) setProgress(emptyProgress());
          window.dispatchEvent(new CustomEvent("backend-status", {
            detail: { scanning: Boolean(status?.running) },
          }));
        })
        .catch(error => {
          const msg = error instanceof Error ? error.message : String(error);
          addLog(`Status reconciliation failed: ${msg}`, "system", "ws");
        });
    };
    ws.onopen    = () => {
      if (wsRef.current !== ws) return;
      setConn("connected");
      retryRef.current = 0;
      addLog("WebSocket connected", "system", "ws");
      // Reconcile on EVERY connect (not only reconnect): a scan already running at
      // app launch (scheduler / persisted sidecar) must be reflected in the UI
      // instead of showing "Ready" until an incidental event happens to arrive.
      reconcileBackendStatus();
    };
    ws.onmessage = (e) => {
      if (wsRef.current !== ws) return;
      try {
        const d = JSON.parse(e.data) as WSMessage;
        const activity = activityEntryFromWSMessage(d);
        if (activity) addLog(activity.msg, activity.kind, activity.src);
        if (d.type === "agent") {
          if (d.event === "scan_start" || d.event === "scan_info") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
            window.dispatchEvent(new CustomEvent("backend-status", { detail: { scanning: true } }));
          }
          if (d.event === "scan_done" || d.event === "scan_stop" || d.event === "scan_error") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
            window.dispatchEvent(new CustomEvent("scan-done"));
            if (d.event === "scan_done") window.dispatchEvent(new CustomEvent("leads-refresh"));
            if (d.event === "scan_error") {
              window.dispatchEvent(new CustomEvent("backend-notice", {
                detail: { level: "warn", msg: d.msg ?? "Search could not be completed." },
              }));
            }
          }
          if (d.event === "eval_start" || d.event === "eval_scored") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
          }
          if (d.event === "eval_done") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
            window.dispatchEvent(new CustomEvent("scan-done"));
          }
          if (d.event === "reeval_start" || d.event === "reeval_scored") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
          }
          if (d.event === "reeval_done") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
            window.dispatchEvent(new CustomEvent("reevaluate-done"));
            window.dispatchEvent(new CustomEvent("leads-refresh"));
          }
          if (d.event === "cleanup_done") {
            setProgress(prev => nextProgressFromAgentEvent(prev, d.event, d.msg));
            window.dispatchEvent(new CustomEvent("cleanup-done"));
            window.dispatchEvent(new CustomEvent("leads-refresh"));
          }
          if (d.event === "auto_discard_done") window.dispatchEvent(new CustomEvent("leads-refresh"));
          if (d.event === "scan_skipped") {
            // Terminate the scan immediately (it returns without eval_done) and
            // tell the user WHY, instead of leaving the spinner stuck for 15 min.
            setProgress(emptyProgress());
            window.dispatchEvent(new CustomEvent("scan-done"));
            window.dispatchEvent(new CustomEvent("backend-notice", { detail: { level: "warn", msg: d.msg ?? "Scan skipped: add a target role or a job source first." } }));
          }
          // Surface degraded/notable outcomes prominently, not only in the log:
          // LLM-fallback scoring, an empty scout, and feedback re-ranking.
          if (d.event === "eval_fallback_summary" || d.event === "eval_prefilter_summary") {
            window.dispatchEvent(new CustomEvent("backend-notice", { detail: { level: "warn", msg: d.msg ?? "Scoring ran degraded." } }));
          }
          if (d.event === "feedback_relearn") {
            window.dispatchEvent(new CustomEvent("backend-notice", { detail: { level: "info", msg: d.msg ?? "Re-ranked leads from your feedback." } }));
          }
          if (d.event === "scout_done" && typeof d.msg === "string" && /-\s*0 new leads/.test(d.msg)) {
            window.dispatchEvent(new CustomEvent("backend-notice", { detail: { level: "warn", msg: d.msg } }));
          }
        } else if (d.type === "LEAD_UPDATED" && d.data) {
          window.dispatchEvent(new CustomEvent("lead-updated", { detail: d.data }));
        } else if (d.type === "HOT_X_LEAD" && d.data) {
          window.dispatchEvent(new CustomEvent("hot-x-lead", { detail: d.data }));
          if ("Notification" in window && Notification.permission === "granted") {
            const lead = d.data as Lead;
            new Notification("Hot X lead", { body: `${lead.company}: ${lead.title}` });
          }
        } else if (d.type === "LEADS_REFRESH") {
          window.dispatchEvent(new CustomEvent("leads-refresh"));
        }
      } catch (err) {
        const preview = typeof e.data === "string" ? e.data.slice(0, 200) : "";
        console.warn("[WS] Failed to parse message:", err, preview);
        addLog(`Message parse error: ${err}`, "system", "ws");
      }
    };
    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      setConn("disconnected");
      wsRef.current = null;
      wsEndpointRef.current = "";
      if (manuallyClosedRef.current) return;
      if (retryRef.current >= MAX_RETRIES) {
        setSidecarError("Backend unreachable. Restart GalaxyHire or check the backend process.");
        setPort(null);
        setApiToken(null);
        addLog("Backend unreachable after repeated WebSocket reconnect attempts", "system", "ws");
        // Re-arm so the sidecar poll resumes probing; if the backend recovers
        // (or relaunches on a new port) we reconnect instead of staying dead
        // until the app is restarted.
        retryRef.current = 0;
        forceResyncRef.current = true;
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, retryRef.current), MAX_RETRY_DELAY);
      const jitter = delay * (0.5 + Math.random() * 0.5);
      retryRef.current += 1;
      retryTimerRef.current = window.setTimeout(() => connect(p, token), jitter);
    };
    ws.onerror = () => ws.close();
  }, [addLog]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    let poll: number | undefined;
    (async () => {
      let token: string | null = null;
      let currentPort: number | null = null;
      let backendReady = false;
      let pendingEndpoint = "";
      let publishedEndpoint = "";
      const publishReadyBackend = async (p: number, t: string) => {
        const endpoint = `${p}:${t}`;
        if (backendReady && publishedEndpoint === endpoint) return;
        if (pendingEndpoint === endpoint) return;
        pendingEndpoint = endpoint;
        const seq = ++readinessSeqRef.current;
        setConn("connecting");
        const ready = await waitForBackendReady(p, () => !cancelled && readinessSeqRef.current === seq);
        if (pendingEndpoint === endpoint) pendingEndpoint = "";
        if (!ready || cancelled || readinessSeqRef.current !== seq) {
          if (!cancelled && readinessSeqRef.current === seq) {
            backendReady = false;
            setPort(null);
            setApiToken(null);
            setSidecarError(`Backend did not become ready on port ${p}.`);
          }
          return;
        }
        backendReady = true;
        publishedEndpoint = endpoint;
        setSidecarError(null);
        setApiToken(t);
        setPort(p);
        connect(p, t);
      };
      const maybePublish = () => {
        if (token && currentPort && publishedEndpoint !== `${currentPort}:${token}`) {
          backendReady = false;
        }
        if (token && currentPort) void publishReadyBackend(currentPort, token);
      };
      // Endpoint discovery is the biggest difference between the two shells, so it lives behind
      // the platform layer (see shared/lib/platform). On the desktop this is the Tauri sidecar
      // handshake — dynamic port, IPC events, restarts mid-session. On the web the API serves this
      // page, so the endpoint is same-origin and known at once. Everything below the callback is
      // identical either way, which is the point of routing both through one interface.
      const active = await platform();

      const onEndpoint = (endpoint: { port: number; token: string } | null) => {
        if (cancelled) return;
        if (endpoint === null) {
          // The backend went away rather than merely changing. Drop the connection instead of
          // retrying against a dead port.
          readinessSeqRef.current += 1;
          currentPort = null;
          token = null;
          backendReady = false;
          pendingEndpoint = "";
          publishedEndpoint = "";
          setPort(null);
          setApiToken(null);
          setConn("disconnected");
          setProgress(emptyProgress());
          window.dispatchEvent(new CustomEvent("backend-status", { detail: { scanning: false, reevaluating: false } }));
          addLog("Backend terminated", "system", "sidecar");
          return;
        }
        currentPort = endpoint.port;
        token = endpoint.token;
        maybePublish();
      };

      const stopWatching = active.watchBackend(onEndpoint);
      unlisten = stopWatching;

      const err = await active.getBackendError();
      if (err && !cancelled) {
        setSidecarError(err);
        addLog(err, "system", "sidecar");
      }

      // Re-publish after an exhausted reconnect budget. The watcher only fires when the endpoint
      // *changes*, so a backend that comes back on the same port would otherwise never reconnect.
      poll = window.setInterval(() => {
        if (cancelled) return;
        if (forceResyncRef.current) {
          forceResyncRef.current = false;
          backendReady = false;
          publishedEndpoint = "";
          if (token && currentPort) void publishReadyBackend(currentPort, token);
        }
      }, 1000);
    })();
    return () => {
      cancelled = true;
      readinessSeqRef.current += 1;
      if (poll !== undefined) window.clearInterval(poll);
      unlisten?.();
      manuallyClosedRef.current = true;
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (wsRef.current) wsRef.current.onclose = null;
      wsRef.current?.close();
    };
  }, [connect]);

  const resetProgress = useCallback(() => setProgress(emptyProgress()), []);

  return { conn, port, apiToken, sidecarError, logs, addLog, progress, resetProgress };
}
