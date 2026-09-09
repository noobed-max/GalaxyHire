-- The portal set becomes part of a scrape's identity (MAJOR-CHANGE/06 §5).
--
-- Per-portal UI toggles decide which boards a scrape visits. If the selection were not recorded,
-- `freshly_scraped` would serve a 3-portal run as "fresh" for a 40-portal search, and the user
-- would silently get 37 portals' worth of nothing. Freshness is therefore keyed on
-- phrase + location + a SUPERSET check over this column (runner._portals_cover).
--
-- NULL means "no set recorded" (a pre-feature or manually-seeded run). After the pre-major-change
-- purge there are no such rows, and the freshness check reads NULL conservatively: not fresh.
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS portals TEXT[];
