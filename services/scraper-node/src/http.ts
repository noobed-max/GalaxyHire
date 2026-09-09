/**
 * The transport career-ops providers are handed.
 *
 * career-ops providers never build their own HTTP client — `scan.mjs` passes one in via `ctx`, and
 * the contract in `vendor/career-ops-providers/_types.js` defines its shape. Supplying that same
 * shape is what lets the providers run unmodified.
 *
 * Concurrency and per-portal pacing live in the orchestrator, not here; this is only the socket.
 */

export interface FetchOptions {
  timeoutMs?: number;
  headers?: Record<string, string>;
  method?: string;
  body?: string | null;
  redirect?: 'error' | 'follow' | 'manual';
}

/** The `Context` career-ops providers receive. Field names are the contract — don't rename. */
export interface CareerOpsCtx {
  transport: 'http';
  fetchText(url: string, opts?: FetchOptions): Promise<string>;
  fetchJson(url: string, opts?: FetchOptions): Promise<unknown>;
  /**
   * Raw `Response` (timeout + non-2xx guard applied), for providers that need response
   * metadata — e.g. csod.mjs reads `Set-Cookie` off the bootstrap response before calling
   * the search API. Added upstream alongside `_http.fetchResponse`; older embedders that
   * omit it still work because providers fall back to `fetchText`.
   */
  fetchResponse(url: string, opts?: FetchOptions): Promise<Response>;
  maxPages?: number;
  sleep?(ms: number): Promise<void>;
}

const DEFAULT_TIMEOUT_MS = 30_000;

/** Identify ourselves honestly. A real UA is also what most boards' WAFs are least hostile to. */
export const USER_AGENT =
  'GalaxyHire/0.1 (+job-search aggregator; contact via repository)';

export const sleep = (ms: number) => new Promise<void>(res => setTimeout(res, ms));

async function request(url: string, opts: FetchOptions = {}): Promise<Response> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(`timeout after ${timeoutMs}ms`)), timeoutMs);
  try {
    const res = await fetch(url, {
      method: opts.method ?? 'GET',
      headers: { 'user-agent': USER_AGENT, accept: '*/*', ...(opts.headers ?? {}) },
      body: opts.body ?? undefined,
      redirect: opts.redirect ?? 'follow',
      signal: controller.signal,
    });
    if (!res.ok) {
      // Include the status in the message: the orchestrator classifies 403/429 as "defended"
      // rather than "broken", and that distinction drives whether a portal is worth retrying.
      throw new Error(`HTTP ${res.status} ${res.statusText} for ${url}`);
    }
    return res;
  } finally {
    clearTimeout(timer);
  }
}

/** Build a fresh context. One per portal run, so an aborted fetch can't leak across portals. */
export function createCareerOpsCtx(opts: { maxPages?: number } = {}): CareerOpsCtx {
  return {
    transport: 'http',
    async fetchText(url, o) {
      return (await request(url, o)).text();
    },
    async fetchJson(url, o) {
      return (await request(url, o)).json();
    },
    async fetchResponse(url, o) {
      return request(url, o);
    },
    maxPages: opts.maxPages,
    sleep,
  };
}
