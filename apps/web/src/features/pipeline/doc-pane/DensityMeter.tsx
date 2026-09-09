import { estimateLines, isEmptySelection, type ProfileData } from "./selectionState";
import type { DocSelection } from "../../../api/docPane";

const CAPACITY = 64;

export function DensityMeter({ profile, selection }: { profile: ProfileData; selection: DocSelection }) {
  if (isEmptySelection(selection)) return null;
  const lines = estimateLines(profile, selection);
  const ratio = lines / CAPACITY;
  const pct = Math.min(100, Math.round(ratio * 100));
  const over = ratio > 1;
  const amber = !over && ratio >= 0.88;
  const color = over ? "var(--bad)" : amber ? "var(--yellow)" : "var(--green)";
  const ink = over ? "var(--bad-ink)" : amber ? "var(--yellow-ink)" : "var(--green-ink)";
  const soft = over ? "var(--bad-soft)" : amber ? "var(--yellow-soft)" : "var(--green-soft)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span className="eyebrow" style={{ margin: 0 }}>Page density</span>
        <span className="mono" style={{ fontSize: 10, fontWeight: 800, color: ink, background: soft, padding: "1px 8px", borderRadius: 999 }}>
          {lines}/{CAPACITY} lines
        </span>
      </div>
      <div style={{ height: 6, background: "var(--paper-3)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 999, transition: "width 0.25s ease" }} />
      </div>
      {over && (
        <div style={{ fontSize: 11, color: "var(--bad)", lineHeight: 1.4 }}>
          Over one page — will auto-shrink to fit.
        </div>
      )}
    </div>
  );
}
