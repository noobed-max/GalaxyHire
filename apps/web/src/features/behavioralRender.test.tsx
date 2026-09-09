import { describe, expect, it, mock } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ApiFetch, Lead } from "../types";

/* Bun's runner has no `vi.mock` hoisting: mock.module() must be registered
 * BEFORE the modules that transitively import the Tauri opener plugin, so
 * every component import below is dynamic and happens after this call. */
mock.module("@tauri-apps/plugin-opener", () => ({
  openUrl: () => undefined,
}));

const { DashboardView } = await import("./dashboard/DashboardView");
const { PortalToggles } = await import("./dashboard/PortalToggles");
const { JobDetailsPanel } = await import("./pipeline/components/JobDetailsPanel");
const { CartView } = await import("./cart/CartView");
const { MiscCard } = await import("./profile/MiscCard");
const { ApplyJobView } = await import("./apply/ApplyJobView");
const { JobCard, PipelineJobCard } = await import("./pipeline/components/JobCard");
const { IngestionView } = await import("./profile/IngestionView");
const { ProfileView } = await import("./profile/ProfileView");
const ErrorBoundary = (await import("../shared/components/ErrorBoundary")).default;

const okResponse = (body: unknown = {}) => ({
  ok: true,
  status: 200,
  json: async () => body,
  blob: async () => new Blob(["pdf"], { type: "application/pdf" }),
}) as Response;

/* Recording stand-in for vitest's `vi.fn()` — assertions read `apiCalls`. */
const apiCalls: Array<[string, RequestInit?]> = [];
const api = (async (path: string, init?: RequestInit) => {
  apiCalls.push([path, init]);
  return okResponse({
    n: "Ada Lovelace",
    skills: [{ n: "Python", cat: "technical" }],
    projects: [],
    exp: [],
  });
}) as unknown as ApiFetch & { __calls: typeof apiCalls };
(api as any).__calls = apiCalls;
const noop = () => {};

const lead: Lead = {
  job_id: "lead-1",
  title: "Backend Engineer",
  company: "Acme AI",
  url: "https://example.com/jobs/1",
  platform: "manual",
  status: "approved",
  asset: "",
  score: 91,
  signal_score: 84,
  reason: "Strong FastAPI match",
  description: "Build Python APIs and reliable AI workflows.",
  match_points: ["Python", "FastAPI"],
  gaps: [],
  source_meta: { seniority_level: "junior" },
};

describe("high-risk component behavioral render coverage", () => {
  it("renders DashboardView with live lead data and primary controls", () => {
    const html = renderToStaticMarkup(
      <DashboardView
        leads={[lead]}
        dueFollowups={[lead]}
        logs={[{ id: 1, ts: "now", msg: "scan complete", src: "test", kind: "agent" }]}
        setView={noop}
        openDrawer={noop}
        scanning={false}
        reevaluating={false}
        cleaning={false}
        progress={{ active: false, mode: null, total: 0, completed: 0, current: "", updatedAt: 0 }}
        onScan={noop}
        onStopScan={noop}
        onReevaluate={noop}
        onStopReevaluate={noop}
        onCleanup={noop}
        scanErr={null}
      />,
    );

    expect(html).toContain("Find new jobs");
    expect(html).toContain("Acme AI");
    expect(html).toContain("Backend Engineer");
  });

  it("renders the location inputs on the dashboard search", () => {
    const html = renderToStaticMarkup(
      <DashboardView
        leads={[lead]}
        dueFollowups={[lead]}
        logs={[]}
        setView={noop}
        openDrawer={noop}
        scanning={false}
        reevaluating={false}
        cleaning={false}
        onScan={noop}
        onStopScan={noop}
        onReevaluate={noop}
        onStopReevaluate={noop}
        onCleanup={noop}
        scanErr={null}
      />,
    );

    expect(html).toContain("Country");
    expect(html).toContain("United Kingdom");
  });

  it("renders JobCard and keeps generation CTA visible", () => {
    const html = renderToStaticMarkup(
      <JobCard lead={lead} onOpen={noop} onDelete={noop} showScore showGenerate port={1420} api={api} />,
    );

    expect(html).toContain("Backend Engineer");
    expect(html).toContain("Generate Resume");
    expect(html).toContain("91%");
  });

  it("renders profile and ingestion entry points without crashing", () => {
    const profileHtml = renderToStaticMarkup(<ProfileView api={api} setView={noop} />);
    const ingestionHtml = renderToStaticMarkup(<IngestionView api={api} />);

    expect(profileHtml).toContain("Profile");
    expect(ingestionHtml).toContain("Resume");
  });

  it("renders apply and details workflows with mocked API surface", () => {
    const applyHtml = renderToStaticMarkup(
      <ApplyJobView port={1420} api={api} leads={[lead]} openDrawer={noop} initialInput="https://example.com/jobs/1" />,
    );
    const detailsHtml = renderToStaticMarkup(<JobDetailsPanel j={lead} api={api} onClose={noop} />);

    expect(applyHtml).toContain("job");
    expect(detailsHtml).toContain("Backend Engineer");
    expect(detailsHtml).toContain("Job Description");
    expect(detailsHtml).not.toContain("Generate Package");
  });

  it("renders the cart with its jobs and builders", () => {
    const cartHtml = renderToStaticMarkup(
      <CartView api={api} leads={[lead]} cart={["lead-1"]} removeFromCart={noop} />,
    );
    expect(cartHtml).toContain("Backend Engineer");
    expect(cartHtml).toContain("Generate Resume");

    const emptyHtml = renderToStaticMarkup(
      <CartView api={api} leads={[]} cart={[]} removeFromCart={noop} />,
    );
    expect(emptyHtml).toContain("Your cart is empty");
  });

  it("renders the miscellaneous data box", () => {
    const html = renderToStaticMarkup(<MiscCard api={api} />);
    expect(html).toContain("Miscellaneous user data");
    expect(html).toContain("Nothing stored yet");
  });

  it("reports ErrorBoundary crashes through the configured API", () => {
    apiCalls.length = 0;
    const boundary = new ErrorBoundary({ label: "Pipeline", api, children: null });
    const nextState = ErrorBoundary.getDerivedStateFromError(new Error("boom"));

    expect(nextState.error.message).toBe("boom");
    boundary.componentDidCatch(new Error("boom"), { componentStack: "\n<Component />" });
    expect(apiCalls[0][0]).toBe("/api/v1/errors");
    expect((apiCalls[0][1] as RequestInit).method).toBe("POST");
  });
});

