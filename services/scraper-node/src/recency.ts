/**
 * Recency policy — GalaxyHire's requirement, deliberately NOT career-ops' semantics.
 *
 * career-ops' `buildPostingAgeFilter` passes every undated posting ("do not penalize missing
 * data" — a reasonable choice for a human reviewing a markdown pipeline). GalaxyHire's product
 * rule is the opposite: "we only scrape data within 24 hours". The divergence is intentional
 * and per-portal, encoded in `portals.yml`'s `gh.recency` block (see MAJOR-CHANGE/05 §3):
 *
 *   strict      — `postedAt` required and inside the window; undated rows are DROPPED.
 *                 (The career-ops default semantics are inverted here — read this comment
 *                 before "fixing" it. Measured in MAJOR-CHANGE/04: this is the mode where
 *                 justjoin/arbeitnow/careerviet/wttj/jobicy/… carry the product.)
 *   first_seen  — the source publishes no usable date (or refreshes slower than a day — the
 *                 Greenhouse/Ashby reality). Freshness then means *new to us*: the posting was
 *                 not in the corpus before this run. Novelty is enforced retrieval-side
 *                 (`first_seen_at >= cutoff` in `galaxy/search/retrieval.py`), because only the
 *                 corpus knows what it has seen; here we only label the envelopes so no layer
 *                 ever mistakes them for "posted within 24h".
 *   off         — never scraped (retired feeds, bot-walled endpoints).
 *
 * The label lands in `fields.extra.freshness` and must survive to the UI, which is required to
 * badge "posted < 24h" and "new to us" as the different claims they are.
 */

import type { CareerOpsJob } from './map/career-ops';

export type RecencyPolicy = 'strict' | 'first_seen' | 'off';

export interface RecencyStats {
  jobs_in: number;
  dropped_undated: number;
  dropped_stale: number;
  kept_first_seen: number;
}

export function parseRecencyPolicy(value: unknown): RecencyPolicy {
  return value === 'strict' || value === 'first_seen' || value === 'off' ? value : 'strict';
}

const hasDate = (j: CareerOpsJob): boolean =>
  typeof j.postedAt === 'number' && Number.isFinite(j.postedAt) && j.postedAt > 0;

/**
 * Clock-skew tolerance for source dates. A posting "from the future" is bad data, not a fresh
 * job — and under `strict` it would otherwise stay inside the window indefinitely. Within the
 * tolerance it is treated as now (boards do skew); beyond it the date is unusable.
 */
const FUTURE_TOLERANCE_MS = 1 * 3_600_000;

/**
 * Apply the portal's policy. Returns the kept jobs plus counters for the run report; each kept
 * job gains `freshness` ('posted' | 'first_seen') for the mapper.
 */
export function applyRecency(
  jobs: CareerOpsJob[],
  policy: RecencyPolicy,
  hours: number,
  now: number = Date.now(),
): { jobs: CareerOpsJob[]; stats: RecencyStats } {
  const cutoff = now - hours * 3_600_000;
  const stats: RecencyStats = {
    jobs_in: jobs.length, dropped_undated: 0, dropped_stale: 0, kept_first_seen: 0,
  };
  const kept: CareerOpsJob[] = [];

  for (const j of jobs) {
    // A far-future date is a broken clock, not a fresh posting — and it would otherwise pass
    // `strict` forever. Fold it back to "no usable date" so each policy handles it honestly.
    const usableDate = hasDate(j) && (j.postedAt as number) <= now + FUTURE_TOLERANCE_MS;
    if (policy === 'strict') {
      if (!usableDate) { stats.dropped_undated += 1; continue; }
      if ((j.postedAt as number) < cutoff) { stats.dropped_stale += 1; continue; }
      kept.push({ ...j, freshness: 'posted' } as CareerOpsJob);
    } else {
      // first_seen: keep everything; label undated rows so downstream never claims a post date
      // they don't have. A dated first_seen job inside the window is genuinely "posted < 24h".
      const fresh = usableDate && (j.postedAt as number) >= cutoff ? 'posted' : 'first_seen';
      if (fresh === 'first_seen') stats.kept_first_seen += 1;
      kept.push({ ...j, freshness: fresh } as CareerOpsJob);
    }
  }
  return { jobs: kept, stats };
}
