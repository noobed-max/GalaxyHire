import { useEffect, useRef, useState } from "react";
import { openExternalUrl } from "../../../shared/lib/openExternal";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch, Lead } from "../../../types";
import { GENERATION_TIMEOUT_MS } from "../../../api/generation";
import { discoveryApi } from "../../../api/discovery";
import { getMark, getTone, leadDisplayHeading, leadSeniority, seniorityLabel, seniorityTone } from "../../../shared/lib/leadUtils";

/** "Not relevant" — durable, unlike Remove, which only drops the row from the current list.
 *
 *  Shared by both cards in this file because only one of them is actually rendered by the app
 *  (`PipelineJobCard`, via PipelineView) while the other is what the tests import. Putting the
 *  logic in one hook means a change cannot land in the tested-but-unrendered copy alone — which is
 *  exactly how the first version of this feature shipped: green tests, nothing in the bundle.
 */
function useNotRelevant(lead: Lead, api?: ApiFetch | null) {
  const [dismissed, setDismissed] = useState(false);
  // Only corpus-sourced leads can be judged: the signal is keyed by canonical job id, and a lead
  // that arrived some other way has none. Offering the button anyway would be offering an action
  // that silently does nothing.
  const corpusJobId = lead.source_meta?.corpus_job_id as string | undefined;
  const available = Boolean(corpusJobId && api);

  const record = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!api || !corpusJobId || dismissed) return;
    try {
      const body = await discoveryApi.feedback(api, corpusJobId, "not_relevant");
      setDismissed(true);
      // Let anything showing the standing preference update without a refetch.
      window.dispatchEvent(new CustomEvent("preference-updated", { detail: body.preference }));
    } catch (error) {
      // Left un-dismissed on purpose: the button reverting is the signal that nothing was saved.
      console.error("Could not record feedback", error);
    }
  };

  const title = dismissed
    ? "Recorded — this job won't appear in future searches"
    : "Not relevant: stop showing me this job in future searches";

  return { available, dismissed, record, title };
}

/** Which 24h claim a result carries — only what the source proves. `posted` (dated inside
 *  24h) badges "<24h"; anything else badges nothing. The posting DATE itself is shown
 *  separately by postedLabel below, so undated/older rows still tell the user when the job
 *  was made instead of hiding behind (or without) a badge. */
function FreshnessBadge({ value }: { value: unknown }) {
  if (value !== "posted") return null;
  return (
    <span
      className="pill mono"
      style={{
        fontSize: 8.5, padding: "1px 6px",
        background: "var(--green-soft)",
        color: "var(--green-ink)",
        border: "1px solid var(--green)",
      }}
      title="Posted within the last 24 hours — the source's own posting date proves it."
    >&lt;24h</span>
  );
}

/** "Posted 3d ago" — every result shows when it was made (2026-09 ruling), so freshness is
 *  judged by the user, not hidden by a filter. Null when the source published no date. */
function postedLabel(iso: unknown): string | null {
  if (typeof iso !== "string" || !iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days <= 0) return "posted today";
  if (days === 1) return "posted yesterday";
  if (days < 30) return `posted ${days}d ago`;
  return `posted ${new Date(t).toISOString().slice(0, 10)}`;
}

function PostedChip({ value }: { value: unknown }) {
  const label = postedLabel(value);
  if (!label) return null;
  return (
    <span
      className="pill mono"
      style={{ fontSize: 8.5, padding: "1px 6px" }}
      title={typeof value === "string" ? `Posting date: ${value}` : label}
    >{label}</span>
  );
}

