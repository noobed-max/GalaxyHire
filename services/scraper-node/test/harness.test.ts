/**
 * Harness tests: portal config loading + ids, the recency policy, the role gate, query
 * injection, the url canon, and the corpus client's batching and validation behaviour.
 *
 * (This file used to also cover the D5 registry gate — the runtime half of the multi-project
 * connector resolution. That whole system is deleted; one project cannot shadow itself.)
 */

import { readFileSync } from 'node:fs';

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import type { JobEnvelope } from '@galaxyhire/contract';

import { isDirectlyFetchable, loadPortalConfig, resolveConfigPath, resolveEntryId, slugFromUrl, type PortalEntry } from '../src/config';
import { applyRecency, parseRecencyPolicy } from '../src/recency';
import { locationPassesGate, titlePassesRoleGate } from '../src/audit/relevance';
import { hasServerSideQuery, injectLocation, injectRole } from '../src/query-injection';
import { normalizeUrl } from '../src/url-key';
import { CorpusClient } from '../src/corpus';
import type { CareerOpsJob } from '../src/map/career-ops';

const HOUR = 3_600_000;
const NOW = Date.parse('2026-08-30T12:00:00Z');

const job = (over: Partial<CareerOpsJob> = {}): CareerOpsJob => ({
  title: 'Software Engineer',
  url: 'https://example.com/jobs/1',
  company: 'Acme',
  location: 'Berlin',
  ...over,
});

describe('recency policy', () => {
  it('parses only the three declared policies, defaulting to strict', () => {
    expect(parseRecencyPolicy('first_seen')).toBe('first_seen');
    expect(parseRecencyPolicy('nonsense')).toBe('strict');
    expect(parseRecencyPolicy(undefined)).toBe('strict');
  });

  it('strict: keeps dated jobs inside the window', () => {
    const { jobs, stats } = applyRecency([job({ postedAt: NOW - 6 * HOUR })], 'strict', 24, NOW);
    expect(jobs).toHaveLength(1);
    expect(jobs[0].freshness).toBe('posted');
    expect(stats.dropped_stale).toBe(0);
  });

  it('strict: DROPS undated jobs — the deliberate inversion of career-ops semantics', () => {
    // career-ops' buildPostingAgeFilter passes undated rows ("don't penalize missing data").
    // GalaxyHire's product rule is the opposite (MAJOR-CHANGE/01 R3): if the source can't prove
    // the posting is ≤24h, it doesn't belong in a ≤24h scrape. This assertion is that decision.
    const { jobs, stats } = applyRecency([job()], 'strict', 24, NOW);
    expect(jobs).toHaveLength(0);
    expect(stats.dropped_undated).toBe(1);
  });

  it('strict: drops dated-but-stale and counts it', () => {
    const { jobs, stats } = applyRecency([job({ postedAt: NOW - 25 * HOUR })], 'strict', 24, NOW);
    expect(jobs).toHaveLength(0);
    expect(stats.dropped_stale).toBe(1);
  });

  it('treats a far-future date as unusable, not as forever-fresh', () => {
    // A broken source clock would otherwise pass `strict` indefinitely — the date never ages
    // past the cutoff. Within 1h of skew it is tolerated; beyond that it is not a date.
    const skewed = applyRecency([job({ postedAt: NOW + 30 * 60_000 })], 'strict', 24, NOW);
    expect(skewed.jobs).toHaveLength(1);
    const broken = applyRecency([job({ postedAt: NOW + 48 * HOUR })], 'strict', 24, NOW);
    expect(broken.jobs).toHaveLength(0);
    expect(broken.stats.dropped_undated).toBe(1);
    const { jobs } = applyRecency([job({ postedAt: NOW + 48 * HOUR })], 'first_seen', 24, NOW);
    expect(jobs[0].freshness).toBe('first_seen');
  });

  it('first_seen: keeps everything and labels the non-provable rows honestly', () => {
    // A dated in-window row earns 'posted' even here — the claim is true, and the UI badges
    // differently based on which claim it is.
    const { jobs, stats } = applyRecency(
      [job({ postedAt: NOW - 2 * HOUR }), job(), job({ postedAt: NOW - 90 * 24 * HOUR })],
      'first_seen', 24, NOW,
    );
    expect(jobs).toHaveLength(3);
    expect(jobs.map(j => j.freshness)).toEqual(['posted', 'first_seen', 'first_seen']);
    expect(stats.kept_first_seen).toBe(2);
  });
});

