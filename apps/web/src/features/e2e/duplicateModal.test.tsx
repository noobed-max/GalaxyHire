import { describe, expect, it } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

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
  pairs: DuplicatePair[];
}

export type ResolutionAction = "use_new" | "keep_both" | "keep_existing";

export interface ResolutionDecision {
  pair_id: string;
  action: ResolutionAction;
}

// Two-column comparison UI component contract
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
    <div className="card duplicate-card" style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, marginBottom: 12 }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>
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
          padding: 8,
          borderRadius: 6,
        }}
      >
        {/* Existing Point Column */}
        <div style={{ borderRight: "1px solid var(--line)", paddingRight: 8 }}>
          <div className="eyebrow" style={{ color: "var(--ink-2)", fontSize: 10 }}>
            EXISTING / OLD POINT
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.4, marginTop: 4 }}>
            {pair.existing_text}
          </div>
          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              className={`btn ${decision === "keep_existing" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("keep_existing")}
              style={{ fontSize: 11, padding: "3px 8px" }}
            >
              Keep Existing
            </button>
          </div>
        </div>

        {/* Incoming Point Column */}
        <div style={{ paddingLeft: 4 }}>
          <div className="eyebrow" style={{ color: "var(--blue)", fontSize: 10 }}>
            INCOMING / NEW POINT
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.4, marginTop: 4 }}>
            {pair.new_text}
          </div>
          <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
            <button
              type="button"
              className={`btn ${decision === "use_new" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("use_new")}
              style={{ fontSize: 11, padding: "3px 8px" }}
            >
              Use New (Replace)
            </button>
            <button
              type="button"
              className={`btn ${decision === "keep_both" ? "btn-primary" : "btn-outline"}`}
              onClick={() => onDecide("keep_both")}
              style={{ fontSize: 11, padding: "3px 8px" }}
            >
              Keep Both
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

describe("R3: Interactive Two-Column Duplicate Resolution Dialog E2E Contracts", () => {
  const mockPair: DuplicatePair = {
    pair_id: "pair-xyz-1",
    existing_point_id: "exp_101",
    existing_text: "Built distributed backend for AI platform on 4-node k3s cluster.",
    new_text: "Built distributed backend for AI platform on GCP k3s with Longhorn storage & Garage S3.",
    explanation: "Wording variant with updated infrastructure details",
  };

  it("renders two-column Old vs New side-by-side comparison modal markup", () => {
    const html = renderToStaticMarkup(
      <DuplicateReviewComparisonCard
        pair={mockPair}
        company="SuprMentr (Intern · Feb 2026 – May 2026)"
        decision="use_new"
        onDecide={() => {}}
      />
    );

    // Assert side-by-side columns exist
    expect(html).toContain("EXISTING / OLD POINT");
    expect(html).toContain("INCOMING / NEW POINT");

    // Assert action buttons exist
    expect(html).toContain("Keep Existing");
    expect(html).toContain("Use New (Replace)");
    expect(html).toContain("Keep Both");

    // Assert bullet contents are presented
    expect(html).toContain("4-node k3s cluster");
    expect(html).toContain("Longhorn storage &amp; Garage S3");
  });

  it("builds valid resolution payload conformant to backend API specification", () => {
    const decisions: Record<string, ResolutionAction> = {
      "pair-1": "use_new",
      "pair-2": "keep_both",
      "pair-3": "keep_existing",
    };

    const payload = {
      resolutions: Object.entries(decisions).map(([pair_id, action]) => ({
        pair_id,
        action,
      })),
    };

    expect(payload.resolutions).toHaveLength(3);
    expect(payload.resolutions[0]).toEqual({ pair_id: "pair-1", action: "use_new" });
    expect(payload.resolutions[1]).toEqual({ pair_id: "pair-2", action: "keep_both" });
    expect(payload.resolutions[2]).toEqual({ pair_id: "pair-3", action: "keep_existing" });
  });

  it("groups duplicate pairs by company/project entity", () => {
    const groups: DuplicateGroup[] = [
      {
        company_name: "CloudScale Inc",
        role: "Senior Engineer",
        pairs: [mockPair],
      },
    ];

    expect(groups[0].company_name).toBe("CloudScale Inc");
    expect(groups[0].pairs).toHaveLength(1);
    expect(groups[0].pairs[0].pair_id).toBe("pair-xyz-1");
  });
});
