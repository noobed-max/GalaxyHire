import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { enrichSolidJobs, enricherFor, htmlToText, ENRICHERS } from '../src/enrich/descriptions';
import type { CareerOpsJob } from '../src/map/career-ops';

const job = (url: string, over: Partial<CareerOpsJob> = {}): CareerOpsJob => ({
  title: 'Software Engineer',
  url,
  company: 'Acme',
  location: 'Berlin',
  ...over,
});

describe('htmlToText (delegated to the vendored stripper)', () => {
  it('strips tags and keeps readable structure', async () => {
    const out = await htmlToText('<div><h2>About</h2><p>We build things.</p><ul><li>Go</li><li>K8s</li></ul></div>');
    expect(out).toContain('About');
    expect(out).toContain('We build things.');
    expect(out).toContain('Go');
    expect(out).not.toContain('<div>');
  });

  it('drops script and style bodies', async () => {
    // Otherwise minified JS ends up in the embedding text and poisons semantic retrieval.
    const out = await htmlToText('<p>Real</p><script>var x = 1;</script><style>.a{color:red}</style>');
    expect(out).toContain('Real');
    expect(out).not.toContain('var x');
    expect(out).not.toContain('color:red');
  });
});

describe('enricher registry', () => {
  it('no longer duplicates what upstream providers already ship', () => {
    // greenhouse.mjs sends content=true (#3175) and ashby.mjs maps descriptionPlain itself;
    // the refetchers that used to live here became duplicate requests for bytes in hand.
    // If either reappears, check upstream first — the fix belongs in career-ops, not a fork.
    expect(ENRICHERS.greenhouse).toBeUndefined();
    expect(ENRICHERS.ashby).toBeUndefined();
    expect(Object.keys(ENRICHERS)).toEqual(['solidjobs']);
  });

  it('resolves only the portals that genuinely need the extra request', () => {
    expect(enricherFor('solidjobs')).toBe(enrichSolidJobs);
    expect(enricherFor('lever')).toBeUndefined(); // descriptionPlain ships in the list payload
    expect(enricherFor('arbeitnow')).toBeUndefined();
  });
});

describe('solidjobs description enrichment', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  const respond = (jobs: unknown[], totalPages = 1) =>
    new Response(JSON.stringify({ jobs, totalPages }), {
      status: 200, headers: { 'content-type': 'application/json' },
    });

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('joins on url, never on position', async () => {
    // The two responses are not guaranteed to be ordered identically; a positional join would
    // silently attach the wrong description to every job — worse than having none.
    fetchMock.mockResolvedValue(respond([
      { url: 'https://solid.jobs/o/2', description: '<p>Second</p>' },
      { url: 'https://solid.jobs/o/1', description: '<p>First</p>' },
    ]));
    const res = await enrichSolidJobs(
      { name: 'SolidJobs', careers_url: 'https://solid.jobs/public-api/offers/it' },
      [job('https://solid.jobs/o/1'), job('https://solid.jobs/o/2')],
    );
    expect(res.jobs[0].description).toBe('First');
    expect(res.jobs[1].description).toBe('Second');
    expect(res.enriched).toBe(2);
  });

  it('passes the board-published experience level through as seniorityHint', () => {
    // Source data beats the corpus normalizer's title inference.
    fetchMock.mockResolvedValue(respond([
      { url: 'https://solid.jobs/o/1', description: '<p>x</p>', experienceLevel: 'Regular' },
    ]));
    return enrichSolidJobs(
      { name: 'SolidJobs', careers_url: 'https://solid.jobs/public-api/offers/it' },
      [job('https://solid.jobs/o/1')],
    ).then(res => expect(res.jobs[0].seniorityHint).toBe('Regular'));
  });

  it('walks pagination and stops at totalPages', async () => {
    fetchMock
      .mockResolvedValueOnce(respond([{ url: 'https://solid.jobs/o/1', description: '<p>a</p>' }], 2))
      .mockResolvedValueOnce(respond([{ url: 'https://solid.jobs/o/2', description: '<p>b</p>' }], 2));
    const res = await enrichSolidJobs(
      { name: 'SolidJobs', careers_url: 'https://solid.jobs/public-api/offers/it' },
      [job('https://solid.jobs/o/1'), job('https://solid.jobs/o/2')],
    );
    expect(res.enriched).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('never costs the already-fetched jobs when enrichment fails', async () => {
    fetchMock.mockRejectedValue(new Error('ETIMEDOUT'));
    const res = await enrichSolidJobs(
      { name: 'SolidJobs', careers_url: 'https://solid.jobs/public-api/offers/it' },
      [job('https://solid.jobs/o/1')],
    );
    expect(res.jobs).toHaveLength(1);
    expect(res.enriched).toBe(0);
    expect(res.error).toMatch(/ETIMEDOUT/);
  });
});