describe('role gate', () => {
  it('software family matches across the canonical spellings', () => {
    for (const q of ['software engineer', 'software developer', 'SDE']) {
      expect(titlePassesRoleGate(q, 'Senior Software Engineer II')).toBe(true);
      expect(titlePassesRoleGate(q, 'Backend Developer')).toBe(true);
    }
  });

  it('software family rejects non-engineering titles', () => {
    // This is the defect class that made the product look broken: "software engineer" returning
    // Production Associate. The gate is ingest-side so the corpus never holds that noise.
    expect(titlePassesRoleGate('software engineer', 'Production Associate')).toBe(false);
    expect(titlePassesRoleGate('software engineer', 'Business Development Manager')).toBe(false);
  });

  it('generic queries pass on any discriminating token', () => {
    expect(titlePassesRoleGate('data analyst', 'Senior Data Analyst')).toBe(true);
    expect(titlePassesRoleGate('data analyst', 'Financial Analyst')).toBe(false);
  });

  it('abstains rather than silently deleting on undiscriminating queries', () => {
    expect(titlePassesRoleGate('', 'anything')).toBe(true);
    expect(titlePassesRoleGate('engineer', 'anything')).toBe(true); // only stopword tokens
  });
});

describe('location gate', () => {
  it('meets UK spellings on either side', () => {
    expect(locationPassesGate('United Kingdom', 'London, UK')).toBe(true);
    expect(locationPassesGate('UK', 'London, United Kingdom')).toBe(true);
    expect(locationPassesGate('London, UK', 'London, United Kingdom')).toBe(true);
  });

  it('drops other countries for a country search', () => {
    expect(locationPassesGate('United Kingdom', 'Sydney, Australia')).toBe(false);
    expect(locationPassesGate('UK', 'Berlin, Germany')).toBe(false);
  });

  it('matches multi-part queries per component, not by adjacency', () => {
    // "Bengaluru, Karnataka, India" must pass a "Bengaluru, India" search — the source
    // never promised the two would sit side by side.
    expect(locationPassesGate('Bengaluru, India', 'Bengaluru, Karnataka, India')).toBe(true);
    expect(locationPassesGate('Bengaluru, India', 'Bengaluru, India')).toBe(true);
    expect(locationPassesGate('Bengaluru, India', 'Mumbai, Maharashtra, India')).toBe(false);
  });

  it('lets missing job locations through rather than deleting them', () => {
    // Most aggregator rows carry no usable location; dropping them would empty the boards.
    expect(locationPassesGate('United Kingdom', '')).toBe(true);
    expect(locationPassesGate('', 'Sydney, Australia')).toBe(true);
  });

  it('keeps bare-remote rows only for a remote search', () => {
    expect(locationPassesGate('remote', 'Remote')).toBe(true);
    expect(locationPassesGate('United Kingdom', 'Remote')).toBe(false);
    expect(locationPassesGate('remote uk', 'Remote')).toBe(true);
  });
});

