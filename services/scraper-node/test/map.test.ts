/**
 * Mapper tests. These are where correctness actually lives in this worker: everything else is
 * plumbing, but a wrong mapping silently corrupts every job from a portal, and the corpus has no
 * way to notice.
 */

import { describe, expect, it } from 'vitest';

import { validateEnvelope } from '@galaxyhire/contract';

import * as co from '../src/map/career-ops';
import { normalizeSeniorityHint } from '../src/map/career-ops';

const OBSERVED = new Date('2026-07-25T12:00:00.000Z');

describe('career-ops → envelope', () => {
  const job: co.CareerOpsJob = {
    title: '  Senior Platform Engineer  ',
    url: 'https://job-boards.greenhouse.io/vercel/jobs/5999792004',
    company: 'Vercel',
    location: 'Berlin, Berlin, Germany',
    postedAt: 1779369969000,
  };

  it('produces an envelope the contract accepts', () => {
    expect(() => validateEnvelope(co.toEnvelope(job, 'greenhouse', OBSERVED))).not.toThrow();
  });

  it('trims the title but keeps the original in raw_title', () => {
    const e = co.toEnvelope(job, 'greenhouse', OBSERVED);
    expect(e.fields.title).toBe('Senior Platform Engineer');
    expect(e.raw_title).toBe('  Senior Platform Engineer  ');
  });

  it('treats postedAt as epoch milliseconds', () => {
    // Reading it as seconds would date this job to 1970 and break every recency filter.
    expect(co.toEnvelope(job, 'greenhouse', OBSERVED).fields.date_posted).toBe(
      new Date(1779369969000).toISOString(),
    );
  });

  it('keys the source id on the normalized url', () => {
    // The provider contract has no id field and dedups on url; the corpus keys on
    // (site, source_job_id), so url-as-id is unique where it needs to be — and the url-key
    // canon means two tracking-param spellings of one posting stop being two sightings.
    expect(co.sourceJobId(job)).toBe(job.url);
    expect(co.sourceJobId({ ...job, url: job.url + '?utm_source=linkedin/' }))
      .toBe(job.url);
  });

  it('falls back to the raw url when the canon refuses the input', () => {
    // '' would collide every malformed url into one key — a silent merge of unrelated jobs.
    const weird = { ...job, url: 'not a url' };
    expect(co.sourceJobId(weird)).toBe('not a url');
  });

  it('omits date_posted rather than inventing one', () => {
    const { postedAt, ...noDate } = job;
    expect(co.toEnvelope(noDate as co.CareerOpsJob, 'greenhouse', OBSERVED).fields.date_posted).toBeNull();
  });

  it('labels the freshness claim and defaults to posted', () => {
    expect(co.toEnvelope(job, 'greenhouse', OBSERVED).fields.extra?.freshness).toBe('posted');
    expect(co.toEnvelope({ ...job, freshness: 'first_seen' }, 'remotive', OBSERVED).fields.extra?.freshness)
      .toBe('first_seen');
  });

  it('always preserves the raw location string', () => {
    const e = co.toEnvelope(job, 'greenhouse', OBSERVED);
    expect(e.fields.extra?.location_raw).toBe('Berlin, Berlin, Germany');
  });

  it('carries career-ops trust signals into extra', () => {
    const e = co.toEnvelope({ ...job, trustScore: 95, trustLevel: 'high', trustFlags: ['x'] }, 'greenhouse', OBSERVED);
    expect(e.fields.extra).toMatchObject({ trust_score: 95, trust_level: 'high', trust_flags: ['x'] });
  });

  describe('location parsing', () => {
    it('splits three-part locations into city/state/country', () => {
      expect(co.parseLocation('Austin, TX, United States')).toEqual({
        city: 'Austin', state: 'TX', country: 'United States', remote: false,
      });
    });

    it('leaves country null on two-part locations', () => {
      // "Berlin, Germany" and "Austin, TX" are indistinguishable without a gazetteer. Guessing
      // wrong is worse than leaving it for the corpus normalizer.
      expect(co.parseLocation('Berlin, Germany')).toEqual({ city: 'Berlin', state: 'Germany', remote: false });
    });

    it('detects remoteness from the free text', () => {
      expect(co.parseLocation('Remote (US)').remote).toBe(true);
      expect(co.parseLocation('Anywhere').remote).toBe(true);
      expect(co.parseLocation('London').remote).toBe(false);
    });

    it('strips a work-arrangement prefix instead of treating it as a city', () => {
      expect(co.parseLocation('Hybrid - London')).toEqual({ city: 'London', remote: false });
      expect(co.parseLocation('Remote — Berlin')).toMatchObject({ city: 'Berlin', remote: true });
    });

    it('survives an empty location', () => {
      expect(co.parseLocation('')).toEqual({ remote: false });
    });
  });

  describe('seniority hint normalization', () => {
    it('maps only exact, unambiguous words', () => {
      expect(normalizeSeniorityHint('Regular')).toBe('mid'); // solid.jobs' word for mid-level
      expect(normalizeSeniorityHint('internship')).toBe('intern');
      expect(normalizeSeniorityHint('STAFF')).toBe('lead');
    });

    it('leaves anything else for the corpus normalizer rather than guessing', () => {
      expect(normalizeSeniorityHint('Level 4')).toBeUndefined();
      expect(normalizeSeniorityHint(undefined)).toBeUndefined();
    });
  });
});