export function JobCard({ lead, onOpen, onDelete, showScore = false, showGenerate = false, port, api }: {
  lead: Lead;
  onOpen: (l: Lead) => void;
  onDelete: (id: string) => void;
  showScore?: boolean;
  showGenerate?: boolean;
  port?: number | null;
  api?: ApiFetch | null;
}) {
  const [generating, setGenerating] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const notRelevant = useNotRelevant(lead, api);
  const desc = lead.description?.trim();
  const signalScore = lead.signal_score || 0;
  const qualityReason = String(lead.lead_quality_reason || lead.source_meta?.lead_quality_reason || "");
  const qualityScore = Number(lead.lead_quality_score || lead.source_meta?.lead_quality_score || 0);
  const isHotX = lead.platform === "x" && signalScore >= 80;
  const level = leadSeniority(lead);
  const levelTone = seniorityTone(level);

  const handleGenerate = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!port || !api) return;
    setGenerating(true);
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await api(`/api/v1/leads/${lead.job_id}/generate`, { method: "POST", signal: controller.signal, timeoutMs: GENERATION_TIMEOUT_MS });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `Generation returned ${response.status}`);
      if (body.lead) window.dispatchEvent(new CustomEvent("lead-updated", { detail: body.lead }));
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (error) {
      console.error("Package generation failed", error);
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
      setGenerating(false);
    }
  };

  useEffect(() => () => requestRef.current?.abort(), []);

  return (
    <div className="card lift" style={{
      padding: 16, cursor: "pointer", border: "1px solid var(--line)",
      background: "var(--card)", display: "flex", flexDirection: "column", gap: 10,
    }} onClick={() => onOpen(lead)}>
      {/* Header row */}
      <div className="row gap-3" style={{ alignItems: "flex-start" }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10, flexShrink: 0,
          background: `var(--${getTone(lead.status)})`, color: `var(--${getTone(lead.status)}-ink)`,
          display: "grid", placeItems: "center",
          fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 500,
          border: `1px solid var(--${getTone(lead.status)}-ink)`,
        }}>{getMark(lead.company)}</div>
        <div className="col" style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.25, color: "var(--ink)" }}>{lead.title}</div>
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{lead.company}</span>
            <span style={{ color: "var(--ink-4)", fontSize: 10 }}>·</span>
            <span className="pill mono" style={{ fontSize: 8.5, padding: "1px 6px" }}>{lead.platform}</span>
            <FreshnessBadge value={lead.source_meta?.freshness} />
            <PostedChip value={lead.source_meta?.date_posted} />
            <span className="pill mono" style={{ fontSize: 8.5, padding: "1px 6px", background: `var(--${levelTone}-soft)`, color: `var(--${levelTone}-ink)`, border: `1px solid var(--${levelTone})` }}>{seniorityLabel(level)}</span>
            {isHotX && <span className="pill mono" style={{ fontSize: 8.5, padding: "1px 6px", background: "var(--orange-soft)", color: "var(--orange-ink)", border: "1px solid var(--orange)" }}>HOT X</span>}
            {lead.budget && <span className="pill mono" style={{ fontSize: 8.5, padding: "1px 6px", background: "var(--green-soft)", color: "var(--green-ink)" }}>{lead.budget}</span>}
          </div>
        </div>
        {signalScore > 0 && (
          <span style={{
            flexShrink: 0, fontSize: 11.5, fontWeight: 800, padding: "3px 9px", borderRadius: 999,
            background: signalScore >= 80 ? "var(--orange-soft)" : signalScore >= 60 ? "var(--yellow-soft)" : "var(--paper-3)",
            color: signalScore >= 80 ? "var(--orange-ink)" : signalScore >= 60 ? "var(--yellow-ink)" : "var(--ink-3)",
            border: `1px solid ${signalScore >= 80 ? "var(--orange)" : "var(--line)"}`,
          }}>{signalScore}</span>
        )}
        {/* Score badge */}
        {showScore && lead.score > 0 && (
          <span style={{
            flexShrink: 0, fontSize: 12, fontWeight: 700, padding: "3px 10px", borderRadius: 999,
            background: lead.score >= 85 ? "var(--green)" : lead.score >= 50 ? "var(--yellow)" : "var(--bad-soft)",
            color:      lead.score >= 85 ? "var(--green-ink)" : lead.score >= 50 ? "var(--yellow-ink)" : "var(--bad)",
          }}>{lead.score}%</span>
        )}
        {/* Not-relevant: durable, unlike Remove. Only for corpus leads, which are the only ones
            the corpus can key a signal to. */}
        {notRelevant.available && (
          <button
            onClick={notRelevant.record}
            disabled={notRelevant.dismissed}
            title={notRelevant.title}
            style={{
              flexShrink: 0, height: 26, borderRadius: 7, padding: "0 8px",
              border: "1px solid var(--line)",
              background: notRelevant.dismissed ? "var(--yellow-soft)" : "var(--paper)",
              color: notRelevant.dismissed ? "var(--yellow-ink)" : "var(--ink-3)",
              cursor: notRelevant.dismissed ? "default" : "pointer",
              display: "flex", alignItems: "center", gap: 4,
              fontSize: 10, lineHeight: 1, whiteSpace: "nowrap",
              opacity: notRelevant.dismissed ? 1 : 0.7, transition: "opacity 0.15s",
            }}
            onMouseEnter={e => { if (!notRelevant.dismissed) e.currentTarget.style.opacity = "1"; }}
            onMouseLeave={e => { if (!notRelevant.dismissed) e.currentTarget.style.opacity = "0.7"; }}
          >{notRelevant.dismissed ? "Hidden" : "Not relevant"}</button>
        )}
        {/* Delete button */}
        <button
          onClick={e => { e.stopPropagation(); Promise.resolve(onDelete(lead.job_id)).catch(() => {}); }}
          title="Remove from this list"
          style={{
            flexShrink: 0, width: 26, height: 26, borderRadius: 7,
            border: "1px solid var(--line)", background: "var(--paper)",
            color: "var(--bad)", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 14, lineHeight: 1, padding: 0, opacity: 0.7,
            transition: "opacity 0.15s",
          }}
          onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
          onMouseLeave={e => (e.currentTarget.style.opacity = "0.7")}
        >×</button>
      </div>

      {/* Description */}
      {desc ? (
        <div style={{
          fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55,
          display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
          overflow: "hidden",
          background: "var(--paper-3)", borderRadius: 8, padding: "8px 10px",
          border: "1px solid var(--line)",
        }}>{desc}</div>
      ) : (
        <div style={{ fontSize: 11.5, color: "var(--ink-4)", fontStyle: "italic" }}>No description extracted.</div>
      )}

      {/* Evaluator reason (for Evaluated tab) */}
      {showScore && lead.reason && (
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, borderLeft: "2px solid var(--line)", paddingLeft: 8 }}>
          {lead.reason.slice(0, 160)}{lead.reason.length > 160 ? "…" : ""}
        </div>
      )}

      {lead.signal_reason && (
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, borderLeft: "2px solid var(--orange)", paddingLeft: 8 }}>
          {lead.signal_reason.slice(0, 150)}{lead.signal_reason.length > 150 ? "..." : ""}
        </div>
      )}

      {qualityReason && (
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5, borderLeft: "2px solid var(--blue)", paddingLeft: 8 }}>
          Shown by quality gate{qualityScore ? ` (${qualityScore})` : ""}: {qualityReason.slice(0, 150)}{qualityReason.length > 150 ? "..." : ""}
        </div>
      )}

      {/* Footer */}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
        <button
          onClick={e => { e.stopPropagation(); openExternalUrl(lead.url); }}
          title={lead.url}
          style={{ fontSize: 11, color: "var(--teal)", background: "none", border: "none", padding: 0, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          <Icon name="external-link" size={11} color="var(--teal)" />
          {lead.url.replace(/^https?:\/\//, "").slice(0, 50)}
        </button>
        <div className="row gap-2">
          {showGenerate && (
            <button
              onClick={handleGenerate}
              disabled={generating}
              style={{
                padding: "4px 10px", borderRadius: 7, fontSize: 11, fontWeight: 600,
                border: "1px solid var(--purple)", background: "var(--purple-soft)",
                color: "var(--purple-ink)", cursor: generating ? "wait" : "pointer",
              }}
            >{generating ? "Queued..." : "Generate Resume"}</button>
          )}
          <button
            onClick={e => { e.stopPropagation(); onOpen(lead); }}
            style={{
              padding: "4px 10px", borderRadius: 7, fontSize: 11, fontWeight: 600,
              border: "1px solid var(--line)", background: "var(--paper)",
              color: "var(--ink-2)", cursor: "pointer",
            }}
          >Details →</button>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════
   PIPELINE VIEW (tabbed)
══════════════════════════════════════ */

export function PipelineJobCard({ lead, onOpen, onDelete, showGenerate = false, port, api, carted = false, onAddToCart }: {
  lead: Lead;
  onOpen: (l: Lead) => void;
  onDelete: (id: string) => void;
  showGenerate?: boolean;
  port?: number | null;
  api?: ApiFetch | null;
  carted?: boolean;
  onAddToCart?: () => void;
}) {
  const [generating, setGenerating] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const notRelevant = useNotRelevant(lead, api);
  const signalScore = lead.signal_score || 0;
  const matchScore = lead.score || 0;
  const qualityScore = Number(lead.lead_quality_score || lead.source_meta?.lead_quality_score || 0);
  const isHotX = lead.platform === "x" && signalScore >= 80;
  const level = leadSeniority(lead);
  const levelTone = seniorityTone(level);
  const statusTone = getTone(lead.status);
  const display = leadDisplayHeading(lead);
  const urlLabel = lead.url ? lead.url.replace(/^https?:\/\//, "").slice(0, 42) : "No source URL";
  const statusLabel: Record<string, string> = {
    discovered: "New",
    evaluating: "Reviewing",
    evaluated: "Reviewed",
    tailoring: "Preparing documents",
    approved: "Documents ready",
    applied: "Applied",
    interviewing: "Interviewing",
    accepted: "Offer",
    rejected: "Closed",
    discarded: "Hidden",
  };

  const handleGenerate = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!port || !api) return;
    setGenerating(true);
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await api(`/api/v1/leads/${lead.job_id}/generate`, { method: "POST", signal: controller.signal, timeoutMs: GENERATION_TIMEOUT_MS });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `Generation returned ${response.status}`);
      if (body.lead) window.dispatchEvent(new CustomEvent("lead-updated", { detail: body.lead }));
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (error) {
      console.error("Package generation failed", error);
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
      setGenerating(false);
    }
  };

  useEffect(() => () => requestRef.current?.abort(), []);

  return (
    <div className="pipeline-job-card lift" data-status={lead.status || "discovered"} onClick={() => onOpen(lead)}>
      <div className="pipeline-job-mark" style={{ background: `var(--${statusTone}-soft)`, color: `var(--${statusTone}-ink)`, borderColor: `var(--${statusTone})` }}>
        {getMark(lead.company)}
      </div>
      <div className="pipeline-job-main">
        <div className="pipeline-job-title-row">
          <div className="pipeline-job-title">
            <span>{display.role}</span>
            <b>||</b>
            <span className="company">{display.company}</span>
          </div>
          <span className="pipeline-status-pill" style={{ background: `var(--${statusTone}-soft)`, color: `var(--${statusTone}-ink)`, borderColor: `var(--${statusTone})` }}>
            {statusLabel[lead.status] || "New"}
          </span>
        </div>
        <div className="pipeline-job-meta">
          <span>{lead.location || "Location not listed"}</span>
          <span style={{ color: `var(--${levelTone}-ink)` }}>{seniorityLabel(level)}</span>
          <span>{lead.platform || "Job site"}</span>
          {typeof lead.source_meta?.freshness === "string" && lead.source_meta.freshness === "posted" && (
            <span title="Posted within the last 24 hours — the source's own posting date proves it.">{"<24h"}</span>
          )}
          {(() => {
            const label = postedLabel(lead.source_meta?.date_posted);
            return label ? <span title={String(lead.source_meta?.date_posted)}>{label}</span> : null;
          })()}
          {isHotX && <span style={{ color: "var(--orange-ink)" }}>Trending</span>}
          {lead.budget && <span style={{ color: "var(--green-ink)" }}>{lead.budget}</span>}
        </div>
      </div>
      <div className="pipeline-job-side">
        <div className="pipeline-score-stack">
          {matchScore > 0 && <span className={`pipeline-score ${matchScore >= 76 ? "good" : matchScore >= 50 ? "warn" : "bad"}`}>{matchScore}% match</span>}
          {!matchScore && signalScore > 0 && <span className={`pipeline-score ${signalScore >= 80 ? "hot" : signalScore >= 60 ? "warn" : ""}`}>{signalScore}% promising</span>}
          {!matchScore && !signalScore && qualityScore > 0 && <span className="pipeline-score">Found job</span>}
        </div>
        <div className="pipeline-job-actions">
          {showGenerate && (
            <button className="btn" onClick={handleGenerate} disabled={generating}>
              <Icon name="file" size={12} /> {generating ? "Preparing…" : "Tailor résumé"}
            </button>
          )}
          <button className="btn btn-icon" onClick={e => { e.stopPropagation(); if (lead.url) openExternalUrl(lead.url); }} title={lead.url} disabled={!lead.url}>
            <Icon name="external-link" size={13} />
          </button>
          <button className="btn" onClick={e => { e.stopPropagation(); onOpen(lead); }}>Review</button>
          {onAddToCart && (
            <button
              className="btn"
              onClick={e => { e.stopPropagation(); onAddToCart(); }}
              disabled={carted}
              title={carted ? "Already in your apply cart" : "Add this job to your apply cart"}
              style={carted ? undefined : { borderColor: "var(--green)", background: "var(--green-soft)", color: "var(--green-ink)", fontWeight: 700 }}
            >
              <Icon name="cart" size={13} /> {carted ? "In cart" : "Add to cart"}
            </button>
          )}
          {/* Durable, unlike the trash button beside it: this trains every future search rather
              than removing one row. The labels have to carry that difference. */}
          {notRelevant.available && (
            <button
              className="btn"
              onClick={notRelevant.record}
              disabled={notRelevant.dismissed}
              title={notRelevant.title}
            >{notRelevant.dismissed ? "Hidden" : "Not relevant"}</button>
          )}
          <button className="btn btn-icon danger" onClick={e => { e.stopPropagation(); Promise.resolve(onDelete(lead.job_id)).catch(() => {}); }} title="Remove job from this list">
            <Icon name="trash" size={13} />
          </button>
        </div>
        <div className="pipeline-source mono" title={lead.url}>{urlLabel}</div>
      </div>
    </div>
  );
}

export function PipelineSkeleton() {
  return (
    <div className="pipeline-skeleton">
      <div className="pipeline-skeleton-bar" />
      {[0, 1, 2, 3].map(i => (
        <div key={i} className="pipeline-skeleton-card">
          <span />
          <div>
            <i />
            <b />
            <em />
          </div>
          <strong />
        </div>
      ))}
    </div>
  );
}
