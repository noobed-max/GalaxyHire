-- Scrape runs, so a collection in progress survives a page refresh.
--
-- The user's requirement: "refreshing the page should be able to dynamically detect the state of
-- scraper etc you know like save states". React state cannot do that, and neither can the
-- in-memory TaskRegistry — both vanish with the process or the tab.
--
-- Progress is deliberately NOT stored as a counter the scraper reports. The scraper prints a
-- summary only at the end, so a progress column would sit at zero for ten minutes and then jump.
-- Instead `jobs_before` is recorded at start, and progress is derived live as
-- `count(canonical_jobs) - jobs_before` — the corpus is the process that receives every ingest, so
-- that number is authoritative and needs no cooperation from the scraper at all.

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           BIGSERIAL PRIMARY KEY,
    phrase       TEXT        NOT NULL,
    -- The only thing sent to the job boards. Filters are applied afterwards, over what was
    -- collected (ARCHITECTURE.md D7), so they are deliberately absent from this table.
    hours        INT         NOT NULL DEFAULT 48,
    status       TEXT        NOT NULL DEFAULT 'running',  -- running | done | failed | stopped
    pid          INT,                                     -- so a stop request can signal it
    jobs_before  INT         NOT NULL DEFAULT 0,
    jobs_after   INT,                                     -- set on completion; progress is live until then
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

-- The status endpoint asks "is anything running, and what is the latest run" on every poll.
CREATE INDEX IF NOT EXISTS ix_scrape_runs_started ON scrape_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS ix_scrape_runs_running ON scrape_runs (status) WHERE status = 'running';
