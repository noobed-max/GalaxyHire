import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "../../shared/components/Icon";
import type { ApiFetch, Lead, LogLine, OperationProgress, View } from "../../types";
import { getMark, leadDisplayHeading, leadSignal } from "../../shared/lib/leadUtils";
import { settingsApi } from "../../api/settings";
import { ScrapeProgress } from "../pipeline/components/ScrapeProgress";
import { PortalSidePanel } from "./PortalSidePanel";
import { roleFromBrief } from "../../shared/lib/searchState";

function Metric({
  value,
  label,
  hint,
}: {
  value: number;
  label: string;
  hint: string;
}) {
  return (
    <div className="gh-metric">
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{hint}</small>
    </div>
  );
}

function JobRow({
  lead,
  openDrawer,
  carted,
  onAddToCart,
}: {
  lead: Lead;
  openDrawer: (lead: Lead) => void;
  carted?: boolean;
  onAddToCart?: (lead: Lead) => void;
}) {
  const { role, company } = leadDisplayHeading(lead);
  const match = lead.score || leadSignal(lead);

  return (
    <button className="gh-next-job" onClick={() => openDrawer(lead)}>
      <span className="gh-company-mark">{getMark(company)}</span>
      <span className="gh-next-job-copy">
        <strong>{role}</strong>
        <small>
          {company}
          {lead.location ? ` · ${lead.location}` : ""}
        </small>
      </span>
      {match > 0 && <span className="gh-match-badge">{Math.round(match)}% match</span>}
      {onAddToCart && (
        <span
          role="button"
          tabIndex={0}
          aria-label={carted ? "Already in apply cart" : "Add to apply cart"}
          title={carted ? "Already in your apply cart" : "Add this job to your apply cart"}
          onClick={(e) => {
            e.stopPropagation();
            if (!carted) onAddToCart(lead);
          }}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !carted) {
              e.stopPropagation();
              onAddToCart(lead);
            }
          }}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 11,
            fontWeight: 700,
            color: carted ? "var(--ink-4)" : "var(--green-ink)",
            cursor: carted ? "default" : "pointer",
            flexShrink: 0,
          }}
        >
          <Icon name="cart" size={13} /> {carted ? "In cart" : "Add"}
        </span>
      )}
      <Icon name="arrow-right" size={14} />
    </button>
  );
}

