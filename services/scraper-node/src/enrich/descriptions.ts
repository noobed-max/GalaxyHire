/**
 * Description enrichment — what is LEFT after upstream career-ops grew into the job.
 *
 * Enrichment exists because R5 (descriptions must actually come back) is a ranking requirement:
 * with no description, embeddings see only the title, the re-ranker's criteria read nothing, and
 * resume tailoring has no JD to select against. Measured on a real corpus once before: 13.9%
 * coverage, and it cost retrieval quality across the board.
 *
 * What changed upstream since this file was written (verified live, MAJOR-CHANGE/04 F-2):
 *   - greenhouse.mjs now requests `?content=true` itself (#3175) — the `enrichGreenhouse`
 *     refetch that used to live here became a duplicate request for bytes already in hand.
 *   - ashby.mjs now maps the `descriptionPlain` its list payload always carried — same story.
 *   - lever always shipped `descriptionPlain`.
 * Both deleted here. The providers stay unmodified; if upstream ever regresses, the fix belongs
 * in a career-ops PR, not a fork of the vendor tree.
 *
 * What stays: solidjobs, whose list payload genuinely lacks body text and whose API answers in
 * one extra request per board. The rule is unchanged — **one extra request per board, never per
 * job**; a per-job fetch belongs behind career-ops' opt-in `fetchDetails`/`detailLimit`, not here.
 *
 * HTML→text now defers to the vendored `_html-to-text.mjs` (upstream fixed its attribute-leak
 * regex — the old local copy of the stripper is deleted with it; maintaining two tag-strippers
 * is how descriptions and providers diverge).
 */

import { createCareerOpsCtx } from '../http';
import type { CareerOpsJob } from '../map/career-ops';
import type { PortalEntry } from '../config';

/** The vendored stripper — same code the providers themselves use for descriptions. */
export async function htmlToText(html: string): Promise<string> {
  const mod: any = await import(
    new URL('../../vendor/career-ops-providers/_html-to-text.mjs', import.meta.url).href
  );
  return typeof mod.htmlToText === 'function' ? mod.htmlToText(html) : html;
}

export interface EnrichResult {
  /** Jobs with `description` filled where it could be resolved. */
  jobs: CareerOpsJob[];
  enriched: number;
  error?: string;
}

/** An enricher adds descriptions to one portal's jobs, given the config entry they came from. */
export type Enricher = (entry: PortalEntry, jobs: CareerOpsJob[]) => Promise<EnrichResult>;

/**
 * solid.jobs: descriptions *and* a source-published seniority, across all pages.
 *
 * Its public API returns `description` (HTML, ~4 KB) and `experienceLevel` for every posting, keyed
 * by the same `url` the provider emits. It also carries `experienceLevel` as one of Junior /
 * Regular / Senior, which is better than inferring a level from the title — so that is passed
 * through as `seniorityHint` and wins over the corpus normalizer's inference.
 *
 * The response is paginated (500 per page, `totalPages`), and the provider only reads page 0, so
 * this walks the rest. Bounded by MAX_PAGES because an unbounded loop over a third-party pagination
 * field is a hang waiting to happen.
 *
 * A note on how this nearly went wrong: an earlier pass concluded this API "returned zero items"
 * and skipped it. That was a mis-read — the probe guessed the envelope key as `offers`/`data` when
 * it is `jobs`. The API was fine all along. Worth stating because the wrong conclusion was recorded
 * in a commit message before it was caught.
 */
const SOLIDJOBS_MAX_PAGES = 6;

export const enrichSolidJobs: Enricher = async (entry, jobs) => {
  const base = typeof entry.careers_url === 'string' ? entry.careers_url
    : typeof entry.api === 'string' ? entry.api : null;
  if (!base || jobs.length === 0) return { jobs, enriched: 0 };

  try {
    const ctx = createCareerOpsCtx();
    const byUrl = new Map<string, { text: string; level?: string }>();

    for (let page = 0; page < SOLIDJOBS_MAX_PAGES; page += 1) {
      const url = new URL(base);
      url.searchParams.set('pageIndex', String(page));
      const payload = (await ctx.fetchJson(url.toString(), { redirect: 'error' })) as any;
      const rows: any[] = Array.isArray(payload?.jobs) ? payload.jobs : [];
      if (rows.length === 0) break;

      for (const r of rows) {
        const jobUrl = typeof r?.url === 'string' ? r.url.trim() : '';
        const html = typeof r?.description === 'string' ? r.description : '';
        if (!jobUrl) continue;
        byUrl.set(jobUrl, {
          text: html ? await htmlToText(html) : '',
          level: typeof r?.experienceLevel === 'string' ? r.experienceLevel : undefined,
        });
      }

      const totalPages = Number(payload?.totalPages);
      if (!Number.isFinite(totalPages) || page + 1 >= totalPages) break;
    }

    let enriched = 0;
    const out = jobs.map(job => {
      const found = byUrl.get(job.url);
      if (!found) return job;
      const next = { ...job };
      if (!next.description && found.text) {
        next.description = found.text;
        enriched += 1;
      }
      if (found.level && !next.seniorityHint) next.seniorityHint = found.level;
      return next;
    });
    return { jobs: out, enriched };
  } catch (err) {
    // Enrichment is an improvement, never a requirement: a failure here must not cost the jobs
    // the provider already fetched successfully.
    return { jobs, enriched: 0, error: (err as Error).message };
  }
};

/**
 * Portals needing one extra request for descriptions.
 *
 * Only add an entry when the extra content genuinely costs one request for the whole board.
 * Absent on purpose (measured 2026-08-27, all supply descriptions in the list payload already):
 * greenhouse (#3175 content=true), ashby + lever (descriptionPlain), alibaba/meituan/tencent/
 * remotli/cryptocurrencyjobs/agentic-jobs (feed-carried JD text). workday and the long tail would
 * need a request per job — that belongs behind an explicit opt-in, not here.
 */
export const ENRICHERS: Record<string, Enricher> = {
  solidjobs: enrichSolidJobs,
};

export function enricherFor(providerId: string): Enricher | undefined {
  return ENRICHERS[providerId];
}
