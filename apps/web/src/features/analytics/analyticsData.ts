/**
 * Application-funnel data derived from REAL pipeline leads.
 *
 * The pipeline tracks each job through: discovered → evaluated → tailoring →
 * approved → applied → interviewing → accepted | rejected (plus discarded).
 * The "Applications" view counts every lead whose status is applied,
 * interviewing, accepted, or rejected — that same set is the funnel here.
 *
 * This replaces the original static spec snapshot (73 fake applications); the
 * chart shows nothing until real applications exist.
 */

import type { Lead } from "../../types";

export interface FlowNodeRaw {
  name: string;
  color: string;
  order: number;
}

export interface FlowLinkRaw {
  source: number;
  target: number;
  value: number;
}

/** Stage colors read the app's theme tokens, so dark mode just works. */
const C = {
  hub: "var(--ink)",
  muted: "var(--ink-3)",
  waiting: "var(--yellow)",
  interviewing: "var(--teal)",
  rejected: "var(--bad)",
  offer: "var(--ok)",
};

export const APPLICATION_STATUSES = ["applied", "interviewing", "accepted", "rejected"] as const;

export const applicationsOf = (leads: Lead[]): Lead[] =>
  leads.filter(l => (APPLICATION_STATUSES as readonly string[]).includes(l.status));

export function buildFlowData(leads: Lead[]): { nodes: FlowNodeRaw[]; links: FlowLinkRaw[] } {
  const apps = applicationsOf(leads);
  const count = (status: string) => apps.filter(l => l.status === status).length;

  const awaiting = count("applied");
  const interviewing = count("interviewing");
  const rejected = count("rejected");
  const accepted = count("accepted");

  const nodes: FlowNodeRaw[] = [
    { name: "Total Applications", color: C.hub, order: 0 },
    { name: "Awaiting Reply", color: C.waiting, order: 1 },
    { name: "Interviewing", color: C.interviewing, order: 2 },
    { name: "Rejected", color: C.rejected, order: 3 },
    { name: "Accepted", color: C.offer, order: 4 },
  ];
  const N = Object.fromEntries(nodes.map((n, i) => [n.name, i])) as Record<string, number>;

  const links: FlowLinkRaw[] = [];
  // Only emit flows that exist so the diagram never draws zero-width ribbons.
  if (awaiting) links.push({ source: N["Total Applications"], target: N["Awaiting Reply"], value: awaiting });
  if (interviewing) links.push({ source: N["Total Applications"], target: N["Interviewing"], value: interviewing });
  if (rejected) links.push({ source: N["Total Applications"], target: N["Rejected"], value: rejected });
  if (accepted) links.push({ source: N["Total Applications"], target: N["Accepted"], value: accepted });

  return { nodes, links };
}

const pct = (part: number, whole: number) => (whole === 0 ? 0 : Math.round((part / whole) * 100));

export interface FlowStat {
  label: string;
  value: string;
  sub: string;
  tone: "neutral" | "info" | "good" | "warn" | "bad";
}

export function buildFlowStats(leads: Lead[]): FlowStat[] {
  const apps = applicationsOf(leads);
  const total = apps.length;
  if (total === 0) return [];

  const awaiting = apps.filter(l => l.status === "applied").length;
  const interviewing = apps.filter(l => l.status === "interviewing").length;
  const rejected = apps.filter(l => l.status === "rejected").length;
  const accepted = apps.filter(l => l.status === "accepted").length;
  const heardBack = total - awaiting;

  return [
    { label: "Total Applications", value: String(total), sub: "tracked in your pipeline", tone: "neutral" },
    { label: "Awaiting Reply", value: String(awaiting), sub: `${pct(awaiting, total)}% of applications`, tone: "info" },
    { label: "Response Rate", value: `${pct(heardBack, total)}%`, sub: `${heardBack} of ${total} moved forward`, tone: "info" },
    { label: "In Interviews", value: String(interviewing), sub: `${pct(interviewing, total)}% of applications`, tone: "good" },
    {
      label: "Outcomes",
      value: `${accepted}↑ ${rejected}↓`,
      sub: `${accepted} accepted · ${rejected} rejected`,
      tone: rejected > accepted ? "warn" : "good",
    },
  ];
}
