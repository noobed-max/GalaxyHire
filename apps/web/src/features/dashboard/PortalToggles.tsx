import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "../../shared/components/Icon";
import { discoveryApi, type PortalSource } from "../../api/discovery";
import type { ApiFetch } from "../../types";

/**
 * The per-portal toggle column (MAJOR-CHANGE/06).
 *
 * The list IS the scraper's config catalog (GET /corpus/sources) — never `DISTINCT site` from
 * the corpus DB, which would list what worked last time and make a broken board undeclarable.
 *
 * Every change persists server-side immediately (POST /corpus/portals): the browser is a thin
 * shell over the same API (ARCHITECTURE.md D3, and the desktop shell shares this backend), so a
 * localStorage-only store would let the two shells disagree about what "my portals" means — and
 * the server resolves the SAME saved map into the scrape, so what the toggle says is what the
 * run records and what the freshness key compares against.
 *
 * Selection semantics follow the router's precedence: saved map > catalog defaults. A saved
 * map with nothing on is refused (zero-source scrape == broken-looking product), not filtered
 * into silence.
 */

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

function Switch({ on, disabled, onToggle, label }: {
  on: boolean; disabled: boolean; onToggle: () => void; label: string;
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

export function PortalToggles({ api, onEnabledChange, onSelectionChange }: {
  api: ApiFetch | null;
  onEnabledChange?: (count: number) => void;
  /** The resolved enabled id list (null while the catalog/selection is still loading).
   *  Lets the search send this run's selection explicitly instead of racing the saved map. */
  onSelectionChange?: (ids: string[] | null) => void;
}) {
  const [sources, setSources] = useState<PortalSource[]>([]);
  const [saved, setSaved] = useState<Record<string, boolean> | null>(null);
  const [filter, setFilter] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
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
            // Corrupt saved map: fall back to catalog defaults rather than silently scraping
            // nothing; the server says the same thing when a search resolves the same map.
            setLoadErr("Saved portal selection was unreadable — using defaults. Toggle any portal to re-save.");
          }
        }
      } catch {
        if (alive) setLoadErr("Could not load the portal catalog.");
      }
    })();
    return () => { alive = false; };
  }, [api]);

  const enabledFor = useCallback((s: PortalSource) =>
    // A portal added to the config after this map was saved was never voted on — it follows
    // the catalog default, so newly-added boards join the selection instead of silently
    // sitting off until the user happens to toggle something.
    saved ? (saved[s.id] ?? s.default_enabled) : s.default_enabled, [saved]);

  const enabledIds = useMemo(
    () => sources.filter(enabledFor).map(s => s.id),
    [sources, enabledFor],
  );

  const enabledSet = useMemo(() => new Set(enabledIds), [enabledIds]);

  useEffect(() => {
    onEnabledChange?.(enabledSet.size);
  }, [enabledSet, onEnabledChange]);

  useEffect(() => {
    onSelectionChange?.(saved === null ? null : enabledIds);
  }, [enabledIds, saved, onSelectionChange]);

  const persist = useCallback((next: Record<string, boolean>) => {
    if (!api) return;
    setSaved(next);
    setSaving(true);
    discoveryApi.portalsSave(api, next)
      .then(result => {
        if (!mounted.current) return;
        if (!result.ok) setLoadErr(result.error || "Could not save the portal selection.");
        else setLoadErr(null);
      })
      .catch(() => mounted.current && setLoadErr("Could not save the portal selection."))
      .finally(() => mounted.current && setSaving(false));
  }, [api]);

  /** The full truth-map for every currently-listed portal, so a filter-narrowed view cannot
   *  silently drop the other entries from the saved map. */
  const fullMap = useCallback((overrides: Record<string, boolean> = {}) => {
    const base: Record<string, boolean> = {};
    for (const s of sources) base[s.id] = enabledFor(s);
    return { ...base, ...overrides };
  }, [sources, enabledFor]);

  const toggle = (s: PortalSource) => persist(fullMap({ [s.id]: !enabledFor(s) }));
  const setAll = (on: boolean) => persist(fullMap(
    Object.fromEntries(visible(sources).map(s => [s.id, on && s.recency_policy !== "off"])) as Record<string, boolean>,
  ));

  const q = filter.trim().toLowerCase();
  function visible(list: PortalSource[]) {
    return q ? list.filter(s => s.label.toLowerCase().includes(q) || s.id.includes(q)) : list;
  }

  const boards = visible(sources.filter(s => s.kind === "board"));
  const tenants = visible(sources.filter(s => s.kind === "tenant"));

  const renderRow = (s: PortalSource) => {
    const on = enabledSet.has(s.id);
    const dead = s.recency_policy === "off";
    const badge = policyBadge(s);
    return (
      <div className="gh-port-row" key={s.id}>
        <span className="gh-port-name" title={s.id}>{s.label}</span>
        <span className={`gh-port-policy ${s.recency_policy}`} title={badge.title}>{badge.text}</span>
        <Switch
          on={on}
          disabled={dead || saving}
          onToggle={() => toggle(s)}
          label={s.label}
        />
      </div>
    );
  };

  if (!api) return null;

  return (
    <aside className="gh-ports" aria-label="Job portals">
      <div className="gh-ports-head">
        <div>
          <span className="gh-kicker">Sources</span>
          <h3>Job portals</h3>
        </div>
        <span className="gh-ports-count">{enabledSet.size}/{sources.length || "…"}</span>
      </div>
      <div className="gh-ports-tools">
        <label className="gh-ports-search">
          <Icon name="search" size={12} />
          <input
            value={filter}
            onChange={event => setFilter(event.target.value)}
            placeholder="Filter portals"
            aria-label="Filter portals"
          />
        </label>
        <button className="gh-ports-all" onClick={() => setAll(true)}>all</button>
        <button className="gh-ports-all" onClick={() => setAll(false)}>none</button>
      </div>
      {loadErr && <div className="gh-inline-error" style={{ margin: "6px 10px" }}>{loadErr}</div>}
      {enabledSet.size === 0 && sources.length > 0 && (
        <div className="gh-ports-warn">No portals on — “Find new jobs” will ask you to turn at least one on.</div>
      )}
      <div className="gh-ports-list scroll">
        {boards.length > 0 && <>
          <div className="gh-ports-section">Boards</div>
          {boards.map(renderRow)}
        </>}
        {tenants.length > 0 && <>
          <div className="gh-ports-section">Company boards</div>
          {tenants.map(renderRow)}
        </>}
        {sources.length === 0 && (
          <div className="gh-ports-section">Loading portals…</div>
        )}
      </div>
    </aside>
  );
}
