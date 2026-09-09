import { useCallback, useEffect, useState } from "react";
import type { ApiFetch } from "../../types";

/**
 * Miscellaneous user data — the facts that belong on application forms but not on a
 * resume (visa, citizenship, military service, EEO answers, notice period, ...).
 *
 * Update semantics, not accumulation: ONE record per user. Every paste/upload is merged
 * with the previous record by the AI into a single comprehensive text, which is what the
 * extension reads when filling forms. The previous record is never lost to a failed merge
 * (the backend surfaces ingest_error and keeps it).
 */
export function MiscCard({ api }: { api: ApiFetch }) {
  const [text, setText] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api("/api/v1/misc-context")
      .then(r => r.json())
      .then(d => {
        setText(String(d?.text || ""));
        setUpdatedAt(String(d?.updated_at || ""));
        if (d?.ingest_error) setError(String(d.ingest_error));
      })
      .catch(() => {});
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const submit = async (file?: File | null) => {
    if (!draft.trim() && !file) return;
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("text", draft);
      if (file) fd.append("file", file);
      const r = await api("/api/v1/misc-context", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `Server returned ${r.status}`);
      setText(String(d?.text || ""));
      setUpdatedAt(String(d?.updated_at || ""));
      setDraft("");
      if (d?.ingest_error) setError(String(d.ingest_error));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Merge failed — previous data kept.");
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!window.confirm("Clear the miscellaneous data box? This cannot be undone.")) return;
    setBusy(true);
    try {
      await api("/api/v1/misc-context", { method: "DELETE" });
      setText("");
      setUpdatedAt("");
      setError(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card col gap-3" style={{ padding: 20 }}>
      <div>
        <span className="gh-kicker">Miscellaneous user data</span>
        <h3 style={{ margin: "2px 0 0" }}>Facts for forms, not for resumes</h3>
        <p style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 4, lineHeight: 1.5 }}>
          Visa, citizenship, military service, EEO answers, notice period — anything a portal asks
          that your resume doesn't say. One evolving record: each addition is merged with what's
          here, and the extension reads it when filling applications.
          {updatedAt && <span className="mono"> · updated {String(updatedAt).slice(0, 10)}</span>}
        </p>
      </div>

      {text ? (
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6, background: "var(--paper-3)", borderRadius: 8, padding: "10px 12px", border: "1px solid var(--line)", whiteSpace: "pre-wrap", maxHeight: 220, overflowY: "auto" }}>
          {text}
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: "var(--ink-4)", fontStyle: "italic" }}>Nothing stored yet — paste facts or upload a file below.</div>
      )}

      <textarea
        value={draft}
        onChange={e => setDraft(e.target.value)}
        rows={3}
        placeholder="e.g. UK citizen, no visa required · Veteran, honourable discharge 2019 · 1 month notice period"
        aria-label="New miscellaneous facts to merge"
        style={{ width: "100%" }}
      />
      <div className="row gap-2" style={{ flexWrap: "wrap" }}>
        <input type="file" accept=".pdf,.docx,.txt,.md" style={{ display: "none" }} id="misc-file-in"
          onChange={e => { const f = e.target.files?.[0]; if (f) void submit(f); e.target.value = ""; }} />
        <button className="btn btn-primary" disabled={busy || !draft.trim()} onClick={() => void submit()}>
          {busy ? "Merging…" : "Merge these facts in"}
        </button>
        <button className="btn" disabled={busy} onClick={() => document.getElementById("misc-file-in")?.click()}>
          Upload file
        </button>
        {text && (
          <button className="btn btn-ghost" disabled={busy} onClick={() => void clear()}>
            Clear
          </button>
        )}
      </div>
      {busy && <div className="mono pulse" style={{ fontSize: 12 }}>Merging with AI…</div>}
      {error && <div className="gh-inline-error">{error}</div>}
    </div>
  );
}
