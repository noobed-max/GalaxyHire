/**
 * Broad-scrape orchestrator — the Node worker's entry point.
 *
 *   npm start -- --role "software engineer" --hours 24
 *
 * The connector set is career-ops' provider modules and nothing else. A role phrase plus a
 * recency window goes out to the selected portals; per-portal policy (strict / first_seen) and
 * the title gate run inside `runEntry`; everything that survives becomes one `JobEnvelope` per
 * posting and is posted to the corpus, which owns dedup, normalization, and embedding.
 *
 * Which portals run is decided by the caller — the UI's per-portal toggles resolve to
 * `--portals a,b,c` (see MAJOR-CHANGE/06 §4); absent the flag, the config's `gh.toggle` defaults
 * apply. Portals are addressable as `provider` for board-wide feeds and `provider:slug` for
 * company tenants, and an id matching nothing is an error, never a silent empty run.
 *
 * Filtering is split deliberately (ARCHITECTURE.md D7, amended by MAJOR-CHANGE/05 §4): the role
 * phrase reaches boards that support a server-side query and gates titles client-side; the
 * location phrase reaches the boards that take one (amazon) and gates locations client-side
 * otherwise; seniority and negatives never reach a board and narrow the stored corpus at
 * retrieval.
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import type { JobEnvelope } from '@galaxyhire/contract';

import { loadCareerOpsProviders, runEntry, type RunOutcome } from './adapters/career-ops';
import { isDirectlyFetchable, loadPortalConfig, resolveEntryId, type PortalEntry } from './config';
import { CorpusClient } from './corpus';
import { REPO_ROOT } from './paths';

interface Args {
  role: string;
  hours: number;
  location?: string;
  concurrency: number;
  dryRun: boolean;
  only?: string;
  portals?: string[];
  maxPortals?: number;
  list: boolean;
  noRoleGate: boolean;
  configPath?: string;
  reportPath?: string;
}

function parseArgs(argv: string[]): Args {
  const get = (flag: string): string | undefined => {
    const i = argv.indexOf(flag);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1] : undefined;
  };
  const has = (flag: string) => argv.includes(flag);
  const num = (flag: string, fallback: number) => {
    const v = get(flag);
    const n = v === undefined ? NaN : Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
  const portals = get('--portals');

  return {
    role: get('--role') ?? '',
    // 24, not the old 48: "we only scrape data within 24 hours" is the product rule
    // (MAJOR-CHANGE/01 R3). The recency policy per portal decides what "within" means for a
    // source that publishes no date — see src/recency.ts.
    hours: num('--hours', 24),
    location: get('--location'),
    concurrency: Math.max(1, num('--concurrency', 8)),
    dryRun: has('--dry-run'),
    only: get('--only'),
    portals: portals ? portals.split(',').map(s => s.trim()).filter(Boolean) : undefined,
    maxPortals: get('--max-portals') ? num('--max-portals', 0) : undefined,
    list: has('--list'),
    noRoleGate: has('--no-role-gate'),
    configPath: get('--config'),
    reportPath: get('--report'),
  };
}

/** Bounded-concurrency map. Ordered results, failures already folded in by the callers. */
async function poolImpl<T, R>(items: T[], limit: number, fn: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const i = cursor++;
      if (i >= items.length) return;
      results[i] = await fn(items[i], i);
    }
  });
  await Promise.all(workers);
  return results;
}

function usage(): never {
  console.log(`
GalaxyHire scraper-node — collect a role from career-ops portals into the corpus

  npm start -- --role "software engineer" [options]

  --role <text>          role to scrape (required unless --list)
  --hours <n>            recency window, default 24
  --location <text>      hint passed to sources that support it; NOT a result filter
  --concurrency <n>      parallel portals, default 8
  --portals a,b,c        exact portal selection (UI toggles); ids from --list
  --only <id>            single portal, manual debugging (mutually exclusive with --portals)
  --max-portals <n>      cap portals this run — fastest way to smoke-test
  --no-role-gate         collect unfiltered (relevance audit mode)
  --dry-run              fetch and validate, post nothing
  --list                 show what would run, fetch nothing
  --config <path>        portal config, defaults to config/portals.yml then the example
  --report <path>        write a machine-readable per-portal audit report
`);
  process.exit(1);
}

/**
 * Resolve which config entries run this invocation.
 * `--portals` (UI selection) > `--only` (manual) > config defaults (`gh.toggle !== false`,
 * policy not `off`). Every requested id must match something: a typo'd portal silently
 * disappearing would present a broken selection as a healthy short run.
 */
