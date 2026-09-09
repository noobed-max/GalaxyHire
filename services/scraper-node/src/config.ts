/**
 * Portal configuration — which tenants to scrape.
 *
 * The format is career-ops' `portals.yml`, reused verbatim rather than redesigned: its ~80
 * providers were written against these field names, and `config/portals.example.yml` ships
 * curated company entries with working ATS endpoints. Inventing a new format would mean
 * translating into the one the providers already expect, for no gain.
 *
 * GalaxyHire adds one namespaced block per entry (`gh:`) that no provider reads — recency
 * policy, default toggle, display grouping. See MAJOR-CHANGE/05 §2.
 *
 * Resolution order: an explicit path, then `config/portals.yml` (the user's own), then
 * `config/portals.example.yml` (the seed). Falling back to the example is what makes a fresh
 * checkout able to scrape something without configuration.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { parse } from 'yaml';

import { REPO_ROOT } from './paths';

const CONFIG_DIR = path.join(REPO_ROOT, 'services/scraper-node/config');

/** One entry from `tracked_companies` or `job_boards`. Shape per `_types.js`'s `PortalEntry`. */
export interface PortalEntry {
  name: string;
  enabled?: boolean;
  careers_url?: string;
  api?: string;
  provider?: string;
  transport?: 'http';
  max_pages?: number;
  offset_param?: string;
  /**
   * GalaxyHire policy block — never read by a provider (career-ops treats unknown entry keys as
   * opaque; MAJOR-CHANGE/03 §4). `recency` drives `src/recency.ts`; `toggle`/`region` drive the
   * catalog the UI lists; `note` carries "why is this off" text into the tooltip.
   */
  gh?: {
    recency?: 'strict' | 'first_seen' | 'off';
    toggle?: boolean;
    region?: string;
    note?: string;
    timeout_ms?: number;
  };
  /** Providers read their own extra keys straight off the entry; keep them. */
  [key: string]: unknown;
}

export interface PortalConfig {
  /** One employer per entry. */
  trackedCompanies: PortalEntry[];
  /** Multi-employer aggregator boards. */
  jobBoards: PortalEntry[];
  /** Which file this came from, for the run report. */
  source: string;
}

export function resolveConfigPath(explicit?: string): string | null {
  const candidates = [
    explicit,
    path.join(CONFIG_DIR, 'portals.yml'),
    path.join(CONFIG_DIR, 'portals.example.yml'),
  ].filter((p): p is string => Boolean(p));
  return candidates.find(p => existsSync(p)) ?? null;
}

/**
 * Entries that are `enabled: false` are dropped.
 *
 * Absent `enabled` means enabled — career-ops documents the default as true, and the example
 * config relies on it.
 */
function usable(entries: unknown): PortalEntry[] {
  if (!Array.isArray(entries)) return [];
  return entries.filter(
    (e): e is PortalEntry =>
      Boolean(e) && typeof e === 'object' && typeof (e as PortalEntry).name === 'string' && (e as PortalEntry).enabled !== false,
  );
}

export function loadPortalConfig(explicit?: string): PortalConfig {
  const file = resolveConfigPath(explicit);
  if (!file) {
    return { trackedCompanies: [], jobBoards: [], source: '(none found)' };
  }
  const doc = parse(readFileSync(file, 'utf8')) as Record<string, unknown> | null;
  return {
    trackedCompanies: usable(doc?.tracked_companies),
    jobBoards: usable(doc?.job_boards),
    source: path.relative(REPO_ROOT, file),
  };
}

/**
 * The stable addressable id of an entry — what the UI toggles and `--portals` speak.
 *
 * Board-wide feeds are one-per-provider (`arbeitnow`); company tenants must not collapse into
 * their provider (`greenhouse` would be one toggle hiding every employer on it), so a tenant
 * carries its slug: `greenhouse:gitlab`. The slug comes from the URL the entry already has —
 * never invented. This id is display/selection only; the corpus `site` key stays the provider
 * id, because observations from every Greenhouse tenant legitimately share that site namespace
 * and dedup happens on (site, source_job_id) regardless.
 */
export function resolveEntryId(entry: PortalEntry): string {
  const provider = String(entry.provider ?? '').trim();
  const url = String(entry.api ?? entry.careers_url ?? '');
  const slug = slugFromUrl(url);
  if (!provider) return slug || entry.name;
  return slug ? `${provider}:${slug}` : provider;
}

/** Tenant slug pulled from the ATS URL shapes the vendored providers themselves resolve. */
const SLUG_PATTERNS: RegExp[] = [
  /boards-api\.greenhouse\.io\/v1\/boards\/([a-z0-9-]+)/i,
  /job-boards(?:\.eu)?\.greenhouse\.io\/([a-z0-9-]+)/i,
  /boards\.greenhouse\.io\/([a-z0-9-]+)/i,
  /jobs\.ashbyhq\.com\/([a-z0-9-]+)/i,
  /jobs(?:\.eu)?\.lever\.co\/([a-z0-9-]+)/i,
  /([a-z0-9-]+)\.greenhouse\.job-boards\.com/i,
  /apply\.workable\.com\/([a-z0-9-]+)/i,
  /jobs\.smartrecruiters\.com\/api\/v2\/([a-z0-9-]+)/i,
  /([a-z0-9-]+)\.teamtailor\.com/i,
  /([a-z0-9-]+)\.recruitee\.com/i,
  /([a-z0-9-]+)\.bamboohr\.com/i,
  /([a-z0-9-]+)\.breezy\.hr/i,
  /([a-z0-9-]+)\.jibeapply\.com/i,
  // cxs shape first: /wday/cxs/<tenant>/<site>/jobs → the SITE segment. Ordered before the
  // generic fallback, which would otherwise capture 'cxs' (a rejected segment) instead.
  /myworkdayjobs\.com\/(?:[^/]+\/)*cxs\/[^/]+\/([a-z0-9-]+)/i,
  // plain hosted board: /<locale>/<site>/jobs → first segment after the locale
  /myworkdayjobs\.com\/[^/]+\/([^/?#]+)/i,
  /([a-z0-9-]+)\.myworkday\.com/i,
];

/** Generic path segments that look like slugs but identify nothing. */
const SLUG_REJECT = /^(en|en-us|api|jobs|careers|wday|cxs)$/i;

/**
 * Tries patterns in order and SKIPS ones whose capture is a rejected generic — the loop matches
 * the Python mirror in galaxy/scrape/catalog.py exactly; the two must agree because the UI sends
 * ids across the boundary.
 */
export function slugFromUrl(url: string): string | null {
  for (const re of SLUG_PATTERNS) {
    const m = re.exec(url ?? '');
    if (!m) continue;
    const raw = m[m.length - 1];
    if (!raw || raw.length < 2 || SLUG_REJECT.test(raw)) continue;
    return raw.toLowerCase().replace(/[^a-z0-9-]/g, '');
  }
  return null;
}

/**
 * Entries whose only viable path is a search engine or a local command, which this harness has no
 * business running: `scan_method: websearch` needs a search API career-ops drives from an AI CLI,
 * and `parser` shells out. Filtered out so they don't show up as provider failures.
 */
export function isDirectlyFetchable(entry: PortalEntry): boolean {
  if (entry.scan_method === 'websearch' && !entry.api) return false;
  if (entry.parser) return false;
  return Boolean(entry.api ?? entry.careers_url ?? entry.provider);
}
