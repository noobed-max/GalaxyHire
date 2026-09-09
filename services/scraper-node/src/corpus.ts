/**
 * Posts envelopes to the corpus service's `/ingest/observations`.
 *
 * Batching exists because a broad scrape produces tens of thousands of observations and one
 * request carrying all of them would be a single point of failure for the entire run. Batches are
 * independent: one rejected batch costs that slice, not the scrape.
 */

import { partitionEnvelopes, type JobEnvelope } from '@galaxyhire/contract';

export interface CorpusClientOptions {
  baseUrl?: string;
  apiKey?: string;
  batchSize?: number;
  /** Post nothing; report what would have been posted. */
  dryRun?: boolean;
}

export interface IngestResponse {
  fetched: number;
  upserted: number;
  canonical_out: number;
  split_groups: number;
  failures: number;
}

export interface PostSummary {
  posted: number;
  upserted: number;
  canonical: number;
  /** Envelopes the contract rejected before any request was made. */
  invalid: { index: number; field: string; reason: string }[];
  batchErrors: string[];
}

const DEFAULT_BATCH = 500;

export class CorpusClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly batchSize: number;
  readonly dryRun: boolean;

  constructor(opts: CorpusClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? process.env.CORPUS_URL ?? 'http://127.0.0.1:8100').replace(/\/+$/, '');
    this.apiKey = opts.apiKey ?? process.env.CORPUS_API_KEY;
    this.batchSize = opts.batchSize ?? DEFAULT_BATCH;
    this.dryRun = opts.dryRun ?? false;
  }

  /**
   * Validate locally, then post in batches.
   *
   * Validation runs first so a malformed envelope is attributed to the connector that produced it
   * rather than surfacing later as an opaque 422 covering a whole batch.
   */
  async post(envelopes: JobEnvelope[]): Promise<PostSummary> {
    const { valid, rejected } = partitionEnvelopes(envelopes);
    const summary: PostSummary = { posted: 0, upserted: 0, canonical: 0, invalid: rejected, batchErrors: [] };
    if (this.dryRun) {
      summary.posted = valid.length;
      return summary;
    }

    for (let i = 0; i < valid.length; i += this.batchSize) {
      const batch = valid.slice(i, i + this.batchSize);
      try {
        const res = await this.postBatch(batch);
        summary.posted += batch.length;
        summary.upserted += res.upserted;
        summary.canonical += res.canonical_out;
      } catch (err) {
        summary.batchErrors.push(`batch ${i / this.batchSize}: ${(err as Error).message}`);
      }
    }
    return summary;
  }

  private async postBatch(observations: JobEnvelope[]): Promise<IngestResponse> {
    const res = await fetch(`${this.baseUrl}/ingest/observations`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(this.apiKey ? { 'x-api-key': this.apiKey } : {}),
      },
      body: JSON.stringify({ worker: 'scraper-node', observations }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 300)}` : ''}`);
    }
    return (await res.json()) as IngestResponse;
  }

  /**
   * Ask the corpus to vectorize whatever is still unembedded.
   *
   * Ingest doesn't embed inline (it would make batch posts slow enough to time out), but retrieval
   * filters on `embedding_version` — so without this a freshly scraped job sits in the corpus
   * invisible to search. Called once after all batches rather than per batch, since the underlying
   * operation is a single idempotent sweep.
   *
   * Returns null when the corpus declines or is unreachable; a failure here doesn't invalidate the
   * scrape, it just means the scheduler will pick the work up on its next pass.
   */
  async embedPending(): Promise<number | null> {
    if (this.dryRun) return null;
    try {
      const res = await fetch(`${this.baseUrl}/ingest/embed`, {
        method: 'POST',
        headers: this.apiKey ? { 'x-api-key': this.apiKey } : {},
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { embedded?: number };
      return body.embedded ?? null;
    } catch {
      return null;
    }
  }

  /** Corpus size and how much of it is searchable. */
  async count(): Promise<{ canonical_jobs: number; searchable: number; pending_embedding: number } | null> {
    try {
      const res = await fetch(`${this.baseUrl}/ingest/count`, {
        headers: this.apiKey ? { 'x-api-key': this.apiKey } : {},
      });
      if (!res.ok) return null;
      return (await res.json()) as { canonical_jobs: number; searchable: number; pending_embedding: number };
    } catch {
      return null;
    }
  }

  async health(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/health`);
      return res.ok;
    } catch {
      return false;
    }
  }

  get url(): string {
    return this.baseUrl;
  }
}
