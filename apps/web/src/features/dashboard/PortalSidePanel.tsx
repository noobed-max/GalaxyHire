import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "../../shared/components/Icon";
import { discoveryApi, isSelectablePortal, portalEnabled, selectablePortals, type PortalSource } from "../../api/discovery";
import type { ApiFetch } from "../../types";

function policyBadge(source: PortalSource): { text: string; title: string } {
  if (source.recency_policy === "strict") {
    return { text: "<24h", title: "Only postings the source proves are under 24 hours old." };
  }
  if (source.recency_policy === "first_seen") {
    return {
      text: "new-to-you",
      title: "This source publishes no reliable posting date — its results are labeled by when we FIRST SAW them, not when they were posted.",
    };
  }
  return { text: "off", title: source.note || "This portal is disabled at the scraper level." };
}

function Switch({
  on,
  disabled,
  onToggle,
  label,
}: {
  on: boolean;
  disabled: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={`Job portal ${label}`}
      disabled={disabled}
      onClick={onToggle}
      className={`gh-port-switch${on ? " on" : ""}`}
    >
      <span className="gh-port-knob" aria-hidden />
    </button>
  );
}

export interface PortalSidePanelProps {
  open: boolean;
  onClose: () => void;
  api: ApiFetch | null;
  onEnabledChange?: (count: number) => void;
  onSelectionChange?: (ids: string[] | null) => void;
}

