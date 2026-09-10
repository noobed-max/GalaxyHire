import type { ApiFetch } from "./types";

/** The four judgements the corpus acts on. Anything else is stored and then ignored. */
export type FeedbackSignal = "not_relevant" | "too_senior" | "too_junior" | "good";

/** What accumulated feedback currently does to this user's searches. */
export interface SearchPreference {
  active: boolean;
  suppressed: number;
  max_seniority: string | null;
  min_seniority: string | null;
  /** Ready-to-display sentences. Rendered as-is so the UI never re-derives the wording. */
  reasons: string[];
}

/** A location that has jobs behind it, with how many. */
export interface LocationSuggestion {
  label: string;
  jobs: number;
}

/** Live state of a collection run, read from the corpus so a page refresh reattaches to it. */
export interface ScrapeState {
  running: boolean;
  phrase: string;
  status: "idle" | "running" | "done" | "failed" | "stopped";
  /** Jobs added since this run started. Derived from the corpus row count, not self-reported. */
  collected: number;
  elapsed_seconds: number;
  error: string;
  run_id: number | null;
  /** The portal set this run covered — recorded at start, echoed by status so a refresh can show it. */
  portals?: string[];
  /** Set on a start response when another persisted scraper is already active. */
  conflict?: boolean;
}

/** One portal the scraper can visit — the toggle column's row shape (GET /corpus/sources). */
export interface PortalSource {
  id: string;
  label: string;
  kind: "board" | "tenant";
  provider: string;
  region: string;
  recency_policy: "strict" | "first_seen" | "off";
  default_enabled: boolean;
  note: string;
}

/**
 * A source may be present in the catalog for diagnostics while still being
 * unavailable to a user selection (for example a retired/off feed). Keep the
 * filter in one place so the dashboard, side panel, and explicit Find request
 * cannot disagree about what "all" means.
 */
export function isSelectablePortal(source: PortalSource): boolean {
  return Boolean(source.id && source.provider && (source.kind === "board" || source.kind === "tenant"))
    && source.recency_policy !== "off";
}

export function selectablePortals(sources: PortalSource[]): PortalSource[] {
  return sources.filter(isSelectablePortal);
}

/** Resolve one toggle using the product's two-state persistence contract:
 * no saved map means every selectable source is on; explicit saved keys are
 * authoritative, while a newly-added source missing from an older map starts on. */
export function portalEnabled(source: PortalSource, saved: Record<string, boolean> | null): boolean {
  if (!isSelectablePortal(source)) return false;
  return saved === null ? true : (saved[source.id] ?? true);
}

export const discoveryApi = {
  scan: (api: ApiFetch) => api("/api/v1/scan", { method: "POST" }),
  stopScan: (api: ApiFetch) => api("/api/v1/scan/stop", { method: "POST" }),
  freeSources: (api: ApiFetch) => api("/api/v1/free-sources/scan", { method: "POST" }),

  /** Record a judgement on a result. Resolves with the *new* preference so the caller can show
   *  the effect straight away rather than waiting for the next search to look different.
   *
   *  Rejects on a non-OK status rather than resolving with a default. A feedback button that
   *  reports success on a failed write is worse than one that errors: the user believes the system
   *  learned something it did not, and stops correcting it. */
  feedback: async (
    api: ApiFetch,
    canonicalJobId: string,
    signal: FeedbackSignal,
  ): Promise<{ recorded: boolean; preference: SearchPreference }> => {
    const res = await api("/api/v1/corpus/feedback", {
      method: "POST",
      body: JSON.stringify({ canonical_job_id: canonicalJobId, signal }),
    });
    if (!res.ok) throw new Error(`Feedback returned ${res.status}`);
    return res.json();
  },

  /** Locations present in the corpus matching `q`, most jobs first.
   *
   *  Resolves to an empty list rather than throwing: a type-ahead that errors while you are
   *  mid-word is worse than one that quietly offers nothing. */
  locations: async (api: ApiFetch, q: string): Promise<LocationSuggestion[]> => {
    const res = await api(`/api/v1/corpus/locations?q=${encodeURIComponent(q)}`);
    if (!res.ok) return [];
    const body = await res.json().catch(() => ({}));
    return Array.isArray(body.suggestions) ? body.suggestions : [];
  },

  /** Begin collecting `phrase`. Only the phrase reaches the job boards — every filter is
   *  applied afterwards, over what was stored, so changing your mind never re-scrapes. */
  scrapeStart: async (
    api: ApiFetch,
    phrase: string,
    hours = 24,
    portals?: string[],
  ): Promise<ScrapeState> => {
    const res = await api("/api/v1/corpus/scrape/start", {
      method: "POST",
      body: JSON.stringify({ phrase, hours, portals: portals ?? null }),
    });
    if (!res.ok) throw new Error(`Scrape start returned ${res.status}`);
    return res.json();
  },

  /** The portal catalog from the scraper's own config — what CAN be scraped, not what worked
   *  last time. The toggle list must never be built from the corpus DB (MAJOR-CHANGE/06 §2). */
  sources: async (api: ApiFetch): Promise<PortalSource[]> => {
    const res = await api("/api/v1/corpus/sources");
    if (!res.ok) return [];
    const body = await res.json().catch(() => ({}));
    return Array.isArray(body.sources) ? body.sources : [];
  },

  /** The saved toggle map as stored server-side: a JSON string, "" meaning 'use defaults'. */
  portalsGet: async (api: ApiFetch): Promise<string> => {
    const res = await api("/api/v1/corpus/portals");
    if (!res.ok) return "";
    const body = await res.json().catch(() => ({}));
    return typeof body.portals === "string" ? body.portals : "";
  },

  /** Persist the selection. Rejects on a failed write: a toggle that looks saved but isn't
   *  silently changes what the next search collects. */
  portalsSave: async (
    api: ApiFetch,
    portals: Record<string, boolean>,
  ): Promise<{ ok: boolean; error?: string }> => {
    const res = await api("/api/v1/corpus/portals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portals }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = typeof body?.detail === "string"
        ? body.detail
        : typeof body?.error === "string" ? body.error : "";
      return { ok: false, error: detail || `Portals save returned ${res.status}` };
    }
    const result = await res.json().catch(() => ({}));
    return result?.ok === false
      ? { ok: false, error: String(result.error || "The server rejected this portal selection.") }
      : result;
  },

  scrapeStatus: async (api: ApiFetch): Promise<ScrapeState> => {
    const res = await api("/api/v1/corpus/scrape/status");
    if (!res.ok) throw new Error(`Scrape status returned ${res.status}`);
    return res.json();
  },

  scrapeStop: async (api: ApiFetch): Promise<ScrapeState> => {
    const res = await api("/api/v1/corpus/scrape/stop", { method: "POST" });
    if (!res.ok) throw new Error(`Scrape stop returned ${res.status}`);
    return res.json();
  },

  /** The standing effect of past feedback, so it can be shown and undone rather than guessed at. */
  preference: async (api: ApiFetch): Promise<SearchPreference & { available: boolean }> => {
    const res = await api("/api/v1/corpus/preference");
    if (!res.ok) throw new Error(`Preference returned ${res.status}`);
    return res.json();
  },
};
