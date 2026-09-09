/**
 * Canonical posting-URL key — a TypeScript port of career-ops' `url-key.mjs`.
 *
 * The corpus keys observations on `(site, source_job_id)` and `map/career-ops.ts` uses the
 * posting URL as the id, so every `?utm_source=…` spelling of one job used to land as a separate
 * sighting. career-ops solved exactly this upstream and we reuse its rules verbatim rather than
 * inventing a second URL canon that could disagree with the tracker's.
 *
 * UNDER-STRIP ON PURPOSE (the upstream comment, restated because it is the whole design):
 * over-normalizing collapses two genuinely different postings into one key — a SILENT merge;
 * under-normalizing leaves two spellings of one posting — a VISIBLE duplicate. The asymmetry
 * decides every case below.
 *
 * Returns '' for anything that is not an http(s) URL — '' means NO KEY, never "the empty key",
 * so callers must treat it as unknown (map/career-ops falls back to the raw url then, which
 * keeps the envelope valid; the duplicate stays visible instead of merging unrelated jobs).
 */

const TRACKING_PARAMS = [
  /^utm_/i, /^gh_src$/i, /^fbclid$/i, /^gclid$/i,
  /^mc_cid$/i, /^mc_eid$/i, /^igshid$/i, /^_hsenc$/i, /^_hsmi$/i, /^trk$/i, /^trackingid$/i,
];

export function normalizeUrl(raw: unknown): string {
  if (typeof raw !== 'string') return '';
  const s = raw.trim();
  if (!s) return '';

  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return ''; // placeholder text ("N/A"), a local path, free text — not a posting locator
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';

  u.protocol = 'https:';
  u.hostname = u.hostname.toLowerCase();
  u.hash = '';

  const keep: [string, string][] = [];
  for (const [k, v] of u.searchParams.entries()) {
    if (!TRACKING_PARAMS.some(re => re.test(k))) keep.push([k, v]);
  }
  keep.sort((x, y) => (x[0] !== y[0] ? (x[0] < y[0] ? -1 : 1) : x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0));
  u.search = '';
  for (const [k, v] of keep) u.searchParams.append(k, v);

  if (u.pathname.length > 1 && u.pathname.endsWith('/')) u.pathname = u.pathname.slice(0, -1);
  return u.toString();
}
