/**
 * The job envelope — the single wire format every scraper worker emits.
 *
 * This is deliberately *not* a new invention: it is the JSON serialization of the corpus
 * service's existing `SourceObservation` pydantic model
 * (`services/corpus/galaxy/models/job.py`). Keeping the two identical means the Python side
 * needs no translation layer at all — `SourceObservation.model_validate(envelope)` just works.
 *
 * Field names are therefore snake_case, matching the Python model rather than TS convention.
 * Do not "fix" that: the names are the contract.
 */

/** Seniority band. Mirrors `galaxy.models.enums.Seniority`. */
export type Seniority = 'intern' | 'junior' | 'mid' | 'senior' | 'lead' | 'exec';

/** Mirrors `galaxy.models.enums.OnsitePolicy`. */
export type OnsitePolicy = 'remote' | 'hybrid' | 'onsite';

/** Mirrors `galaxy.models.enums.CompInterval`. */
export type CompInterval = 'yearly' | 'monthly' | 'weekly' | 'daily' | 'hourly';

/** Mirrors `galaxy.models.enums.SalarySource`. */
export type SalarySource = 'direct_data' | 'description';

/** Mirrors `galaxy.models.job.Location`. */
export interface Location {
  country?: string | null;
  state?: string | null;
  city?: string | null;
  /** Defaults to false on the Python side when omitted. */
  remote?: boolean;
}

/** Mirrors `galaxy.models.job.Compensation`. */
export interface Compensation {
  interval?: CompInterval | null;
  min_amount?: number | null;
  max_amount?: number | null;
  currency?: string | null;
  salary_source?: SalarySource | null;
}

/**
 * Mirrors `galaxy.models.job.RawJobFields` — what one source returned, normalized but not
 * yet merged with other sources' view of the same role.
 *
 * The derived fields (`seniority`, `min_years_experience`, `onsite_policy`,
 * `clearance_required`, `jd_keywords`, `jd_skills`) are optional here on purpose: the corpus
 * normalizer derives them at ingest. A worker that happens to know them may pass them through,
 * but no worker is required to compute them.
 */
export interface RawJobFields {
  title: string;
  company: string;
  location?: Location;
  description_html?: string | null;
  description_md?: string | null;
  compensation?: Compensation | null;
  /** ISO 8601. Serialized as a string because JSON has no date type. */
  date_posted?: string | null;
  seniority?: Seniority | null;
  min_years_experience?: number | null;
  onsite_policy?: OnsitePolicy | null;
  clearance_required?: boolean | null;
  jd_keywords?: string[];
  jd_skills?: string[];
  /** Source-specific extras that don't fit the model. Preserved verbatim, never interpreted. */
  extra?: Record<string, unknown>;
}

/**
 * Mirrors `galaxy.models.job.SourceObservation` — one per-source sighting of a role.
 *
 * `site` is an open string, not a closed enum: it is a career-ops **provider id** (the vendored
 * tree ships ~80 of them), the corpus stores `site` as TEXT, and its own `Site` enum admits any
 * well-formed id (models/enums.py `_missing_`). Validation checks shape, not membership — the
 * set of providers is decided by the vendor tree, and pinning a copy here would only drift.
 */
export interface JobEnvelope {
  site: string;
  /** The platform's own id. Unique only *together with* `site`. */
  source_job_id: string;
  url: string;
  /** ISO 8601 timestamp of when this observation was made. */
  observed_at: string;
  /** The untouched title as the source presented it, before normalization. */
  raw_title?: string | null;
  fields: RawJobFields;
}

/** A batch as POSTed to the corpus service's `/ingest/observations`. */
export interface EnvelopeBatch {
  /** Which worker produced this batch — for provenance in the corpus, not for routing. */
  worker: 'scraper-node' | 'scraper-py';
  observations: JobEnvelope[];
}

// ── validation ────────────────────────────────────────────────────────────────

export class EnvelopeError extends Error {
  constructor(
    message: string,
    readonly field: string,
  ) {
    super(message);
    this.name = 'EnvelopeError';
  }
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v.trim().length > 0;
}

/**
 * Validate one envelope, throwing `EnvelopeError` on the first problem found.
 *
 * Checks only what the corpus cannot recover from on its own: the identity triple
 * (`site` + `source_job_id` + `url`), a usable `title`, and well-formed timestamps. Everything
 * else is optional by design and gets derived or defaulted at ingest — a worker being unable to
 * supply a salary is normal, not an error.
 *
 * `company` may be empty: several list-page sources genuinely cannot expose it at that level
 * (career-ops documents exactly this in its provider contract), and the corpus backfills it.
 */
export function validateEnvelope(e: unknown): JobEnvelope {
  if (typeof e !== 'object' || e === null) {
    throw new EnvelopeError('envelope must be an object', '.');
  }
  const o = e as Record<string, unknown>;

  if (!isNonEmptyString(o.site)) throw new EnvelopeError('site is required', 'site');
  if (!isNonEmptyString(o.source_job_id)) {
    throw new EnvelopeError('source_job_id is required', 'source_job_id');
  }
  if (!isNonEmptyString(o.url)) throw new EnvelopeError('url is required', 'url');
  try {
    // eslint-disable-next-line no-new
    new URL(o.url);
  } catch {
    throw new EnvelopeError(`url is not absolute: ${o.url}`, 'url');
  }

  if (!isNonEmptyString(o.observed_at) || Number.isNaN(Date.parse(o.observed_at))) {
    throw new EnvelopeError('observed_at must be an ISO 8601 timestamp', 'observed_at');
  }

  const fields = o.fields;
  if (typeof fields !== 'object' || fields === null) {
    throw new EnvelopeError('fields is required', 'fields');
  }
  const f = fields as Record<string, unknown>;
  if (!isNonEmptyString(f.title)) throw new EnvelopeError('fields.title is required', 'fields.title');
  if (typeof f.company !== 'string') {
    throw new EnvelopeError('fields.company must be a string (may be empty)', 'fields.company');
  }
  if (f.date_posted != null && Number.isNaN(Date.parse(String(f.date_posted)))) {
    throw new EnvelopeError('fields.date_posted must be ISO 8601 or null', 'fields.date_posted');
  }

  return o as unknown as JobEnvelope;
}

/**
 * Partition a batch into valid envelopes and rejects.
 *
 * Never throws. One malformed row out of a thousand must not lose the batch — the same
 * failure-isolation rule the ingest pipeline applies to whole sources.
 */
export function partitionEnvelopes(items: unknown[]): {
  valid: JobEnvelope[];
  rejected: { index: number; field: string; reason: string }[];
} {
  const valid: JobEnvelope[] = [];
  const rejected: { index: number; field: string; reason: string }[] = [];
  items.forEach((item, index) => {
    try {
      valid.push(validateEnvelope(item));
    } catch (err) {
      const e = err as EnvelopeError;
      rejected.push({ index, field: e.field ?? '.', reason: e.message });
    }
  });
  return { valid, rejected };
}
