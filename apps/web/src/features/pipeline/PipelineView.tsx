import { useEffect, useMemo, useState } from "react";
import Icon from "../../shared/components/Icon";
import { LeadFilterBar } from "./components/LeadFilterBar";
import { ScrapeProgress } from "./components/ScrapeProgress";
import { PipelineJobCard, PipelineSkeleton } from "./components/JobCard";
import type { LocationSuggestion } from "../../api/discovery";
import type { ApiFetch, Lead, LeadSort, PipelineTab, SeniorityFilter } from "../../types";
import { locationMatches, PAGE_SIZE, leadSearchText, sortLeads, seniorityMatches, uniqueLeadValues } from "../../shared/lib/leadUtils";
import {
  ACTIVE_SEARCH_EVENT,
  activeFilterLabels,
  clearActiveSearch,
  readActiveSearch,
  type ActiveSearch,
} from "../../shared/lib/searchState";

export function PipelineView({ leads, openDrawer, deleteLead, port, api, scanning, reevaluating, cleaning, onStopScan, onReevaluate, onStopReevaluate, onCleanup, loading, error, tab, onTabChange, cart, addToCart }: {
  leads: Lead[]; openDrawer: (l: Lead) => void;
  deleteLead: (id: string) => void; port: number | null; api: ApiFetch | null;
  scanning: boolean; reevaluating: boolean; cleaning: boolean; onReevaluate: () => void; onStopReevaluate: () => void; onCleanup: () => void;
  onStopScan: () => void;
  loading: boolean; error: string | null;
  tab: PipelineTab;
  onTabChange: (tab: PipelineTab) => void;
  cart: string[]; addToCart: (jobId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [sort, setSort] = useState<LeadSort>("recommended");
  const [seniority, setSeniority] = useState<SeniorityFilter>("all");
  const [location, setLocation] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [bulkSelecting, setBulkSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const [activeSearch, setActiveSearch] = useState<ActiveSearch | null>(() => readActiveSearch());

  useEffect(() => setVisibleCount(PAGE_SIZE), [tab, search, platform, sort, seniority, location, activeSearch]);
  useEffect(() => {
    setBulkSelecting(false);
    setSelected(new Set());
  }, [tab]);

  useEffect(() => {
    const refresh = () => setActiveSearch(readActiveSearch());
    window.addEventListener(ACTIVE_SEARCH_EVENT, refresh);
    return () => window.removeEventListener(ACTIVE_SEARCH_EVENT, refresh);
  }, []);

  const platforms = useMemo(() => uniqueLeadValues(leads, "platform"), [leads]);
  const activeJobIds = useMemo(
    () => activeSearch ? new Set(activeSearch.jobIds) : null,
    [activeSearch],
  );
  const inferredFilters = useMemo(
    () => activeSearch ? activeFilterLabels(activeSearch.filters) : [],
    [activeSearch],
  );

  // Suggestions must describe the jobs this page can actually show. The backend's old dropdown
  // counted the entire corpus ("India 164") while the active role search contained nine India
  // jobs, so choosing the suggestion appeared to lose 155 results. Count over the active search
  // and the other visible filters, deliberately excluding the location filter itself.
  const locationSuggestions = useMemo<LocationSuggestion[]>(() => {
    const q = search.trim().toLowerCase();
    const counts = new Map<string, number>();
    for (const lead of leads) {
      if (activeJobIds && !activeJobIds.has(lead.job_id)) continue;
      if (q && !leadSearchText(lead).includes(q)) continue;
      if (platform && lead.platform !== platform) continue;
      if (!seniorityMatches(lead, seniority)) continue;

      const raw = String(lead.location || "").trim();
      if (!raw) continue;
      const labels = new Set<string>([raw]);
      for (const part of raw.replace(/[()]/g, ",").split(",")) {
        const label = part.trim();
        if (label) labels.add(label);
      }
      for (const label of labels) counts.set(label, (counts.get(label) || 0) + 1);
    }
    return [...counts.entries()]
      .map(([label, jobs]) => ({ label, jobs }))
      .sort((a, b) => b.jobs - a.jobs || a.label.localeCompare(b.label));
  }, [leads, activeJobIds, search, platform, seniority]);

  const tabs = useMemo(() => {
    const q = search.trim().toLowerCase();
    const loc = location.trim();
    const keep = (lead: Lead) => {
      if (activeJobIds && !activeJobIds.has(lead.job_id)) return false;
      if (q && !leadSearchText(lead).includes(q)) return false;
      if (platform && lead.platform !== platform) return false;
      if (!seniorityMatches(lead, seniority)) return false;
      // Substring, case-insensitive, and remote always passes. Mirrors the SQL filter in
      // galaxy/search/retrieval.py deliberately: the stored data puts a country in one row and a
      // bare state code in the next, so an exact match would hide most of a country's jobs, and a
      // remote job is not excluded by wanting to work from a particular place.
      if (loc) {
        if (!locationMatches(lead.location, loc)) return false;
      }
      return true;
    };
    const apply = (arr: Lead[]) => sortLeads(arr.filter(keep), sort);
    const tabItems: { id: PipelineTab; label: string; tone: string; leads: Lead[] }[] = [
      { id: "all",       label: "All jobs",        tone: "teal",   leads: apply(leads) },
      { id: "hot",       label: "Best matches",    tone: "orange", leads: apply(leads.filter(l => (l.signal_score || 0) >= 80 || (l.score || 0) >= 85)) },
      { id: "found",     label: "New",             tone: "blue",   leads: apply(leads.filter(l => l.status === "discovered")) },
      { id: "evaluated", label: "Reviewed",        tone: "yellow", leads: apply(leads.filter(l => l.score > 0 || (l.signal_score || 0) > 0)) },
      { id: "generated", label: "Documents ready", tone: "purple", leads: apply(leads.filter(l => l.status === "tailoring" || l.status === "approved")) },
      { id: "applied",   label: "Applications",    tone: "orange", leads: apply(leads.filter(l => ["applied", "interviewing", "accepted", "rejected"].includes(l.status))) },
      { id: "discarded", label: "Hidden",          tone: "bad",    leads: apply(leads.filter(l => l.status === "discarded")) },
    ];
    return tabItems;
  }, [leads, search, platform, sort, seniority, location, activeJobIds]);

  const activeTab = tabs.find(t => t.id === tab) || tabs[0];
  const visibleLeads = activeTab.leads.slice(0, visibleCount);
  const hasFilters = Boolean(activeSearch || search || platform || location || seniority !== "all" || sort !== "recommended");
  const busyLabel = scanning ? "Finding new jobs" : reevaluating ? "Updating match scores" : cleaning ? "Removing bad results" : "";

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const bulkDelete = async () => {
    if (!window.confirm(`Delete ${selected.size} leads?`)) return;
    const count = selected.size;
    const results = await Promise.allSettled([...selected].map(id => Promise.resolve(deleteLead(id))));
    const failed = results.filter(r => r.status === "rejected").length;
    if (failed > 0) alert(`${failed} of ${count} deletions failed. Refreshing list.`);
    setSelected(new Set());
    setBulkSelecting(false);
    window.dispatchEvent(new CustomEvent("leads-refresh"));
  };

  const bulkMarkApplied = async () => {
    if (!api || selected.size === 0) return;
    const ids = [...selected];
    const results = await Promise.allSettled(ids.map(id => api(`/api/v1/leads/${id}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "applied" }),
    })));
    const failed = results.filter(r => r.status === "rejected" || (r.status === "fulfilled" && !r.value.ok)).length;
    if (failed > 0) alert(`${failed} of ${ids.length} jobs could not be marked as applied.`);
    ids.forEach(job_id => window.dispatchEvent(new CustomEvent("lead-updated", { detail: { job_id, status: "applied" } })));
    setSelected(new Set());
    setBulkSelecting(false);
    window.dispatchEvent(new CustomEvent("leads-refresh"));
  };

  const exportCsv = async () => {
    if (!api || exporting) return;
    setExporting(true);
    setExportErr(null);
    try {
      const res = await api("/api/v1/leads/export.csv");
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "galaxyhire-applications.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="pipeline-page">
      <div className="pipeline-top">
        {(busyLabel || error || exportErr) && (
          <div className={`pipeline-notice ${error || exportErr ? "error" : ""}`}>
            {error || exportErr ? <Icon name="x" size={13} /> : <span className="dot pulse-soft" />}
            <span>{error || exportErr || busyLabel}</span>
          </div>
        )}

        <ScrapeProgress api={api} onStop={onStopScan} />
        <nav className="gh-job-tabs" aria-label="Job views">
          {tabs.map(item => (
            <button
              key={item.id}
              className={item.id === activeTab.id ? "active" : ""}
              onClick={() => onTabChange(item.id)}
              aria-current={item.id === activeTab.id ? "page" : undefined}
            >
              {item.label}
              <span>{item.leads.length}</span>
            </button>
          ))}
        </nav>
        {activeSearch && (
          <section className="pipeline-search-constraints" aria-label="Current search filters">
            <div className="pipeline-search-constraints-copy">
              <span>Current search</span>
              <strong>{activeSearch.role}</strong>
              <small>
                The role was collected broadly. These filters were applied afterwards, before the
                jobs appeared here.
              </small>
            </div>
            <div className="pipeline-search-chips">
              <span className="pipeline-search-chip role">Role: {activeSearch.role}</span>
              {inferredFilters.map(label => (
                <span className="pipeline-search-chip" key={label}>{label}</span>
              ))}
            </div>
            <button className="pipeline-clear" onClick={clearActiveSearch}>Show all saved jobs</button>
          </section>
        )}
        <LeadFilterBar
          search={search}
          setSearch={setSearch}
          platform={platform}
          setPlatform={setPlatform}
          sort={sort}
          setSort={setSort}
          seniority={seniority}
          setSeniority={setSeniority}
          location={location}
          setLocation={setLocation}
          api={api}
          locationSuggestions={locationSuggestions}
          platforms={platforms}
          total={activeTab.leads.length}
          shown={Math.min(visibleCount, activeTab.leads.length)}
          label="jobs"
          actions={(
            <>
              <button className="btn" onClick={exportCsv} disabled={!api || exporting || loading}>
                {exporting ? "Exporting…" : "Export list"}
              </button>
              {bulkSelecting ? (
                <>
                  <button className="btn" onClick={bulkMarkApplied} disabled={!api || selected.size === 0 || loading}>
                    <Icon name="check" size={13} /> Mark {selected.size} applied
                  </button>
                  <button className="btn" onClick={() => { setBulkSelecting(false); setSelected(new Set()); }}>Cancel</button>
                </>
              ) : (
                <button className="btn" onClick={() => setBulkSelecting(true)} disabled={activeTab.leads.length === 0 || loading}>
                  <Icon name="check" size={13} /> Select jobs
                </button>
              )}
              {reevaluating ? (
                <button className="btn danger" onClick={onStopReevaluate}>
                  <Icon name="x" size={13} /> Stop updating
                </button>
              ) : (
                <button className="btn" onClick={onReevaluate} disabled={leads.length === 0 || scanning || cleaning || loading}>
                  <Icon name="pulse" size={13} /> Update matches
                </button>
              )}
              <button className="btn danger-soft" onClick={onCleanup} disabled={leads.length === 0 || scanning || reevaluating || cleaning || loading}>
                <Icon name="trash" size={13} /> {cleaning ? "Cleaning…" : "Remove bad results"}
              </button>
              {tab === "discarded" && (
                bulkSelecting ? (
                  <button className="btn danger" onClick={bulkDelete} disabled={selected.size === 0}>Delete {selected.size}</button>
                ) : (
                  <button className="btn" onClick={() => setBulkSelecting(true)} disabled={activeTab.leads.length === 0}>Bulk delete</button>
                )
              )}
            </>
          )}
        />
      </div>

      <div className="pipeline-content scroll">
        <div className="pipeline-results-head">
          <div>
            <h3>{activeTab.label}</h3>
            <p>{hasFilters ? "Jobs matching your filters" : "Jobs saved in this view"} · showing {Math.min(visibleCount, activeTab.leads.length)} of {activeTab.leads.length}</p>
          </div>
          {bulkSelecting && (
            <span className={`pipeline-selected mono ${tab === "discarded" ? "danger" : "applied"}`}>
              {selected.size} selected
            </span>
          )}
        </div>
        {loading ? (
          <PipelineSkeleton />
        ) : activeTab.leads.length === 0 ? (
          <div className="pipeline-empty">
            <Icon name={hasFilters ? "filter" : "search"} size={18} />
            <h3>{hasFilters ? "No jobs match these filters" : `No ${activeTab.label.toLowerCase()} yet`}</h3>
            <p>{hasFilters ? "Try clearing one or more filters." : "Go to Home and choose Find new jobs, or tailor a job you already found."}</p>
          </div>
        ) : (
          <div className="pipeline-list">
            {visibleLeads.map(lead => (
              <div key={lead.job_id} className="pipeline-list-item">
                {bulkSelecting && (
                  <div
                    className="pipeline-select-box"
                    onClick={() => toggleSelect(lead.job_id)}
                    style={{
                      borderColor: selected.has(lead.job_id) ? (tab === "discarded" ? "var(--bad)" : "var(--orange)") : "var(--line)",
                      background: selected.has(lead.job_id) ? (tab === "discarded" ? "var(--bad)" : "var(--orange)") : "var(--paper)",
                    }}
                  >
                    {selected.has(lead.job_id) && <Icon name="check" size={11} color="#fff" />}
                  </div>
                )}
                <PipelineJobCard
                  lead={lead}
                  onOpen={openDrawer}
                  onDelete={deleteLead}
                  showGenerate={tab === "evaluated"}
                  port={port}
                  api={api}
                  carted={cart.includes(lead.job_id)}
                  onAddToCart={() => addToCart(lead.job_id)}
                />
              </div>
            ))}
          </div>
        )}
        {activeTab.leads.length > visibleCount && (
          <div className="row" style={{ justifyContent: "center", marginTop: 18 }}>
            <button className="btn" onClick={() => setVisibleCount(v => v + PAGE_SIZE)}>
              Show next {Math.min(PAGE_SIZE, activeTab.leads.length - visibleCount)} of {activeTab.leads.length}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════
   PENTAGON GRAPH COMPONENT
══════════════════════════════════════ */