describe('query injection', () => {
  it('reaches the keyword-driven providers under their own config keys', () => {
    expect(injectRole({ name: 'VDAB' }, 'software engineer', 'vdab')).toMatchObject({
      vdab: { keywords: ['software engineer'] },
    });
    expect(injectRole({ name: 'WTTJ' }, 'backend', 'wttj')).toMatchObject({
      wttj: { queries: ['backend'] },
    });
    expect(injectRole({ name: 'MCF' }, 'devops', 'mycareersfuture')).toMatchObject({
      mycareersfuture: { keywords: ['devops'] },
    });
    expect(injectRole({ name: 'a16z' }, 'swe', 'a16z-speedrun-talent')).toMatchObject({ q: 'swe' });
  });

  it('leaves board-wide feeds untouched and preserves existing nested config', () => {
    const entry: PortalEntry = { name: 'Arbeitnow' };
    expect(injectRole(entry, 'swe', 'arbeitnow')).toBe(entry);
    const nested = { name: 'VDAB', vdab: { size: 50 } };
    expect(injectRole(nested, 'swe', 'vdab')).toMatchObject({ vdab: { size: 50, keywords: ['swe'] } });
  });

  it('hasServerSideQuery agrees with the injector table', () => {
    expect(hasServerSideQuery('wttj')).toBe(true);
    expect(hasServerSideQuery('greenhouse')).toBe(false);
  });

  it('injects the location where the board takes one, nowhere else', () => {
    // amazon.jobs is global without loc_query; every other provider gets no location key.
    expect(injectLocation({ name: 'Amazon', careers_url: 'https://www.amazon.jobs' }, 'United Kingdom', 'amazon'))
      .toMatchObject({ amazon: { loc_query: 'United Kingdom' } });
    const entry: PortalEntry = { name: 'Arbeitnow' };
    expect(injectLocation(entry, 'United Kingdom', 'arbeitnow')).toBe(entry);
    expect(injectLocation({ name: 'Amazon' }, '', 'amazon')).toEqual({ name: 'Amazon' });
  });
});

describe('portal entry ids', () => {
  it('names board-wide feeds by provider', () => {
    expect(resolveEntryId({ name: 'Arbeitnow', provider: 'arbeitnow' })).toBe('arbeitnow');
  });

  it('names tenants provider:slug so one toggle never hides 175 employers', () => {
    expect(resolveEntryId({ name: 'GitLab', provider: 'greenhouse', api: 'https://boards-api.greenhouse.io/v1/boards/gitlab/jobs' }))
      .toBe('greenhouse:gitlab');
    expect(resolveEntryId({ name: 'OpenAI', provider: 'ashby', careers_url: 'https://jobs.ashbyhq.com/openai' }))
      .toBe('ashby:openai');
    expect(resolveEntryId({ name: 'Spotify', provider: 'lever', careers_url: 'https://jobs.lever.co/spotify' }))
      .toBe('lever:spotify');
  });

  it('slugFromUrl refuses generic path segments', () => {
    expect(slugFromUrl('https://jobs.lever.co')).toBeNull();
    expect(slugFromUrl('')).toBeNull();
  });
});

describe('url canon', () => {
  it('strips tracking params and keeps functional ones', () => {
    expect(normalizeUrl('https://example.com/Jobs/1/?gh_src=linkedin&utm_medium=x'))
      .toBe('https://example.com/Jobs/1');
    expect(normalizeUrl('https://example.com/jobs/1?gh_jid=42')).toContain('gh_jid=42');
  });

  it('returns NO KEY for anything that is not an http(s) url', () => {
    // '' must never compare equal as a key — callers fall back to the raw url (map/career-ops).
    expect(normalizeUrl('N/A')).toBe('');
    expect(normalizeUrl('ftp://x/jobs/1')).toBe('');
  });
});

