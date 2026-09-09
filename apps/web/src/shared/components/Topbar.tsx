import Icon from "./Icon";
import type { OperationProgress, View } from "../../types";
import type { IngestionJob } from "../../api/types";
import { useTheme } from "../lib/theme";
import { useAppContext } from "../context/AppContext";

const COPY: Record<View, { title: string; subtitle: string }> = {
  dashboard: {
    title: "Home",
    subtitle: "Your next useful step, without the busywork.",
  },
  apply: {
    title: "Tailor & apply",
    subtitle: "Create truthful, job-specific documents and prepare the application.",
  },
  cart: {
    title: "Apply cart",
    subtitle: "Jobs you set aside — tailor each one, then apply.",
  },
  pipeline: {
    title: "Find jobs",
    subtitle: "Search, filter, and save roles worth your time.",
  },
  "pipeline-hot": {
    title: "Best matches",
    subtitle: "Roles most closely aligned with what you asked for.",
  },
  "pipeline-found": {
    title: "New jobs",
    subtitle: "Fresh roles that have not been reviewed yet.",
  },
  "pipeline-evaluated": {
    title: "Reviewed jobs",
    subtitle: "Roles GalaxyHire has compared with your preferences.",
  },
  "pipeline-generated": {
    title: "Documents ready",
    subtitle: "Applications with a tailored résumé or cover letter.",
  },
  "pipeline-applied": {
    title: "Applications",
    subtitle: "Track what you sent and what happened next.",
  },
  analytics: {
    title: "Job Search Analytics",
    subtitle: "See how your applications convert into interviews and offers.",
  },
  "pipeline-discarded": {
    title: "Hidden jobs",
    subtitle: "Jobs you removed or marked as not relevant.",
  },
  email: {
    title: "Email updates",
    subtitle: "Use replies from employers to keep your application statuses current.",
  },
  activity: {
    title: "Activity log",
    subtitle: "A technical record of recent background work.",
  },
  profile: {
    title: "Your profile",
    subtitle: "Keep the facts used for matching and document generation accurate.",
  },
  ingestion: {
    title: "Add experience",
    subtitle: "Import a résumé, portfolio, GitHub profile, or project.",
  },
};

export function Topbar({
  view,
  progress,
  activeIngestion: propActiveIngestion,
}: {
  view: View;
  progress?: OperationProgress;
  activeIngestion?: IngestionJob | null;
}) {
  const { resolved, setPref } = useTheme();
  const appCtx = useAppContext();
  const activeIngestion = propActiveIngestion !== undefined ? propActiveIngestion : appCtx?.activeIngestion;
  const copy = COPY[view];

  return (
    <header className="topbar gh-topbar">
      <div className="gh-topbar-copy">
        <h1>{copy.title}</h1>
        <p>{copy.subtitle}</p>
      </div>

      {activeIngestion && activeIngestion.status === "processing" && (
        <div
          className="gh-work-status ingestion-indicator-pill"
          role="status"
          style={{
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            padding: "5px 12px",
            background: "var(--teal-soft)",
            border: "2px solid var(--hard)",
            color: "var(--teal-ink)",
            fontSize: "12.5px",
            fontWeight: 600,
            fontFamily: "var(--mono), var(--font-mono), monospace",
          }}
          onClick={() => {
            if (appCtx?.setView) appCtx.setView("ingestion");
          }}
          title="Resume ingestion in progress — click to view extraction stream"
        >
          <span className="pulse-soft" style={{ display: "inline-block" }}>⟳</span>
          <span>
            {`[ ⟳ Parsing ${activeIngestion.filename || "resume"} · Stage ${activeIngestion.stage || 1}/4 (${activeIngestion.elapsedSeconds || 0}s) ]`}
          </span>
        </div>
      )}

      {progress?.active && (
        <div className="gh-work-status" role="status">
          <span className="gh-live-dot" />
          <div>
            <strong>{progress.mode === "reevaluate" ? "Updating matches" : "Finding jobs"}</strong>
            <span>
              {progress.total
                ? `${Math.min(progress.completed, progress.total)} of ${progress.total}`
                : progress.current || `${progress.completed} found`}
            </span>
          </div>
        </div>
      )}

      {view === "profile" && (
        <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent("profile-export"))}>
          <Icon name="download" size={13} /> Export profile
        </button>
      )}

      <button
        className="gh-theme-button"
        onClick={() => setPref(resolved === "dark" ? "light" : "dark")}
        title={resolved === "dark" ? "Use light theme" : "Use dark theme"}
        aria-label={resolved === "dark" ? "Use light theme" : "Use dark theme"}
      >
        <Icon name={resolved === "dark" ? "sun" : "moon"} size={17} />
      </button>
    </header>
  );
}
