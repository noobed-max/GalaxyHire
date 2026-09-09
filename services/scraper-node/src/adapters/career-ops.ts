/**
 * Runs career-ops providers unmodified.
 *
 * career-ops already solved provider loading and routing in `_registry.mjs` (`loadProviders` +
 * `resolveProvider`), and its portal config format is the thing its ~80 providers were written
 * against. Both are reused here rather than reimplemented — that is the whole "wrap, don't
 * rebuild" premise, and a reimplementation would drift from the providers' expectations the first
 * time upstream changed a detection rule.
 *
 * Everything GalaxyHire adds on top (recency policy, role gate, wall-clock guard) runs on the
 * `Job[]` *after* `provider.fetch` returns. Provider files stay byte-identical to upstream
 * except for the two security-hardening patches that externalize public provider keys into
 * runtime environment variables; `make vendor-check` excludes only those two files.
 */

import path from 'node:path';

import type { JobEnvelope } from '@galaxyhire/contract';

import { toEnvelope, type CareerOpsJob } from '../map/career-ops';
import { createCareerOpsCtx } from '../http';
import { enricherFor } from '../enrich/descriptions';
import { injectLocation, injectRole, hasServerSideQuery } from '../query-injection';
import { applyRecency, parseRecencyPolicy } from '../recency';
import { locationPassesGate, titlePassesRoleGate } from '../audit/relevance';
import { REPO_ROOT } from '../paths';
import type { PortalEntry } from '../config';

const PROVIDERS_DIR = path.join(REPO_ROOT, 'services/scraper-node/vendor/career-ops-providers');
const REGISTRY_MODULE = path.join(PROVIDERS_DIR, '_registry.mjs');

/**
 * `local-parser` is never loaded.
 *
 * It exists to exec a per-company command from the portal config, which is a sensible feature in
 * career-ops (where the config is the user's own file, edited by hand) and an unacceptable one
 * here: this harness runs unattended over config that may be seeded from a template. Nothing in
 * the unified product needs shelling out to scrape, so the capability is dropped rather than
 * gated.
 */
const SKIP_PROVIDER_IDS = ['local-parser'];

/**
 * Wall-clock guard per portal run. themuse took 119s for one crawl and agentic-jobs ~100s
 * (MAJOR-CHANGE/04 F-4); without this, two pagination-heavy boards dominate a 24h scrape while
 * every other connector waits behind them. The provider promise is not cancellable — racing it
 * releases the *slot* and reports the timeout; the orphan fetch finishes against its own
 * transport timeout and is discarded. Overridable per entry via `gh.timeout_ms`.
 */
const DEFAULT_ENTRY_TIMEOUT_MS = 45_000;

interface CareerOpsProvider {
  id: string;
  detect?(entry: PortalEntry): { url: string } | null;
  fetch(entry: PortalEntry, ctx: unknown): Promise<CareerOpsJob[]>;
}

interface CareerOpsRegistryModule {
  loadProviders(dir: string): Promise<Map<string, CareerOpsProvider>>;
  resolveProvider(
    entry: PortalEntry,
    providers: Map<string, CareerOpsProvider>,
    opts?: { skipIds?: string[] },
  ): { provider: CareerOpsProvider } | { error: string } | null;
}

let providersCache: Map<string, CareerOpsProvider> | null = null;
let registryCache: CareerOpsRegistryModule | null = null;

async function loadRegistryModule(): Promise<CareerOpsRegistryModule> {
  if (!registryCache) {
    // Dynamic import: the vendored tree is CJS-transformed and its named exports are not
    // statically visible (see the note in probe.ts).
    registryCache = (await import(REGISTRY_MODULE)) as unknown as CareerOpsRegistryModule;
  }
  return registryCache;
}

export async function loadCareerOpsProviders(): Promise<Map<string, CareerOpsProvider>> {
  if (!providersCache) {
    const reg = await loadRegistryModule();
    providersCache = await reg.loadProviders(PROVIDERS_DIR);
    for (const id of SKIP_PROVIDER_IDS) providersCache.delete(id);
  }
  return providersCache;
}

export interface RunOutcome {
  portal: string;
  entry: string;
  ok: boolean;
  jobs: number;
  /** How many of this entry's jobs gained a description from enrichment. */
  enriched?: number;
  /** Recency-policy accounting (see src/recency.ts). */
  jobs_in?: number;
  dropped_undated?: number;
  dropped_stale?: number;
  /** Title-gate drops — the role phrase applied client-side where the board has no search. */
  dropped_irrelevant?: number;
  /** Location-gate drops — the location phrase applied where the board has no location query. */
  dropped_location?: number;
  /** Kept jobs carrying non-trivial description text; the R5 regression meter. */
  desc_present?: number;
  /** strict | first_seen | off — what the policy actually was this run. */
  recency?: string;
  /** Whether the role phrase reached the board server-side (query injection). */
  server_query?: boolean;
  /** Set when ok is false. */
  error?: string;
  skipped?: string;
}

export interface CareerOpsRunResult {
  envelopes: JobEnvelope[];
  outcomes: RunOutcome[];
}