describe("JobCard not-relevant control", () => {
  // The control writes a durable preference that hides the job from every future search, so it
  // must only appear where that is actually possible — the signal is keyed by canonical job id.
  const corpusLead: Lead = {
    ...lead,
    job_id: "canon-abc",
    source_meta: { seniority_level: "junior", corpus_job_id: "canon-abc" },
  };

  it("offers the control for a corpus-sourced lead", () => {
    const html = renderToStaticMarkup(
      <JobCard lead={corpusLead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).toContain("Not relevant");
  });

  it("hides the control when the lead has no corpus id", () => {
    // `lead` came from elsewhere, so there is nothing to key a signal to. Showing the button would
    // offer an action that silently does nothing.
    const html = renderToStaticMarkup(
      <JobCard lead={lead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).not.toContain("Not relevant");
  });

  it("hides the control when there is no api to record through", () => {
    const html = renderToStaticMarkup(
      <JobCard lead={corpusLead} onOpen={noop} onDelete={noop} api={null} />,
    );
    expect(html).not.toContain("Not relevant");
  });

  it("keeps Remove distinct from Not relevant", () => {
    // Two different actions: one drops the row from this list, the other trains future searches.
    // Their tooltips have to say so, or the durable one gets clicked by accident.
    const html = renderToStaticMarkup(
      <JobCard lead={corpusLead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).toContain("Remove from this list");
    expect(html).toContain("future searches");
  });
});

describe("the not-relevant control reaches the component the app renders", () => {
  /* The first version of this feature passed every test and shipped nothing.
   *
   * JobCard.tsx exports two cards. PipelineView renders `PipelineJobCard`; `JobCard` is imported
   * only by this file. Adding the control to `JobCard` alone produced a green suite and a bundle
   * whose hash did not even change — the component was never in it. Tests that import a component
   * directly cannot tell you whether anything renders it.
   */
  const corpusLead: Lead = {
    ...lead,
    job_id: "canon-xyz",
    source_meta: { seniority_level: "junior", corpus_job_id: "canon-xyz" },
  };

  it("PipelineJobCard offers it — this is the card PipelineView actually mounts", () => {
    const html = renderToStaticMarkup(
      <PipelineJobCard lead={corpusLead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).toContain("Not relevant");
  });

  it("PipelineJobCard hides it for a non-corpus lead", () => {
    const html = renderToStaticMarkup(
      <PipelineJobCard lead={lead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).not.toContain("Not relevant");
  });

  it("keeps it distinct from the delete action", () => {
    const html = renderToStaticMarkup(
      <PipelineJobCard lead={corpusLead} onOpen={noop} onDelete={noop} api={api} />,
    );
    expect(html).toContain("Remove job from this list");
    expect(html).toContain("future searches");
  });
});

/* Portal toggle column (MAJOR-CHANGE/06). Static markup only — bun's runner does not execute
 * effects, so the interactive path (load catalog → toggle → persist) is covered by the live
 * manual probe in the MAJOR-CHANGE checklist, not here. What IS pinned: the panel renders in
 * the dashboard with its accessible name, and without an api handle it renders nothing rather
 * than a dead control surface. */
describe("portal toggles", () => {
  it("renders the labeled panel when the backend is reachable", () => {
    const html = renderToStaticMarkup(<PortalToggles api={api} />);
    expect(html).toContain("Job portals");
    expect(html).toContain("Loading portals"); // catalog arrives via effect, static render is empty
  });

  it("renders nothing without an api handle", () => {
    expect(renderToStaticMarkup(<PortalToggles api={null} />)).toBe("");
  });

  it("badges source-proved freshness and shows when each job was posted", () => {
    // 2026-09 ruling: every result shows its posting time; only a source date inside 24h
    // badges "<24h". Older and undated rows show the date (or nothing), never a badge.
    const posted = renderToStaticMarkup(
      <JobCard lead={{ ...lead, source_meta: { freshness: "posted", date_posted: new Date(Date.now() - 3600000).toISOString() } }} onOpen={noop} onDelete={noop} />,
    );
    expect(posted).toContain("&lt;24h");
    expect(posted).toContain("posted today");
    const oldDate = new Date(Date.now() - 15 * 86400000).toISOString();
    const old = renderToStaticMarkup(
      <PipelineJobCard lead={{ ...lead, source_meta: { date_posted: oldDate } }} onOpen={noop} onDelete={noop} />,
    );
    expect(old).toContain("posted 15d ago");
    expect(old).not.toContain("&lt;24h");
    const ancient = renderToStaticMarkup(
      <PipelineJobCard lead={{ ...lead, source_meta: { date_posted: "2026-05-01T10:00:00Z" } }} onOpen={noop} onDelete={noop} />,
    );
    expect(ancient).toContain("posted 2026-05-01");
    const legacy = renderToStaticMarkup(
      <JobCard lead={lead} onOpen={noop} onDelete={noop} />,
    );
    expect(legacy).not.toContain("posted today");
  });
});
