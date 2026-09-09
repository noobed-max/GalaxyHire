/**
 * career-ops `Job` → `JobEnvelope`.
 *
 * career-ops providers are zero-token list-page readers: they return whatever the board's own
 * listing payload gave up for free, and deliberately do not make a second request per job. So a
 * lot of envelope fields are legitimately absent here — that is the design, not a gap to paper
 * over. The corpus normalizer derives seniority, skills, and keywords at ingest.
 *
 * The provider contract is documented in `vendor/career-ops-providers/_types.js`.
 */

import type { JobEnvelope, RawJobFields } from '@galaxyhire/contract';

import { normalizeUrl } from '../url-key';

/** The shape career-ops providers return. Mirrors the `Job` typedef in `_types.js`. */
export interface CareerOpsJob {
  title: string;
  url: string;
  company: string;
  location: string;
  description?: string;
  /** Epoch **milliseconds**, not seconds. */
  postedAt?: number;
  trustScore?: number;
  trustFlags?: string[];
  trustLevel?: 'high' | 'medium' | 'low';
  /**
   * Seniority the *source* stated, where a board publishes it as a structured field.
   *
   * Distinct from anything inferred: solid.jobs returns `experienceLevel` per posting, which beats
   * reading a level out of the title. The corpus normalizer only derives `seniority` when it is
   * absent, so supplying it here means source data wins over inference — the right precedence.
   */
  seniorityHint?: string;
  /**
   * Set by `recency.ts` before mapping, never by a provider: which kind of freshness this row
   * earned. `posted` = the source's own date puts it inside the window; `first_seen` = the source
   * publishes no usable date and novelty is decided retrieval-side by `first_seen_at`. Surfaced
   * in `fields.extra.freshness` so the UI can badge the two claims differently.
   */
  freshness?: 'posted' | 'first_seen';
}

/**
 * Split career-ops' single free-text location into the envelope's structured form.
 *
 * These strings are whatever the employer typed — "Hybrid - London", "Remote (US)",
 * "Berlin, Germany", "San Francisco, CA, United States". There is no schema to lean on, so this
 * stays deliberately conservative: detect remoteness, then take the last comma-separated part as
 * the country only when there are 3+ parts (with 2, "Berlin, Germany" and "Austin, TX" are
 * indistinguishable without a gazetteer, and guessing wrong is worse than leaving it null).
 *
 * The full original always survives in `extra.location_raw`, so a better parser can be run over
 * the corpus later without re-scraping.
 */
export function parseLocation(raw: string): { city?: string; state?: string; country?: string; remote: boolean } {
  const text = (raw ?? '').trim();
  const remote = /\bremote\b|\banywhere\b|\bwork from home\b|\bwfh\b/i.test(text);
  if (!text) return { remote };

  // Strip a leading work-arrangement prefix: "Hybrid - London", "Remote — Berlin".
  const stripped = text.replace(/^\s*(hybrid|remote|on-?site|in-?office)\s*[-–—:|]\s*/i, '').trim();
  if (!stripped) return { remote };

  const parts = stripped.split(',').map(p => p.trim()).filter(Boolean);
  if (parts.length === 0) return { remote };
  if (parts.length === 1) return { city: parts[0], remote };
  if (parts.length === 2) return { city: parts[0], state: parts[1], remote };
  return { city: parts[0], state: parts[1], country: parts[parts.length - 1], remote };
}

/**
 * career-ops jobs carry no source-side id — the provider contract uses `url` as the dedup key,
 * normalized through career-ops' own URL canon (`url-key.mjs`, ported to `src/url-key.ts`) so
 * `?utm_…` / trailing-slash / fragment spellings of one posting stop landing as separate
 * sightings. Falls back to the raw url only when the canon refuses the input (non-URL), where
 * "keep it visible as a duplicate" beats "merge on a placeholder".
 *
 * Safe because the corpus keys observations on `(site, source_job_id)` — uniqueness only has to
 * hold within one portal, which a board's own job URL does by construction.
 */
export function sourceJobId(job: CareerOpsJob): string {
  return normalizeUrl(job.url) || job.url;
}

/**
 * Map a board's own level wording onto the envelope's bands.
 *
 * Only exact, unambiguous words are accepted. A board using its own scheme should fall through to
 * the corpus normalizer rather than be guessed at — a wrong level is worse than none, because the
 * seniority filter acts on it.
 */
export function normalizeSeniorityHint(hint: string | undefined): RawJobFields['seniority'] {
  const value = (hint ?? '').trim().toLowerCase();
  switch (value) {
    case 'intern':
    case 'internship':
    case 'trainee':
      return 'intern';
    case 'junior':
    case 'entry':
      return 'junior';
    // solid.jobs uses "Regular" for what every other board calls mid-level.
    case 'regular':
    case 'mid':
    case 'mid-level':
    case 'intermediate':
      return 'mid';
    case 'senior':
      return 'senior';
    case 'lead':
    case 'staff':
    case 'principal':
    case 'expert':
      return 'lead';
    default:
      return undefined;
  }
}

export function toEnvelope(job: CareerOpsJob, site: string, observedAt: Date): JobEnvelope {
  const loc = parseLocation(job.location);

  const extra: Record<string, unknown> = {};
  if (job.location) extra.location_raw = job.location;
  // The freshness claim, set by recency.ts. Default 'posted' covers callers that map without
  // running the policy layer (tests, probe) — those carry a real date or null, and a null
  // date_posted with freshness 'posted' is a mapper misuse the corpus note field will expose.
  extra.freshness = job.freshness ?? 'posted';
  // Preserve career-ops' posting-legitimacy signals rather than discarding them — the corpus has
  // its own legitimacy detector and a second independent opinion is worth keeping.
  if (job.trustScore !== undefined) extra.trust_score = job.trustScore;
  if (job.trustLevel !== undefined) extra.trust_level = job.trustLevel;
  if (job.trustFlags?.length) extra.trust_flags = job.trustFlags;

  const fields: RawJobFields = {
    title: job.title.trim(),
    company: (job.company ?? '').trim(),
    location: { city: loc.city ?? null, state: loc.state ?? null, country: loc.country ?? null, remote: loc.remote },
    // Providers only include a description when the list payload carried it for free (Lever's
    // `descriptionPlain`, for instance). It is plain text, so it goes in the markdown slot —
    // plain text is valid markdown, and the corpus treats this field as the searchable body.
    description_md: job.description?.trim() || null,
    date_posted: job.postedAt ? new Date(job.postedAt).toISOString() : null,
    // Only set when the board published a level itself; otherwise left for the corpus normalizer.
    seniority: normalizeSeniorityHint(job.seniorityHint),
    extra,
  };

  return {
    site,
    source_job_id: sourceJobId(job),
    url: job.url,
    observed_at: observedAt.toISOString(),
    raw_title: job.title,
    fields,
  };
}
