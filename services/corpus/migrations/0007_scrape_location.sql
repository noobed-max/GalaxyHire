-- Location becomes part of a scrape's identity, not just a post-hoc filter.
--
-- Job boards support location natively and return far better results when told: LinkedIn returns
-- 600 India-specific software roles for "software engineer" + India, where an unlocated query
-- returns a global spread that India-filtering afterwards mostly throws away. So for LOCATION ONLY
-- the filter moves to scrape time. Seniority, negatives and years stay post-hoc, because boards
-- either ignore them or apply them inconsistently.
--
-- It has to be part of the freshness key too. Without this column, "software engineer" collected
-- for India would count as fresh for a later "software engineer" in the UK, and the second search
-- would silently return Indian jobs.
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS location TEXT NOT NULL DEFAULT '';

-- Freshness is looked up by phrase + location together.
CREATE INDEX IF NOT EXISTS ix_scrape_runs_phrase_loc
    ON scrape_runs (lower(phrase), lower(location), finished_at DESC);