describe('portal config', () => {
  it('falls back to the shipped example so a fresh checkout can scrape', () => {
    expect(resolveConfigPath()).toMatch(/portals\.(yml|example\.yml)$/);
  });

  it('loads company and board entries from the example', () => {
    const cfg = loadPortalConfig();
    expect(cfg.trackedCompanies.length).toBeGreaterThan(50);
    expect(cfg.jobBoards.length).toBeGreaterThan(0);
  });

  it('drops entries explicitly disabled', () => {
    const cfg = loadPortalConfig();
    for (const e of [...cfg.trackedCompanies, ...cfg.jobBoards]) expect(e.enabled).not.toBe(false);
  });

  it('reads the gh policy block without confusing it for provider config', () => {
    const cfg = loadPortalConfig();
    const boards = cfg.jobBoards.filter(e => e.gh?.recency === 'strict');
    expect(boards.length).toBeGreaterThan(0); // measured-fresh portals must be strict
  });

  describe('fetchability', () => {
    const entry = (o: Partial<PortalEntry>): PortalEntry => ({ name: 'X', ...o });

    it('accepts an entry with an api, careers_url, or explicit provider', () => {
      expect(isDirectlyFetchable(entry({ api: 'https://x/api' }))).toBe(true);
      expect(isDirectlyFetchable(entry({ careers_url: 'https://x' }))).toBe(true);
      expect(isDirectlyFetchable(entry({ provider: 'solidjobs' }))).toBe(true);
    });

    it('rejects a websearch-only entry', () => {
      // career-ops drives those through a search API from an AI CLI; this harness has no such
      // path, and letting them through just manufactures provider failures.
      expect(isDirectlyFetchable(entry({ careers_url: 'https://x', scan_method: 'websearch' }))).toBe(false);
    });

    it('still accepts a websearch entry that also has an api', () => {
      expect(isDirectlyFetchable(entry({ api: 'https://x/api', scan_method: 'websearch' }))).toBe(true);
    });

    it('rejects an entry that would shell out', () => {
      // local-parser execs a configured command. Not something an unattended worker should do.
      expect(isDirectlyFetchable(entry({ careers_url: 'https://x', parser: { command: 'node' } }))).toBe(false);
    });

    it('rejects an entry with no way to reach it', () => {
      expect(isDirectlyFetchable(entry({}))).toBe(false);
    });
  });
});

describe('portal ids — shared golden fixture', () => {
  // This fixture is asserted against by the corpus's Python catalog tests too
  // (services/corpus/tests/test_portal_catalog.py). The UI sends ids across the process
  // boundary; both resolvers must agree, so both are pinned to the same expected outputs.
  const cases: { entry: PortalEntry; id: string }[] =
    JSON.parse(readFileSync(new URL('./fixtures/entry-ids.json', import.meta.url), 'utf8')).cases;

  it.each(cases)('$id', ({ entry, id }) => {
    expect(resolveEntryId(entry)).toBe(id);
  });
});

describe('corpus client', () => {
  const envelope = (id: string): JobEnvelope => ({
    site: 'greenhouse',
    source_job_id: id,
    url: `https://example.com/${id}`,
    observed_at: '2026-07-25T10:00:00Z',
    fields: { title: 'SWE', company: 'Acme' },
  });

  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ fetched: 0, upserted: 2, canonical_out: 2, split_groups: 0, failures: 0 }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('splits large sets into independent batches', async () => {
    const client = new CorpusClient({ baseUrl: 'http://corpus.test', batchSize: 2 });
    await client.post([envelope('a'), envelope('b'), envelope('c')]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps going when one batch fails', async () => {
    // A broad scrape is tens of thousands of rows; one rejected batch must cost that slice only.
    fetchMock.mockImplementationOnce(async () => new Response('boom', { status: 500 }));
    const client = new CorpusClient({ baseUrl: 'http://corpus.test', batchSize: 1 });
    const summary = await client.post([envelope('a'), envelope('b')]);
    expect(summary.batchErrors).toHaveLength(1);
    expect(summary.posted).toBe(1);
  });

  it('rejects malformed envelopes locally instead of posting them', async () => {
    const client = new CorpusClient({ baseUrl: 'http://corpus.test' });
    const summary = await client.post([envelope('a'), { site: 'x' } as unknown as JobEnvelope]);
    expect(summary.invalid).toHaveLength(1);
    // The good row still went out.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('posts nothing at all on a dry run', async () => {
    const client = new CorpusClient({ baseUrl: 'http://corpus.test', dryRun: true });
    const summary = await client.post([envelope('a')]);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(summary.posted).toBe(1);
  });

  it('sends the api key when configured', async () => {
    const client = new CorpusClient({ baseUrl: 'http://corpus.test', apiKey: 'secret' });
    await client.post([envelope('a')]);
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers['x-api-key']).toBe('secret');
  });

  it('reports an unreachable corpus as unhealthy rather than throwing', async () => {
    fetchMock.mockImplementation(async () => { throw new Error('ECONNREFUSED'); });
    expect(await new CorpusClient({ baseUrl: 'http://corpus.test' }).health()).toBe(false);
  });
});
