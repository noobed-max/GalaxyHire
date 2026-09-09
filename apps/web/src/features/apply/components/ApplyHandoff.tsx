import { useState } from "react";

import type { ApiFetch } from "../../../api/types";
import { openExternalUrl } from "../../../shared/lib/openExternal";

/**
 * The §6 hand-off: save the résumé, open the portal, let the extension take over.
 *
 * One click does three things the user would otherwise do by hand — writes the tailored résumé (PDF
 * plus the editable `.tex`) into their chosen folder, opens the application page, and leaves a
 * prepared fill context the extension picks up for that tab.
 *
 * What it deliberately does *not* do is mark the job applied. Opening a form is not submitting one:
 * the user may look at the page and walk away, and a pipeline that already claims "applied" is worse
 * than one that asks. So confirming is a separate, explicit button — the UI counterpart of the
 * extension's never-submit guard.
 */

interface Props {
  api: ApiFetch;
  jobId: string;
  /** Set once the lead is already marked applied, so the confirm button doesn't reappear. */
  alreadyApplied?: boolean;
}

interface Prepared {
  applyUrl: string | null;
  pdfPath?: string;
  texPath?: string;
}

export function ApplyHandoff({ api, jobId, alreadyApplied }: Props) {
  const [busy, setBusy] = useState<"idle" | "preparing" | "confirming">("idle");
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [applied, setApplied] = useState(Boolean(alreadyApplied));
  const [error, setError] = useState<string | null>(null);

  const prepareAndOpen = async () => {
    setBusy("preparing");
    setError(null);
    try {
      const res = await api(`/api/v1/leads/${encodeURIComponent(jobId)}/apply`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ check_live: true }),
        // Tailoring, rendering and writing to disk together take longer than a normal request.
        timeoutMs: 90_000,
      });
      if (res.status === 409) {
        setError("That posting is no longer open, so nothing was prepared.");
        return;
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const body = (await res.json()) as {
        apply_url?: string | null;
        resume_paths?: { pdf?: string; tex?: string };
      };
      setPrepared({
        applyUrl: body.apply_url ?? null,
        pdfPath: body.resume_paths?.pdf,
        texPath: body.resume_paths?.tex,
      });
      if (body.apply_url) {
        // Opened after the résumé is on disk and the fill context is stored, so the extension has
        // something to find the moment the tab loads.
        await openExternalUrl(body.apply_url);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("idle");
    }
  };

  const confirmApplied = async () => {
    setBusy("confirming");
    setError(null);
    try {
      const res = await api(`/api/v1/leads/${encodeURIComponent(jobId)}/applied`, { method: "POST" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setApplied(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("idle");
    }
  };

  return (
    <div className="card" style={{ padding: 14, display: "grid", gap: 10 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800 }}>Open the application</div>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
            GalaxyHire saves your résumé, opens the job site, and makes the details available to the
            browser extension. You review each page and press Submit yourself.
          </div>
        </div>
        <div className="row gap-2">
          <button className="btn" onClick={() => void prepareAndOpen()} disabled={busy !== "idle"}>
            {busy === "preparing" ? "Preparing…" : "Save résumé & open job"}
          </button>
          {prepared && !applied && (
            <button
              className="btn btn-ghost"
              onClick={() => void confirmApplied()}
              disabled={busy !== "idle"}
              title="Only you can confirm this — nothing submits on your behalf"
            >
              {busy === "confirming" ? "Saving..." : "I submitted it"}
            </button>
          )}
          {applied && <span className="eyebrow" style={{ color: "var(--ok)" }}>applied</span>}
        </div>
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--bad)" }}>{error}</div>}

      {prepared && (
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", display: "grid", gap: 3 }}>
          {prepared.pdfPath && (
            <span className="mono" style={{ wordBreak: "break-all" }}>PDF: {prepared.pdfPath}</span>
          )}
          {prepared.texPath && (
            <span className="mono" style={{ wordBreak: "break-all" }}>LaTeX: {prepared.texPath}</span>
          )}
          {!prepared.applyUrl && <span>No application URL on this job — open it manually.</span>}
        </div>
      )}
    </div>
  );
}
