import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import Icon from "../../shared/components/Icon";
import type { ApiFetch } from "../../types";
import { useAppContext, STAGE_LABELS, STAGE_KEY_TO_NUMBER } from "../../context/AppContext";
import type { IngestionJob, IngestionStatus, IngestionStatusResponse } from "../../api/types";
import { MiscCard } from "./MiscCard";
import { DuplicateResolutionModal } from "./components/DuplicateResolutionModal";

async function responseErrorMessage(response: Response, fallback: string) {
  // M3: surface rate-limit cooldown using the standard Retry-After header so
  // the user sees "Please wait X seconds" instead of a generic 429 error.
  if (response.status === 429) {
    const retryAfter = Number(response.headers.get("Retry-After"));
    if (Number.isFinite(retryAfter) && retryAfter > 0) {
      return `Too many requests. Please wait ${retryAfter} second${retryAfter === 1 ? "" : "s"} and try again.`;
    }
  }
  try {
    const data = await response.clone().json();
    const detail = data?.detail ?? data?.error;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail) return JSON.stringify(detail);
  } catch {
    // Fall through to text/plain error bodies.
  }
  try {
    const text = await response.text();
    if (text.trim()) return text;
  } catch {
    // Fall through to the caller-provided fallback.
  }
  return fallback;
}

function requestErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

const INGESTION_STAGES = [
  { step: 1 as const, label: "Reading document & extracting text..." },
  { step: 2 as const, label: "Analyzing experiences & projects with AI..." },
  { step: 3 as const, label: "Cross-referencing existing profile for duplicates..." },
  { step: 4 as const, label: "Indexing skills and tags..." },
];

