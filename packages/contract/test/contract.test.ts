/**
 * Envelope contract tests.
 *
 * The portal-key/registry/D5 suites that used to live here were deleted with the multi-project
 * connector system — with career-ops as the only source there is no cross-project portal identity
 * to resolve (MAJOR-CHANGE/02 §1d). What remains is the wire format itself, which is still the
 * seam between two processes and worth every assertion here.
 */

import { describe, expect, it } from 'vitest';

import { partitionEnvelopes, validateEnvelope, type JobEnvelope } from '../src/envelope';

function envelope(over: Partial<JobEnvelope> = {}): JobEnvelope {
  return {
    site: 'greenhouse',
    source_job_id: '4012345',
    url: 'https://boards.greenhouse.io/acme/jobs/4012345',
    observed_at: '2026-07-25T10:00:00Z',
    fields: { title: 'Software Engineer', company: 'Acme' },
    ...over,
  };
}

describe('envelope validation', () => {
  it('accepts the minimum viable observation', () => {
    expect(validateEnvelope(envelope())).toMatchObject({ site: 'greenhouse' });
  });

  it('accepts an empty company — list pages often cannot expose it', () => {
    // career-ops documents this explicitly in its provider contract; the corpus backfills it.
    expect(() => validateEnvelope(envelope({ fields: { title: 'SWE', company: '' } }))).not.toThrow();
  });

  it('accepts a provider id the corpus enum has never heard of', () => {
    // `site` is open by design (models/enums.py `_missing_`): the vendored provider set is the
    // source of truth, and the contract must not hardcode a copy of it.
    expect(() => validateEnvelope(envelope({ site: 'arbeitnow' }))).not.toThrow();
  });

  it.each([
    ['site', { site: '' }],
    ['source_job_id', { source_job_id: '  ' }],
    ['url', { url: '' }],
  ])('rejects a missing %s', (field, over) => {
    expect(() => validateEnvelope(envelope(over as Partial<JobEnvelope>))).toThrowError(
      expect.objectContaining({ field }),
    );
  });

  it('rejects a relative url — the corpus dedups on absolute urls', () => {
    expect(() => validateEnvelope(envelope({ url: '/jobs/123' }))).toThrowError(/not absolute/);
  });

  it('rejects an unparseable observed_at', () => {
    expect(() => validateEnvelope(envelope({ observed_at: 'last tuesday' }))).toThrowError(
      expect.objectContaining({ field: 'observed_at' }),
    );
  });

  it('rejects a missing title but not a missing salary', () => {
    expect(() => validateEnvelope(envelope({ fields: { title: '', company: 'Acme' } }))).toThrowError(
      expect.objectContaining({ field: 'fields.title' }),
    );
    expect(() =>
      validateEnvelope(envelope({ fields: { title: 'SWE', company: 'Acme', compensation: null } })),
    ).not.toThrow();
  });

  it('isolates one bad row instead of losing the batch', () => {
    const { valid, rejected } = partitionEnvelopes([envelope(), { site: 'x' }, envelope()]);
    expect(valid).toHaveLength(2);
    expect(rejected).toEqual([{ index: 1, field: 'source_job_id', reason: expect.any(String) }]);
  });
});