function selectEntries(entries: PortalEntry[], args: Args): PortalEntry[] {
  let selected: PortalEntry[];
  if (args.only && args.portals) {
    throw new Error('--only and --portals are mutually exclusive (one is manual, one is the UI)');
  }
  if (args.portals) {
    const wanted = new Set(args.portals);
    selected = entries.filter(e => wanted.has(resolveEntryId(e)));
    const matched = new Set(selected.map(resolveEntryId));
    const unknown = [...wanted].filter(w => !matched.has(w));
    if (unknown.length) throw new Error(`unknown portal id(s): ${unknown.join(', ')} — see --list`);
  } else if (args.only) {
    selected = entries.filter(e => resolveEntryId(e).includes(args.only!));
    if (!selected.length) throw new Error(`--only ${args.only} matched nothing — see --list`);
  } else {
    selected = entries.filter(e => e.gh?.toggle !== false && (e.gh?.recency ?? 'strict') !== 'off');
  }
  if (args.maxPortals !== undefined) selected = selected.slice(0, args.maxPortals);
  return selected;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.role && !args.list) usage();

  const observedAt = new Date();
  const client = new CorpusClient({ dryRun: args.dryRun });

  // The corpus receives every envelope; without it a non-dry run collects jobs into a void.
  // Fail before hitting dozens of boards. Dry-run stays a standalone portal probe.
  if (!args.dryRun && !(await client.health())) {
    console.error(`\n✗ corpus unreachable at ${client.url} — start it with \`make corpus\`, or re-run with --dry-run`);
    process.exitCode = 1;
    return;
  }

  const providers = await loadCareerOpsProviders();
  const config = loadPortalConfig(args.configPath);
  const entries = [...config.jobBoards, ...config.trackedCompanies].filter(isDirectlyFetchable);
  const registryModule = await import(`${REPO_ROOT}/services/scraper-node/vendor/career-ops-providers/_registry.mjs`);

  console.log(`career-ops   ${providers.size} providers · ${entries.length} fetchable entries from ${config.source}`);

  const selected = selectEntries(entries, args);

  if (args.list) {
    // Lists EVERY fetchable portal, not just the ones the selection rules picked: the unknown-id
    // error above tells the caller to "see --list", so the list must contain every valid id.
    const selectedIds = new Set(selected.map(resolveEntryId));
    for (const e of entries) {
      const id = resolveEntryId(e);
      console.log(
        `  ${selectedIds.has(id) ? "[x]" : "[ ]"} ${id.padEnd(36)} ${String(e.gh?.recency ?? 'strict').padEnd(11)} ${e.api ?? e.careers_url ?? e.provider}`,
      );
    }
    console.log(`\n  ${selected.length}/${entries.length} selected by default (--portals overrides)`);
    return;
  }

  console.log(`\nscraping role="${args.role}" hours=${args.hours} portals=${selected.length} concurrency=${args.concurrency}${args.dryRun ? ' [dry-run]' : ''}`);

  const results = await poolImpl(selected, args.concurrency, entry =>
    runEntry(entry, providers, registryModule as never, observedAt, {
      role: args.role,
      hours: args.hours,
      location: args.location,
      maxPages: 5,
      roleGate: !args.noRoleGate,
    }),
  );

  const envelopes: JobEnvelope[] = [];
  const outcomes: RunOutcome[] = [];
  for (const r of results) {
    envelopes.push(...r.envelopes);
    outcomes.push(...r.outcomes);
  }

  report(outcomes, envelopes);
  if (args.reportPath) {
    writeAuditReport(args.reportPath, {
      startedAt: observedAt,
      role: args.role,
      location: args.location,
      hours: args.hours,
      dryRun: args.dryRun,
      roleGate: !args.noRoleGate,
      configuredEntries: entries.length,
      outcomes,
      envelopes,
    });
  }

  const summary = await client.post(envelopes);
  console.log(`\ncorpus ${args.dryRun ? '(dry-run) ' : ''}${client.url}`);
  console.log(`  posted     ${summary.posted}`);
  if (!args.dryRun) {
    console.log(`  upserted   ${summary.upserted}`);
    console.log(`  canonical  ${summary.canonical}`);
  }
  if (summary.invalid.length) {
    console.log(`  invalid    ${summary.invalid.length} (rejected by the envelope contract)`);
    for (const i of summary.invalid.slice(0, 5)) console.log(`    [${i.index}] ${i.field}: ${i.reason}`);
  }
  for (const e of summary.batchErrors) console.error(`  batch error: ${e}`);

  if (!args.dryRun && summary.posted > 0) {
    // Retrieval filters on embedding_version, so skipping this leaves the scrape invisible to
    // search until the scheduler's next pass. Cross-run dedup + freshness bookkeeping happen
    // corpus-side when the run finalizes (galaxy/scrape/runner.py).
    const embedded = await client.embedPending();
    if (embedded !== null) console.log(`  embedded   ${embedded}`);
    const count = await client.count();
    if (count) {
      console.log(`\ncorpus now holds ${count.canonical_jobs} jobs, ${count.searchable} searchable`);
      if (count.pending_embedding > 0) {
        console.log(`  ${count.pending_embedding} awaiting embedding — POST /ingest/embed to finish`);
      }
    }
  }

  if (summary.batchErrors.length || summary.invalid.length) process.exitCode = 1;
}

