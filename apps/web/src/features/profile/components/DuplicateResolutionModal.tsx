import { useEffect, useState } from "react";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch } from "../../../types";

export interface DuplicatePair {
  pair_id: string;
  existing_point_id: string;
  existing_text: string;
  new_text: string;
  explanation?: string;
}

export interface DuplicateGroup {
  company_name: string;
  role?: string;
  period?: string;
  parent_kind?: string;
  parent_id?: string;
  entity_title?: string;
  pairs: DuplicatePair[];
}

export interface DuplicateGroupsResponse {
  task_id?: string;
  status?: string;
  groups?: unknown;
}

/**
 * Keep the review UI strictly bullet-pair based. The ingestion endpoint groups
 * pairs by their parent entity, but older payloads could also contain entity
 * rows without the two bullet wordings. Those rows are not reviewable points.
 */
export function normalizeDuplicateGroups(payload: unknown): DuplicateGroup[] {
  const rawGroups: unknown[] = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object" && Array.isArray((payload as DuplicateGroupsResponse).groups)
      ? (payload as DuplicateGroupsResponse).groups as unknown[]
      : [];

  return rawGroups
    .filter((group): group is Record<string, unknown> => Boolean(group && typeof group === "object"))
    .map(group => {
      const rawPairs = Array.isArray(group.pairs) ? group.pairs : [];
      const pairs = rawPairs
        .filter((pair): pair is Record<string, unknown> => Boolean(pair && typeof pair === "object"))
        .map(pair => ({
          pair_id: String(pair.pair_id || "").trim(),
          existing_point_id: String(pair.existing_point_id || "").trim(),
          existing_text: String(pair.existing_text || "").trim(),
          new_text: String(pair.new_text || "").trim(),
          explanation: pair.explanation ? String(pair.explanation) : undefined,
        }))
        .filter(pair => Boolean(pair.pair_id && pair.existing_point_id && pair.existing_text && pair.new_text));

      return {
        company_name: String(group.company_name || group.entity_title || "Profile entity"),
        role: group.role ? String(group.role) : undefined,
        period: group.period ? String(group.period) : undefined,
        parent_kind: group.parent_kind ? String(group.parent_kind) : undefined,
        parent_id: group.parent_id ? String(group.parent_id) : undefined,
        entity_title: group.entity_title ? String(group.entity_title) : undefined,
        pairs,
      };
    })
    .filter(group => group.pairs.length > 0);
}

async function readResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { detail: text };
  }
}

export async function parseDuplicateResponse<T>(response: Response, fallback: string): Promise<T> {
  const body = await readResponseBody(response);
  if (!response.ok) {
    const detail = body && typeof body === "object"
      ? (body as Record<string, unknown>).detail ?? (body as Record<string, unknown>).error
      : undefined;
    const message = typeof detail === "string" && detail.trim() ? detail : `${fallback} (HTTP ${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export async function fetchDuplicateGroups(api: ApiFetch, taskId: string): Promise<DuplicateGroup[]> {
  const response = await api(`/api/v1/documents/ingest/${encodeURIComponent(taskId)}/duplicates`);
  const payload = await parseDuplicateResponse<DuplicateGroupsResponse>(response, "Failed to load duplicate points");
  return normalizeDuplicateGroups(payload);
}

export async function submitDuplicateResolutions(
  api: ApiFetch,
  taskId: string,
  resolutions: ResolutionDecision[],
): Promise<Record<string, unknown>> {
  const response = await api(`/api/v1/documents/ingest/${encodeURIComponent(taskId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolutions }),
  });
  return parseDuplicateResponse<Record<string, unknown>>(response, "Failed to resolve duplicate points");
}

export type ResolutionAction = "use_new" | "keep_both" | "keep_existing";

export interface ResolutionDecision {
  pair_id: string;
  action: ResolutionAction;
}

