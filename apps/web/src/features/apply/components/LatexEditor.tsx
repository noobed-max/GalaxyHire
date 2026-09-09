import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiFetch } from "../../../api/types";

/**
 * In-UI LaTeX editor (§5).
 *
 * The brief asks for two things the other document views don't provide: the `.tex` download, and a
 * place to "fix errors or make edits before using it". Fixing errors requires *seeing* them, so
 * preview compiles the source and shows the LaTeX log inline when it fails.
 *
 * Two deliberate behaviours:
 *
 *  - **Download never depends on compilation.** Most machines have no TeX install, and the `.tex`
 *    file is useful regardless — the user can compile it on Overleaf or anywhere else. So preview is
 *    hidden when no engine exists, while download stays available.
 *  - **Edits are never silently discarded.** The source is generated from the user's profile, but
 *    once they have typed in this box it is *their* text. Regenerating replaces it, so that is an
 *    explicit action with a confirmation rather than something that happens on re-render.
 */

interface Props {
  api: ApiFetch;
  jobId: string;
  /** Shown in the download filename. */
  jobLabel?: string;
}

interface CompileState {
  status: "idle" | "compiling" | "ok" | "error";
  pdfUrl?: string;
  error?: string;
  log?: string;
}

export function LatexEditor({ api, jobId, jobLabel }: Props) {
  const [source, setSource] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [compile, setCompile] = useState<CompileState>({ status: "idle" });
  const [engine, setEngine] = useState<{ available: boolean; engine?: string | null } | null>(null);
  // Tracked so the blob URL from a previous preview can be revoked; leaking them holds the whole
  // PDF in memory for the lifetime of the page.
  const pdfUrlRef = useRef<string | null>(null);

  const revokePdf = useCallback(() => {
    if (pdfUrlRef.current) {
      URL.revokeObjectURL(pdfUrlRef.current);
      pdfUrlRef.current = null;
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await api(`/api/v1/leads/${encodeURIComponent(jobId)}/resume.tex`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const body = (await res.json()) as { source?: string };
      setSource(body.source ?? "");
      setDirty(false);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [api, jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await api("/api/v1/latex/available");
        if (!res.ok) return;
        const body = (await res.json()) as { available: boolean; engine?: string | null };
        if (alive) setEngine(body);
      } catch {
        if (alive) setEngine({ available: false });
      }
    })();
    return () => {
      alive = false;
    };
  }, [api]);

  useEffect(() => revokePdf, [revokePdf]);

  const download = () => {
    const blob = new Blob([source], { type: "application/x-tex" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const slug = (jobLabel || jobId).toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40);
    a.download = `resume-${slug || "tailored"}.tex`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const preview = async () => {
    setCompile({ status: "compiling" });
    try {
      const res = await api("/api/v1/latex/compile", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source }),
        // A cold TeX run can take a while on first use, well past the default request timeout.
        timeoutMs: 120_000,
      });
      const body = (await res.json()) as { ok: boolean; pdf_base64?: string; error?: string; log?: string };
      if (!body.ok || !body.pdf_base64) {
        setCompile({ status: "error", error: body.error ?? "Compilation failed.", log: body.log });
        return;
      }
      const bytes = Uint8Array.from(atob(body.pdf_base64), c => c.charCodeAt(0));
      revokePdf();
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      pdfUrlRef.current = url;
      setCompile({ status: "ok", pdfUrl: url });
    } catch (err) {
      setCompile({ status: "error", error: err instanceof Error ? err.message : String(err) });
    }
  };

  const regenerate = () => {
    // Only confirm when there is something to lose. Prompting over an untouched buffer trains
    // people to click through the dialog that matters.
    if (dirty && !window.confirm("Discard your edits and regenerate from your profile?")) return;
    revokePdf();
    setCompile({ status: "idle" });
    void load();
  };

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 520 }}>
      <div
        className="row"
        style={{
          padding: 11,
          borderBottom: "1px solid var(--line)",
          background: "var(--paper-3)",
          justifyContent: "space-between",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <div className="row gap-2">
          <span style={{ fontSize: 13, fontWeight: 800 }}>LaTeX source</span>
          {dirty && <span className="eyebrow" style={{ color: "var(--warn)" }}>edited</span>}
        </div>
        <div className="row gap-2">
          <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 9px" }} onClick={regenerate} disabled={loading}>
            Regenerate
          </button>
          {engine?.available && (
            <button
              className="btn btn-ghost"
              style={{ fontSize: 11, padding: "4px 9px" }}
              onClick={() => void preview()}
              disabled={loading || compile.status === "compiling" || !source.trim()}
            >
              {compile.status === "compiling" ? "Compiling..." : "Preview PDF"}
            </button>
          )}
          <button
            className="btn"
            style={{ fontSize: 11, padding: "4px 9px" }}
            onClick={download}
            disabled={loading || !source.trim()}
          >
            Download .tex
          </button>
        </div>
      </div>

      {loadError && (
        <div style={{ padding: 12, color: "var(--bad)", fontSize: 12 }}>
          Could not load LaTeX source: {loadError}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: compile.pdfUrl ? "1fr 1fr" : "1fr" }}>
        <textarea
          value={loading ? "Loading..." : source}
          onChange={e => {
            setSource(e.target.value);
            setDirty(true);
          }}
          readOnly={loading}
          spellCheck={false}
          className="mono"
          style={{
            width: "100%",
            height: "100%",
            minHeight: 420,
            border: "none",
            borderRight: compile.pdfUrl ? "1px solid var(--line)" : "none",
            resize: "none",
            padding: 12,
            fontSize: 12,
            lineHeight: 1.5,
            background: "var(--paper)",
            color: "var(--ink-1)",
          }}
        />
        {compile.pdfUrl && (
          <iframe key={compile.pdfUrl} src={compile.pdfUrl} title="Compiled résumé" style={{ border: "none", width: "100%", height: "100%", minHeight: 420 }} />
        )}
      </div>

      {compile.status === "error" && (
        <div style={{ borderTop: "1px solid var(--line)", background: "var(--paper-3)", padding: 12 }}>
          <div style={{ color: "var(--bad)", fontSize: 12, fontWeight: 700, marginBottom: 6 }}>{compile.error}</div>
          {compile.log && (
            // The log is pre-filtered server-side to the `!` error lines and their line markers;
            // a raw LaTeX log is hundreds of lines of package chatter and useless in a panel.
            <pre className="mono" style={{ fontSize: 11, lineHeight: 1.45, maxHeight: 160, overflow: "auto", margin: 0, color: "var(--ink-2)" }}>
              {compile.log}
            </pre>
          )}
        </div>
      )}

      {engine && !engine.available && (
        <div style={{ borderTop: "1px solid var(--line)", padding: "8px 12px", fontSize: 11, color: "var(--ink-3)" }}>
          No local LaTeX engine, so preview is unavailable — download the .tex and compile it anywhere
          (Overleaf, TeX Live, MiKTeX).
        </div>
      )}
    </div>
  );
}
