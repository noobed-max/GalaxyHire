/**
 * Server-side role injection — where `--role` finally reaches a career-ops provider.
 *
 * career-ops providers are config-driven, and most board-wide feeds accept no query at all
 * (they return the whole board and the scanner's `title_filter` narrows afterwards —
 * MAJOR-CHANGE/03 §3 category 3). A minority DO take a search term, and for those it is worth
 * sending: the board's own search answers far more precisely than any client-side match, and
 * returns fewer rows to chew through.
 *
 * The role phrase is injected into a *copy* of the entry here, never into the provider files
 * (the vendor tree stays untouched — MAJOR-CHANGE/00 rule 2) and never by name-guessing:
 * every key below was read off the provider's own source before being added.
 */

import type { PortalEntry } from './config';

/** Providers whose fetch changes shape when given a query — verified against their sources. */
const INJECTORS: Record<string, (entry: PortalEntry, role: string) => PortalEntry> = {
  // `<id>.keywords[]` fallback is career-ops' config/profile.yml target_roles, which does not
  // exist here — without injection these four throw ("has no X.keywords[]") and the portal
  // silently contributes nothing.
  vdab: (e, r) => ({ ...e, vdab: { ...(e.vdab as object), keywords: [r] } }),
  jobbankca: (e, r) => ({ ...e, jobbankca: { ...(e.jobbankca as object), keywords: [r] } }),
  mycareersfuture: (e, r) => ({ ...e, mycareersfuture: { ...(e.mycareersfuture as object), keywords: [r] } }),
  arbeitsagentur: (e, r) => ({ ...e, arbeitsagentur: { ...(e.arbeitsagentur as object), keywords: [r] } }),
  // Server-side full-text with synonym expansion (`q:` runs on the feed itself).
  'a16z-speedrun-talent': (e, r) => ({ ...e, q: r }),
  // CN career portals query per keyword; omitting keywords pulls the WHOLE board, so injecting
  // is strictly less traffic, not more.
  alibaba: (e, r) => ({ ...e, keywords: [r] }),
  meituan: (e, r) => ({ ...e, keywords: [r] }),
  tencent: (e, r) => ({ ...e, keywords: [r] }),
  // WTTJ's board is global; without queries it throws ("configure explicit searches").
  wttj: (e, r) => ({ ...e, wttj: { ...(e.wttj as object), queries: [r] } }),
  // VN boards read the same keys their own search forms generate.
  careerviet: (e, r) => ({ ...e, searchKeywords: r }),
  itviec: (e, r) => ({ ...e, searchKeywords: r }),
};

export function injectRole(entry: PortalEntry, role: string, providerId?: string): PortalEntry {
  const key = providerId ?? String(entry.provider ?? '');
  const injector = INJECTORS[key];
  if (!injector || !role.trim()) return entry;
  return injector(entry, role.trim());
}

/** Providers whose fetch changes shape when given a location — verified against their sources. */
const LOCATION_INJECTORS: Record<string, (entry: PortalEntry, location: string) => PortalEntry> = {
  // amazon.jobs is one global endpoint; without loc_query the MAX_PAGES cap just returns the
  // most recent worldwide slice, so sending the location is strictly less traffic, not more.
  amazon: (e, l) => ({ ...e, amazon: { ...((e.amazon as object) ?? {}), loc_query: l } }),
};

export function injectLocation(entry: PortalEntry, location: string, providerId?: string): PortalEntry {
  const key = providerId ?? String(entry.provider ?? '');
  const injector = LOCATION_INJECTORS[key];
  if (!injector || !location.trim()) return entry;
  return injector(entry, location.trim());
}

/** For the run report: which portals actually saw the role server-side. */
export function hasServerSideQuery(providerId: string): boolean {
  return Object.prototype.hasOwnProperty.call(INJECTORS, providerId);
}
