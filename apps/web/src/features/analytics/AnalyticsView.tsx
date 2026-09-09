import { useMemo } from "react";
import { SankeyChart } from "./SankeyChart";
import { applicationsOf, buildFlowData, buildFlowStats } from "./analyticsData";
import type { Lead } from "../../types";

const LEGEND: Array<{ label: string; color: string }> = [
  { label: "Awaiting reply", color: "var(--yellow)" },
  { label: "Interviewing", color: "var(--teal)" },
  { label: "Accepted", color: "var(--ok)" },
  { label: "Rejected", color: "var(--bad)" },
];

export function AnalyticsView({ leads }: { leads: Lead[] }) {
  const apps = useMemo(() => applicationsOf(leads), [leads]);
  const stats = useMemo(() => buildFlowStats(leads), [leads]);
  const flow = useMemo(() => buildFlowData(leads), [leads]);

  if (apps.length === 0) {
    return (
      <div className="gh-page scroll analytics-page">
        <div className="an-grid">
          <section className="card an-hero">
            <span className="eyebrow">Pipeline health</span>
            <h2>No applications yet</h2>
            <p>
              These charts track every job you actually apply to. Move a job into
              <strong> Applications</strong> from the pipeline and its journey — replies,
              interviews, offers — shows up here with real numbers, never placeholders.
            </p>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="gh-page scroll analytics-page">
      <div className="an-grid">
        <section className="card an-hero">
          <span className="eyebrow">Pipeline health</span>
          <h2>Where your applications stand</h2>
          <p>
            Every application traced from send-off to final decision — replies, interview
            rounds, offers, and the stages that leak the most candidates. Hover any stage
            or flow ribbon for exact numbers.
          </p>
        </section>

        {stats.map(stat => (
          <div key={stat.label} className={`card an-stat tone-${stat.tone}`}>
            <span>{stat.label}</span>
            <strong>{stat.value}</strong>
            <em>{stat.sub}</em>
          </div>
        ))}
      </div>

      <section className="card an-chart-card">
        <div className="an-chart-head">
          <div>
            <span className="eyebrow">Funnel</span>
            <h3>Application flow</h3>
          </div>
          <div className="an-legend">
            {LEGEND.map(item => (
              <span key={item.label}>
                <i style={{ background: item.color }} />
                {item.label}
              </span>
            ))}
          </div>
        </div>

        <SankeyChart nodes={flow.nodes} links={flow.links} />

        <p className="an-footnote">
          Ribbons are colored by their stage and width is proportional to the number of
          applications; stage totals sit beside each node. Data comes live from your
          pipeline's application statuses.
        </p>
      </section>
    </div>
  );
}