export function DuplicateReviewComparisonCard({
  pair,
  company,
  decision,
  onDecide,
}: {
  pair: DuplicatePair;
  company: string;
  decision?: ResolutionAction;
  onDecide: (action: ResolutionAction) => void;
}) {
  return (
    <div
      className="card duplicate-card"
      style={{
        border: "1px solid var(--line)",
        borderRadius: 8,
        padding: 12,
        marginBottom: 12,
        background: "var(--paper)",
      }}
    >
      <div className="eyebrow" style={{ marginBottom: 6, color: "var(--ink-2)" }}>
        {company} — Review Duplicate Bullet
      </div>
      {pair.explanation && (
        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 8 }}>
          Note: {pair.explanation}
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          background: "var(--paper-2)",
          padding: 12,
          borderRadius: 6,
        }}
      >
        {/* Existing Point Column */}
        <div style={{ borderRight: "1px solid var(--line)", paddingRight: 10 }}>
          <div className="eyebrow" style={{ color: "var(--ink-2)", fontSize: 10, letterSpacing: "0.05em" }}>
            EXISTING / OLD POINT
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.5, marginTop: 6, color: "var(--ink)" }}>
            {pair.existing_text}
          </div>
          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              className={`btn ${decision === "keep_existing" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("keep_existing")}
              style={{ fontSize: 11, padding: "4px 10px", fontWeight: decision === "keep_existing" ? 600 : 400 }}
            >
              {decision === "keep_existing" ? "✓ Keep Existing" : "Keep Existing"}
            </button>
          </div>
        </div>

        {/* Incoming Point Column */}
        <div style={{ paddingLeft: 6 }}>
          <div className="eyebrow" style={{ color: "var(--blue)", fontSize: 10, letterSpacing: "0.05em" }}>
            INCOMING / NEW POINT
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.5, marginTop: 6, color: "var(--ink)" }}>
            {pair.new_text}
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              type="button"
              className={`btn ${decision === "use_new" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("use_new")}
              style={{ fontSize: 11, padding: "4px 10px", fontWeight: decision === "use_new" ? 600 : 400 }}
            >
              {decision === "use_new" ? "✓ Use New (Replace)" : "Use New (Replace)"}
            </button>
            <button
              type="button"
              className={`btn ${decision === "keep_both" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("keep_both")}
              style={{ fontSize: 11, padding: "4px 10px", fontWeight: decision === "keep_both" ? 600 : 400 }}
            >
              {decision === "keep_both" ? "✓ Keep Both" : "Keep Both"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function DuplicateResolutionModal({
  isOpen,
  onClose,
  taskId,
  api,
  onResolved,
  initialGroups,
  expectedStagedDuplicates = false,
}: {
  isOpen: boolean;
  onClose: () => void;
  taskId?: string | null;
  api: ApiFetch;
  onResolved?: () => void;
  initialGroups?: DuplicateGroup[];
  /** Set when the task/status payload says staged duplicates exist. */
  expectedStagedDuplicates?: boolean;
}) {
  const initialGroupsProvided = initialGroups !== undefined;
  const [groups, setGroups] = useState<DuplicateGroup[]>(() => normalizeDuplicateGroups(initialGroups));
  const [loading, setLoading] = useState<boolean>(isOpen && !initialGroupsProvided);
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, ResolutionAction>>({});
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    let mounted = true;
    const providedGroups = normalizeDuplicateGroups(initialGroups);

    // A modal instance may be reused for another ingestion task. Never carry
    // choices, errors, or old pairs into that task.
    setDecisions({});
    setError(null);
    setSubmitting(false);
    setGroups(providedGroups);

    if (!isOpen) {
      setLoading(false);
      return;
    }

    if (initialGroupsProvided) {
      setLoading(false);
      return;
    }

    if (!taskId) {
      setLoading(false);
      setError("No ingestion task is available for duplicate review. Close this dialog and retry from the ingestion task.");
      return;
    }

    setLoading(true);
    fetchDuplicateGroups(api, taskId)
      .then(fetchedGroups => {
        if (!mounted) return;
        setGroups(fetchedGroups);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : "Failed to load duplicate points");
        setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [isOpen, taskId, initialGroups, initialGroupsProvided, api, retryNonce]);

  if (!isOpen) return null;

  const allPairs = groups.flatMap((g) => g.pairs);
  const totalPairs = allPairs.length;
  const resolvedCount = allPairs.filter(pair => Boolean(decisions[pair.pair_id])).length;
  const reviewLocked = totalPairs > 0;

  const handleDecide = (pairId: string, action: ResolutionAction) => {
    setDecisions((prev) => ({ ...prev, [pairId]: action }));
  };

  const handleKeepAllExisting = () => {
    const updated: Record<string, ResolutionAction> = {};
    for (const pair of allPairs) {
      updated[pair.pair_id] = "keep_existing";
    }
    setDecisions(updated);
  };

  const handleSubmit = async () => {
    if (submitting) return;

    if (!taskId) {
      setError("No ingestion task is available for duplicate review. Close this dialog and retry from the ingestion task.");
      return;
    }

    if (resolvedCount < totalPairs) {
      setError(`Choose an action for every duplicate pair (${resolvedCount} of ${totalPairs} selected).`);
      return;
    }

    const finalResolutions: { pair_id: string; action: ResolutionAction }[] = allPairs.map((p) => ({
      pair_id: p.pair_id,
      action: decisions[p.pair_id] as ResolutionAction,
    }));

    setSubmitting(true);
    setError(null);

    try {
      await submitDuplicateResolutions(api, taskId, finalResolutions);
      if (onResolved) {
        onResolved();
      }
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to resolve duplicate points");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.65)",
        backdropFilter: "blur(3px)",
        WebkitBackdropFilter: "blur(3px)",
        zIndex: 10000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
      onClick={(e) => {
        if (!reviewLocked && e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Review Duplicate Points"
        style={{
          background: "var(--paper)",
          border: "1.5px solid var(--hard, var(--line))",
          borderRadius: 12,
          boxShadow: "0 20px 60px rgba(0, 0, 0, 0.4)",
          width: "min(860px, 96vw)",
          maxHeight: "88vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--line)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "var(--paper-2)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Icon name="layers" size={20} color="var(--yellow-ink, #ca8a04)" />
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>
                Review Duplicate Points
              </h3>
            </div>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--ink-2)" }}>
              The resume contains bullet points similar to existing profile items. Choose which wording to preserve.
            </p>
          </div>
          {reviewLocked ? (
            <span className="pill" style={{ fontSize: 10.5, background: "var(--yellow-soft)", color: "var(--yellow-ink)" }}>
              Complete review to continue
            </span>
          ) : (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={onClose}
              aria-label="Close"
              style={{ padding: "4px 8px", fontSize: 14 }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px", overflowY: "auto", flex: 1 }}>
          {loading && (
            <div style={{ textAlign: "center", padding: "32px 0", color: "var(--ink-2)", fontSize: 13 }}>
              Loading reviewable duplicate pairs…
            </div>
          )}

          {error && (
            <div
              className="card"
              style={{
                background: "var(--red-soft, rgba(239, 68, 68, 0.1))",
                color: "var(--red, #ef4444)",
                padding: 12,
                borderRadius: 6,
                marginBottom: 12,
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}

          {!loading && groups.length === 0 && !error && !expectedStagedDuplicates && (
            <div style={{ textAlign: "center", padding: "32px 0", color: "var(--ink-3)", fontSize: 13 }}>
              No duplicate points pending review.
            </div>
          )}

          {!loading && groups.length === 0 && expectedStagedDuplicates && (
            <div
              role="status"
              className="card"
              style={{
                background: "var(--yellow-soft, rgba(234, 179, 8, 0.12))",
                color: "var(--yellow-ink, var(--ink))",
                padding: 12,
                borderRadius: 6,
                fontSize: 12,
              }}
            >
              This ingestion task reports staged duplicate points, but the review endpoint returned no bullet pairs.
              Please retry. If this persists, keep the task ID ({taskId || "unknown"}) for diagnostics.
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => setRetryNonce(value => value + 1)}
                style={{ display: "block", fontSize: 11, padding: "4px 10px", marginTop: 9 }}
              >
                Retry loading duplicate pairs
              </button>
            </div>
          )}

          {!loading &&
            groups.map((group, gIdx) => {
              const groupHeading =
                group.entity_title ||
                `${group.company_name}${
                  group.role ? ` (${group.role}${group.period ? ` · ${group.period}` : ""})` : ""
                }`;

              return (
                <div key={gIdx} style={{ marginBottom: 20 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      color: "var(--ink)",
                      marginBottom: 8,
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <Icon name="briefcase" size={14} color="var(--ink-2)" />
                    <span>{groupHeading}</span>
                  </div>

                  {group.pairs.map((pair) => (
                    <DuplicateReviewComparisonCard
                      key={pair.pair_id}
                      pair={pair}
                      company={groupHeading}
                      decision={decisions[pair.pair_id]}
                      onDecide={(action) => handleDecide(pair.pair_id, action)}
                    />
                  ))}
                </div>
              );
            })}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "12px 20px",
            borderTop: "1px solid var(--line)",
            background: "var(--paper-2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
            {loading
              ? "Loading duplicate pairs…"
              : expectedStagedDuplicates && totalPairs === 0
              ? "Review data unavailable — retry loading pairs"
              : `Resolved ${resolvedCount} of ${totalPairs} duplicate point${totalPairs === 1 ? "" : "s"}`}
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={handleKeepAllExisting}
              disabled={submitting || totalPairs === 0}
              style={{ fontSize: 12, padding: "6px 14px" }}
            >
              Keep All Existing
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={submitting || totalPairs === 0 || resolvedCount < totalPairs}
              style={{ fontSize: 12, padding: "6px 16px", fontWeight: 600 }}
            >
              {submitting ? "Applying…" : "Apply & Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
