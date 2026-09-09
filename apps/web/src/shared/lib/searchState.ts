export interface SearchFilters {
  max_seniority?: string;
  max_years?: number;
  remote?: boolean;
  location?: string;
  negative_titles?: string[];
  negative_phrases?: string[];
  positive_skills?: string[];
}

export interface ActiveSearch {
  brief: string;
  role: string;
  filters: SearchFilters;
  jobIds: string[];
  completedAt: number;
}

export interface ScanResult {
  ok: boolean;
  cancelled?: boolean;
  error?: string;
  detail?: string | Record<string, unknown>;
  retrieved?: number;
  saved?: number;
  deduplicated?: number;
  retired?: number;
  brief?: string;
  query?: string;
  filters?: SearchFilters;
  leads?: { job_id?: string }[];
}

/** Request body for the dashboard's explicit Find action.
 *
 * Keeping the intent in a small pure builder gives the UI/API contract a direct test and prevents
 * a future caller from accidentally using the background search's freshness-cached semantics.
 */
export function dashboardFindRequest(
  query: string,
  portals: string[] | null | undefined,
  location: string | null | undefined,
) {
  return {
    query: String(query || "").trim(),
    portals: portals ?? null,
    location: String(location || "").trim(),
    force_scrape: true as const,
  };
}

export const ACTIVE_SEARCH_KEY = "galaxyhire-active-search-v1";
export const ACTIVE_SEARCH_EVENT = "search-constraints";

export function roleFromBrief(brief: string) {
  const first = String(brief || "").split(/[,;]|\b(?:no|not|without|excluding|except)\b/i, 1)[0];
  return first
    .replace(/^(?:i(?:'m| am)?\s+)?(?:looking|searching)\s+for\s+/i, "")
    .replace(/^(?:please\s+)?(?:find|show)\s+(?:me\s+)?/i, "")
    .replace(/^i\s+want\s+/i, "")
    .trim();
}

export function readActiveSearch(): ActiveSearch | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(window.localStorage.getItem(ACTIVE_SEARCH_KEY) || "null");
    if (!value || typeof value.role !== "string" || !Array.isArray(value.jobIds)) return null;
    return value as ActiveSearch;
  } catch {
    return null;
  }
}

export function saveActiveSearch(result: ScanResult, submittedBrief: string) {
  if (typeof window === "undefined" || !result.ok || !result.query) return null;
  const state: ActiveSearch = {
    brief: String(result.brief || submittedBrief || result.query),
    role: result.query,
    filters: result.filters || {},
    jobIds: (result.leads || [])
      .map(lead => String(lead?.job_id || ""))
      .filter(Boolean),
    completedAt: Date.now(),
  };
  window.localStorage.setItem(ACTIVE_SEARCH_KEY, JSON.stringify(state));
  window.dispatchEvent(new CustomEvent(ACTIVE_SEARCH_EVENT, { detail: state }));
  return state;
}

export function clearActiveSearch() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(ACTIVE_SEARCH_KEY);
  window.dispatchEvent(new CustomEvent(ACTIVE_SEARCH_EVENT, { detail: null }));
}

export function activeFilterLabels(filters: SearchFilters): string[] {
  const labels: string[] = [];
  if (filters.max_seniority) {
    const level = filters.max_seniority === "mid" ? "mid-level" : filters.max_seniority;
    labels.push(`Up to ${level}`);
  }
  if (Number.isFinite(filters.max_years)) {
    const max = Number(filters.max_years);
    labels.push(max === 0 ? "No experience required" : `Up to ${max} year${max === 1 ? "" : "s"} required`);
  }
  if (filters.remote === true) labels.push("Remote");
  if (filters.location) labels.push(filters.location);
  for (const value of [...(filters.negative_titles || []), ...(filters.negative_phrases || [])]) {
    labels.push(`Exclude ${value}`);
  }
  for (const value of filters.positive_skills || []) labels.push(`Include ${value}`);
  return [...new Set(labels.map(label => label.trim()).filter(Boolean))];
}