export function DashboardView({
  leads,
  dueFollowups,
  logs,
  setView,
  openDrawer,
  scanning,
  reevaluating,
  cleaning,
  progress,
  onScan,
  onStopScan,
  onReevaluate,
  onStopReevaluate,
  onCleanup,
  scanErr,
  api = null,
  cart = [],
  addToCart,
}: {
  leads: Lead[];
  dueFollowups: Lead[];
  logs: LogLine[];
  setView: (view: View) => void;
  openDrawer: (lead: Lead) => void;
  scanning: boolean;
  reevaluating: boolean;
  cleaning: boolean;
  progress?: OperationProgress;
  onScan: (brief?: string, portals?: string[] | null, location?: string) => void;
  onStopScan: () => void;
  onReevaluate: () => void;
  onStopReevaluate: () => void;
  onCleanup: () => void;
  scanErr: string | null;
  api?: ApiFetch | null;
  cart?: string[];
  addToCart?: (jobId: string) => void;
}) {
  const [roleBrief, setRoleBrief] = useState("");
  const [roleLoaded, setRoleLoaded] = useState(false);
  const [roleStatus, setRoleStatus] = useState<"" | "saving" | "saved" | "error">("");
  const lastSavedRole = useRef("");

  // Country & City location states
  const [country, setCountry] = useState("");
  const [city, setCity] = useState("");
  const [locationLoaded, setLocationLoaded] = useState(false);
  const lastSavedLocation = useRef("");

  // Portals side panel state
  const [portalsOpen, setPortalsOpen] = useState(false);
  const [portalCount, setPortalCount] = useState<number | null>(null);
  const [portalIds, setPortalIds] = useState<string[] | null>(null);

  const active = leads.filter((lead) => lead.status !== "discarded");
  const ready = active.filter((lead) => lead.status === "tailoring" || lead.status === "approved");
  const applied = active.filter((lead) =>
    ["applied", "interviewing", "accepted", "rejected"].includes(lead.status),
  );
  const strongMatches = active.filter((lead) => (lead.score || leadSignal(lead)) >= 75);
  const nextJobs = [...active]
    .filter((lead) => !["applied", "rejected", "accepted"].includes(lead.status))
    .sort((a, b) => (b.score || leadSignal(b)) - (a.score || leadSignal(a)))
    .slice(0, 4);

  const busy = scanning || reevaluating || cleaning;
  const latestActivity = logs[0]?.msg;
  const role = roleFromBrief(roleBrief);

  // Combined location: empty if both country and city are blank
  const location = useMemo(
    () => [city.trim(), country.trim()].filter(Boolean).join(", "),
    [city, country],
  );

  // Load preferences (role prompt)
  useEffect(() => {
    if (!api) return;
    let alive = true;
    settingsApi
      .getPreferences(api)
      .then((response) => response.json())
      .then((data) => {
        if (!alive) return;
        const saved = String(data?.preferences || "");
        setRoleBrief(saved);
        lastSavedRole.current = saved;
        setRoleLoaded(true);
      })
      .catch(() => {
        if (alive) setRoleLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [api]);

  // Load location settings
  useEffect(() => {
    if (!api) return;
    let alive = true;
    settingsApi
      .get(api)
      .then((response) => response.json())
      .then((data: Record<string, unknown>) => {
        if (!alive) return;
        const c = String(data?.search_location_country || "");
        const t = String(data?.search_location_city || "");
        setCountry(c);
        setCity(t);
        lastSavedLocation.current = `${t}\n${c}`;
        setLocationLoaded(true);
      })
      .catch(() => {
        if (alive) setLocationLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [api]);

  // Save role preferences on blur
  const saveRolePreferences = useCallback(async () => {
    if (!api || roleBrief === lastSavedRole.current) return;
    setRoleStatus("saving");
    try {
      const response = await settingsApi.savePreferences(api, roleBrief);
      if (!response.ok) throw new Error("Save failed");
      lastSavedRole.current = roleBrief;
      setRoleStatus("saved");
      window.setTimeout(() => setRoleStatus(""), 1800);
    } catch {
      setRoleStatus("error");
    }
  }, [api, roleBrief]);

  // Save location on blur
  const saveLocation = useCallback(async () => {
    if (!api) return;
    const key = `${city.trim()}\n${country.trim()}`;
    if (key === lastSavedLocation.current) return;
    try {
      await settingsApi.save(api, {
        search_location_country: country.trim(),
        search_location_city: city.trim(),
      });
      lastSavedLocation.current = key;
    } catch {
      // Best-effort persist: current field values are still sent directly onScan
    }
  }, [api, country, city]);

  const handleStartScan = useCallback(() => {
    // Persist before scan
    void saveRolePreferences();
    void saveLocation();
    onScan(roleBrief, portalIds, location);
  }, [roleBrief, portalIds, location, onScan, saveRolePreferences, saveLocation]);

  return (
    <main className="gh-page gh-dashboard scroll">
      {/* Minimal Top Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          marginBottom: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <span className="gh-kicker" style={{ marginBottom: 4 }}>GALAXYHIRE</span>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800, letterSpacing: "-0.02em" }}>
            Job Discovery
          </h1>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button
            className="gh-button gh-button-secondary"
            onClick={() => setPortalsOpen(true)}
            title="Configure scraper job portals"
          >
            <Icon name="grid" size={14} />
            Sources ({portalCount !== null ? portalCount : "…"})
          </button>
          <button className="gh-button gh-button-secondary" onClick={() => setView("pipeline")}>
            Browse saved jobs ({active.length}) <Icon name="arrow-right" size={13} />
          </button>
          <button className="gh-button gh-button-quiet" onClick={() => setView("apply")}>
            Tailor a job
          </button>
        </div>
      </header>

      {/* Unified Minimal Search Console */}
      <section
        className="gh-card gh-search-console"
        aria-label="Job search console"
        style={{
          padding: "18px 22px",
          marginBottom: 20,
          border: "2px solid var(--hard)",
          background: "var(--card)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <div className="gh-search-console-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <div className="gh-search-console-title-wrap">
            <span className="gh-kicker">YOUR SEARCH</span>
            <h2 style={{ margin: "4px 0 0", fontSize: 16, fontWeight: 750 }}>What kind of job do you want?</h2>
          </div>
          {roleStatus && (
            <span className={`gh-save-state ${roleStatus}`}>
              {roleStatus === "saving" ? "Saving…" : roleStatus === "saved" ? "Saved" : "Could not save"}
            </span>
          )}
        </div>

        {/* Role Brief Input */}
        <div className="gh-search-console-body" style={{ marginBottom: 12 }}>
          <textarea
            className="field-input field-input--ta gh-search-console-textarea"
            value={roleBrief}
            disabled={!roleLoaded && !!api}
            onChange={(event) => setRoleBrief(event.target.value)}
            onBlur={saveRolePreferences}
            rows={2}
            placeholder="For example: junior backend roles using Python, remote, product companies, no senior"
            aria-label="Describe the job you want"
            style={{
              width: "100%",
              minHeight: 64,
              padding: "10px 12px",
              border: "2px solid var(--hard)",
              background: "var(--paper)",
              fontSize: 13.5,
              lineHeight: 1.5,
              resize: "vertical",
            }}
          />
        </div>

        {/* Integrated Controls Bar: Country, City, Sources, and Find New Jobs */}
        <div
          className="gh-search-console-toolbar"
          style={{
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "space-between",
            gap: 16,
            flexWrap: "wrap",
            paddingTop: 12,
            borderTop: "1px dashed var(--line)",
          }}
        >
          <div
            className="gh-search-location-group"
            style={{
              display: "flex",
              alignItems: "flex-end",
              gap: 12,
              flex: 1,
              minWidth: 280,
            }}
          >
            <label
              className="gh-search-location-field"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                flex: 1,
                minWidth: 140,
              }}
            >
              <span className="gh-location-label" style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-3)" }}>
                Country
              </span>
              <input
                className="field-input"
                value={country}
                disabled={!locationLoaded && !!api}
                onChange={(event) => setCountry(event.target.value)}
                onBlur={saveLocation}
                placeholder="United Kingdom"
                aria-label="Country to search in"
                style={{
                  height: 38,
                  padding: "0 10px",
                  border: "2px solid var(--hard)",
                  background: "var(--paper)",
                  fontSize: 12.5,
                }}
              />
            </label>
            <label
              className="gh-search-location-field"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                flex: 1,
                minWidth: 140,
              }}
            >
              <span className="gh-location-label" style={{ fontSize: 10, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--ink-3)" }}>
                City (optional)
              </span>
              <input
                className="field-input"
                value={city}
                disabled={!locationLoaded && !!api}
                onChange={(event) => setCity(event.target.value)}
                onBlur={saveLocation}
                placeholder="London"
                aria-label="City to search in"
                style={{
                  height: 38,
                  padding: "0 10px",
                  border: "2px solid var(--hard)",
                  background: "var(--paper)",
                  fontSize: 12.5,
                }}
              />
            </label>
          </div>

          <div
            className="gh-search-actions-group"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
          >
            <button
              type="button"
              className="gh-button gh-button-secondary gh-portals-trigger-btn"
              onClick={() => setPortalsOpen(true)}
              title="Open job portals side panel"
              style={{ height: 38, whiteSpace: "nowrap" }}
            >
              <Icon name="grid" size={13} />
              <span>
                Portals: <b>{portalCount !== null ? portalCount : "…"}</b>
              </span>
            </button>

            {scanning ? (
              <button
                className="gh-button gh-button-danger"
                onClick={onStopScan}
                style={{ height: 38, whiteSpace: "nowrap" }}
              >
                <Icon name="x" size={14} /> Stop finding jobs
              </button>
            ) : (
              <button
                className="gh-button gh-button-primary"
                onClick={handleStartScan}
                disabled={busy || portalCount === 0}
                title={portalCount === 0 ? "Turn at least one portal on in Sources" : undefined}
                style={{ height: 38, whiteSpace: "nowrap" }}
              >
                <Icon name="search" size={14} /> Find new jobs
              </button>
            )}
          </div>
        </div>

        {scanErr && <div className="gh-inline-error" style={{ marginTop: 10 }}>{scanErr}</div>}

        <div className="gh-role-help" style={{ marginTop: 10 }}>
          <Icon name="spark" size={13} />
          Your résumé can improve tie-breaks, but it will never hide jobs from you.
          {location ? (
            <span style={{ marginLeft: 6, color: "var(--ink-2)" }}>
              Location filter: <b>{location}</b>
            </span>
          ) : (
            <span style={{ marginLeft: 6, color: "var(--ink-4)" }}>
              (No location specified — searching worldwide)
            </span>
          )}
        </div>
      </section>

      {/* Active Scan Status */}
      {scanning && (
        <section className="gh-search-status" role="status" aria-live="polite">
          <span className="dot pulse-soft" aria-hidden />
          <div>
            <strong>{role ? `Starting a fresh scrape for “${role}”` : "Starting a fresh job scrape"}</strong>
            <p>
              Collecting fresh results from your selected portals. Experience and title exclusions
              are applied after collection, before jobs reach your list.
            </p>
            {progress?.current && <small>{progress.current}</small>}
          </div>
          <button className="gh-button gh-button-danger" onClick={onStopScan}>
            <Icon name="x" size={14} /> Stop
          </button>
        </section>
      )}

      <ScrapeProgress api={api} onStop={onStopScan} />

      {/* Key Metrics Strip */}
      <section className="gh-metrics" aria-label="Job search summary">
        <Metric value={active.length} label="Saved jobs" hint="available to review" />
        <Metric value={strongMatches.length} label="Strong matches" hint="75% match or better" />
        <Metric value={ready.length} label="Documents ready" hint="résumé or cover letter" />
        <Metric value={applied.length} label="Applications sent" hint="including outcomes" />
      </section>

      {/* Main Grid: Shortlist & Next Actions */}
      <div className="gh-dashboard-grid">
        <section className="gh-card gh-next-card">
          <div className="gh-section-heading">
            <div>
              <span className="gh-kicker">RECOMMENDED NEXT</span>
              <h2>{nextJobs.length ? "Jobs worth a closer look" : "Your shortlist will appear here"}</h2>
            </div>
            {nextJobs.length > 0 && (
              <button className="gh-text-button" onClick={() => setView("pipeline-hot")}>
                See all <Icon name="arrow-right" size={13} />
              </button>
            )}
          </div>
          <div className="gh-next-list">
            {nextJobs.length ? (
              nextJobs.map((lead) => (
                <JobRow
                  key={lead.job_id}
                  lead={lead}
                  openDrawer={openDrawer}
                  carted={cart?.includes(lead.job_id)}
                  onAddToCart={addToCart ? (l) => addToCart(l.job_id) : undefined}
                />
              ))
            ) : (
              <div className="gh-empty-compact">
                <Icon name="search" size={20} />
                <p>
                  Describe what you want, then choose <b>Find new jobs</b>.
                </p>
              </div>
            )}
          </div>
        </section>

        <aside className="gh-card gh-action-card">
          <span className="gh-kicker">KEEP MOVING</span>
          <h2>Your next actions</h2>
          <button onClick={() => setView("pipeline-generated")}>
            <span className="gh-action-icon">
              <Icon name="file" size={16} />
            </span>
            <span>
              <b>Review tailored documents</b>
              <small>{ready.length} ready or being prepared</small>
            </span>
            <Icon name="arrow-right" size={13} />
          </button>
          <button onClick={() => setView("pipeline-applied")}>
            <span className="gh-action-icon">
              <Icon name="pulse" size={16} />
            </span>
            <span>
              <b>Check applications</b>
              <small>
                {dueFollowups.length} follow-up{dueFollowups.length === 1 ? "" : "s"} due
              </small>
            </span>
            <Icon name="arrow-right" size={13} />
          </button>
          <button onClick={() => setView("profile")}>
            <span className="gh-action-icon">
              <Icon name="user" size={16} />
            </span>
            <span>
              <b>Check your profile</b>
              <small>Keep facts and experience current</small>
            </span>
            <Icon name="arrow-right" size={13} />
          </button>
          {latestActivity && <p className="gh-latest-activity">Latest: {latestActivity}</p>}
        </aside>
      </div>

      {/* Maintenance Tools */}
      <details className="gh-maintenance">
        <summary>Job list tools</summary>
        <div>
          <p>Use these only when the saved list looks stale or contains obvious non-job content.</p>
          {reevaluating ? (
            <button className="btn" onClick={onStopReevaluate}>
              Stop updating matches
            </button>
          ) : (
            <button className="btn" onClick={onReevaluate} disabled={!leads.length || busy}>
              Update match scores
            </button>
          )}
          <button className="btn" onClick={onCleanup} disabled={!leads.length || busy}>
            {cleaning ? "Removing bad results…" : "Remove obvious bad results"}
          </button>
        </div>
      </details>

      {/* Bookmarks-style slide-over side panel for job portals */}
      <PortalSidePanel
        open={portalsOpen}
        onClose={() => setPortalsOpen(false)}
        api={api}
        onEnabledChange={setPortalCount}
        onSelectionChange={setPortalIds}
      />
    </main>
  );
}
