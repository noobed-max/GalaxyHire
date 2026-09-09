import { useMemo, useState } from "react";
import type { LogLine } from "../../types";

type ActivityTab = "all" | "scout" | "eval" | "customize" | "system";

function visibleForTab(logs: LogLine[], tab: ActivityTab) {
  return logs.filter(l => {
    const message = l.msg.toLowerCase();
    if (tab === "all") return l.kind !== "heartbeat" && l.src !== "ws" && l.src !== "hb";
    if (tab === "scout") return ["search", "scout", "jobs"].includes(l.src) || message.includes("search") || message.includes("collect");
    if (tab === "eval") return ["matching", "eval"].includes(l.src) || message.includes("match") || message.includes("scor");
    if (tab === "customize") return ["documents", "profile", "apply"].includes(l.src) || message.includes("generat") || message.includes("profile import");
    if (tab === "system") return l.kind === "system";
    return true;
  });
}

const SOURCE_LABELS: Record<string, string> = {
  search: "Job search",
  scout: "Job search",
  jobs: "Jobs",
  matching: "Matching",
  eval: "Matching",
  documents: "Documents",
  apply: "Documents",
  profile: "Profile",
  workflow: "Workflow",
  ws: "Connection",
  sidecar: "Backend",
  system: "System",
};

export function ActivityView({ logs }: { logs: LogLine[] }) {
  const [actTab, setActTab] = useState<ActivityTab>("all");
  const [copied, setCopied] = useState(false);
  const visibleLogs = useMemo(() => visibleForTab(logs, actTab), [actTab, logs]);

  const copyThinking = async () => {
    const body = visibleLogs
      .map(ln => `[${ln.ts}] ${ln.kind.toUpperCase()} ${ln.src}: ${ln.msg}`)
      .join("\n");
    const text = body || "No activity is visible.";
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="scroll" style={{ padding: 24, flex: 1, height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
        {(["all", "scout", "eval", "customize", "system"] as const).map(tab => (
          <button key={tab} onClick={() => setActTab(tab)} style={{
            padding: "5px 14px", borderRadius: 999, fontSize: 11, fontWeight: 700,
            letterSpacing: "0.1em", textTransform: "uppercase", cursor: "pointer",
            border: actTab === tab ? "none" : "1px solid var(--line)",
            background: actTab === tab ? "var(--ink)" : "var(--paper)",
            color: actTab === tab ? "var(--card)" : "var(--ink-3)",
            transition: "all 0.15s ease",
          }}>
            {tab === "all" ? "All" : tab === "scout" ? "Job search" : tab === "eval" ? "Matching" : tab === "customize" ? "Documents" : "System"}
          </button>
        ))}
      </div>
      <div className="card" style={{ padding: "24px 28px", marginBottom: 18, background: "var(--orange-soft)" }}>
        <span className="eyebrow">Work history</span>
        <h1 style={{ fontSize: 40 }}>GalaxyHire <span className="italic-serif">activity.</span></h1>
        <p style={{ marginTop: 7, color: "var(--ink-2)", maxWidth: 680 }}>
          Follow job searches, matching, profile imports, and document generation. Connection details stay under System.
        </p>
      </div>
      <div className="card" style={{ padding: 18, background: "var(--purple-soft)" }}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <h3>Recent events <span style={{ color: "var(--ink-3)", fontWeight: 500 }}>({visibleLogs.length})</span></h3>
          <div className="row gap-2">
            <button className="btn btn-ghost" onClick={copyThinking} style={{ fontSize: 12 }}>
              {copied ? "Copied" : "Copy activity"}
            </button>
            <span className="pill" style={{ background: "var(--green)", color: "var(--green-ink)" }}>
              <span className="dot pulse-soft" /> live
            </span>
          </div>
        </div>
        <div style={{ height: 440, display: "flex" }}>
          <div className="scroll terminal" style={{ background: "var(--term-bg)", color: "var(--term-fg)", borderRadius: 12, padding: "14px 16px", flex: 1 }}>
            {visibleLogs.length === 0 && (
              <div style={{ color: "var(--term-mid)", padding: "22px 4px", lineHeight: 1.6 }}>
                No activity in this category yet. Start a job search, import a résumé, or generate application documents to see progress here.
              </div>
            )}
            {visibleLogs.map(ln => {
              const failed = /failed|error|rejected|unreachable/i.test(ln.msg);
              const tone = failed ? "orange" : ln.kind === "agent" ? "green" : "blue";
              return (
                <div key={ln.id} className="row gap-3" style={{ marginBottom: 5, alignItems: "baseline" }}>
                  <span className="mono tabular" style={{ color: "var(--term-dim)", fontSize: 10.5, minWidth: 68 }}>{ln.ts}</span>
                  <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", padding: "1px 6px", borderRadius: 4, background: `var(--${tone})`, color: `var(--${tone}-ink)`, minWidth: 74, textAlign: "center" }}>{failed ? "Attention" : ln.kind === "agent" ? "Activity" : "System"}</span>
                  <span style={{ color: "var(--term-mid)", fontSize: 11, minWidth: 78 }}>{SOURCE_LABELS[ln.src] || ln.src}</span>
                  <span style={{ flex: 1, lineHeight: 1.45 }}>{ln.msg}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