export function IngestionStepper({ stage, status }: { stage: 1 | 2 | 3 | 4; status: IngestionStatus }) {
  return (
    <div className="col gap-2" style={{ margin: "14px 0", width: "100%" }}>
      {INGESTION_STAGES.map(s => {
        const isCompleted = status === "completed" || status === "review_needed" || status === "review_required" || s.step < stage;
        const isActive = (status === "processing" || status === "review_needed" || status === "review_required") && s.step === stage;
        const isFailed = status === "failed" && s.step === stage;
        const isPending = !isCompleted && !isActive && !isFailed;

        return (
          <div
            key={s.step}
            className="row items-center gap-3"
            style={{
              padding: "7px 12px",
              background: isActive ? "var(--teal-soft)" : isCompleted ? "var(--paper-2)" : "transparent",
              border: isActive ? "1.5px solid var(--hard)" : "1px solid transparent",
              borderRadius: 4,
            }}
          >
            <div style={{ width: 26, height: 26, display: "grid", placeItems: "center", flexShrink: 0 }}>
              {isCompleted && (
                <span
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    background: "var(--teal-soft)",
                    border: "2px solid var(--teal)",
                    display: "grid",
                    placeItems: "center",
                  }}
                  title="Completed"
                >
                  <Icon name="check" size={14} color="var(--teal)" stroke={2.5} />
                </span>
              )}
              {isActive && (
                <span
                  className="pulse-soft"
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    background: "var(--teal-soft)",
                    border: "2px solid var(--hard)",
                    boxShadow: "0 0 0 4px rgba(0, 166, 166, 0.35)",
                    display: "grid",
                    placeItems: "center",
                  }}
                  title="In progress"
                >
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--teal)" }} />
                </span>
              )}
              {isFailed && (
                <span
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    background: "var(--bad-soft)",
                    border: "2px solid var(--bad)",
                    display: "grid",
                    placeItems: "center",
                  }}
                  title="Failed"
                >
                  <Icon name="x" size={14} color="var(--bad)" stroke={2.5} />
                </span>
              )}
              {isPending && (
                <span
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    border: "1.5px dashed var(--ink-4)",
                    display: "grid",
                    placeItems: "center",
                    background: "var(--paper-2)",
                  }}
                  title="Pending"
                >
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ink-4)" }} />
                </span>
              )}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: isActive ? 700 : isCompleted ? 600 : 400,
                  color: isFailed ? "var(--bad)" : isPending ? "var(--ink-4)" : "var(--ink)",
                }}
              >
                {s.label}
              </span>
            </div>

            <div style={{ flexShrink: 0 }}>
              {isCompleted && (
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    padding: "2px 8px",
                    background: "var(--teal-soft)",
                    color: "var(--teal-ink)",
                    border: "1px solid var(--teal)",
                    borderRadius: 3,
                  }}
                >
                  Done
                </span>
              )}
              {isActive && (
                <span
                  className="mono pulse-soft"
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "2px 8px",
                    background: "var(--yellow)",
                    color: "var(--ink)",
                    border: "1.5px solid var(--hard)",
                    borderRadius: 3,
                  }}
                >
                  Active
                </span>
              )}
              {isFailed && (
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    padding: "2px 8px",
                    background: "var(--bad-soft)",
                    color: "var(--bad)",
                    border: "1px solid var(--bad)",
                    borderRadius: 3,
                  }}
                >
                  Error
                </span>
              )}
              {isPending && (
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    background: "var(--paper-3)",
                    color: "var(--ink-4)",
                    borderRadius: 3,
                  }}
                >
                  Pending
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ThoughtPreviewBox({ thoughts, isProcessing }: { thoughts: string[]; isProcessing: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current && !collapsed) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [thoughts.length, collapsed]);

  return (
    <div
      className="card col"
      style={{
        marginTop: 10,
        border: "1.5px solid var(--hard)",
        background: "var(--paper)",
        overflow: "hidden",
      }}
    >
      <div
        className="row items-center justify-between"
        style={{
          padding: "8px 14px",
          background: "var(--paper-3)",
          borderBottom: collapsed ? "none" : "1.5px solid var(--hard)",
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setCollapsed(prev => !prev)}
      >
        <div className="row items-center gap-2">
          <span style={{ fontSize: 12 }}>{collapsed ? "▶" : "▼"}</span>
          <span style={{ fontWeight: 600, fontSize: 12.5 }}>AI Reasoning & Extraction Log</span>
          {isProcessing && (
            <span
              className="pulse-soft mono"
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                color: "var(--teal-ink)",
                background: "var(--teal-soft)",
                padding: "1px 6px",
                borderRadius: 3,
                border: "1px solid var(--teal)",
              }}
            >
              STREAMING
            </span>
          )}
        </div>
        <div className="row items-center gap-2">
          <span
            className="mono"
            style={{
              fontSize: 11,
              padding: "2px 8px",
              background: "var(--paper-2)",
              border: "1px solid var(--hard)",
              borderRadius: 3,
              color: "var(--ink-2)",
            }}
          >
            {`[ ${thoughts.length} line${thoughts.length === 1 ? "" : "s"} ]`}
          </span>
        </div>
      </div>

      {!collapsed && (
        <div
          ref={scrollRef}
          className="mono scroll"
          style={{
            fontFamily: "var(--mono), var(--font-mono), monospace",
            fontSize: "11.5px",
            lineHeight: "1.6",
            padding: "12px 14px",
            maxHeight: "180px",
            overflowY: "auto",
            background: "var(--paper-1, var(--paper))",
            color: "var(--ink)",
          }}
        >
          {thoughts.length === 0 ? (
            <div style={{ color: "var(--ink-4)", fontStyle: "italic" }}>Awaiting reasoning tokens...</div>
          ) : (
            thoughts.map((thought, idx) => (
              <div key={idx} className="row" style={{ gap: 12, marginBottom: 2 }}>
                <span style={{ color: "var(--ink-4)", minWidth: 24, textAlign: "right", userSelect: "none" }}>
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <span style={{ flex: 1, wordBreak: "break-word" }}>{thought}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function IngestionProgressCard({
  job,
  onCancel,
  onDismiss,
  onReviewDuplicates,
}: {
  job: IngestionJob;
  onCancel: (taskId: string) => void;
  onDismiss: () => void;
  onReviewDuplicates?: () => void;
}) {
  const needsReview = job.status === "review_needed" || job.status === "review_required";
  const isProcessing = job.status === "processing";
  const isCompleted = job.status === "completed" || needsReview;
  const isFailed = job.status === "failed";
  const isCancelled = job.status === "cancelled";

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="card col gap-3"
      style={{
        padding: "20px 24px",
        border: "2px solid var(--hard)",
        background: isProcessing ? "var(--paper)" : isCompleted ? "var(--teal-soft)" : "var(--paper)",
        boxShadow: "var(--shadow-card)",
      }}
    >
      <div className="row items-center justify-between" style={{ flexWrap: "wrap", gap: 10 }}>
        <div className="row items-center gap-3">
          <div
            style={{
              width: 36,
              height: 36,
              display: "grid",
              placeItems: "center",
              background: isCompleted ? "var(--teal)" : "var(--teal-soft)",
              color: isCompleted ? "#fff" : "var(--teal)",
              border: "1.5px solid var(--hard)",
            }}
          >
            {isProcessing ? (
              <span className="pulse-soft" style={{ fontSize: 18, fontWeight: "bold" }}>⟳</span>
            ) : isCompleted ? (
              <Icon name="check" size={20} color="#fff" stroke={2.5} />
            ) : (
              <Icon name="file" size={18} />
            )}
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>
              {isProcessing
                ? `Resume parsing in progress: ${job.filename}`
                : isCompleted
                ? `Parsing Complete: ${job.filename}`
                : isFailed
                ? `Parsing Failed: ${job.filename}`
                : `Parsing Cancelled: ${job.filename}`}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
              {isProcessing
                ? `${job.stageLabel} (${job.elapsedSeconds}s elapsed)`
                : needsReview
                ? "Profile updated; staged duplicate bullet pairs need your review"
                : isCompleted
                ? `Finished in ${job.elapsedSeconds}s · Profile updated with new experience & skills`
                : job.error || "Execution terminated"}
            </div>
          </div>
        </div>

        <div className="row items-center gap-2">
          {isProcessing && (
            <button
              className="btn btn-ghost"
              style={{ fontSize: 12, padding: "5px 12px", border: "1.5px solid var(--bad)", color: "var(--bad)" }}
              onClick={() => onCancel(job.taskId)}
              title="Abort this ingestion task"
            >
              Cancel
            </button>
          )}
          {(isCompleted || isFailed || isCancelled) && (
            <button
              className="btn btn-primary"
              style={{ fontSize: 12, padding: "6px 14px" }}
              onClick={onDismiss}
            >
              Upload Another Resume
            </button>
          )}
        </div>
      </div>

      <IngestionStepper stage={job.stage} status={job.status} />

      <ThoughtPreviewBox thoughts={job.thoughts || []} isProcessing={isProcessing} />

      {(job.hasStagedDuplicates || needsReview) && (
        <div
          className="card row items-center justify-between gap-3"
          style={{
            padding: "12px 16px",
            background: "var(--yellow-soft)",
            border: "1.5px solid var(--hard)",
            color: "var(--yellow-ink)",
          }}
        >
          <div className="row items-center gap-2">
            <Icon name="alert-circle" size={18} color="var(--yellow-ink)" />
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              Similar / duplicate points detected against existing profile! Review staging prepared.
            </span>
          </div>
          {onReviewDuplicates && (
            <button
              type="button"
              className="btn btn-primary"
              style={{ fontSize: 12, padding: "5px 14px", whiteSpace: "nowrap" }}
              onClick={onReviewDuplicates}
            >
              Review Duplicates (Side-by-Side)
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}

export function IngestionView({
  api,
  activeIngestion: propActiveIngestion,
  setActiveIngestion: propSetActiveIngestion,
}: {
  api: ApiFetch;
  activeIngestion?: IngestionJob | null;
  setActiveIngestion?: (job: IngestionJob | null | ((prev: IngestionJob | null) => IngestionJob | null)) => void;
}) {
  const appCtx = useAppContext();
  const [localActiveIngestion, setLocalActiveIngestion] = useState<IngestionJob | null>(null);
  const activeIngestion = propActiveIngestion !== undefined ? propActiveIngestion : (appCtx?.activeIngestion ?? localActiveIngestion);
  const setActiveIngestion = propSetActiveIngestion !== undefined ? propSetActiveIngestion : (appCtx?.setActiveIngestion ?? setLocalActiveIngestion);

  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);

  useEffect(() => {
    if (activeIngestion?.hasStagedDuplicates || activeIngestion?.status === "review_needed" || activeIngestion?.status === "review_required") {
      setShowDuplicateModal(true);
    }
  }, [activeIngestion?.hasStagedDuplicates, activeIngestion?.status]);
  // Phase text under the upload buttons: only resumes run AI extraction, so
  // cover-letter saves get a shorter message.
  const [busyMsg, setBusyMsg] = useState("");
  const [activeTab, setActiveTab] = useState<"resume" | "manual" | "raw" | "template">("resume");

  // Forms
  const [skillForm, setSkillForm] = useState({ n: "", cat: "technical" });
  const [expForm, setExpForm]     = useState({ role: "", co: "", period: "", d: "" });
  const [projForm, setProjForm]   = useState({ title: "", stack: "", repo: "", impact: "" });
  const [identityForm, setIdentityForm] = useState({ email: "", phone: "", linkedin_url: "", github_url: "", website_url: "", city: "" });
  const [eduForm, setEduForm] = useState({ title: "" });
  const [certForm, setCertForm] = useState({ title: "" });
  const [achievementForm, setAchievementForm] = useState({ title: "" });
  const [rawText, setRawText]     = useState("");
  const [template, setTemplate]   = useState("");
  const [templateLoaded, setTemplateLoaded] = useState(false);

  // Document library (uploaded resumes + cover letters) and their profile tags.
  type Doc = { id: string; kind: "resume" | "cover_letter"; topic: string; tag_id: string | null; tag_name?: string; source_filename: string; created_at: string };
  type Tag = { id: string; name: string };
  const [docs, setDocs] = useState<Doc[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [docKindFilter, setDocKindFilter] = useState<"" | "resume" | "cover_letter">("");
  const [docTagFilter, setDocTagFilter] = useState("");
  // Upload assignment is deliberately independent from the library filter.
  // Selecting “Any tag” to inspect documents must never silently turn the next
  // resume into an untagged/General source.
  const [uploadTag, setUploadTag] = useState("");
  const [editingDoc, setEditingDoc] = useState<string | null>(null);
  const [editTopic, setEditTopic] = useState("");
  const [editTag, setEditTag] = useState("");
  const [newTagName, setNewTagName] = useState("");
  // Standalone tag creation (no document edit needed).
  const [standaloneTag, setStandaloneTag] = useState("");

  const loadDocs = (k = docKindFilter, t = docTagFilter) => {
    const params = new URLSearchParams();
    if (k) params.set("kind", k);
    if (t) params.set("tag_id", t);
    api(`/api/v1/documents${params.size ? `?${params}` : ""}`)
      .then(r => r.json())
      .then(d => setDocs(d.documents || []))
      .catch(() => {});
  };
  const loadTags = () => {
    api(`/api/v1/tags`).then(r => r.json()).then(d => setTags(d.tags || [])).catch(() => {});
  };

  // "Check API": one tiny round-trip against the configured provider so a bad
  // or missing key shows up HERE instead of as a silently-empty profile.
  type AiCheck = { ok: boolean; provider: string; model: string; error: string; latency_ms: number; key_present: boolean };
  const [aiChecking, setAiChecking] = useState(false);
  const [aiCheckResult, setAiCheckResult] = useState<AiCheck | null>(null);
  const checkAi = () => {
    setAiChecking(true);
    setAiCheckResult(null);
    api(`/api/v1/ai/check`, { method: "POST", timeoutMs: 60000 })
      .then(r => r.json())
      .then(d => setAiCheckResult(d))
      .catch(err => setAiCheckResult({ ok: false, provider: "?", model: "", key_present: false, latency_ms: 0, error: requestErrorMessage(err, "Could not reach the API server.") }))
      .finally(() => setAiChecking(false));
  };

  // Bullet-level tag assignment over existing profile points (skills,
  // projects, experience). Untagged points are universal material; a point
  // tagged with the chosen profile is what makes an application scoped.
  type PointTagRow = { point_kind: string; point_id: string; tag_id: string; tag_name: string };
  type ProfilePoint = { id: string; n?: string; title?: string; role?: string; co?: string };
  const [points, setPoints] = useState<{ skills: ProfilePoint[]; projects: ProfilePoint[]; exp: ProfilePoint[] }>({ skills: [], projects: [], exp: [] });
  const [pointTags, setPointTags] = useState<PointTagRow[]>([]);
  const [ptOpen, setPtOpen] = useState<string | null>(null);

  const loadPointTags = () => {
    api(`/api/v1/point-tags`).then(r => r.json()).then(d => setPointTags(d.point_tags || [])).catch(() => {});
    api(`/api/v1/profile`).then(r => r.json()).then(d => setPoints({
      skills: d.skills || [], projects: d.projects || [], exp: d.exp || [],
    })).catch(() => {});
  };
  const togglePointTag = async (kind: "skill" | "project" | "experience", pointId: string, tagId: string) => {
    const current = pointTags.filter(r => r.point_kind === kind && r.point_id === pointId).map(r => r.tag_id);
    const wanted = current.includes(tagId) ? current.filter(t => t !== tagId) : [...current, tagId];
    // Optimistic flip; server response is authoritative when it lands.
    setPointTags(prev => [
      ...prev.filter(r => !(r.point_kind === kind && r.point_id === pointId)),
      ...wanted.map(tid => ({
        point_kind: kind, point_id: pointId, tag_id: tid,
        tag_name: tags.find(t => t.id === tid)?.name ?? "",
      })),
    ]);
    try {
      const r = await api(`/api/v1/point-tags/${kind}/${pointId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag_ids: wanted }),
      });
      if (r.ok) {
        const d = await r.json();
        setPointTags(prev => [...prev.filter(x => !(x.point_kind === kind && x.point_id === pointId)), ...(d.point_tags || [])]);
      }
    } catch { /* chip state snaps back on next reload */ }
  };

  // Auto-reconnect on mount: query GET /api/v1/documents/ingest/status
  useEffect(() => {
    if (!api) return;
    let cancelled = false;

    async function checkActiveJob() {
      try {
        const res = await api(`/api/v1/documents/ingest/status`);
        if (!res.ok || cancelled) return;
        const data: IngestionStatusResponse = await res.json();
        if (cancelled) return;

        if (data && data.task_id && (data.status === "processing" || data.status === "review_needed" || data.status === "review_required" || (data.status === "completed" && (data.elapsed_seconds < 120 || data.has_staged_duplicates)))) {
          const stageNum = (data.stage_number || STAGE_KEY_TO_NUMBER[data.stage] || 1) as 1 | 2 | 3 | 4;
          setActiveIngestion({
            taskId: data.task_id,
            status: data.status,
            stage: stageNum,
            stageLabel: data.stage_message || data.stage_label || STAGE_LABELS[stageNum],
            filename: data.filename || "Uploaded resume",
            tagId: data.tag_id || null,
            startedAt: data.started_at ? new Date(data.started_at).getTime() : Date.now() - (data.elapsed_seconds || 0) * 1000,
            elapsedSeconds: Math.round(data.elapsed_seconds || 0),
            thoughts: Array.isArray(data.thoughts) ? data.thoughts : [],
            hasStagedDuplicates: Boolean(data.has_staged_duplicates),
            reviewableDuplicates: data.reviewable_duplicates || [],
            error: data.error || null,
            result: data.result || null,
          });
        }
      } catch {
        // Tolerant to offline/network hiccups
      }
    }

    checkActiveJob();
    return () => {
      cancelled = true;
    };
  }, [api, setActiveIngestion]);

  useEffect(() => {
    if (activeTab !== "resume") return;
    loadDocs();
    loadTags();
    loadPointTags();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, docKindFilter, docTagFilter]);

  const cancelIngest = async (taskId: string) => {
    try {
      await api(`/api/v1/documents/ingest/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
      setActiveIngestion(prev =>
        prev && prev.taskId === taskId
          ? { ...prev, status: "cancelled", stageLabel: "Cancelled by user" }
          : prev
      );
    } catch {
      setActiveIngestion(null);
    }
  };

  const uploadDoc = async (kind: "resume" | "cover_letter", file: File) => {
    if (kind === "cover_letter") {
      setStatus("loading");
      setErrorMessage(null);
      setBusyMsg("Saving cover letter…");
      const fd = new FormData();
      fd.append("kind", "cover_letter");
      fd.append("file", file);
      if (uploadTag) fd.append("tag_id", uploadTag);
      try {
        const r = await api(`/api/v1/documents`, { method: "POST", body: fd, timeoutMs: 0 });
        const d = await r.json();
        if (!r.ok) throw new Error(d?.detail || "Upload failed");
        setStatus("done");
        if (docTagFilter === "" || docKindFilter === "" || docKindFilter === kind) loadDocs();
      } catch (err) {
        setErrorMessage(requestErrorMessage(err, "Could not upload the cover letter."));
        setStatus("error");
      }
      return;
    }

    // Resume: Asynchronous Ingestion (Milestone 1)
    setStatus("loading");
    setErrorMessage(null);
    setBusyMsg("Submitting resume for parsing…");

    const tempId = `task-${Date.now()}`;
    const initialJob: IngestionJob = {
      taskId: tempId,
      status: "processing",
      stage: 1,
      stageLabel: STAGE_LABELS[1],
      filename: file.name,
      startedAt: Date.now(),
      elapsedSeconds: 0,
      thoughts: [`Uploading ${file.name}...`, "Registering ingestion task with background worker..."],
      error: null,
    };
    setActiveIngestion(initialJob);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", "resume");
    if (uploadTag) fd.append("tag_id", uploadTag);

    try {
      const res = await api(`/api/v1/documents/ingest`, {
        method: "POST",
        body: fd,
        timeoutMs: 0,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || "Upload rejected by server");
      }

      setActiveIngestion({
        taskId: data.task_id,
        status: "processing",
        stage: 1,
        stageLabel: STAGE_LABELS[1],
        filename: data.filename || file.name,
        tagId: data.tag_id || uploadTag || null,
        startedAt: data.started_at ? new Date(data.started_at).getTime() : Date.now(),
        elapsedSeconds: 0,
        thoughts: [
          `Ingestion task registered: ${data.task_id}`,
          `Stage 1/4: ${STAGE_LABELS[1]}`,
        ],
        error: null,
      });
      if (docTagFilter === "" || docKindFilter === "" || docKindFilter === kind) loadDocs();
    } catch (err) {
      const errText = requestErrorMessage(err, "Could not upload resume for parsing.");
      setErrorMessage(errText);
      setStatus("error");
      setActiveIngestion({
        taskId: tempId,
        status: "failed",
        stage: 1,
        stageLabel: STAGE_LABELS[1],
        filename: file.name,
        startedAt: Date.now(),
        elapsedSeconds: 0,
        thoughts: [`Upload failed: ${errText}`],
        error: errText,
      });
    }
  };

  const patchDoc = async (id: string, patch: { topic?: string; tag_id?: string }) => {
    await api(`/api/v1/documents/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).catch(() => {});
    loadDocs();
  };

  const deleteDoc = async (id: string) => {
    await api(`/api/v1/documents/${id}`, { method: "DELETE" }).catch(() => {});
    loadDocs();
  };

  const createTag = async (name: string): Promise<Tag | null> => {
    try {
      const r = await api(`/api/v1/tags`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  };

  const previewDoc = async (d: Doc) => {
    try {
      const r = await api(`/api/v1/documents/${d.id}/file`);
      if (!r.ok) throw new Error(`Preview failed (${r.status})`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      setErrorMessage(requestErrorMessage(err, "Could not open the document."));
      setStatus("error");
    }
  };

  // Load existing template on mount
  useEffect(() => {
    if (activeTab !== "template" || templateLoaded) return;
    api(`/api/v1/template`)
      .then(r => r.json())
      .then(d => { setTemplate(d.template || ""); setTemplateLoaded(true); })
      .catch(() => {});
  }, [activeTab, api, templateLoaded]);

  const saveTemplate = async () => {
    setStatus("loading");
    setBusyMsg("");
    setErrorMessage(null);
    try {
      const r = await api(`/api/v1/template`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template }),
      });
      if (r.ok) {
        setStatus("done");
      } else {
        setErrorMessage(await responseErrorMessage(r, "Could not save resume template."));
        setStatus("error");
      }
    } catch (err) {
      setErrorMessage(requestErrorMessage(err, "Could not save resume template."));
      setStatus("error");
    }
  };

  const addManual = async (type: string, data: any) => {
    setStatus("loading");
    setBusyMsg("");
    setErrorMessage(null);
    try {
      const endpointType = type === "exp" ? "experience" : type;
      // "Add Context" identity save is a PARTIAL update on a form that starts blank
      // (unlike ProfileView, which pre-fills). Send only the fields the user filled
      // so blank inputs don't overwrite previously-saved email/linkedin/etc. The
      // backend uses exclude_unset, so unsent keys are left untouched.
      const payload = type === "identity"
        ? Object.fromEntries(Object.entries(data).filter(([, v]) => String(v ?? "").trim()))
        : data;
      const r = await api(`/api/v1/profile/${endpointType}`, {
        method: type === "identity" ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (r.ok) {
        setStatus("done");
        if (type === "skill")   setSkillForm({ n: "", cat: "technical" });
        if (type === "exp")     setExpForm({ role: "", co: "", period: "", d: "" });
        if (type === "project") setProjForm({ title: "", stack: "", repo: "", impact: "" });
        if (type === "identity") setIdentityForm({ email: "", phone: "", linkedin_url: "", github_url: "", website_url: "", city: "" });
        if (type === "education") setEduForm({ title: "" });
        if (type === "certification") setCertForm({ title: "" });
        if (type === "achievement") setAchievementForm({ title: "" });
        window.dispatchEvent(new CustomEvent("profile-refresh"));
      } else {
        setErrorMessage(await responseErrorMessage(r, "Could not save profile context."));
        setStatus("error");
      }
    } catch (err) {
      setErrorMessage(requestErrorMessage(err, "Could not save profile context."));
      setStatus("error");
    }
  };

  const ingestRaw = async () => {
    setStatus("loading");
    setBusyMsg("");
    setErrorMessage(null);
    const fd = new FormData();
    fd.append("raw", rawText);
    try {
      const r = await api(`/api/v1/ingest`, { method: "POST", body: fd, timeoutMs: 0 });
      if (r.ok) {
        window.dispatchEvent(new CustomEvent("profile-refresh"));
        setStatus("done");
        setRawText("");
      } else {
        setErrorMessage(await responseErrorMessage(r, "Could not sync raw context."));
        setStatus("error");
      }
    } catch (err) {
      setErrorMessage(requestErrorMessage(err, "Could not sync raw context."));
      setStatus("error");
    }
  };

  const TABS = [
    { id: "resume" as const, label: "Resume", description: "PDF, DOCX, text", icon: "upload", accent: "teal" },
    { id: "manual" as const, label: "Manual", description: "Skills, roles, projects", icon: "plus", accent: "blue" },
    { id: "raw" as const, label: "Raw Text", description: "Paste notes", icon: "file", accent: "yellow" },
    { id: "template" as const, label: "Template", description: "Resume format", icon: "layers", accent: "purple" },
  ];
  const activeTabMeta = TABS.find(t => t.id === activeTab) ?? TABS[0];

  return (
    <div className="ingestion-page scroll">
      <div className="ingestion-shell">
        <div className="ingestion-hero">
          <div className="ingestion-hero-copy">
            <span className="eyebrow">Build a complete profile</span>
            <h2>Add your experience</h2>
            <p>Bring in a résumé, projects, or notes. You can review and edit everything afterward.</p>
          </div>
          <div className={`ingestion-active-card ingestion-accent-${activeTabMeta.accent}`}>
            <div className="ingestion-active-icon"><Icon name={activeTabMeta.icon} size={18} /></div>
            <div>
              <span>Adding from</span>
              <strong>{activeTabMeta.label}</strong>
            </div>
          </div>
        </div>

        <div className="ingestion-tabs" role="tablist" aria-label="Experience source">
          {TABS.map(t => (
            <button key={t.id} onClick={() => { setActiveTab(t.id); setStatus("idle"); setErrorMessage(null); }}
              className={`ingestion-tab ingestion-accent-${t.accent} ${activeTab === t.id ? "active" : ""}`}
              role="tab"
              aria-selected={activeTab === t.id}>
              <span className="ingestion-tab-icon"><Icon name={t.icon} size={15} /></span>
              <span className="ingestion-tab-copy">
                <strong>{t.label}</strong>
                <small>{t.description}</small>
              </span>
            </button>
          ))}
        </div>

        {status === "done" && (
          <motion.div initial={{opacity:0,y:-10}} animate={{opacity:1,y:0}} className="ingestion-alert success">
            <Icon name="check" size={18} /><div style={{fontWeight:600}}>Saved successfully!</div>
          </motion.div>
        )}
        {status === "error" && (
          <motion.div initial={{opacity:0,y:-10}} animate={{opacity:1,y:0}} className="ingestion-alert error">
            {errorMessage || "An error occurred."}
          </motion.div>
        )}

        {activeTab === "resume" && (
          <motion.div initial={{opacity:0}} animate={{opacity:1}} className="col gap-4">
            {activeIngestion && (
              <IngestionProgressCard
                job={activeIngestion}
                onCancel={cancelIngest}
                onDismiss={() => setActiveIngestion(null)}
                onReviewDuplicates={() => setShowDuplicateModal(true)}
              />
            )}

            <div
              className="card col gap-4"
              style={{
                padding: "40px 32px",
                alignItems: "center",
                textAlign: "center",
                border: "2px dashed var(--hard)",
                background: "var(--paper-2)",
                opacity: activeIngestion?.status === "processing" ? 0.75 : 1,
              }}
            >
              <div style={{ width: 56, height: 56, borderRadius: 0, background: "var(--teal-soft)", color: "var(--teal)", display: "grid", placeItems: "center" }}><Icon name="upload" size={26} /></div>
              <div style={{ fontWeight: 600, fontSize: 17 }}>Upload a document</div>
              <div style={{ fontSize: 13.5, color: "var(--ink-3)", maxWidth: 420, lineHeight: 1.5 }}>
                Résumé uploads are read into your profile (skills, roles, projects) and kept as reusable files.
                Cover letters are kept as rewrite bases. Assign each a profile tag below.
              </div>
              <div className="row gap-3" style={{ marginTop: 8 }}>
                <label className="row gap-2" style={{ alignItems: "center", fontSize: 12.5, color: "var(--ink-2)" }}>
                  <span className="mono" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em" }}>Assign uploaded file to</span>
                  <select
                    className="field-input"
                    aria-label="Assign uploaded file to profile tag"
                    value={uploadTag}
                    onChange={e => setUploadTag(e.target.value)}
                    style={{ width: "auto", minWidth: 130, padding: "5px 10px", fontSize: 12 }}
                  >
                    <option value="">No tag (General)</option>
                    {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </label>
                <input type="file" accept=".pdf,.docx,.txt,.md" style={{ display: "none" }} id="doc-resume-in"
                  disabled={activeIngestion?.status === "processing"}
                  onChange={e => { const f = e.target.files?.[0]; if (f) { uploadDoc("resume", f); } e.target.value = ""; }} />
                <input type="file" accept=".pdf,.docx,.txt,.md" style={{ display: "none" }} id="doc-cl-in"
                  disabled={activeIngestion?.status === "processing"}
                  onChange={e => { const f = e.target.files?.[0]; if (f) { uploadDoc("cover_letter", f); } e.target.value = ""; }} />
                <button
                  className="btn btn-primary"
                  style={{ padding: "12px 28px", fontSize: 14 }}
                  onClick={() => document.getElementById("doc-resume-in")?.click()}
                  disabled={status === "loading" || activeIngestion?.status === "processing"}
                >
                  {activeIngestion?.status === "processing" ? "Parsing In Progress..." : "Upload Resume"}
                </button>
                <button
                  className="btn"
                  style={{ padding: "12px 28px", fontSize: 14 }}
                  onClick={() => document.getElementById("doc-cl-in")?.click()}
                  disabled={status === "loading" || activeIngestion?.status === "processing"}
                >
                  Upload Cover Letter
                </button>
                <button className="btn btn-ghost" style={{ padding: "12px 20px", fontSize: 13 }}
                  onClick={checkAi} disabled={aiChecking} title="Sends a tiny test request to the configured AI provider">
                  {aiChecking ? "Checking…" : "Check API"}
                </button>
              </div>
              {activeIngestion?.status === "processing" && (
                <div
                  className="mono pulse-soft"
                  style={{
                    fontSize: 12,
                    color: "var(--teal-ink)",
                    background: "var(--teal-soft)",
                    padding: "6px 14px",
                    border: "1.5px solid var(--hard)",
                    borderRadius: 4,
                  }}
                >
                  Upload form locked: resume parsing task is currently active for {activeIngestion.filename}.
                </div>
              )}
              {status === "loading" && !activeIngestion && <div className="mono pulse" style={{ fontSize: 12 }}>{busyMsg || "Working…"}</div>}
              {aiCheckResult && (
                <div className="card col gap-2" style={{
                  marginTop: 10, padding: "10px 14px", maxWidth: 520, fontSize: 12.5,
                  border: aiCheckResult.ok ? "2px solid var(--teal)" : "2px solid var(--bad)",
                  background: aiCheckResult.ok ? "var(--teal-soft)" : "var(--bad-soft)",
                  color: aiCheckResult.ok ? "var(--teal-ink)" : "var(--bad-ink)", textAlign: "left",
                }}>
                  <b>{aiCheckResult.ok ? "AI connection OK" : "AI connection FAILED"} — {aiCheckResult.provider} / {aiCheckResult.model || "default model"}{aiCheckResult.latency_ms ? ` (${(aiCheckResult.latency_ms / 1000).toFixed(1)}s)` : ""}</b>
                  {aiCheckResult.error}
                </div>
              )}
            </div>

            <MiscCard api={api} />

            <div className="row gap-3" style={{ alignItems: "center", flexWrap: "wrap" }}>
              <span className="eyebrow">Filter:</span>
              {["", "resume", "cover_letter"].map(k => (
                <button key={k || "all"} className={`btn btn-ghost ${docKindFilter === k ? "active" : ""}`}
                  style={{ fontSize: 12, padding: "4px 10px", ...(docKindFilter === k ? { border: "2px solid var(--hard)", background: "var(--yellow)", color: "var(--ink)" } : {}) }}
                  onClick={() => setDocKindFilter(k as "" | "resume" | "cover_letter")}>
                  {k === "" ? "All" : k === "resume" ? "Resumes" : "Cover letters"}
                </button>
              ))}
              <span style={{ flex: 1 }} />
              <input className="field-input" style={{ width: 140, padding: "4px 10px", fontSize: 12 }}
                placeholder="New tag name…" value={standaloneTag}
                onChange={e => setStandaloneTag(e.target.value)} />
              <button className="btn btn-primary" style={{ fontSize: 12, padding: "5px 12px" }}
                disabled={!standaloneTag.trim()}
                onClick={async () => {
                  const t = await createTag(standaloneTag.trim());
                  if (t) { setTags(prev => prev.some(x => x.id === t.id) ? prev : [...prev, t]); setStandaloneTag(""); }
                }}>+ Create tag</button>
              <select className="field-input" style={{ width: "auto", padding: "4px 10px", fontSize: 12 }} value={docTagFilter} onChange={e => setDocTagFilter(e.target.value)}>
                <option value="">Any tag</option>
                {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>

            {docs.length === 0 ? (
              <div className="card" style={{ padding: 28, textAlign: "center", color: "var(--ink-3)", fontSize: 13.5 }}>
                Nothing here yet. Upload a resume or cover letter above — each gets a box you can preview, tag, rename, or delete.
              </div>
            ) : (
              <div className="docs-grid">
                {docs.map(d => (
                  <div key={d.id} className="card doc-box">
                    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <span className={`pill mono ${d.kind === "resume" ? "doc-kind-resume" : "doc-kind-cl"}`} style={{ fontSize: 10 }}>
                        {d.kind === "resume" ? "RESUME" : "COVER LETTER"}
                      </span>
                      {d.tag_name && <span className="pill" style={{ fontSize: 10, background: "var(--purple-soft)", color: "var(--purple-ink)" }}>{d.tag_name}</span>}
                    </div>
                    {editingDoc === d.id ? (
                      <div className="col gap-2" style={{ marginTop: 8 }}>
                        <input className="field-input" style={{ fontSize: 12, padding: "5px 8px" }} value={editTopic}
                          onChange={e => setEditTopic(e.target.value)} placeholder="Topic (e.g. Backend SWE v2)" autoFocus />
                        <select className="field-input" style={{ fontSize: 12, padding: "5px 8px" }} value={editTag}
                          onChange={e => setEditTag(e.target.value)}>
                          <option value="">No tag</option>
                          {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                          {editTag && !tags.some(t => t.id === editTag) && <option value={editTag}>{editTag} (new — type below)</option>}
                        </select>
                        <input className="field-input" style={{ fontSize: 12, padding: "5px 8px" }} value={newTagName}
                          onChange={e => setNewTagName(e.target.value)} placeholder="…or new tag name" />
                        <div className="row gap-2">
                          <button className="btn btn-primary" style={{ fontSize: 11, padding: "4px 10px" }}
                            onClick={async () => {
                              let tagId = editTag;
                              const name = newTagName.trim();
                              if (name) {
                                const t = await createTag(name);
                                if (t) { tagId = t.id; setTags(prev => prev.some(x => x.id === t.id) ? prev : [...prev, t]); }
                              }
                              await patchDoc(d.id, { topic: editTopic, tag_id: tagId });
                              setEditingDoc(null);
                            }}>Save</button>
                          <button className="btn btn-ghost" style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setEditingDoc(null)}>Cancel</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div style={{ fontWeight: 700, fontSize: 13.5, marginTop: 8, lineHeight: 1.3, wordBreak: "break-word" }}>{d.topic || d.source_filename}</div>
                        <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{d.source_filename} · {String(d.created_at).slice(0, 10)}</div>
                      </>
                    )}
                    {editingDoc !== d.id && (
                      <div className="row gap-2" style={{ marginTop: "auto", paddingTop: 10 }}>
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 8px" }}
                          onClick={() => previewDoc(d)}>Preview</button>
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 8px" }}
                          onClick={() => { setEditingDoc(d.id); setEditTopic(d.topic || d.source_filename || ""); setEditTag(d.tag_id || ""); setNewTagName(""); }}>Edit</button>
                        <span style={{ flex: 1 }} />
                        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 8px", color: "var(--bad)" }}
                          onClick={() => { if (confirm(`Delete "${d.topic || d.source_filename}"? Only this file is removed — your profile points and other documents stay.`)) deleteDoc(d.id); }}>Delete</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Bullet-level profile-point tagging: pick which skills/projects/
                experience belong to each profile tag, so generation with a tag
                scopes the master superset instead of reusing everything. */}
            <div className="card col gap-3" style={{ padding: 20 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center", margin: 0 }}>
                  <Icon name="layers" size={16}/> Tag your profile points
                </h3>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)", maxWidth: 460 }}>
                  Untagged points are used for every application. Points tagged here are added to that profile's applications and hidden from others.
                </span>
              </div>
              {tags.length === 0 && (
                <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                  No tags yet — type a name (e.g. SWE, AI Engineer) above and press "+ Create tag".
                </div>
              )}
              {([
                ["skill", "Skills", points.skills],
                ["project", "Projects", points.projects],
                ["experience", "Experience", points.exp],
              ] as const).map(([kind, label, items]) => {
                const open = ptOpen === kind;
                const taggedCount = new Set(pointTags.filter(r => r.point_kind === kind).map(r => r.point_id)).size;
                return (
                  <div key={kind} className="col gap-2">
                    <button className="btn btn-ghost" style={{ fontSize: 12, padding: "6px 10px", justifyContent: "flex-start", display: "flex" }}
                      onClick={() => setPtOpen(open ? null : kind)}>
                      {open ? "▾" : "▸"} {label} <span className="pill mono" style={{ fontSize: 9, marginLeft: 6 }}>{items.length}</span>
                      {taggedCount > 0 && <span className="pill" style={{ fontSize: 9, marginLeft: 6, background: "var(--purple-soft)", color: "var(--purple-ink)" }}>{taggedCount} tagged</span>}
                    </button>
                    {open && (
                      <div className="col gap-2" style={{ maxHeight: 300, overflowY: "auto", padding: "2px 4px" }}>
                        {items.length === 0 && <div style={{ fontSize: 12, color: "var(--ink-3)" }}>No {label.toLowerCase()} in your profile yet.</div>}
                        {items.map(p => {
                          const mine = pointTags.filter(r => r.point_kind === kind && r.point_id === p.id);
                          const pointLabel = (p.n || p.title || [p.role, p.co].filter(Boolean).join(" — ") || p.id).slice(0, 90);
                          return (
                            <div key={p.id} className="row" style={{ alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                              <span style={{ fontSize: 12.5, fontWeight: 600, minWidth: 180, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={pointLabel}>{pointLabel}</span>
                              <div className="row gap-2" style={{ flexWrap: "wrap" }}>
                                {tags.map(t => {
                                  const on = mine.some(r => r.tag_id === t.id);
                                  return (
                                    <button key={t.id}
                                      onClick={() => togglePointTag(kind, p.id, t.id)}
                                      title={on ? `Remove from ${t.name}` : `Add to ${t.name}`}
                                      style={{
                                        fontSize: 10.5, padding: "2px 9px", cursor: "pointer",
                                        border: on ? "2px solid var(--hard)" : "1px solid var(--line)",
                                        background: on ? "var(--yellow)" : "var(--paper)",
                                        color: "var(--ink)", fontWeight: on ? 800 : 500,
                                      }}>
                                      {t.name}{on ? " ✓" : ""}
                                    </button>
                                  );
                                })}
                                {tags.length === 0 && <span style={{ fontSize: 11, color: "var(--ink-3)" }}>no tags yet</span>}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                 );
              })}
            </div>

            {/* Staged duplicate reviews come from the ingestion task endpoint.
                Keep this entry point focused on bullet-pair review; the legacy
                /conflicts entity chips are intentionally not rendered here. */}
            <div className="card col gap-3" style={{ padding: 20 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center", margin: 0 }}>
                  <Icon name="layers" size={16}/> Duplicate point review
                </h3>
                <span style={{ fontSize: 11.5, color: "var(--ink-3)", maxWidth: 460 }}>
                  Review staged bullet pairs from the active ingestion task side by side. Nothing is deleted until you choose how each pair should resolve.
                </span>
              </div>
              <div className="row gap-2" style={{ alignItems: "center", marginTop: 4, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{ fontSize: 12, padding: "5px 14px", display: "flex", gap: 6, alignItems: "center" }}
                  onClick={() => setShowDuplicateModal(true)}
                  disabled={!activeIngestion?.taskId}
                  title={activeIngestion?.taskId ? "Open the staged bullet-pair review" : "Upload a resume with staged duplicates first"}
                >
                  <Icon name="layers" size={14} /> Open Side-by-Side Review Modal
                </button>
                {!activeIngestion?.taskId && <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>No active ingestion task has staged pairs.</span>}
              </div>
            </div>

            <DuplicateResolutionModal
              isOpen={showDuplicateModal}
              taskId={activeIngestion?.taskId}
              api={api}
              onClose={() => setShowDuplicateModal(false)}
              onResolved={() => {
                setShowDuplicateModal(false);
                loadDocs();
                loadPointTags();
                setActiveIngestion(prev =>
                  prev ? { ...prev, hasStagedDuplicates: false, reviewableDuplicates: [] } : null
                );
              }}
              expectedStagedDuplicates={Boolean(activeIngestion?.hasStagedDuplicates || activeIngestion?.status === "review_required" || activeIngestion?.status === "review_needed")}
            />
          </motion.div>
        )}

        {activeTab === "manual" && (
          <motion.div initial={{opacity:0}} animate={{opacity:1}} className="col gap-8">
            <div className="card col gap-4" style={{ padding: 24 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="spark" size={16}/> Add Skill</h3>
              <input className="field-input" placeholder="Skill name" value={skillForm.n} onChange={v => setSkillForm({...skillForm, n: v.target.value})} />
              <select className="field-input" value={skillForm.cat} onChange={v => setSkillForm({...skillForm, cat: v.target.value})}>
                <option value="technical">Technical</option>
                <option value="soft">Soft Skill</option>
                <option value="tool">Tool / Utility</option>
                <option value="language">Language</option>
                <option value="framework">Framework</option>
              </select>
              <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("skill", skillForm)} disabled={status==="loading" || !skillForm.n.trim()}>Add Skill</button>
            </div>
            <div className="card col gap-4" style={{ padding: 24 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="brief" size={16}/> Add Experience</h3>
              <input className="field-input" placeholder="Role Title" value={expForm.role} onChange={v => setExpForm({...expForm, role: v.target.value})} />
              <input className="field-input" placeholder="Company" value={expForm.co} onChange={v => setExpForm({...expForm, co: v.target.value})} />
              <input className="field-input" placeholder="Period (e.g. 2022-2024)" value={expForm.period} onChange={v => setExpForm({...expForm, period: v.target.value})} />
              <textarea className="field-input" placeholder="Description" rows={3} value={expForm.d} onChange={v => setExpForm({...expForm, d: v.target.value})} />
              <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("exp", expForm)} disabled={status==="loading" || (!expForm.role.trim() && !expForm.co.trim())}>Add Experience</button>
            </div>
            <div className="card col gap-4" style={{ padding: 24 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="layers" size={16}/> Add Project</h3>
              <input className="field-input" placeholder="Project Title" value={projForm.title} onChange={v => setProjForm({...projForm, title: v.target.value})} />
              <input className="field-input" placeholder="Stack (comma-separated)" value={projForm.stack} onChange={v => setProjForm({...projForm, stack: v.target.value})} />
              <input className="field-input" placeholder="Repo URL (optional)" value={projForm.repo} onChange={v => setProjForm({...projForm, repo: v.target.value})} />
              <textarea className="field-input" placeholder="Impact / Description" rows={3} value={projForm.impact} onChange={v => setProjForm({...projForm, impact: v.target.value})} />
              <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("project", projForm)} disabled={status==="loading" || !projForm.title.trim()}>Add Project</button>
            </div>
            <div className="card col gap-4" style={{ padding: 24 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="user" size={16}/> Contact & Social Links</h3>
              <div className="grid-2 gap-3">
                <input className="field-input" placeholder="Email address" value={identityForm.email} onChange={v => setIdentityForm({...identityForm, email: v.target.value})} />
                <input className="field-input" placeholder="Phone number" value={identityForm.phone} onChange={v => setIdentityForm({...identityForm, phone: v.target.value})} />
                <input className="field-input" placeholder="LinkedIn URL" value={identityForm.linkedin_url} onChange={v => setIdentityForm({...identityForm, linkedin_url: v.target.value})} />
                <input className="field-input" placeholder="GitHub URL" value={identityForm.github_url} onChange={v => setIdentityForm({...identityForm, github_url: v.target.value})} />
                <input className="field-input" placeholder="Portfolio / website URL" value={identityForm.website_url} onChange={v => setIdentityForm({...identityForm, website_url: v.target.value})} />
                <input className="field-input" placeholder="City / location" value={identityForm.city} onChange={v => setIdentityForm({...identityForm, city: v.target.value})} />
              </div>
              <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("identity", identityForm)} disabled={status==="loading"}>Save Contact</button>
            </div>
            <div className="grid-2 gap-4">
              <div className="card col gap-4" style={{ padding: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="file" size={16}/> Add Education</h3>
                <input className="field-input" placeholder="Degree, school, year" value={eduForm.title} onChange={v => setEduForm({...eduForm, title: v.target.value})} />
                <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("education", eduForm)} disabled={status==="loading" || !eduForm.title.trim()}>Add Education</button>
              </div>
              <div className="card col gap-4" style={{ padding: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="check" size={16}/> Add Certification</h3>
                <input className="field-input" placeholder="Certification, issuer, year" value={certForm.title} onChange={v => setCertForm({...certForm, title: v.target.value})} />
                <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("certification", certForm)} disabled={status==="loading" || !certForm.title.trim()}>Add Certification</button>
              </div>
            </div>
            <div className="card col gap-4" style={{ padding: 24 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, display: "flex", gap: 8, alignItems: "center" }}><Icon name="trending" size={16}/> Add Achievement</h3>
              <input className="field-input" placeholder="Award, publication, shipped milestone, competition result" value={achievementForm.title} onChange={v => setAchievementForm({...achievementForm, title: v.target.value})} />
              <button className="btn btn-primary" style={{alignSelf:"flex-start",padding:"10px 24px"}} onClick={() => addManual("achievement", achievementForm)} disabled={status==="loading" || !achievementForm.title.trim()}>Add Achievement</button>
            </div>
          </motion.div>
        )}

        {activeTab === "raw" && (
          <motion.div initial={{opacity:0}} animate={{opacity:1}} className="card col gap-4" style={{ padding: 24 }}>
            <div className="eyebrow">Raw Text Aggregator</div>
            <textarea className="field-input" placeholder="Paste unstructured text from old résumés, bios, job descriptions, or notes..." rows={16} value={rawText} onChange={v => setRawText(v.target.value)} style={{ fontSize: 14, lineHeight: 1.6 }} />
            <button className="btn btn-primary" style={{ padding: 16, fontSize: 15 }} onClick={ingestRaw} disabled={status==="loading"}>
              {status === "loading" ? "Processing..." : "Sync Raw Context"}
            </button>
          </motion.div>
        )}

        {activeTab === "template" && (
          <motion.div initial={{opacity:0}} animate={{opacity:1}} className="col gap-4">
            <div className="card" style={{ padding: 24, background: "var(--purple-soft)", border: "1px solid var(--purple)" }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>Resume Template</h3>
              <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.6 }}>
                Paste your preferred resume format here (plain text or Markdown). When the agent generates a tailored resume, it will follow this structure: section order, headings, and layout, and fill it in with your profile and the job requirements.
              </p>
            </div>
            <div className="card col gap-4" style={{ padding: 24 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-2)" }}>Template content</span>
                {template && <span className="pill mono" style={{ fontSize: 10, background: "var(--green-soft)", color: "var(--green-ink)", border: "1px solid var(--green)" }}>Template saved</span>}
              </div>
              <textarea
                className="field-input"
                placeholder={`Paste your resume template here. For example:\n\n# [Name]\n[Contact info]\n\n## Summary\n[2-3 sentence professional summary]\n\n## Experience\n### [Role] - [Company] ([Period])\n- [Bullet points]\n\n## Projects\n### [Project Name]\n- Stack: ...\n- Impact: ...\n\n## Skills\n[Comma-separated list]`}
                rows={24}
                value={template}
                onChange={e => setTemplate(e.target.value)}
                style={{ fontSize: 13, lineHeight: 1.65, fontFamily: "var(--font-mono)" }}
              />
              <div className="row gap-3" style={{ alignItems: "center" }}>
                <button className="btn btn-primary" style={{ padding: "12px 28px", fontSize: 14 }} onClick={saveTemplate} disabled={status==="loading"}>
                  {status === "loading" ? "Saving..." : "Save Template"}
                </button>
                {template && (
                  <button className="btn btn-ghost" style={{ fontSize: 13 }} onClick={() => { setTemplate(""); }}>
                    Clear
                  </button>
                )}
                <span style={{ fontSize: 12, color: "var(--ink-4)" }}>{template.length} chars</span>
              </div>
            </div>
          </motion.div>
        )}
      </div>
    </div>
  );
}
