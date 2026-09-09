/**
 * `@galaxyhire/contract` — the boundary between scrapers and the corpus.
 *
 * One thing lives here and nothing else: the **envelope**, the one job shape every scraper emits.
 *
 * The package used to also hold the multi-project connector registry (which of career-ops /
 * ever-jobs / GalaxyJobsAi owned each of ~1,860 portals). That system was deleted with the
 * move to career-ops-only scraping — one project cannot shadow itself — and portal selection
 * became config + UI toggles instead (MAJOR-CHANGE/02 §1d, MAJOR-CHANGE/06).
 *
 * No fetching, no storage, no I/O. Keeping this package inert is what lets both the Node worker
 * and the Python corpus depend on it without a cycle.
 */

export {
  type Compensation,
  type CompInterval,
  type EnvelopeBatch,
  EnvelopeError,
  type JobEnvelope,
  type Location,
  type OnsitePolicy,
  partitionEnvelopes,
  type RawJobFields,
  type SalarySource,
  type Seniority,
  validateEnvelope,
} from './envelope';