/**
 * Fetch one portal-config entry through its resolved provider.
 *
 * Never throws: a provider failing is normal operating condition (boards go down, tenants get
 * renamed, WAFs tighten), and one dead portal must not stop a broad scrape. The failure is
 * recorded and reported instead — and a failure is reported as a failure, never as `jobs: 0`,
 * which would hide a broken connector behind a healthy-looking run (docs/audits/SCRAPER-DIARY.md
 * rule: preserve raw output).
 */
export async function runEntry(
  entry: PortalEntry,
  providers: Map<string, CareerOpsProvider>,
  reg: CareerOpsRegistryModule,
  observedAt: Date,
  opts: {
    role: string;
    hours: number;
    location?: string;
    maxPages?: number;
    enrich?: boolean;
    /** Set false for the relevance audit: collect unfiltered, judge afterwards. */
    roleGate?: boolean;
  },
): Promise<CareerOpsRunResult> {
  const label = entry.name ?? '(unnamed)';
  const policy = parseRecencyPolicy(entry.gh?.recency);
  const base = {
    portal: String(entry.provider ?? '?'), entry: label, recency: policy, ok: false, jobs: 0,
  };

  if (policy === 'off') {
    return { envelopes: [], outcomes: [{ ...base, ok: true, skipped: 'recency policy off' }] };
  }

  const resolved = reg.resolveProvider(entry, providers, { skipIds: SKIP_PROVIDER_IDS });
  if (resolved === null) {
    return { envelopes: [], outcomes: [{ ...base, skipped: 'no provider matched' }] };
  }
  if ('error' in resolved) {
    return { envelopes: [], outcomes: [{ ...base, error: resolved.error }] };
  }

  const provider = resolved.provider;
  const timeoutMs = entry.gh?.timeout_ms ?? DEFAULT_ENTRY_TIMEOUT_MS;

  try {
    const run = (async () => {
      const roleEntry = injectRole(entry, opts.role, provider.id);
      const enrichedEntry = injectLocation(roleEntry, opts.location ?? '', provider.id);
      let jobs = await provider.fetch(
        enrichedEntry,
        createCareerOpsCtx({ maxPages: opts.maxPages }),
      ) ?? [];

      // career-ops providers never fetch descriptions (zero-token by design); the enrichers fire
      // only where one extra request for the WHOLE board yields them — see descriptions.ts.
      let enriched = 0;
      if (opts.enrich !== false) {
        const enricher = enricherFor(provider.id);
        if (enricher) {
          const res = await enricher(entry, jobs);
          jobs = res.jobs;
          enriched = res.enriched;
        }
      }
      return { jobs, enriched };
    })();

    const guard = new Promise<never>((_, reject) => {
      // The handle is cleared when the run settles: an uncleared 45s timer per portal keeps
      // the event loop alive long after the last board answered — up to a 45s tail on exit.
      const t = setTimeout(() => reject(new Error(`wall-clock timeout after ${timeoutMs}ms`)), timeoutMs);
      run.then(() => clearTimeout(t), () => clearTimeout(t));
    });
    const { jobs, enriched } = await Promise.race([run, guard]);

    // A provider returning a row with no title or url is a provider bug, not something to
    // forward to the corpus. Dropping it here keeps the invalid-envelope count meaningful.
    const usable = jobs.filter(j =>
      j && typeof j.title === 'string' && j.title.trim() && typeof j.url === 'string' && j.url);

    const { jobs: fresh, stats } = applyRecency(usable, policy, opts.hours, observedAt.getTime());

    // The role gate replaces what career-ops' title_filter does for a human-curated keyword
    // list: drop titles that plainly are not the searched role. Server-side query injection
    // already narrowed the boards that support it; this catches everyone else, so a
    // "software engineer" scrape stops posting the whole world's "Production Associate" rows.
    let selected = fresh;
    let droppedIrrelevant = 0;
    if (opts.roleGate !== false && opts.role.trim()) {
      selected = fresh.filter(j => titlePassesRoleGate(opts.role, j.title));
      droppedIrrelevant = fresh.length - selected.length;
    }

    // The location phrase reaches boards almost nowhere server-side (amazon excepted,
    // injected above), so it gates client-side here — same shape as the title gate, same
    // career-ops location_filter semantics: empty job locations pass, the rest must name
    // the wanted place. Retrieval narrows further afterwards; this only stops a located
    // scrape storing the rest of the world.
    let located = selected;
    let droppedLocation = 0;
    if ((opts.location ?? '').trim()) {
      located = selected.filter(j => locationPassesGate(opts.location as string, j.location));
      droppedLocation = selected.length - located.length;
    }

    const envelopes = located.map(j => toEnvelope(j, provider.id, observedAt));
    return {
      envelopes,
      outcomes: [{
        ...base,
        portal: provider.id,
        ok: true,
        jobs: envelopes.length,
        enriched,
        jobs_in: stats.jobs_in,
        dropped_undated: stats.dropped_undated,
        dropped_stale: stats.dropped_stale,
        dropped_irrelevant: droppedIrrelevant,
        desc_present: located.filter(j => (j.description ?? '').trim().length > 40).length,
        dropped_location: droppedLocation,
        server_query: hasServerSideQuery(provider.id) && Boolean(opts.role.trim()),
      }],
    };
  } catch (err) {
    return { envelopes: [], outcomes: [{ ...base, portal: provider.id, error: (err as Error).message }] };
  }
}
