import { useState } from "react";
import { openExternalUrl } from "../../../shared/lib/openExternal";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch, Lead } from "../../../types";
import { cleanLeadText, getTone, leadDisplayHeading } from "../../../shared/lib/leadUtils";

/**
 * Job details sidebar — the read half of the old ApprovalDrawer, without any of the
 * resume/cover-letter building (that lives in JobBuilders, hosted by the cart view).
 *
 * Slides in from the LEFT (not a centered modal): click a job anywhere, read the posting,
 * triage with feedback/follow-up, close. Generation and applying happen from the cart.
 */
export function JobDetailsPanel({ j, api, onClose, onAddToCart, carted }: {
  j: Lead; api: ApiFetch; onClose: () => void;
  onAddToCart?: () => void; carted?: boolean;
}) {
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);
  const [feedbackErr, setFeedbackErr] = useState<string | null>(null);
  const [followupBusy, setFollowupBusy] = useState<number | null>(null);

  const submitFeedback = async (feedback: string) => {
    setFeedbackBusy(feedback);
    setFeedbackErr(null);
    try {
      const r = await api(`/api/v1/leads/${j.job_id}/feedback`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback }),
      });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || `Server returned ${r.status}`);
      }
      // Optimistically reflect the selection locally instead of depending only on
      // the server's WS LEAD_UPDATED broadcast (which never arrives while the
      // socket is mid-reconnect).
      window.dispatchEvent(new CustomEvent("lead-updated", { detail: { job_id: j.job_id, feedback } }));
      window.dispatchEvent(new CustomEvent("leads-refresh"));
    } catch (err) {
      setFeedbackErr(err instanceof Error ? err.message : "Feedback failed");
    } finally {
      setFeedbackBusy(null);
    }
  };

  const scheduleFollowup = async (days: number) => {
    setFollowupBusy(days);
    setFeedbackErr(null);
    try {
      const r = await api(`/api/v1/leads/${j.job_id}/followup`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days }),
      });
      if (!r.ok) {
        const detail = await r.json().then(d => d.detail).catch(() => "");
        throw new Error(detail || `Server returned ${r.status}`);
      }
    } catch (err) {
      setFeedbackErr(err instanceof Error ? err.message : "Follow-up save failed");
    } finally {
      setFollowupBusy(null);
    }
  };

  const display = leadDisplayHeading(j);
  const originalTitle = cleanLeadText(j.title);
  const descriptionText = cleanLeadText(j.description);
  const jobDescription = [
    originalTitle && originalTitle !== display.role ? `Original listing title:\n${originalTitle}` : "",
    descriptionText ? `Description:\n${descriptionText}` : "",
  ].filter(Boolean).join("\n\n") || "No job description extracted yet.";

  const extractedDetails = [
    ["Tech stack", (j.tech_stack || []).join(", ")],
    ["Location", j.location || ""],
    ["Urgency", j.urgency || ""],
    ["Budget", j.budget || ""],
  ].filter(([, value]) => value);

  const draftBlock = (label: string, value?: string) => value ? (
    <div key={label} style={{ background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</span>
        <button className="btn btn-ghost" style={{ fontSize: 11, padding: "3px 8px" }} onClick={() => navigator.clipboard?.writeText(value)}>Copy</button>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{value}</div>
    </div>
  ) : null;

  const qualityScore = Number(j.lead_quality_score || j.source_meta?.lead_quality_score || 0);
  const qualityReason = String(j.lead_quality_reason || j.source_meta?.lead_quality_reason || "");

  return (
    <div className="drawer-backdrop" onClick={onClose} style={{ zIndex: 100 }}>
      <aside
        className="card gh-job-details"
        role="dialog"
        aria-label={`Job details: ${display.role}`}
        onClick={e => e.stopPropagation()}
        style={{
          position: "absolute", top: 0, left: 0, bottom: 0,
          width: "min(560px, calc(100vw - 24px))",
          background: "var(--paper)", zIndex: 101,
          borderRight: "1px solid var(--line)",
          display: "flex", flexDirection: "column", overflow: "hidden",
          animation: "gh-slide-in-left 0.22s ease-out",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", padding: "16px 18px 14px", borderBottom: "1px solid var(--line)", gap: 12, flexShrink: 0, flexWrap: "wrap" }}>
          <div style={{ minWidth: 0 }}>
            <div className="row gap-2" style={{ marginBottom: 7, flexWrap: "wrap" }}>
              <span className="pill" style={{ background: `var(--${getTone(j.status)})`, color: `var(--${getTone(j.status)}-ink)` }}>{j.status}</span>
              <span className="pill mono" style={{ background: "var(--paper-3)", color: "var(--ink-3)" }}>{j.platform}</span>
              {j.budget && <span className="pill mono" style={{ background: "var(--green-soft)", color: "var(--green-ink)", border: "1px solid var(--green)" }}>{j.budget}</span>}
              {(j.signal_score || 0) > 0 && <span className="pill mono" style={{ background: (j.signal_score || 0) >= 80 ? "var(--orange-soft)" : "var(--yellow-soft)", color: (j.signal_score || 0) >= 80 ? "var(--orange-ink)" : "var(--yellow-ink)", border: `1px solid ${(j.signal_score || 0) >= 80 ? "var(--orange)" : "var(--yellow)"}` }}>Lead signal {j.signal_score}</span>}
              {j.score > 0 && <span className="pill mono" style={{ background: j.score >= 85 ? "var(--green-soft)" : j.score >= 60 ? "var(--yellow-soft)" : "var(--bad-soft)", color: j.score >= 85 ? "var(--green-ink)" : j.score >= 60 ? "var(--yellow-ink)" : "var(--bad)" }}>{j.score}/100 match</span>}
            </div>
            <h2 style={{ fontSize: 21, fontWeight: 600, overflowWrap: "anywhere" }}>
              {display.role} <span style={{ color: "var(--ink-3)", fontWeight: 700 }}>||</span> {display.company}
            </h2>
            <p style={{ color: "var(--ink-3)", fontSize: 12.5, marginTop: 2 }}>{j.platform}</p>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
            {onAddToCart && (
              <button
                onClick={onAddToCart}
                disabled={carted}
                title={carted ? "Already in your apply cart" : "Add this job to your apply cart"}
                className="btn"
                style={{ fontSize: 12, borderColor: "var(--green)", background: carted ? "var(--paper-3)" : "var(--green-soft)", color: carted ? "var(--ink-4)" : "var(--green-ink)", fontWeight: 700 }}
              >
                <Icon name="cart" size={12} /> {carted ? "In cart" : "Add to cart"}
              </button>
            )}
            <button
              onClick={() => openExternalUrl(j.url)}
              title="Open original job posting"
              className="btn"
              style={{ fontSize: 12, borderColor: "var(--teal)", background: "var(--teal-soft)", color: "var(--teal)" }}
            >
              <Icon name="external-link" size={12} color="var(--teal)" /> View Posting
            </button>
            <button className="btn btn-icon" onClick={onClose} aria-label="Close details"><Icon name="x" size={15} /></button>
          </div>
        </div>

        <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 14, overflowY: "auto", minHeight: 0, flex: 1 }}>
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Job Description</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6, background: "var(--paper-3)", borderRadius: 8, padding: "10px 12px", border: "1px solid var(--line)", whiteSpace: "pre-wrap" }}>
              {jobDescription}
            </div>
          </div>

          {extractedDetails.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Extracted Details</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8 }}>
                {extractedDetails.map(([label, value]) => (
                  <div key={label} style={{ background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 9, padding: "9px 10px", minWidth: 0 }}>
                    <div className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>{label}</div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-2)", overflowWrap: "anywhere" }}>{value}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="eyebrow">Match Reasoning</div>

          {(j.signal_score || j.signal_reason || (j.signal_tags?.length ?? 0) > 0) && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Lead Signal</div>
              <div style={{ background: "var(--orange-soft)", border: "1px solid var(--orange)", borderRadius: 10, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: 12.5, color: "var(--orange-ink)", fontWeight: 700 }}>Opportunity strength</span>
                  <span className="mono" style={{ fontSize: 13, fontWeight: 800, color: "var(--orange-ink)" }}>{j.signal_score || 0}/100</span>
                </div>
                {!!j.learning_delta && (
                  <div style={{ background: "var(--paper)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px" }}>
                    <div className="row" style={{ justifyContent: "space-between", gap: 10, alignItems: "center" }}>
                      <span className="mono" style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Feedback learning</span>
                      <span className="mono" style={{ fontSize: 12, fontWeight: 800, color: j.learning_delta > 0 ? "var(--green-ink)" : "var(--bad)" }}>
                        {(j.base_signal_score ?? 0) || ((j.signal_score || 0) - j.learning_delta)} {j.learning_delta > 0 ? "+" : ""}{j.learning_delta}
                      </span>
                    </div>
                    {j.learning_reason && <div style={{ marginTop: 5, fontSize: 12.2, color: "var(--ink-2)", lineHeight: 1.45 }}>{j.learning_reason}</div>}
                  </div>
                )}
                {j.signal_reason && <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55 }}>{j.signal_reason}</div>}
                {(j.signal_tags?.length ?? 0) > 0 && (
                  <div className="row gap-2" style={{ flexWrap: "wrap" }}>
                    {j.signal_tags!.slice(0, 8).map(tag => (
                      <span key={tag} className="pill mono" style={{ fontSize: 9, background: "var(--paper)", color: "var(--ink-3)", border: "1px solid var(--line)" }}>{tag}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {((j.fit_bullets?.length ?? 0) > 0 || j.proof_snippet) && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Proof Pack</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(j.fit_bullets?.length ?? 0) > 0 && (
                  <div style={{ background: "var(--green-soft)", border: "1px solid var(--green)", borderRadius: 10, padding: "10px 12px" }}>
                    <div className="mono" style={{ fontSize: 10, color: "var(--green-ink)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Why I fit</div>
                    <div className="col gap-1">
                      {j.fit_bullets!.map((bullet, idx) => (
                        <div key={idx} style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45 }}>{bullet}</div>
                      ))}
                    </div>
                  </div>
                )}
                {j.proof_snippet && draftBlock("Proof snippet", j.proof_snippet)}
              </div>
            </div>
          )}

          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Lead Feedback</div>
            <div className="row gap-2" style={{ flexWrap: "wrap" }}>
              {[
                ["relevant", "Relevant"],
                ["not_relevant", "Not Relevant"],
                ["duplicate", "Duplicate"],
                ["low_quality", "Low Quality"],
                ["incorrect_category", "Incorrect Category"],
                ["already_contacted", "Contacted"],
              ].map(([id, label]) => {
                const active = j.feedback === id;
                return (
                  <button key={id} onClick={() => submitFeedback(id)} disabled={feedbackBusy === id} style={{
                    padding: "5px 10px", borderRadius: 8, fontSize: 11.5, fontWeight: 700, cursor: feedbackBusy === id ? "wait" : "pointer",
                    border: `1px solid ${active ? "var(--blue)" : "var(--line)"}`,
                    background: active ? "var(--blue-soft)" : "var(--paper-3)",
                    color: active ? "var(--blue-ink)" : "var(--ink-2)",
                  }}>{feedbackBusy === id ? "Saving..." : label}</button>
                );
              })}
            </div>
            {feedbackErr && <div style={{ marginTop: 6, color: "var(--bad)", fontSize: 11.5 }}>{feedbackErr}</div>}
          </div>

          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Follow-up</div>
            <div className="row gap-2" style={{ flexWrap: "wrap" }}>
              {[2, 5, 10].map(days => (
                <button key={days} onClick={() => scheduleFollowup(days)} disabled={followupBusy === days} style={{
                  padding: "5px 10px", borderRadius: 8, fontSize: 11.5, fontWeight: 700, cursor: followupBusy === days ? "wait" : "pointer",
                  border: "1px solid var(--green)", background: "var(--green-soft)", color: "var(--green-ink)",
                }}>{followupBusy === days ? "Saving..." : `${days} days`}</button>
              ))}
            </div>
            {j.followup_due_at && <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 6 }}>Due {j.followup_due_at}</div>}
            {(j.followup_sequence?.length ?? 0) > 0 && (
              <div style={{ marginTop: 8, background: "var(--paper-3)", border: "1px solid var(--line)", borderRadius: 10, padding: "9px 11px" }}>
                <div className="mono" style={{ fontSize: 10, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Suggested sequence</div>
                <div className="col gap-1">
                  {j.followup_sequence!.map((step, idx) => (
                    <div key={idx} style={{ fontSize: 12.2, color: "var(--ink-2)", lineHeight: 1.45 }}>{step}</div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Match Score</span>
              <span style={{
                fontSize: 13, fontWeight: 700,
                color: j.score >= 85 ? "var(--green-ink)" : j.score >= 60 ? "var(--yellow-ink)" : "var(--bad)",
                background: j.score >= 85 ? "var(--green-soft)" : j.score >= 60 ? "var(--yellow-soft)" : "var(--bad-soft)",
                padding: "2px 10px", borderRadius: 999,
              }}>{j.score ?? 0}/100</span>
            </div>
            <div style={{ height: 6, background: "var(--paper-3)", borderRadius: 999, marginBottom: 16 }}>
              <div style={{ height: "100%", borderRadius: 999, width: `${Math.min(100, j.score ?? 0)}%`, background: j.score >= 85 ? "var(--green)" : j.score >= 60 ? "var(--yellow)" : "var(--bad)", transition: "width 0.4s ease" }} />
            </div>
          </div>

          {j.reason && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>Evaluator Reasoning</div>
              <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6, background: "var(--paper)", borderRadius: 10, padding: "10px 12px", border: "1px solid var(--line)" }}>{j.reason}</div>
            </div>
          )}

          {qualityReason && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>Why This Lead Was Shown</div>
              <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6, background: "var(--blue-soft)", borderRadius: 10, padding: "10px 12px", border: "1px solid var(--blue)" }}>
                {qualityScore ? `Quality ${qualityScore}: ` : ""}{qualityReason}
              </div>
            </div>
          )}

          {j.match_points && j.match_points.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Match Points</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {j.match_points.map((pt, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "var(--ink-2)" }}>
                    <Icon name="check" size={13} color="var(--ok)" style={{ flexShrink: 0, marginTop: 2 }} />
                    <span style={{ lineHeight: 1.5 }}>{pt}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {j.gaps && j.gaps.length > 0 && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Skill Gaps</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {j.gaps.map((g, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "var(--ink-2)" }}>
                    <Icon name="alert-circle" size={13} color="var(--bad)" style={{ flexShrink: 0, marginTop: 2 }} />
                    <span style={{ lineHeight: 1.5 }}>{g}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
