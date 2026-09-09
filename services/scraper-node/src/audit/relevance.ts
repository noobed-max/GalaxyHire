/** Deterministic audit rules shared by live scraper reports.
 *
 * The product's canonical software family intentionally treats Software Engineer, Software
 * Developer, SDE and SWE as one role. Scraper audits use the same rule so query variants are
 * comparable instead of being judged by different ad-hoc substring checks.
 */

const SOFTWARE_QUERY =
  /(^|\b)(software\s+(?:engineering|engineer|developer|development\s+engineer)|sde|swe)(\b|$)/i;

const SOFTWARE_TITLE =
  /(?:\bsoftware[\s-]+(?:engineer|developer)s?\b|\bsoftware[\s-]+development[\s-]+engineers?\b|\b(?:sde|swe)(?:[\s-]*(?:i|ii|iii|1|2|3))?\b|\b(?:back[\s-]?end|front[\s-]?end|full[\s-]?stack|fullstack|web|mobile|android|ios)[\s-]+(?:software[\s-]+)?(?:engineer|developer)s?\b|\b(?:application|platform|cloud|devops|site[\s-]+reliability)[\s-]+(?:engineer|developer)s?\b)/i;

export function titleMatchesRole(query: string, title: string): boolean {
  if (!SOFTWARE_QUERY.test(query)) return title.toLowerCase().includes(query.trim().toLowerCase());
  return SOFTWARE_TITLE.test(title);
}

export function normalizedLocation(value: string): string {
  return value
    .toLowerCase()
    // Country shorthands, both sides: a "UK" search must meet "London, UK" and
    // "London, United Kingdom" alike. Word-boundaried so "uk" never matches mid-word.
    .replace(/\buk\b/g, 'united kingdom')
    .replace(/\bgb\b/g, 'united kingdom')
    .replace(/\bgreat britain\b/g, 'united kingdom')
    .replace(/\busa?\b/g, 'united states')
    .replace(/\buae\b/g, 'united arab emirates')
    .replace(/\bbangalore\b/g, 'bengaluru')
    .replace(/\s+/g, ' ')
    .trim();
}

export function locationMatches(query: string | undefined, value: string): boolean | null {
  const wanted = normalizedLocation(query ?? '');
  if (!wanted) return null;
  const actual = normalizedLocation(value);
  if (!actual) return null; // missing source data is unknown, not a false match
  return actual.includes(wanted) || wanted.includes(actual);
}


/**
 * The ingest-time role gate — the career-ops `title_filter` analogue.
 *
 * career-ops' curated `title_filter.positive` list is a human's "at least one of these words
 * must appear in the title" rule (`providers/README.md`, `modes/scan.md` step 6). GalaxyHire
 * derives the same rule mechanically from the searched phrase, with the software-role family
 * special-cased through `titleMatchesRole` (SWE / Software Developer / SDE-II are one role).
 *
 * Deliberately BROAD: the gate exists to stop a "software engineer" scrape posting the world's
 * "Production Associate" rows into the corpus — not to arbitrate relevance inside a family,
 * which is what retrieval and the re-ranker are for (ARCHITECTURE.md D7). An unknown query
 * passes on ANY content token, and a title with no tokens passes rather than being silently
 * deleted: a visible near-miss in the results beats an invisible drop.
 */
const GATE_STOPWORDS = new Set([
  'the', 'and', 'for', 'with', 'role', 'roles', 'job', 'jobs', 'position', 'positions',
  'remote', 'hybrid', 'onsite', 'on-site', 'full', 'time', 'part', 'senior', 'junior',
  'mid', 'lead', 'staff', 'principal', 'entry', 'level', 'in', 'of', 'at', 'or', 'a', 'an',
  'engineer', 'developer', // too generic alone; still checked as part of the full phrase
]);

/** True when a job's title is plausibly about the searched role. */
export function titlePassesRoleGate(query: string, title: string): boolean {

  const q = query.trim().toLowerCase();
  const t = (title ?? '').toLowerCase();
  if (!q) return true;
  if (SOFTWARE_QUERY.test(q)) return SOFTWARE_TITLE.test(title ?? '');
  if (!t.trim()) return true; // empty title → gate abstains; the envelope validator will catch it
  if (t.includes(q)) return true;
  const words = q.split(/[\s,]+/).filter(Boolean);
  // The trailing word of a role query is almost always the family noun ("data ANALYST",
  // "site reliability ENGINEER") — matching on it alone would let "Financial Analyst" through a
  // "data analyst" search. Drop it; require a qualifier. If the qualifier list ends up empty
  // (bare "engineer"), the gate abstains — permissive by design.
  const last = words[words.length - 1];
  const tokens = words
    .slice(0, -1)
    .filter(w => w.length >= 4 && !GATE_STOPWORDS.has(w) && w !== last);
  if (tokens.length === 0) return true; // nothing discriminating to check
  return tokens.some(token => t.includes(token));
}

/**
 * The ingest-time location gate — where `--location` finally reaches the scrape.
 *
 * Almost no board takes a server-side location query (amazon's `loc_query` is the
 * exception, injected in query-injection.ts), so the location phrase is applied here,
 * client-side, mirroring career-ops' own `location_filter` semantics: an empty job
 * location PASSES (missing data is unknown, not a mismatch — dropping it would delete
 * most of the aggregator boards), remote-only rows without a country pass only when the
 * query itself says remote, and everything else must contain the wanted place.
 *
 * Deliberately coarse: this is collection hygiene (don't store Sydney rows for a UK
 * search), not the final filter — retrieval's location clause narrows afterwards with
 * the same alias rules (see normalizedLocation above).
 */
export function locationPassesGate(wanted: string, jobLocation: string | undefined): boolean {
  const w = normalizedLocation(wanted);
  if (!w) return true;
  const actual = normalizedLocation(jobLocation ?? '');
  if (!actual) return true; // missing source data is unknown, not a false match
  // Every wanted component must appear somewhere in the job's location. Components match
  // independently ("Bengaluru, India" meets "Bengaluru, Karnataka, India") — requiring the
  // joined phrase would demand adjacency the source never promised.
  const parts = w.split(',').map(p => p.trim()).filter(Boolean);
  if (parts.length > 1) {
    if (parts.every(p => actual.includes(p))) return true;
  } else if (actual.includes(w) || w.includes(actual)) {
    return true;
  }
  // A bare "remote" job names no country: keep it only for a remote search, so a
  // country search doesn't fill with placeless rows nor lose them silently elsewhere.
  if (/\bremote\b/.test(actual) && /\bremote\b/.test(w)) return true;
  return false;
}