function report(outcomes: RunOutcome[], envelopes: JobEnvelope[]) {
  const ran = outcomes.filter(o => !o.skipped);
  const ok = ran.filter(o => o.ok);
  const failed = ran.filter(o => !o.ok);
  const skipped = outcomes.filter(o => o.skipped);
  const producing = ok.filter(o => o.jobs > 0).sort((a, b) => b.jobs - a.jobs);
  const withDesc = envelopes.filter(e => (e.fields.description_md ?? '').trim().length > 40).length;

  console.log(`\n── results ──`);
  console.log(`  portals run      ${ran.length}  (${ok.length} ok, ${failed.length} failed)`);
  console.log(`  skipped          ${skipped.length}`);
  console.log(`  jobs collected   ${envelopes.length}`);
  console.log(`  with description ${envelopes.length ? `${withDesc} (${Math.round((withDesc / envelopes.length) * 100)}%)` : '0'}`);
  const dropped = ok.reduce((n, o) => n + (o.dropped_stale ?? 0) + (o.dropped_undated ?? 0), 0);
  const irrelevant = ok.reduce((n, o) => n + (o.dropped_irrelevant ?? 0), 0);
  const offLocation = ok.reduce((n, o) => n + (o.dropped_location ?? 0), 0);
  if (dropped || irrelevant || offLocation) console.log(`  dropped stale/undated ${dropped} · irrelevant ${irrelevant} · off-location ${offLocation}`);

  if (producing.length) {
    console.log('\n  top portals:');
    console.log('    jobs  desc  undat  stale  irrel  portal (entry)');
    for (const o of producing.slice(0, 15)) {
      console.log(
        `    ${String(o.jobs).padStart(4)}  ${String(o.desc_present ?? 0).padStart(4)}  ` +
        `${String(o.dropped_undated ?? 0).padStart(5)}  ${String(o.dropped_stale ?? 0).padStart(5)}  ` +
        `${String(o.dropped_irrelevant ?? 0).padStart(5)}  ${o.portal} (${o.entry})`,
      );
    }
  }
  if (failed.length) {
    const shown = Math.min(12, failed.length);
    console.log(`\n  failures${failed.length > shown ? ` (first ${shown} of ${failed.length})` : ` (${failed.length})`}:`);
    for (const o of failed.slice(0, shown)) console.log(`    ${o.portal} (${o.entry}): ${o.error}`);
  }
}

function writeAuditReport(
  destination: string,
  input: {
    startedAt: Date;
    role: string;
    location?: string;
    hours: number;
    dryRun: boolean;
    roleGate: boolean;
    configuredEntries: number;
    outcomes: RunOutcome[];
    envelopes: JobEnvelope[];
  },
) {
  const bySite = new Map<string, JobEnvelope[]>();
  for (const envelope of input.envelopes) {
    const rows = bySite.get(envelope.site) ?? [];
    rows.push(envelope);
    bySite.set(envelope.site, rows);
  }
  const sourceResults = [...bySite.entries()].map(([site, rows]) => {
    const withDesc = rows.filter(r => (r.fields.description_md ?? '').trim().length > 40).length;
    const dated = rows.filter(r => r.fields.date_posted).length;
    return {
      site,
      jobs: rows.length,
      with_description: withDesc,
      desc_rate: rows.length ? Number((withDesc / rows.length).toFixed(4)) : 0,
      with_date_posted: dated,
      sample_titles: rows.slice(0, 5).map(row => row.fields.title),
    };
  }).sort((a, b) => b.jobs - a.jobs || a.site.localeCompare(b.site));

  const payload = {
    schema_version: 2,
    connector_set: 'career-ops',
    started_at: input.startedAt.toISOString(),
    finished_at: new Date().toISOString(),
    query: {
      role: input.role,
      location: input.location ?? null,
      hours: input.hours,
      dry_run: input.dryRun,
      role_gate: input.roleGate,
    },
    inventory: { configured_entries: input.configuredEntries, portals_run: input.outcomes.length },
    summary: {
      ok: input.outcomes.filter(row => row.ok && !row.skipped).length,
      productive: input.outcomes.filter(row => row.ok && !row.skipped && row.jobs > 0).length,
      // A portal answering 200-with-zero-rows and one that threw are different facts. The
      // diary rule: never present a failed connector as a zero-result success.
      empty_unverified: input.outcomes.filter(row => row.ok && !row.skipped && row.jobs === 0).length,
      failed: input.outcomes.filter(row => !row.ok && !row.skipped).length,
      skipped: input.outcomes.filter(row => Boolean(row.skipped)).length,
      jobs: input.envelopes.length,
      with_description: sourceResults.reduce((n, r) => n + r.with_description, 0),
    },
    portal_outcomes: input.outcomes,
    source_results: sourceResults,
  };

  const absolute = path.resolve(destination);
  mkdirSync(path.dirname(absolute), { recursive: true });
  writeFileSync(absolute, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  console.log(`\n  audit report  ${absolute}`);
}

try {
  await main();
} catch (err) {
  console.error(`✗ ${(err as Error).message}`);
  process.exitCode = 1;
}
