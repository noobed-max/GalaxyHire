import { useEffect, useRef, useState } from "react";
import Icon from "../../shared/components/Icon";
import type { ApiFetch } from "../../types";

export interface ResumeTemplate {
  id: string;
  name: string;
  source_filename: string;
  is_default: boolean;
  created_at: string;
  char_count: number;
  preview: string;
}

const FORMAT_META: Record<string, { label: string; tone: string }> = {
  pdf: { label: "PDF", tone: "red" },
  docx: { label: "DOCX", tone: "blue" },
  doc: { label: "DOC", tone: "blue" },
  txt: { label: "TXT", tone: "gray" },
  md: { label: "MD", tone: "purple" },
};

const ACCEPTED = ".pdf,.docx,.txt,.md";

function formatOf(filename: string): { label: string; tone: string } {
  const ext = filename.toLowerCase().split(".").pop() || "";
  return FORMAT_META[ext] || { label: (ext || "TXT").slice(0, 4).toUpperCase(), tone: "gray" };
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function ResumeTemplatesPanel({ api }: { api: ApiFetch }) {
  const [templates, setTemplates] = useState<ResumeTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api("/api/v1/templates");
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Could not load templates");
      setTemplates(body.templates || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load templates");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [api]);

  const upload = async (file: File) => {
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("make_default", templates.length === 0 ? "true" : "false");
      const res = await api("/api/v1/templates/upload", { method: "POST", body: form, timeoutMs: 60000 });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Upload failed (${res.status})`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLoading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const act = async (id: string, run: () => Promise<Response>) => {
    setBusyId(id);
    setError(null);
    try {
      const res = await run();
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusyId("");
    }
  };

  const setDefault = (id: string) => act(id, () => api(`/api/v1/templates/${id}/default`, { method: "POST" }));
  const remove = (id: string) => act(id, () => api(`/api/v1/templates/${id}`, { method: "DELETE" }));

  return (
    <div className="rt-panel">
      <div className="rt-head">
        <div className="rt-title-row">
          <span className="rt-badge"><Icon name="file" size={17} /></span>
          <div>
            <div className="rt-title">Resume Templates</div>
            <p className="rt-sub">
              Upload your own resumes as reusable style guides — the generator mimics the one you pick per job.
            </p>
          </div>
        </div>
        <span className={`rt-count ${templates.length ? "" : "empty"}`}>
          {templates.length} saved
        </span>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept={ACCEPTED}
        style={{ display: "none" }}
        onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); }}
      />

      <button className="rt-upload" disabled={loading} onClick={() => fileRef.current?.click()}>
        <span className="rt-upload-icon">
          {loading ? <span className="spinner-sm" aria-hidden="true" /> : <Icon name="upload" size={18} />}
        </span>
        <span className="rt-upload-copy">
          <strong>{loading ? "Working…" : templates.length === 0 ? "Upload your first resume template" : "Upload resume template"}</strong>
          <span className="rt-upload-hint">Click to browse — PDF, DOCX, TXT or MD</span>
        </span>
        <span className="rt-upload-formats">
          {["pdf", "docx", "txt", "md"].map(ext => (
            <span key={ext} className="pill mono">{FORMAT_META[ext].label}</span>
          ))}
        </span>
      </button>

      {error && (
        <div className="rt-error" role="alert">
          <Icon name="alert-circle" size={14} />
          <span>{error}</span>
        </div>
      )}

      {templates.length === 0 && !loading && !error && (
        <div className="rt-empty">
          <Icon name="file" size={20} />
          <strong>No templates yet</strong>
          <span>Upload a resume you like — it becomes the default style for generated resumes until you add more.</span>
        </div>
      )}

      {templates.length > 0 && (
        <div className="rt-list">
          {templates.map(t => {
            const meta = formatOf(t.source_filename || t.name);
            const busy = busyId === t.id;
            return (
              <article key={t.id} className={`rt-card ${t.is_default ? "default" : ""}`}>
                <span className={`rt-file-tile tone-${meta.tone}`}>{meta.label}</span>

                <div className="rt-card-body">
                  <div className="rt-card-name-row">
                    <span className="rt-card-name">{t.name}</span>
                    {t.is_default ? (
                      <span className="rt-default-chip"><Icon name="star" size={9} /> Default</span>
                    ) : null}
                  </div>
                  <div className="rt-meta">
                    {[t.source_filename || "Pasted text", `${t.char_count.toLocaleString()} chars`, formatDate(t.created_at)]
                      .filter(Boolean)
                      .join("  ·  ")}
                  </div>
                  {t.preview && <div className="rt-preview">{t.preview}</div>}
                </div>

                <div className="rt-actions">
                  {!t.is_default && (
                    <button className="btn" disabled={busy} onClick={() => setDefault(t.id)} title="Use this template by default">
                      <Icon name="star" size={12} /> Set default
                    </button>
                  )}
                  <button className="btn rt-delete" disabled={busy} onClick={() => remove(t.id)} title="Delete this template">
                    <Icon name="trash" size={12} /> Delete
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