export function PortalSidePanel({
  open,
  onClose,
  api,
  onEnabledChange,
  onSelectionChange,
}: PortalSidePanelProps) {
  const [sources, setSources] = useState<PortalSource[]>([]);
  const [saved, setSaved] = useState<Record<string, boolean> | null>(null);
  const [filter, setFilter] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!api) return;
    let alive = true;
    (async () => {
      try {
        const [list, raw] = await Promise.all([
          discoveryApi.sources(api),
          discoveryApi.portalsGet(api),
        ]);
        if (!alive) return;
        setSources(list);
        if (raw.trim()) {
          try {
            setSaved(JSON.parse(raw));
          } catch {
            setLoadErr("Saved portal selection was unreadable — using defaults. Toggle any portal to re-save.");
          }
        }
      } catch {
        if (alive) setLoadErr("Could not load the portal catalog.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [api]);

  const enabledFor = useCallback((s: PortalSource) => portalEnabled(s, saved), [saved]);

  const enabledIds = useMemo(
    () => selectablePortals(sources).filter(enabledFor).map((s) => s.id),
    [sources, enabledFor],
  );

  const selectable = useMemo(() => selectablePortals(sources), [sources]);

  const enabledSet = useMemo(() => new Set(enabledIds), [enabledIds]);

  useEffect(() => {
    onEnabledChange?.(enabledSet.size);
  }, [enabledSet, onEnabledChange]);

  useEffect(() => {
    onSelectionChange?.(sources.length === 0 ? null : enabledIds);
  }, [enabledIds, sources.length, onSelectionChange]);

  const persist = useCallback(
    (next: Record<string, boolean>) => {
      if (!api) return;
      setSaved(next);
      setSaving(true);
      discoveryApi
        .portalsSave(api, next)
        .then((result) => {
          if (!mounted.current) return;
          if (!result.ok) setLoadErr(result.error || "Could not save the portal selection.");
          else setLoadErr(null);
        })
        .catch(() => mounted.current && setLoadErr("Could not save the portal selection."))
        .finally(() => mounted.current && setSaving(false));
    },
    [api],
  );

  const fullMap = useCallback(
    (overrides: Record<string, boolean> = {}) => {
      const base: Record<string, boolean> = {};
      for (const s of selectable) base[s.id] = enabledFor(s);
      return { ...base, ...overrides };
    },
    [selectable, enabledFor],
  );

  const toggle = (s: PortalSource) => persist(fullMap({ [s.id]: !enabledFor(s) }));

  const q = filter.trim().toLowerCase();
  function visible(list: PortalSource[]) {
    return q ? list.filter((s) => s.label.toLowerCase().includes(q) || s.id.includes(q)) : list;
  }

  const setAll = (on: boolean) =>
    persist(
      fullMap(
        Object.fromEntries(
          visible(selectable).map((s) => [s.id, on]),
        ) as Record<string, boolean>,
      ),
    );

  const boards = visible(sources.filter((s) => s.kind === "board"));
  const tenants = visible(sources.filter((s) => s.kind === "tenant"));

  // ESC key to close
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const renderRow = (s: PortalSource) => {
    const on = enabledSet.has(s.id);
    const dead = !isSelectablePortal(s);
    const badge = policyBadge(s);
    return (
      <div className="gh-port-row" key={s.id}>
        <div className="gh-port-info">
          <span className="gh-port-name" title={s.id}>
            {s.label}
          </span>
          <span className="gh-port-id">{s.id}</span>
        </div>
        <span className={`gh-port-policy ${s.recency_policy}`} title={badge.title}>
          {badge.text}
        </span>
        <Switch
          on={on}
          disabled={dead || saving}
          onToggle={() => toggle(s)}
          label={s.label}
        />
      </div>
    );
  };

  if (!api || !open) return null;

  return (
    <>
      <div
        className="gh-portal-backdrop"
        onClick={onClose}
        aria-hidden="true"
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0, 0, 0, 0.45)",
          backdropFilter: "blur(2px)",
          WebkitBackdropFilter: "blur(2px)",
          zIndex: 9999,
        }}
      />
      <aside
        className="gh-portal-side-panel open"
        aria-label="Job portals bookmarks side panel"
        role="dialog"
        aria-modal="true"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(440px, 92vw)",
          background: "var(--paper)",
          borderLeft: "2px solid var(--hard)",
          boxShadow: "-8px 0 32px rgba(0, 0, 0, 0.25)",
          zIndex: 10000,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div className="gh-ports-head">
          <div>
            <span className="gh-kicker">SOURCES</span>
            <h3>Job Portals</h3>
          </div>
          <div className="gh-ports-head-actions">
            <span className="gh-ports-count-badge">
              {enabledSet.size} / {selectable.length || "…"} active
            </span>
            <button
              className="gh-ports-close-btn"
              onClick={onClose}
              aria-label="Close portals panel"
              title="Close panel (Esc)"
            >
              <Icon name="x" size={15} />
            </button>
          </div>
        </div>

        <div className="gh-ports-tools">
          <label className="gh-ports-search">
            <Icon name="search" size={12} />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter portals..."
              aria-label="Filter portals by name or domain"
            />
            {filter && (
              <button
                className="gh-ports-search-clear"
                onClick={() => setFilter("")}
                aria-label="Clear filter"
              >
                <Icon name="x" size={11} />
              </button>
            )}
          </label>
          <button className="gh-ports-bulk-btn" onClick={() => setAll(true)}>
            All
          </button>
          <button className="gh-ports-bulk-btn" onClick={() => setAll(false)}>
            None
          </button>
        </div>

        {loadErr && <div className="gh-inline-error" style={{ margin: "8px 16px" }}>{loadErr}</div>}

        {enabledSet.size === 0 && sources.length > 0 && (
          <div className="gh-ports-warn" style={{ margin: "8px 16px" }}>
            No portals active — turn at least one on so "Find new jobs" can discover postings.
          </div>
        )}

        <div className="gh-ports-list scroll">
          {boards.length > 0 && (
            <>
              <div className="gh-ports-section">Job Boards ({boards.length})</div>
              {boards.map(renderRow)}
            </>
          )}
          {tenants.length > 0 && (
            <>
              <div className="gh-ports-section">Company Portals ({tenants.length})</div>
              {tenants.map(renderRow)}
            </>
          )}
          {sources.length === 0 && (
            <div className="gh-ports-loading">
              <span className="dot pulse-soft" aria-hidden /> Loading available portals…
            </div>
          )}
          {sources.length > 0 && boards.length === 0 && tenants.length === 0 && (
            <div className="gh-ports-empty">No portals match "{filter}"</div>
          )}
        </div>

        <div className="gh-ports-footer">
          <small>Changes save automatically to local configuration</small>
        </div>
      </aside>
    </>
  );
}
